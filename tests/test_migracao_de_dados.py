"""Migração de dados entre bancos (Fase 32).

`sped-hub migrar` versiona **schema**. Até aqui não havia caminho para o
**conteúdo**: um escritório rodando em SQLite que quisesse PostgreSQL só podia
reimportar todas as ECDs — e perdia no caminho tudo que não vem de arquivo:
usuários, mapeamentos de conta, visões de filtro, chaves de API e o histórico
de auditoria.

O defeito mais perigoso desta classe de migração é **silencioso**: as linhas
chegam com id explícito, a sequência do Postgres continua em 1, e a migração
parece ter dado certo. O erro só aparece quando o escritório cadastra a próxima
empresa e recebe violação de chave primária. Por isso os testes de sequência
rodam contra um Postgres real (`TEST_DATABASE_URL`), não contra SQLite — em
SQLite não há sequência para errar.
"""

from __future__ import annotations

import datetime
import os
import uuid

import pytest
import sqlalchemy

from src.db.migrations import (
    ErroDeMigracaoDeDados,
    conferir_migracao_de_dados,
    migrar_dados,
)
from src.db.models import (
    ECD,
    ApiKey,
    AuditLog,
    Empresa,
    Escritorio,
    Lancamento,
    PlanoConta,
    Usuario,
    criar_engine,
    get_session,
    init_db,
)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

sem_postgres = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="defina TEST_DATABASE_URL para exercitar o PostgreSQL",
)


@pytest.fixture
def origem(tmp_path) -> str:
    alvo = f"sqlite:///{tmp_path / 'origem.db'}"
    engine = criar_engine(url=alvo)
    try:
        init_db(engine)
    finally:
        engine.dispose()
    return alvo


@pytest.fixture
def destino_sqlite(tmp_path) -> str:
    alvo = f"sqlite:///{tmp_path / 'destino.db'}"
    engine = criar_engine(url=alvo)
    try:
        init_db(engine)
    finally:
        engine.dispose()
    return alvo


@pytest.fixture
def destino_postgres():
    """Schema Postgres descartável, com o schema criado."""
    schema = f"dados_{uuid.uuid4().hex[:12]}"
    base = criar_engine(url=TEST_DATABASE_URL)
    with base.begin() as conexao:
        conexao.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    base.dispose()
    url = f"{TEST_DATABASE_URL}?options=-csearch_path%3D{schema}"
    engine = criar_engine(url=url)
    try:
        init_db(engine)
    finally:
        engine.dispose()
    try:
        yield url
    finally:
        base = criar_engine(url=TEST_DATABASE_URL)
        with base.begin() as conexao:
            conexao.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        base.dispose()


def _semear(url: str, *, empresas: int = 1, lancamentos: int = 0) -> dict[str, int]:
    """Popula a origem com um escritório completo. Devolve as contagens."""
    engine = criar_engine(url=url)
    try:
        with get_session(engine) as sessao:
            escritorio = Escritorio(nome="Assessoria Frederico", slug="frederico")
            sessao.add(escritorio)
            sessao.flush()

            senha_hash, salt = Usuario.hash_senha("senha-do-contador")
            sessao.add(
                Usuario(
                    escritorio_id=escritorio.id,
                    email="contador@escritorio.local",
                    nome="Contador",
                    senha_hash=senha_hash,
                    salt=salt,
                )
            )
            sessao.add(ApiKey(nome="Integração", key_hash="k" * 64, prefixo="spd_integ"))
            sessao.add(AuditLog(acao="ecd.importada", recurso="ECD #1"))

            for i in range(empresas):
                empresa = Empresa(
                    escritorio_id=escritorio.id,
                    cnpj=f"0012345600{i:04d}",
                    nome=f"Cliente {i} Ltda",
                )
                sessao.add(empresa)
                sessao.flush()
                ecd = ECD(
                    empresa_id=empresa.id,
                    leiaute="9",
                    dt_ini=datetime.date(2024, 1, 1),
                    dt_fin=datetime.date(2024, 12, 31),
                    importado_em=datetime.datetime.now(datetime.UTC),
                )
                sessao.add(ecd)
                sessao.flush()
                sessao.add(
                    PlanoConta(
                        ecd_id=ecd.id,
                        cod_cta="1",
                        nome_cta="ATIVO",
                        ind_cta="S",
                        nivel=1,
                        cod_nat="01",
                    )
                )
                for j in range(lancamentos):
                    sessao.add(
                        Lancamento(
                            ecd_id=ecd.id,
                            num_lcto=str(j),
                            dt_lcto=datetime.date(2024, 6, 1),
                            vl_lcto=100 + j,
                            ind_lcto="N",
                        )
                    )
            sessao.commit()
    finally:
        engine.dispose()

    engine = criar_engine(url=url)
    try:
        with engine.connect() as conexao:
            return {
                modelo.__tablename__: conexao.execute(
                    sqlalchemy.select(sqlalchemy.func.count()).select_from(modelo.__table__)
                ).scalar()
                for modelo in (Escritorio, Empresa, Usuario, ECD, PlanoConta, Lancamento)
            }
    finally:
        engine.dispose()


