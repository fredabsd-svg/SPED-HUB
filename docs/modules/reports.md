# reports

## O que faz

Transforma a escrituração importada nos demonstrativos contábeis — balancete,
razão, balanço patrimonial, DRE, DFC e livro diário — e exporta em PDF
(WeasyPrint, identidade "Tinta & Latão") e XLSX (openpyxl). Cada relatório é
uma classe que lê o banco e devolve estruturas prontas; a renderização fica
com o `ExportEngine`.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `Balancete`, `Razao`, `BalancoPatrimonial`, `DRE`, `DFC`, `LivroDiario` | Geradores. `gerar(criterios)` devolve `(ReportContext, linhas[, totais])`. |
| `Balancete.totais(linhas)` / `Balancete.conferir(linhas)` | Totais sem dobrar a conta; conferência SI+D−C=SF linha a linha. |
| `ExportEngine` | `render_html`, `export_pdf`, `export_xlsx`, `export_xlsx_to_buffer`. |
| `WhiteLabel` | Marca do escritório: nome, cor primária, cor clara, logo. |
| `base.py` | `ReportContext`, `valor_sinalizado`, `saldo_por_natureza`, `fmt_moeda/fmt_data/fmt_data_hora`. |

Templates em `templates/`: `base.html` (moldura comum), um HTML por
relatório, `tokens.css` (paleta e tipografia) e `print.css` (regras de
página A4).

## Depende de / quem depende

Depende de `src.db.models`, `src.filters.engine` (critérios) e, na
exportação, WeasyPrint e openpyxl.

Consumido por `cli`, `api.routes`, `api.graphql`, `dashboard` e
`validators.integridade` (que reusa `valor_sinalizado`).

## Decisões não óbvias e armadilhas

- **Identidade "Tinta & Latão"** (verde-tinta `#0C3A30` + latão `#A9812F`,
  Source Serif 4 sobre Source Sans 3, OFL, TTFs versionados em
  `templates/fonts/`). Os nomes `--color-primary-*` do `tokens.css` são a
  API dos templates: o white-label sobrescreve `primary-700` e `primary-50`
  com a marca do escritório — mudar o *nome* dessas variáveis quebra o
  white-label em silêncio.
- **Fonte ausente não dá erro.** O WeasyPrint degrada para Times/Helvetica
  sem avisar; `tests/test_identidade_export.py` confere que todo
  `@font-face` aponta para arquivo existente.
- **`Balancete.totais` soma só o menor nível presente.** Sintética agrega as
  analíticas; somar todas as linhas dobra cada valor. Em listagem filtrada
  por conteúdo, os totais são do topo do que está listado — a consistência
  contábil é assunto do `conferir`, não do rodapé.
- **Sinal contábil**: os valores no banco seguem D positivo / C negativo
  (`valor_sinalizado`); a natureza da conta (`saldo_por_natureza`) decide a
  exibição. Relatório novo deve passar por essas funções, nunca refazer o
  sinal na mão.
- **Números idênticos em SQLite e Postgres** é garantia da §6.3, coberta por
  `tests/test_multibackend.py::TestRelatoriosIdenticos`.
- O XLSX usa Calibri de propósito: a planilha abre na máquina do cliente,
  onde as fontes da identidade não estão instaladas. A paleta acompanha a
  identidade; a tipografia não.

## Como testar isoladamente

```bash
pytest tests/test_reports.py tests/test_fase2.py tests/test_identidade_export.py -q
pytest tests/test_cli.py -q -k exportar
```

Os geradores aceitam qualquer `Session` com o schema criado — a fixture de
`tests/test_reports.py` monta a base mínima sem arquivo.

## O que não faz

- Não altera dados: os geradores só leem.
- Não valida a escrituração — divergência aparece anotada (balancete) ou é
  assunto de `validators`.
- Não agenda nem envia relatório; entrega arquivo ou buffer a quem pediu.
- Não gera DOCX.
