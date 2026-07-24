"""Testes para Balanço Patrimonial, DRE, Livro Diário e Export Engine."""

import datetime
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Setup path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.models import (
    Aglutinacao,
    ECD,
    Empresa,
    Lancamento,
    Mapeamento,
    Partida,
    PlanoConta,
    SaldoPeriodico,
    SaldoResultado,
    criar_engine,
    get_session,
    init_db,
)
from src.filters.engine import FilterCriteria
from src.reports.balanco import BalancoPatrimonial
from src.reports.dre import DRE
from src.reports.diario import LivroDiario
from src.reports.export_engine import ExportEngine, WhiteLabel
from src.reports.base import ReportContext


@pytest.fixture
def db():
    """Banco em memória com dados de teste."""
    engine = criar_engine(":memory:")
    init_db(engine)
    session = get_session(engine)

    # Empresa
    emp = Empresa(cnpj="00123456000199", nome="Empresa Teste Ltda")
    session.add(emp)
    session.flush()

    # ECD
    ecd = ECD(
        empresa_id=emp.id,
        leiaute="009",
        dt_ini=datetime.date(2024, 1, 1),
        dt_fin=datetime.date(2024, 12, 31),
        hash_arquivo="abc123",
    )
    session.add(ecd)
    session.flush()

    # Plano de Contas
    contas_data = [
        # Ativo (01)
        ("1", None, "ATIVO", "01", "S", 1),
        ("1.1", "1", "Ativo Circulante", "01", "S", 2),
        ("1.1.1", "1.1", "Caixa", "01", "A", 3),
        ("1.1.2", "1.1", "Bancos", "01", "A", 3),
        ("1.1.3", "1.1", "Clientes", "01", "A", 3),
        ("1.2", "1", "Ativo Não Circulante", "01", "S", 2),
        ("1.2.1", "1.2", "Imobilizado", "01", "A", 3),
        # Passivo (02)
        ("2", None, "PASSIVO", "02", "S", 1),
        ("2.1", "2", "Passivo Circulante", "02", "S", 2),
        ("2.1.1", "2.1", "Fornecedores", "02", "A", 3),
        ("2.1.2", "2.1", "Salários a Pagar", "02", "A", 3),
        ("2.2", "2", "Passivo Não Circulante", "02", "S", 2),
        ("2.2.1", "2.2", "Empréstimos LP", "02", "A", 3),
        # PL (03)
        ("3", None, "PATRIMÔNIO LÍQUIDO", "03", "S", 1),
        ("3.1", "3", "Capital Social", "03", "A", 2),
        ("3.2", "3", "Lucros Acumulados", "03", "A", 2),
        # Resultado (04)
        ("4", None, "RESULTADO", "04", "S", 1),
        ("4.1", "4", "Receita de Vendas", "04", "A", 2),
        ("4.2", "4", "CMV", "04", "A", 2),
        ("4.3", "4", "Despesas Administrativas", "04", "A", 2),
        ("4.4", "4", "Receitas Financeiras", "04", "A", 2),
        ("4.5", "4", "Despesas Financeiras", "04", "A", 2),
        ("4.6", "4", "IRPJ/CSLL", "04", "A", 2),
    ]
    for cod_cta, sup, nome, nat, ind, niv in contas_data:
        session.add(PlanoConta(
            ecd_id=ecd.id, cod_cta=cod_cta, cod_cta_sup=sup,
            nome_cta=nome, cod_nat=nat, ind_cta=ind, nivel=niv,
        ))
    session.flush()

    # Saldos Periódicos (I155) — valores sinalizados internamente
    saldos_data = [
        # Ativo: débito = positivo
        ("1.1.1", 50000.0, "D", 10000.0, 5000.0, 55000.0, "D"),
        ("1.1.2", 120000.0, "D", 30000.0, 20000.0, 130000.0, "D"),
        ("1.1.3", 80000.0, "D", 50000.0, 30000.0, 100000.0, "D"),
        ("1.2.1", 200000.0, "D", 0.0, 10000.0, 190000.0, "D"),
        # Passivo: crédito = positivo no SPED
        ("2.1.1", 60000.0, "C", 20000.0, 40000.0, 80000.0, "C"),
        ("2.1.2", 15000.0, "C", 5000.0, 0.0, 20000.0, "C"),
        ("2.2.1", 100000.0, "C", 0.0, 10000.0, 90000.0, "C"),
        # PL: crédito = positivo
        ("3.1", 200000.0, "C", 0.0, 0.0, 200000.0, "C"),
        ("3.2", 85000.0, "C", 0.0, 0.0, 85000.0, "C"),
    ]
    for cod_cta, si, dci, deb, cred, sf, dcf in saldos_data:
        session.add(SaldoPeriodico(
            ecd_id=ecd.id, cod_cta=cod_cta,
            dt_ini=datetime.date(2024, 1, 1),
            dt_fin=datetime.date(2024, 12, 31),
            vl_sld_ini=si, ind_dc_ini=dci,
            vl_deb=deb, vl_cred=cred,
            vl_sld_fin=sf, ind_dc_fin=dcf,
        ))
    session.flush()

    # Saldos Resultado (I355)
    res_data = [
        ("4.1", 500000.0, "C"),   # Receita Vendas
        ("4.2", 300000.0, "D"),   # CMV
        ("4.3", 80000.0, "D"),    # Desp Admin
        ("4.4", 5000.0, "C"),     # Rec Financeiras
        ("4.5", 10000.0, "D"),    # Desp Financeiras
        ("4.6", 30000.0, "D"),    # IRPJ/CSLL
    ]
    for cod_cta, sf, dcf in res_data:
        session.add(SaldoResultado(
            ecd_id=ecd.id, cod_cta=cod_cta,
            dt_res=datetime.date(2024, 12, 31),
            vl_sld_fin=sf, ind_dc_fin=dcf,
        ))
    session.flush()

    # Lançamentos e Partidas
    lanc = Lancamento(
        ecd_id=ecd.id, num_lcto="1",
        dt_lcto=datetime.date(2024, 1, 15),
        vl_lcto=10000.0, ind_lcto="N",
    )
    session.add(lanc)
    session.flush()

    session.add(Partida(lancamento_id=lanc.id, cod_cta="1.1.1", vl_dc=10000.0, ind_dc="D", hist="Venda à vista"))
    session.add(Partida(lancamento_id=lanc.id, cod_cta="4.1", vl_dc=10000.0, ind_dc="C", hist="Receita de venda"))
    session.flush()

    # Aglutinações para teste de publicação
    session.add(Aglutinacao(
        plano_conta_id=session.query(PlanoConta).filter_by(ecd_id=ecd.id, cod_cta="1.1.1").first().id,
        cod_agl="J100",
    ))
    session.add(Aglutinacao(
        plano_conta_id=session.query(PlanoConta).filter_by(ecd_id=ecd.id, cod_cta="1.1.2").first().id,
        cod_agl="J100",
    ))
    session.flush()

    session.commit()
    yield session, ecd, emp
    session.close()


