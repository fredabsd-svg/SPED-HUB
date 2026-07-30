"""Rotas da API REST externa v1 — Fase 7 + Fase 10 + Fase 12.

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

API Keys (Fase 12):
  GET    /api/v1/api-keys        — Lista API Keys
  POST   /api/v1/api-keys        — Cria nova API Key
  DELETE /api/v1/api-keys/{id}   — Revoga API Key
  GET    /api/v1/webhooks        — Lista webhooks
  POST   /api/v1/webhooks        — Registra webhook
  PUT    /api/v1/webhooks/{id}   — Atualiza webhook
  DELETE /api/v1/webhooks/{id}   — Remove webhook
  GET    /api/v1/webhooks/eventos — Lista eventos disponíveis
"""

import datetime
import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.api import ApiKeyService, requer_admin_de_sessao, requer_api_key
from src.audit import AuditService
from src.dashboard.services import DashboardService
from src.db.models import (
    ECD,
    Empresa,
    Lancamento,
    Partida,
    PlanoConta,
    get_session,
    init_db_once,
    obter_engine,
)
from src.ratelimit import RateLimitService, get_limiter
from src.reports.balanco import BalancoPatrimonial
from src.reports.dfc import DFC
from src.reports.diario import LivroDiario
from src.reports.dre import DRE
from src.settings import database_reference
from src.validators.integridade import ValidadorIntegridade
from src.version import APP_VERSION
from src.webhooks import EVENTOS_DISPONIVEIS, WebhookService

logger = logging.getLogger("sped-hub.api.v1")

router = APIRouter(prefix="/api/v1", tags=["API v1"])


def _get_db_path() -> str:

    return database_reference()


def _get_session() -> Session:
    engine = obter_engine(_get_db_path())
    init_db_once(engine)
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
        "version": APP_VERSION,
        "database": db_status,
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
    }


# ── Empresas ────────────────────────────────────────────────────────────────


def _escopo_da_credencial(stmt, credencial):
    """Restringe uma query com `Empresa` ao escritório da credencial.

    `credencial` é a API Key (ou o usuário admin de sessão) que passou pela
    dependência. Chave **sem** escritório é chave de instância e lê tudo — é o
    comportamento histórico, mantido para não invalidar integração existente.
    Chave **com** escritório lê só o dele.

    Antes nenhuma rota de `/api/v1` filtrava por escritório e a coluna de dono
    não existia: uma chave entregue ao integrador do escritório A lia a
    escrituração do B.
    """
    escritorio_id = getattr(credencial, "escritorio_id", None)
    if escritorio_id is None:
        return stmt
    return stmt.where(Empresa.escritorio_id == escritorio_id)


async def ecd_autorizada(ecd_id: int, credencial=Depends(requer_api_key)) -> int:
    """Confirma que a credencial pode ver esta ECD e devolve o `ecd_id`.

    É dependência, não verificação copiada em cada rota: são nove rotas com
    `/{ecd_id}` e a décima esqueceria. Escopar só a listagem seria cosmético —
    quem quisesse a escrituração do vizinho pediria o id direto.

    Responde **404**, não 403: confirmar que a ECD existe e é de outro
    escritório já é informação. `usuario_pode_acessar_ecd` segue a mesma regra.
    """
    session = _get_session()
    try:
        stmt = _escopo_da_credencial(
            select(ECD.id).join(Empresa, ECD.empresa_id == Empresa.id).where(ECD.id == ecd_id),
            credencial,
        )
        if session.execute(stmt).scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="ECD não encontrada")
    finally:
        session.close()
    return ecd_id


