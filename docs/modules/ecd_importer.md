# ecd_importer

## O que faz

Importa um arquivo de ECD (Escrituração Contábil Digital) para o banco,
percorrendo-o uma única vez. Persiste plano de contas, centros de custo,
participantes, históricos padrão, saldos periódicos, saldos de resultado,
lançamentos e partidas. Mantém em memória apenas metadados e mapas pequenos —
o arquivo nunca é carregado inteiro.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `ECDImportService(session, parser=None)` | Serviço. `importar(...)` faz o trabalho. |
| `ECDImportResult` | Contagem por tipo de registro e identificadores criados. |
| `ECDImportError` | Erro de validação ou consistência. |
| `DuplicateECDImportError` | Mesmo hash já importado; carrega o `ecd_id` existente. |
| `ECDImportCancelled` | Cancelamento cooperativo, com quantos registros haviam sido lidos. |
| `CancelToken` | Permite ao chamador pedir cancelamento (`cancelar()` / `cancelado`). |
| `hash_file(path, chunk_size=None)` | SHA-256 em chunks. |

`importar` aceita `progress: Callable[[float, str], None]` para barra de
progresso.

## Depende de / quem depende

Depende de `src.parsers.ecd` (leitura do layout), `src.db.models` (schema) e
`src.settings` (tamanho de chunk).

Consumido por: `cli`, `watchdog`, `worker_runner` e `dashboard.app`.

## Decisões não óbvias e armadilhas

- **A importação é uma transação só.** Interrupção reverte tudo (§6.1). Uma
  ECD pela metade é pior que nenhuma: o balanço não fecha e nada indica que
  faltam lançamentos. Por isso não existe retomada por offset.
- **Deduplicação por hash do arquivo**, não por nome nem por período. Reenviar
  o mesmo arquivo levanta `DuplicateECDImportError` com o `ecd_id` anterior.
- **Nada de flush por registro.** A versão anterior chamava `flush()` a cada
  linha — 20.267 flushes num arquivo de 8,6 MB. Removê-los levou a importação
  de 59 s para 27 s. A medição veio antes: o palpite inicial era limite de
  memória, e a memória estava constante (§3.3).
- **O hash pode vir pronto do fluxo de upload**, que já leu o arquivo em
  chunks. Recalcular seria uma segunda passada completa.
- **Cancelamento é cooperativo**, verificado entre blocos. Não interrompe uma
  operação de banco em andamento.
- Os tamanhos de bloco vêm de `SPED_HUB_ECD_CHUNK_ROWS` e
  `SPED_HUB_ECD_CHUNK_BYTES`.

## Como testar isoladamente

```bash
pytest tests/test_ecd_grande.py -q          # volume e ausência de flush por registro
pytest tests/test_integracao.py -q          # fluxo completo
```

`tests/fixtures/sintetico.py` gera ECD de tamanho arbitrário sem precisar de
arquivo real de cliente.

## O que não faz

- Não valida regra fiscal nem consistência contábil além do que o layout
  exige — isso é `validators/`.
- Não gera relatório: só persiste.
- Não importa EFD nem ECF.
- Não escreve parcialmente: ou entra tudo, ou nada.