def _contar(url: str, modelo) -> int:
    engine = criar_engine(url=url)
    try:
        with engine.connect() as conexao:
            return (
                conexao.execute(
                    sqlalchemy.select(sqlalchemy.func.count()).select_from(modelo.__table__)
                ).scalar()
                or 0
            )
    finally:
        engine.dispose()


# ═══════════════════════════════════════════════════════════════════════════
# 1. A cópia acontece, e é fiel
# ═══════════════════════════════════════════════════════════════════════════


class TestCopia:
    def test_copia_todas_as_tabelas_com_dados(self, origem, destino_sqlite):
        esperado = _semear(origem, empresas=2, lancamentos=5)

        copiadas = migrar_dados(origem, destino_sqlite)

        for tabela, quantas in esperado.items():
            assert copiadas[tabela] == quantas, tabela

    def test_conferencia_nao_acha_divergencia(self, origem, destino_sqlite):
        """ "Não deu erro" não é o mesmo que "os dados chegaram"."""
        _semear(origem, empresas=2, lancamentos=5)

        migrar_dados(origem, destino_sqlite)

        assert conferir_migracao_de_dados(origem, destino_sqlite) == {}

    def test_ids_sao_preservados(self, origem, destino_sqlite):
        """Renumerar exigiria reescrever cada chave estrangeira do banco."""
        _semear(origem, empresas=3)
        engine = criar_engine(url=origem)
        try:
            with engine.connect() as conexao:
                antes = sorted(
                    conexao.execute(
                        sqlalchemy.select(Empresa.id, Empresa.cnpj).order_by(Empresa.id)
                    ).all()
                )
        finally:
            engine.dispose()

        migrar_dados(origem, destino_sqlite)

        engine = criar_engine(url=destino_sqlite)
        try:
            with engine.connect() as conexao:
                depois = sorted(
                    conexao.execute(
                        sqlalchemy.select(Empresa.id, Empresa.cnpj).order_by(Empresa.id)
                    ).all()
                )
        finally:
            engine.dispose()

        assert antes == depois

    def test_chaves_estrangeiras_continuam_validas(self, origem, destino_sqlite):
        """A ordem de cópia tem de respeitar as dependências."""
        _semear(origem, empresas=2, lancamentos=3)

        migrar_dados(origem, destino_sqlite)

        engine = criar_engine(url=destino_sqlite)
        try:
            with engine.connect() as conexao:
                orfaos = conexao.execute(
                    sqlalchemy.text(
                        "SELECT COUNT(*) FROM empresas e "
                        "LEFT JOIN escritorios s ON s.id = e.escritorio_id "
                        "WHERE e.escritorio_id IS NOT NULL AND s.id IS NULL"
                    )
                ).scalar()
                orfaos += conexao.execute(
                    sqlalchemy.text(
                        "SELECT COUNT(*) FROM lancamentos l "
                        "LEFT JOIN ecds d ON d.id = l.ecd_id WHERE d.id IS NULL"
                    )
                ).scalar()
        finally:
            engine.dispose()

        assert orfaos == 0

    def test_valores_chegam_intactos(self, origem, destino_sqlite):
        """Cópia de linha crua tem de preservar texto, data e decimal."""
        _semear(origem, empresas=1, lancamentos=2)

        migrar_dados(origem, destino_sqlite)

        engine = criar_engine(url=destino_sqlite)
        try:
            with get_session(engine) as sessao:
                usuario = sessao.execute(sqlalchemy.select(Usuario)).scalars().one()
                ecd = sessao.execute(sqlalchemy.select(ECD)).scalars().one()
                assert usuario.email == "contador@escritorio.local"
                assert (
                    usuario.verificar_senha("senha-do-contador") is True
                ), "hash e salt precisam sobreviver — senão ninguém entra no destino"
                assert ecd.dt_ini == datetime.date(2024, 1, 1)
        finally:
            engine.dispose()

    def test_banco_vazio_migra_sem_erro(self, origem, destino_sqlite):
        assert sum(migrar_dados(origem, destino_sqlite).values()) == 0

    def test_copia_sai_em_lotes(self, origem, destino_sqlite):
        """Memória constante: `partidas` de uma ECD real não cabe em memória.

        Contar linhas copiadas não prova nada — sai igual buscando tudo de uma
        vez. O que se verifica é o número de INSERTs emitidos: com `lote=3` e
        25 lançamentos, são vários; buscando tudo, seria um.
        """
        _semear(origem, empresas=1, lancamentos=25)
        inserts: list[str] = []

        def espiar(conn, cursor, statement, parameters, context, executemany):
            if "INSERT INTO lancamentos" in statement:
                inserts.append(statement)

        sqlalchemy.event.listen(sqlalchemy.engine.Engine, "before_cursor_execute", espiar)
        try:
            copiadas = migrar_dados(origem, destino_sqlite, lote=3)
        finally:
            sqlalchemy.event.remove(sqlalchemy.engine.Engine, "before_cursor_execute", espiar)

        assert copiadas["lancamentos"] == 25
        assert _contar(destino_sqlite, Lancamento) == 25
        assert len(inserts) >= 25 // 3, (
            f"25 lançamentos entraram em {len(inserts)} INSERT(s) com lote=3 — "
            "sem fatiar, a migração carrega a tabela inteira em memória"
        )

    def test_lote_maior_que_a_tabela_funciona(self, origem, destino_sqlite):
        _semear(origem, empresas=1, lancamentos=4)

        assert migrar_dados(origem, destino_sqlite, lote=10_000)["lancamentos"] == 4


