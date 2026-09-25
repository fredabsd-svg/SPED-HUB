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
import re
import threading
import time
from collections.abc import Callable
from typing import Any

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


# `<pacote.Classe object at 0x7f...>`: a representação padrão de objeto, que
# só diz ONDE ele está na memória.
_REPR_POR_ENDERECO = re.compile(r" at 0x[0-9a-fA-F]+>")


def _parte_estavel(valor: Any) -> Any:
    """Converte, para a chave do `@cached`, um argumento que o JSON não serializa.

    Usa `valor.__cache_key__()` quando o objeto o define.  Sem ele, usa
    `str(valor)` — desde que isso descreva o VALOR, e não o endereço.  Num
    método decorado, `self` entrava na chave como
    `<DashboardService object at 0x7f…>`.  O endereço de um objeto coletado é
    reaproveitado pelo próximo, então o serviço de uma ECD receberia o
    resultado cacheado de outra — número de outra escrituração, e
    possivelmente de outro escritório.  Argumento assim é recusado com
    `TypeError`, em vez de virar chave que colide em silêncio.
    """
    chave = getattr(valor, "__cache_key__", None)
    if callable(chave):
        return [type(valor).__module__, type(valor).__qualname__, chave()]
    texto = str(valor)
    if _REPR_POR_ENDERECO.search(texto):
        raise TypeError(
            f"@cached: argumento do tipo {type(valor).__qualname__} não tem chave estável "
            f"({texto!r} identifica o endereço na memória, que é reaproveitado). "
            "Defina __cache_key__() nele ou não passe o objeto à função cacheada."
        )
    return texto


def cached(ttl: int = 300, prefix: str = ""):
    """Decorator que cacheia o resultado de uma função.

    A chave é gerada a partir do módulo e nome qualificado da função +
    args/kwargs (hash SHA-256).  Argumento que o JSON não serializa entra na
    chave por `__cache_key__()` ou por `str()`; objeto cuja representação é só
    o endereço na memória é recusado (`TypeError`) — ver `_parte_estavel`.

    Args:
        ttl: Tempo de vida em segundos (default: 5 minutos)
        prefix: Prefixo opcional para a chave (ex: "kpi:")

    Exemplo:
        @cached(ttl=60, prefix="dashboard:")
        def get_kpis(ecd_id: int) -> dict:
            ...

        class Servico:
            def __cache_key__(self):
                return self.ecd_id

            @cached(ttl=60)
            def total(self): ...
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            cache = get_cache()

            # Gera chave única.  Módulo e nome qualificado, não só `__name__`:
            # duas funções `total` em classes ou módulos diferentes não podem
            # dividir entrada.
            key_parts = [prefix, func.__module__, func.__qualname__]
            key_parts.append(json.dumps(args, default=_parte_estavel))
            key_parts.append(json.dumps(kwargs, default=_parte_estavel, sort_keys=True))
            key_raw = "|".join(key_parts)
            digest = hashlib.sha256(key_raw.encode()).hexdigest()[:32]
            key = f"{prefix}{digest}"

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
