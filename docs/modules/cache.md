# cache

## O que faz

Fornece cache chave-valor com TTL em duas implementações de mesma interface:
`CacheService` (`__init__.py`), em memória, thread-safe, com estatísticas e o
decorator `@cached`; e `RedisCacheService` (`redis_cache.py`), que tenta o
Redis e, se indisponível, cai automaticamente para um dicionário em memória.
Ambas expõem `get`, `set`, `delete`, `invalidate_prefix`, `clear` e `stats`.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `CacheEntry` | Entrada com `expires_at` em `time.monotonic()`. |
| `CacheService(max_entries=10000, cleanup_interval=300)` | Cache em memória com lock, TTL por entrada, limpeza periódica e contadores (hits, misses, sets, evictions). |
| `init_cache(max_entries)` / `get_cache()` | Instância global do `CacheService`. |
| `cached(ttl=300, prefix="")` | Decorator: chave = SHA-256 de nome da função + args/kwargs, truncado a 32 hex, com prefixo. |
| `RedisCacheService(redis_url, max_entries, prefix="sped:")` | Redis com fallback para memória; `stats()` inclui `"backend": "redis"` ou `"memory"`. |

## Depende de / quem depende

`CacheService` usa só a stdlib. `RedisCacheService` importa `redis` dentro de
`_connect` — a lib pode nem estar instalada que o fallback assume.

Consumido por: `dashboard.app` (`/api/cache/stats`, `/api/redis/cache/stats`,
`/api/health/full`), `monitoring` (snapshot) e `worker_runner` (instancia
`RedisCacheService` com prefixo `worker:`).

## Decisões não óbvias e armadilhas

- **O fallback é definitivo dentro da instância.** Qualquer exceção do Redis
  derruba `_redis_available` para `False` e a instância nunca tenta
  reconectar. Volta do Redis só com instância nova. Os testes rodam com Redis
  fora do ar de propósito — o CI não tem Redis.
- **Redis quebrado não derruba nada**: a conexão usa timeout de 2 s e o
  construtor apenas loga warning. É o desenho para deploy progressivo.
- **Os dois lados do fallback não são espelhos.** O fallback em memória do
  `RedisCacheService` não tem lock (o `CacheService` tem), e os valores
  passam por JSON (`default=str`): tuplas viram listas, objetos viram string.
  No `CacheService` o valor é guardado como está.
- **Eviction por limite não é FIFO nem LRU**: remove a entrada com menor
  `expires_at` — a mais próxima de expirar. Com TTLs heterogêneos, uma
  entrada recém-gravada com TTL curto sai antes de uma antiga com TTL longo.
- **`@cached` não cacheia `None`**: `get` devolve `None` tanto para miss
  quanto para valor ausente, então função que retorna `None` executa sempre.
- **Invalidação do `@cached` só funciona pelo prefixo.** A chave é um hash:
  não dá para deletar uma entrada específica por nome.
  `tests/test_review_regressions.py` fixa que o prefixo sobrevive na chave
  final, para `invalidate_prefix("dashboard:")` alcançá-la.
- No `RedisCacheService`, `stats()["entries"]` conta só o dicionário em
  memória, mesmo com Redis ativo.

## Como testar isoladamente

```bash
pytest tests/test_fase14.py -k "Cache or cached" -q            # CacheService e @cached
pytest tests/test_fase15.py -k RedisCacheService -q            # fallback, TTL, prefixo
pytest tests/test_review_regressions.py -k cached_prefix -q    # invalidação por prefixo
```

Os testes de `RedisCacheService` passam sem Redis rodando — é o fallback em
ação.

## O que não faz

- Não compartilha entre processos quando está no fallback de memória: cada
  worker tem o seu.
- Não persiste entre reinícios (fora do Redis).
- Não reconecta ao Redis após falha.
- Não faz LRU verdadeiro nem limita memória por bytes — o limite é por
  número de entradas.
