# monitoring

## O que faz

Observabilidade em processo: coleta cada requisição HTTP do dashboard
(método, path normalizado, status, latência) numa janela móvel em memória e
monta um snapshot operacional agregando banco (empresas, ECDs, jobs,
webhooks falhos, auditoria), cache, fila de workers, email e o próprio
processo (RSS, threads). O snapshot alimenta a página `/monitoring` e a rota
`/api/monitoring/summary`, ambas restritas a administradores.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `RequestMetric` | Dataclass congelada de um evento HTTP. |
| `MetricsCollector(max_events=20_000)` | Coletor thread-safe em `deque`; `record`, `snapshot(minutes)`, `reset`. |
| `metrics_collector` | Instância global usada pelo middleware do dashboard. |
| `normalize_path(path)` | Troca segmentos numéricos por `{id}` e hex longos por `{token}`. |
| `percentile(values, fraction)` | Nearest-rank sobre lista ordenada. |
| `build_operational_snapshot(collector, *, db_path, minutes=60)` | Snapshot completo: `http`, `database`, `cache`, `email`, `workers`, `process`, `version`. |

## Depende de / quem depende

Depende de `db.models` (contagens), `version`; imports tardios de `cache`,
`email_service` e `worker_queue` dentro do snapshot (evitam ciclo); stdlib
(`resource`, `threading`, `collections`).

Consumido por `dashboard.app` — o middleware chama
`metrics_collector.record` em toda requisição, e as rotas `/monitoring`,
`/api/monitoring/summary` e `/api/monitoring/reset` usam o snapshot.

## Decisões não óbvias e armadilhas

- **Privacidade por construção.** `normalize_path` reduz cardinalidade e
  evita registrar identificadores: segmento só de dígitos vira `{id}`,
  segmento hex com 16+ caracteres vira `{token}`. O middleware grava apenas
  o path — nunca query string nem payload.
- **Retenção tem dois tetos e nenhum vem da setting.** A janela do
  `snapshot` é limitada a 24 h e o `deque` guarda no máximo 20.000 eventos —
  sob tráfego alto, a janela efetiva encolhe em silêncio.
  **`SPED_HUB_MONITORING_RETENTION_HOURS` existe em settings e o coletor não
  a lê** — violação da §2.2, registrada em `docs/status.md`.
- **Tudo em memória, por processo.** Reinício zera as métricas HTTP;
  réplicas têm coletores independentes. `reset` limpa só a janela HTTP — não
  remove dados de negócio.
- **`ru_maxrss` muda de unidade por plataforma.** Linux reporta KiB, macOS
  bytes; o código multiplica por 1024 só em Linux (o alvo de deploy é
  Linux).
- **Snapshot não derruba com banco quebrado**: captura exceção e devolve
  `{"status": "error"}`; a engine é descartada com `dispose()` a cada
  chamada. O tamanho do banco soma os arquivos `-wal`/`-shm` do SQLite.
- **Acesso é de admin.** Anônimo: redirect/401; usuário comum: 403.
- Fila não inicializada aparece como `"not_initialized"` em vez de erro.

## Como testar isoladamente

```bash
pytest tests/test_fase16.py -q                     # coletor, normalize_path, snapshot, permissões
pytest tests/test_fase16.py -k MetricsCollector -q # só o coletor, sem TestClient
```

## O que não faz

- Não exporta Prometheus/OpenTelemetry — o snapshot é JSON próprio.
- Não persiste métricas: nada vai ao banco.
- Não agrega entre processos ou réplicas.
- Não alerta: coleta e expõe; quem reage é o operador.
- Não mede tempo de queries nem instrumenta o worker por dentro — do banco
  só ficam contagens.
