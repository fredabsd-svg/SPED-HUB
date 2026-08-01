"""Dashboard Web — FastAPI + Jinja2 + HTMX + Alpine.js.

Rotas:
  GET  /                    — Dashboard principal (requer auth)
  GET  /login               — Página de login
  POST /api/login           — Autenticação
  GET  /logout              — Logout
  GET  /register            — Página de registro
  POST /api/register        — Registro
  GET  /upload              — Upload de ECD/EFD/ECF
  POST /api/upload          — Upload de arquivo ECD
  POST /api/upload-efd      — Upload de arquivo EFD-Contribuições
  POST /api/upload-ecf      — Upload de arquivo ECF
  GET  /api/kpis            — KPIs (HTMX partial)
  GET  /api/graficos        — Dados para gráficos (JSON)
  GET  /api/balanco         — Balanço Patrimonial (HTMX partial)
  GET  /api/dre             — DRE (HTMX partial)
  GET  /api/diario          — Livro Diário (HTMX partial)
  GET  /api/dfc             — DFC (HTMX partial)
  GET  /api/export/pdf      — Exporta relatório para PDF
  GET  /api/export/xlsx     — Exporta relatório para XLSX
  GET  /api/ecds            — Lista ECDs disponíveis
  GET  /api/filtros/aplicar — Aplica filtros e retorna dados filtrados

API REST v1 (autenticação por X-API-Key):
  GET  /api/v1/health            — Health check público
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
"""

import asyncio
import datetime
import io
import logging
import pathlib
import sys
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import selectinload

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.api.graphql import graphql_router
from src.api.routes import router as api_v1_router
from src.audit import AuditService, get_audit_service, init_audit_service
from src.auth import (
    aplicar_escopo_empresas,
    get_auth,
    get_usuario_atual,
    init_auth,
    usuario_pode_acessar_ecd,
)
from src.cache.redis_cache import RedisCacheService
from src.dashboard.services import DashboardService
from src.db.models import (
    ECD,
    AjusteFiscal,
    DocumentoFiscal,
    Empresa,
    Escrituracao,
    criar_engine,
    get_session,
    init_db,
    obter_engine,
)
from src.documentos import (
    Alteracao,
    AlteracaoInvalida,
    ImportadorDeDocumentos,
    MotorDeClassificacao,
    PoliticaDeDuplicidade,
    Selecao,
    SelecaoVazia,
    confirmar,
    desfazer_lote,
    efetivo,
    historico,
    novo_lote,
    simular,
    valor_tipado,
)
from src.documentos.classificacao import aplicar as aplicar_classificacao
from src.documentos.planilha import EDITAVEIS
from src.ecd_importer import ECDImportError, ECDImportService
from src.email_service import get_email_service
from src.escrituracoes import (
    CADASTRO_FISCAL,
    TIPOS,
    CadastroInvalido,
    CampoObrigatorioAusente,
    GeradorEFDContribuicoes,
    GeradorEFDICMS,
    TipoSemLeiaute,
    TransmissaoInvalida,
    arquivar,
    campos,
    escrituracoes_do_documento,
    espelho,
    marcar_transmitida,
    pendencias,
    preencher,
)
from src.filters.engine import FilterCriteria
from src.logging_config import configurar_logging
from src.monitoring import build_operational_snapshot, janela_padrao_minutos, metrics_collector
from src.parsers.ecf import ECFParser
from src.parsers.efd import EFDParser
from src.ratelimit import get_ip_limiter, init_limiter, ip_do_request
from src.reports.balanco import BalancoPatrimonial
from src.reports.base import fmt_data, fmt_moeda
from src.reports.dfc import DFC
from src.reports.diario import LivroDiario
from src.reports.dre import DRE
from src.settings import database_reference, get_settings
from src.uploads import max_upload_bytes, safe_original_name, save_upload
from src.version import APP_VERSION

logger = logging.getLogger("sped-hub.dashboard")

# ── App ────────────────────────────────────────────────────────────────────


def executar_manutencao() -> dict[str, int]:
    """Expurga histórico operacional vencido.  Devolve o que foi removido.

    Duas tabelas cresciam sem limite. A de jobs tinha o expurgo escrito
    (`limpar_antigos`) e o docstring do módulo prometia "limpeza automática" —
    **e nada chamava a função**. A de entregas de webhook não tinha expurgo
    nenhum, e guarda uma linha por *tentativa*: integração instável enche a
    tabela rápido.

    `audit_logs` NÃO entra aqui, de propósito. Log de auditoria de escritório
    contábil é registro de quem mexeu em escrituração fiscal; apagá-lo por
    conta própria é decisão que o sistema não pode tomar sozinho. A limpeza
    dele segue manual, por rota de administrador.

    Idempotente e sem estado: rodar duas vezes junto não duplica nada, o que
    torna seguro haver mais de uma réplica executando.
    """
    from src.async_jobs import get_async_job_service
    from src.webhooks import WebhookService

    cfg = get_settings()
    referencia = _db_reference()
    resultado = {"jobs": 0, "entregas_de_webhook": 0, "webhooks_reenviados": 0}

    # Cada expurgo é isolado: falha em um não pode impedir o outro.
    try:
        if cfg.job_retention_hours > 0:
            resultado["jobs"] = get_async_job_service(referencia).limpar_antigos(
                horas=cfg.job_retention_hours
            )
    except Exception:
        logger.exception("Expurgo de jobs falhou")
    try:
        resultado["entregas_de_webhook"] = WebhookService(referencia).purgar_deliveries()
    except Exception:
        logger.exception("Expurgo de entregas de webhook falhou")

    # Reenvio automático só do que o processo abandonou — nunca do que esgotou
    # as tentativas.  Roda depois do expurgo: a entrega abandonada nunca é
    # expurgada, então a ordem não muda o conjunto, mas trabalhar sobre a
    # tabela já enxuta é mais barato.
    try:
        if cfg.webhook_auto_retry:
            recuperacao = asyncio.run(WebhookService(referencia).reenviar_abandonadas())
            resultado["webhooks_reenviados"] = recuperacao["sucessos"]
            if recuperacao["reenviados"]:
                logger.warning(
                    "Reenvio automático de webhook: %d entrega(s) retomada(s), "
                    "%d com sucesso — o processo havia morrido no meio delas",
                    recuperacao["reenviados"],
                    recuperacao["sucessos"],
                )
    except Exception:
        logger.exception("Reenvio automático de webhook falhou")

    if any(resultado.values()):
        logger.info(
            "Manutenção: %d job(s) e %d entrega(s) removidos, %d webhook(s) reenviado(s)",
            resultado["jobs"],
            resultado["entregas_de_webhook"],
            resultado["webhooks_reenviados"],
        )
    return resultado


async def _laco_de_manutencao() -> None:
    """Roda a manutenção a cada `SPED_HUB_MAINTENANCE_INTERVAL_MINUTES`.

    O intervalo é relido a cada volta, não fixado na entrada: a instância
    global nasce no import e congelar configuração ali valeria para o processo
    inteiro. Intervalo `<= 0` encerra o laço — desligar a manutenção é uma
    escolha válida (quem prefere cron, por exemplo), e o histórico volta a
    crescer sem limite.

    O expurgo é síncrono e toca o banco, então vai para um thread: dentro do
    laço de eventos ele bloquearia toda requisição durante a execução.
    """
    while True:
        intervalo = get_settings().maintenance_interval_minutes
        if intervalo <= 0:
            logger.info("Manutenção periódica desligada (intervalo <= 0)")
            return
        await asyncio.sleep(intervalo * 60)
        try:
            await asyncio.to_thread(executar_manutencao)
        except asyncio.CancelledError:
            raise
        except Exception:
            # O laço não pode morrer por causa de uma volta ruim: se morrer,
            # o histórico volta a crescer sem limite e ninguém percebe.
            logger.exception("Volta da manutenção periódica falhou")


@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    """Encerra jobs abandonados na subida e mantém o expurgo periódico rodando.

    O executor de uma importação assíncrona é uma thread `daemon` dentro deste
    processo. Thread `daemon` é morta no encerramento do interpretador sem
    rodar `finally`, então reinício, atualização ou queda deixavam o job em
    aberto no banco para sempre — e a mensagem que sobrava era "Aguardando
    processamento...", que diz a quem enviou a escrituração que ela está na
    fila. Não estava: ninguém mais ia executá-la.

    Job em aberto enquanto o processo sobe é, por construção, job abandonado —
    não há fila que alguém varra. Ver `AsyncJobService.recuperar_interrompidos`
    para a ressalva de múltiplas réplicas.

    A manutenção periódica é o que faz o expurgo de histórico acontecer de
    fato: o código existia e ninguém o chamava.
    """
    try:
        from src.async_jobs import get_async_job_service, init_async_job_service

        init_async_job_service(_db_reference())
        get_async_job_service().recuperar_interrompidos()
    except Exception:
        # Recuperar jobs não pode impedir a aplicação de subir: um job em
        # aberto a mais é melhor que o escritório sem sistema.
        logger.exception("Falha ao recuperar jobs interrompidos na subida")

    manutencao = asyncio.create_task(_laco_de_manutencao())
    try:
        yield
    finally:
        # Sem o cancelamento, o teste que sobe o app deixa a tarefa pendurada e
        # o `asyncio` reclama de "task was destroyed but it is pending".
        manutencao.cancel()
        with suppress(asyncio.CancelledError):
            await manutencao


app = FastAPI(title="SPED-HUB Dashboard", version=APP_VERSION, lifespan=ciclo_de_vida)

# ── API REST v1 ──────────────────────────────────────────────────────────
app.include_router(api_v1_router)

# ── GraphQL API v2 ───────────────────────────────────────────────────────
app.include_router(graphql_router)

# Templates
STATIC_DIR = Path(__file__).resolve().parent / "static"
# htmx, Alpine, Chart.js e SortableJS são servidos daqui, não de CDN: sem
# acesso externo a aplicação degradava em silêncio, e cada página carregava
# uma versão diferente.  Ver src/dashboard/static/vendor/README.md.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)

configurar_logging()

# Banco configurado (resolvido antes de inicializar serviços globais).
# Vem de settings, então DATABASE_URL vale aqui — antes só SPED_HUB_DB era
# lido e a URL documentada era silenciosamente ignorada pelo dashboard.
DB_REFERENCE = database_reference()

# Inicializa auth e banco
init_auth(DB_REFERENCE)
init_audit_service(DB_REFERENCE)
init_limiter(DB_REFERENCE)
engine = criar_engine(DB_REFERENCE)
init_db(engine)


def _db_reference() -> str:
    """Banco corrente — relido a cada uso porque os testes o trocam em runtime."""
    return database_reference()


def _get_engine():
    return obter_engine(_db_reference())


_PUBLIC_API_PATHS = {
    "/api/login",
    "/api/register",
    "/api/health/full",
}


# Nomes de loopback ficam sempre liberados, independentemente do allowlist:
# o HEALTHCHECK do container chama `http://localhost:8000/api/v1/health`, e
# recusar esse Host marcaria o container como não saudável para sempre — o
# mesmo defeito que a 0.16.0 já corrigiu por outro caminho.  Liberar loopback
# não ajuda atacante: um link `http://localhost/...` não leva a nada para ele.
_HOSTS_SEMPRE_ACEITOS = {"localhost", "127.0.0.1", "[::1]", "::1", "testserver"}


def _host_permitido(host: str, permitidos: tuple[str, ...]) -> bool:
    """Compara o Host recebido com o allowlist, aceitando `*` e `*.dominio`."""
    if "*" in permitidos:
        return True
    # A porta não entra na comparação: o allowlist documenta domínio.
    host = host.strip().lower()
    if host.startswith("["):  # IPv6 literal: [::1]:8000
        host = host.split("]")[0] + "]"
    elif ":" in host:
        host = host.split(":", 1)[0]
    if not host or host in _HOSTS_SEMPRE_ACEITOS:
        return True
    for permitido in permitidos:
        permitido = permitido.strip().lower()
        if not permitido:
            continue
        if permitido.startswith("*."):
            if host == permitido[2:] or host.endswith(permitido[1:]):
                return True
        elif host == permitido:
            return True
    return False


@app.middleware("http")
async def validar_host(request: Request, call_next):
    """Recusa Host desconhecido quando `SPED_HUB_ALLOWED_HOSTS` não é `*`.

    A variável era documentada em `.env.example`, no README e no
    `docs/deploy.md` — que manda pôr o domínio real, "**não** `*`", como
    passo de endurecimento — e nenhum componente a lia.  Quem seguia o guia
    de deploy acreditava ter restringido o Host e não havia restrição
    nenhuma (§2.2).

    Lido a cada requisição, não no import: as fixtures trocam configuração em
    runtime, e `add_middleware` congelaria o valor no import do módulo.
    """
    permitidos = get_settings().allowed_hosts
    if not _host_permitido(request.headers.get("host", ""), permitidos):
        # A resposta não ecoa o allowlist: não é informação para quem
        # está sondando qual domínio responde aqui.
        return JSONResponse(
            {"status": "erro", "mensagem": "Host não permitido"},
            status_code=400,
        )
    return await call_next(request)