@router.get("/empresas")
async def listar_empresas(
    api_key=Depends(requer_api_key),
    pagina: int = Query(1, ge=1),
    limite: int = Query(20, ge=1, le=100),
):
    """Lista empresas com paginação."""
    session = _get_session()
    try:
        # A contagem também é escopada: sem isso o total anunciaria empresas
        # que a página nunca mostra, e revelaria quantas existem no vizinho.
        total = (
            session.execute(_escopo_da_credencial(select(func.count(Empresa.id)), api_key)).scalar()
            or 0
        )
        offset = (pagina - 1) * limite
        empresas = (
            session.execute(
                _escopo_da_credencial(select(Empresa).order_by(Empresa.nome), api_key)
                .offset(offset)
                .limit(limite)
            )
            .scalars()
            .all()
        )

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

        ecds = (
            session.execute(
                select(ECD).where(ECD.empresa_id == empresa_id).order_by(ECD.dt_ini.desc())
            )
            .scalars()
            .all()
        )

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
        # O `join` com Empresa é o que permite escopar: sem ele a contagem de
        # ECDs não tem por onde chegar ao escritório.
        query = _escopo_da_credencial(
            select(func.count(ECD.id)).join(Empresa, ECD.empresa_id == Empresa.id), api_key
        )
        if empresa_id:
            query = query.where(ECD.empresa_id == empresa_id)
        total = session.execute(query).scalar() or 0

        offset = (pagina - 1) * limite
        query_ecds = _escopo_da_credencial(
            select(ECD, Empresa.nome).join(Empresa).order_by(ECD.importado_em.desc()), api_key
        )
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
async def detalhe_ecd(ecd_id: int = Depends(ecd_autorizada)):
    """Detalhes de uma ECD com estatísticas."""
    session = _get_session()
    try:
        ecd = session.get(ECD, ecd_id)
        if not ecd:
            raise HTTPException(status_code=404, detail="ECD não encontrada")

        empresa = session.get(Empresa, ecd.empresa_id)

        n_contas = (
            session.execute(
                select(func.count(PlanoConta.id)).where(PlanoConta.ecd_id == ecd_id)
            ).scalar()
            or 0
        )
        n_lancs = (
            session.execute(
                select(func.count(Lancamento.id)).where(Lancamento.ecd_id == ecd_id)
            ).scalar()
            or 0
        )
        n_partidas = (
            session.execute(
                select(func.count(Partida.id)).join(Lancamento).where(Lancamento.ecd_id == ecd_id)
            ).scalar()
            or 0
        )

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
    visao: str = Query("hierarquica", pattern="^(hierarquica|publicacao)$"),
    ecd_id: int = Depends(ecd_autorizada),
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
                    "cod_cta": ln.cod_cta,
                    "nome_cta": ln.nome_cta,
                    "nivel": ln.nivel,
                    "saldo_atual": ln.saldo_atual,
                    "saldo_anterior": ln.saldo_anterior,
                }
                for ln in grupos["ativo"]
            ],
            "passivo": [
                {
                    "cod_cta": ln.cod_cta,
                    "nome_cta": ln.nome_cta,
                    "nivel": ln.nivel,
                    "saldo_atual": ln.saldo_atual,
                    "saldo_anterior": ln.saldo_anterior,
                }
                for ln in grupos["passivo"]
            ],
            "patrimonio_liquido": [
                {
                    "cod_cta": ln.cod_cta,
                    "nome_cta": ln.nome_cta,
                    "nivel": ln.nivel,
                    "saldo_atual": ln.saldo_atual,
                    "saldo_anterior": ln.saldo_anterior,
                }
                for ln in grupos["pl"]
            ],
            "totais": totais,
        }
    finally:
        session.close()


@router.get("/ecds/{ecd_id}/dre")
async def api_dre(ecd_id: int = Depends(ecd_autorizada)):
    """Demonstração do Resultado do Exercício."""
    session = _get_session()
    try:
        dre = DRE(session, ecd_id)
        ctx, linhas, totais = dre.gerar()

        return {
            "titulo": ctx.titulo,
            "linhas": [
                {
                    "tipo": ln.tipo,
                    "descricao": ln.descricao,
                    "valor_atual": ln.valor_atual,
                    "valor_anterior": ln.valor_anterior,
                }
                for ln in linhas
            ],
            "totais": totais,
        }
    finally:
        session.close()


@router.get("/ecds/{ecd_id}/dfc")
async def api_dfc(ecd_id: int = Depends(ecd_autorizada)):
    """Demonstração dos Fluxos de Caixa."""
    session = _get_session()
    try:
        dfc = DFC(session, ecd_id)
        ctx, linhas, totais = dfc.gerar()

        return {
            "titulo": ctx.titulo,
            "linhas": [
                {
                    "tipo": ln.tipo,
                    "descricao": ln.descricao,
                    "valor": ln.valor,
                    "valor_anterior": ln.valor_anterior,
                }
                for ln in linhas
            ],
            "totais": totais,
        }
    finally:
        session.close()


