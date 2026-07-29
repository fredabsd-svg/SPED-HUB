# async_jobs

## O que faz

Registra e acompanha jobs assíncronos na tabela `async_jobs` do banco: cria
com status `pending`, atualiza progresso (0–100), conclui, falha ou cancela,
e remove jobs terminados com mais de N horas. É o tracking do upload
assíncrono do dashboard (`POST /api/upload-async` cria o job;
`GET /api/jobs/{id}` faz polling). Mantém ainda dois estados em memória do
processo: um overlay de progresso ao vivo e o registro de tokens de
cancelamento dos jobs em execução.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `JobStatus` | Enum: `pending`, `processing`, `completed`, `failed`, `cancelled`. |
| `JobInfo` | Dataclass de resumo para a API (id, status, progresso, resultado, erro, datas). |
| `AsyncJobService(db_path)` | `criar`, `atualizar_progresso`, `concluir`, `falhar`, `obter`, `listar`, `limpar_antigos`. |
| `registrar_token` / `esquecer_token` / `cancelar` / `marcar_cancelado` | Cancelamento cooperativo: liga o token do job em execução ao pedido que chega por outra requisição. |
| `init_async_job_service(db_path)` / `get_async_job_service(db_path=None)` | Instância global; sem `db_path`, usa `database_reference()`. |

## Depende de / quem depende

Depende de `src.db.models` (modelo `AsyncJob`, engine, sessão) e
`src.settings` (`database_reference`).

Consumido por `dashboard.app` (rotas `/api/upload-async`, `/api/jobs/*`).
Não é usado por `worker_queue` nem por `worker_runner` — são mecanismos de
tracking paralelos e independentes.

## Decisões não óbvias e armadilhas

- **O executor do job não é este módulo.** Quem processa o upload assíncrono
  é uma `threading.Thread` criada em `dashboard.app`; `async_jobs` só
  registra o estado. Nada aqui passa pela `worker_queue`.
- **Progresso ao vivo fica em memória.** A thread de importação chama
  `atualizar_progresso(..., persistir=False)`: o valor vai para um overlay em
  memória (protegido por `threading.Lock`) e `obter`/`listar` mesclam o
  overlay ao registro do banco. Isso evita um commit por atualização — mas o
  overlay só existe no processo que está importando.
- **Cancelamento é cooperativo e cruza requisições.** O token vive no
  processo que importa; o pedido chega por outra requisição HTTP. `cancelar`
  só sinaliza o token e retorna `False` se o job não estiver rodando neste
  processo (a rota responde 409). Quem grava o estado final é o importador,
  via `marcar_cancelado` — só ele sabe quando de fato parou. Nada é
  persistido: a transação da importação reverte inteira.
- **Controle de acesso pelos parâmetros.** `obter`/`listar` comparam
  `usuario_id` com o campo gravado no JSON de `parametros`; `admin` vê tudo.
  Não há coluna própria de dono.
- **`get_async_job_service` recria a instância quando `db_path` muda** — e
  com ela se perdem overlay e tokens. Em produção o caminho é estável; em
  testes que trocam o banco, é o comportamento esperado.
- `limpar_antigos` remove apenas `completed` e `failed`; jobs `cancelled` (e
  `pending` órfãos) ficam.

## Como testar isoladamente

```bash
pytest tests/test_fase14.py -q                       # CRUD, progresso, limpeza, upload-async E2E
pytest tests/test_ecd_grande.py -k Cancelamento -q   # token, cancelar, marcar_cancelado
```

## O que não faz

- Não executa nada: não tem fila, não tem worker, não agenda. Só registra
  estado.
- Não sobrevive a restart no meio do job: o job fica preso em
  `pending`/`processing` no banco e ninguém o retoma (a thread morreu junto
  com o processo).
- Não cancela jobs de outro processo: o token é local.
- Não notifica: o cliente descobre o desfecho por polling.
