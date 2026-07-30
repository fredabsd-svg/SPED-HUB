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
| `MetricsCollector(max_events=20_000, retention_hours=None)` | Coletor thread-safe em `deque`; `record`, `snapshot(minutes=None)`, `reset`. `retention_hours=None` lê das settings a cada uso. |
| `MetricsCollector.retention_hours` | Retenção efetiva em horas: o valor fixado no construtor ou o configurado. |
| `janela_padrao_minutos()` | Janela padrão das métricas, de `SPED_HUB_METRICS_WINDOW_MINUTES`. |
| `metrics_collector` | Instância global usada pelo middleware do dashboard. |
| `normalize_path(path)` | Troca segmentos numéricos por `{id}` e hex longos por `{token}`. |
| `percentile(values, fraction)` | Nearest-rank sobre lista ordenada. |
| `build_operational_snapshot(collector, *, db_path, minutes=None)` | Snapshot completo: `http`, `database`, `cache`, `email`, `workers`, `process`, `version`. |

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
- **Retenção tem dois tetos, e um deles é configurável.** A janela do
  `snapshot` é limitada por `SPED_HUB_MONITORING_RETENTION_HOURS` (24 h por
  omissão) e o `deque` guarda no máximo 20.000 eventos — sob tráfego alto é o
  teto de eventos que encolhe a janela efetiva, em silêncio. Antes a retenção
  era 24 h fixas no código e a variável não tinha consumidor (§2.2).
- **A configuração é lida no uso, não no `__init__`.** `metrics_collector` é
  criado no import do módulo; resolver retenção ou janela padrão ali
  congelaria o valor para o processo inteiro — é o defeito que o
  `worker_runner` carrega. Por isso `retention_hours` é `@property` e
  `janela_padrao_minutos()` é função. Passar `retention_hours=` no construtor
  fixa o valor de propósito (teste).
- **`SPED_HUB_METRICS_WINDOW_MINUTES` é só o padrão.** Quem passa `minutes`
  (a query string de `/api/monitoring/summary`, o select da página) manda; a
  retenção acima limita os dois. O default da rota é `None`, não um literal:
  um literal seria avaliado no import e ignoraria a configuração.
- **`build_operational_snapshot` resolve a janela uma vez** e passa o mesmo
  valor para as métricas HTTP e as do banco — senão o painel compararia
  períodos diferentes lado a lado.
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