@router.get("/ecds/{ecd_id}/diario")
async def api_diario(
    pagina: int = Query(1, ge=1),
    limite: int = Query(50, ge=1, le=200),
    ecd_id: int = Depends(ecd_autorizada),
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
                    "num_lcto": ln.num_lcto,
                    "data": str(ln.data),
                    "ind_lcto": ln.ind_lcto if hasattr(ln, "ind_lcto") else "",
                    "total_debito": ln.total_debito if hasattr(ln, "total_debito") else 0.0,
                    "total_credito": ln.total_credito if hasattr(ln, "total_credito") else 0.0,
                    "partidas": [
                        {
                            "cod_cta": p.cod_cta,
                            "historico": p.historico if hasattr(p, "historico") else "",
                            "debito": p.debito if hasattr(p, "debito") else 0.0,
                            "credito": p.credito if hasattr(p, "credito") else 0.0,
                        }
                        for p in (ln.partidas if hasattr(ln, "partidas") else [])
                    ],
                }
                for ln in pagina_lancs
            ],
        }
    finally:
        session.close()


@router.get("/ecds/{ecd_id}/kpis")
async def api_kpis(ecd_id: int = Depends(ecd_autorizada)):
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
                {
                    "titulo": k.titulo,
                    "valor": k.valor,
                    "formato": k.formato,
                    "tendencia": k.tendencia,
                }
                for k in data.kpis
            ],
        }
    finally:
        session.close()


@router.get("/ecds/{ecd_id}/notas")
async def api_notas(ecd_id: int = Depends(ecd_autorizada)):
    """Notas explicativas automáticas."""
    session = _get_session()
    try:
        svc = DashboardService(session, ecd_id)
        notas = svc.get_notas_explicativas()
        return {"notas": notas}
    finally:
        session.close()


@router.get("/ecds/{ecd_id}/validar")
async def api_validar(ecd_id: int = Depends(ecd_autorizada)):
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
async def api_evolucao_multi(ecd_id: int = Depends(ecd_autorizada)):
    """Evolução patrimonial multi-período."""
    session = _get_session()
    try:
        svc = DashboardService(session, ecd_id)
        data = svc.get_evolucao_multi_periodo()
        return data or {
            "labels": [],
            "ativos": [],
            "passivos": [],
            "pls": [],
            "resultados": [],
            "num_periodos": 0,
        }
    finally:
        session.close()


# ── Webhooks (Fase 10) ──────────────────────────────────────────────────────


