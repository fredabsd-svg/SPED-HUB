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
import os
import socket
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

logger = logging.getLogger("sped-hub.webhooks")

EVENTOS_DISPONIVEIS = [
    "ecd.importada",
    "ecd.validada",
    "relatorio.gerado",
]

# Exponential backoff: 2s, 4s, 8s, 16s, 32s
BACKOFF_BASE = 2
BACKOFF_MAX = 60


def validate_webhook_url(url: str, *, resolve: bool = False) -> str:
    """Valida URL e bloqueia alvos locais/privados para reduzir risco de SSRF."""
    parsed = urlsplit(url.strip())
    allow_http = os.environ.get("SPED_HUB_WEBHOOK_ALLOW_HTTP", "false").lower() == "true"
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
                    raise ValueError("Hostname do webhook não possui endereço válido")
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
    timestamp: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC).isoformat()
    )


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
        max_retries: int = 3,
    ) -> WebhookRegistration:
        """Registra um novo webhook."""
        url = validate_webhook_url(url)
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
            total_webhooks = session.execute(
                select(func.count(WebhookRegistration.id))
            ).scalar() or 0

            total_ativos = session.execute(
                select(func.count(WebhookRegistration.id)).where(
                    WebhookRegistration.ativo == True
                )
            ).scalar() or 0

            total_deliveries = session.execute(
                select(func.count(WebhookDelivery.id))
            ).scalar() or 0

            total_success = session.execute(
                select(func.count(WebhookDelivery.id)).where(
                    WebhookDelivery.status == "success"
                )
            ).scalar() or 0

            total_failed = session.execute(
                select(func.count(WebhookDelivery.id)).where(
                    WebhookDelivery.status == "failed"
                )
            ).scalar() or 0

            # Últimas 24h
            agora = datetime.datetime.now(datetime.UTC)
            ontem = agora - datetime.timedelta(hours=24)
            deliveries_24h = session.execute(
                select(func.count(WebhookDelivery.id)).where(
                    WebhookDelivery.criado_em >= ontem
                )
            ).scalar() or 0

            success_24h = session.execute(
                select(func.count(WebhookDelivery.id)).where(
                    WebhookDelivery.criado_em >= ontem,
                    WebhookDelivery.status == "success",
                )
            ).scalar() or 0

            # Taxa de sucesso
            taxa_sucesso = round((total_success / total_deliveries * 100), 1) if total_deliveries > 0 else 100.0

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
            webhooks = session.execute(
                select(WebhookRegistration).where(
                    WebhookRegistration.ativo == True
                )
            ).scalars().all()
        finally:
            session.close()

        sucessos = 0
        falhas = 0

        for wh in webhooks:
            eventos_inscritos = json.loads(wh.eventos)
            if evento.tipo not in eventos_inscritos:
                continue

            resultado = await self._enviar_com_retry(wh, evento)
            if resultado:
                sucessos += 1
            else:
                falhas += 1

        return {"sucessos": sucessos, "falhas": falhas}

    async def _enviar_com_retry(
        self, wh: WebhookRegistration, evento: WebhookEvent
    ) -> bool:
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
            signature = hmac.new(
                wh.secret.encode(), body, hashlib.sha256
            ).hexdigest()
            headers["X-SPED-HUB-Signature"] = signature

        target_url = validate_webhook_url(wh.url, resolve=True)
        max_tentativas = max(1, wh.max_retries or 3)

        last_error = "Max retries exceeded"
        for tentativa in range(1, max_tentativas + 1):
            # Cria registro de entrega
            delivery = self._criar_delivery(
                wh.id, evento.tipo, json.dumps(payload), tentativa
            )

            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(target_url, json=payload, headers=headers)

                if 200 <= response.status_code < 300:
                    self._atualizar_delivery(
                        delivery.id, "success", response.status_code,
                        response.text[:2000]
                    )
                    self._atualizar_webhook_stats(wh.id, sucesso=True)
                    return True
                else:
                    last_error = f"HTTP {response.status_code}"
                    self._atualizar_delivery(
                        delivery.id, "retrying", response.status_code,
                        response.text[:2000],
                        f"HTTP {response.status_code}"
                    )

            except Exception as exc:
                last_error = str(exc)[:500]
                self._atualizar_delivery(
                    delivery.id, "retrying", error=last_error
                )

            # Backoff antes do próximo retry
            if tentativa < max_tentativas:
                delay = min(BACKOFF_BASE ** tentativa, BACKOFF_MAX)
                await asyncio.sleep(delay)

        # Todas as tentativas falharam
        self._atualizar_delivery(delivery.id, "failed", error=last_error)
        self._atualizar_webhook_stats(wh.id, sucesso=False)
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
                if status in ("success", "failed"):
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

    async def retry_failed(self, webhook_id: int | None = None) -> dict:
        """Reenvia de fato até 100 entregas com falha."""
        session = self._get_session()
        try:
            query = select(WebhookDelivery).where(WebhookDelivery.status == "failed")
            if webhook_id:
                query = query.where(WebhookDelivery.webhook_id == webhook_id)
            deliveries = session.execute(
                query.order_by(WebhookDelivery.criado_em.desc()).limit(100)
            ).scalars().all()
            pending = [
                {
                    "id": delivery.id,
                    "webhook_id": delivery.webhook_id,
                    "evento": delivery.evento,
                    "body": delivery.request_body,
                }
                for delivery in deliveries
            ]
        finally:
            session.close()

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

            self._atualizar_delivery(delivery["id"], "retrying")
            reenviados += 1
            try:
                delivered = await self._enviar_com_retry(webhook, event)
            except ValueError as exc:
                logger.warning("Retry do webhook %s bloqueado: %s", webhook.id, exc)
                delivered = False
            if delivered:
                sucessos += 1
                self._atualizar_delivery(delivery["id"], "retried")
            else:
                falhas += 1
                self._atualizar_delivery(delivery["id"], "failed")

        return {
            "reenviados": reenviados,
            "sucessos": sucessos,
            "falhas": falhas,
            "total_falhas": len(pending),
        }

    def _get_webhook(self, webhook_id: int) -> WebhookRegistration | None:
        session = self._get_session()
        try:
            return session.get(WebhookRegistration, webhook_id)
        finally:
            session.close()