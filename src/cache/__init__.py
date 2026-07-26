"""Módulo de Cache — Fase 14.

Cache em memória com TTL, invalidação e decorator @cached.
Pronto para migração para Redis no deploy em produção.

Uso:
    from src.cache import CacheService, cached, get_cache

    svc = get_cache()
    svc.set("key", value, ttl=300)  # 5 minutos
    value = svc.get("key")

    @cached(ttl=60)
    def query_pesada(ecd_id):
        ...
"""

import functools
import hashlib
import json
import logging
import threading
import time
from typing import Any, Callable

logger = logging.getLogger("sped-hub.cache")


class CacheEntry:
    """Entrada individual do cache com TTL."""

    def __init__(self, value: Any, ttl: int):
        self.value = value
        self.expires_at = time.monotonic() + ttl

    @property
    def expired(self) -> bool:
        return time.monotonic() > self.expires_at


class CacheService:
    """Cache em memória thread-safe com TTL e estatísticas.

    Características:
    - Thread-safe via threading.Lock
    - TTL por entrada (expiração automática)
    - Estatísticas: hits, misses, sets, evictions
    - Limpeza periódica de entradas expiradas
    - Invalidação por prefixo (ex: "kpi:*")
    """

    def __init__(self, max_entries: int = 10000, cleanup_interval: int = 300):
        self._cache: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._cleanup_interval = cleanup_interval
        self._last_cleanup = time.monotonic()

        # Estatísticas
        self.hits = 0
        self.misses = 0
        self.sets = 0
        self.evictions = 0

    def get(self, key: str) -> Any | None:
        """Obtém valor do cache. Retorna None se não encontrado ou expirado."""
        self._maybe_cleanup()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self.misses += 1
                return None
            if entry.expired:
                del self._cache[key]
                self.evictions += 1
                self.misses += 1
                return None
            self.hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl: int = 300):
        """Armazena valor no cache com TTL em segundos."""
        self._maybe_cleanup()
        with self._lock:
            # Se atingiu o limite, remove a entrada mais antiga
            if len(self._cache) >= self._max_entries:
                oldest_key = min(
                    self._cache.keys(),
                    key=lambda k: self._cache[k].expires_at,
                )
                del self._cache[oldest_key]
                self.evictions += 1

            self._cache[key] = CacheEntry(value, ttl)
            self.sets += 1

    def delete(self, key: str) -> bool:
        """Remove uma entrada específica."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def invalidate_prefix(self, prefix: str) -> int:
        """Invalida todas as entradas que começam com o prefixo."""
        count = 0
        with self._lock:
            keys_to_delete = [k for k in self._cache if k.startswith(prefix)]
            for k in keys_to_delete:
                del self._cache[k]
                count += 1
        if count:
            logger.debug("Cache: invalidado prefixo '%s' (%d entradas)", prefix, count)
        return count

    def clear(self):
        """Limpa todo o cache."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            logger.info("Cache: limpo (%d entradas)", count)
            return count

    def stats(self) -> dict:
        """Retorna estatísticas do cache."""
        with self._lock:
            total = self.hits + self.misses
            hit_rate = round(self.hits / total * 100, 1) if total > 0 else 0.0
            return {
                "entries": len(self._cache),
                "max_entries": self._max_entries,
                "hits": self.hits,
                "misses": self.misses,
                "sets": self.sets,
                "evictions": self.evictions,
                "hit_rate_pct": hit_rate,
            }

    def _maybe_cleanup(self):
        """Executa limpeza periódica de entradas expiradas."""
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now

        with self._lock:
            expired_keys = [k for k, v in self._cache.items() if v.expired]
            for k in expired_keys:
                del self._cache[k]
                self.evictions += 1
            if expired_keys:
                logger.debug("Cache: %d entradas expiradas removidas", len(expired_keys))


# Instância global
_cache_service: CacheService | None = None


def init_cache(max_entries: int = 10000) -> CacheService:
    global _cache_service
    _cache_service = CacheService(max_entries=max_entries)
    return _cache_service


def get_cache() -> CacheService:
    global _cache_service
    if _cache_service is None:
        return init_cache()
    return _cache_service


def cached(ttl: int = 300, prefix: str = ""):
    """Decorator que cacheia o resultado de uma função.

    A chave é gerada a partir do nome da função + args/kwargs (hash SHA-256).

    Args:
        ttl: Tempo de vida em segundos (default: 5 minutos)
        prefix: Prefixo opcional para a chave (ex: "kpi:")

    Exemplo:
        @cached(ttl=60, prefix="dashboard:")
        def get_kpis(ecd_id: int) -> dict:
            ...
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            cache = get_cache()

            # Gera chave única
            key_parts = [prefix, func.__name__]
            key_parts.append(json.dumps(args, default=str))
            key_parts.append(json.dumps(kwargs, default=str, sort_keys=True))
            key_raw = "|".join(key_parts)
            key = hashlib.sha256(key_raw.encode()).hexdigest()[:32]

            # Tenta cache
            cached_value = cache.get(key)
            if cached_value is not None:
                return cached_value

            # Executa e cacheia
            result = func(*args, **kwargs)
            cache.set(key, result, ttl=ttl)
            return result

        return wrapper
    return decorator