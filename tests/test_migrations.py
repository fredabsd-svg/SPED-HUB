"""Migrações Alembic (Fase 17, Etapa 3).

O risco real de adotar migrações não é elas falharem — é elas **divergirem**
dos modelos.  Alguém adiciona uma coluna em `models.py`, esquece de gerar a
revisão, e o schema de produção passa a ser diferente do que o código espera.
Isso não gera erro em desenvolvimento, onde `create_all` cria tudo do zero.

Por isso o teste central aqui compara o schema produzido por
``alembic upgrade head`` com o produzido por ``Base.metadata.create_all``,
tabela a tabela e coluna a coluna, nos dois backends.

Para incluir o PostgreSQL, defina ``TEST_DATABASE_URL`` (ver
``tests/test_multibackend.py``).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import inspect

from src.db.migrations import revisao_atual, revisao_head, stamp_head, upgrade_head
from src.db.models import Base, criar_engine, init_db

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

BACKENDS = [
    pytest.param("sqlite", id="sqlite"),
    pytest.param(
        "postgres",
        id="postgres",
        marks=pytest.mark.skipif(
            not TEST_DATABASE_URL,
            reason="defina TEST_DATABASE_URL para exercitar o PostgreSQL",
        ),
    ),
]


class _BancoDescartavel:
    """Banco vazio que se limpa sozinho, em qualquer um dos backends."""

    def __init__(self, tipo: str, tmp_path, sufixo: str):
        self.tipo = tipo
        self.schema = None
        if tipo == "sqlite":
            self.url = f"sqlite:///{tmp_path / f'{sufixo}.db'}"
        else:
            self.schema = f"mig_{uuid.uuid4().hex[:12]}"
            base = criar_engine(url=TEST_DATABASE_URL)
            with base.begin() as conn:
                conn.exec_driver_sql(f'CREATE SCHEMA "{self.schema}"')
            base.dispose()
            self.url = f"{TEST_DATABASE_URL}?options=-csearch_path%3D{self.schema}"

    def limpar(self) -> None:
        if self.schema:
            base = criar_engine(url=TEST_DATABASE_URL)
            with base.begin() as conn:
                conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
            base.dispose()


@pytest.fixture
def banco(request, tmp_path):
    alvo = _BancoDescartavel(request.param, tmp_path, "migrado")
    try:
        yield alvo
    finally:
        alvo.limpar()


def _retrato(engine) -> dict:
    """Descrição comparável do schema: tabelas, colunas, tipos, nulidade, índices."""
    inspetor = inspect(engine)
    retrato = {}
    for tabela in sorted(inspetor.get_table_names()):
        if tabela == "alembic_version":  # controle do Alembic, não faz parte do modelo
            continue
        colunas = {
            c["name"]: (str(c["type"]).upper(), bool(c["nullable"]))
            for c in inspetor.get_columns(tabela)
        }
        indices = {
            (i["name"], tuple(i["column_names"]), bool(i.get("unique")))
            for i in inspetor.get_indexes(tabela)
        }
        pks = tuple(inspetor.get_pk_constraint(tabela).get("constrained_columns") or ())
        fks = {
            (
                tuple(f["constrained_columns"]),
                f["referred_table"],
                tuple(f["referred_columns"]),
            )
            for f in inspetor.get_foreign_keys(tabela)
        }
        retrato[tabela] = {"colunas": colunas, "indices": indices, "pk": pks, "fks": fks}
    return retrato


def _migrar_para(url: str, revisao: str) -> None:
    """Leva `url` até `revisao` exata, injetando a conexão.

    Não dá para usar `command.upgrade(alembic_config(url), ...)` direto: o
    `alembic/env.py` sobrescreve `sqlalchemy.url` com `database_reference()`,
    então a migração iria para o banco configurado do processo — o real, em
    desenvolvimento — e não para o de teste.  Injetar a conexão em
    `cfg.attributes` é o caminho que o `env.py` respeita, o mesmo que
    `upgrade_head` usa.
    """
    from alembic import command
    from src.db.migrations import alembic_config

    engine = criar_engine(url=url)
    cfg = alembic_config(url)
    try:
        with engine.begin() as conexao:
            cfg.attributes["connection"] = conexao
            command.upgrade(cfg, revisao)
    finally:
        engine.dispose()


def _desmigrar_para(url: str, revisao: str) -> None:
    from alembic import command
    from src.db.migrations import alembic_config

    engine = criar_engine(url=url)
    cfg = alembic_config(url)
    try:
        with engine.begin() as conexao:
            cfg.attributes["connection"] = conexao
            command.downgrade(cfg, revisao)
    finally:
        engine.dispose()


class TestLoggingSobreviveAMigracao:
    """Migrar não pode emudecer o resto do processo.

    O `alembic/env.py` chama `fileConfig(...)`, e o padrão dessa função é
    `disable_existing_loggers=True`. "Existing" quer dizer *todo* logger já
    criado que o `alembic.ini` não nomeie — ou seja, todos os `src.*`. Eles
    ficavam mudos no processo inteiro depois de qualquer migração.

    Onde isso aparecia: o `logger.info` de fim de migração não saía, e na
    suíte, qualquer arquivo que rodasse uma migração deixava sem registro
    nenhum os `caplog` dos arquivos seguintes — testes que passavam sozinhos e
    falhavam em conjunto.
    """

    def test_env_py_preserva_os_loggers_existentes(self):
        """A linha é uma só, e some se alguém regenerar o env.py do modelo."""
        env = (Path(__file__).resolve().parent.parent / "alembic" / "env.py").read_text("utf-8")
        chamada = re.search(r"fileConfig\((.*?)\)", env, re.S)
        assert chamada, "alembic/env.py não chama fileConfig"
        assert "disable_existing_loggers=False" in chamada.group(1), (
            "fileConfig sem `disable_existing_loggers=False` emudece todos os "
            "loggers de src.* no processo inteiro"
        )

    def test_migrar_de_verdade_nao_emudece_os_loggers(self, tmp_path):
        """O que importa é o efeito, não a linha: migra e confere que sai log.

        O handler é montado à mão, depois da migração, de propósito. O
        `fileConfig` troca os handlers do logger raiz pelos do `alembic.ini` —
        inclusive o de captura do pytest —, então o `caplog` fica cego pelo
        resto do teste e não serve de instrumento aqui. Trocar handler é o que
        `fileConfig` existe para fazer; o defeito é outro, e é o que se afere:
        o logger continuar emitindo.
        """
        logger = logging.getLogger("src.db.migrations")
        assert not logger.disabled, "logger já vinha desativado antes da migração"

        upgrade_head(f"sqlite:///{tmp_path / 'depois.db'}")

        assert not logger.disabled, "migrar desativou o logger de src.db.migrations"

        capturados: list[str] = []

        class _Coletor(logging.Handler):
            def emit(self, record):
                capturados.append(record.getMessage())

        coletor = _Coletor()
        nivel_anterior = logger.level
        logger.addHandler(coletor)
        logger.setLevel(logging.INFO)
        try:
            logger.info("continua audível")
        finally:
            logger.removeHandler(coletor)
            logger.setLevel(nivel_anterior)

        assert capturados == [
            "continua audível"
        ], "o logger de src.db.migrations parou de emitir depois da migração"


class TestRevisoes:
    def test_existe_uma_head(self):
        assert revisao_head(), "nenhuma revisão encontrada em alembic/versions"

    def test_banco_novo_nao_tem_revisao(self, tmp_path):
        engine = criar_engine(url=f"sqlite:///{tmp_path / 'virgem.db'}")
        try:
            assert revisao_atual(engine) is None
        finally:
            engine.dispose()


@pytest.mark.parametrize("banco", BACKENDS, indirect=True)
class TestUpgrade:
    def test_upgrade_cria_todas_as_tabelas(self, banco):
        upgrade_head(banco.url)
        engine = criar_engine(url=banco.url)
        try:
            criadas = set(inspect(engine).get_table_names())
            assert set(Base.metadata.tables) <= criadas
            assert "alembic_version" in criadas
            assert revisao_atual(engine) == revisao_head()
        finally:
            engine.dispose()

    def test_upgrade_e_idempotente(self, banco):
        assert upgrade_head(banco.url) == upgrade_head(banco.url) == revisao_head()

    def test_schema_migrado_e_identico_ao_dos_modelos(self, banco, tmp_path, request):
        """O teste que impede a divergência silenciosa entre migração e modelo.

        Sem ele, esquecer de gerar a revisão ao mudar `models.py` passa
        despercebido: em desenvolvimento tudo funciona, porque lá o schema
        nasce de `create_all`.
        """
        upgrade_head(banco.url)
        engine_migrado = criar_engine(url=banco.url)

        referencia = _BancoDescartavel(banco.tipo, tmp_path, "referencia")
        engine_modelo = criar_engine(url=referencia.url)
        try:
            init_db(engine_modelo)
            do_modelo = _retrato(engine_modelo)
            do_alembic = _retrato(engine_migrado)

            assert set(do_alembic) == set(do_modelo), (
                "tabelas divergem — falta gerar uma revisão? "
                f"só na migração: {sorted(set(do_alembic) - set(do_modelo))}; "
                f"só nos modelos: {sorted(set(do_modelo) - set(do_alembic))}"
            )
            for tabela in sorted(do_modelo):
                assert (
                    do_alembic[tabela] == do_modelo[tabela]
                ), f"tabela {tabela!r} difere entre a migração e os modelos"
        finally:
            engine_migrado.dispose()
            engine_modelo.dispose()
            referencia.limpar()

    def test_aplicacao_funciona_sobre_o_schema_migrado(self, banco):
        """Migrar precisa produzir um banco utilizável, não só tabelas certas."""
        from pathlib import Path

        from src.db.models import ECD, get_session
        from src.ecd_importer import ECDImportService

        upgrade_head(banco.url)
        engine = criar_engine(url=banco.url)
        session = get_session(engine)
        try:
            fixture = Path(__file__).parent / "fixtures" / "ecd_sample.txt"
            resultado = ECDImportService(session).importar(fixture)
            assert resultado.contas == 23
            assert session.query(ECD).count() == 1
        finally:
            session.close()
            engine.dispose()

    def test_stamp_adota_banco_existente_sem_recriar(self, banco):
        """Instalações anteriores à Etapa 3 já têm o schema: `stamp` as adota."""
        engine = criar_engine(url=banco.url)
        try:
            init_db(engine)  # schema criado por create_all, como nas fases 1-16
            assert revisao_atual(engine) is None

            stamp_head(banco.url)
            assert revisao_atual(engine) == revisao_head()

            # E o upgrade seguinte não tem nada a fazer.
            assert upgrade_head(banco.url) == revisao_head()
        finally:
            engine.dispose()


@pytest.mark.parametrize("banco", BACKENDS, indirect=True)
class TestReconciliacaoDeDeliveries:
    """A revisão `a1c7f2b9e40d` conserta dados, não schema.

    Bancos em uso carregam linhas de `webhook_deliveries` presas em
    `retrying`: toda tentativa que falhava era marcada assim e nunca mais
    tocada.  Elas não eram desfecho do evento, não eram reenviáveis e ninguém
    as resolvia.  Migrar sem reconciliá-las deixaria o painel mentindo e as
    entregas perdidas invisíveis para sempre.
    """

    @staticmethod
    def _semear_presas(url: str) -> None:
        """Um banco como o de antes da revisão, com os dois casos que importam."""
        from sqlalchemy import text

        engine = criar_engine(url=url)
        try:
            with engine.begin() as conexao:
                conexao.execute(
                    text(
                        "INSERT INTO webhooks (url, eventos, descricao, ativo, "
                        "max_retries, total_envios, total_falhas, criado_em) "
                        "VALUES ('https://destino.exemplo/hook', '[\"ecd.importada\"]', "
                        "'', true, 3, 0, 0, '2026-07-01 00:00:00')"
                    )
                )
                wh_id = conexao.execute(text("SELECT id FROM webhooks")).scalar()
                # `concluido_em` acompanha o que a versão anterior gravava:
                # ela o preenchia em `success`/`failed` e deixava NULL em
                # `retrying` — que era justamente o sinal de linha presa.
                linhas = [
                    # Entrega que terminou: a tentativa presa é só histórico.
                    ("retrying", '{"dados":{"ecd_id":1}}', 1, None),
                    ("success", '{"dados":{"ecd_id":1}}', 2, "2026-07-01 00:00:05"),
                    # Entrega abandonada: nenhuma tentativa alcançou desfecho.
                    ("retrying", '{"dados":{"ecd_id":2}}', 1, None),
                    # Linha antiga sem corpo: não dá para agrupar com segurança.
                    ("retrying", None, 1, None),
                ]
                for status, corpo, tentativa, concluido in linhas:
                    conexao.execute(
                        text(
                            "INSERT INTO webhook_deliveries (webhook_id, evento, status, "
                            "request_body, tentativa, criado_em, concluido_em) "
                            "VALUES (:w, 'ecd.importada', :s, :b, :t, "
                            "'2026-07-01 00:00:00', :c)"
                        ),
                        {"w": wh_id, "s": status, "b": corpo, "t": tentativa, "c": concluido},
                    )
        finally:
            engine.dispose()

    @staticmethod
    def _estados(url: str) -> list[tuple]:
        from sqlalchemy import text

        engine = criar_engine(url=url)
        try:
            with engine.connect() as conexao:
                return [
                    (linha[0], linha[1], linha[2] is not None)
                    for linha in conexao.execute(
                        text(
                            "SELECT status, request_body, concluido_em FROM webhook_deliveries "
                            "ORDER BY id"
                        )
                    )
                ]
        finally:
            engine.dispose()

    def test_reconcilia_presas_conforme_a_entrega_terminou_ou_nao(self, banco):
        _migrar_para(banco.url, "6e470dce13c0")
        self._semear_presas(banco.url)

        _migrar_para(banco.url, "a1c7f2b9e40d")

        estados = self._estados(banco.url)
        assert [e[0] for e in estados] == [
            "superseded",  # entrega 1 terminou: tentativa vira histórico
            "success",  # intacta
            "failed",  # entrega 2 nunca terminou: precisa ficar reenviável
            "failed",  # sem corpo: ramo conservador, visível ao reenvio
        ]
        assert "retrying" not in {e[0] for e in estados}

    def test_toda_linha_reconciliada_ganha_concluido_em(self, banco):
        """Sem isso, o histórico do painel a mostra em andamento para sempre."""
        _migrar_para(banco.url, "6e470dce13c0")
        self._semear_presas(banco.url)

        _migrar_para(banco.url, "a1c7f2b9e40d")

        assert all(concluido for _, _, concluido in self._estados(banco.url))

    def test_downgrade_nao_esconde_entrega_perdida(self, banco):
        """Voltar a `retrying` o que virou `failed` sumiria com o evento de novo.

        Perder entrega é pior que carregar uma linha a mais no histórico, então
        o downgrade só desfaz o que era exclusivo da migração.
        """
        _migrar_para(banco.url, "6e470dce13c0")
        self._semear_presas(banco.url)
        _migrar_para(banco.url, "a1c7f2b9e40d")

        _desmigrar_para(banco.url, "6e470dce13c0")

        estados = [e[0] for e in self._estados(banco.url)]
        assert estados.count("failed") == 2, "as perdidas seguem visíveis ao reenvio"
        assert estados.count("retrying") == 1, "só a superada volta ao estado antigo"

    def test_migracao_e_idempotente(self, banco):
        _migrar_para(banco.url, "6e470dce13c0")
        self._semear_presas(banco.url)
        _migrar_para(banco.url, "a1c7f2b9e40d")
        antes = self._estados(banco.url)

        _desmigrar_para(banco.url, "6e470dce13c0")
        _migrar_para(banco.url, "a1c7f2b9e40d")

        assert self._estados(banco.url) == antes


# Última revisão antes de `b182f5a414b4`, que criou `signatarios` e as colunas
# `responsavel_*` de `empresas`.  Um banco nela é o de quem atualizou o código
# da 0.20.0 e não migrou.
ANTES_DOS_SIGNATARIOS = "e3a91c7d5b28"


def _banco_da_versao_anterior(url: str) -> None:
    """Banco parado antes de `b182f5a414b4`, com uma empresa cadastrada."""
    from sqlalchemy import text

    _migrar_para(url, ANTES_DOS_SIGNATARIOS)
    engine = criar_engine(url=url)
    try:
        with engine.begin() as conexao:
            conexao.execute(
                text(
                    "INSERT INTO empresas (cnpj, nome) VALUES ('11222333000181', 'EMPRESA ANTIGA')"
                )
            )
    finally:
        engine.dispose()


def _empresas(engine) -> list[tuple]:
    from src.db.models import Empresa, get_session

    session = get_session(engine)
    try:
        return [(e.nome, e.responsavel_nome) for e in session.query(Empresa).all()]
    finally:
        session.close()


class TestCodigoNovoSobreBancoDaVersaoAnterior:
    """Atualizar o código sem migrar o banco não pode derrubar o painel.

    Depois do merge da 0.20.0, quem subiu o painel direto sobre o SQLite de
    antes recebeu "Internal Server Error" na página inicial: o `create_all`
    criou a tabela nova `signatarios`, mas não acrescentou as colunas
    `responsavel_*` em `empresas`, que já existia, e toda consulta à empresa
    passou a falhar com "no such column".  Pior: com `signatarios` já criada,
    `sped-hub migrar aplicar` falhava em seguida com "table already exists",
    e o banco ficava sem saída pelo caminho documentado.
    """

    def test_painel_le_as_empresas_de_banco_nao_migrado(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'antigo.db'}"
        _banco_da_versao_anterior(url)
        engine = criar_engine(url=url)
        try:
            init_db(engine)  # o que o painel faz ao subir

            assert _empresas(engine) == [("EMPRESA ANTIGA", None)], (
                "o painel não lê a empresa de um banco que ainda não migrou — "
                "é o 500 da página inicial"
            )
        finally:
            engine.dispose()

    def test_banco_sem_alembic_tambem_ganha_as_colunas(self, tmp_path):
        """Banco nascido do `create_all`, que nunca passou pelo Alembic."""
        url = f"sqlite:///{tmp_path / 'sem_alembic.db'}"
        _banco_da_versao_anterior(url)
        engine = criar_engine(url=url)
        try:
            with engine.begin() as conexao:
                conexao.exec_driver_sql("DROP TABLE alembic_version")
            assert revisao_atual(engine) is None

            init_db(engine)

            assert _empresas(engine) == [("EMPRESA ANTIGA", None)]
        finally:
            engine.dispose()

    @pytest.mark.parametrize("banco", BACKENDS, indirect=True)
    def test_migrar_depois_que_o_painel_subiu(self, banco):
        """A ordem errada — painel antes da migração — ainda termina em `head`."""
        _banco_da_versao_anterior(banco.url)
        engine = criar_engine(url=banco.url)
        try:
            init_db(engine)  # cria `signatarios` antes da migração que a cria

            assert upgrade_head(banco.url) == revisao_head(), (
                "`migrar aplicar` falha depois que o painel subiu sobre o banco "
                "antigo — o banco fica sem caminho documentado para migrar"
            )
            assert _empresas(engine) == [("EMPRESA ANTIGA", None)]
        finally:
            engine.dispose()

    @pytest.mark.parametrize("banco", BACKENDS, indirect=True)
    def test_a_migracao_mais_recente_aplica_depois_que_o_painel_subiu(self, banco):
        """Vale para toda revisão nova, não só a `b182f5a414b4`.

        Migração que cria tabela, índice ou coluna sem conferir se já existe
        quebra aqui: o painel sobe antes dela e cria o que ela ia criar.
        """
        from alembic.script import ScriptDirectory

        from src.db.migrations import alembic_config

        anterior = ScriptDirectory.from_config(alembic_config()).get_revision(revisao_head())
        _migrar_para(banco.url, anterior.down_revision)
        engine = criar_engine(url=banco.url)
        try:
            init_db(engine)

            assert upgrade_head(banco.url) == revisao_head(), (
                f"a revisão {revisao_head()} não confere se o que cria já existe "
                "(ADR 0013) — falha em quem subiu o painel antes de migrar"
            )
        finally:
            engine.dispose()

    def test_schema_completado_e_igual_ao_migrado(self, tmp_path):
        """O que o painel completa sozinho é o mesmo que a migração faria."""
        completado = f"sqlite:///{tmp_path / 'completado.db'}"
        _banco_da_versao_anterior(completado)
        migrado = f"sqlite:///{tmp_path / 'migrado.db'}"
        _banco_da_versao_anterior(migrado)

        engine_completado = criar_engine(url=completado)
        engine_migrado = criar_engine(url=migrado)
        try:
            init_db(engine_completado)
            upgrade_head(migrado)

            assert _retrato(engine_completado) == _retrato(engine_migrado)
        finally:
            engine_completado.dispose()
            engine_migrado.dispose()


class TestCompletarColunas:
    """O que `completar_colunas` acrescenta, e o que ela deixa para a migração."""

    @staticmethod
    def _tabela_antiga(tmp_path):
        engine = criar_engine(url=f"sqlite:///{tmp_path / 'colunas.db'}")
        with engine.begin() as conexao:
            conexao.exec_driver_sql("CREATE TABLE coisas (id INTEGER PRIMARY KEY)")
            conexao.exec_driver_sql("INSERT INTO coisas (id) VALUES (1)")
        return engine

    @staticmethod
    def _modelo():
        from sqlalchemy import Column, Integer, MetaData, String, Table

        metadata = MetaData()
        Table(
            "coisas",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("apelido", String(20), nullable=True),
            Column("contagem", Integer, nullable=False, server_default="0"),
            Column("obrigatoria", String(5), nullable=False),
            Column("codigo", String(5), unique=True),
            Column("nome", String(20)),
        )
        Table(
            "tabela_nova",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("rotulo", String(10)),
        )
        return metadata

    def test_acrescenta_so_o_que_o_sqlite_aceita_numa_tabela_com_linhas(self, tmp_path):
        from src.db.models import completar_colunas

        engine = self._tabela_antiga(tmp_path)
        try:
            acrescentadas = completar_colunas(engine, self._modelo())

            assert acrescentadas == ["coisas.apelido", "coisas.contagem", "coisas.nome"]
            colunas = {c["name"] for c in inspect(engine).get_columns("coisas")}
            assert colunas == {"id", "apelido", "contagem", "nome"}, (
                "coluna NOT NULL sem default ou UNIQUE não entra por ALTER TABLE "
                "numa tabela com linhas — fica para a migração"
            )
            assert (
                "tabela_nova" not in inspect(engine).get_table_names()
            ), "tabela nova é trabalho do create_all, não desta função"
            with engine.connect() as conexao:
                assert conexao.exec_driver_sql("SELECT contagem FROM coisas").scalar() == 0
        finally:
            engine.dispose()

    @pytest.mark.skipif(
        not TEST_DATABASE_URL, reason="defina TEST_DATABASE_URL para exercitar o PostgreSQL"
    )
    def test_postgres_fica_so_com_a_migracao(self, tmp_path):
        """PostgreSQL é versionado por migração: o painel não mexe no schema dele."""
        from src.db.models import completar_colunas

        alvo = _BancoDescartavel("postgres", tmp_path, "sem_completar")
        try:
            _banco_da_versao_anterior(alvo.url)
            engine = criar_engine(url=alvo.url)
            try:
                assert completar_colunas(engine) == []
                colunas = {c["name"] for c in inspect(engine).get_columns("empresas")}
                assert "responsavel_nome" not in colunas
            finally:
                engine.dispose()
        finally:
            alvo.limpar()

    def test_segunda_chamada_nao_faz_nada(self, tmp_path):
        from src.db.models import completar_colunas

        engine = self._tabela_antiga(tmp_path)
        try:
            completar_colunas(engine, self._modelo())
            assert completar_colunas(engine, self._modelo()) == []
        finally:
            engine.dispose()

    def test_avisa_o_que_nao_conseguiu_acrescentar(self, tmp_path, caplog):
        from src.db.models import completar_colunas

        engine = self._tabela_antiga(tmp_path)
        try:
            with caplog.at_level(logging.WARNING, logger="sped-hub.db"):
                completar_colunas(engine, self._modelo())
            avisos = " ".join(r.getMessage() for r in caplog.records)
            assert "coisas.obrigatoria" in avisos
            assert "coisas.codigo" in avisos
            assert "migrar status" in avisos
        finally:
            engine.dispose()


class TestPainelSobreBancoDaVersaoAnterior:
    """O mesmo defeito pela porta de entrada: login e página inicial (§7.1).

    O banco nasce na versão atual, recebe escritório, usuário e ECD, e perde
    o que a `b182f5a414b4` trouxe: `signatarios` e as colunas
    `responsavel_*`.  É o SQLite de quem usava a versão anterior e atualizou
    só o código.
    """

    SENHA = "senha-de-teste"

    @pytest.fixture
    def cliente(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from src.audit import init_audit_service
        from src.auth import init_auth
        from src.db.models import Escritorio, Usuario, get_session
        from src.ecd_importer import ECDImportService
        from src.ratelimit import init_limiter
        from src.settings import reset_settings_cache

        preparo = f"sqlite:///{tmp_path / 'preparo.db'}"
        monkeypatch.setenv("DATABASE_URL", preparo)
        monkeypatch.delenv("SPED_HUB_DB", raising=False)
        reset_settings_cache()

        upgrade_head(preparo)
        engine = criar_engine(url=preparo)
        try:
            with get_session(engine) as sessao:
                escritorio = Escritorio(nome="Escritório", slug="escritorio")
                sessao.add(escritorio)
                sessao.flush()
                senha_hash, salt = Usuario.hash_senha(self.SENHA)
                sessao.add(
                    Usuario(
                        email="contador@teste.local",
                        nome="Contador",
                        senha_hash=senha_hash,
                        salt=salt,
                        admin=True,
                        escritorio_id=escritorio.id,
                    )
                )
                sessao.commit()
                escritorio_id = escritorio.id
            with get_session(engine) as sessao:
                amostra = Path(__file__).parent / "fixtures" / "ecd_sample.txt"
                ecd_id = (
                    ECDImportService(sessao).importar(amostra, escritorio_id=escritorio_id).ecd_id
                )
        finally:
            engine.dispose()

        # O `downgrade` recria `empresas` pelo modo batch e esbarra nas chaves
        # estrangeiras das ECDs; o SQLite remove a coluna direto.
        engine = criar_engine(url=preparo)
        try:
            with engine.begin() as conexao:
                for coluna in ("responsavel_nome", "responsavel_cpf", "responsavel_qualificacao"):
                    conexao.exec_driver_sql(f"ALTER TABLE empresas DROP COLUMN {coluna}")
                conexao.exec_driver_sql("DROP TABLE signatarios")
                conexao.exec_driver_sql(
                    f"UPDATE alembic_version SET version_num = '{ANTES_DOS_SIGNATARIOS}'"
                )
            colunas = {c["name"] for c in inspect(engine).get_columns("empresas")}
            assert "responsavel_nome" not in colunas, "o banco não voltou à versão anterior"
        finally:
            engine.dispose()

        # O painel encontra o banco num arquivo que nenhuma engine do processo
        # conhece, como ao subir.  No de preparo, a importação deixou uma
        # engine em cache marcada como pronta (`init_db_once`) com o schema
        # novo, e ninguém conferiria as colunas de novo.
        shutil.copy(tmp_path / "preparo.db", tmp_path / "painel_antigo.db")
        referencia = f"sqlite:///{tmp_path / 'painel_antigo.db'}"
        monkeypatch.setenv("DATABASE_URL", referencia)
        reset_settings_cache()

        init_auth(referencia)
        init_audit_service(referencia)
        init_limiter(referencia)
        from src.dashboard.app import app

        cliente = TestClient(app, raise_server_exceptions=False)
        resposta = cliente.post(
            "/api/login", data={"email": "contador@teste.local", "senha": self.SENHA}
        )
        assert resposta.status_code == 200, resposta.text
        return cliente, ecd_id

    def test_pagina_inicial_abre(self, cliente):
        cliente, _ = cliente
        resposta = cliente.get("/")

        assert resposta.status_code == 200, (
            f"a página inicial respondeu {resposta.status_code} sobre o banco da "
            "versão anterior — é o 'Internal Server Error' de quem atualizou sem migrar"
        )

    def test_assinaturas_da_ecd_abrem(self, cliente):
        cliente, ecd_id = cliente
        resposta = cliente.get("/api/assinaturas", params={"ecd_id": ecd_id})

        assert resposta.status_code == 200, resposta.text[:200]
