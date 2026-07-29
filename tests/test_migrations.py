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

import os
import uuid

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
