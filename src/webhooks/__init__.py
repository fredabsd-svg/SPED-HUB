"""Módulo de Webhooks — Fase 10.

Sistema de notificação para integração com sistemas de terceiros.
Permite registrar endpoints que recebem POST em eventos do SPED-HUB.

Eventos suportados:
  - ecd.importada      — Nova ECD importada
  - ecd.validada       — Validação de integridade concluída
  - relatorio.gerado   — Relatório contábil gerado
"""

import datetime
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import WebhookRegistration, criar_engine, get_session, init_db

logger = logging.getLogger("sped-hub.webhooks")

EVENTOS_DISPONIVEIS = [
    "ecd.importada",
    "ecd.validada",
    "relatorio.gerado",
]


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
    ) -> WebhookRegistration:
        """Registra um novo webhook."""
        session = self._get_session()
        try:
            wh = WebhookRegistration(
                url=url,
                eventos=json.dumps(eventos),
                secret=secret,
                descricao=descricao,
                ativo=ativo,
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
    ) -> WebhookRegistration | None:
        """Atualiza um webhook existente."""
        session = self._get_session()
        try:
            wh = session.get(WebhookRegistration, webhook_id)
            if not wh:
                return None
            if url is not None:
                wh.url = url
            if eventos is not None:
                wh.eventos = json.dumps(eventos)
            if secret is not None:
                wh.secret = secret
            if descricao is not None:
                wh.descricao = descricao
            if ativo is not None:
                wh.ativo = ativo
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

    async def dispatch(self, evento: WebhookEvent) -> dict[str, int]:
        """Dispara evento para todos os webhooks inscritos.

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

            try:
                await self._enviar(wh, evento)
                sucessos += 1
            except Exception as e:
                logger.warning(
                    "Falha ao enviar webhook %s para %s: %s",
                    wh.id, wh.url, e,
                )
                falhas += 1

        return {"sucessos": sucessos, "falhas": falhas}

    async def _enviar(self, wh: WebhookRegistration, evento: WebhookEvent):
        """Envia requisição POST para o endpoint do webhook."""
        payload = {
            "evento": evento.tipo,
            "dados": evento.dados,
            "timestamp": evento.timestamp,
            "webhook_id": wh.id,
        }

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "SPED-HUB-Webhook/1.0",
        }

        if wh.secret:
            import hashlib
            import hmac

            body = json.dumps(payload).encode()
            signature = hmac.new(
                wh.secret.encode(), body, hashlib.sha256
            ).hexdigest()
            headers["X-SPED-HUB-Signature"] = signature

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(wh.url, json=payload, headers=headers)
            response.raise_for_status()

        # Atualiza último envio
        session = self._get_session()
        try:
            wh_db = session.get(WebhookRegistration, wh.id)
            if wh_db:
                wh_db.ultimo_envio = datetime.datetime.now(datetime.UTC)
                wh_db.total_envios = (wh_db.total_envios or 0) + 1
                session.commit()
        finally:
            session.close()