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
| `saldos.py` | A consolidação única de saldos: `consolidar(engine, criterios)` → `SaldosConsolidados`; `consolidar_periodos`, `saldos_por_periodo`, `somar_resultado`; `Hierarquia` (árvore `COD_CTA → COD_CTA_SUP`, à prova de ciclo: ancestrais, descendentes, ordem do plano, contas maximais, contas-base, rollup) e `Saldo`. |

Templates em `templates/`: `base.html` (moldura comum), um HTML por
relatório, `tokens.css` (paleta e tipografia) e `print.css` (regras de
página A4).

## Depende de / quem depende

Depende de `src.db.models`, `src.filters.engine` (critérios) e, na
exportação, WeasyPrint e openpyxl.

Consumido por `cli`, `api.routes`, `api.graphql`, `dashboard`,
`validators.integridade` (que reusa `valor_sinalizado` e a consolidação de
`saldos.py`) e `filters.engine` (que usa a `Hierarquia` na subárvore).

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
- **Todo saldo passa por `saldos.consolidar`.** O I155 e o I355 só existem
  para conta analítica (o manual diz "código da conta analítica"), uma ECD
  real traz um I150 por mês, e a conta com centro de custo tem um I155 por
  centro. A consolidação soma os centros de custo do mesmo (conta,
  período), toma o SI do **primeiro** I150 e o SF do **último** (débitos e
  créditos somados) e dá às sintéticas a soma das filhas. Sintética com
  I155 próprio usa o próprio saldo, e as filhas deixam de subir por ela:
  nada é contado duas vezes. Balancete, balanço (inclusive a visão de
  publicação e o comparativo), DRE, validações e o painel usam esse único
  caminho — antes cada um tinha o seu, e cada um errava num ponto (saldos
  de doze meses somados, ATIVO zerado, centro de custo sobrescrito).
- **Critérios de conta escolhem o que aparece, não o que soma.** Com "nível
  até 2", a sintética de nível 2 continua com o saldo das analíticas de
  nível 3. Critérios de valor (mínimo, máximo, saldo zero, sem movimento)
  olham o saldo consolidado; período e centro de custo restringem as
  linhas do I155 (o período pega os I150 inteiramente dentro dele).
- **Total conta cada valor uma vez.** `Balancete.totais` e os totais do
  balanço somam só as linhas sem superior listado. Somar só o menor nível
  presente, como era antes, deixava de fora as analíticas mais fundas
  (820.000 de débitos na amostra, contra 2.980.000). Linha montada fora do
  `gerar` (sem `ancestrais`) herda a superior da indentação.
- **`conferir` conta só as contas com saldo próprio**, e confere o intervalo
  inteiro (SI do primeiro mês + D − C = SF do último). A conferência mês a
  mês é a validação (b) de `validators`.
- **DRE: o valor da linha é o saldo com o sinal trocado**, para receita e
  para despesa (crédito soma, débito subtrai); `totais["receita_bruta"]`
  vem com o sinal da DRE. Cada conta com I355 entra uma vez, na categoria
  dela ou do superior mapeado mais próximo — mapear a sintética leva as
  filhas. O período do filtro vale para a data do I350.
- **Sinal contábil**: os valores no banco seguem D positivo / C negativo
  (`valor_sinalizado`); a natureza da conta (`saldo_por_natureza`) decide a
  exibição. Relatório novo deve passar por essas funções, nunca refazer o
  sinal na mão.
- **Razão: uma linha por partida, saldo desde o I155.** O saldo corrente
  parte do SI do I155 (sem data inicial, o do primeiro período; com data,
  o do período em que ela cai mais as partidas anteriores a ela dentro
  dele) — `Razao.saldo_inicial` guarda esse valor, e a CLI o imprime como
  "Saldo anterior". As contrapartidas vêm das outras partidas do mesmo
  lançamento, numa consulta à parte (a do filtro só traz a conta
  razonada). Duas partidas do lançamento na mesma conta são duas linhas.
  O `criterios` recebido não é alterado.
- **Razão e diário ordenam por data e, no mesmo dia, pelo número lido como
  número** (`chave_num_lcto`): como texto, "10" vinha antes de "9", e o
  razão chegava a ordenar pelo número antes da data.
- **`fmt_moeda` arredonda uma vez, em `Decimal`, meio para cima.** Arredondar
  os centavos à parte fazia 1,999 sair "1,100" (o vai-um não chegava à parte
  inteira). Valor que arredonda para zero sai "0,00", nunca "(0,00)".
- **Números idênticos em SQLite e Postgres** é garantia da §6.3, coberta por
  `tests/test_multibackend.py::TestRelatoriosIdenticos`.
- O XLSX usa Calibri de propósito: a planilha abre na máquina do cliente,
  onde as fontes da identidade não estão instaladas. A paleta acompanha a
  identidade; a tipografia não.

## Como testar isoladamente

```bash
pytest tests/test_reports.py tests/test_fase2.py tests/test_identidade_export.py -q
pytest tests/test_saldos_consolidados.py tests/test_razao_diario.py -q   # ECD trimestral
pytest tests/test_cli.py -q -k exportar
```

`tests/fixtures/multiperiodo.py` gera uma ECD trimestral (três I150,
sintéticas sem I155, despesa rateada em dois centros de custo, encerramento)
que fecha por construção.

Os geradores aceitam qualquer `Session` com o schema criado — a fixture de
`tests/test_reports.py` monta a base mínima sem arquivo.

## O que não faz

- Não altera dados: os geradores só leem.
- Não valida a escrituração — divergência aparece anotada (balancete) ou é
  assunto de `validators`.
- Não agenda nem envia relatório; entrega arquivo ou buffer a quem pediu.
- Não gera DOCX.
