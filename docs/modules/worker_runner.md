# worker_runner

## O que faz

Processo dedicado de workers: `python -m src.worker_runner` configura
logging, instancia um `RedisCacheService` (prefixo `worker:`), cria a
`WorkerQueue` global com `WORKER_COUNT` workers, registra o handler
`handler_ecd_import`, instala shutdown graceful em SIGTERM/SIGINT e entra em
loop chamando `queue.process_results()` a cada 0,5 s. No docker-compose,
roda como serviço `worker`, separado do web.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `handler_ecd_import(payload, update_progress)` | Handler da task `ecd_import`: resolve o arquivo, abre sessão e delega a `ECDImportService.importar`, repassando o progresso. Retorna `result.to_dict()`. |
| `main()` | Ponto de entrada do processo. |
| `DB_PATH`, `REDIS_URL`, `WORKER_COUNT` | Config resolvida de `get_settings()` na importação do módulo. |

É um executável, não uma biblioteca: nada importa símbolos dele.

## Depende de / quem depende

Depende de `worker_queue`, `cache.redis_cache`, `logging_config`, `settings`
e, dentro do handler, `ecd_importer` e `db.models`.

Ninguém no código importa `worker_runner`; os consumidores são operacionais:
`docker-compose.yml` (serviço `worker`) e execução manual.

## Decisões não óbvias e armadilhas

- **É um caminho paralelo ao do dashboard, não o backend dele.** O upload
  assíncrono do web usa `threading.Thread` e tracking em `async_jobs`; o
  runner consome a `worker_queue` com tracking próprio em memória. São duas
  pontas que não se conversam: nada no dashboard enfileira na
  `worker_queue`, e o runner não toca na tabela `async_jobs`.
- **O handler valida o caminho do arquivo.** Sem `path` explícito no
  payload, resolve `arquivo` dentro de `upload_dir` (só o nome, via
  `Path(...).name`) e rejeita com `ValueError` se o resultado escapar do
  diretório. Com `path` explícito, aceita como veio — quem enfileira precisa
  ser confiável.
- **Web e worker em containers separados exigem volume compartilhado** para
  `SPED_HUB_UPLOAD_DIR` — o handler abre o arquivo pelo filesystem.
- **A config congela na importação**: `DB_PATH`, `REDIS_URL` e
  `WORKER_COUNT` são lidos no import do módulo. Mudar variável de ambiente
  depois não afeta o processo vivo.
- O cache Redis criado em `main()` serve para logar o backend ativo
  (`redis` ou `memory`) na subida; handler que quiser cache cria o seu.
- Erros de importação passam pelo retry da fila (3 tentativas com backoff) —
  inclusive `DuplicateECDImportError`, que aqui conta como falha comum,
  diferente do watchdog, que a trata como "já importada".

## Como testar isoladamente

Não há teste dedicado (nenhum arquivo em `tests/` importa o módulo). As
peças que ele liga têm testes próprios:

```bash
pytest tests/test_fase15.py -k WorkerQueue -q    # a fila que ele consome
pytest tests/test_ecd_grande.py -q               # o importador que o handler chama
```

O teste real do processo inteiro é subir o compose.

## O que não faz

- Não expõe API: quem consulta status de fila por HTTP é o dashboard — e só
  dentro do próprio processo web, onde a fila não existe.
- Não registra jobs em `async_jobs` nem envia e-mail — os callbacks
  `on_complete`/`on_failure` só logam.
- Não recebe tasks de outro processo: `enqueue` precisa acontecer no mesmo
  processo do runner, e hoje nenhum código de produção enfileira nele.
- Não re-executa tasks perdidas em restart (herda a não-persistência da
  `worker_queue`).
