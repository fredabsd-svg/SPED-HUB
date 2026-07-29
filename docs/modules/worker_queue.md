# worker_queue

## O que faz

Fila de processamento em `multiprocessing`: um pool de processos-worker
consome tasks de uma `Queue`, executa o handler registrado para o tipo da
task e devolve eventos de progresso e resultado por uma fila de resultados.
Faz retry com backoff exponencial (1 s, 2 s, 4 s), shutdown graceful com
poison pills e expõe status e contadores por task. Em produção é consumida
pelo `worker_runner`, num processo separado do web.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `TaskStatus` | Enum: `queued`, `running`, `completed`, `failed`, `retrying`. |
| `Task` | Dataclass da task (id, tipo, payload, status, progresso, tentativas, timestamps); `to_dict()` para API. |
| `WorkerQueue(num_workers=4, max_queue_size=100, db_path=None)` | `register_handler`, `on_complete`, `on_failure`, `start`, `shutdown`, `enqueue`, `status`, `list_tasks`, `pending_count`, `active_count`, `process_results`. |
| `init_worker_queue(...)` / `get_worker_queue()` | Instância global. `get_worker_queue()` retorna `None` se ninguém inicializou. |
| `enqueue(tipo, payload)` | Atalho para a fila global; `RuntimeError` se não inicializada. |

## Depende de / quem depende

Depende de `src.settings` (`database_reference` para o default de `db_path`)
e da stdlib (`multiprocessing`, `signal`, `queue`).

Consumido por: `worker_runner` (o único que chama `init_worker_queue` e
`start`), `dashboard.app` (`/api/worker/status` e `/api/health/full`, só
leitura) e `monitoring` (snapshot).

## Decisões não óbvias e armadilhas

- **O upload assíncrono do dashboard NÃO passa por aqui.**
  `POST /api/upload-async` usa `threading.Thread` no processo web. No web
  ninguém chama `init_worker_queue`, então `get_worker_queue()` retorna
  `None` e `/api/worker/status` responde `not_initialized`. A fila só existe
  dentro do `worker_runner`.
- **Nada acontece com o estado sem `process_results()`.** Os workers só
  empurram eventos para a fila de resultados; é o processo principal que
  precisa drenar em loop (o `worker_runner` drena a cada 0,5 s). Sem isso,
  `status()` congela em `queued` e callbacks nunca disparam.
- **Registre handlers antes de `start()`.** Os workers recebem uma cópia de
  `self` (com `_handlers`) na criação do processo; handler registrado depois
  não chega aos workers já vivos. Task sem handler falha com "Handler não
  registrado".
- **O callback de progresso vem de uma factory** para fixar a task no escopo
  — closure definida no laço capturaria a variável de iteração e reportaria
  progresso da task errada. O comentário no código documenta o defeito
  evitado; `tests/test_review_regressions.py` garante o progresso visível
  durante `running`.
- **Retry bloqueia o worker**: o `time.sleep(delay)` do backoff acontece
  dentro do worker antes de reenfileirar; com 1 worker, ninguém mais processa
  durante a espera.
- **Eventos viajam como cópias** (`replace(task)`) — o dict `_tasks` do
  processo principal é sobrescrito pelo último evento recebido, não
  compartilhado.
- Workers ignoram SIGINT (o principal gerencia); shutdown envia um `None` por
  worker e, se o join estourar o timeout, aplica `terminate()`.
- `db_path` é aceito e armazenado, mas a fila em si não o usa — quem abre
  banco é o handler.

## Como testar isoladamente

```bash
pytest tests/test_fase15.py -k WorkerQueue -q               # enqueue, retry, shutdown
pytest tests/test_review_regressions.py -k worker -q        # progresso durante running
```

Os testes toleram lentidão de CI: subir processo num runner de 2 núcleos
leva bem mais que numa máquina ociosa.

## O que não faz

- Não persiste a fila: tasks e resultados vivem em memória. Restart perde
  tudo que estava enfileirado ou rodando.
- Não coordena réplicas: cada processo que criar uma `WorkerQueue` tem a
  sua, isolada.
- Não tem prioridade, agendamento nem deduplicação de tasks.
- Não limita tempo de execução de handler (sem timeout por task).
- Não grava nada em banco — o tracking em `async_jobs` é outro mecanismo,
  não integrado a esta fila.
