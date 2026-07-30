"""Módulo de Webhooks — Fase 10 + Fase 11.

Sistema de notificação para integração com sistemas de terceiros.
Permite registrar endpoints que recebem POST em eventos do SPED-HUB.

Fase 11: +retry com exponential backoff, +WebhookDelivery para dashboard,
         +dispatch síncrono com tracking completo.

Eventos suportados:
  - ecd.importada      — Nova ECD importada
  - ecd.validada       — Validação de integridade concluída
  - relatorio.gerado   — Relatório contábil gerado
"""

import asyncio
import datetime
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import threading
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import (
    WebhookDelivery,
    WebhookRegistration,
    criar_engine,
    get_session,
    init_db,
)
from src.settings import get_settings

logger = logging.getLogger("sped-hub.webhooks")

EVENTOS_DISPONIVEIS = [
    "ecd.importada",
    "ecd.validada",
    "relatorio.gerado",
]

# Exponential backoff: 2s, 4s, 8s, 16s, 32s
BACKOFF_BASE = 2
BACKOFF_MAX = 60

# ── Vocabulário de `WebhookDelivery.status` ────────────────────────────────
#
# Cada linha de `WebhookDelivery` é UMA TENTATIVA, não um evento.  A distinção
# importa: sem ela, uma entrega que só funcionou na 3ª tentativa aparecia como
# "1 sucesso em 3 entregas" e o painel anunciava 33% de sucesso para uma
# integração que estava funcionando.
#
#   pending     — tentativa em voo.
#   success     — a tentativa recebeu 2xx: o evento CHEGOU.
#   superseded  — a tentativa falhou e outra a seguiu.  Estado TERMINAL e
#                 histórico: não é desfecho do evento e não é reenviável.
#   failed      — a última tentativa falhou: o evento NÃO chegou.  É o único
#                 desfecho negativo, e o que o reenvio manual procura.
#   retried     — entrega `failed` que foi reenviada à mão e o reenvio chegou.
#
# `retrying` foi RETIRADO.  Ele era escrito em toda tentativa que falhava e
# nunca mais tocado, o que deixava linhas presas nele para sempre: não eram
# desfecho, não eram reenviáveis e ninguém as resolvia.  Linha nesse estado
# hoje é resíduo de versão anterior — a migração as reconcilia.
STATUS_EM_VOO = "pending"
STATUS_SUCESSO = "success"
STATUS_SUPERADA = "superseded"
STATUS_FALHA = "failed"
STATUS_REENVIADA = "retried"

# Estados que encerram o destino de um evento.  `superseded` e `pending` não
# entram: o primeiro é histórico de tentativa, o segundo ainda não terminou.
STATUS_DESFECHO = (STATUS_SUCESSO, STATUS_FALHA, STATUS_REENVIADA)

# Estados terminais — nenhum processo vai voltar a mexer na linha.
STATUS_TERMINAIS = (*STATUS_DESFECHO, STATUS_SUPERADA)

# Quantas entregas um clique em "Reenviar falhas" processa.
#
# O reenvio é sequencial e `POST /api/v1/webhooks/retry` o aguarda dentro da
# requisição.  Cada entrega custa, no pior caso, todas as tentativas
# esgotando o timeout mais os backoffs — ~36 s com a configuração padrão.  O
# lote era de 100, o que dá quase uma hora de requisição aberta contra um
# endpoint morto: o navegador do operador desiste, o trabalho continua no
# servidor, e ele clica de novo.  Com 20, o pior caso cabe em minutos e o
# retorno informa quantas ficaram — clicar de novo drena o resto.
LOTE_DE_REENVIO = 20