@app.middleware("http")
async def collect_request_metrics(request: Request, call_next):
    """Registra status e latência sem armazenar query strings ou payloads."""
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        metrics_collector.record(
            request.method,
            request.url.path,
            status_code,
            (time.perf_counter() - started) * 1000,
        )


_ROTAS_DE_AUTENTICACAO = {"/api/login", "/api/register"}


@app.middleware("http")
async def rate_limit_por_ip(request: Request, call_next):
    """Limite por endereço de origem, complementar ao limite por API Key.

    O limitador por API Key não protege `/api/login` nem `/api/register`:
    são públicos por definição e não têm chave.  Sem limite por IP, varrer
    senhas não custa nada ao atacante.  Por isso o escopo de autenticação
    tem cota própria e bem mais apertada que a do restante da API.
    """
    caminho = request.url.path.rstrip("/") or "/"
    if not caminho.startswith("/api/"):
        return await call_next(request)

    cfg = get_settings()
    if caminho in _ROTAS_DE_AUTENTICACAO:
        escopo, limite, janela = (
            "login",
            cfg.rate_limit_login_default,
            cfg.rate_limit_login_window_seconds,
        )
    else:
        escopo, limite, janela = (
            "api",
            cfg.rate_limit_ip_default,
            cfg.rate_limit_ip_window_seconds,
        )

    origem = ip_do_request(request)
    permitido, info = get_ip_limiter().verificar(origem, escopo, limite, janela)
    if not permitido:
        # A resposta não distingue "usuário existe" de "usuário não existe"
        # nem ecoa o que foi tentado — só o limite.
        return JSONResponse(
            {
                "status": "erro",
                "mensagem": f"Muitas requisições. Tente novamente em {info.reset_em}s.",
            },
            status_code=429,
            headers={
                "Retry-After": str(info.reset_em),
                "X-RateLimit-Limit": str(info.limite),
                "X-RateLimit-Remaining": "0",
            },
        )

    resposta = await call_next(request)
    # `setdefault`, não atribuição: o limitador por API Key roda mais adentro e
    # já pode ter anunciado a cota específica daquela chave.  Sobrescrever com
    # a cota por IP faria o cliente ver o número errado e planejar em cima dele.
    resposta.headers.setdefault("X-RateLimit-Limit", str(info.limite))
    resposta.headers.setdefault("X-RateLimit-Remaining", str(info.restantes))
    return resposta


@app.middleware("http")
async def require_dashboard_api_auth(request: Request, call_next):
    """Protege APIs internas; REST v1 e GraphQL têm autenticação própria."""
    path = request.url.path.rstrip("/") or "/"
    is_external_api = path.startswith("/api/v1") or path.startswith("/api/v2/graphql")
    if path.startswith("/api/") and path not in _PUBLIC_API_PATHS and not is_external_api:
        usuario = await get_usuario_atual(request)
        if usuario is None:
            return JSONResponse(
                {"status": "erro", "mensagem": "Não autenticado"},
                status_code=401,
            )
        request.state.usuario = usuario

        admin_prefixes = (
            "/api/audit",
            "/api/cache",
            "/api/email",
            "/api/worker",
            "/api/redis",
            "/api/monitoring",
        )
        if path.startswith(admin_prefixes) and not usuario.admin:
            return JSONResponse(
                {"status": "erro", "mensagem": "Acesso administrativo necessário"},
                status_code=403,
            )

        raw_ids = []
        if request.query_params.get("ecd_id"):
            raw_ids.append(request.query_params["ecd_id"])
        if request.query_params.get("ecd_ids"):
            raw_ids.extend(request.query_params["ecd_ids"].split(","))
        ecd_ids = {int(value.strip()) for value in raw_ids if value.strip().isdigit()}
        if ecd_ids:
            session = get_session(_get_engine())
            try:
                if any(
                    not usuario_pode_acessar_ecd(session, usuario, ecd_id) for ecd_id in ecd_ids
                ):
                    return JSONResponse(
                        {"status": "erro", "mensagem": "ECD não encontrada"},
                        status_code=404,
                    )
            finally:
                session.close()
    return await call_next(request)


# ── Filtros Jinja2 ─────────────────────────────────────────────────────────

jinja_env.globals["fmt_moeda"] = fmt_moeda
jinja_env.globals["fmt_data"] = fmt_data
jinja_env.globals["now"] = datetime.datetime.now
jinja_env.globals["app_version"] = APP_VERSION


# ── Rotas: Autenticação ────────────────────────────────────────────────────


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Página de login."""
    usuario = await get_usuario_atual(request)
    if usuario:
        return RedirectResponse(url="/", status_code=302)
    return HTMLResponse(jinja_env.get_template("login.html").render({"request": request}))


@app.post("/api/login")
async def api_login(request: Request):
    """Autentica usuário."""
    form = await request.form()
    email = form.get("email", "")
    senha = form.get("senha", "")

    try:
        auth = get_auth()
        usuario, token, usuario_id, usuario_email = auth.login(
            email=email,
            senha=senha,
            ip=request.client.host if request.client else "",
            user_agent=request.headers.get("User-Agent", ""),
        )
        # Registra auditoria
        svc = get_audit_service()
        svc.registrar(
            acao="auth.login",
            recurso=f"Login: {email}",
            usuario_id=usuario_id,
            usuario_email=usuario_email,
            ip=request.client.host if request.client else None,
            status_code=200,
        )

        response = JSONResponse({"status": "ok", "redirect": "/"})
        response.set_cookie(
            key="sped_hub_session",
            value=token,
            httponly=True,
            max_age=86400,
            samesite="lax",
        )
        return response
    except ValueError as e:
        # Registra tentativa falha
        svc = get_audit_service()
        svc.registrar(
            acao="auth.login",
            recurso=f"Login falho: {email}",
            usuario_email=email,
            ip=request.client.host if request.client else None,
            status_code=401,
            detalhes={"erro": str(e)},
        )
        return JSONResponse({"status": "erro", "mensagem": str(e)}, status_code=401)


@app.get("/logout")
async def logout(request: Request):
    """Logout."""
    token = request.cookies.get("sped_hub_session")
    usuario = None
    if token:
        usuario = get_auth().validar_token(token)
        get_auth().logout(token)
    # Captura dados antes do objeto ser detached
    uid = usuario.id if usuario else None
    uemail = usuario.email if usuario else None
    # Registra auditoria
    svc = get_audit_service()
    svc.registrar(
        acao="auth.logout",
        recurso="Logout",
        usuario_id=uid,
        usuario_email=uemail,
        ip=request.client.host if request.client else None,
    )
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("sped_hub_session")
    return response


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Página de registro."""
    return HTMLResponse(jinja_env.get_template("register.html").render({"request": request}))


@app.post("/api/register")
async def api_register(request: Request):
    """Registra novo usuário."""
    form = await request.form()
    email = form.get("email", "")
    nome = form.get("nome", "")
    senha = form.get("senha", "")

    if not email or not nome or not senha:
        return JSONResponse(
            {"status": "erro", "mensagem": "Todos os campos são obrigatórios"}, status_code=400
        )
    if len(senha) < 6:
        return JSONResponse(
            {"status": "erro", "mensagem": "Senha deve ter no mínimo 6 caracteres"}, status_code=400
        )

    try:
        auth = get_auth()
        usuario = auth.registrar(email=email, nome=nome, senha=senha)
    except ValueError as e:
        # Registro fechado é recusa de permissão, não erro de preenchimento:
        # devolver 400 faria a tela pedir para o visitante corrigir o formulário.
        if "fechado" in str(e).lower():
            return JSONResponse({"status": "erro", "mensagem": str(e)}, status_code=403)
        return JSONResponse({"status": "erro", "mensagem": str(e)}, status_code=400)

    try:
        usuario_id = usuario.id
        # Registra auditoria
        svc = get_audit_service()
        svc.registrar(
            acao="auth.register",
            recurso=f"Novo usuário: {email}",
            usuario_id=usuario_id,
            usuario_email=email,
            ip=request.client.host if request.client else None,
        )
        return JSONResponse({"status": "ok", "redirect": "/login"})
    except ValueError as e:
        return JSONResponse({"status": "erro", "mensagem": str(e)}, status_code=400)


# ── Rotas: Páginas ─────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard principal (requer autenticação)."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    session = get_session(_get_engine())
    try:
        from sqlalchemy import desc

        latest_stmt = select(ECD).join(Empresa)
        latest_stmt = aplicar_escopo_empresas(latest_stmt, usuario)
        ecd = session.execute(
            latest_stmt.order_by(desc(ECD.importado_em)).limit(1)
        ).scalar_one_or_none()

        if ecd:
            svc = DashboardService(session, ecd.id)
            data = svc.get_dashboard_data()
            evolucao = svc.get_evolucao_patrimonial()
            composicao = svc.get_composicao_ativo()
            dre_waterfall = svc.get_dre_waterfall()
            ecds = svc.get_ecds_disponiveis(usuario)
            # Dados para comparativo
            comparativo = svc.get_comparativo_empresas(usuario) if len(ecds) > 1 else None
        else:
            data = None
            evolucao = None
            composicao = None
            dre_waterfall = None
            ecds = []
            comparativo = None

        return HTMLResponse(
            jinja_env.get_template("dashboard.html").render(
                {
                    "request": request,
                    "usuario": usuario,
                    "data": data,
                    "evolucao": evolucao,
                    "composicao": composicao,
                    "dre_waterfall": dre_waterfall,
                    "comparativo": comparativo,
                    "ecds": ecds,
                    "ecd_ativo": ecd.id if ecd else None,
                    "current_page": "dashboard",
                }
            )
        )
    finally:
        session.close()