# ═══════════════════════════════════════════════════════════════════════════
# Balanço Patrimonial
# ═══════════════════════════════════════════════════════════════════════════

class TestBalancoPatrimonial:
    def test_gerar_visao_hierarquica(self, db):
        session, ecd, emp = db
        balanco = BalancoPatrimonial(session, ecd.id)
        ctx, grupos, totais = balanco.gerar()

        assert ctx.titulo == "Balanço Patrimonial"
        assert len(grupos["ativo"]) > 0
        assert len(grupos["passivo"]) > 0
        assert len(grupos["pl"]) > 0
        assert totais["ativo"] > 0
        assert totais["passivo"] > 0
        assert totais["pl"] > 0

    def test_ativo_igual_passivo_mais_pl(self, db):
        session, ecd, emp = db
        balanco = BalancoPatrimonial(session, ecd.id)
        ctx, grupos, totais = balanco.gerar()

        assert abs(totais["ativo"] - totais["passivo_pl"]) < 0.02

    def test_visao_publicacao(self, db):
        session, ecd, emp = db
        balanco = BalancoPatrimonial(session, ecd.id)
        ctx, grupos, totais = balanco.gerar_publicacao()

        assert ctx.titulo == "Balanço Patrimonial (Publicação)"
        # Deve ter pelo menos o grupo J100
        assert len(grupos["ativo"]) >= 1

    def test_filtro_natureza(self, db):
        session, ecd, emp = db
        balanco = BalancoPatrimonial(session, ecd.id)
        criterios = FilterCriteria()
        criterios.cod_nat = ["01"]
        ctx, grupos, totais = balanco.gerar(criterios)

        # Só ativo
        assert len(grupos["ativo"]) > 0
        assert len(grupos["passivo"]) == 0
        assert len(grupos["pl"]) == 0