# ═══════════════════════════════════════════════════════════════════════════
# 2. Recusas — o que este comando NÃO faz sozinho
# ═══════════════════════════════════════════════════════════════════════════


class TestRecusas:
    def test_destino_com_dados_e_recusado(self, origem, destino_sqlite):
        """Migrar sobre banco ocupado misturaria a escrituração de dois lugares."""
        _semear(origem)
        migrar_dados(origem, destino_sqlite)

        with pytest.raises(ErroDeMigracaoDeDados, match="já tem dados"):
            migrar_dados(origem, destino_sqlite)

    def test_destino_sem_schema_e_recusado(self, origem, tmp_path):
        vazio = f"sqlite:///{tmp_path / 'sem-schema.db'}"

        with pytest.raises(ErroDeMigracaoDeDados, match="schema"):
            migrar_dados(origem, vazio)

    def test_mesmo_banco_e_recusado(self, origem):
        with pytest.raises(ErroDeMigracaoDeDados, match="mesmo banco"):
            migrar_dados(origem, origem)

    def test_recusa_nao_grava_nada(self, origem, destino_sqlite):
        """A recusa acontece antes de qualquer escrita."""
        _semear(origem, empresas=2)
        engine = criar_engine(url=destino_sqlite)
        try:
            with get_session(engine) as sessao:
                sessao.add(Escritorio(nome="Já existia", slug="ja-existia"))
                sessao.commit()
        finally:
            engine.dispose()

        with pytest.raises(ErroDeMigracaoDeDados):
            migrar_dados(origem, destino_sqlite)

        assert _contar(destino_sqlite, Escritorio) == 1, "a recusa mexeu no destino"
        assert _contar(destino_sqlite, Empresa) == 0

    def test_falha_no_meio_da_copia_nao_deixa_metade(self, origem, destino_sqlite):
        """Meia escrituração migrada é pior que nenhuma: parece completa.

        É dela que sairia um balanço errado — e ninguém desconfiaria, porque a
        migração "funcionou". A falha é injetada **no meio da cópia**, depois de
        algumas tabelas já terem entrado: falhar só no fim testaria menos.
        """
        _semear(origem, empresas=3, lancamentos=4)

        def explodir_no_meio(conn, cursor, statement, parameters, context, executemany):
            if "INSERT INTO ecds" in statement:
                raise RuntimeError("queda no meio da migração")

        sqlalchemy.event.listen(sqlalchemy.engine.Engine, "before_cursor_execute", explodir_no_meio)
        try:
            with pytest.raises(Exception, match="queda no meio"):
                migrar_dados(origem, destino_sqlite)
        finally:
            sqlalchemy.event.remove(
                sqlalchemy.engine.Engine, "before_cursor_execute", explodir_no_meio
            )

        # `escritorios`, `empresas` e `usuarios` já tinham sido inseridos
        # quando a falha aconteceu — e nada disso pode ter ficado.
        assert _contar(destino_sqlite, Escritorio) == 0, "sobrou metade no destino"
        assert _contar(destino_sqlite, Empresa) == 0
        assert _contar(destino_sqlite, Usuario) == 0
        assert _contar(destino_sqlite, Lancamento) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 3. PostgreSQL: as sequências, que é onde o defeito é silencioso