@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    """Página de upload."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    return HTMLResponse(
        jinja_env.get_template("upload.html").render(
            {
                "request": request,
                "usuario": usuario,
                "current_page": "upload",
            }
        )
    )


# ── Rotas: API Upload ──────────────────────────────────────────────────────


@app.post("/api/upload")
async def api_upload(request: Request, file: UploadFile = File(...)):
    """Upload e importação incremental de arquivo ECD."""
    saved = await save_upload(file, (".txt", ".ecd"))
    session = get_session(_get_engine())
    try:
        result = ECDImportService(session).importar(
            saved.path,
            hash_arquivo=saved.sha256,
            nome_arquivo=saved.original_name,
            escritorio_id=request.state.usuario.escritorio_id,
        )
        AuditService(_db_reference()).registrar(
            acao="ecd.upload",
            recurso=f"ECD #{result.ecd_id} ({result.empresa})",
            detalhes=result.to_dict(),
        )
        return JSONResponse(
            {
                "status": "ok",
                "mensagem": (
                    "ECD importada com sucesso! "
                    f"{result.contas} contas, {result.lancamentos} lançamentos, "
                    f"{result.partidas} partidas."
                ),
                "ecd_id": result.ecd_id,
                "empresa": result.empresa,
                "periodo": result.periodo,
            }
        )
    except ECDImportError as exc:
        return JSONResponse(
            {"status": "erro", "mensagem": str(exc)},
            status_code=400,
        )
    except Exception as exc:
        logger.exception("Erro ao importar ECD")
        return JSONResponse(
            {"status": "erro", "mensagem": str(exc)},
            status_code=500,
        )
    finally:
        session.close()
        saved.path.unlink(missing_ok=True)


@app.post("/api/upload-efd")
async def api_upload_efd(file: UploadFile = File(...)):
    """Upload de arquivo EFD-Contribuições."""
    saved = await save_upload(file, (".txt", ".efd"))
    temp_path = saved.path

    try:
        parser = EFDParser()
        resumo = parser.extrair_resumo(temp_path)
        return JSONResponse(
            {
                "status": "ok",
                "mensagem": f"EFD-Contribuições processada! {resumo['total_registros']} registros.",
                "resumo": resumo,
            }
        )
    except Exception as e:
        logger.exception("Erro ao processar EFD")
        return JSONResponse({"status": "erro", "mensagem": str(e)}, status_code=500)
    finally:
        if temp_path.exists():
            temp_path.unlink()


@app.post("/api/upload-ecf")
async def api_upload_ecf(file: UploadFile = File(...)):
    """Upload de arquivo ECF."""
    saved = await save_upload(file, (".txt", ".ecf"))
    temp_path = saved.path

    try:
        parser = ECFParser()
        resumo = parser.extrair_resumo(temp_path)
        return JSONResponse(
            {
                "status": "ok",
                "mensagem": f"ECF processada! {resumo['total_registros']} registros.",
                "resumo": resumo,
            }
        )
    except Exception as e:
        logger.exception("Erro ao processar ECF")
        return JSONResponse({"status": "erro", "mensagem": str(e)}, status_code=500)
    finally:
        if temp_path.exists():
            temp_path.unlink()


# ── Rotas: API Dados ───────────────────────────────────────────────────────


@app.get("/api/kpis", response_class=HTMLResponse)
async def api_kpis(request: Request, ecd_id: int = Query(...)):
    session = get_session(_get_engine())
    try:
        svc = DashboardService(session, ecd_id)
        data = svc.get_dashboard_data()
        return HTMLResponse(
            jinja_env.get_template("partials/kpis.html").render(
                {
                    "request": request,
                    "data": data,
                }
            )
        )
    finally:
        session.close()


@app.get("/api/graficos")
async def api_graficos(request: Request, ecd_id: int = Query(...)):
    session = get_session(_get_engine())
    try:
        svc = DashboardService(session, ecd_id)
        return {
            "evolucao_multi": svc.get_evolucao_multi_periodo(),
            "evolucao": svc.get_evolucao_patrimonial(),
            "composicao": svc.get_composicao_ativo(),
            "dre_waterfall": svc.get_dre_waterfall(),
            "dfc": svc.get_dfc_data(),
            "comparativo": svc.get_comparativo_empresas(request.state.usuario),
        }
    finally:
        session.close()


@app.get("/api/balanco", response_class=HTMLResponse)
async def api_balanco(
    request: Request, ecd_id: int = Query(...), visao: str = Query("hierarquica")
):
    session = get_session(_get_engine())
    try:
        balanco = BalancoPatrimonial(session, ecd_id)
        if visao == "publicacao":
            ctx, grupos, totais = balanco.gerar_publicacao()
        else:
            ctx, grupos, totais = balanco.gerar(visao=visao)
        return HTMLResponse(
            jinja_env.get_template("partials/balanco.html").render(
                {
                    "request": request,
                    "ctx": ctx,
                    "grupos": grupos,
                    "totais": totais,
                    "visao": visao,
                    "ecd_id": ecd_id,
                }
            )
        )
    finally:
        session.close()


@app.get("/api/dre", response_class=HTMLResponse)
async def api_dre(request: Request, ecd_id: int = Query(...)):
    session = get_session(_get_engine())
    try:
        dre = DRE(session, ecd_id)
        ctx, linhas, totais = dre.gerar()
        return HTMLResponse(
            jinja_env.get_template("partials/dre.html").render(
                {
                    "request": request,
                    "ctx": ctx,
                    "linhas": linhas,
                    "totais": totais,
                    "ecd_id": ecd_id,
                }
            )
        )
    finally:
        session.close()


@app.get("/api/diario", response_class=HTMLResponse)
async def api_diario(request: Request, ecd_id: int = Query(...), pagina: int = Query(1)):
    session = get_session(_get_engine())
    try:
        diario = LivroDiario(session, ecd_id)
        ctx, lancamentos, totais = diario.gerar()
        per_page = 20
        total_paginas = max(1, (len(lancamentos) + per_page - 1) // per_page)
        pagina = max(1, min(pagina, total_paginas))
        inicio = (pagina - 1) * per_page
        fim = inicio + per_page
        pagina_lancs = lancamentos[inicio:fim]
        return HTMLResponse(
            jinja_env.get_template("partials/diario.html").render(
                {
                    "request": request,
                    "ctx": ctx,
                    "lancamentos": pagina_lancs,
                    "totais": totais,
                    "pagina": pagina,
                    "total_paginas": total_paginas,
                    "ecd_id": ecd_id,
                }
            )
        )
    finally:
        session.close()


@app.get("/api/dfc", response_class=HTMLResponse)
async def api_dfc(request: Request, ecd_id: int = Query(...)):
    session = get_session(_get_engine())
    try:
        dfc = DFC(session, ecd_id)
        ctx, linhas, totais = dfc.gerar()
        return HTMLResponse(
            jinja_env.get_template("partials/dfc.html").render(
                {
                    "request": request,
                    "ctx": ctx,
                    "linhas": linhas,
                    "totais": totais,
                    "ecd_id": ecd_id,
                }
            )
        )
    finally:
        session.close()


@app.get("/api/ecds")
async def api_ecds(request: Request):
    session = get_session(_get_engine())
    try:
        svc = DashboardService(session, 0)
        return svc.get_ecds_disponiveis(request.state.usuario)
    finally:
        session.close()


# ── Rotas: Auditoria (Fase 13) ─────────────────────────────────────────────


@app.get("/auditoria", response_class=HTMLResponse)
async def auditoria_page(request: Request):
    """Página de logs de auditoria."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)
    if not usuario.admin:
        return _monitoring_forbidden()

    return HTMLResponse(
        jinja_env.get_template("auditoria.html").render(
            {
                "request": request,
                "usuario": usuario,
                "current_page": "auditoria",
            }
        )
    )


@app.get("/api/audit/logs")
async def api_audit_logs(
    request: Request,
    usuario_id: int | None = Query(None),
    acao: str | None = Query(None),
    limite: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Lista logs de auditoria (requer autenticação)."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return JSONResponse({"status": "erro", "mensagem": "Não autenticado"}, status_code=401)

    svc = get_audit_service()
    logs = svc.listar(
        usuario_id=usuario_id,
        acao=acao,
        limite=limite,
        offset=offset,
    )
    total = svc.contar(usuario_id=usuario_id, acao=acao)
    return {"total": total, "limite": limite, "offset": offset, "dados": logs}


@app.get("/api/audit/stats")
async def api_audit_stats(
    request: Request,
    horas: int = Query(24, ge=1, le=720),
):
    """Estatísticas de auditoria (requer autenticação)."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return JSONResponse({"status": "erro", "mensagem": "Não autenticado"}, status_code=401)

    svc = get_audit_service()
    return svc.estatisticas(horas=horas)


@app.post("/api/audit/limpar")
async def api_audit_limpar(
    request: Request,
    dias: int = Query(90, ge=1, le=3650),
):
    """Remove logs antigos (requer autenticação)."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return JSONResponse({"status": "erro", "mensagem": "Não autenticado"}, status_code=401)

    svc = get_audit_service()
    removidos = svc.limpar_antigos(dias=dias)

    # Registra a ação de limpeza
    svc.registrar(
        acao="audit.limpar",
        recurso=f"Logs > {dias} dias",
        usuario_id=usuario.id,
        usuario_email=usuario.email,
        ip=request.client.host if request.client else None,
        detalhes={"removidos": removidos, "dias": dias},
    )

    return {"status": "ok", "removidos": removidos}


# ── Rotas: Jobs Assíncronos (Fase 14) ──────────────────────────────────────


@app.post("/api/upload-async")
async def api_upload_async(request: Request, file: UploadFile = File(...)):
    """Upload assíncrono de ECD com importação incremental e polling."""
    from src.async_jobs import get_async_job_service, init_async_job_service

    saved = await save_upload(file, (".txt", ".ecd"))
    escritorio_id = request.state.usuario.escritorio_id
    db_path = _db_reference()
    init_async_job_service(db_path)
    job_service = get_async_job_service()
    job = job_service.criar(
        tipo="ecd_import",
        parametros={
            "arquivo": saved.original_name,
            "tamanho_bytes": saved.size_bytes,
            "usuario_id": request.state.usuario.id,
            "escritorio_id": escritorio_id,
        },
    )

    import threading

    from src.ecd_importer import CancelToken, ECDImportCancelled

    cancel_token = CancelToken()
    job_service.registrar_token(job.id, cancel_token)
    # Grava no banco que o job começou e onde está o arquivo dele.  O progresso
    # é reportado com `persistir=False`, então sem isto a linha diria
    # `pending` / 0% / "Aguardando processamento..." durante a importação
    # inteira, e um reinício deixaria um job que parece nem ter começado.
    job_service.marcar_em_execucao(job.id, arquivo_temporario=str(saved.path))

    def process_upload():
        session = get_session(obter_engine(db_path))
        try:
            result = ECDImportService(session).importar(
                saved.path,
                hash_arquivo=saved.sha256,
                nome_arquivo=saved.original_name,
                escritorio_id=escritorio_id,
                cancel_token=cancel_token,
                progress=lambda pct, msg: job_service.atualizar_progresso(
                    job.id, pct, msg, persistir=False
                ),
            )
            job_service.concluir(job.id, result.to_dict())
        except ECDImportCancelled as cancelado:
            # Não é falha: o usuário pediu.  Nada foi persistido.
            job_service.marcar_cancelado(job.id, str(cancelado))
        except Exception as exc:
            logger.exception("Erro no job assíncrono #%d", job.id)
            job_service.falhar(job.id, str(exc))
        finally:
            job_service.esquecer_token(job.id)
            session.close()
            saved.path.unlink(missing_ok=True)

    threading.Thread(target=process_upload, daemon=True).start()
    return JSONResponse(
        {
            "status": "ok",
            "mensagem": "Processamento iniciado em background",
            "job_id": job.id,
            "poll_url": f"/api/jobs/{job.id}",
        }
    )


@app.get("/api/jobs/{job_id}")
async def api_job_status(request: Request, job_id: int):
    """Consulta status de um job assíncrono."""
    from src.async_jobs import get_async_job_service

    svc = get_async_job_service(_db_reference())
    usuario = request.state.usuario
    info = svc.obter(job_id, usuario_id=usuario.id, admin=usuario.admin)
    if info is None:
        return JSONResponse({"status": "erro", "mensagem": "Job não encontrado"}, status_code=404)

    return {
        "id": info.id,
        "status": info.status,
        "progresso": info.progresso,
        "tipo": info.tipo,
        "mensagem": info.mensagem,
        "resultado": info.resultado,
        "erro": info.erro,
        "criado_em": info.criado_em,
        "concluido_em": info.concluido_em,
    }


@app.post("/api/jobs/{job_id}/cancelar")
async def api_job_cancelar(request: Request, job_id: int):
    """Cancela um job em andamento.  Nada do que foi lido é persistido."""
    from src.async_jobs import get_async_job_service

    usuario = request.state.usuario
    svc = get_async_job_service(_db_reference())
    info = svc.obter(job_id, usuario_id=usuario.id, admin=usuario.admin)
    if info is None:
        return JSONResponse({"status": "erro", "mensagem": "Job não encontrado"}, status_code=404)

    if not svc.cancelar(job_id, motivo=f"cancelado por {usuario.email}"):
        return JSONResponse(
            {"status": "erro", "mensagem": "Job não está em execução neste processo"},
            status_code=409,
        )
    return JSONResponse({"status": "ok", "mensagem": "Cancelamento solicitado", "job_id": job_id})


@app.get("/api/jobs")
async def api_jobs_list(request: Request, status: str | None = Query(None)):
    """Lista jobs assíncronos."""
    from src.async_jobs import get_async_job_service

    svc = get_async_job_service(_db_reference())
    usuario = request.state.usuario
    jobs = svc.listar(status=status, usuario_id=usuario.id, admin=usuario.admin)
    return {
        "total": len(jobs),
        "dados": [
            {
                "id": j.id,
                "status": j.status,
                "progresso": j.progresso,
                "tipo": j.tipo,
                "mensagem": j.mensagem,
                "criado_em": j.criado_em,
                "concluido_em": j.concluido_em,
            }
            for j in jobs
        ],
    }


@app.get("/api/cache/stats")
async def api_cache_stats():
    """Estatísticas do cache."""
    from src.cache import get_cache, init_cache

    init_cache()
    return get_cache().stats()


# ── Rotas: Exportação ──────────────────────────────────────────────────────


@app.get("/api/export/pdf")
async def api_export_pdf(
    ecd_id: int = Query(...),
    tipo: str = Query("balanco"),
    visao: str = Query("hierarquica"),
):
    """Exporta relatório para PDF."""
    session = get_session(_get_engine())
    try:
        from src.db.models import ECD, Empresa
        from src.reports.base import ReportContext
        from src.reports.export_engine import ExportEngine, WhiteLabel

        ecd = session.get(ECD, ecd_id)
        empresa = session.get(Empresa, ecd.empresa_id) if ecd else None

        wl = WhiteLabel()
        export = ExportEngine()
        ctx = ReportContext(
            titulo="",
            empresa_nome=empresa.nome if empresa else "",
            empresa_cnpj=empresa.cnpj if empresa else "",
            periodo_ref=f"{ecd.dt_ini} a {ecd.dt_fin}" if ecd else "",
        )

        if tipo == "balanco":
            balanco = BalancoPatrimonial(session, ecd_id)
            if visao == "publicacao":
                ctx_rel, grupos, totais = balanco.gerar_publicacao()
            else:
                ctx_rel, grupos, totais = balanco.gerar(visao=visao)
            ctx.titulo = ctx_rel.titulo
            html = export.render_html("balanco.html", ctx, wl, grupos=grupos, totais=totais)

        elif tipo == "dre":
            dre = DRE(session, ecd_id)
            ctx_rel, linhas, totais = dre.gerar()
            ctx.titulo = ctx_rel.titulo
            html = export.render_html("dre.html", ctx, wl, linhas=linhas, totais=totais)

        elif tipo == "dfc":
            dfc = DFC(session, ecd_id)
            ctx_rel, linhas, totais = dfc.gerar()
            ctx.titulo = ctx_rel.titulo
            html = export.render_html("dfc.html", ctx, wl, linhas=linhas, totais=totais)

        else:
            return JSONResponse({"status": "erro", "mensagem": "Tipo inválido"}, status_code=400)

        # Gera PDF
        from weasyprint import HTML as WHTML

        pdf_bytes = WHTML(string=html).write_pdf()

        # Registra auditoria
        svc_audit = AuditService(_db_reference())
        svc_audit.registrar(
            acao="relatorio.export",
            recurso=f"PDF: {tipo} ECD #{ecd_id}",
            detalhes={"tipo": tipo, "visao": visao, "formato": "pdf"},
        )

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={tipo}_{ecd_id}.pdf"},
        )

    except Exception as e:
        logger.exception("Erro ao exportar PDF")
        return JSONResponse({"status": "erro", "mensagem": str(e)}, status_code=500)
    finally:
        session.close()


