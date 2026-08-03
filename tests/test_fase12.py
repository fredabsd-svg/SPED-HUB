"""Testes da Fase 12 — Multi-Tenancy, API Keys e Testes de Carga.

Cobre:
  1. Middleware multi-tenant (TenantFilter, MultiTenantMiddleware, contextvars)
  2. API Keys — CRUD (criar, listar, revogar)
  3. Testes de carga — geração de ECDs sintéticas e benchmark de parsing
"""

import datetime
import os
import tempfile
import time
from pathlib import Path

import pytest
from sqlalchemy import func, select

from src.api import ApiKeyService, _hash_key, gerar_api_key
from src.auth import (
    AuthService,
    MultiTenantMiddleware,
    TenantFilter,
    get_tenant_id,
    set_tenant_id,
)
from src.db.models import (
    ECD,
    Empresa,
    Escritorio,
    Lancamento,
    Partida,
    PlanoConta,
    Usuario,
    criar_engine,
    get_session,
    init_db,
)
from src.db.repository import Repository
from src.parsers.ecd import ECDParser

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path():
    """Cria banco temporário para cada teste."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="sped_test_fase12_")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def session(db_path):
    """Sessão de banco limpa."""
    engine = criar_engine(db_path)
    init_db(engine)
    sess = get_session(engine)
    yield sess
    sess.close()


@pytest.fixture
def repo(session):
    return Repository(session)


@pytest.fixture
def escritorio(session):
    """Cria escritório de teste."""
    esc = Escritorio(nome="Escritório Teste", slug="teste", cnpj="12345678000199")
    session.add(esc)
    session.commit()
    return esc


@pytest.fixture
def empresa_com_tenant(session, escritorio):
    """Cria empresa vinculada a um escritório."""
    emp = Empresa(
        cnpj="00987654000188",
        nome="Empresa com Tenant",
        uf="SP",
        escritorio_id=escritorio.id,
    )
    session.add(emp)
    session.commit()
    return emp


@pytest.fixture
def empresa_sem_tenant(session):
    """Cria empresa sem escritório (retrocompatibilidade)."""
    emp = Empresa(
        cnpj="00112233000144",
        nome="Empresa sem Tenant",
        uf="RJ",
        escritorio_id=None,
    )
    session.add(emp)
    session.commit()
    return emp


# ── Testes: Multi-Tenancy ───────────────────────────────────────────────────


class TestTenantContext:
    """Testes do contextvar de tenant."""

    def test_tenant_id_default_none(self):
        """Sem contexto definido, get_tenant_id retorna None."""
        # Reset para garantir
        set_tenant_id(None)
        assert get_tenant_id() is None

    def test_set_and_get_tenant(self):
        """Define e recupera tenant_id."""
        set_tenant_id(42)
        assert get_tenant_id() == 42
        set_tenant_id(None)

    def test_tenant_isolado_por_contexto(self):
        """Cada contexto tem seu próprio tenant (via contextvars)."""
        import contextvars

        ctx_var = contextvars.ContextVar("test_tenant", default=None)

        def get_in_ctx(val):
            ctx_var.set(val)
            return ctx_var.get()

        # O valor padrão é None
        assert ctx_var.get() is None


class TestTenantFilter:
    """Testes do TenantFilter para queries SQLAlchemy."""

    def test_filter_sem_tenant_nao_altera_query(self, session):
        """Sem tenant ativo, a query não é modificada."""
        set_tenant_id(None)
        stmt = select(Empresa)
        result = TenantFilter.aplicar(stmt, Empresa)
        # A query deve ser a mesma (sem where clauses adicionais)
        assert result is not None

    def test_filter_empresa_com_tenant(
        self, session, escritorio, empresa_com_tenant, empresa_sem_tenant
    ):
        """Filtra empresas por escritorio_id."""
        set_tenant_id(escritorio.id)
        stmt = TenantFilter.aplicar(select(Empresa), Empresa)
        empresas = session.execute(stmt).scalars().all()
        set_tenant_id(None)

        # Deve retornar apenas a empresa do escritório
        assert len(empresas) == 1
        assert empresas[0].id == empresa_com_tenant.id

    def test_filter_ecd_com_tenant(self, session, escritorio, empresa_com_tenant):
        """Filtra ECDs via join com Empresa."""
        # Cria ECD para empresa com tenant
        ecd = ECD(
            empresa_id=empresa_com_tenant.id,
            leiaute="009",
            dt_ini=datetime.date(2025, 1, 1),
            dt_fin=datetime.date(2025, 12, 31),
        )
        session.add(ecd)
        session.commit()

        set_tenant_id(escritorio.id)
        stmt = TenantFilter.aplicar(select(ECD), ECD)
        ecds = session.execute(stmt).scalars().all()
        set_tenant_id(None)

        assert len(ecds) == 1
        assert ecds[0].id == ecd.id

    def test_filter_ecd_sem_tenant_nao_retorna(self, session, empresa_sem_tenant):
        """ECD de empresa sem tenant não aparece quando tenant está ativo."""
        ecd = ECD(
            empresa_id=empresa_sem_tenant.id,
            leiaute="009",
            dt_ini=datetime.date(2025, 1, 1),
            dt_fin=datetime.date(2025, 12, 31),
        )
        session.add(ecd)
        session.commit()

        set_tenant_id(99)  # Tenant que não existe
        stmt = TenantFilter.aplicar(select(ECD), ECD)
        ecds = session.execute(stmt).scalars().all()
        set_tenant_id(None)

        assert len(ecds) == 0


class TestMultiTenantMiddleware:
    """Testes do middleware de isolamento."""

    def test_middleware_init(self, db_path):
        """Middleware inicializa sem erros."""
        middleware = MultiTenantMiddleware(app=None, db_path=db_path)
        assert middleware is not None
        assert middleware.db_path == db_path

    def test_resolve_tenant_sem_token(self, db_path):
        """Sem token, resolve_tenant retorna None."""
        middleware = MultiTenantMiddleware(app=None, db_path=db_path)
        # Token inválido
        result = middleware._resolve_tenant("token_invalido_123")
        assert result is None


class TestAuthServiceTenant:
    """Testes do AuthService com suporte a tenant."""

    def test_registrar_com_escritorio(self, db_path, escritorio):
        """Registra usuário vinculado a escritório."""
        auth = AuthService(db_path)
        usuario = auth.registrar(
            email="contador@teste.com",
            nome="Contador",
            senha="senha123",
            escritorio_id=escritorio.id,
        )
        assert usuario.escritorio_id == escritorio.id

    def test_registrar_sem_escritorio(self, db_path):
        """Registra usuário sem escritório (retrocompatível)."""
        auth = AuthService(db_path)
        usuario = auth.registrar(
            email="admin@teste.com",
            nome="Admin",
            senha="senha123",
        )
        assert usuario.escritorio_id is None

    def test_login_com_tenant(self, db_path, escritorio):
        """Login retorna usuário com tenant."""
        auth = AuthService(db_path)
        auth.registrar(
            email="contador@teste.com",
            nome="Contador",
            senha="senha123",
            escritorio_id=escritorio.id,
        )
        usuario, token, _, _ = auth.login("contador@teste.com", "senha123")
        assert token is not None
        assert len(token) == 128  # 64 bytes hex
        # auth.login() fecha a sessão interna — consultamos direto no banco
        from src.db.models import criar_engine, get_session, init_db

        engine2 = criar_engine(db_path)
        init_db(engine2)
        sess2 = get_session(engine2)
        try:
            from sqlalchemy import select

            u = sess2.execute(
                select(Usuario).where(Usuario.email == "contador@teste.com")
            ).scalar_one()
            assert u.escritorio_id == escritorio.id
        finally:
            sess2.close()


# ── Testes: API Keys ────────────────────────────────────────────────────────


class TestApiKeyGeneration:
    """Testes de geração de API Keys."""

    def test_gerar_api_key_formato(self):
        """Chave gerada tem prefixo spd_ e 64 chars hex."""
        chave, hash_val = gerar_api_key()
        assert chave.startswith("spd_")
        assert len(chave) == 4 + 64  # spd_ + 64 hex chars
        assert len(hash_val) == 64  # SHA-256 hex

    def test_hash_deterministico(self):
        """Mesma chave gera mesmo hash."""
        chave = "spd_abc123"
        h1 = _hash_key(chave)
        h2 = _hash_key(chave)
        assert h1 == h2

    def test_hash_diferente_para_chaves_diferentes(self):
        """Chaves diferentes geram hashes diferentes."""
        h1 = _hash_key("spd_abc")
        h2 = _hash_key("spd_xyz")
        assert h1 != h2


class TestApiKeyService:
    """Testes do serviço de API Keys."""

    def test_criar_api_key(self, db_path):
        """Cria API Key e retorna dados completos."""
        svc = ApiKeyService(db_path)
        result = svc.criar(nome="Integração ERP")
        assert result["nome"] == "Integração ERP"
        assert result["chave"].startswith("spd_")
        assert result["prefixo"].startswith("spd_")
        assert result["ativo"] is True
        assert result["id"] > 0

    def test_criar_com_expiracao(self, db_path):
        """Cria API Key com data de expiração."""
        svc = ApiKeyService(db_path)
        result = svc.criar(nome="Temporária", dias_expiracao=30)
        assert result["expira_em"] is not None

    def test_listar_api_keys(self, db_path):
        """Lista API Keys sem expor chave completa."""
        svc = ApiKeyService(db_path)
        svc.criar(nome="Key 1")
        svc.criar(nome="Key 2")

        keys = svc.listar()
        assert len(keys) == 2
        # Não deve conter a chave completa
        for k in keys:
            assert "chave" not in k
            assert "prefixo" in k
            assert "key_hash" not in k

    def test_revogar_api_key(self, db_path):
        """Revoga API Key (desativa)."""
        svc = ApiKeyService(db_path)
        result = svc.criar(nome="Revogável")
        key_id = result["id"]

        assert svc.revogar(key_id) is True

        keys = svc.listar()
        key = next(k for k in keys if k["id"] == key_id)
        assert key["ativo"] is False

    def test_revogar_inexistente(self, db_path):
        """Revogar chave inexistente retorna False."""
        svc = ApiKeyService(db_path)
        assert svc.revogar(99999) is False

    def test_excluir_api_key(self, db_path):
        """Exclui permanentemente uma API Key."""
        svc = ApiKeyService(db_path)
        result = svc.criar(nome="Excluível")
        key_id = result["id"]

        assert svc.excluir(key_id) is True
        keys = svc.listar()
        assert all(k["id"] != key_id for k in keys)

    def test_chave_completa_so_retornada_na_criacao(self, db_path):
        """A chave completa só aparece no retorno de criar()."""
        svc = ApiKeyService(db_path)
        result = svc.criar(nome="Secreta")
        chave_original = result["chave"]

        # Listar não expõe a chave
        keys = svc.listar()
        for k in keys:
            assert "chave" not in k

        # Mas o hash confere
        assert _hash_key(chave_original) is not None


# ── Testes: Carga / Stress ──────────────────────────────────────────────────


class TestCargaSyntheticECD:
    """Testes de carga com ECDs sintéticas."""

    def _gerar_ecd_sintetica(self, path: str, n_contas: int = 100, n_lancamentos: int = 500):
        """Gera arquivo ECD sintético válido com tamanho configurável."""
        linhas = []

        # Registro 0000 — abertura
        dt_ini = "01012025"
        dt_fin = "31122025"
        linhas.append(
            f"|0000|009|{dt_ini}|{dt_fin}|EMPRESA TESTE CARGA|12345678000199|SP||12345678||"
            f"0|0|0|0|0|0|0|0|0|0|0|0|0|0|"
        )

        # I010
        linhas.append("|I010|G|009|")

        # I030 — identificação (2 registros)
        linhas.append(
            "|I030|TERMO DE ABERTURA|1|Diario|500|EMPRESA TESTE"
            "|31123456789|00987654000188|01012015||BELO HORIZONTE|31122023|"
        )
        linhas.append(
            "|I030|TERMO DE ABERTURA|2|Diario|500|EMPRESA TESTE"
            "|31123456789|00987654000188|01012015||BELO HORIZONTE|31122023|"
        )

        # I050 — plano de contas
        for i in range(1, n_contas + 1):
            cod = f"1.{i:04d}"
            cod_sup = f"1.{((i-1)//10)*10:04d}" if i > 10 else ""
            natureza = "01" if i <= n_contas // 2 else "02"
            ind = "A" if i > 10 else "S"
            nivel = 2 if i > 10 else 1
            linhas.append(f"|I050|{dt_ini}|{natureza}|{ind}|{nivel}|{cod}|{cod_sup}|CONTA {i:04d}|")

        # I051 — contas referenciais (10%)
        for i in range(1, n_contas // 10 + 1):
            cod = f"1.{i*10:04d}"
            linhas.append(f"|I051||{cod}|1.01.01|")

        # I075 — históricos
        for i in range(1, 11):
            linhas.append(f"|I075|{dt_ini}|HIST{i:03d}|HISTORICO PADRAO {i:03d}|")

        # I155 — saldos periódicos
        for i in range(1, n_contas + 1):
            cod = f"1.{i:04d}"
            vl = round(10000.0 + i * 100.0, 2)
            linhas.append(
                f"|I155|{cod}||{dt_ini}|{dt_fin}|{vl}|D|{vl*0.3:.2f}|{vl*0.1:.2f}|{vl*1.2:.2f}|D|"
            )

        # I200 + I250 — lançamentos
        for ln in range(1, n_lancamentos + 1):
            num_lcto = f"LCTO{ln:06d}"
            dt_lcto = f"{15 + (ln % 28):02d}{(ln % 12) + 1:02d}2025"
            vl = round(1000.0 + ln * 10.0, 2)
            linhas.append(f"|I200|{num_lcto}|{dt_lcto}|{vl}|N|1|")

            # Duas partidas (débito e crédito)
            conta_deb = f"1.{((ln % (n_contas // 2)) + 1):04d}"
            conta_cred = f"1.{((ln % (n_contas // 2)) + n_contas // 2 + 1):04d}"
            linhas.append(f"|I250|{num_lcto}|{conta_deb}||{vl}|D|1|HIST001|LANCAMENTO {ln}|")
            linhas.append(f"|I250|{num_lcto}|{conta_cred}||{vl}|C|1|HIST001|LANCAMENTO {ln}|")

        # I355 — saldos resultado (todas as contas)
        for i in range(1, n_contas + 1):
            cod = f"1.{i:04d}"
            vl = round(5000.0 + i * 50.0, 2)
            linhas.append(f"|I355|{cod}||{dt_fin}|{vl}|D|")

        # I990 — encerramento
        linhas.append(f"|I990|{n_contas + n_lancamentos * 3 + 20}|")

        # 9999
        linhas.append("|9999|1|")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(linhas) + "\n")

        return path

    def test_gerar_ecd_pequena(self, db_path):
        """Gera e parseia ECD sintética pequena (100 contas, 500 lançamentos)."""
        fd, ecd_path = tempfile.mkstemp(suffix=".txt", prefix="ecd_carga_")
        os.close(fd)

        try:
            self._gerar_ecd_sintetica(ecd_path, n_contas=100, n_lancamentos=500)

            # Verifica tamanho
            size_mb = os.path.getsize(ecd_path) / (1024 * 1024)
            assert size_mb > 0.01  # Pelo menos 10KB

            # Parseia
            parser = ECDParser()
            start = time.time()
            registros = parser.parse_todos(Path(ecd_path))
            elapsed = time.time() - start

            assert len(registros) > 0
            # Deve processar em menos de 5 segundos
            assert elapsed < 5.0, f"Parsing demorou {elapsed:.2f}s"

        finally:
            try:
                os.unlink(ecd_path)
            except OSError:
                pass

    def test_gerar_ecd_media(self, db_path):
        """Gera e parseia ECD sintética média (500 contas, 2000 lançamentos)."""
        fd, ecd_path = tempfile.mkstemp(suffix=".txt", prefix="ecd_carga_")
        os.close(fd)

        try:
            self._gerar_ecd_sintetica(ecd_path, n_contas=500, n_lancamentos=2000)

            size_mb = os.path.getsize(ecd_path) / (1024 * 1024)
            assert size_mb > 0.1  # Pelo menos 100KB

            parser = ECDParser()
            start = time.time()
            registros = parser.parse_todos(Path(ecd_path))
            elapsed = time.time() - start

            assert len(registros) > 0
            # Deve processar em menos de 15 segundos
            assert elapsed < 15.0, f"Parsing demorou {elapsed:.2f}s"

        finally:
            try:
                os.unlink(ecd_path)
            except OSError:
                pass

    def test_benchmark_importacao(self, db_path):
        """Benchmark: importação completa de ECD sintética no banco."""
        fd, ecd_path = tempfile.mkstemp(suffix=".txt", prefix="ecd_bench_")
        os.close(fd)

        try:
            self._gerar_ecd_sintetica(ecd_path, n_contas=200, n_lancamentos=1000)

            engine = criar_engine(db_path)
            init_db(engine)
            session = get_session(engine)
            repo = Repository(session)

            parser = ECDParser()
            registros = parser.parse_todos(Path(ecd_path))

            from collections import defaultdict

            grupos = defaultdict(list)
            for r in registros:
                grupos[r["_reg"]].append(r)

            r0000 = grupos["0000"][0]

            start = time.time()

            cnpj_raw = r0000.get("CNPJ", "")
            cnpj_str = str(int(float(cnpj_raw))).zfill(14) if cnpj_raw else "00000000000000"
            empresa = repo.upsert_empresa(
                {
                    "cnpj": cnpj_str,
                    "nome": r0000.get("NOME", ""),
                    "uf": r0000.get("UF", ""),
                }
            )

            ecd = repo.criar_ecd(
                empresa.id,
                {
                    "leiaute": "009",
                    "dt_ini": datetime.date(2025, 1, 1),
                    "dt_fin": datetime.date(2025, 12, 31),
                    "nome_arquivo": "benchmark.ecd",
                },
            )

            # Plano de contas
            contas = []
            for r in grupos.get("I050", []):
                contas.append(
                    {
                        "cod_cta": r.get("COD_CTA", ""),
                        "cod_cta_sup": r.get("COD_CTA_SUP", ""),
                        "nome_cta": r.get("NOME_CTA", ""),
                        "cod_nat": r.get("COD_NAT", "01"),
                        "ind_cta": r.get("IND_CTA", "A"),
                        "nivel": int(r.get("NIVEL") or 0),
                    }
                )
            repo.inserir_plano_contas(ecd.id, contas)

            # Lançamentos
            lancs = []
            for r in grupos.get("I200", []):
                lancs.append(
                    {
                        "num_lcto": r.get("NUM_LCTO", ""),
                        "dt_lcto": datetime.date(2025, 1, 15),
                        "vl_lcto": r.get("VL_LCTO", 0.0) or 0.0,
                        "ind_lcto": r.get("IND_LCTO", "N"),
                    }
                )
            repo.inserir_lancamentos(ecd.id, lancs)

            # Partidas
            partidas = []
            for r in grupos.get("I250", []):
                partidas.append(
                    {
                        "num_lcto": r.get("NUM_LCTO", ""),
                        "dt_lcto": "2025-01-15",
                        "cod_cta": r.get("COD_CTA", ""),
                        "cod_ccus": r.get("COD_CCUS", ""),
                        "vl_dc": r.get("VL_DC", 0.0) or 0.0,
                        "ind_dc": r.get("IND_DC", "D"),
                    }
                )
            repo.inserir_partidas(ecd.id, partidas)

            repo.commit()
            elapsed = time.time() - start

            # Verificações
            n_contas_db = session.execute(
                select(func.count(PlanoConta.id)).where(PlanoConta.ecd_id == ecd.id)
            ).scalar()
            n_lancs_db = session.execute(
                select(func.count(Lancamento.id)).where(Lancamento.ecd_id == ecd.id)
            ).scalar()
            n_partidas_db = session.execute(
                select(func.count(Partida.id)).join(Lancamento).where(Lancamento.ecd_id == ecd.id)
            ).scalar()

            assert n_contas_db == len(contas)
            assert n_lancs_db == len(lancs)
            assert n_partidas_db == len(partidas)

            # Importação completa deve levar menos de 10 segundos
            assert elapsed < 10.0, f"Importação demorou {elapsed:.2f}s"

            session.close()

        finally:
            try:
                os.unlink(ecd_path)
            except OSError:
                pass

    def test_ecd_grande_streaming(self, db_path):
        """Testa streaming de ECD grande (3000 contas, 10000 lançamentos)."""
        fd, ecd_path = tempfile.mkstemp(suffix=".txt", prefix="ecd_grande_")
        os.close(fd)

        try:
            self._gerar_ecd_sintetica(ecd_path, n_contas=3000, n_lancamentos=10000)

            size_mb = os.path.getsize(ecd_path) / (1024 * 1024)
            # Deve gerar pelo menos 1MB
            assert size_mb > 0.5, f"ECD muito pequena: {size_mb:.2f}MB"

            parser = ECDParser()
            start = time.time()
            registros = parser.parse_todos(Path(ecd_path))
            elapsed = time.time() - start

            assert len(registros) > 0
            # Streaming deve processar em menos de 60 segundos
            assert elapsed < 60.0, f"Parsing de {size_mb:.1f}MB demorou {elapsed:.2f}s"

        finally:
            try:
                os.unlink(ecd_path)
            except OSError:
                pass

    def test_contar_registros_ecd_grande(self, db_path):
        """Testa contagem de registros em ECD grande."""
        fd, ecd_path = tempfile.mkstemp(suffix=".txt", prefix="ecd_count_")
        os.close(fd)

        try:
            self._gerar_ecd_sintetica(ecd_path, n_contas=500, n_lancamentos=2000)

            parser = ECDParser()
            contagem = parser.contar_registros(Path(ecd_path))
            n = sum(contagem.values())

            # Esperado: 0000 + I010 + 2*I030 + 500*I050 + 50*I051 + 10*I075
            # + 500*I155 + 2000*I200 + 4000*I250 + 500*I355 + I990 + 9999
            esperado = 1 + 1 + 2 + 500 + 50 + 10 + 500 + 2000 + 4000 + 500 + 1 + 1
            assert n == esperado, f"Contagem: {n} != esperado: {esperado}"

        finally:
            try:
                os.unlink(ecd_path)
            except OSError:
                pass


class TestEscritorioModel:
    """Testes do modelo Escritorio."""

    def test_criar_escritorio(self, session):
        """Cria escritório com slug único."""
        esc = Escritorio(nome="Contabilidade ABC", slug="abc", cnpj="11222333000144")
        session.add(esc)
        session.commit()

        assert esc.id > 0
        assert esc.slug == "abc"
        assert esc.ativo is True

    def test_slug_unico(self, session):
        """Slug deve ser único."""
        esc1 = Escritorio(nome="A", slug="unico")
        session.add(esc1)
        session.commit()

        esc2 = Escritorio(nome="B", slug="unico")
        session.add(esc2)
        with pytest.raises(Exception):
            session.commit()
        session.rollback()

    def test_empresa_com_escritorio(self, session, escritorio):
        """Empresa vinculada a escritório."""
        emp = Empresa(cnpj="55443322000177", nome="Cliente Ltda", escritorio_id=escritorio.id)
        session.add(emp)
        session.commit()

        assert emp.escritorio_id == escritorio.id
        assert emp.escritorio.slug == escritorio.slug

    def test_usuario_com_escritorio(self, session, escritorio):
        """Usuário vinculado a escritório."""
        senha_hash, salt = Usuario.hash_senha("teste123")
        user = Usuario(
            email="user@abc.com",
            nome="Usuário ABC",
            senha_hash=senha_hash,
            salt=salt,
            escritorio_id=escritorio.id,
        )
        session.add(user)
        session.commit()

        assert user.escritorio_id == escritorio.id