# ═══════════════════════════════════════════════════════════════════════════


@sem_postgres
class TestPostgres:
    def test_migra_sqlite_para_postgres(self, origem, destino_postgres):
        esperado = _semear(origem, empresas=2, lancamentos=10)

        copiadas = migrar_dados(origem, destino_postgres)

        for tabela, quantas in esperado.items():
            assert copiadas[tabela] == quantas, tabela
        assert conferir_migracao_de_dados(origem, destino_postgres) == {}

    def test_sequencia_avanca_alem_dos_ids_copiados(self, origem, destino_postgres):
        """O defeito silencioso desta classe de migração.

        Sem `setval`, a sequência continua em 1: a migração parece ter dado
        certo, e o erro só aparece quando o escritório cadastra a próxima
        empresa e recebe violação de chave primária.
        """
        _semear(origem, empresas=3)

        migrar_dados(origem, destino_postgres)

        engine = criar_engine(url=destino_postgres)
        try:
            with get_session(engine) as sessao:
                escritorio = sessao.execute(sqlalchemy.select(Escritorio)).scalars().first()
                # Sem a correção, este INSERT levanta IntegrityError.
                sessao.add(
                    Empresa(
                        escritorio_id=escritorio.id,
                        cnpj="99999999000199",
                        nome="Cliente Novo Ltda",
                    )
                )
                sessao.commit()
                assert (
                    sessao.execute(
                        sqlalchemy.select(sqlalchemy.func.count()).select_from(Empresa.__table__)
                    ).scalar()
                    == 4
                )
        finally:
            engine.dispose()

    def test_sequencia_corrigida_em_todas_as_tabelas(self, origem, destino_postgres):
        """Uma tabela esquecida quebra só quando alguém insere nela."""
        _semear(origem, empresas=2, lancamentos=3)

        migrar_dados(origem, destino_postgres)

        engine = criar_engine(url=destino_postgres)
        try:
            with engine.connect() as conexao:
                atrasadas = []
                for tabela in ("escritorios", "empresas", "usuarios", "ecds", "lancamentos"):
                    proximo = conexao.execute(
                        sqlalchemy.text(
                            "SELECT last_value FROM pg_sequences "
                            "WHERE sequencename = pg_get_serial_sequence("
                            "  :t, 'id')::regclass::text"
                        ).bindparams(t=tabela)
                    ).scalar()
                    maximo = conexao.execute(
                        sqlalchemy.text(f"SELECT COALESCE(MAX(id), 0) FROM {tabela}")  # noqa: S608
                    ).scalar()
                    if proximo is not None and proximo <= maximo:
                        atrasadas.append((tabela, proximo, maximo))
                assert not atrasadas, f"sequência atrás do maior id: {atrasadas}"
        finally:
            engine.dispose()

    def test_tabela_vazia_nao_quebra_a_sequencia(self, origem, destino_postgres):
        """`MAX(id)` de tabela vazia é NULL; `setval(NULL)` levantaria erro."""
        migrar_dados(origem, destino_postgres)  # origem inteira vazia

        engine = criar_engine(url=destino_postgres)
        try:
            with get_session(engine) as sessao:
                sessao.add(Escritorio(nome="Primeiro", slug="primeiro"))
                sessao.commit()
        finally:
            engine.dispose()


class TestCli:
    def test_comando_migra_e_confere(self, origem, destino_sqlite, capsys):
        _semear(origem, empresas=2, lancamentos=3)
        from src.cli import main

        codigo = main(["migrar-dados", "--de", origem, "--para", destino_sqlite])

        saida = capsys.readouterr().out
        assert codigo == 0
        assert "contagens idênticas" in saida
        assert "empresas" in saida

    def test_comando_recusa_destino_ocupado(self, origem, destino_sqlite, capsys):
        _semear(origem)
        migrar_dados(origem, destino_sqlite)
        from src.cli import main

        codigo = main(["migrar-dados", "--de", origem, "--para", destino_sqlite])

        assert codigo == 1
        assert "Recusado" in capsys.readouterr().out

    def test_conferir_sozinho_nao_copia(self, origem, destino_sqlite, capsys):
        _semear(origem, empresas=2)
        from src.cli import main

        codigo = main(["migrar-dados", "--de", origem, "--para", destino_sqlite, "--conferir"])

        assert codigo == 1, "as contagens divergem — nada foi copiado"
        assert "DIVERGÊNCIA" in capsys.readouterr().out
        assert _contar(destino_sqlite, Empresa) == 0