@app.get("/api/export/xlsx")
async def api_export_xlsx(
    ecd_id: int = Query(...),
    tipo: str = Query("balanco"),
    visao: str = Query("hierarquica"),
):
    """Exporta relatório para XLSX."""
    session = get_session(_get_engine())
    try:
        from src.db.models import ECD, Empresa
        from src.reports.base import ReportContext
        from src.reports.export_engine import ExportEngine, WhiteLabel

        ecd = session.get(ECD, ecd_id)
        empresa = session.get(Empresa, ecd.empresa_id) if ecd else None

        wl = WhiteLabel()
        export = ExportEngine()
        ctx = ReportContext(
            titulo="",
            empresa_nome=empresa.nome if empresa else "",
            empresa_cnpj=empresa.cnpj if empresa else "",
            periodo_ref=f"{ecd.dt_ini} a {ecd.dt_fin}" if ecd else "",
        )

        output_path = f"/workspace/outputs/{tipo}_{ecd_id}.xlsx"

        if tipo == "balanco":
            balanco = BalancoPatrimonial(session, ecd_id)
            if visao == "publicacao":
                ctx_rel, grupos, totais = balanco.gerar_publicacao()
            else:
                ctx_rel, grupos, totais = balanco.gerar(visao=visao)
            ctx.titulo = ctx_rel.titulo
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
            colunas = ["secao", "cod_cta", "nome_cta", "saldo_atual"]
            export.export_xlsx(output_path, ctx, linhas_dict, colunas, ctx.titulo, wl)

        elif tipo == "dre":
            dre = DRE(session, ecd_id)
            ctx_rel, linhas, totais = dre.gerar()
            ctx.titulo = ctx_rel.titulo
            linhas_dict = [
                {"tipo": ln.tipo, "descricao": ln.descricao, "valor_atual": ln.valor_atual}
                for ln in linhas
            ]
            colunas = ["tipo", "descricao", "valor_atual"]
            export.export_xlsx(output_path, ctx, linhas_dict, colunas, ctx.titulo, wl)

        elif tipo == "dfc":
            dfc = DFC(session, ecd_id)
            ctx_rel, linhas, totais = dfc.gerar()
            ctx.titulo = ctx_rel.titulo
            linhas_dict = [
                {"tipo": ln.tipo, "descricao": ln.descricao, "valor": ln.valor} for ln in linhas
            ]
            colunas = ["tipo", "descricao", "valor"]
            export.export_xlsx(output_path, ctx, linhas_dict, colunas, ctx.titulo, wl)

        else:
            return JSONResponse({"status": "erro", "mensagem": "Tipo inválido"}, status_code=400)

        # Registra auditoria
        svc_audit = AuditService(_db_reference())
        svc_audit.registrar(
            acao="relatorio.export",
            recurso=f"XLSX: {tipo} ECD #{ecd_id}",
            detalhes={"tipo": tipo, "visao": visao, "formato": "xlsx"},
        )

        return JSONResponse({"status": "ok", "arquivo": f"{tipo}_{ecd_id}.xlsx"})

    except Exception as e:
        logger.exception("Erro ao exportar XLSX")
        return JSONResponse({"status": "erro", "mensagem": str(e)}, status_code=500)
    finally:
        session.close()


# ── Rotas: Filtros ─────────────────────────────────────────────────────────


@app.get("/api/filtros/aplicar", response_class=HTMLResponse)
async def api_filtros_aplicar(
    request: Request,
    ecd_id: int = Query(...),
    natureza: str = Query(""),
    nivel_ate: str = Query(""),
    dt_ini: str = Query(""),
    dt_fin: str = Query(""),
    conta: str = Query(""),
    nome_cta: str = Query(""),
    ocultar_zero: bool = Query(False),
):
    """Aplica filtros e retorna balanço filtrado."""
    session = get_session(_get_engine())
    try:
        criterios = FilterCriteria()
        if natureza:
            criterios.cod_nat = [n.strip() for n in natureza.split(",")]
        if nivel_ate:
            criterios.nivel_ate = int(nivel_ate)
        if dt_ini:
            criterios.dt_ini = datetime.date.fromisoformat(dt_ini)
        if dt_fin:
            criterios.dt_fin = datetime.date.fromisoformat(dt_fin)
        if conta:
            criterios.cod_cta_exato = [conta]
        if nome_cta:
            criterios.nome_cta = nome_cta
        if ocultar_zero:
            criterios.ocultar_saldo_zero = True

        balanco = BalancoPatrimonial(session, ecd_id)
        ctx, grupos, totais = balanco.gerar(criterios=criterios)

        return HTMLResponse(
            jinja_env.get_template("partials/balanco.html").render(
                {
                    "request": request,
                    "ctx": ctx,
                    "grupos": grupos,
                    "totais": totais,
                    "visao": "hierarquica",
                    "ecd_id": ecd_id,
                }
            )
        )
    finally:
        session.close()


# ── Rotas: Fase 9 — Exportação Multi-formato ───────────────────────────────


@app.get("/api/export/multi-formato")
async def api_export_multi_formato(
    ecd_id: int = Query(...),
    formatos: str = Query("pdf,xlsx,csv"),
):
    """Exportacao multi-formato: gera ZIP com PDF, XLSX e CSV para uma ECD."""
    import csv
    import io as io_mod
    import zipfile

    session = get_session(_get_engine())
    try:
        from src.db.models import ECD, Empresa
        from src.reports.base import ReportContext
        from src.reports.export_engine import ExportEngine, WhiteLabel

        ecd = session.get(ECD, ecd_id)
        if not ecd:
            return JSONResponse(
                {"status": "erro", "mensagem": "ECD nao encontrada"}, status_code=404
            )

        empresa = session.get(Empresa, ecd.empresa_id)

        wl = WhiteLabel()
        export = ExportEngine()
        ctx = ReportContext(
            titulo="",
            empresa_nome=empresa.nome if empresa else "",
            empresa_cnpj=empresa.cnpj if empresa else "",
            periodo_ref=f"{ecd.dt_ini} a {ecd.dt_fin}",
        )

        formatos_list = [f.strip().lower() for f in formatos.split(",")]
        zip_buffer = io_mod.BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for tipo in ["balanco", "dre", "dfc"]:
                if tipo == "balanco":
                    balanco = BalancoPatrimonial(session, ecd_id)
                    ctx_rel, grupos, totais = balanco.gerar()
                    ctx.titulo = ctx_rel.titulo
                elif tipo == "dre":
                    dre = DRE(session, ecd_id)
                    ctx_rel, linhas, totais = dre.gerar()
                    ctx.titulo = ctx_rel.titulo
                elif tipo == "dfc":
                    dfc = DFC(session, ecd_id)
                    ctx_rel, linhas, totais = dfc.gerar()
                    ctx.titulo = ctx_rel.titulo

                if "pdf" in formatos_list:
                    if tipo == "balanco":
                        html = export.render_html(
                            "balanco.html", ctx, wl, grupos=grupos, totais=totais
                        )
                    elif tipo == "dre":
                        html = export.render_html("dre.html", ctx, wl, linhas=linhas, totais=totais)
                    elif tipo == "dfc":
                        html = export.render_html("dfc.html", ctx, wl, linhas=linhas, totais=totais)
                    from weasyprint import HTML as WHTML

                    pdf_bytes = WHTML(string=html).write_pdf()
                    zf.writestr(f"{tipo}_{ecd_id}.pdf", pdf_bytes)

                if "xlsx" in formatos_list:
                    xlsx_buffer = io_mod.BytesIO()
                    if tipo == "balanco":
                        linhas_dict = []
                        for secao, nome in [
                            ("ativo", "Ativo"),
                            ("passivo", "Passivo"),
                            ("pl", "PL"),
                        ]:
                            for ln in grupos[secao]:
                                linhas_dict.append(
                                    {
                                        "secao": nome,
                                        "cod_cta": ln.cod_cta,
                                        "nome_cta": ln.nome_cta,
                                        "saldo_atual": ln.saldo_atual,
                                    }
                                )
                        colunas = ["secao", "cod_cta", "nome_cta", "saldo_atual"]
                    elif tipo == "dre":
                        linhas_dict = [
                            {
                                "tipo": ln.tipo,
                                "descricao": ln.descricao,
                                "valor_atual": ln.valor_atual,
                            }
                            for ln in linhas
                        ]
                        colunas = ["tipo", "descricao", "valor_atual"]
                    elif tipo == "dfc":
                        linhas_dict = [
                            {"tipo": ln.tipo, "descricao": ln.descricao, "valor": ln.valor}
                            for ln in linhas
                        ]
                        colunas = ["tipo", "descricao", "valor"]
                    export.export_xlsx_to_buffer(
                        xlsx_buffer, ctx, linhas_dict, colunas, ctx.titulo, wl
                    )
                    zf.writestr(f"{tipo}_{ecd_id}.xlsx", xlsx_buffer.getvalue())

                if "csv" in formatos_list:
                    csv_buffer = io_mod.StringIO()
                    if tipo == "balanco":
                        writer = csv.writer(csv_buffer)
                        writer.writerow(["secao", "cod_cta", "nome_cta", "saldo_atual"])
                        for secao, nome in [
                            ("ativo", "Ativo"),
                            ("passivo", "Passivo"),
                            ("pl", "PL"),
                        ]:
                            for ln in grupos[secao]:
                                writer.writerow([nome, ln.cod_cta, ln.nome_cta, ln.saldo_atual])
                    elif tipo == "dre":
                        writer = csv.writer(csv_buffer)
                        writer.writerow(["tipo", "descricao", "valor_atual"])
                        for ln in linhas:
                            writer.writerow([ln.tipo, ln.descricao, ln.valor_atual])
                    elif tipo == "dfc":
                        writer = csv.writer(csv_buffer)
                        writer.writerow(["tipo", "descricao", "valor"])
                        for ln in linhas:
                            writer.writerow([ln.tipo, ln.descricao, ln.valor])
                    zf.writestr(f"{tipo}_{ecd_id}.csv", csv_buffer.getvalue().encode("utf-8-sig"))

        zip_buffer.seek(0)

        return Response(
            content=zip_buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=sped_hub_export_{ecd_id}.zip"},
        )

    except Exception as e:
        logger.exception("Erro ao exportar multi-formato")
        return JSONResponse({"status": "erro", "mensagem": str(e)}, status_code=500)
    finally:
        session.close()


# ── Rotas: Fase 6 ──────────────────────────────────────────────────────────


@app.get("/api/evolucao-multi")
async def api_evolucao_multi(ecd_id: int = Query(...)):
    """Evolução patrimonial multi-período (Fase 6)."""
    session = get_session(_get_engine())
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


@app.get("/api/notas", response_class=HTMLResponse)
async def api_notas(request: Request, ecd_id: int = Query(...)):
    """Notas explicativas automáticas (Fase 6)."""
    session = get_session(_get_engine())
    try:
        svc = DashboardService(session, ecd_id)
        notas = svc.get_notas_explicativas()
        return HTMLResponse(
            jinja_env.get_template("partials/notas.html").render(
                {
                    "request": request,
                    "notas": notas,
                    "ecd_id": ecd_id,
                }
            )
        )
    finally:
        session.close()


