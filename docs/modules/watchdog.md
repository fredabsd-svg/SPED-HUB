# watchdog

## O que faz

Monitora um diretório por polling e importa automaticamente arquivos `.txt`
e `.ecd` novos, chamando o mesmo `ECDImportService` do CLI e do dashboard.
Roda como processo próprio (`python -m src.watchdog --dir ... --db ...
--interval 30`), com modo `--once` para execução única (usado pelo cron).
Mantém em memória um dicionário de hashes já processados para não reimportar
o mesmo arquivo a cada ciclo.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `processar_arquivo(caminho, db_path)` | Importa um arquivo; `True` se importou, `False` se repetido, duplicado ou com erro. |
| `escanear_e_importar(watch_dir, db_path)` | Um ciclo de varredura; retorna quantos importou. |
| `main()` | CLI (`--dir`, `--db`, `--interval`, `--once`); script `sped-hub-watchdog` no pyproject. |
| `_hash_file(path)` | SHA-256 do arquivo (delega a `ecd_importer.hash_file`); usado pelos testes. |

## Depende de / quem depende

Depende de `ecd_importer` (`ECDImportService`, `DuplicateECDImportError`,
`hash_file`), `db.models` e `logging_config`.

Consumidores operacionais: `scripts/cron-import.sh` (modo `--once`), o entry
point no pyproject e um CMD comentado no Dockerfile. Nenhum módulo Python
importa o watchdog fora dos testes.

## Decisões não óbvias e armadilhas

- **Deduplicação em duas camadas.** A primeira é o dicionário `_processed`
  (hash → timestamp), em memória, no nível do módulo: evita reimportar
  arquivos que continuam no diretório a cada ciclo. A segunda é o banco:
  após restart o dicionário some, mas o reimporte esbarra em
  `DuplicateECDImportError`, que o watchdog trata como sucesso silencioso
  ("ECD já importada") e anota no dicionário. Restart não duplica dados.
- **Dedupe por conteúdo, não por nome**: renomear o arquivo não provoca
  reimportação; editar o conteúdo, sim (hash novo).
- **Arquivo com erro fica em retry eterno implícito**: falha na importação
  não entra em `_processed`, então cada ciclo tenta de novo e loga a
  exceção. Não há quarentena nem contagem de tentativas.
- **Não usa a fila nem o tracking de jobs.** Chama `ECDImportService`
  diretamente, no próprio processo — nada de `worker_queue`,
  `worker_runner` ou `async_jobs`. O progresso vai para log em nível debug.
- **Não move nem apaga o que importou**: os arquivos permanecem no
  diretório; é o dicionário/banco que impede o reprocesso.
- Não há verificação de estabilidade do arquivo: um arquivo ainda sendo
  copiado pode ser lido no meio da escrita. A transação única do importador
  garante que nada parcial entra no banco.
- O estado `_processed` é global do módulo — processos distintos não o
  compartilham.

## Como testar isoladamente

```bash
pytest tests/test_fase7.py -k Watchdog -q
```

Um ciclo manual sem daemon: `python -m src.watchdog --dir /tmp/ecds --db
/tmp/teste.db --once`.

## O que não faz

- Não usa inotify/watchdog de filesystem: é polling puro, com
  `time.sleep(interval)`.
- Não persiste o estado de processados: só o dedupe do banco sobrevive a
  restart.
- Não processa em paralelo: um arquivo por vez, no processo principal.
- Não importa EFD nem ECF — só as extensões `.txt` e `.ecd`, pelo caminho da
  ECD.
- Não notifica falhas: apenas loga.
