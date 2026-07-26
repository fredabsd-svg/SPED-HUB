"""Rate Limiting — Fase 13.

Sistema de rate limiting para API Keys com janela deslizante.

Funcionamento:
  - Cada API Key pode ter uma configuração de rate limit (limite/janela)
  - Sem configuração, usa o default global (100 req/60s)
  - Contagem em memória (sliding window) com reset automático
  - Headers X-RateLimit-* nas respostas
  - HTTP 429 quando excede o limite

Uso:
  from src.ratelimit import RateLimiter, RateLimitMiddleware

  # Middleware ASGI
  app.add_middleware(RateLimitMiddleware, db_path="sped_hub.db")

  # Ou verificação manual
  limiter = RateLimiter(db_path)
  allowed, info = limiter.verificar(api_key_id)
  if not allowed:
      raise HTTPException(429, "Rate limit exceeded")
"""

import datetime
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import RateLimitConfig, criar_engine, get_session, init_db

logger = logging.getLogger("sped-hub.ratelimit")

# Defaults globais
DEFAULT_LIMITE = 100  # requisições por janela
DEFAULT_JANELA = 60   # segundos


@dataclass
class RateLimitInfo:
    """Informações de rate limit para uma API Key."""
    limite: int = DEFAULT_LIMITE
    janela: int = DEFAULT_JANELA
    requisicoes_restantes: int = DEFAULT_LIMITE
    reset_em: int = 0  # segundos até reset
    limite_excedido: bool = False