@app.get("/api/export/lote")
async def api_export_lote(
    ecd_ids: str = Query(...),
    tipo: str = Query("balanco"),
):
    """Exportação de lote: múltiplas ECDs em ZIP (Fase 6)."""
    import zipfile

    session = get_session(_get_engine())
    try:
        from src.db.models import ECD, Empresa
        from src.reports.base import ReportContext
        from src.reports.export_engine import ExportEngine, WhiteLabel

        ids = [int(x.strip()) for x in ecd_ids.split(",") if x.strip().isdigit()]
        if not ids:
            return JSONResponse(
                {"status": "erro", "mensagem": "Nenhum ecd_id válido"}, status_code=400
            )
        if len(ids) > 10:
            return JSONResponse(
                {"status": "erro", "mensagem": "Máximo de 10 ECDs por lote"}, status_code=400
            )

        wl = WhiteLabel()
        export = ExportEngine()
        buf = io.BytesIO()

        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for ecd_id in ids:
                ecd = session.get(ECD, ecd_id)
                if not ecd:
                    continue
                empresa = session.get(Empresa, ecd.empresa_id)
                ctx = ReportContext(
                    titulo="",
                    empresa_nome=empresa.nome if empresa else "",
                    empresa_cnpj=empresa.cnpj if empresa else "",
                    periodo_ref=f"{ecd.dt_ini} a {ecd.dt_fin}" if ecd else "",
                )

                if tipo == "balanco":
                    balanco = BalancoPatrimonial(session, ecd_id)
                    ctx_rel, grupos, totais = balanco.gerar()
                    ctx.titulo = ctx_rel.titulo
                    html = export.render_html("balanco.html", ctx, wl, grupos=grupos, totais=totais)
                elif tipo == "dre":
                    dre = DRE(session, ecd_id)
                    ctx_rel, linhas, totais = dre.gerar()
                    ctx.titulo = ctx_rel.titulo
                    html = export.render_html("dre.html", ctx, wl, linhas=linhas, totais=totais)
                elif tipo == "dfc":
                    dfc = DFC(session, ecd_id)
                    ctx_rel, linhas, totais = dfc.gerar()
                    ctx.titulo = ctx_rel.titulo
                    html = export.render_html("dfc.html", ctx, wl, linhas=linhas, totais=totais)
                else:
                    continue

                from weasyprint import HTML as WHTML

                pdf_bytes = WHTML(string=html).write_pdf()
                nome_arquivo = f"{tipo}_{ecd_id}_{empresa.nome[:20] if empresa else 'NI'}.pdf"
                zf.writestr(nome_arquivo, pdf_bytes)

        buf.seek(0)
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=lote_{tipo}.zip"},
        )

    except Exception as e:
        logger.exception("Erro ao exportar lote")
        return JSONResponse({"status": "erro", "mensagem": str(e)}, status_code=500)
    finally:
        session.close()


# ── Rotas: Fase 7 ──────────────────────────────────────────────────────────


@app.get("/api/multi-ecd")
async def api_multi_ecd(ecd_ids: str = Query(...)):
    """Comparação lado a lado de múltiplas ECDs (Fase 7)."""
    session = get_session(_get_engine())
    try:
        ids = [int(x.strip()) for x in ecd_ids.split(",") if x.strip().isdigit()]
        if len(ids) < 2:
            return JSONResponse(
                {"status": "erro", "mensagem": "Mínimo de 2 ECDs para comparação"}, status_code=400
            )
        if len(ids) > 5:
            return JSONResponse(
                {"status": "erro", "mensagem": "Máximo de 5 ECDs para comparação"}, status_code=400
            )

        svc = DashboardService(session, ids[0])
        data = svc.get_multi_ecd_comparison(ids)
        return data or {"status": "erro", "mensagem": "Dados insuficientes"}
    finally:
        session.close()


@app.get("/api/layout")
async def api_layout(ecd_id: int = Query(...), relatorio: str = Query("balanco")):
    """Configuração de layout customizável para relatórios (Fase 7)."""
    session = get_session(_get_engine())
    try:
        svc = DashboardService(session, ecd_id)
        return svc.get_layout_customizavel(ecd_id, relatorio)
    finally:
        session.close()


# ── Entry point ─────────────────────────────────────────────────────────────


# ── Rota: Dashboard de Webhooks (Fase 11) ──────────────────────────────────


@app.get("/webhooks", response_class=HTMLResponse)
async def webhooks_page(request: Request):
    """Dashboard de monitoramento de webhooks."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)
    if not usuario.admin:
        return _monitoring_forbidden()

    return HTMLResponse(
        jinja_env.get_template("webhooks.html").render(
            {
                "request": request,
                "usuario": usuario,
                "current_page": "webhooks",
            }
        )
    )


# ── Rotas: Fase 10 — Comparar Multi-ECD ─────────────────────────────────


@app.get("/comparar", response_class=HTMLResponse)
async def comparar_page(request: Request):
    """Página de comparação multi-ECD."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)
    return HTMLResponse(
        jinja_env.get_template("comparar.html").render(
            {
                "request": request,
                "usuario": usuario,
                "current_page": "comparar",
            }
        )
    )


@app.get("/layout", response_class=HTMLResponse)
async def layout_page(request: Request):
    """Página de layout customizável."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)
    return HTMLResponse(
        jinja_env.get_template("layout.html").render(
            {
                "request": request,
                "usuario": usuario,
                "current_page": "layout",
            }
        )
    )


@app.get("/api/comparar")
async def api_comparar(ecd_ids: str = Query(...)):
    """Comparação multi-ECD — retorna dados de Balanço, DRE e DFC."""
    ids = [int(x.strip()) for x in ecd_ids.split(",") if x.strip()]
    if len(ids) < 2:
        return JSONResponse(
            {"status": "erro", "mensagem": "Selecione pelo menos 2 ECDs"}, status_code=400
        )
    ids = ids[:5]

    session = get_session(_get_engine())
    try:
        svc = DashboardService(session, ids[0])
        dados = svc.get_multi_ecd_comparison(ids)
        if not dados:
            return JSONResponse(
                {"status": "erro", "mensagem": "Dados insuficientes"}, status_code=404
            )
        return dados
    finally:
        session.close()


# ── Rotas: Fase 12 — API Keys UI ────────────────────────────────────────────


@app.get("/api-keys", response_class=HTMLResponse)
async def api_keys_page(request: Request):
    """Página de gerenciamento de API Keys."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)
    if not usuario.admin:
        return _monitoring_forbidden()

    return HTMLResponse(
        jinja_env.get_template("api_keys.html").render(
            {
                "request": request,
                "usuario": usuario,
                "current_page": "api_keys",
            }
        )
    )


# ── Rotas: cadastro fiscal da empresa ──────────────────────────────────────


def _empresa_do_usuario(session, usuario, empresa_id: int) -> Empresa | None:
    """A empresa, **se** ela for do escritório do usuário.

    O escopo é aplicado na consulta, e não conferido depois de carregar: um
    `session.get` seguido de `if empresa.escritorio_id != ...` é a mesma
    proteção até alguém acrescentar um caminho que esquece o `if`. Empresa de
    outro escritório devolve `None` — igual a empresa inexistente, para não
    contar a quem tentou que ela existe.
    """
    consulta = aplicar_escopo_empresas(select(Empresa).where(Empresa.id == empresa_id), usuario)
    return session.execute(consulta).scalar_one_or_none()


def _pagina_cadastro(session, usuario, request, *, empresa=None, erro=None, salvo=False):
    empresas = (
        session.execute(aplicar_escopo_empresas(select(Empresa), usuario).order_by(Empresa.nome))
        .scalars()
        .all()
    )
    return HTMLResponse(
        jinja_env.get_template("cadastro_fiscal.html").render(
            {
                "request": request,
                "usuario": usuario,
                "current_page": "cadastro_fiscal",
                "empresas": empresas,
                "empresa": empresa,
                "campos": campos(empresa) if empresa else [],
                "pendencias": pendencias(empresa) if empresa else {},
                "nomes_das_obrigacoes": TIPOS,
                "erro": erro,
                "salvo": salvo,
            }
        ),
        status_code=400 if erro else 200,
    )


@app.get("/fiscal/cadastro", response_class=HTMLResponse)
async def cadastro_fiscal_page(request: Request, empresa: int | None = Query(default=None)):
    """O cadastro fiscal — os cinco códigos sem os quais não se gera EFD.

    Existe porque até aqui esses campos só podiam ser preenchidos pela linha de
    comando ou escrevendo no banco, o que deixava a geração de EFD fora do
    alcance de quem não é programador — que é justamente quem escritura.
    """
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    session = get_session(_get_engine())
    try:
        alvo = _empresa_do_usuario(session, usuario, empresa) if empresa else None
        if empresa and alvo is None:
            return _pagina_cadastro(
                session, usuario, request, erro=f"Empresa #{empresa} não encontrada."
            )
        return _pagina_cadastro(session, usuario, request, empresa=alvo)
    finally:
        session.close()


@app.post("/fiscal/cadastro", response_class=HTMLResponse)
async def cadastro_fiscal_salvar(request: Request):
    """Grava o cadastro, recusando valor fora da tabela oficial.

    A validação é a mesma de `sped-hub fiscal cadastro` — vem do domínio, não
    daqui. Uma segunda cópia da tabela na tela divergiria da primeira no
    primeiro ato normativo, e a tela é onde ninguém iria conferir.
    """
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    formulario = await request.form()
    try:
        empresa_id = int(formulario.get("empresa") or "")
    except ValueError:
        return HTMLResponse("Empresa não informada", status_code=400)

    session = get_session(_get_engine())
    try:
        alvo = _empresa_do_usuario(session, usuario, empresa_id)
        if alvo is None:
            return _pagina_cadastro(
                session, usuario, request, erro=f"Empresa #{empresa_id} não encontrada."
            )

        # Campo em branco é "não mexer", não "apagar": o formulário traz todos
        # os cinco sempre, e um branco significaria zerar o que já estava lá
        # só porque a pessoa veio corrigir outro campo.
        informados = {
            campo: (formulario.get(campo) or "").strip()
            for campo in CADASTRO_FISCAL
            if (formulario.get(campo) or "").strip()
        }
        try:
            preencher(alvo, informados)
        except CadastroInvalido as erro:
            session.rollback()
            alvo = _empresa_do_usuario(session, usuario, empresa_id)
            return _pagina_cadastro(session, usuario, request, empresa=alvo, erro=str(erro))

        session.commit()
        return _pagina_cadastro(session, usuario, request, empresa=alvo, salvo=bool(informados))
    finally:
        session.close()


# ── Rotas: Central de Documentos ───────────────────────────────────────────


def _documento_do_usuario(session, usuario, documento_id: int) -> DocumentoFiscal | None:
    """O documento, **se** a empresa dele for do escritório do usuário.

    Como no cadastro, o escopo entra na consulta e não numa conferência
    posterior. Documento de outro escritório devolve `None` — igual a
    inexistente, para não contar a quem tentou que ele existe.
    """
    consulta = aplicar_escopo_empresas(
        select(DocumentoFiscal)
        .join(Empresa, DocumentoFiscal.empresa_id == Empresa.id)
        .options(selectinload(DocumentoFiscal.itens))
        .where(DocumentoFiscal.id == documento_id),
        usuario,
    )
    return session.execute(consulta).scalar_one_or_none()


def _data_opcional(bruto: str | None):
    """`AAAA-MM-DD` vindo do formulário, ou `None`.

    Data ilegível vira `None` — o filtro simplesmente não se aplica. Recusar a
    página inteira por causa de um campo de data meio digitado seria trocar um
    filtro que não pegou por uma tela que não abre.
    """
    if not bruto:
        return None
    try:
        return datetime.date.fromisoformat(bruto)
    except ValueError:
        return None


