"""Testes da Fase 9 — GraphQL API v2, Exportação Multi-formato, Parser Streaming."""

import csv
import datetime
import io
import os
import tempfile
import zipfile
from pathlib import Path

import pytest

from src.db.models import (
    criar_engine,
    get_session,
    init_db,
)
from src.db.repository import Repository
from src.parsers.ecd import ECDParser
from src.reports.balanco import BalancoPatrimonial
from src.reports.base import ReportContext
from src.reports.export_engine import ExportEngine

# ── Fixture ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    """Banco temporário com fixture ECD carregada."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()
    os.environ["SPED_HUB_DB"] = db_path

    engine = criar_engine(db_path)
    init_db(engine)
    session = get_session(engine)

    fixture = Path(__file__).parent / "fixtures" / "ecd_sample.txt"
    parser = ECDParser()
    registros = parser.parse_todos(fixture)

    from collections import defaultdict

    grupos = defaultdict(list)
    for r in registros:
        grupos[r["_reg"]].append(r)

    repo = Repository(session)

    r0000 = grupos["0000"][0]
    empresa = repo.upsert_empresa(
        {
            "cnpj": str(int(r0000.get("CNPJ", 0))).zfill(14),
            "nome": r0000.get("NOME", ""),
            "uf": r0000.get("UF", ""),
            "ie": r0000.get("IE", ""),
            "cod_mun": str(int(r0000.get("COD_MUN", 0))).zfill(7) if r0000.get("COD_MUN") else None,
            "im": r0000.get("IM", ""),
            "ind_sit_esp": int(r0000.get("IND_SIT_ESP", 0)) if r0000.get("IND_SIT_ESP") else None,
            "ind_nire": int(r0000.get("IND_NIRE", 0)) if r0000.get("IND_NIRE") else None,
            "ind_fin_esc": int(r0000.get("IND_FIN_ESC", 0)) if r0000.get("IND_FIN_ESC") else None,
            "ind_grande_por": (
                int(r0000.get("IND_GRANDE_POR", 0)) if r0000.get("IND_GRANDE_POR") else None
            ),
            "tip_ecd": r0000.get("TIP_ECD", ""),
            "ident_mf": r0000.get("IDENT_MF", ""),
            "ind_esc_cons": r0000.get("IND_ESC_CONS", ""),
        }
    )

    rI010 = grupos["I010"][0] if grupos["I010"] else {}
    leiaute = rI010.get("COD_VER_LC", "009")

    def _parse_data(valor):
        if len(valor) == 8 and valor.isdigit():
            return datetime.date(int(valor[4:8]), int(valor[2:4]), int(valor[0:2]))
        return datetime.date.today()

    dt_ini = _parse_data(str(int(r0000.get("DT_INI", 0))).zfill(8))
    dt_fin = _parse_data(str(int(r0000.get("DT_FIN", 0))).zfill(8))

    ecd = repo.criar_ecd(
        empresa.id,
        {
            "leiaute": leiaute,
            "dt_ini": dt_ini,
            "dt_fin": dt_fin,
            "ind_esc": rI010.get("IND_ESC", ""),
            "cod_ver_lc": leiaute,
            "hash_arquivo": "test_hash",
            "nome_arquivo": "ecd_sample.txt",
        },
    )

    contas = []
    for r in grupos["I050"]:
        contas.append(
            {
                "cod_cta": r.get("COD_CTA", ""),
                "cod_cta_sup": r.get("COD_CTA_SUP", ""),
                "nome_cta": r.get("NOME_CTA", ""),
                "cod_nat": r.get("COD_NAT", "01"),
                "ind_cta": r.get("IND_CTA", "A"),
                "nivel": int(r.get("NIVEL", 0)),
                "dt_alt": (
                    _parse_data(str(int(r.get("DT_ALT", 0))).zfill(8)) if r.get("DT_ALT") else None
                ),
            }
        )
    repo.inserir_plano_contas(ecd.id, contas)

    refs = []
    for r in grupos["I051"]:
        refs.append(
            {
                "cod_cta": r.get("COD_CTA", ""),
                "cod_ccus": r.get("COD_CCUS", ""),
                "cod_cta_ref": r.get("COD_CTA_REF", ""),
            }
        )
    repo.inserir_contas_referenciais(ecd.id, refs)

    agls = []
    for r in grupos["I052"]:
        agls.append(
            {
                "cod_cta": r.get("COD_CTA", ""),
                "cod_ccus": r.get("COD_CCUS", ""),
                "cod_agl": r.get("COD_AGL", ""),
            }
        )
    repo.inserir_aglutinacoes(ecd.id, agls)

    hists = []
    for r in grupos["I075"]:
        hists.append({"cod_hist": r.get("COD_HIST", ""), "descr_hist": r.get("DESCR_HIST", "")})
    repo.inserir_historicos_padrao(ecd.id, hists)

    saldos = []
    for r in grupos["I155"]:
        saldos.append(
            {
                "cod_cta": r.get("COD_CTA", ""),
                "cod_ccus": r.get("COD_CCUS", ""),
                "dt_ini": dt_ini,
                "dt_fin": dt_fin,
                "vl_sld_ini": r.get("VL_SLD_INI", 0.0) or 0.0,
                "ind_dc_ini": r.get("IND_DC_INI", "D"),
                "vl_deb": r.get("VL_DEB", 0.0) or 0.0,
                "vl_cred": r.get("VL_CRED", 0.0) or 0.0,
                "vl_sld_fin": r.get("VL_SLD_FIN", 0.0) or 0.0,
                "ind_dc_fin": r.get("IND_DC_FIN", "D"),
            }
        )
    repo.inserir_saldos_periodicos(ecd.id, saldos)

    saldos_res = []
    for r in grupos["I355"]:
        saldos_res.append(
            {
                "cod_cta": r.get("COD_CTA", ""),
                "cod_ccus": r.get("COD_CCUS", ""),
                "dt_res": dt_fin,
                "vl_sld_fin": r.get("VL_SLD_FIN", 0.0) or 0.0,
                "ind_dc_fin": r.get("IND_DC_FIN", "D"),
            }
        )
    repo.inserir_saldos_resultado(ecd.id, saldos_res)

    lancs = []
    for r in grupos["I200"]:
        lancs.append(
            {
                "num_lcto": r.get("NUM_LCTO", ""),
                "dt_lcto": (
                    _parse_data(str(int(r.get("DT_LCTO", 0))).zfill(8))
                    if r.get("DT_LCTO")
                    else dt_ini
                ),
                "vl_lcto": r.get("VL_LCTO", 0.0) or 0.0,
                "ind_lcto": r.get("IND_LCTO", "N"),
                "num_arq": int(r.get("NUM_ARQ", 0)) if r.get("NUM_ARQ") else None,
            }
        )
    repo.inserir_lancamentos(ecd.id, lancs)

    partidas = []
    for r in grupos["I250"]:
        partidas.append(
            {
                "num_lcto": r.get("NUM_LCTO", ""),
                "dt_lcto": (
                    _parse_data(str(int(r.get("DT_LCTO", 0))).zfill(8)).isoformat()
                    if r.get("DT_LCTO")
                    else dt_ini.isoformat()
                ),
                "cod_cta": r.get("COD_CTA", ""),
                "cod_ccus": r.get("COD_CCUS", ""),
                "vl_dc": r.get("VL_DC", 0.0) or 0.0,
                "ind_dc": r.get("IND_DC", "D"),
                "num_arq": int(r.get("NUM_ARQ", 0)) if r.get("NUM_ARQ") else None,
                "cod_hist_pad": r.get("COD_HIST_PAD", ""),
                "hist": r.get("HIST", ""),
                "cod_part": r.get("COD_PART", ""),
            }
        )
    repo.inserir_partidas(ecd.id, partidas)

    repo.commit()
    ecd_id_val = ecd.id
    session.close()

    yield ecd_id_val

    os.unlink(db_path)


