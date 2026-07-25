"""Dashboard Web — FastAPI + Jinja2 + HTMX + Alpine.js.

Rotas:
  GET  /                    — Dashboard principal
  GET  /upload              — Página de upload de ECD
  POST /api/upload          — Upload de arquivo ECD
  GET  /api/kpis            — KPIs (HTMX partial)
  GET  /api/graficos        — Dados para gráficos (JSON)
  GET  /api/balanco         — Balanço Patrimonial (HTMX partial)
  GET  /api/dre             — DRE (HTMX partial)
  GET  /api/diario          — Livro Diário (HTMX partial)
  GET  /api/ecds            — Lista ECDs disponíveis
  GET  /api/selecionar-ecd  — Seleciona ECD ativa
"""

import datetime
import hashlib
import logging
import sys
from pathlib import Path

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

# Adiciona raiz do projeto ao path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.db.models import criar_engine, get_session, init_db
from src.db.repository import Repository
from src.parsers.ecd import ECDParser
from src.dashboard.services import DashboardService
from src.reports.balanco import BalancoPatrimonial
from src.reports.dre import DRE
from src.reports.diario import LivroDiario
from src.reports.base import fmt_moeda, fmt_data

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sped-hub.dashboard")

# ── App ────────────────────────────────────────────────────────────────────

app = FastAPI(title="SPED-HUB Dashboard", version="0.2.0")

# Templates
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(['html']),
)

# Banco padrão
DB_PATH = Path("sped_hub.db")
if not DB_PATH.is_absolute():
    DB_PATH = Path.cwd() / DB_PATH

# Garante que o banco existe
engine = criar_engine(str(DB_PATH))
init_db(engine)


def _get_engine():
    return criar_engine(str(DB_PATH))


# ── Filtros Jinja2 ─────────────────────────────────────────────────────────

jinja_env.globals["fmt_moeda"] = fmt_moeda
jinja_env.globals["fmt_data"] = fmt_data
jinja_env.globals["now"] = datetime.datetime.now


# ── Rotas: Páginas ─────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard principal."""
    session = get_session(_get_engine())
    try:
        # Busca última ECD
        from sqlalchemy import select, desc
        from src.db.models import ECD

        ecd = session.execute(
            select(ECD).order_by(desc(ECD.importado_em)).limit(1)
        ).scalar_one_or_none()

        if ecd:
            svc = DashboardService(session, ecd.id)
            data = svc.get_dashboard_data()
            evolucao = svc.get_evolucao_patrimonial()
            composicao = svc.get_composicao_ativo()
            dre_waterfall = svc.get_dre_waterfall()
            ecds = svc.get_ecds_disponiveis()
        else:
            data = None
            evolucao = None
            composicao = None
            dre_waterfall = None
            ecds = []

        return HTMLResponse(jinja_env.get_template("dashboard.html").render({
            "request": request,
            "data": data,
            "evolucao": evolucao,
            "composicao": composicao,
            "dre_waterfall": dre_waterfall,
            "ecds": ecds,
            "ecd_ativo": ecd.id if ecd else None,
        }))
    finally:
        session.close()


@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    """Página de upload de ECD."""
    return HTMLResponse(jinja_env.get_template("upload.html").render({
        "request": request,
    }))