@router.get("/webhooks/eventos")
async def listar_eventos(api_key=Depends(requer_api_key)):
    """Lista eventos disponíveis para webhooks."""
    return {
        "eventos": [{"tipo": e, "descricao": _descricao_evento(e)} for e in EVENTOS_DISPONIVEIS]
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
    try:
        wh = svc.registrar(url=url, eventos=eventos, secret=secret, descricao=descricao)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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

    try:
        wh = svc.atualizar(
            webhook_id=webhook_id,
            url=payload.get("url"),
            eventos=eventos,
            secret=payload.get("secret"),
            descricao=payload.get("descricao"),
            ativo=payload.get("ativo"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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
    result = await svc.retry_failed(webhook_id=webhook_id)
    return {"status": "ok", **result}


# ── API Keys (Fase 12) ──────────────────────────────────────────────────────


@router.get("/api-keys")
async def listar_api_keys(admin=Depends(requer_admin_de_sessao)):
    """Lista todas as API Keys (sem expor a chave completa)."""
    svc = ApiKeyService(_get_db_path())
    keys = svc.listar()
    return {"total": len(keys), "dados": keys}


@router.post("/api-keys")
async def criar_api_key(
    payload: dict = Body(...),
    admin=Depends(requer_admin_de_sessao),
):
    """Cria uma nova API Key.

    Body:
        nome: str — nome descritivo da chave
        dias_expiracao: int | None — dias até expirar (None = sem expiração)
    """
    nome = payload.get("nome", "").strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome é obrigatório")

    dias = payload.get("dias_expiracao")
    if dias is not None and (not isinstance(dias, int) or dias < 1):
        raise HTTPException(status_code=400, detail="dias_expiracao deve ser inteiro positivo")

    svc = ApiKeyService(_get_db_path())
    result = svc.criar(nome=nome, dias_expiracao=dias)
    return {"status": "ok", **result}


@router.delete("/api-keys/{key_id}")
async def revogar_api_key(key_id: int, admin=Depends(requer_admin_de_sessao)):
    """Revoga (desativa) uma API Key."""
    svc = ApiKeyService(_get_db_path())
    if svc.revogar(key_id):
        return {"status": "ok", "mensagem": f"API Key #{key_id} revogada"}
    raise HTTPException(status_code=404, detail="API Key não encontrada")


# ── Rate Limiting (Fase 13) ────────────────────────────────────────────────


@router.get("/api-keys/{key_id}/rate-limit")
async def obter_rate_limit(key_id: int, admin=Depends(requer_admin_de_sessao)):
    """Obtém a configuração de rate limit de uma API Key."""
    svc = RateLimitService(_get_db_path())
    config = svc.obter(key_id)
    if config is None:
        return {
            "api_key_id": key_id,
            "limite": 100,
            "janela": 60,
            "default": True,
            "mensagem": "Usando configuração padrão (100 req/60s)",
        }
    return {**config, "default": False}


@router.put("/api-keys/{key_id}/rate-limit")
async def configurar_rate_limit(
    key_id: int,
    payload: dict = Body(...),
    admin=Depends(requer_admin_de_sessao),
):
    """Configura o rate limit de uma API Key.

    Body:
        limite: int — máximo de requisições por janela
        janela: int — duração da janela em segundos
    """
    limite = payload.get("limite")
    janela = payload.get("janela")

    if limite is None or janela is None:
        raise HTTPException(status_code=400, detail="limite e janela são obrigatórios")

    if not isinstance(limite, int) or limite < 1:
        raise HTTPException(status_code=400, detail="limite deve ser inteiro >= 1")
    if not isinstance(janela, int) or janela < 1:
        raise HTTPException(status_code=400, detail="janela deve ser inteiro >= 1")

    svc = RateLimitService(_get_db_path())
    try:
        result = svc.configurar(key_id, limite, janela)
        return {"status": "ok", **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/api-keys/{key_id}/rate-limit")
async def remover_rate_limit(key_id: int, admin=Depends(requer_admin_de_sessao)):
    """Remove a configuração de rate limit (volta ao padrão)."""
    svc = RateLimitService(_get_db_path())
    if svc.remover(key_id):
        return {"status": "ok", "mensagem": f"Rate limit da API Key #{key_id} removido (padrão)"}
    raise HTTPException(status_code=404, detail="Configuração de rate limit não encontrada")


@router.get("/api-keys/{key_id}/rate-limit/status")
async def status_rate_limit(key_id: int, admin=Depends(requer_admin_de_sessao)):
    """Retorna o status atual do rate limit (contador em tempo real)."""
    limiter = get_limiter(_get_db_path())
    info = limiter.get_info(key_id)
    return {
        "api_key_id": key_id,
        "limite": info.limite,
        "janela": info.janela,
        "requisicoes_restantes": info.requisicoes_restantes,
        "reset_em_segundos": info.reset_em,
        "limite_excedido": info.limite_excedido,
    }


# ── Auditoria (Fase 13) ────────────────────────────────────────────────────


@router.get("/audit/logs")
async def listar_audit_logs(
    api_key=Depends(requer_api_key),
    usuario_id: int | None = Query(None),
    acao: str | None = Query(None),
    limite: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Lista logs de auditoria com filtros opcionais."""
    svc = AuditService(_get_db_path())
    logs = svc.listar(
        usuario_id=usuario_id,
        acao=acao,
        limite=limite,
        offset=offset,
    )
    total = svc.contar(usuario_id=usuario_id, acao=acao)
    return {
        "total": total,
        "limite": limite,
        "offset": offset,
        "dados": logs,
    }


@router.get("/audit/stats")
async def audit_stats(
    api_key=Depends(requer_api_key),
    horas: int = Query(24, ge=1, le=720),
):
    """Estatísticas de auditoria das últimas N horas."""
    svc = AuditService(_get_db_path())
    return svc.estatisticas(horas=horas)


@router.post("/audit/limpar")
async def limpar_audit_logs(
    api_key=Depends(requer_api_key),
    dias: int = Query(90, ge=1, le=3650),
):
    """Remove logs de auditoria mais antigos que o número de dias especificado."""
    svc = AuditService(_get_db_path())
    removidos = svc.limpar_antigos(dias=dias)
    return {
        "status": "ok",
        "removidos": removidos,
        "mensagem": f"{removidos} logs removidos (> {dias} dias)",
    }
