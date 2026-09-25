"""Redis Cache Service — Fase 15.

Cache distribuído com Redis, com fallback automático para memória.
Mesma interface do CacheService para troca transparente.

Uso:
    from src.cache.redis_cache import RedisCacheService

    cache = RedisCacheService(redis_url="redis://localhost:6379/0")
    cache.set("key", value, ttl=300)
    value = cache.get("key")
"""

import json
import logging
import threading
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger("sped-hub.cache.redis")


def url_sem_senha(url: str) -> str:
    """``redis://:segredo@redis:6379/0`` → ``redis://:***@redis:6379/0``.

    Para log e mensagem.  A URL do Redis carrega a senha, e ela era logada
    inteira a cada conexão — em toda subida e, pelo `/api/health/full`, a
    cada requisição anônima.  O filtro de log também mascara URL com senha;
    isto aqui é para a senha nem chegar ao logger.
    """
    try:
        partes = urlsplit(url)
        if partes.password is None:
            return url
        usuario = partes.username or ""
        destino = partes.netloc.rsplit("@", 1)[1]
        return urlunsplit(partes._replace(netloc=f"{usuario}:***@{destino}"))
    except ValueError:
        return "<url do redis ilegível>"


class RedisCacheService:
    """Cache com Redis + fallback para memória.

    Tenta conectar ao Redis; se indisponível, usa cache em memória
    com a mesma interface. Ideal para deploy progressivo.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        max_entries: int = 10000,
        prefix: str = "sped:",
    ):
        self._redis_url = redis_url
        self._prefix = prefix
        self._redis = None
        self._redis_available = False

        # Fallback em memória
        self._memory: dict[str, tuple[Any, float]] = {}
        self._max_entries = max_entries

        # Estatísticas
        self.hits = 0
        self.misses = 0
        self.sets = 0
        self.evictions = 0

        self._connect()

    def _connect(self):
        """Tenta conectar ao Redis."""
        try:
            import redis as redis_lib

            self._redis = redis_lib.from_url(
                self._redis_url,
                socket_connect_timeout=2,
                socket_timeout=2,
                decode_responses=False,
            )
            self._redis.ping()
            self._redis_available = True
            logger.info("Redis conectado: %s", url_sem_senha(self._redis_url))
        except Exception as e:
            self._redis_available = False
            logger.warning("Redis indisponível (%s) — usando cache em memória", e)

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def get(self, key: str) -> Any | None:
        """Obtém valor do cache."""
        if self._redis_available and self._redis:
            try:
                raw = self._redis.get(self._key(key))
                if raw is None:
                    self.misses += 1
                    return None
                self.hits += 1
                return json.loads(raw)
            except Exception:
                self._redis_available = False
                logger.warning("Redis falhou — fallback para memória")

        # Fallback memória
        entry = self._memory.get(key)
        if entry is None:
            self.misses += 1
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            del self._memory[key]
            self.evictions += 1
            self.misses += 1
            return None
        self.hits += 1
        return value

    def set(self, key: str, value: Any, ttl: int = 300):
        """Armazena valor no cache."""
        if self._redis_available and self._redis:
            try:
                self._redis.setex(
                    self._key(key),
                    ttl,
                    json.dumps(value, default=str),
                )
                self.sets += 1
                return
            except Exception:
                self._redis_available = False
                logger.warning("Redis falhou — fallback para memória")

        # Fallback memória
        if len(self._memory) >= self._max_entries:
            oldest = min(self._memory.keys(), key=lambda k: self._memory[k][1])
            del self._memory[oldest]
            self.evictions += 1
        self._memory[key] = (value, time.monotonic() + ttl)
        self.sets += 1

    def delete(self, key: str) -> bool:
        """Remove uma entrada."""
        deleted = False
        if self._redis_available and self._redis:
            try:
                deleted = self._redis.delete(self._key(key)) > 0
            except Exception:
                self._redis_available = False

        if key in self._memory:
            del self._memory[key]
            deleted = True
        return deleted

    def invalidate_prefix(self, prefix: str) -> int:
        """Invalida entradas por prefixo."""
        count = 0
        full_prefix = f"{self._prefix}{prefix}"

        if self._redis_available and self._redis:
            try:
                cursor = 0
                while True:
                    cursor, keys = self._redis.scan(cursor, match=f"{full_prefix}*", count=100)
                    if keys:
                        self._redis.delete(*keys)
                        count += len(keys)
                    if cursor == 0:
                        break
            except Exception:
                self._redis_available = False

        # Fallback memória
        mem_keys = [k for k in self._memory if k.startswith(prefix)]
        for k in mem_keys:
            del self._memory[k]
            count += 1

        if count:
            logger.debug("Cache: invalidado prefixo '%s' (%d entradas)", prefix, count)
        return count

    def clear(self) -> int:
        """Limpa todo o cache."""
        count = 0
        if self._redis_available and self._redis:
            try:
                cursor = 0
                while True:
                    cursor, keys = self._redis.scan(cursor, match=f"{self._prefix}*", count=100)
                    if keys:
                        self._redis.delete(*keys)
                        count += len(keys)
                    if cursor == 0:
                        break
            except Exception:
                self._redis_available = False

        count += len(self._memory)
        self._memory.clear()
        logger.info("Cache: limpo (%d entradas)", count)
        return count

    def stats(self) -> dict:
        """Retorna estatísticas do cache."""
        total = self.hits + self.misses
        hit_rate = round(self.hits / total * 100, 1) if total > 0 else 0.0
        return {
            "entries": len(self._memory),
            "max_entries": self._max_entries,
            "hits": self.hits,
            "misses": self.misses,
            "sets": self.sets,
            "evictions": self.evictions,
            "hit_rate_pct": hit_rate,
            "backend": "redis" if self._redis_available else "memory",
        }


# ── Instância compartilhada ────────────────────────────────────────────────

#: Intervalo mínimo entre duas tentativas de reconexão de uma instância
#: compartilhada que está no fallback de memória.
RECONEXAO_SEGUNDOS = 30.0

_compartilhados: dict[tuple[str, str], tuple[RedisCacheService, float]] = {}
_trava_compartilhados = threading.Lock()


def cache_compartilhado(redis_url: str, prefix: str = "sped:") -> RedisCacheService:
    """Instância reaproveitada por ``(redis_url, prefix)``.

    Construir `RedisCacheService` conecta e faz `ping` com timeout de 2 s.  O
    `/api/health/full` — público — construía uma a cada requisição: com o
    Redis fora do ar, cada chamada anônima prendia 2 s de conexão.  Aqui a
    instância é criada uma vez e reaproveitada.

    Como o fallback de uma instância é definitivo (ela nunca reconecta), a
    que estiver em memória é trocada por uma nova — no máximo uma tentativa a
    cada `RECONEXAO_SEGUNDOS` —, para o Redis que volta ser percebido.
    """
    chave = (redis_url, prefix)
    with _trava_compartilhados:
        existente = _compartilhados.get(chave)
        agora = time.monotonic()
        if existente is not None:
            servico, criado_em = existente
            if servico._redis_available or agora - criado_em < RECONEXAO_SEGUNDOS:
                return servico
        servico = RedisCacheService(redis_url=redis_url, prefix=prefix)
        _compartilhados[chave] = (servico, agora)
        return servico