@app.get("/fiscal/documentos", response_class=HTMLResponse)
async def documentos_page(
    request: Request,
    empresa: int | None = Query(default=None),
    de: str | None = Query(default=None),
    ate: str | None = Query(default=None),
):
    """A Central de Documentos: o que foi importado, no recorte pedido."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    session = get_session(_get_engine())
    try:
        empresas = (
            session.execute(
                aplicar_escopo_empresas(select(Empresa), usuario).order_by(Empresa.nome)
            )
            .scalars()
            .all()
        )
        alvo = _empresa_do_usuario(session, usuario, empresa) if empresa else None
        documentos: list[DocumentoFiscal] = []
        com_ajuste: set[int] = set()
        if alvo is not None:
            consulta = select(DocumentoFiscal).where(DocumentoFiscal.empresa_id == alvo.id)
            inicio, fim = _data_opcional(de), _data_opcional(ate)
            if inicio:
                consulta = consulta.where(DocumentoFiscal.data_emissao >= inicio)
            if fim:
                consulta = consulta.where(DocumentoFiscal.data_emissao <= fim)
            documentos = list(
                session.execute(consulta.order_by(DocumentoFiscal.data_emissao, DocumentoFiscal.id))
                .scalars()
                .all()
            )
            # Quais foram corrigidos — numa consulta só, e não uma por linha:
            # mil notas dariam mil consultas para pintar uma coluna.
            if documentos:
                com_ajuste = set(
                    session.execute(
                        select(AjusteFiscal.documento_id).where(
                            AjusteFiscal.documento_id.in_([d.id for d in documentos])
                        )
                    )
                    .scalars()
                    .all()
                )

        return HTMLResponse(
            jinja_env.get_template("documentos.html").render(
                {
                    "request": request,
                    "usuario": usuario,
                    "current_page": "documentos",
                    "empresas": empresas,
                    "empresa": alvo,
                    "empresa_pedida": empresa,
                    "de": de or "",
                    "ate": ate or "",
                    "documentos": documentos,
                    "com_ajuste": com_ajuste,
                    "total": sum(d.valor_total for d in documentos),
                    "fmt_moeda": fmt_moeda,
                    "fmt_data": fmt_data,
                }
            )
        )
    finally:
        session.close()


@app.get("/fiscal/documentos/{documento_id}", response_class=HTMLResponse)
async def documento_page(request: Request, documento_id: int):
    """As três camadas de um documento, lado a lado.

    É a tela que responde "por que este registro saiu assim?": o que chegou, o
    que o sistema fez, e em que arquivo isso foi parar. Separá-las é o ponto
    de toda a Central — mostrar só o valor final faria a tela desmentir o
    modelo de dados.
    """
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    session = get_session(_get_engine())
    try:
        documento = _documento_do_usuario(session, usuario, documento_id)
        if documento is None:
            return HTMLResponse("Documento não encontrado", status_code=404)

        visao = efetivo(session, documento)
        return HTMLResponse(
            jinja_env.get_template("documento.html").render(
                {
                    "request": request,
                    "usuario": usuario,
                    "current_page": "documentos",
                    "documento": documento,
                    "visao": visao,
                    "cabecalho": _linhas_de_revisao(
                        documento, visao.valores, visao.campos_alterados
                    ),
                    "itens": [
                        {
                            "item": item,
                            "numero": valores.get("numero_item"),
                            "linhas": _linhas_de_revisao(
                                item, valores, visao.itens_alterados.get(item.id, set())
                            ),
                            "alterados": visao.itens_alterados.get(item.id, set()),
                        }
                        for item, valores in zip(documento.itens, visao.itens, strict=False)
                    ],
                    "ajustes": historico(session, documento),
                    "escrituracoes": escrituracoes_do_documento(session, documento),
                    "fmt_moeda": fmt_moeda,
                    "fmt_data": fmt_data,
                }
            )
        )
    finally:
        session.close()


# Os campos que alguém revisa numa nota.  São os mesmos que a planilha leva e
# traz — uma segunda lista divergiria da primeira, e as duas dizem a mesma
# coisa: "isto é o que se confere numa nota".
CABECALHO_REVISADO = (
    "chave",
    "modelo",
    "serie",
    "numero",
    "data_emissao",
    "data_entrada_saida",
    "sentido",
    "situacao",
    "natureza_operacao",
    "modalidade_frete",
    "valor_produtos",
    "valor_desconto",
    "valor_frete",
    "base_icms",
    "valor_icms",
    "valor_ipi",
    "valor_pis",
    "valor_cofins",
    "valor_total",
)


def _linhas_de_revisao(alvo, valores: dict, alterados: set[str]) -> list[dict]:
    """Campo a campo: o que foi normalizado e o que vale hoje.

    Os campos alterados entram **sempre**, mesmo fora da lista de revisão. Um
    ajuste que a tela não mostrasse seria uma correção invisível — e a tela
    existe justamente para que nenhuma o seja.
    """
    base = CABECALHO_REVISADO if isinstance(alvo, DocumentoFiscal) else EDITAVEIS
    nomes = list(base) + [c for c in sorted(alterados) if c not in base]
    linhas = []
    for nome in nomes:
        if nome not in type(alvo).__table__.columns:
            continue
        linhas.append(
            {
                "campo": nome,
                "normalizado": getattr(alvo, nome),
                "efetivo": valores.get(nome),
                "alterado": nome in alterados,
            }
        )
    return linhas


@app.get("/fiscal/documentos/{documento_id}/xml")
async def documento_xml(request: Request, documento_id: int):
    """O XML como ele chegou — a primeira camada, byte a byte.

    Devolvido do que foi guardado, nunca remontado a partir das colunas: a
    camada original só vale enquanto for o que o emitente assinou.
    """
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    session = get_session(_get_engine())
    try:
        documento = _documento_do_usuario(session, usuario, documento_id)
        if documento is None or not documento.xml_original:
            return HTMLResponse("Documento não encontrado", status_code=404)
        return Response(
            content=documento.xml_original,
            media_type="application/xml",
            headers={
                "Content-Disposition": f'attachment; filename="{documento.chave or documento.id}.xml"'
            },
        )
    finally:
        session.close()


# ── Rotas: classificar e corrigir ──────────────────────────────────────────
#
# As duas telas têm a mesma forma, e ela não é acidental: **mostrar, e só
# depois gravar**. É a mesma garantia que os motores dão por dentro e que a
# linha de comando dá com `--aplicar`/`--confirmar`; invertê-la na tela — que
# é por onde a maioria vai passar — desfaria a proteção justamente na porta
# mais usada.
#
# Sobre a segunda etapa: o POST **re-simula** a partir dos mesmos parâmetros e
# confere o total contra o que a tela mostrou. Não é preciosismo. Entre ver e
# confirmar cabe uma importação, outra pessoa corrigindo, uma regra nova; sem
# a conferência, alguém aprovaria trinta mudanças e gravaria trezentas.


def _selecao_do_recorte(empresa, de, ate, filtros=()):
    return Selecao(
        empresa_id=empresa.id,
        escritorio_id=empresa.escritorio_id,
        data_inicio=_data_opcional(de),
        data_fim=_data_opcional(ate),
        filtros=list(filtros),
    )


def _documentos_da_selecao(session, empresa, de, ate):
    consulta = (
        select(DocumentoFiscal)
        .options(selectinload(DocumentoFiscal.itens))
        .where(DocumentoFiscal.empresa_id == empresa.id)
    )
    inicio, fim = _data_opcional(de), _data_opcional(ate)
    if inicio:
        consulta = consulta.where(DocumentoFiscal.data_emissao >= inicio)
    if fim:
        consulta = consulta.where(DocumentoFiscal.data_emissao <= fim)
    return list(
        session.execute(consulta.order_by(DocumentoFiscal.data_emissao, DocumentoFiscal.id))
        .scalars()
        .unique()
        .all()
    )


def _classificacao(session, empresa, de, ate, obrigacao):
    """O que as regras propõem para o recorte — sem tocar no banco."""
    motor = MotorDeClassificacao(session, obrigacao=obrigacao or None)
    propostas = []
    for documento in _documentos_da_selecao(session, empresa, de, ate):
        resultado = motor.avaliar(documento)
        if resultado.sugestoes or resultado.conflitos:
            propostas.append({"documento": documento, "resultado": resultado})
    return propostas


def _pagina_classificar(session, usuario, request, **contexto):
    empresas = (
        session.execute(aplicar_escopo_empresas(select(Empresa), usuario).order_by(Empresa.nome))
        .scalars()
        .all()
    )
    dados = {
        "request": request,
        "usuario": usuario,
        "current_page": "classificar",
        "empresas": empresas,
        "empresa": None,
        "propostas": [],
        "total_sugestoes": 0,
        "total_conflitos": 0,
        "erro": None,
        "lote": None,
        "aplicadas": 0,
        "de": "",
        "ate": "",
        "obrigacao": "",
        "fmt_moeda": fmt_moeda,
    }
    dados.update(contexto)
    return HTMLResponse(
        jinja_env.get_template("classificar.html").render(dados),
        status_code=400 if dados["erro"] else 200,
    )


@app.get("/fiscal/classificar", response_class=HTMLResponse)
async def classificar_page(
    request: Request,
    empresa: int | None = Query(default=None),
    de: str | None = Query(default=None),
    ate: str | None = Query(default=None),
    obrigacao: str | None = Query(default=None),
):
    """O que as regras propõem. **Não grava** — propor não é decidir."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    session = get_session(_get_engine())
    try:
        alvo = _empresa_do_usuario(session, usuario, empresa) if empresa else None
        if empresa and alvo is None:
            return _pagina_classificar(
                session, usuario, request, erro=f"Empresa #{empresa} não encontrada."
            )
        propostas = _classificacao(session, alvo, de, ate, obrigacao) if alvo else []
        return _pagina_classificar(
            session,
            usuario,
            request,
            empresa=alvo,
            propostas=propostas,
            total_sugestoes=sum(len(p["resultado"].sugestoes) for p in propostas),
            total_conflitos=sum(len(p["resultado"].conflitos) for p in propostas),
            de=de or "",
            ate=ate or "",
            obrigacao=obrigacao or "",
        )
    finally:
        session.close()


@app.post("/fiscal/classificar", response_class=HTMLResponse)
async def classificar_aplicar(request: Request):
    """Aplica as sugestões, num lote reversível.

    As sugestões viram ajustes de origem `regra`, e não `usuario`: o histórico
    tem de distinguir o que uma regra propôs do que uma pessoa decidiu, senão
    "por que este campo está assim?" perde a única resposta que ele tem.
    """
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    formulario = await request.form()
    de = (formulario.get("de") or "").strip()
    ate = (formulario.get("ate") or "").strip()
    obrigacao = (formulario.get("obrigacao") or "").strip()

    session = get_session(_get_engine())
    try:
        try:
            empresa_id = int(formulario.get("empresa") or "")
        except ValueError:
            return _pagina_classificar(session, usuario, request, erro="Empresa não informada.")

        alvo = _empresa_do_usuario(session, usuario, empresa_id)
        if alvo is None:
            return _pagina_classificar(
                session, usuario, request, erro=f"Empresa #{empresa_id} não encontrada."
            )

        propostas = _classificacao(session, alvo, de, ate, obrigacao)
        total = sum(len(p["resultado"].sugestoes) for p in propostas)
        divergencia = _confere_o_que_foi_visto(formulario.get("esperado"), total)
        if divergencia:
            return _pagina_classificar(
                session,
                usuario,
                request,
                empresa=alvo,
                propostas=propostas,
                total_sugestoes=total,
                total_conflitos=sum(len(p["resultado"].conflitos) for p in propostas),
                de=de,
                ate=ate,
                obrigacao=obrigacao,
                erro=divergencia,
            )

        lote = novo_lote()
        for proposta in propostas:
            aplicar_classificacao(
                session,
                proposta["documento"],
                proposta["resultado"].sugestoes,
                lote=lote,
                usuario_id=usuario.id,
            )
        session.commit()
        return _pagina_classificar(
            session,
            usuario,
            request,
            empresa=alvo,
            propostas=_classificacao(session, alvo, de, ate, obrigacao),
            de=de,
            ate=ate,
            obrigacao=obrigacao,
            lote=lote,
            aplicadas=total,
        )
    finally:
        session.close()


def _confere_o_que_foi_visto(esperado: str | None, total: int) -> str | None:
    """A tela mostrou N; o banco agora tem M. Se diferem, ninguém grava.

    Entre ver e confirmar cabe uma importação, outra pessoa corrigindo, uma
    regra nova. Sem esta conferência, alguém aprovaria trinta mudanças e
    gravaria trezentas — e o lote que desfaz não ajuda quem não percebeu.
    """
    try:
        visto = int(esperado or "")
    except ValueError:
        return "Não deu para saber o que você viu na tela; confira e envie de novo."
    if visto != total:
        return (
            f"A tela mostrava {visto} e agora há {total}. Nada foi gravado — "
            "alguma coisa mudou no meio do caminho. Confira de novo."
        )
    return None


def _pagina_corrigir(session, usuario, request, **contexto):
    empresas = (
        session.execute(aplicar_escopo_empresas(select(Empresa), usuario).order_by(Empresa.nome))
        .scalars()
        .all()
    )
    dados = {
        "request": request,
        "usuario": usuario,
        "current_page": "corrigir",
        "empresas": empresas,
        "empresa": None,
        "simulacao": None,
        "erro": None,
        "lote": None,
        "gravadas": 0,
        "desfeitos": None,
        "de": "",
        "ate": "",
        "campo": "",
        "valor": "",
        "motivo": "",
        "apenas_vazios": False,
        "editaveis": EDITAVEIS,
        "fmt_moeda": fmt_moeda,
    }
    dados.update(contexto)
    return HTMLResponse(
        jinja_env.get_template("corrigir.html").render(dados),
        status_code=400 if dados["erro"] else 200,
    )


def _simular_do_formulario(session, alvo, dados):
    """A simulação do recorte + alteração pedidos, ou `None` se falta algo."""
    if not dados["campo"] or not dados["valor"]:
        return None
    selecao = _selecao_do_recorte(alvo, dados["de"], dados["ate"])
    alteracao = Alteracao(
        campo=dados["campo"],
        valor=valor_tipado(dados["campo"], dados["valor"]),
        apenas_vazios=dados["apenas_vazios"],
    )
    return simular(session, selecao, [alteracao])


