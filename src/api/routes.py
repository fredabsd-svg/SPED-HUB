"""Rotas da API REST externa v1 — Fase 7 + Fase 10.

Endpoints:
  GET  /api/v1/empresas          — Lista empresas
  GET  /api/v1/empresas/{id}     — Detalhes da empresa
  GET  /api/v1/ecds              — Lista ECDs
  GET  /api/v1/ecds/{id}         — Detalhes da ECD
  GET  /api/v1/ecds/{id}/balanco — Balanço Patrimonial
  GET  /api/v1/ecds/{id}/dre     — DRE
  GET  /api/v1/ecds/{id}/dfc     — DFC
  GET  /api/v1/ecds/{id}/diario  — Livro Diário (paginado)
  GET  /api/v1/ecds/{id}/kpis    — KPIs
  GET  /api/v1/ecds/{id}/notas   — Notas Explicativas
  GET  /api/v1/ecds/{id}/validar — Validações de integridade
  GET  /api/v1/health            — Health check público

Webhooks (Fase 10):
  GET    /api/v1/webhooks        — Lista webhooks
  POST   /api/v1/webhooks        — Registra webhook
  PUT    /api/v1/webhooks/{id}   — Atualiza webhook
  DELETE /api/v1/webhooks/{id}   — Remove webhook
  GET    /api/v1/webhooks/eventos — Lista eventos disponíveis
"""

import datetime
import json
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from src.api import requer_api_key
from src.db.models import (
    ECD,
    Empresa,
    Lancamento,
    Partida,
    PlanoConta,
    SaldoPeriodico,
    SaldoResultado,
    WebhookDelivery,
    WebhookRegistration,
    criar_engine,
    get_session,
    init_db,
)
from src.dashboard.services import DashboardService
from src.reports.balanco import BalancoPatrimonial
from src.reports.dfc import DFC
from src.reports.diario import LivroDiario
from src.reports.dre import DRE
from src.validators.integridade import ValidadorIntegridade
from src.webhooks import EVENTOS_DISPONIVEIS, WebhookService

logger = logging.getLogger("sped-hub.api.v1")

router = APIRouter(prefix="/api/v1", tags=["API v1"])

def _get_db_path() -> str:
    import os as _os
    return _os.environ.get("SPED_HUB_DB", "sped_hub.db")


def _get_session() -> Session:
    engine = criar_engine(_get_db_path())
    init_db(engine)
    return get_session(engine)


# ── Health Check ────────────────────────────────────────────────────────────


@router.get("/health")
async def health_check():
    """Health check público — não requer autenticação."""
    try:
        session = _get_session()
        session.execute(select(1))
        session.close()
        db_status = "ok"
    except Exception:
        db_status = "error"

    return {
        "status": "ok",
        "version": "0.9.0",
        "database": db_status,
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
    }


# ── Empresas ────────────────────────────────────────────────────────────────


