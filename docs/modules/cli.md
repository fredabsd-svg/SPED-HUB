# cli

## O que faz

Ponto de entrada de linha de comando (`sped-hub`, via argparse) para operar o
banco sem subir o servidor: importa ECD, gera relatórios no terminal, exporta
PDF/XLSX com white-label, valida integridade contábil, gerencia visões de
filtro, mostra informações do banco e aplica migrações Alembic. Chama
`configurar_logging()` no import do módulo.

## O que expõe

Subcomandos (entry point `sped-hub = "src.cli:main"` no pyproject; também
`python -m src.cli`):

| Subcomando | Para quê |
|---|---|
| `importar-ecd ARQUIVO [--db]` | Importa ECD em modo incremental; sai com 1 em arquivo inexistente, inválido ou duplicado. |
| `relatorio TIPO [--conta --natureza --nivel-ate --dt-ini --dt-fin --visao --ecd-id --db]` | `balancete`, `razao`, `balanco`, `dre`, `diario` no terminal. |
| `exportar TIPO [--formato pdf\|xlsx --saida --escritorio --cor --cor-clara --logo …]` | PDF/XLSX com white-label. |
| `validar [--ecd-id --db]` | Roda `ValidadorIntegridade` e imprime erros/alertas. |
| `filtros listar\|salvar\|mostrar [--nome --criterios]` | Visões salvas de filtros (JSON). |
| `info [--db]` | Contagens do banco e últimas 5 ECDs. |
| `migrar [status\|aplicar\|adotar] [--db]` | Migrações Alembic; `status` é o default. |
| `migrar-dados --de --para [--lote --conferir]` | Copia o conteúdo de um banco para outro. |
| `usuario criar\|listar [--email --nome --senha --admin --escritorio --db]` | Contas do painel. Sem `--senha`, pede sem eco. |
| `fiscal empresas [--db]` | As empresas cadastradas, com o cadastro fiscal que decide se podem gerar. |
| `fiscal importar CAMINHO… [--escritorio --db]` | Importa XML; pasta é varrida por `.xml`, recursivamente. |
| `fiscal documentos --empresa [--de --ate --db]` | Os documentos da Central, com o total. |
| `fiscal gerar --empresa --de --ate [--tipo --saida --db]` | Gera a EFD **e arquiva** a escrituração. |
| `fiscal historico [--empresa --db]` | As escriturações geradas, com hash. |
| `fiscal conferir --escrituracao [--diff --db]` | O entregue contra o que sairia agora. Sai com **2** se divergiu. |

O `fiscal` vive em `src/cli_fiscal.py` — a cadeia da Central é grande o
bastante para não caber junto com os relatórios contábeis, e o `cli.py` só
registra o parser e despacha. As decisões dele estão em
[`cli_fiscal.md`](cli_fiscal.md).

## Depende de / quem depende

Depende de `db.models`, `db.repository`, `db.migrations` (só dentro de
`cmd_migrar`), `ecd_importer`, `filters.engine`, `logging_config`,
`validators.integridade` e dos módulos de `reports/`.

Quem depende: `pyproject.toml` (script `sped-hub`), `docker-compose.yml`
(o serviço de migração roda `python -m src.cli migrar aplicar` no boot) e
`docs/deploy.md` (`migrar adotar` / `migrar status`). Nenhum módulo de `src/`
importa a CLI.

## Decisões não óbvias e armadilhas

- **`exportar balancete --formato pdf` já foi mentira.** Até a 0.16.x não
  existia template PDF do balancete: o comando caía num XLSX gravado em OUTRO
  caminho (`<saida>.xlsx`), avisando só em log INFO — automações recebiam
  sucesso e não achavam o arquivo. O template `balancete.html` fechou isso e
  `tests/test_cli.py` guarda a regressão.
- **`raise SystemExit(1) from None`** na importação: o traceback já foi
  logado por `logger.exception`; encadear a exceção só poluiria a saída.
- **`migrar --db` aceita caminho de arquivo OU URL**; o default funciona como
  sentinela — quando não passado, vale `DATABASE_URL`. Os demais subcomandos
  tratam `--db` como caminho SQLite.
- **`usuario criar` existe porque o `/register` fecha.** O registro público
  só vale enquanto não há usuário nenhum — é o bootstrap do administrador.
  Depois dele, qualquer um que alcançasse o servidor criaria conta e cairia no
  mesmo grupo do contador, enxergando a escrituração dos clientes: numa
  instalação de escritório único ninguém tem `escritorio_id`, nem os usuários
  nem as empresas. Quem cria conta a partir daí é quem tem acesso ao servidor.
- **`usuario criar` sem `--senha` pergunta sem eco** (`getpass`): senha em
  argumento de linha de comando fica no histórico do shell e no `ps`.
- **`migrar aplicar` roda sob advisory lock** (via `db.migrations`): réplicas
  subindo juntas não aplicam a mesma migração em paralelo. `migrar adotar` só
  carimba banco pré-existente, sem executar migração.
- Reimportar o mesmo arquivo é **recusado com exit 1, sem duplicar nada**
  (`DuplicateECDImportError` no serviço de importação).
- `_parse_data` aceita `DDMMAAAA` e `AAAA-MM-DD`; `31/12/2024` levanta
  `ValueError`.
- Sem `--ecd-id`, `relatorio`/`exportar`/`validar` usam a **última ECD
  importada** (`ORDER BY importado_em DESC`).
- `relatorio diario` imprime só os 10 primeiros lançamentos na tela.
- Os testes entram por `main()` com `sys.argv` trocado — o parser também é
  exercitado, não só as funções `cmd_*` (o módulo já esteve com 0% de
  cobertura).
- **`fiscal conferir` sai com 2 quando divergiu**, distinto do 1 de erro —
  divergência não é falha. Os demais subcomandos usam só 0 e 1. As outras
  decisões do `fiscal` estão em [`cli_fiscal.md`](cli_fiscal.md).

## Como testar isoladamente

```bash
pytest tests/test_cli.py -q
pytest tests/test_migrations.py -q   # upgrade_head/stamp_head usados por `migrar`
```

## O que não faz

- Não autentica nem aplica isolamento de tenant: fala direto com o banco.
- Não registra auditoria.
- Não importa EFD/ECF — só ECD (os outros formatos entram pelo dashboard).
- Não exporta `razao` (o subcomando `exportar` aceita apenas balancete,
  balanco, dre e diario).
- Não gera migração — só aplica/carimba as existentes.
