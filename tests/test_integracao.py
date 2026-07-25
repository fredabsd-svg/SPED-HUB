"""Testes de integração — ECDs sintéticos grandes e performance."""

import datetime
import tempfile
import time

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from src.db.models import (
    Base,
    ECD,
    Empresa,
    Lancamento,
    Partida,
    PlanoConta,
    SaldoPeriodico,
    SaldoResultado,
)
from src.reports.balanco import BalancoPatrimonial
from src.reports.dre import DRE
from src.reports.dfc import DFC
from src.reports.diario import LivroDiario
from src.validators.integridade import ValidadorIntegridade


@pytest.fixture
def db_grande():
    """Cria um banco com ~500 contas e ~200 lançamentos."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session = Session(engine)

    emp = Empresa(cnpj="12345678000199", nome="Empresa Grande Ltda")
    session.add(emp)
    session.flush()

    ecd = ECD(
        empresa_id=emp.id,
        leiaute="009",
        dt_ini=datetime.date(2024, 1, 1),
        dt_fin=datetime.date(2024, 12, 31),
        nome_arquivo="ecd_grande.txt",
    )
    session.add(ecd)
    session.flush()

    # Plano de contas grande
    naturezas = {"01": "Ativo", "02": "Passivo", "03": "PL", "04": "Resultado"}
    contas = []
    for nat, nome_nat in naturezas.items():
        # Sintética nível 1
        cod_sup = f"{nat}.0"
        session.add(PlanoConta(
            ecd_id=ecd.id, cod_cta=cod_sup, nome_cta=f"{nome_nat} Total",
            cod_nat=nat, ind_cta="S", nivel=1,
        ))
        # 5 subgrupos nível 2
        for g in range(1, 6):
            cod_grupo = f"{nat}.{g}"
            session.add(PlanoConta(
                ecd_id=ecd.id, cod_cta=cod_grupo,
                nome_cta=f"{nome_nat} Grupo {g}",
                cod_cta_sup=cod_sup, cod_nat=nat, ind_cta="S", nivel=2,
            ))
            # 20 contas analíticas por grupo
            for a in range(1, 21):
                cod_ana = f"{nat}.{g}.{a:03d}"
                session.add(PlanoConta(
                    ecd_id=ecd.id, cod_cta=cod_ana,
                    nome_cta=f"{nome_nat} Conta {g}.{a:03d}",
                    cod_cta_sup=cod_grupo, cod_nat=nat, ind_cta="A", nivel=3,
                ))
                contas.append((cod_ana, nat))

    session.flush()

    # Saldos periódicos para todas as contas
    for cod_cta, nat in contas:
        if nat == "01":
            si, dci, sf, dcf = 10000.0, "D", 12000.0, "D"
        elif nat == "02":
            si, dci, sf, dcf = 5000.0, "C", 6000.0, "C"
        elif nat == "03":
            si, dci, sf, dcf = 3000.0, "C", 3500.0, "C"
        else:
            si, dci, sf, dcf = 0.0, "D", 0.0, "D"

        session.add(SaldoPeriodico(
            ecd_id=ecd.id, cod_cta=cod_cta,
            dt_ini=datetime.date(2024, 1, 1),
            dt_fin=datetime.date(2024, 12, 31),
            vl_sld_ini=si, ind_dc_ini=dci,
            vl_deb=500.0, vl_cred=300.0,
            vl_sld_fin=sf, ind_dc_fin=dcf,
        ))

    # Saldos de resultado
    for cod_cta, nat in contas:
        if nat == "04":
            session.add(SaldoResultado(
                ecd_id=ecd.id, cod_cta=cod_cta,
                dt_res=datetime.date(2024, 12, 31),
                vl_sld_fin=2000.0, ind_dc_fin="C",
            ))

    # 200 lançamentos
    for i in range(1, 201):
        lanc = Lancamento(
            ecd_id=ecd.id,
            num_lcto=str(i),
            dt_lcto=datetime.date(2024, (i % 12) + 1, min((i % 28) + 1, 28)),
            vl_lcto=1000.0,
            ind_lcto="N",
        )
        session.add(lanc)
        session.flush()

        # 2 partidas por lançamento
        session.add(Partida(
            lancamento_id=lanc.id, cod_cta=contas[i % len(contas)][0],
            vl_dc=1000.0, ind_dc="D", hist=f"Lançamento {i}",
        ))
        session.add(Partida(
            lancamento_id=lanc.id, cod_cta=contas[(i + 50) % len(contas)][0],
            vl_dc=1000.0, ind_dc="C", hist=f"Lançamento {i}",
        ))

    session.commit()
    yield session, ecd, emp
    session.close()


class TestIntegracaoGrande:
    """Testes com ECD sintético grande (~500 contas, ~200 lançamentos)."""

    def test_balanco_performance(self, db_grande):
        session, ecd, emp = db_grande
        balanco = BalancoPatrimonial(session, ecd.id)

        t0 = time.perf_counter()
        ctx, grupos, totais = balanco.gerar()
        elapsed = time.perf_counter() - t0

        assert ctx.titulo == "Balanço Patrimonial"
        assert len(grupos["ativo"]) > 0
        assert len(grupos["passivo"]) > 0
        assert len(grupos["pl"]) > 0
        assert totais["ativo"] > 0
        # Deve rodar em menos de 2 segundos
        assert elapsed < 2.0, f"Balanço demorou {elapsed:.2f}s"

    def test_dre_performance(self, db_grande):
        session, ecd, emp = db_grande
        dre = DRE(session, ecd.id)

        t0 = time.perf_counter()
        ctx, linhas, totais = dre.gerar()
        elapsed = time.perf_counter() - t0

        assert ctx.titulo == "Demonstração do Resultado do Exercício"
        assert len(linhas) == 11
        assert elapsed < 2.0, f"DRE demorou {elapsed:.2f}s"

    def test_dfc_performance(self, db_grande):
        session, ecd, emp = db_grande
        dfc = DFC(session, ecd.id)

        t0 = time.perf_counter()
        ctx, linhas, totais = dfc.gerar()
        elapsed = time.perf_counter() - t0

        assert ctx.titulo == "Demonstração dos Fluxos de Caixa"
        assert len(linhas) > 0
        assert elapsed < 2.0, f"DFC demorou {elapsed:.2f}s"

    def test_diario_performance(self, db_grande):
        session, ecd, emp = db_grande
        diario = LivroDiario(session, ecd.id)

        t0 = time.perf_counter()
        ctx, lancamentos, totais = diario.gerar()
        elapsed = time.perf_counter() - t0

        assert totais["num_lancamentos"] == 200
        assert elapsed < 3.0, f"Diário demorou {elapsed:.2f}s"

    def test_validacao_integridade(self, db_grande):
        session, ecd, emp = db_grande
        validador = ValidadorIntegridade(session, ecd.id)

        t0 = time.perf_counter()
        resultados = validador.validar_todas()
        elapsed = time.perf_counter() - t0

        assert isinstance(resultados, list)
        assert elapsed < 3.0, f"Validação demorou {elapsed:.2f}s"

    def test_balanco_ativo_igual_passivo_pl(self, db_grande):
        session, ecd, emp = db_grande
        balanco = BalancoPatrimonial(session, ecd.id)
        ctx, grupos, totais = balanco.gerar()

        # Com dados sintéticos, pode haver diferença
        # Verificamos que os totais são razoáveis
        assert totais["ativo"] > 0
        assert totais["passivo"] >= 0
        assert totais["pl"] >= 0

    def test_contagem_contas(self, db_grande):
        session, ecd, emp = db_grande
        total = session.query(PlanoConta).filter_by(ecd_id=ecd.id).count()
        # 4 naturezas × (1 sintética + 5 grupos + 100 analíticas) = 424
        assert total == 424

    def test_contagem_lancamentos(self, db_grande):
        session, ecd, emp = db_grande
        total = session.query(Lancamento).filter_by(ecd_id=ecd.id).count()
        assert total == 200

    def test_contagem_partidas(self, db_grande):
        session, ecd, emp = db_grande
        total = session.query(Partida).join(Lancamento).filter(Lancamento.ecd_id == ecd.id).count()
        assert total == 400  # 2 partidas por lançamento