def validate_webhook_url(url: str, *, resolve: bool = False) -> str:
    """Valida URL e bloqueia alvos locais/privados para reduzir risco de SSRF."""
    parsed = urlsplit(url.strip())
    allow_http = get_settings().webhook_allow_http
    allowed_schemes = {"https", "http"} if allow_http else {"https"}
    if parsed.scheme.lower() not in allowed_schemes:
        expected = "HTTPS" if not allow_http else "HTTP ou HTTPS"
        raise ValueError(f"Webhook deve usar {expected}")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL de webhook inválida")
    if parsed.fragment:
        raise ValueError("URL de webhook não pode conter fragmento")

    def ensure_public(address: str) -> None:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("Webhook não pode apontar para endereço local ou privado")

    try:
        ensure_public(parsed.hostname)
    except ValueError as exc:
        # Um hostname comum não é um IP; literais inválidos/privados são rejeitados.
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            if resolve:
                try:
                    addresses = {
                        item[4][0]
                        for item in socket.getaddrinfo(
                            parsed.hostname,
                            parsed.port or (443 if parsed.scheme == "https" else 80),
                            type=socket.SOCK_STREAM,
                        )
                    }
                except socket.gaierror as dns_error:
                    raise ValueError("Hostname do webhook não pôde ser resolvido") from dns_error
                if not addresses:
                    raise ValueError("Hostname do webhook não possui endereço válido") from exc
                for address in addresses:
                    ensure_public(address)
            elif parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
                raise ValueError("Webhook não pode apontar para localhost") from exc
        else:
            raise

    return parsed.geturl()


@dataclass
class WebhookEvent:
    """Evento disparado internamente."""

    tipo: str
    dados: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.UTC).isoformat())


