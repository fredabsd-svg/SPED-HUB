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
| `JobStatus` | Enum: `pending`, `processing`, `completed`, `failed`, `cancelled`, `interrupted`. |
| `STATUS_TERMINAIS` / `STATUS_EM_ABERTO` | Classificação dos estados. Toda verificação de estado usa estas listas. |
| `JobInfo` | Dataclass de resumo para a API (id, status, progresso, resultado, erro, datas). |
| `AsyncJobService(db_path)` | `criar`, `atualizar_progresso`, `concluir`, `falhar`, `obter`, `listar`, `limpar_antigos`. |
| `marcar_em_execucao(job_id, arquivo_temporario=None)` | Persiste `processing` e o caminho do upload. |
| `recuperar_interrompidos()` | Encerra os jobs que o processo anterior deixou em aberto; roda na subida. |
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
- **Por isso o job grava `processing` no início, via `marcar_em_execucao`.**
  Com o progresso só em memória, a linha do banco dizia `pending` / 0% /
  "Aguardando processamento..." durante a importação inteira. Depois de um
  reinício era isso que sobrava: um job que parecia nem ter começado, com uma
  mensagem afirmando que estava na fila.
- **A thread é `daemon`, e thread `daemon` morre sem rodar `finally`.** No
  encerramento do interpretador — reinício, `docker compose up -d` novo,
  queda — o `finally` que apaga o upload e fecha o job não executa. Antes isso
  deixava três resíduos: job em aberto para sempre, arquivo órfão no volume de
  uploads, e nada que soubesse onde procurá-lo. O caminho temporário agora é
  gravado nos `parametros` justamente para o arquivo poder ser encontrado
  depois.
- **`recuperar_interrompidos` roda no `lifespan` da aplicação.** Job em aberto
  no banco enquanto o processo sobe é, por construção, job abandonado: o
  executor é uma thread deste processo, não uma fila que alguém varre. Não há
  threshold de tempo nem falso positivo. Falha aqui é logada e engolida —
  escritório sem sistema é pior que um job em aberto a mais.
- **A ressalva é réplica.** Com mais de uma instância web, a subida de uma
  marcaria como interrompido o job em andamento da outra. O deploy documentado
  é de instância única (o limite por IP e o próprio overlay de progresso já
  pressupõem isso); mudar exigiria um worker com posse explícita do job, e está
  registrado em `docs/status.md`.
- **Estado novo entra nas duas listas, não em `if`s espalhados.** As
  verificações eram `(COMPLETED, FAILED)` escritas à mão em quatro lugares —
  `cancelled` ficava de fora de todas, e a limpeza automática nunca removia
  job cancelado. `STATUS_TERMINAIS` e `STATUS_EM_ABERTO` cobrem o enum
  inteiro, e há teste que quebra se um estado novo ficar sem classificação.
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

- Não retoma importação interrompida: o job é encerrado como `interrupted` e
  quem enviou precisa reenviar o arquivo. Retomar exigiria commits parciais,
  que a §6.1 proíbe.

- Não executa nada: não tem fila, não tem worker, não agenda. Só registra
  estado.
- Não sobrevive a restart no meio do job: o job fica preso em
  `pending`/`processing` no banco e ninguém o retoma (a thread morreu junto
  com o processo).
- Não cancela jobs de outro processo: o token é local.
- Não notifica: o cliente descobre o desfecho por polling.