class RateLimiter:
    """Rate limiter com janela deslizante em memória.

    Mantém um contador por API Key que reseta a cada janela.
    Thread-safe via lock.
    """

    def __init__(self, db_path: str = "sped_hub.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        # Estrutura: {api_key_id: {"count": int, "window_start": float}}
        self._counters: dict[int, dict] = {}

    def _get_config(self, api_key_id: int) -> tuple[int, int]:
        """Busca configuração de rate limit para a API Key.

        Returns:
            (limite, janela_em_segundos)
        """
        engine = criar_engine(self.db_path)
        init_db(engine)
        session = get_session(engine)
        try:
            config = session.execute(
                select(RateLimitConfig).where(RateLimitConfig.api_key_id == api_key_id)
            ).scalar_one_or_none()
            if config:
                return config.limite, config.janela
            return DEFAULT_LIMITE, DEFAULT_JANELA
        finally:
            session.close()

    def verificar(self, api_key_id: int) -> tuple[bool, RateLimitInfo]:
        """Verifica se a requisição é permitida e incrementa o contador.

        Args:
            api_key_id: ID da API Key

        Returns:
            (permitido, info) — permitido=True se dentro do limite
        """
        limite, janela = self._get_config(api_key_id)
        agora = time.monotonic()

        with self._lock:
            estado = self._counters.get(api_key_id)

            if estado is None:
                # Primeira requisição
                self._counters[api_key_id] = {
                    "count": 1,
                    "window_start": agora,
                }
                return True, RateLimitInfo(
                    limite=limite,
                    janela=janela,
                    requisicoes_restantes=limite - 1,
                    reset_em=int(janela),
                    limite_excedido=False,
                )

            # Verifica se a janela expirou
            elapsed = agora - estado["window_start"]
            if elapsed >= janela:
                # Reset da janela
                estado["count"] = 1
                estado["window_start"] = agora
                return True, RateLimitInfo(
                    limite=limite,
                    janela=janela,
                    requisicoes_restantes=limite - 1,
                    reset_em=int(janela),
                    limite_excedido=False,
                )

            # Dentro da janela — incrementa
            restante = limite - estado["count"]
            reset_em = int(janela - elapsed)

            if estado["count"] >= limite:
                # Limite excedido
                return False, RateLimitInfo(
                    limite=limite,
                    janela=janela,
                    requisicoes_restantes=0,
                    reset_em=reset_em,
                    limite_excedido=True,
                )

            estado["count"] += 1
            return True, RateLimitInfo(
                limite=limite,
                janela=janela,
                requisicoes_restantes=restante - 1,
                reset_em=reset_em,
                limite_excedido=False,
            )

    def reset(self, api_key_id: int | None = None):
        """Reseta contadores (para testes ou admin)."""
        with self._lock:
            if api_key_id is not None:
                self._counters.pop(api_key_id, None)
            else:
                self._counters.clear()

    def get_info(self, api_key_id: int) -> RateLimitInfo:
        """Retorna info atual sem incrementar o contador."""
        limite, janela = self._get_config(api_key_id)
        agora = time.monotonic()

        with self._lock:
            estado = self._counters.get(api_key_id)
            if estado is None:
                return RateLimitInfo(
                    limite=limite, janela=janela,
                    requisicoes_restantes=limite, reset_em=int(janela),
                )

            elapsed = agora - estado["window_start"]
            if elapsed >= janela:
                return RateLimitInfo(
                    limite=limite, janela=janela,
                    requisicoes_restantes=limite, reset_em=int(janela),
                )

            restante = max(0, limite - estado["count"])
            return RateLimitInfo(
                limite=limite, janela=janela,
                requisicoes_restantes=restante,
                reset_em=int(janela - elapsed),
                limite_excedido=restante == 0,
            )


# ── Instância global ───────────────────────────────────────────────────────

_limiter: RateLimiter | None = None


def get_limiter(db_path: str = "sped_hub.db") -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(db_path)
    return _limiter


def init_limiter(db_path: str = "sped_hub.db") -> RateLimiter:
    global _limiter
    _limiter = RateLimiter(db_path)
    return _limiter


# ── Serviço de configuração ────────────────────────────────────────────────


class RateLimitService:
    """Serviço para gerenciar configurações de rate limit por API Key."""

    def __init__(self, db_path: str = "sped_hub.db"):
        self.db_path = db_path

    def _get_session(self) -> Session:
        engine = criar_engine(self.db_path)
        init_db(engine)
        return get_session(engine)

    def configurar(self, api_key_id: int, limite: int, janela: int) -> dict:
        """Configura ou atualiza o rate limit de uma API Key.

        Args:
            api_key_id: ID da API Key
            limite: Número máximo de requisições por janela
            janela: Duração da janela em segundos

        Returns:
            dict com a configuração salva
        """
        if limite < 1:
            raise ValueError("Limite deve ser >= 1")
        if janela < 1:
            raise ValueError("Janela deve ser >= 1")

        session = self._get_session()
        try:
            config = session.execute(
                select(RateLimitConfig).where(RateLimitConfig.api_key_id == api_key_id)
            ).scalar_one_or_none()

            if config:
                config.limite = limite
                config.janela = janela
                config.atualizado_em = datetime.datetime.now(datetime.UTC)
            else:
                config = RateLimitConfig(
                    api_key_id=api_key_id,
                    limite=limite,
                    janela=janela,
                )
                session.add(config)

            session.commit()
            session.refresh(config)

            # Reseta o contador em memória
            get_limiter(self.db_path).reset(api_key_id)

            return {
                "id": config.id,
                "api_key_id": config.api_key_id,
                "limite": config.limite,
                "janela": config.janela,
                "criado_em": config.criado_em.isoformat() if config.criado_em else None,
                "atualizado_em": config.atualizado_em.isoformat() if config.atualizado_em else None,
            }
        finally:
            session.close()

    def obter(self, api_key_id: int) -> dict | None:
        """Obtém a configuração de rate limit de uma API Key."""
        session = self._get_session()
        try:
            config = session.execute(
                select(RateLimitConfig).where(RateLimitConfig.api_key_id == api_key_id)
            ).scalar_one_or_none()

            if not config:
                return None

            return {
                "id": config.id,
                "api_key_id": config.api_key_id,
                "limite": config.limite,
                "janela": config.janela,
                "criado_em": config.criado_em.isoformat() if config.criado_em else None,
                "atualizado_em": config.atualizado_em.isoformat() if config.atualizado_em else None,
            }
        finally:
            session.close()

    def remover(self, api_key_id: int) -> bool:
        """Remove a configuração de rate limit (volta ao default)."""
        session = self._get_session()
        try:
            config = session.execute(
                select(RateLimitConfig).where(RateLimitConfig.api_key_id == api_key_id)
            ).scalar_one_or_none()

            if not config:
                return False

            session.delete(config)
            session.commit()
            get_limiter(self.db_path).reset(api_key_id)
            return True
        finally:
            session.close()

    def listar(self) -> list[dict]:
        """Lista todas as configurações de rate limit."""
        session = self._get_session()
        try:
            configs = session.execute(
                select(RateLimitConfig).order_by(RateLimitConfig.api_key_id)
            ).scalars().all()

            return [
                {
                    "id": c.id,
                    "api_key_id": c.api_key_id,
                    "limite": c.limite,
                    "janela": c.janela,
                    "criado_em": c.criado_em.isoformat() if c.criado_em else None,
                    "atualizado_em": c.atualizado_em.isoformat() if c.atualizado_em else None,
                }
                for c in configs
            ]
        finally:
            session.close()