# ═══════════════════════════════════════════════════════════════════════════
# DRE
# ═══════════════════════════════════════════════════════════════════════════

class TestDRE:
    def test_gerar_dre(self, db):
        session, ecd, emp = db
        dre = DRE(session, ecd.id)
        ctx, linhas, totais = dre.gerar()

        assert ctx.titulo == "Demonstração do Resultado do Exercício"
        assert len(linhas) == 11  # 11 degraus
        assert "resultado_liquido" in totais

    def test_dre_tem_total(self, db):
        session, ecd, emp = db
        dre = DRE(session, ecd.id)
        ctx, linhas, totais = dre.gerar()

        # Última linha deve ser o total
        assert linhas[-1].tipo == "total"
        assert "Resultado Líquido" in linhas[-1].descricao

    def test_dre_com_mapeamento_personalizado(self, db):
        session, ecd, emp = db
        # Cria mapeamento customizado
        session.add(Mapeamento(
            empresa_id=emp.id, tipo="dre",
            cod_cta="4.1", categoria="receita_bruta", ordem=1,
        ))
        session.add(Mapeamento(
            empresa_id=emp.id, tipo="dre",
            cod_cta="4.2", categoria="custos", ordem=2,
        ))
        session.commit()

        dre = DRE(session, ecd.id)
        ctx, linhas, totais = dre.gerar(empresa_id=emp.id)

        assert len(linhas) == 11

    def test_dre_resultado_liquido_calculo(self, db):
        session, ecd, emp = db
        dre = DRE(session, ecd.id)
        ctx, linhas, totais = dre.gerar()

        # Receita 500k - CMV 300k - Desp 80k + Rec Fin 5k - Desp Fin 10k - IRPJ 30k = 85k
        # Internamente: receitas são crédito (negativo), despesas são débito (positivo)
        # DRE: receitas positivas, despesas negativas
        # 500k - 300k - 80k + 5k - 10k - 30k = 85k
        assert abs(totais["resultado_liquido"] - 85000.0) < 1.0


# ═══════════════════════════════════════════════════════════════════════════
# Livro Diário
# ═══════════════════════════════════════════════════════════════════════════