@app.get("/fiscal/corrigir", response_class=HTMLResponse)
async def corrigir_page(
    request: Request,
    empresa: int | None = Query(default=None),
    de: str | None = Query(default=None),
    ate: str | None = Query(default=None),
    campo: str | None = Query(default=None),
    valor: str | None = Query(default=None),
    apenas_vazios: bool = Query(default=False),
):
    """A alteração em massa — **simulada**. Gravar exige o segundo passo."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    dados = {
        "de": de or "",
        "ate": ate or "",
        "campo": campo or "",
        "valor": valor or "",
        "apenas_vazios": apenas_vazios,
    }
    session = get_session(_get_engine())
    try:
        alvo = _empresa_do_usuario(session, usuario, empresa) if empresa else None
        if empresa and alvo is None:
            return _pagina_corrigir(
                session, usuario, request, erro=f"Empresa #{empresa} não encontrada.", **dados
            )
        simulacao = None
        if alvo is not None:
            try:
                simulacao = _simular_do_formulario(session, alvo, dados)
            except (AlteracaoInvalida, SelecaoVazia, ValueError) as erro:
                return _pagina_corrigir(
                    session, usuario, request, empresa=alvo, erro=str(erro), **dados
                )
        return _pagina_corrigir(
            session, usuario, request, empresa=alvo, simulacao=simulacao, **dados
        )
    finally:
        session.close()


@app.post("/fiscal/corrigir", response_class=HTMLResponse)
async def corrigir_confirmar(request: Request):
    """Grava o que a simulação mostrou, num lote reversível."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    formulario = await request.form()
    dados = {
        "de": (formulario.get("de") or "").strip(),
        "ate": (formulario.get("ate") or "").strip(),
        "campo": (formulario.get("campo") or "").strip(),
        "valor": (formulario.get("valor") or "").strip(),
        "motivo": (formulario.get("motivo") or "").strip(),
        "apenas_vazios": bool(formulario.get("apenas_vazios")),
    }

    session = get_session(_get_engine())
    try:
        try:
            empresa_id = int(formulario.get("empresa") or "")
        except ValueError:
            return _pagina_corrigir(
                session, usuario, request, erro="Empresa não informada.", **dados
            )
        alvo = _empresa_do_usuario(session, usuario, empresa_id)
        if alvo is None:
            return _pagina_corrigir(
                session, usuario, request, erro=f"Empresa #{empresa_id} não encontrada.", **dados
            )

        try:
            simulacao = _simular_do_formulario(session, alvo, dados)
        except (AlteracaoInvalida, SelecaoVazia, ValueError) as erro:
            return _pagina_corrigir(
                session, usuario, request, empresa=alvo, erro=str(erro), **dados
            )
        if simulacao is None:
            return _pagina_corrigir(
                session, usuario, request, empresa=alvo, erro="Informe o campo e o valor.", **dados
            )

        divergencia = _confere_o_que_foi_visto(formulario.get("esperado"), simulacao.total_mudancas)
        if divergencia:
            return _pagina_corrigir(
                session,
                usuario,
                request,
                empresa=alvo,
                simulacao=simulacao,
                erro=divergencia,
                **dados,
            )

        try:
            lote = confirmar(
                session,
                simulacao,
                motivo=dados["motivo"] or None,
                usuario_id=usuario.id,
                forcar=bool(formulario.get("forcar")),
            )
        except AlteracaoInvalida as erro:
            return _pagina_corrigir(
                session,
                usuario,
                request,
                empresa=alvo,
                simulacao=simulacao,
                erro=str(erro),
                **dados,
            )
        session.commit()
        return _pagina_corrigir(
            session,
            usuario,
            request,
            empresa=alvo,
            lote=lote,
            gravadas=simulacao.total_mudancas,
            **dados,
        )
    finally:
        session.close()


@app.post("/fiscal/desfazer", response_class=HTMLResponse)
async def corrigir_desfazer(request: Request):
    """Desfaz um lote inteiro — se ele for todo do escritório de quem pede.

    `desfazer_lote` não conhece escritório, e do jeito que a linha de comando o
    usa isso está certo: lá quem roda o comando já tem o banco na mão. Aqui
    não. O lote é uma string opaca, mas "opaca" não é controle de acesso.

    A recusa é para o lote **inteiro**, e não "apago só a parte que é minha":
    desfazer metade de um lote deixaria a escrituração num estado que ninguém
    escolheu, e que nem quem pediu nem o dono do resto saberiam explicar.
    """
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    formulario = await request.form()
    lote = (formulario.get("lote") or "").strip()

    session = get_session(_get_engine())
    try:
        if not lote:
            return _pagina_corrigir(session, usuario, request, erro="Informe o lote.")

        do_lote = set(
            session.execute(select(AjusteFiscal.documento_id).where(AjusteFiscal.lote == lote))
            .scalars()
            .all()
        )
        if not do_lote:
            return _pagina_corrigir(session, usuario, request, erro=f"Lote {lote} não encontrado.")

        alcancaveis = set(
            session.execute(
                aplicar_escopo_empresas(
                    select(DocumentoFiscal.id).join(
                        Empresa, DocumentoFiscal.empresa_id == Empresa.id
                    ),
                    usuario,
                ).where(DocumentoFiscal.id.in_(do_lote))
            )
            .scalars()
            .all()
        )
        if do_lote - alcancaveis:
            # A mesma frase de lote inexistente: dizer "esse lote é de outro
            # escritório" já confirmaria que ele existe.
            return _pagina_corrigir(session, usuario, request, erro=f"Lote {lote} não encontrado.")

        quantos = desfazer_lote(session, lote)
        session.commit()
        return _pagina_corrigir(session, usuario, request, desfeitos=quantos, lote=lote)
    finally:
        session.close()


# ── Rotas: importar XML ────────────────────────────────────────────────────

# Um XML de NF-e é pequeno; um lote de mil, não.  O limite é o mesmo dos
# outros uploads e vale por arquivo — quem manda a pasta inteira do mês recebe
# a recusa do arquivo grande, e não a do lote.
EXTENSOES_XML = (".xml",)


def _pagina_importar(session, usuario, request, **contexto):
    dados = {
        "request": request,
        "usuario": usuario,
        "current_page": "importar",
        "resultado": None,
        "erro": None,
        "politica": PoliticaDeDuplicidade.IGNORAR.value,
        "limite_mb": max_upload_bytes() // (1024 * 1024),
    }
    dados.update(contexto)
    return HTMLResponse(
        jinja_env.get_template("importar.html").render(dados),
        status_code=400 if dados["erro"] else 200,
    )


@app.get("/fiscal/importar", response_class=HTMLResponse)
async def importar_page(request: Request):
    """A porta de entrada da Central: o XML como o portal o entrega."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    session = get_session(_get_engine())
    try:
        return _pagina_importar(session, usuario, request)
    finally:
        session.close()


@app.post("/fiscal/importar", response_class=HTMLResponse)
async def importar_xml(request: Request, arquivos: list[UploadFile] = File(default=[])):
    """Importa os XML enviados, no escritório de quem está logado.

    **O escritório vem do usuário, nunca do formulário.** É ele que decide de
    quem é o documento importado, e aceitá-lo de um campo escondido deixaria
    qualquer um plantar nota no acervo alheio — sem precisar sequer enxergar o
    outro escritório.
    """
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    if usuario.escritorio_id is None:
        return _pagina_importar(
            session=None,
            usuario=usuario,
            request=request,
            erro=(
                "Seu usuário não está ligado a nenhum escritório, e o documento "
                "importado precisa de um dono. Peça a um administrador para "
                "vincular a sua conta."
            ),
        )

    bruto = (await request.form()).get("politica") or PoliticaDeDuplicidade.IGNORAR.value
    try:
        politica = PoliticaDeDuplicidade(bruto)
    except ValueError:
        return _pagina_importar(None, usuario, request, erro=f"Política {bruto!r} desconhecida.")

    try:
        lote = await _ler_uploads(arquivos)
    except HTTPException as erro:
        return _pagina_importar(None, usuario, request, erro=erro.detail, politica=politica.value)
    if not lote:
        return _pagina_importar(
            None, usuario, request, erro="Nenhum arquivo enviado.", politica=politica.value
        )

    session = get_session(_get_engine())
    try:
        resultado = ImportadorDeDocumentos(
            session, escritorio_id=usuario.escritorio_id, politica=politica
        ).importar_lote(lote)
        session.commit()
        return _pagina_importar(
            session, usuario, request, resultado=resultado, politica=politica.value
        )
    finally:
        session.close()


async def _ler_uploads(arquivos: list[UploadFile]) -> list[tuple[str, bytes]]:
    """Os arquivos em memória, com nome saneado e tamanho conferido.

    Vão para a memória, e não para disco, porque o importador os quer em bytes
    e porque o que sobra deles — o XML original — vai para o banco, não para o
    sistema de arquivos. Escrever em disco criaria um terceiro lugar onde o
    documento existe, e um a mais para esquecer de apagar.
    """
    limite = max_upload_bytes()
    lote: list[tuple[str, bytes]] = []
    for arquivo in arquivos:
        if not arquivo.filename:
            continue
        nome = safe_original_name(arquivo.filename)
        if pathlib.Path(nome).suffix.lower() not in EXTENSOES_XML:
            raise HTTPException(
                status_code=400,
                detail=f"{nome}: só XML de documento fiscal é importado por aqui.",
            )
        conteudo = await arquivo.read(limite + 1)
        await arquivo.close()
        if len(conteudo) > limite:
            raise HTTPException(
                status_code=413,
                detail=f"{nome} passa do limite de {limite // (1024 * 1024)} MB.",
            )
        if not conteudo:
            raise HTTPException(status_code=400, detail=f"{nome} está vazio.")
        lote.append((nome, conteudo))
    return lote


# ── Rotas: gerar, conferir e marcar a transmissão ──────────────────────────
#
# **Gerar sempre arquiva**, e a tela diz isso antes de o botão existir. Não há
# prévia que grave em disco: a prévia é o espelho, que é prosa e não arquivo
# transmissível — ninguém o entrega por engano. Um arquivo que sai do sistema
# sem deixar registro é exatamente o buraco que a terceira camada fecha.

GERADORES_WEB = {
    "efd_icms": GeradorEFDICMS,
    "efd_contribuicoes": GeradorEFDContribuicoes,
}


def _escrituracoes_do_periodo(session, empresa, inicio, fim, tipo):
    consulta = (
        select(Escrituracao)
        .where(
            Escrituracao.empresa_id == empresa.id,
            Escrituracao.tipo == tipo,
            Escrituracao.data_inicio == inicio,
            Escrituracao.data_fim == fim,
        )
        .order_by(Escrituracao.gerada_em.desc(), Escrituracao.id.desc())
    )
    return list(session.execute(consulta).scalars().all())


def _pagina_gerar(session, usuario, request, **contexto):
    empresas = (
        session.execute(aplicar_escopo_empresas(select(Empresa), usuario).order_by(Empresa.nome))
        .scalars()
        .all()
    )
    dados = {
        "request": request,
        "usuario": usuario,
        "current_page": "gerar",
        "empresas": empresas,
        "empresa": None,
        "espelho": None,
        "escrituracoes": [],
        "erro": None,
        "gerada": None,
        "transmitida": None,
        "de": "",
        "ate": "",
        "tipo": "efd_icms",
        "tipos": TIPOS,
        "fmt_moeda": fmt_moeda,
        "fmt_data": fmt_data,
    }
    dados.update(contexto)
    return HTMLResponse(
        jinja_env.get_template("gerar.html").render(dados),
        status_code=400 if dados["erro"] else 200,
    )


def _periodo_pedido(de: str, ate: str):
    inicio, fim = _data_opcional(de), _data_opcional(ate)
    if inicio is None or fim is None:
        return None, "Informe o período inteiro — começo e fim."
    if fim < inicio:
        return None, "O fim do período é anterior ao começo."
    return (inicio, fim), None


@app.get("/fiscal/gerar", response_class=HTMLResponse)
async def gerar_page(
    request: Request,
    empresa: int | None = Query(default=None),
    de: str | None = Query(default=None),
    ate: str | None = Query(default=None),
    tipo: str = Query(default="efd_icms"),
):
    """O espelho do que sairia — **sem gerar e sem arquivar**.

    O espelho é o arquivo em forma de leitura, com as conferências que o
    validador do Fisco faria. Existe para que a resposta a "está certo?" venha
    antes de o arquivo existir, e não depois de ele ter sido entregue.
    """
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    dados = {"de": de or "", "ate": ate or "", "tipo": tipo}
    session = get_session(_get_engine())
    try:
        if tipo not in GERADORES_WEB:
            return _pagina_gerar(
                session, usuario, request, erro=f"Obrigação {tipo!r} desconhecida."
            )
        alvo = _empresa_do_usuario(session, usuario, empresa) if empresa else None
        if empresa and alvo is None:
            return _pagina_gerar(
                session, usuario, request, erro=f"Empresa #{empresa} não encontrada.", **dados
            )
        if alvo is None or not (de and ate):
            return _pagina_gerar(session, usuario, request, empresa=alvo, **dados)

        periodo, problema = _periodo_pedido(de, ate)
        if problema:
            return _pagina_gerar(session, usuario, request, empresa=alvo, erro=problema, **dados)
        inicio, fim = periodo

        try:
            resultado = GERADORES_WEB[tipo](
                session, empresa=alvo, data_inicio=inicio, data_fim=fim
            ).gerar()
            visao = espelho(resultado, tipo=tipo)
        except (CampoObrigatorioAusente, TipoSemLeiaute, ValueError) as erro:
            return _pagina_gerar(
                session,
                usuario,
                request,
                empresa=alvo,
                erro=str(erro),
                escrituracoes=_escrituracoes_do_periodo(session, alvo, inicio, fim, tipo),
                **dados,
            )

        return _pagina_gerar(
            session,
            usuario,
            request,
            empresa=alvo,
            espelho=visao,
            escrituracoes=_escrituracoes_do_periodo(session, alvo, inicio, fim, tipo),
            **dados,
        )
    finally:
        session.close()


@app.post("/fiscal/gerar", response_class=HTMLResponse)
async def gerar_arquivo(request: Request):
    """Gera a EFD **e arquiva**. Não há como fazer uma sem a outra."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    formulario = await request.form()
    dados = {
        "de": (formulario.get("de") or "").strip(),
        "ate": (formulario.get("ate") or "").strip(),
        "tipo": (formulario.get("tipo") or "").strip(),
    }

    session = get_session(_get_engine())
    try:
        if dados["tipo"] not in GERADORES_WEB:
            return _pagina_gerar(
                session, usuario, request, erro=f"Obrigação {dados['tipo']!r} desconhecida."
            )
        try:
            empresa_id = int(formulario.get("empresa") or "")
        except ValueError:
            return _pagina_gerar(session, usuario, request, erro="Empresa não informada.", **dados)
        alvo = _empresa_do_usuario(session, usuario, empresa_id)
        if alvo is None:
            return _pagina_gerar(
                session, usuario, request, erro=f"Empresa #{empresa_id} não encontrada.", **dados
            )

        periodo, problema = _periodo_pedido(dados["de"], dados["ate"])
        if problema:
            return _pagina_gerar(session, usuario, request, empresa=alvo, erro=problema, **dados)
        inicio, fim = periodo

        try:
            resultado = GERADORES_WEB[dados["tipo"]](
                session, empresa=alvo, data_inicio=inicio, data_fim=fim
            ).gerar()
        except (CampoObrigatorioAusente, ValueError) as erro:
            return _pagina_gerar(session, usuario, request, empresa=alvo, erro=str(erro), **dados)

        escrituracao = arquivar(
            session,
            resultado=resultado,
            empresa=alvo,
            tipo=dados["tipo"],
            data_inicio=inicio,
            data_fim=fim,
            usuario_id=usuario.id,
        )
        session.commit()
        return _pagina_gerar(
            session,
            usuario,
            request,
            empresa=alvo,
            gerada=escrituracao,
            escrituracoes=_escrituracoes_do_periodo(session, alvo, inicio, fim, dados["tipo"]),
            **dados,
        )
    finally:
        session.close()