# ── Helper ───────────────────────────────────────────────────────────────────


def _open_session():
    """Abre sessão no banco de teste."""
    eng = criar_engine(os.environ["SPED_HUB_DB"])
    return get_session(eng)


# ── Testes: GraphQL Schema ──────────────────────────────────────────────────


class TestGraphQLSchema:

    def test_schema_compila(self):
        from src.api.graphql import schema

        assert schema is not None

    def test_query_health(self, db):
        from src.api.graphql import schema

        result = schema.execute_sync("{ health }")
        assert result.errors is None
        assert result.data["health"] == "ok"

    def test_query_empresas(self, db):
        from src.api.graphql import schema

        result = schema.execute_sync(
            "{ empresas(pagina: 1, limite: 10) { dados { id cnpj nome } pageInfo { total } } }"
        )
        assert result.errors is None
        assert result.data["empresas"]["pageInfo"]["total"] >= 1

    def test_query_empresa_por_id(self, db):
        from src.api.graphql import schema

        result = schema.execute_sync("{ empresas(pagina: 1, limite: 1) { dados { id } } }")
        empresa_id = result.data["empresas"]["dados"][0]["id"]
        result = schema.execute_sync(f"{{ empresa(id: {empresa_id}) {{ id cnpj nome }} }}")
        assert result.errors is None
        assert result.data["empresa"]["id"] == empresa_id

    def test_query_ecds(self, db):
        from src.api.graphql import schema

        result = schema.execute_sync(
            "{ ecds(pagina: 1, limite: 10) { dados { id empresaNome leiaute } pageInfo { total } } }"
        )
        assert result.errors is None
        assert result.data["ecds"]["pageInfo"]["total"] >= 1

    def test_query_ecd_por_id(self, db):
        from src.api.graphql import schema

        result = schema.execute_sync("{ ecds(pagina: 1, limite: 1) { dados { id } } }")
        ecd_id = result.data["ecds"]["dados"][0]["id"]
        result = schema.execute_sync(f"{{ ecd(id: {ecd_id}) {{ id empresaNome leiaute }} }}")
        assert result.errors is None
        assert result.data["ecd"]["id"] == ecd_id

    def test_query_balanco(self, db):
        from src.api.graphql import schema

        result = schema.execute_sync(
            f"{{ balanco(ecdId: {db}) {{ titulo totalAtivo totalPassivo totalPl fecha }} }}"
        )
        assert result.errors is None
        assert result.data["balanco"]["titulo"] == "Balanço Patrimonial"

    def test_query_dre(self, db):
        from src.api.graphql import schema

        result = schema.execute_sync(
            f"{{ dre(ecdId: {db}) {{ titulo resultadoLiquido receitaBruta }} }}"
        )
        assert result.errors is None
        assert result.data["dre"]["titulo"] == "Demonstração do Resultado do Exercício"

    def test_query_dfc(self, db):
        from src.api.graphql import schema

        result = schema.execute_sync(f"{{ dfc(ecdId: {db}) {{ titulo variacaoCaixa }} }}")
        assert result.errors is None
        assert result.data["dfc"]["titulo"] == "Demonstração dos Fluxos de Caixa"

    def test_query_diario(self, db):
        from src.api.graphql import schema

        result = schema.execute_sync(
            f"{{ diario(ecdId: {db}, pagina: 1, limite: 10) {{ titulo totalLancamentos }} }}"
        )
        assert result.errors is None
        assert result.data["diario"]["titulo"] == "Livro Diário"

    def test_query_kpis(self, db):
        from src.api.graphql import schema

        result = schema.execute_sync(
            f"{{ kpis(ecdId: {db}) {{ empresa ativoTotal patrimonioLiquido }} }}"
        )
        assert result.errors is None
        assert result.data["kpis"]["empresa"] != ""

    def test_query_notas(self, db):
        from src.api.graphql import schema

        result = schema.execute_sync(f"{{ notas(ecdId: {db}) {{ numero titulo tipo }} }}")
        assert result.errors is None
        assert len(result.data["notas"]) >= 1

    def test_query_validar(self, db):
        from src.api.graphql import schema

        result = schema.execute_sync(
            f"{{ validar(ecdId: {db}) {{ status totalInconsistencias erros alertas }} }}"
        )
        assert result.errors is None
        assert result.data["validar"]["status"] in ("OK", "ERROS")

    def test_query_evolucao_multi(self, db):
        from src.api.graphql import schema

        result = schema.execute_sync(f"{{ evolucaoMulti(ecdId: {db}) {{ numPeriodos }} }}")
        assert result.errors is None