class TestLivroDiario:
    def test_gerar_diario(self, db):
        session, ecd, emp = db
        diario = LivroDiario(session, ecd.id)
        ctx, lancamentos, totais = diario.gerar()

        assert ctx.titulo == "Livro Diário"
        assert totais["num_lancamentos"] >= 1
        assert totais["total_debitos"] > 0
        assert totais["total_creditos"] > 0

    def test_partidas_dobradas_no_diario(self, db):
        session, ecd, emp = db
        diario = LivroDiario(session, ecd.id)
        ctx, lancamentos, totais = diario.gerar()

        for lanc in lancamentos:
            assert abs(lanc.total_debito - lanc.total_credito) < 0.005

    def test_diario_com_filtro_periodo(self, db):
        session, ecd, emp = db
        diario = LivroDiario(session, ecd.id)
        criterios = FilterCriteria()
        criterios.dt_ini = datetime.date(2024, 6, 1)
        criterios.dt_fin = datetime.date(2024, 12, 31)
        ctx, lancamentos, totais = diario.gerar(criterios)

        # Lançamento é de 15/01 — fora do filtro
        assert totais["num_lancamentos"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# Export Engine
# ═══════════════════════════════════════════════════════════════════════════

class TestExportEngine:
    def test_render_html_balanco(self, db):
        session, ecd, emp = db
        balanco = BalancoPatrimonial(session, ecd.id)
        ctx_rel, grupos, totais = balanco.gerar()

        ctx = ReportContext(
            titulo=ctx_rel.titulo,
            empresa_nome=emp.nome,
            empresa_cnpj=emp.cnpj,
            periodo_ref=f"{ecd.dt_ini} a {ecd.dt_fin}",
        )

        export = ExportEngine()
        html = export.render_html("balanco.html", ctx, grupos=grupos, totais=totais)

        assert "<!DOCTYPE html>" in html
        assert "Balanço Patrimonial" in html
        assert "ATIVO" in html.upper() or "Ativo" in html

    def test_render_html_dre(self, db):
        session, ecd, emp = db
        dre = DRE(session, ecd.id)
        ctx_rel, linhas, totais = dre.gerar()

        ctx = ReportContext(
            titulo=ctx_rel.titulo,
            empresa_nome=emp.nome,
            empresa_cnpj=emp.cnpj,
            periodo_ref=f"{ecd.dt_ini} a {ecd.dt_fin}",
        )

        export = ExportEngine()
        html = export.render_html("dre.html", ctx, linhas=linhas, totais=totais)

        assert "<!DOCTYPE html>" in html
        assert "DRE" in html.upper() or "Resultado" in html

    def test_render_html_diario(self, db):
        session, ecd, emp = db
        diario = LivroDiario(session, ecd.id)
        ctx_rel, lancamentos, totais = diario.gerar()

        ctx = ReportContext(
            titulo=ctx_rel.titulo,
            empresa_nome=emp.nome,
            empresa_cnpj=emp.cnpj,
            periodo_ref=f"{ecd.dt_ini} a {ecd.dt_fin}",
        )

        export = ExportEngine()
        html = export.render_html("diario.html", ctx, lancamentos=lancamentos, totais=totais)

        assert "<!DOCTYPE html>" in html
        assert "Livro Diário" in html

    def test_white_label(self, db):
        session, ecd, emp = db
        balanco = BalancoPatrimonial(session, ecd.id)
        ctx_rel, grupos, totais = balanco.gerar()

        ctx = ReportContext(
            titulo=ctx_rel.titulo,
            empresa_nome=emp.nome,
            empresa_cnpj=emp.cnpj,
            periodo_ref=f"{ecd.dt_ini} a {ecd.dt_fin}",
        )

        wl = WhiteLabel(
            escritorio_nome="Contabilidade ABC",
            cor_primaria="#1B8553",
            cor_primaria_clara="#E6F5EE",
        )

        export = ExportEngine()
        html = export.render_html("balanco.html", ctx, wl, grupos=grupos, totais=totais)

        assert "Contabilidade ABC" in html
        assert "#1B8553" in html

    def test_export_pdf(self, db):
        session, ecd, emp = db
        balanco = BalancoPatrimonial(session, ecd.id)
        ctx_rel, grupos, totais = balanco.gerar()

        ctx = ReportContext(
            titulo=ctx_rel.titulo,
            empresa_nome=emp.nome,
            empresa_cnpj=emp.cnpj,
            periodo_ref=f"{ecd.dt_ini} a {ecd.dt_fin}",
        )

        export = ExportEngine()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            output = f.name

        try:
            result = export.export_pdf("balanco.html", output, ctx, grupos=grupos, totais=totais)
            assert os.path.exists(result)
            assert os.path.getsize(result) > 100  # PDF não vazio
        finally:
            if os.path.exists(output):
                os.unlink(output)

    def test_export_xlsx(self, db):
        session, ecd, emp = db
        ctx = ReportContext(
            titulo="Balancete de Verificação",
            empresa_nome=emp.nome,
            empresa_cnpj=emp.cnpj,
            periodo_ref=f"{ecd.dt_ini} a {ecd.dt_fin}",
        )

        linhas = [
            {"cod_cta": "1.1.1", "nome_cta": "Caixa", "nivel": 3, "saldo_inicial": "50.000,00", "debitos": "10.000,00", "creditos": "5.000,00", "saldo_final": "55.000,00"},
        ]
        colunas = ["cod_cta", "nome_cta", "nivel", "saldo_inicial", "debitos", "creditos", "saldo_final"]

        export = ExportEngine()
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            output = f.name

        try:
            result = export.export_xlsx(output, ctx, linhas, colunas, "Teste")
            assert os.path.exists(result)
            assert os.path.getsize(result) > 100
        finally:
            if os.path.exists(output):
                os.unlink(output)