def _escrituracao_do_usuario(session, usuario, escrituracao_id: int) -> Escrituracao | None:
    consulta = aplicar_escopo_empresas(
        select(Escrituracao)
        .join(Empresa, Escrituracao.empresa_id == Empresa.id)
        .where(Escrituracao.id == escrituracao_id),
        usuario,
    )
    return session.execute(consulta).scalar_one_or_none()


@app.get("/fiscal/escrituracoes/{escrituracao_id}/arquivo")
async def escrituracao_arquivo(request: Request, escrituracao_id: int):
    """O arquivo que saiu — o guardado, não um recém-gerado.

    Gerar de novo produziria um arquivo parecido e possivelmente diferente: a
    camada efetiva pode ter mudado desde então. A terceira camada existe para
    responder "o que você entregou", e responder isso com uma reconstrução
    seria responder outra pergunta.
    """
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    session = get_session(_get_engine())
    try:
        escrituracao = _escrituracao_do_usuario(session, usuario, escrituracao_id)
        if escrituracao is None:
            return HTMLResponse("Escrituração não encontrada", status_code=404)
        nome = f"{escrituracao.tipo}_{escrituracao.data_inicio:%Y%m}_{escrituracao.id}.txt"
        # O conteúdo já vem com CRLF do gerador, e vai como bytes justamente
        # para ninguém no caminho "consertar" a quebra de linha — o validador
        # recusa o arquivo inteiro se ela mudar.
        return Response(
            content=escrituracao.conteudo.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{nome}"'},
        )
    finally:
        session.close()


@app.post("/fiscal/transmitida", response_class=HTMLResponse)
async def marcar_como_transmitida(request: Request):
    """Registra qual geração foi a entregue.

    **Não se desfaz.** Marcar a errada e "corrigir depois" apagaria o registro
    de que a primeira foi dada como entregue, que é justamente o que se quer
    provar. Por isso a tela pede o recibo e avisa antes.
    """
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)

    formulario = await request.form()
    session = get_session(_get_engine())
    try:
        try:
            escrituracao_id = int(formulario.get("escrituracao") or "")
        except ValueError:
            return _pagina_gerar(session, usuario, request, erro="Escrituração não informada.")

        escrituracao = _escrituracao_do_usuario(session, usuario, escrituracao_id)
        if escrituracao is None:
            return _pagina_gerar(
                session,
                usuario,
                request,
                erro=f"Escrituração #{escrituracao_id} não encontrada.",
            )

        dados = {
            "de": escrituracao.data_inicio.isoformat(),
            "ate": escrituracao.data_fim.isoformat(),
            "tipo": escrituracao.tipo,
        }
        # A empresa vem por id: `Escrituracao` guarda a chave, não o objeto —
        # e o escopo já foi conferido ao achar a escrituração.
        empresa = session.get(Empresa, escrituracao.empresa_id)
        try:
            marcar_transmitida(
                session,
                escrituracao,
                recibo=(formulario.get("recibo") or "").strip() or None,
                usuario_id=usuario.id,
                forcar=bool(formulario.get("forcar")),
            )
        except TransmissaoInvalida as erro:
            return _pagina_gerar(
                session,
                usuario,
                request,
                empresa=empresa,
                erro=str(erro),
                escrituracoes=_escrituracoes_do_periodo(
                    session,
                    empresa,
                    escrituracao.data_inicio,
                    escrituracao.data_fim,
                    escrituracao.tipo,
                ),
                **dados,
            )
        session.commit()
        return _pagina_gerar(
            session,
            usuario,
            request,
            empresa=empresa,
            transmitida=escrituracao,
            escrituracoes=_escrituracoes_do_periodo(
                session,
                empresa,
                escrituracao.data_inicio,
                escrituracao.data_fim,
                escrituracao.tipo,
            ),
            **dados,
        )
    finally:
        session.close()


# ── Rotas: Fase 16 — Monitoramento Operacional ─────────────────────────────


def _monitoring_forbidden(api: bool = False):
    if api:
        return JSONResponse(
            {"status": "erro", "mensagem": "Acesso administrativo necessário"},
            status_code=403,
        )
    return HTMLResponse("Acesso administrativo necessário", status_code=403)


@app.get("/monitoring", response_class=HTMLResponse)
async def monitoring_page(request: Request):
    """Dashboard operacional restrito a administradores."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return RedirectResponse(url="/login", status_code=302)
    if not usuario.admin:
        return _monitoring_forbidden()
    return HTMLResponse(
        jinja_env.get_template("monitoring.html").render(
            {
                "request": request,
                "usuario": usuario,
                "current_page": "monitoring",
                "app_version": APP_VERSION,
                # A janela que a página abre selecionada é a configurada, não
                # um literal no template — senão a variável valeria só para
                # quem chama a API direto.
                "janela_minutos": janela_padrao_minutos(),
            }
        )
    )


@app.get("/api/monitoring/summary")
async def api_monitoring_summary(
    request: Request,
    minutes: int | None = Query(None, ge=1, le=1440),
):
    """Snapshot agregado de saúde, tráfego e serviços internos.

    Sem ``minutes``, vale a janela de ``SPED_HUB_METRICS_WINDOW_MINUTES``.  O
    default fica em ``None`` de propósito: um literal aqui seria avaliado no
    import do módulo e ignoraria a configuração.
    """
    usuario = await get_usuario_atual(request)
    if not usuario:
        return JSONResponse({"status": "erro", "mensagem": "Não autenticado"}, status_code=401)
    if not usuario.admin:
        return _monitoring_forbidden(api=True)
    return build_operational_snapshot(
        metrics_collector,
        db_path=_db_reference(),
        minutes=minutes,
    )


@app.post("/api/monitoring/reset")
async def api_monitoring_reset(request: Request):
    """Limpa somente a janela HTTP em memória; não remove dados de negócio."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return JSONResponse({"status": "erro", "mensagem": "Não autenticado"}, status_code=401)
    if not usuario.admin:
        return _monitoring_forbidden(api=True)
    metrics_collector.reset()
    return {"status": "ok", "mensagem": "Métricas HTTP reiniciadas"}


# ── Rotas: Fase 15 — Email, Worker Status, Redis Cache ────────────────────


@app.get("/api/email/stats")
async def api_email_stats(request: Request):
    """Estatísticas do serviço de email."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return JSONResponse({"status": "erro", "mensagem": "Não autenticado"}, status_code=401)
    svc = get_email_service()
    return svc.stats()


@app.get("/api/email/historico")
async def api_email_historico(request: Request, limite: int = Query(20, ge=1, le=100)):
    """Histórico de emails enviados."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return JSONResponse({"status": "erro", "mensagem": "Não autenticado"}, status_code=401)
    svc = get_email_service()
    return {"dados": svc.historico(limite=limite)}


@app.post("/api/email/test")
async def api_email_test(request: Request):
    """Envia email de teste (modo log)."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return JSONResponse({"status": "erro", "mensagem": "Não autenticado"}, status_code=401)
    svc = get_email_service()
    msg = svc.enviar(
        para=usuario.email,
        assunto="[SPED-HUB] Email de Teste",
        corpo=f"Olá {usuario.nome},\n\nEste é um email de teste do SPED-HUB.\n\nSeu sistema de notificações está configurado corretamente.",
        async_mode=False,
    )
    return {
        "status": "ok",
        "mensagem": f"Email enviado para {usuario.email}",
        "detalhes": {"status": msg.status},
    }


@app.get("/api/worker/status")
async def api_worker_status(request: Request):
    """Status da fila de workers."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return JSONResponse({"status": "erro", "mensagem": "Não autenticado"}, status_code=401)
    try:
        from src.worker_queue import get_worker_queue

        q = get_worker_queue()
        if q is None:
            return {"status": "not_initialized", "mensagem": "Worker queue não inicializada"}
        return {
            "status": "running",
            "pending": q.pending_count(),
            "active": q.active_count(),
            "total_tasks": len(q.list_tasks()),
        }
    except Exception as e:
        return {"status": "error", "mensagem": str(e)}


@app.get("/api/redis/cache/stats")
async def api_redis_cache_stats(request: Request):
    """Estatísticas do cache Redis (Fase 15)."""
    usuario = await get_usuario_atual(request)
    if not usuario:
        return JSONResponse({"status": "erro", "mensagem": "Não autenticado"}, status_code=401)

    redis_url = get_settings().redis_url_or_local
    cache = RedisCacheService(redis_url=redis_url, prefix="api:")
    stats = cache.stats()
    return stats


@app.get("/api/health/full")
async def api_health_full():
    """Health check completo — verifica DB, cache, workers."""

    status = {"database": "ok", "cache": "unknown", "workers": "unknown"}

    # DB
    try:
        engine = _get_engine()
        session = get_session(engine)
        session.execute(select(1))
        session.close()
    except Exception as e:
        status["database"] = f"error: {e}"

    # Cache
    try:
        redis_url = get_settings().redis_url_or_local
        cache = RedisCacheService(redis_url=redis_url, prefix="health:")
        cache.set("health", "ok", ttl=10)
        if cache.get("health") == "ok":
            status["cache"] = f"ok ({cache.stats()['backend']})"
        else:
            status["cache"] = "error: write/read mismatch"
    except Exception as e:
        status["cache"] = f"error: {e}"

    # Workers
    try:
        from src.worker_queue import get_worker_queue

        q = get_worker_queue()
        if q:
            status["workers"] = f"running ({q.active_count()} active, {q.pending_count()} pending)"
        else:
            status["workers"] = "not_initialized"
    except Exception as e:
        status["workers"] = f"error: {e}"

    return {"status": "ok", "version": APP_VERSION, "components": status}


def main():
    """Inicia o dashboard usando configuração de ambiente."""
    import uvicorn

    cfg = get_settings()
    host = cfg.host
    port = cfg.port
    reload_enabled = cfg.reload
    uvicorn.run("src.dashboard.app:app", host=host, port=port, reload=reload_enabled)


if __name__ == "__main__":
    main()