# ── Testes: Parser Streaming ────────────────────────────────────────────────


class TestParserStreaming:

    def test_parse_em_lotes(self):
        fixture = Path(__file__).parent / "fixtures" / "ecd_sample.txt"
        parser = ECDParser()
        lotes = list(parser.parse_em_lotes(fixture, tamanho_lote=50))
        assert len(lotes) > 0
        total = sum(len(ln) for ln in lotes)
        assert total > 0

    def test_contar_registros(self):
        fixture = Path(__file__).parent / "fixtures" / "ecd_sample.txt"
        parser = ECDParser()
        contagem = parser.contar_registros(fixture)
        assert "0000" in contagem
        assert "I050" in contagem
        assert contagem["0000"] == 1

    def test_parse_streaming_mantem_resultados(self):
        fixture = Path(__file__).parent / "fixtures" / "ecd_sample.txt"
        parser = ECDParser()
        todos = parser.parse_todos(fixture)
        streaming = list(parser.parse(fixture))
        assert len(todos) == len(streaming)
        for t, s in zip(todos, streaming, strict=True):
            assert t["_reg"] == s["_reg"]


# ── Testes: Exportação Multi-formato ────────────────────────────────────────


class TestExportMultiFormato:

    def test_export_xlsx_to_buffer(self, db):
        session = _open_session()
        try:
            balanco = BalancoPatrimonial(session, db)
            ctx_rel, grupos, totais = balanco.gerar()
            ctx = ReportContext(
                titulo=ctx_rel.titulo,
                empresa_nome="Teste",
                empresa_cnpj="00000000000000",
                periodo_ref="2024-01-01 a 2024-12-31",
            )
            linhas_dict = []
            for secao, nome in [("ativo", "Ativo"), ("passivo", "Passivo"), ("pl", "PL")]:
                for ln in grupos[secao]:
                    linhas_dict.append(
                        {
                            "secao": nome,
                            "cod_cta": ln.cod_cta,
                            "nome_cta": ln.nome_cta,
                            "saldo_atual": ln.saldo_atual,
                        }
                    )
            export = ExportEngine()
            buffer = io.BytesIO()
            export.export_xlsx_to_buffer(
                buffer,
                ctx,
                linhas_dict,
                ["secao", "cod_cta", "nome_cta", "saldo_atual"],
                ctx.titulo,
            )
            buffer.seek(0)
            assert buffer.getvalue()[:2] == b"PK"
        finally:
            session.close()

    def test_export_csv_balanco(self, db):
        session = _open_session()
        try:
            balanco = BalancoPatrimonial(session, db)
            ctx_rel, grupos, totais = balanco.gerar()
            csv_buffer = io.StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow(["secao", "cod_cta", "nome_cta", "saldo_atual"])
            for secao, nome in [("ativo", "Ativo"), ("passivo", "Passivo"), ("pl", "PL")]:
                for ln in grupos[secao]:
                    writer.writerow([nome, ln.cod_cta, ln.nome_cta, ln.saldo_atual])
            csv_buffer.seek(0)
            reader = csv.reader(csv_buffer)
            rows = list(reader)
            assert rows[0] == ["secao", "cod_cta", "nome_cta", "saldo_atual"]
            assert len(rows) > 1
        finally:
            session.close()

    def test_export_zip_multi_formato(self, db):
        session = _open_session()
        try:
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                balanco = BalancoPatrimonial(session, db)
                ctx_rel, grupos, totais = balanco.gerar()
                csv_buffer = io.StringIO()
                writer = csv.writer(csv_buffer)
                writer.writerow(["secao", "cod_cta", "nome_cta", "saldo_atual"])
                for secao, nome in [("ativo", "Ativo"), ("passivo", "Passivo"), ("pl", "PL")]:
                    for ln in grupos[secao]:
                        writer.writerow([nome, ln.cod_cta, ln.nome_cta, ln.saldo_atual])
                zf.writestr(f"balanco_{db}.csv", csv_buffer.getvalue().encode("utf-8-sig"))
                export = ExportEngine()
                ctx = ReportContext(
                    titulo=ctx_rel.titulo,
                    empresa_nome="Teste",
                    empresa_cnpj="00000000000000",
                    periodo_ref="2024-01-01 a 2024-12-31",
                )
                linhas_dict = []
                for secao, nome in [("ativo", "Ativo"), ("passivo", "Passivo"), ("pl", "PL")]:
                    for ln in grupos[secao]:
                        linhas_dict.append(
                            {
                                "secao": nome,
                                "cod_cta": ln.cod_cta,
                                "nome_cta": ln.nome_cta,
                                "saldo_atual": ln.saldo_atual,
                            }
                        )
                xlsx_buffer = io.BytesIO()
                export.export_xlsx_to_buffer(
                    xlsx_buffer,
                    ctx,
                    linhas_dict,
                    ["secao", "cod_cta", "nome_cta", "saldo_atual"],
                    ctx.titulo,
                )
                zf.writestr(f"balanco_{db}.xlsx", xlsx_buffer.getvalue())
            zip_buffer.seek(0)
            with zipfile.ZipFile(zip_buffer, "r") as zf:
                names = zf.namelist()
                assert f"balanco_{db}.csv" in names
                assert f"balanco_{db}.xlsx" in names
        finally:
            session.close()


