"""Rate Limiting — Fase 13.

Sistema de rate limiting para API Keys com janela deslizante.

Funcionamento:
  - Cada API Key pode ter uma configuração de rate limit (limite/janela)
  - Sem configuração, usa o default global
    (``SPED_HUB_RATE_LIMIT_DEFAULT`` req / ``SPED_HUB_RATE_LIMIT_WINDOW`` s,
    por omissão 100 req/60 s)
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
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import RateLimitConfig, criar_engine, get_session, init_db

logger = logging.getLogger("sped-hub.ratelimit")

# Defaults globais de último recurso.  O valor que vale em runtime vem de
# ``SPED_HUB_RATE_LIMIT_DEFAULT`` / ``SPED_HUB_RATE_LIMIT_WINDOW`` via
# :func:`limite_padrao` — estas constantes só cobrem os campos do dataclass,
# que são sobrescritos em todo caminho real.
DEFAULT_LIMITE = 100  # requisições por janela
DEFAULT_JANELA = 60  # segundos


def limite_padrao() -> tuple[int, int]:
    """Cota global para API Key sem configuração própria: ``(limite, janela)``.

    Lido a cada chamada, não no import: o limiter global é criado junto com a
    aplicação e resolver configuração no import congelaria o valor para o
    processo inteiro.  Antes as constantes acima eram usadas direto e
    ``SPED_HUB_RATE_LIMIT_DEFAULT`` não tinha consumidor — quem a configurava
    não mudava cota nenhuma (§2.2).
    """
    from src.settings import get_settings

    cfg = get_settings()
    return max(1, cfg.rate_limit_default), max(1, cfg.rate_limit_window_seconds)


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
            return limite_padrao()
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
                    limite=limite,
                    janela=janela,
                    requisicoes_restantes=limite,
                    reset_em=int(janela),
                )

            elapsed = agora - estado["window_start"]
            if elapsed >= janela:
                return RateLimitInfo(
                    limite=limite,
                    janela=janela,
                    requisicoes_restantes=limite,
                    reset_em=int(janela),
                )

            restante = max(0, limite - estado["count"])
            return RateLimitInfo(
                limite=limite,
                janela=janela,
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
            configs = (
                session.execute(select(RateLimitConfig).order_by(RateLimitConfig.api_key_id))
                .scalars()
                .all()
            )

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


# ── Rate limit por IP (Fase 17, Etapa 5) ───────────────────────────────────
#
# O limitador acima é por API Key, o que deixa de fora justamente o que mais
# precisa de proteção: `/api/login` e `/api/register` são públicos por
# definição e não têm chave.  Sem limite por IP, tentar senhas em sequência
# custa nada ao atacante.


@dataclass
class IPRateLimitInfo:
    limite: int
    janela: int
    restantes: int
    reset_em: int


class IPRateLimiter:
    """Janela deslizante em memória, por endereço de origem.

    Escopos separados (`login`, `api`) para que uma rajada legítima de
    requisições autenticadas não consuma a cota de tentativas de senha.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, str], dict] = {}

    def verificar(
        self, ip: str, escopo: str, limite: int, janela: int
    ) -> tuple[bool, IPRateLimitInfo]:
        agora = time.monotonic()
        chave = (escopo, ip)
        with self._lock:
            estado = self._counters.get(chave)
            if estado is None or agora - estado["inicio"] >= janela:
                self._counters[chave] = {"count": 1, "inicio": agora}
                return True, IPRateLimitInfo(limite, janela, limite - 1, janela)

            decorrido = agora - estado["inicio"]
            reset_em = max(1, int(janela - decorrido))
            if estado["count"] >= limite:
                return False, IPRateLimitInfo(limite, janela, 0, reset_em)

            estado["count"] += 1
            return True, IPRateLimitInfo(limite, janela, limite - estado["count"], reset_em)

    def reset(self, ip: str | None = None) -> None:
        with self._lock:
            if ip is None:
                self._counters.clear()
            else:
                for chave in [c for c in self._counters if c[1] == ip]:
                    del self._counters[chave]

    def limpar_expirados(self, janela_maxima: int = 3600) -> int:
        """Descarta janelas velhas — sem isto o dicionário cresce com os IPs vistos."""
        agora = time.monotonic()
        with self._lock:
            velhas = [c for c, e in self._counters.items() if agora - e["inicio"] > janela_maxima]
            for chave in velhas:
                del self._counters[chave]
        return len(velhas)


_ip_limiter = IPRateLimiter()


def get_ip_limiter() -> IPRateLimiter:
    return _ip_limiter


def ip_do_request(request) -> str:
    """Endereço de origem, respeitando proxy reverso **apenas se confiável**.

    `X-Forwarded-For` é escrito pelo cliente quando não há proxy à frente:
    confiar nele sem condição transforma o limite por IP em decoração, porque
    o atacante troca o cabeçalho a cada tentativa.  Por isso o cabeçalho só é
    lido quando `SPED_HUB_TRUST_PROXY` está ligado — o que só faz sentido com
    um nginx na frente sobrescrevendo o valor, como no docker-compose.
    """
    from src.settings import get_settings

    direto = request.client.host if request.client else "desconhecido"
    if not get_settings().trust_proxy:
        return direto

    encaminhado = request.headers.get("X-Forwarded-For", "")
    if encaminhado:
        # O primeiro da lista é o cliente original.
        return encaminhado.split(",")[0].strip() or direto
    return request.headers.get("X-Real-IP", "").strip() or direto