# ── Rotas: API ─────────────────────────────────────────────────────────────


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    """Upload e processamento de arquivo ECD."""
    if not file.filename or not file.filename.lower().endswith((".txt", ".ecd")):
        return JSONResponse({"status": "erro", "mensagem": "Formato inválido. Envie um arquivo .txt ou .ecd"}, status_code=400)

    # Salva temporário
    upload_dir = Path("/workspace/uploads")
    upload_dir.mkdir(exist_ok=True)
    temp_path = upload_dir / file.filename

    content = await file.read()
    temp_path.write_bytes(content)

    # Hash
    hash_arquivo = hashlib.sha256(content).hexdigest()

    # Processa
    engine = _get_engine()
    session = get_session(engine)
    repo = Repository(session)

    try:
        parser = ECDParser()
        registros = parser.parse_todos(temp_path)

        from collections import defaultdict
        grupos = defaultdict(list)
        for r in registros:
            grupos[r["_reg"]].append(r)

        if not grupos.get("0000"):
            return JSONResponse({"status": "erro", "mensagem": "Arquivo não contém registro 0000 — não é uma ECD válida"}, status_code=400)

        # ── 0000: Empresa ──
        r0000 = grupos["0000"][0]
        empresa = repo.upsert_empresa({
            "cnpj": str(int(r0000.get("CNPJ", 0))).zfill(14),
            "nome": r0000.get("NOME", ""),
            "uf": r0000.get("UF", ""),
            "ie": r0000.get("IE", ""),
            "cod_mun": str(int(r0000.get("COD_MUN", 0))).zfill(7) if r0000.get("COD_MUN") else None,
            "im": r0000.get("IM", ""),
            "ind_sit_esp": int(r0000.get("IND_SIT_ESP", 0)) if r0000.get("IND_SIT_ESP") else None,
            "ind_nire": int(r0000.get("IND_NIRE", 0)) if r0000.get("IND_NIRE") else None,
            "ind_fin_esc": int(r0000.get("IND_FIN_ESC", 0)) if r0000.get("IND_FIN_ESC") else None,
            "ind_grande_por": int(r0000.get("IND_GRANDE_POR", 0)) if r0000.get("IND_GRANDE_POR") else None,
            "tip_ecd": r0000.get("TIP_ECD", ""),
            "ident_mf": r0000.get("IDENT_MF", ""),
            "ind_esc_cons": r0000.get("IND_ESC_CONS", ""),
        })

        rI010 = grupos["I010"][0] if grupos["I010"] else {}
        leiaute = rI010.get("COD_VER_LC", "009")

        def _parse_data(valor):
            if len(valor) == 8 and valor.isdigit():
                return datetime.date(int(valor[4:8]), int(valor[2:4]), int(valor[0:2]))
            return datetime.date.today()

        dt_ini = _parse_data(str(int(r0000.get("DT_INI", 0))).zfill(8))
        dt_fin = _parse_data(str(int(r0000.get("DT_FIN", 0))).zfill(8))

        ecd = repo.criar_ecd(empresa.id, {
            "leiaute": leiaute,
            "dt_ini": dt_ini,
            "dt_fin": dt_fin,
            "ind_esc": rI010.get("IND_ESC", ""),
            "cod_ver_lc": leiaute,
            "hash_arquivo": hash_arquivo,
            "nome_arquivo": file.filename,
        })

        # ── I050: Plano de Contas ──
        contas = []
        for r in grupos["I050"]:
            contas.append({
                "cod_cta": r.get("COD_CTA", ""),
                "cod_cta_sup": r.get("COD_CTA_SUP", ""),
                "nome_cta": r.get("NOME_CTA", ""),
                "cod_nat": r.get("COD_NAT", "01"),
                "ind_cta": r.get("IND_CTA", "A"),
                "nivel": int(r.get("NIVEL", 0)),
                "dt_alt": _parse_data(str(int(r.get("DT_ALT", 0))).zfill(8)) if r.get("DT_ALT") else None,
            })
        repo.inserir_plano_contas(ecd.id, contas)

        # ── I051: Contas Referenciais ──
        refs = []
        for r in grupos["I051"]:
            refs.append({
                "cod_cta": r.get("COD_CTA", ""),
                "cod_ccus": r.get("COD_CCUS", ""),
                "cod_cta_ref": r.get("COD_CTA_REF", ""),
            })
        repo.inserir_contas_referenciais(ecd.id, refs)

        # ── I052: Aglutinações ──
        agls = []
        for r in grupos["I052"]:
            agls.append({
                "cod_cta": r.get("COD_CTA", ""),
                "cod_ccus": r.get("COD_CCUS", ""),
                "cod_agl": r.get("COD_AGL", ""),
            })
        repo.inserir_aglutinacoes(ecd.id, agls)

        # ── I075: Históricos ──
        hists = []
        for r in grupos["I075"]:
            hists.append({
                "cod_hist": r.get("COD_HIST", ""),
                "descr_hist": r.get("DESCR_HIST", ""),
            })
        repo.inserir_historicos_padrao(ecd.id, hists)

        # ── I155: Saldos Periódicos ──
        saldos = []
        for r in grupos["I155"]:
            saldos.append({
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
            })
        repo.inserir_saldos_periodicos(ecd.id, saldos)

        # ── I355: Saldos Resultado ──
        saldos_res = []
        for r in grupos["I355"]:
            saldos_res.append({
                "cod_cta": r.get("COD_CTA", ""),
                "cod_ccus": r.get("COD_CCUS", ""),
                "dt_res": dt_fin,
                "vl_sld_fin": r.get("VL_SLD_FIN", 0.0) or 0.0,
                "ind_dc_fin": r.get("IND_DC_FIN", "D"),
            })
        repo.inserir_saldos_resultado(ecd.id, saldos_res)

        # ── I200/I250: Lançamentos e Partidas ──
        lancs = []
        for r in grupos["I200"]:
            lancs.append({
                "num_lcto": r.get("NUM_LCTO", ""),
                "dt_lcto": _parse_data(str(int(r.get("DT_LCTO", 0))).zfill(8)) if r.get("DT_LCTO") else dt_ini,
                "vl_lcto": r.get("VL_LCTO", 0.0) or 0.0,
                "ind_lcto": r.get("IND_LCTO", "N"),
                "num_arq": int(r.get("NUM_ARQ", 0)) if r.get("NUM_ARQ") else None,
            })
        repo.inserir_lancamentos(ecd.id, lancs)

        partidas = []
        for r in grupos["I250"]:
            partidas.append({
                "num_lcto": r.get("NUM_LCTO", ""),
                "dt_lcto": _parse_data(str(int(r.get("DT_LCTO", 0))).zfill(8)).isoformat() if r.get("DT_LCTO") else dt_ini.isoformat(),
                "cod_cta": r.get("COD_CTA", ""),
                "cod_ccus": r.get("COD_CCUS", ""),
                "vl_dc": r.get("VL_DC", 0.0) or 0.0,
                "ind_dc": r.get("IND_DC", "D"),
                "num_arq": int(r.get("NUM_ARQ", 0)) if r.get("NUM_ARQ") else None,
                "cod_hist_pad": r.get("COD_HIST_PAD", ""),
                "hist": r.get("HIST", ""),
                "cod_part": r.get("COD_PART", ""),
            })
        repo.inserir_partidas(ecd.id, partidas)

        repo.commit()

        return JSONResponse({
            "status": "ok",
            "mensagem": f"ECD importada com sucesso! {len(contas)} contas, {len(lancs)} lançamentos, {len(partidas)} partidas.",
            "ecd_id": ecd.id,
            "empresa": empresa.nome,
            "periodo": f"{dt_ini} a {dt_fin}",
        })

    except Exception as e:
        repo.rollback()
        logger.exception("Erro ao importar ECD")
        return JSONResponse({"status": "erro", "mensagem": str(e)}, status_code=500)
    finally:
        session.close()
        # Limpa temporário
        if temp_path.exists():
            temp_path.unlink()