class WebhookService:
    """Serviço de gerenciamento e dispatch de webhooks."""

    def __init__(self, db_path: str = "sped_hub.db"):
        self.db_path = db_path

    def _get_session(self) -> Session:
        engine = criar_engine(self.db_path)
        init_db(engine)
        return get_session(engine)

    def registrar(
        self,
        url: str,
        eventos: list[str],
        secret: str | None = None,
        descricao: str = "",
        ativo: bool = True,
        max_retries: int | None = None,
    ) -> WebhookRegistration:
        """Registra um novo webhook.

        `max_retries=None` usa `SPED_HUB_WEBHOOK_DEFAULT_MAX_RETRIES`. A
        setting é o default para registro NOVO, não um teto aplicado na
        entrega: a coluna é `NOT NULL` e cada registro carrega o próprio
        valor, então mudar a variável de ambiente depois não reescreve o que
        já está no banco — reescrever seria surpresa, não configuração.
        """
        url = validate_webhook_url(url)
        if max_retries is None:
            max_retries = get_settings().webhook_default_max_retries
        session = self._get_session()
        try:
            wh = WebhookRegistration(
                url=url,
                eventos=json.dumps(eventos),
                secret=secret,
                descricao=descricao,
                ativo=ativo,
                max_retries=max_retries,
            )
            session.add(wh)
            session.commit()
            session.refresh(wh)
            logger.info("Webhook registrado: %s → %s", wh.id, url)
            return wh
        finally:
            session.close()

    def listar(self) -> list[WebhookRegistration]:
        """Lista todos os webhooks registrados."""
        session = self._get_session()
        try:
            return list(
                session.execute(
                    select(WebhookRegistration).order_by(WebhookRegistration.criado_em.desc())
                ).scalars()
            )
        finally:
            session.close()

    def atualizar(
        self,
        webhook_id: int,
        url: str | None = None,
        eventos: list[str] | None = None,
        secret: str | None = None,
        descricao: str | None = None,
        ativo: bool | None = None,
        max_retries: int | None = None,
    ) -> WebhookRegistration | None:
        """Atualiza um webhook existente."""
        session = self._get_session()
        try:
            wh = session.get(WebhookRegistration, webhook_id)
            if not wh:
                return None
            if url is not None:
                wh.url = validate_webhook_url(url)
            if eventos is not None:
                wh.eventos = json.dumps(eventos)
            if secret is not None:
                wh.secret = secret
            if descricao is not None:
                wh.descricao = descricao
            if ativo is not None:
                wh.ativo = ativo
            if max_retries is not None:
                wh.max_retries = max_retries
            session.commit()
            session.refresh(wh)
            return wh
        finally:
            session.close()

    def remover(self, webhook_id: int) -> bool:
        """Remove um webhook."""
        session = self._get_session()
        try:
            wh = session.get(WebhookRegistration, webhook_id)
            if not wh:
                return False
            session.delete(wh)
            session.commit()
            return True
        finally:
            session.close()

    def get_deliveries(
        self,
        webhook_id: int | None = None,
        status: str | None = None,
        limite: int = 50,
    ) -> list[WebhookDelivery]:
        """Lista entregas com filtros opcionais."""
        session = self._get_session()
        try:
            query = select(WebhookDelivery).order_by(WebhookDelivery.criado_em.desc())
            if webhook_id:
                query = query.where(WebhookDelivery.webhook_id == webhook_id)
            if status:
                query = query.where(WebhookDelivery.status == status)
            return list(session.execute(query.limit(limite)).scalars())
        finally:
            session.close()

    def get_dashboard_stats(self) -> dict:
        """Estatísticas agregadas para o dashboard de webhooks."""
        session = self._get_session()
        try:
            total_webhooks = (
                session.execute(select(func.count(WebhookRegistration.id))).scalar() or 0
            )

            total_ativos = (
                session.execute(
                    select(func.count(WebhookRegistration.id)).where(
                        WebhookRegistration.ativo.is_(True)
                    )
                ).scalar()
                or 0
            )

            total_deliveries = session.execute(select(func.count(WebhookDelivery.id))).scalar() or 0

            total_success = (
                session.execute(
                    select(func.count(WebhookDelivery.id)).where(
                        WebhookDelivery.status.in_((STATUS_SUCESSO, STATUS_REENVIADA))
                    )
                ).scalar()
                or 0
            )

            total_failed = (
                session.execute(
                    select(func.count(WebhookDelivery.id)).where(
                        WebhookDelivery.status == STATUS_FALHA
                    )
                ).scalar()
                or 0
            )

            # Denominador da taxa: desfechos de evento, não tentativas.  Cada
            # tentativa é uma linha, então contar linhas fazia uma entrega que
            # só funcionou na 3ª tentativa valer 1 sucesso em 3 — o painel
            # anunciava 33% para uma integração que estava entregando.
            total_desfechos = (
                session.execute(
                    select(func.count(WebhookDelivery.id)).where(
                        WebhookDelivery.status.in_(STATUS_DESFECHO)
                    )
                ).scalar()
                or 0
            )

            # Últimas 24h
            agora = datetime.datetime.now(datetime.UTC)
            ontem = agora - datetime.timedelta(hours=24)
            deliveries_24h = (
                session.execute(
                    select(func.count(WebhookDelivery.id)).where(WebhookDelivery.criado_em >= ontem)
                ).scalar()
                or 0
            )

            success_24h = (
                session.execute(
                    select(func.count(WebhookDelivery.id)).where(
                        WebhookDelivery.criado_em >= ontem,
                        WebhookDelivery.status.in_((STATUS_SUCESSO, STATUS_REENVIADA)),
                    )
                ).scalar()
                or 0
            )

            taxa_sucesso = (
                round((total_success / total_desfechos * 100), 1) if total_desfechos > 0 else 100.0
            )

            return {
                "total_webhooks": total_webhooks,
                "webhooks_ativos": total_ativos,
                "total_deliveries": total_deliveries,
                "total_success": total_success,
                "total_failed": total_failed,
                "taxa_sucesso": taxa_sucesso,
                "deliveries_24h": deliveries_24h,
                "success_24h": success_24h,
            }
        finally:
            session.close()

    async def dispatch(self, evento: WebhookEvent) -> dict[str, int]:
        """Dispara evento para todos os webhooks inscritos com retry.

        Returns:
            dict com contagem de sucessos e falhas.
        """
        session = self._get_session()
        try:
            webhooks = (
                session.execute(
                    select(WebhookRegistration).where(WebhookRegistration.ativo.is_(True))
                )
                .scalars()
                .all()
            )
        finally:
            session.close()

        sucessos = 0
        falhas = 0

        for wh in webhooks:
            # Um registro com `eventos` ilegível não pode impedir os demais de
            # receber: antes, o `json.loads` estourava e o dispatch inteiro
            # morria no primeiro registro corrompido.
            try:
                eventos_inscritos = json.loads(wh.eventos or "[]")
            except (TypeError, ValueError):
                logger.warning(
                    "Webhook %s tem lista de eventos ilegível; ignorado nesta entrega", wh.id
                )
                continue
            if evento.tipo not in eventos_inscritos:
                continue

            resultado = await self._enviar_com_retry(wh, evento)
            if resultado:
                sucessos += 1
            else:
                falhas += 1

        return {"sucessos": sucessos, "falhas": falhas}

    async def _enviar_com_retry(self, wh: WebhookRegistration, evento: WebhookEvent) -> bool:
        """Envia webhook com retry usando exponential backoff."""
        payload = {
            "evento": evento.tipo,
            "dados": evento.dados,
            "timestamp": evento.timestamp,
            "webhook_id": wh.id,
        }
        body = json.dumps(payload).encode()

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "SPED-HUB-Webhook/1.0",
        }

        if wh.secret:
            signature = hmac.new(wh.secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-SPED-HUB-Signature"] = signature

        target_url = validate_webhook_url(wh.url, resolve=True)
        # `SPED_HUB_WEBHOOK_DEFAULT_MAX_RETRIES` e `SPED_HUB_WEBHOOK_TIMEOUT`
        # existiam nas settings sem nenhum consumidor: quem as configurava
        # não mudava nada (§2.2).  O registro do webhook continua vencendo o
        # default quando declara o próprio `max_retries`.
        settings = get_settings()
        # O registro manda: `max_retries` é NOT NULL e vem do default da
        # setting no momento do cadastro. O `or` cobre linha legada com 0.
        max_tentativas = max(1, wh.max_retries or settings.webhook_default_max_retries)
        timeout = max(1, settings.webhook_timeout_seconds)

        last_error = "Max retries exceeded"
        for tentativa in range(1, max_tentativas + 1):
            # Cria registro de entrega
            delivery = self._criar_delivery(wh.id, evento.tipo, json.dumps(payload), tentativa)

            try:
                async with httpx.AsyncClient(timeout=float(timeout)) as client:
                    response = await client.post(target_url, json=payload, headers=headers)

                if 200 <= response.status_code < 300:
                    self._atualizar_delivery(
                        delivery.id, STATUS_SUCESSO, response.status_code, response.text[:2000]
                    )
                    self._atualizar_webhook_stats(wh.id, sucesso=True)
                    return True
                last_error = f"HTTP {response.status_code}"
                erro_desta = last_error
                codigo_desta = response.status_code
                corpo_desta = response.text[:2000]
            except Exception as exc:
                last_error = str(exc)[:500]
                erro_desta = last_error
                codigo_desta = None
                corpo_desta = None

            # A tentativa falhou.  O desfecho dela já é conhecido AQUI: se
            # ainda há tentativa pela frente, esta foi superada; se era a
            # última, o evento não chegou.  Marcar tudo como "retrying" e só
            # depois corrigir a última era o que deixava as anteriores presas.
            ultima = tentativa == max_tentativas
            self._atualizar_delivery(
                delivery.id,
                STATUS_FALHA if ultima else STATUS_SUPERADA,
                codigo_desta,
                corpo_desta,
                erro_desta,
            )
            if ultima:
                self._atualizar_webhook_stats(wh.id, sucesso=False)
                return False

            await asyncio.sleep(min(BACKOFF_BASE**tentativa, BACKOFF_MAX))

        # Inalcançável: o `return` da última tentativa fecha o laço.  Fica como
        # rede, não como caminho — `max_tentativas` é sempre >= 1.
        return False

    def _criar_delivery(
        self, webhook_id: int, evento: str, request_body: str, tentativa: int
    ) -> WebhookDelivery:
        """Cria registro de entrega no banco."""
        session = self._get_session()
        try:
            delivery = WebhookDelivery(
                webhook_id=webhook_id,
                evento=evento,
                status="pending",
                request_body=request_body,
                tentativa=tentativa,
            )
            session.add(delivery)
            session.commit()
            session.refresh(delivery)
            return delivery
        finally:
            session.close()

    def _atualizar_delivery(
        self,
        delivery_id: int,
        status: str,
        status_code: int | None = None,
        response_body: str | None = None,
        error: str | None = None,
    ):
        """Atualiza status de uma entrega."""
        session = self._get_session()
        try:
            d = session.get(WebhookDelivery, delivery_id)
            if d:
                d.status = status
                if status_code:
                    d.status_code = status_code
                if response_body:
                    d.response_body = response_body
                if error:
                    d.error_message = error
                # `superseded` também conclui: aquela tentativa terminou.
                # Ficar sem `concluido_em` era o que fazia a linha parecer em
                # andamento para sempre no histórico do painel.
                if status in STATUS_TERMINAIS:
                    d.concluido_em = datetime.datetime.now(datetime.UTC)
                session.commit()
        finally:
            session.close()

    def _atualizar_webhook_stats(self, webhook_id: int, sucesso: bool):
        """Atualiza estatísticas do webhook."""
        session = self._get_session()
        try:
            wh = session.get(WebhookRegistration, webhook_id)
            if wh:
                wh.ultimo_envio = datetime.datetime.now(datetime.UTC)
                wh.total_envios = (wh.total_envios or 0) + 1
                if not sucesso:
                    wh.total_falhas = (wh.total_falhas or 0) + 1
                session.commit()
        finally:
            session.close()

    def _segundos_para_abandono(self, wh: WebhookRegistration | None) -> int:
        """Quanto tempo uma entrega pode ficar sem desfecho antes de ser órfã.

        Tem de ser folgado acima do pior caso legítimo de UMA entrega — todas
        as tentativas esgotando o timeout, mais todos os backoffs — senão o
        reenvio dispara sobre uma entrega que ainda está em voo e o assinante
        recebe o evento duas vezes.  Daí a margem de 3×, e um piso de 5 min.
        """
        cfg = get_settings()
        tentativas = max(1, (wh.max_retries if wh else 0) or cfg.webhook_default_max_retries)
        timeout = max(1, cfg.webhook_timeout_seconds)
        backoff = sum(min(BACKOFF_BASE**n, BACKOFF_MAX) for n in range(1, tentativas))
        return max(300, (tentativas * timeout + backoff) * 3)

    def deliveries_abandonadas(self, webhook_id: int | None = None) -> list[WebhookDelivery]:
        """Entregas que ficaram sem desfecho porque o processo morreu no meio.

        A entrega roda em thread, com `asyncio.sleep` entre as tentativas.  Um
        restart, um deploy ou um crash no meio disso deixa a linha em
        `pending` (morreu durante o POST) ou `superseded` (morreu no backoff,
        e a tentativa seguinte nunca aconteceu).  Nos dois casos o evento não
        chegou, não existe linha `failed`, e o reenvio manual — que procura
        `failed` — não via nada: o assinante perdia o evento em silêncio e
        **nem manualmente** era possível recuperar.

        Uma entrega lógica é o conjunto de tentativas do mesmo
        `(webhook_id, request_body)` — o `request_body` carrega o timestamp do
        evento, então identifica a emissão.  O conjunto é órfão quando nenhuma
        das linhas alcançou desfecho e a mais recente já passou do limite de
        abandono daquele webhook.
        """
        session = self._get_session()
        try:
            query = select(WebhookDelivery).where(
                WebhookDelivery.status.notin_(STATUS_DESFECHO),
                WebhookDelivery.request_body.is_not(None),
            )
            if webhook_id:
                query = query.where(WebhookDelivery.webhook_id == webhook_id)
            candidatas = list(session.execute(query).scalars())
            if not candidatas:
                return []

            # Um desfecho em qualquer tentativa encerra a entrega lógica.
            desfecho = select(WebhookDelivery.webhook_id, WebhookDelivery.request_body).where(
                WebhookDelivery.status.in_(STATUS_DESFECHO)
            )
            if webhook_id:
                desfecho = desfecho.where(WebhookDelivery.webhook_id == webhook_id)
            resolvidas = set(session.execute(desfecho).all())

            agora = datetime.datetime.now(datetime.UTC)
            por_entrega: dict[tuple[int, str], list[WebhookDelivery]] = {}
            for linha in candidatas:
                chave = (linha.webhook_id, linha.request_body)
                if chave in resolvidas:
                    continue
                por_entrega.setdefault(chave, []).append(linha)

            orfas = []
            for (wh_id, _), tentativas in por_entrega.items():
                mais_recente = max(tentativas, key=lambda linha: linha.tentativa)
                criado = mais_recente.criado_em
                if criado is None:
                    continue
                if criado.tzinfo is None:
                    criado = criado.replace(tzinfo=datetime.UTC)
                limite = self._segundos_para_abandono(self._get_webhook(wh_id))
                if (agora - criado).total_seconds() >= limite:
                    orfas.append(mais_recente)
            return orfas
        finally:
            session.close()

    async def retry_failed(self, webhook_id: int | None = None) -> dict:
        """Reenvia até 100 entregas sem desfecho positivo.

        Cobre as `failed` (todas as tentativas falharam) e as **abandonadas**
        (o processo morreu no meio da entrega).  As segundas não tinham
        caminho de recuperação nenhum, nem automático nem manual.
        """
        session = self._get_session()
        try:
            query = select(WebhookDelivery).where(WebhookDelivery.status == STATUS_FALHA)
            if webhook_id:
                query = query.where(WebhookDelivery.webhook_id == webhook_id)
            deliveries = (
                session.execute(
                    query.order_by(WebhookDelivery.criado_em.desc()).limit(LOTE_DE_REENVIO)
                )
                .scalars()
                .all()
            )
            restantes_falhas = max(
                0,
                (session.execute(query.with_only_columns(func.count())).scalar() or 0)
                - len(deliveries),
            )
            pending = [
                {
                    "id": delivery.id,
                    "webhook_id": delivery.webhook_id,
                    "evento": delivery.evento,
                    "body": delivery.request_body,
                    "abandonada": False,
                }
                for delivery in deliveries
            ]
        finally:
            session.close()

        # As abandonadas entram depois das `failed` e dentro do mesmo lote.
        abandonadas = 0
        restantes_abandonadas = 0
        if len(pending) < LOTE_DE_REENVIO:
            ja_incluidas = {item["id"] for item in pending}
            for orfa in self.deliveries_abandonadas(webhook_id):
                if len(pending) >= LOTE_DE_REENVIO:
                    restantes_abandonadas += 1
                    continue
                if orfa.id in ja_incluidas:
                    continue
                pending.append(
                    {
                        "id": orfa.id,
                        "webhook_id": orfa.webhook_id,
                        "evento": orfa.evento,
                        "body": orfa.request_body,
                        "abandonada": True,
                    }
                )
                abandonadas += 1
            if abandonadas:
                logger.warning(
                    "%d entrega(s) de webhook sem desfecho recuperada(s): o processo "
                    "morreu no meio da entrega e o assinante não recebeu o evento",
                    abandonadas,
                )

        reenviados = 0
        sucessos = 0
        falhas = 0
        for delivery in pending:
            webhook = self._get_webhook(delivery["webhook_id"])
            if not webhook or not webhook.ativo:
                continue
            try:
                payload = json.loads(delivery["body"]) if delivery["body"] else {}
                event_type = payload.get("evento") or delivery["evento"]
                data = payload.get("dados", {})
                timestamp = payload.get("timestamp")
                event = WebhookEvent(tipo=event_type, dados=data)
                if timestamp:
                    event.timestamp = timestamp
            except (json.JSONDecodeError, TypeError):
                logger.warning("Delivery %s possui payload inválido", delivery["id"])
                falhas += 1
                continue

            # A linha de origem NÃO é posta em estado não-terminal aqui.  Era
            # o que fazia o reenvio criar um órfão novo quando o processo
            # morria durante ele: a linha ficava em `retrying` para sempre.
            reenviados += 1
            try:
                delivered = await self._enviar_com_retry(webhook, event)
            except ValueError as exc:
                logger.warning("Retry do webhook %s bloqueado: %s", webhook.id, exc)
                delivered = False
            if delivered:
                sucessos += 1
                self._atualizar_delivery(delivery["id"], STATUS_REENVIADA)
            else:
                falhas += 1
                self._atualizar_delivery(delivery["id"], STATUS_FALHA)

        return {
            "reenviados": reenviados,
            "sucessos": sucessos,
            "falhas": falhas,
            "total_falhas": len(pending),
            "abandonadas_recuperadas": abandonadas,
            # Truncamento silencioso leria como "processei tudo".  O painel
            # mostra este número para o operador saber que falta clicar.
            "restantes": restantes_falhas + restantes_abandonadas,
        }

    def _get_webhook(self, webhook_id: int) -> WebhookRegistration | None:
        session = self._get_session()
        try:
            return session.get(WebhookRegistration, webhook_id)
        finally:
            session.close()


# ═══════════════════════════════════════════════════════════════════════════
# Emissão de eventos
# ═══════════════════════════════════════════════════════════════════════════
#
# Até a versão 0.17.0 este módulo tinha CRUD, proteção contra SSRF, assinatura
# HMAC e entrega com retry — e nenhum ponto do código chamava `dispatch()`.
# Os três eventos de `EVENTOS_DISPONIVEIS` estavam documentados, o cliente
# cadastrava o endpoint e nada chegava nele.
#
# `emitir` é a entrada que faltava.  Três invariantes:
#
# 1. **Nunca quebra a operação de negócio.**  Importação que deu certo não
#    pode virar erro porque o endpoint do cliente está fora do ar.  Toda
#    exceção é logada e engolida.
# 2. **Nunca bloqueia quem chamou.**  A entrega é sequencial e faz backoff
#    de até 60 s por tentativa; fazer isso no caminho da requisição
#    penalizaria o usuário pela lentidão de um terceiro.
# 3. **Custo zero sem assinante.**  Uma consulta indexada decide se há
#    webhook ativo inscrito; sem nenhum, não se cria thread nem event loop.


def _em_segundo_plano(alvo, nome: str) -> None:
    """Executa `alvo` fora do caminho de quem chamou.

    Existe como função própria para ser a costura de teste: entrega em
    thread é o comportamento certo em produção e o errado num teste, que
    precisa afirmar o resultado logo depois de emitir. Teste substitui esta
    função por execução inline em vez de dormir esperando a thread.
    """
    threading.Thread(target=alvo, name=nome, daemon=True).start()


def _ha_assinante(db_path: str, tipo: str) -> bool:
    """Existe webhook ativo inscrito neste tipo de evento?

    A coluna `eventos` guarda JSON, então o filtro fino é em Python — mas a
    consulta já elimina os inativos, que é o caso comum de quem desligou a
    integração sem apagar o registro.
    """
    engine = criar_engine(db_path)
    try:
        init_db(engine)
        with get_session(engine) as session:
            inscricoes = (
                session.execute(
                    select(WebhookRegistration.eventos).where(WebhookRegistration.ativo.is_(True))
                )
                .scalars()
                .all()
            )
    finally:
        engine.dispose()

    for bruto in inscricoes:
        try:
            if tipo in json.loads(bruto or "[]"):
                return True
        except (TypeError, ValueError):
            # Registro com JSON corrompido não impede os demais de receber.
            logger.warning("Webhook com lista de eventos ilegível: %r", bruto)
    return False


def emitir(
    tipo: str,
    dados: dict,
    *,
    db_path: str | None = None,
    aguardar: bool = False,
) -> dict[str, int] | None:
    """Dispara um evento para os webhooks inscritos.

    Args:
        tipo: um de :data:`EVENTOS_DISPONIVEIS`.
        dados: corpo do evento, serializável em JSON.
        db_path: banco a consultar; sem valor, usa a configuração corrente.
        aguardar: entrega no mesmo thread e devolve o resultado. Use em
            teste e em processo que vai encerrar — em caminho de requisição,
            deixe ``False``.

    Returns:
        O resultado de :meth:`WebhookService.dispatch` quando ``aguardar``;
        ``None`` quando a entrega foi para segundo plano ou não havia
        assinante.
    """
    if tipo not in EVENTOS_DISPONIVEIS:
        raise ValueError(f"Evento desconhecido: {tipo!r}. Conhecidos: {EVENTOS_DISPONIVEIS}")

    from src.settings import database_reference

    destino = db_path or database_reference()

    try:
        if not _ha_assinante(destino, tipo):
            return None
    except Exception:
        # Banco indisponível ou schema ausente não pode derrubar quem emitiu.
        logger.exception("Falha ao procurar assinantes de %s", tipo)
        return None

    evento = WebhookEvent(tipo=tipo, dados=dados)

    def entregar() -> dict[str, int] | None:
        try:
            return asyncio.run(WebhookService(destino).dispatch(evento))
        except Exception:
            logger.exception("Falha ao entregar webhook %s", tipo)
            return None

    if aguardar:
        return entregar()

    _em_segundo_plano(entregar, f"webhook-{tipo}")
    return None