# ── Testes: GraphQL Router ──────────────────────────────────────────────────


class TestGraphQLRouter:

    def test_router_existe(self):
        from src.api.graphql import graphql_router

        assert graphql_router is not None
        assert len(graphql_router.routes) == 3

    def test_graphql_endpoint_registrado(self):
        from src.api.graphql import graphql_router

        paths = [r.path for r in graphql_router.routes if hasattr(r, "path")]
        assert "/api/v2/graphql" in paths


# ── Testes: Integração Fase 9 ───────────────────────────────────────────────


class TestIntegracaoFase9:

    def test_graphql_balanco_completo(self, db):
        from src.api.graphql import schema

        query = f"""
        {{
            balanco(ecdId: {db}) {{
                titulo
                totalAtivo
                totalPassivo
                totalPl
                fecha
                ativo {{ codCta nomeCta nivel saldoAtual }}
                passivo {{ codCta nomeCta nivel saldoAtual }}
                patrimonioLiquido {{ codCta nomeCta nivel saldoAtual }}
            }}
        }}
        """
        result = schema.execute_sync(query)
        assert result.errors is None
        assert result.data["balanco"]["titulo"] == "Balanço Patrimonial"
        assert len(result.data["balanco"]["ativo"]) > 0

    def test_graphql_dre_completo(self, db):
        from src.api.graphql import schema

        query = f"""
        {{
            dre(ecdId: {db}) {{
                titulo
                resultadoLiquido
                receitaBruta
                lucroBruto
                linhas {{ tipo descricao valorAtual valorAnterior }}
            }}
        }}
        """
        result = schema.execute_sync(query)
        assert result.errors is None
        assert len(result.data["dre"]["linhas"]) == 11

    def test_graphql_dfc_completo(self, db):
        from src.api.graphql import schema

        query = f"""
        {{
            dfc(ecdId: {db}) {{
                titulo
                variacaoCaixa
                operacional
                investimento
                financiamento
                linhas {{ tipo descricao valor valorAnterior }}
            }}
        }}
        """
        result = schema.execute_sync(query)
        assert result.errors is None
        assert len(result.data["dfc"]["linhas"]) > 0

    def test_graphql_diario_paginado(self, db):
        from src.api.graphql import schema

        query = f"""
        {{
            diario(ecdId: {db}, pagina: 1, limite: 5) {{
                titulo
                totalLancamentos
                totalDebitos
                totalCreditos
                lancamentos {{ numLcto data indLcto totalDebito totalCredito }}
            }}
        }}
        """
        result = schema.execute_sync(query)
        assert result.errors is None
        assert result.data["diario"]["totalLancamentos"] > 0

    def test_graphql_validar_detalhes(self, db):
        from src.api.graphql import schema

        query = f"""
        {{
            validar(ecdId: {db}) {{
                status
                totalInconsistencias
                erros
                alertas
                detalhes {{ tipo severidade descricao }}
            }}
        }}
        """
        result = schema.execute_sync(query)
        assert result.errors is None
        assert "status" in result.data["validar"]