@router.get("/empresas")
async def listar_empresas(
    api_key=Depends(requer_api_key),
    pagina: int = Query(1, ge=1),
    limite: int = Query(20, ge=1, le=100),
):
    """Lista empresas com paginação."""
    session = _get_session()
    try:
        total = session.execute(select(func.count(Empresa.id))).scalar() or 0
        offset = (pagina - 1) * limite
        empresas = session.execute(
            select(Empresa).order_by(Empresa.nome).offset(offset).limit(limite)
        ).scalars().all()

        return {
            "pagina": pagina,
            "limite": limite,
            "total": total,
            "total_paginas": max(1, (total + limite - 1) // limite),
            "dados": [
                {
                    "id": e.id,
                    "cnpj": e.cnpj,
                    "nome": e.nome,
                    "uf": e.uf,
                    "ie": e.ie,
                    "cod_mun": e.cod_mun,
                }
                for e in empresas
            ],
        }
    finally:
        session.close()


@router.get("/empresas/{empresa_id}")
async def detalhe_empresa(empresa_id: int, api_key=Depends(requer_api_key)):
    """Detalhes de uma empresa."""
    session = _get_session()
    try:
        empresa = session.get(Empresa, empresa_id)
        if not empresa:
            raise HTTPException(status_code=404, detail="Empresa não encontrada")

        ecds = session.execute(
            select(ECD).where(ECD.empresa_id == empresa_id).order_by(ECD.dt_ini.desc())
        ).scalars().all()

        return {
            "id": empresa.id,
            "cnpj": empresa.cnpj,
            "nome": empresa.nome,
            "uf": empresa.uf,
            "ie": empresa.ie,
            "cod_mun": empresa.cod_mun,
            "im": empresa.im,
            "ind_sit_esp": empresa.ind_sit_esp,
            "ind_nire": empresa.ind_nire,
            "ind_fin_esc": empresa.ind_fin_esc,
            "ind_grande_por": empresa.ind_grande_por,
            "tip_ecd": empresa.tip_ecd,
            "ident_mf": empresa.ident_mf,
            "ind_esc_cons": empresa.ind_esc_cons,
            "ecds": [
                {
                    "id": e.id,
                    "leiaute": e.leiaute,
                    "dt_ini": e.dt_ini.isoformat() if e.dt_ini else None,
                    "dt_fin": e.dt_fin.isoformat() if e.dt_fin else None,
                    "nome_arquivo": e.nome_arquivo,
                    "importado_em": e.importado_em.isoformat() if e.importado_em else None,
                }
                for e in ecds
            ],
        }
    finally:
        session.close()


# ── ECDs ────────────────────────────────────────────────────────────────────


@router.get("/ecds")
async def listar_ecds(
    api_key=Depends(requer_api_key),
    empresa_id: int | None = Query(None),
    pagina: int = Query(1, ge=1),
    limite: int = Query(20, ge=1, le=100),
):
    """Lista ECDs com paginação e filtro opcional por empresa."""
    session = _get_session()
    try:
        query = select(func.count(ECD.id))
        if empresa_id:
            query = query.where(ECD.empresa_id == empresa_id)
        total = session.execute(query).scalar() or 0

        offset = (pagina - 1) * limite
        query_ecds = select(ECD, Empresa.nome).join(Empresa).order_by(ECD.importado_em.desc())
        if empresa_id:
            query_ecds = query_ecds.where(ECD.empresa_id == empresa_id)
        ecds = session.execute(query_ecds.offset(offset).limit(limite)).all()

        return {
            "pagina": pagina,
            "limite": limite,
            "total": total,
            "total_paginas": max(1, (total + limite - 1) // limite),
            "dados": [
                {
                    "id": ecd.id,
                    "empresa_id": ecd.empresa_id,
                    "empresa_nome": nome,
                    "leiaute": ecd.leiaute,
                    "dt_ini": ecd.dt_ini.isoformat() if ecd.dt_ini else None,
                    "dt_fin": ecd.dt_fin.isoformat() if ecd.dt_fin else None,
                    "ind_esc": ecd.ind_esc,
                    "nome_arquivo": ecd.nome_arquivo,
                    "importado_em": ecd.importado_em.isoformat() if ecd.importado_em else None,
                }
                for ecd, nome in ecds
            ],
        }
    finally:
        session.close()


@router.get("/ecds/{ecd_id}")
async def detalhe_ecd(ecd_id: int, api_key=Depends(requer_api_key)):
    """Detalhes de uma ECD com estatísticas."""
    session = _get_session()
    try:
        ecd = session.get(ECD, ecd_id)
        if not ecd:
            raise HTTPException(status_code=404, detail="ECD não encontrada")

        empresa = session.get(Empresa, ecd.empresa_id)

        n_contas = session.execute(
            select(func.count(PlanoConta.id)).where(PlanoConta.ecd_id == ecd_id)
        ).scalar() or 0
        n_lancs = session.execute(
            select(func.count(Lancamento.id)).where(Lancamento.ecd_id == ecd_id)
        ).scalar() or 0
        n_partidas = session.execute(
            select(func.count(Partida.id))
            .join(Lancamento)
            .where(Lancamento.ecd_id == ecd_id)
        ).scalar() or 0

        return {
            "id": ecd.id,
            "empresa": {
                "id": empresa.id if empresa else None,
                "nome": empresa.nome if empresa else "",
                "cnpj": empresa.cnpj if empresa else "",
            },
            "leiaute": ecd.leiaute,
            "dt_ini": ecd.dt_ini.isoformat() if ecd.dt_ini else None,
            "dt_fin": ecd.dt_fin.isoformat() if ecd.dt_fin else None,
            "ind_esc": ecd.ind_esc,
            "cod_ver_lc": ecd.cod_ver_lc,
            "nome_arquivo": ecd.nome_arquivo,
            "hash_arquivo": ecd.hash_arquivo,
            "importado_em": ecd.importado_em.isoformat() if ecd.importado_em else None,
            "estatisticas": {
                "contas": n_contas,
                "lancamentos": n_lancs,
                "partidas": n_partidas,
            },
        }
    finally:
        session.close()


# ── Relatórios ──────────────────────────────────────────────────────────────


@router.get("/ecds/{ecd_id}/balanco")
async def api_balanco(
    ecd_id: int,
    visao: str = Query("hierarquica", pattern="^(hierarquica|publicacao)$"),
    api_key=Depends(requer_api_key),
):
    """Balanço Patrimonial — visão hierárquica ou de publicação."""
    session = _get_session()
    try:
        balanco = BalancoPatrimonial(session, ecd_id)
        if visao == "publicacao":
            ctx, grupos, totais = balanco.gerar_publicacao()
        else:
            ctx, grupos, totais = balanco.gerar()

        return {
            "titulo": ctx.titulo,
            "ativo": [
                {
                    "cod_cta": l.cod_cta,
                    "nome_cta": l.nome_cta,
                    "nivel": l.nivel,
                    "saldo_atual": l.saldo_atual,
                    "saldo_anterior": l.saldo_anterior,
                }
                for l in grupos["ativo"]
            ],
            "passivo": [
                {
                    "cod_cta": l.cod_cta,
                    "nome_cta": l.nome_cta,
                    "nivel": l.nivel,
                    "saldo_atual": l.saldo_atual,
                    "saldo_anterior": l.saldo_anterior,
                }
                for l in grupos["passivo"]
            ],
            "patrimonio_liquido": [
                {
                    "cod_cta": l.cod_cta,
                    "nome_cta": l.nome_cta,
                    "nivel": l.nivel,
                    "saldo_atual": l.saldo_atual,
                    "saldo_anterior": l.saldo_anterior,
                }
                for l in grupos["pl"]
            ],
            "totais": totais,
        }
    finally:
        session.close()


@router.get("/ecds/{ecd_id}/dre")
async def api_dre(ecd_id: int, api_key=Depends(requer_api_key)):
    """Demonstração do Resultado do Exercício."""
    session = _get_session()
    try:
        dre = DRE(session, ecd_id)
        ctx, linhas, totais = dre.gerar()

        return {
            "titulo": ctx.titulo,
            "linhas": [
                {
                    "tipo": l.tipo,
                    "descricao": l.descricao,
                    "valor_atual": l.valor_atual,
                    "valor_anterior": l.valor_anterior,
                }
                for l in linhas
            ],
            "totais": totais,
        }
    finally:
        session.close()


@router.get("/ecds/{ecd_id}/dfc")
async def api_dfc(ecd_id: int, api_key=Depends(requer_api_key)):
    """Demonstração dos Fluxos de Caixa."""
    session = _get_session()
    try:
        dfc = DFC(session, ecd_id)
        ctx, linhas, totais = dfc.gerar()

        return {
            "titulo": ctx.titulo,
            "linhas": [
                {
                    "tipo": l.tipo,
                    "descricao": l.descricao,
                    "valor": l.valor,
                    "valor_anterior": l.valor_anterior,
                }
                for l in linhas
            ],
            "totais": totais,
        }
    finally:
        session.close()


@router.get("/ecds/{ecd_id}/diario")
async def api_diario(
    ecd_id: int,
    pagina: int = Query(1, ge=1),
    limite: int = Query(50, ge=1, le=200),
    api_key=Depends(requer_api_key),
):
    """Livro Diário com paginação."""
    session = _get_session()
    try:
        diario = LivroDiario(session, ecd_id)
        ctx, lancamentos, totais = diario.gerar()

        total = len(lancamentos)
        total_paginas = max(1, (total + limite - 1) // limite)
        pagina = max(1, min(pagina, total_paginas))
        inicio = (pagina - 1) * limite
        fim = inicio + limite
        pagina_lancs = lancamentos[inicio:fim]

        return {
            "titulo": ctx.titulo,
            "pagina": pagina,
            "limite": limite,
            "total_lancamentos": total,
            "total_paginas": total_paginas,
            "totais": totais,
            "lancamentos": [
                {
                    "num_lcto": l.num_lcto,
                    "data": str(l.data),
                    "ind_lcto": l.ind_lcto if hasattr(l, "ind_lcto") else "",
                    "total_debito": l.total_debito if hasattr(l, "total_debito") else 0.0,
                    "total_credito": l.total_credito if hasattr(l, "total_credito") else 0.0,
                    "partidas": [
                        {
                            "cod_cta": p.cod_cta,
                            "historico": p.historico if hasattr(p, "historico") else "",
                            "debito": p.debito if hasattr(p, "debito") else 0.0,
                            "credito": p.credito if hasattr(p, "credito") else 0.0,
                        }
                        for p in (l.partidas if hasattr(l, "partidas") else [])
                    ],
                }
                for l in pagina_lancs
            ],
        }
    finally:
        session.close()


@router.get("/ecds/{ecd_id}/kpis")
async def api_kpis(ecd_id: int, api_key=Depends(requer_api_key)):
    """Indicadores financeiros (KPIs)."""
    session = _get_session()
    try:
        svc = DashboardService(session, ecd_id)
        data = svc.get_dashboard_data()

        return {
            "empresa": data.empresa_nome,
            "cnpj": data.empresa_cnpj,
            "periodo": data.periodo,
            "ativo_total": data.ativo_total,
            "passivo_total": data.passivo_total,
            "patrimonio_liquido": data.pl_total,
            "receita_liquida": data.receita_liquida,
            "resultado_liquido": data.resultado_liquido,
            "num_lancamentos": data.num_lancamentos,
            "num_contas": data.num_contas,
            "kpis": [
                {"titulo": k.titulo, "valor": k.valor, "formato": k.formato, "tendencia": k.tendencia}
                for k in data.kpis
            ],
        }
    finally:
        session.close()


@router.get("/ecds/{ecd_id}/notas")
async def api_notas(ecd_id: int, api_key=Depends(requer_api_key)):
    """Notas explicativas automáticas."""
    session = _get_session()
    try:
        svc = DashboardService(session, ecd_id)
        notas = svc.get_notas_explicativas()
        return {"notas": notas}
    finally:
        session.close()


@router.get("/ecds/{ecd_id}/validar")
async def api_validar(ecd_id: int, api_key=Depends(requer_api_key)):
    """Validações de integridade contábil."""
    session = _get_session()
    try:
        validador = ValidadorIntegridade(session, ecd_id)
        inconsistencias = validador.validar_todas()
        relatorio = validador.relatorio(inconsistencias)
        return relatorio
    finally:
        session.close()


@router.get("/ecds/{ecd_id}/evolucao-multi")
async def api_evolucao_multi(ecd_id: int, api_key=Depends(requer_api_key)):
    """Evolução patrimonial multi-período."""
    session = _get_session()
    try:
        svc = DashboardService(session, ecd_id)
        data = svc.get_evolucao_multi_periodo()
        return data or {"labels": [], "ativos": [], "passivos": [], "pls": [], "resultados": [], "num_periodos": 0}
    finally:
        session.close()


# ── Webhooks (Fase 10) ──────────────────────────────────────────────────────


@router.get("/webhooks/eventos")
async def listar_eventos(api_key=Depends(requer_api_key)):
    """Lista eventos disponíveis para webhooks."""
    return {
        "eventos": [
            {"tipo": e, "descricao": _descricao_evento(e)}
            for e in EVENTOS_DISPONIVEIS
        ]
    }


@router.get("/webhooks")
async def listar_webhooks(api_key=Depends(requer_api_key)):
    """Lista todos os webhooks registrados."""
    svc = WebhookService(_get_db_path())
    webhooks = svc.listar()
    return {
        "total": len(webhooks),
        "dados": [
            {
                "id": wh.id,
                "url": wh.url,
                "eventos": wh.get_eventos(),
                "descricao": wh.descricao,
                "ativo": wh.ativo,
                "criado_em": wh.criado_em.isoformat() if wh.criado_em else None,
                "ultimo_envio": wh.ultimo_envio.isoformat() if wh.ultimo_envio else None,
                "total_envios": wh.total_envios,
            }
            for wh in webhooks
        ],
    }


@router.post("/webhooks")
async def registrar_webhook(
    payload: dict = Body(...),
    api_key=Depends(requer_api_key),
):
    """Registra um novo webhook.

    Body:
        url: str — endpoint que receberá os POSTs
        eventos: list[str] — ex: ["ecd.importada", "ecd.validada"]
        secret: str | None — segredo para HMAC (opcional)
        descricao: str — descrição amigável
    """
    url = payload.get("url", "")
    eventos = payload.get("eventos", [])
    secret = payload.get("secret")
    descricao = payload.get("descricao", "")

    if not url:
        raise HTTPException(status_code=400, detail="URL é obrigatória")
    if not eventos:
        raise HTTPException(status_code=400, detail="Pelo menos um evento é obrigatório")

    # Valida eventos
    for evt in eventos:
        if evt not in EVENTOS_DISPONIVEIS:
            raise HTTPException(
                status_code=400,
                detail=f"Evento inválido: {evt}. Disponíveis: {EVENTOS_DISPONIVEIS}",
            )

    svc = WebhookService(_get_db_path())
    wh = svc.registrar(url=url, eventos=eventos, secret=secret, descricao=descricao)

    return {
        "status": "ok",
        "id": wh.id,
        "url": wh.url,
        "eventos": wh.get_eventos(),
        "descricao": wh.descricao,
    }


@router.put("/webhooks/{webhook_id}")
async def atualizar_webhook(
    webhook_id: int,
    payload: dict = Body(...),
    api_key=Depends(requer_api_key),
):
    """Atualiza um webhook existente."""
    svc = WebhookService(_get_db_path())

    eventos = payload.get("eventos")
    if eventos:
        for evt in eventos:
            if evt not in EVENTOS_DISPONIVEIS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Evento inválido: {evt}",
                )

    wh = svc.atualizar(
        webhook_id=webhook_id,
        url=payload.get("url"),
        eventos=eventos,
        secret=payload.get("secret"),
        descricao=payload.get("descricao"),
        ativo=payload.get("ativo"),
    )

    if not wh:
        raise HTTPException(status_code=404, detail="Webhook não encontrado")

    return {
        "status": "ok",
        "id": wh.id,
        "url": wh.url,
        "eventos": wh.get_eventos(),
        "descricao": wh.descricao,
        "ativo": wh.ativo,
    }


@router.delete("/webhooks/{webhook_id}")
async def remover_webhook(webhook_id: int, api_key=Depends(requer_api_key)):
    """Remove um webhook."""
    svc = WebhookService(_get_db_path())
    removido = svc.remover(webhook_id)
    if not removido:
        raise HTTPException(status_code=404, detail="Webhook não encontrado")
    return {"status": "ok", "mensagem": f"Webhook #{webhook_id} removido"}


def _descricao_evento(tipo: str) -> str:
    return {
        "ecd.importada": "Disparado quando uma nova ECD é importada com sucesso",
        "ecd.validada": "Disparado quando a validação de integridade é concluída",
        "relatorio.gerado": "Disparado quando um relatório contábil é gerado",
    }.get(tipo, tipo)
# ── Webhooks Dashboard (Fase 11) ─────────────────────────────────────────────


@router.get("/webhooks/dashboard")
async def webhook_dashboard_stats(api_key=Depends(requer_api_key)):
    """Estatísticas agregadas do dashboard de webhooks."""
    svc = WebhookService(_get_db_path())
    return svc.get_dashboard_stats()


@router.get("/webhooks/deliveries")
async def listar_deliveries(
    webhook_id: int | None = None,
    status: str | None = None,
    limite: int = 50,
    api_key=Depends(requer_api_key),
):
    """Lista histórico de entregas de webhooks."""
    svc = WebhookService(_get_db_path())
    deliveries = svc.get_deliveries(webhook_id=webhook_id, status=status, limite=limite)
    return {
        "total": len(deliveries),
        "dados": [
            {
                "id": d.id,
                "webhook_id": d.webhook_id,
                "evento": d.evento,
                "status": d.status,
                "status_code": d.status_code,
                "error_message": d.error_message,
                "tentativa": d.tentativa,
                "criado_em": d.criado_em.isoformat() if d.criado_em else None,
                "concluido_em": d.concluido_em.isoformat() if d.concluido_em else None,
            }
            for d in deliveries
        ],
    }


@router.post("/webhooks/retry")
async def retry_webhooks(
    webhook_id: int | None = None,
    api_key=Depends(requer_api_key),
):
    """Reenvia entregas com falha (retry manual)."""
    svc = WebhookService(_get_db_path())
    result = svc.retry_failed(webhook_id=webhook_id)
    return {"status": "ok", **result}