@app.get("/api/kpis", response_class=HTMLResponse)
async def api_kpis(request: Request, ecd_id: int = Query(...)):
    """Retorna HTML parcial com KPIs (HTMX)."""
    session = get_session(_get_engine())
    try:
        svc = DashboardService(session, ecd_id)
        data = svc.get_dashboard_data()
        return HTMLResponse(jinja_env.get_template("partials/kpis.html").render({
            "request": request,
            "data": data,
        }))
    finally:
        session.close()


@app.get("/api/graficos")
async def api_graficos(ecd_id: int = Query(...)):
    """Retorna dados para gráficos em JSON."""
    session = get_session(_get_engine())
    try:
        svc = DashboardService(session, ecd_id)
        return {
            "evolucao": svc.get_evolucao_patrimonial(),
            "composicao": svc.get_composicao_ativo(),
            "dre_waterfall": svc.get_dre_waterfall(),
        }
    finally:
        session.close()


@app.get("/api/balanco", response_class=HTMLResponse)
async def api_balanco(request: Request, ecd_id: int = Query(...), visao: str = Query("hierarquica")):
    """Retorna HTML parcial com Balanço Patrimonial (HTMX)."""
    session = get_session(_get_engine())
    try:
        balanco = BalancoPatrimonial(session, ecd_id)
        if visao == "publicacao":
            ctx, grupos, totais = balanco.gerar_publicacao()
        else:
            ctx, grupos, totais = balanco.gerar(visao=visao)

        return HTMLResponse(jinja_env.get_template("partials/balanco.html").render({
            "request": request,
            "ctx": ctx,
            "grupos": grupos,
            "totais": totais,
            "visao": visao,
        }))
    finally:
        session.close()


@app.get("/api/dre", response_class=HTMLResponse)
async def api_dre(request: Request, ecd_id: int = Query(...)):
    """Retorna HTML parcial com DRE (HTMX)."""
    session = get_session(_get_engine())
    try:
        dre = DRE(session, ecd_id)
        ctx, linhas, totais = dre.gerar()
        return HTMLResponse(jinja_env.get_template("partials/dre.html").render({
            "request": request,
            "ctx": ctx,
            "linhas": linhas,
            "totais": totais,
        }))
    finally:
        session.close()


@app.get("/api/diario", response_class=HTMLResponse)
async def api_diario(request: Request, ecd_id: int = Query(...), pagina: int = Query(1)):
    """Retorna HTML parcial com Livro Diário paginado (HTMX)."""
    session = get_session(_get_engine())
    try:
        diario = LivroDiario(session, ecd_id)
        ctx, lancamentos, totais = diario.gerar()

        # Paginação: 20 lançamentos por página
        per_page = 20
        total_paginas = max(1, (len(lancamentos) + per_page - 1) // per_page)
        pagina = max(1, min(pagina, total_paginas))
        inicio = (pagina - 1) * per_page
        fim = inicio + per_page
        pagina_lancs = lancamentos[inicio:fim]

        return HTMLResponse(jinja_env.get_template("partials/diario.html").render({
            "request": request,
            "ctx": ctx,
            "lancamentos": pagina_lancs,
            "totais": totais,
            "pagina": pagina,
            "total_paginas": total_paginas,
            "ecd_id": ecd_id,
        }))
    finally:
        session.close()


@app.get("/api/ecds")
async def api_ecds():
    """Lista ECDs disponíveis."""
    session = get_session(_get_engine())
    try:
        svc = DashboardService(session, 0)
        return svc.get_ecds_disponiveis()
    finally:
        session.close()


# ── Entry point ─────────────────────────────────────────────────────────────


def main():
    import uvicorn
    uvicorn.run("src.dashboard.app:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()