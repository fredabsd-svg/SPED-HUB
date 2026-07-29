"""Portabilidade entre SQLite e PostgreSQL (Fase 17, Etapa 2).

Rodam sempre em SQLite.  Para incluir o PostgreSQL, aponte ``TEST_DATABASE_URL``
para um banco descartável::

    TEST_DATABASE_URL=postgresql+psycopg://user@host:5432/sped_hub_test pytest

Sem a variável, os casos de Postgres pulam — o CI padrão não tem servidor.

O objetivo não é repetir a suíte inteira em dois bancos: é cobrir onde os dois
divergem em silêncio.  As duas divergências abaixo foram encontradas assim, e
nenhuma levantava erro em SQLite:

  * ``LIKE`` é case-insensitive para ASCII no SQLite e case-sensitive no
    Postgres — a busca por histórico devolvia resultados em um e nada no outro;
  * ``String(n)`` é ignorado pelo SQLite e imposto pelo Postgres — um cabeçalho
    ``User-Agent`` grande derrubava o login só em Postgres.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import inspect

from src.db.models import (
    ECD,
    AuditLog,
    Base,
    Sessao,
    criar_engine,
    get_session,
    init_db,
    truncar_para_coluna,
)
from tests.conftest import url_com_senha

FIXTURE = Path(__file__).parent / "fixtures" / "ecd_sample.txt"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _urls_para_testar(tmp_path_factory) -> list[pytest.param]:
    casos = [pytest.param("sqlite", id="sqlite")]
    casos.append(
        pytest.param(
            "postgres",
            id="postgres",
            marks=pytest.mark.skipif(
                not TEST_DATABASE_URL,
                reason="defina TEST_DATABASE_URL para exercitar o PostgreSQL",
            ),
        )
    )
    return casos


@pytest.fixture(params=_urls_para_testar(None))
def backend(request, tmp_path):
    """Devolve uma URL de banco limpa para o backend do parâmetro."""
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'portabilidade.db'}"
        engine = criar_engine(url=url)
    else:
        # Schema próprio por teste: isola sem exigir CREATE DATABASE.
        engine = criar_engine(url=TEST_DATABASE_URL)
        schema = f"teste_{uuid.uuid4().hex[:12]}"
        with engine.begin() as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        engine.dispose()
        url = f"{TEST_DATABASE_URL}?options=-csearch_path%3D{schema}"
        engine = criar_engine(url=url)

    try:
        init_db(engine)
        yield engine
    finally:
        if request.param == "postgres":
            with engine.begin() as conn:
                conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        engine.dispose()


@pytest.fixture
def sessao_com_ecd(backend):
    from src.ecd_importer import ECDImportService

    session = get_session(backend)
    try:
        ECDImportService(session).importar(FIXTURE)
        yield session
    finally:
        session.close()


class TestSchema:
    def test_todas_as_tabelas_sao_criadas(self, backend):
        criadas = set(inspect(backend).get_table_names())
        esperadas = set(Base.metadata.tables)
        assert esperadas <= criadas, f"faltando: {sorted(esperadas - criadas)}"

    def test_escrituracao_importa(self, sessao_com_ecd):
        from src.db.models import Lancamento, Partida, PlanoConta

        assert sessao_com_ecd.query(ECD).count() == 1
        assert sessao_com_ecd.query(PlanoConta).count() == 23
        assert sessao_com_ecd.query(Lancamento).count() == 9
        assert sessao_com_ecd.query(Partida).count() == 18


class TestRelatoriosPortaveis:
    """Os números contábeis não podem depender do banco."""

    def test_balancete_confere(self, sessao_com_ecd):
        from src.filters.engine import FilterCriteria
        from src.reports.balancete import Balancete

        _, linhas = Balancete(sessao_com_ecd, 1).gerar(FilterCriteria())
        assert linhas

    def test_balanco_fecha(self, sessao_com_ecd):
        from src.filters.engine import FilterCriteria
        from src.reports.balanco import BalancoPatrimonial

        _, grupos, totais = BalancoPatrimonial(sessao_com_ecd, 1).gerar(FilterCriteria())
        assert totais["ativo"] == pytest.approx(totais["passivo_pl"], abs=0.01)

    @pytest.mark.parametrize("relatorio", ["dre", "dfc", "diario"])
    def test_demais_relatorios_geram(self, sessao_com_ecd, relatorio):
        from src.filters.engine import FilterCriteria
        from src.reports.dfc import DFC
        from src.reports.diario import LivroDiario
        from src.reports.dre import DRE

        classes = {"dre": DRE, "dfc": DFC, "diario": LivroDiario}
        assert classes[relatorio](sessao_com_ecd, 1).gerar(FilterCriteria())

    def test_validacoes_rodam(self, sessao_com_ecd):
        from src.validators.integridade import ValidadorIntegridade

        assert ValidadorIntegridade(sessao_com_ecd, 1).validar_todas() is not None


class TestBuscaTextualCaseInsensitive:
    """`LIKE` diverge entre os backends; `ilike` uniformiza."""

    @pytest.mark.parametrize("termo", ["RECEBIMENTO", "recebimento", "ReCeBiMeNtO"])
    def test_busca_por_historico_ignora_caixa(self, sessao_com_ecd, termo):
        from src.filters.engine import FilterCriteria, FilterEngine

        encontrados = FilterEngine(sessao_com_ecd, 1).aplicar_lancamentos(
            FilterCriteria(hist_texto=termo)
        )
        assert len(encontrados) == 2, (
            f"busca por {termo!r} devolveu {len(encontrados)}; com `like` em vez de "
            "`ilike`, o Postgres devolve 0 e o filtro morre em silêncio"
        )


class TestLimitesDeColuna:
    """`String(n)` é ignorado pelo SQLite e imposto pelo Postgres."""

    def test_truncar_respeita_o_limite_declarado(self):
        assert len(truncar_para_coluna(Sessao, "user_agent", "M" * 1000)) == 512
        assert len(truncar_para_coluna(AuditLog, "recurso", "r" * 900)) == 255
        assert truncar_para_coluna(Sessao, "user_agent", None) is None
        assert truncar_para_coluna(Sessao, "user_agent", "curto") == "curto"

    def test_login_aceita_user_agent_grande(self, backend):
        """Qualquer cliente pode mandar um User-Agent de 1 KB."""
        from src.auth import AuthService

        auth = AuthService(url_com_senha(backend))
        auth.registrar("limite@teste.com", "L", "senha123456")
        _, token, _, _ = auth.login(
            "limite@teste.com", "senha123456", ip="1" * 80, user_agent="M" * 1000
        )
        assert auth.validar_token(token) is not None

    def test_auditoria_sobrevive_a_campos_grandes(self, backend):
        """Perder a trilha justamente na tentativa suspeita é o pior resultado."""
        from src.audit import AuditService

        servico = AuditService(url_com_senha(backend))
        servico.registrar(
            acao="auth.login",
            recurso="/api/" + "x" * 900,
            usuario_email="e" * 600,
            ip="i" * 90,
            status_code=401,
        )
        session = get_session(backend)
        try:
            assert session.query(AuditLog).count() == 1
        finally:
            session.close()
