# reports

## O que faz

Transforma a escrituração importada nos demonstrativos contábeis — balancete,
razão, balanço patrimonial, DRE, DFC (métodos direto e indireto), índices de
habilitação econômico-financeira para licitação, plano de contas e livro
diário — e exporta em PDF (WeasyPrint, identidade "Tinta & Latão"), XLSX
(openpyxl) e TXT. Cada relatório é uma classe que lê o banco e devolve
estruturas prontas; `documentos.py` empacota o relatório com cabeçalho,
filtros e assinaturas, e a renderização fica com o `ExportEngine`.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `Balancete`, `Razao`, `BalancoPatrimonial`, `DRE`, `DFC`, `LivroDiario`, `IndicesFinanceiros`, `PlanoDeContas` | Geradores. `gerar(criterios)` devolve `(ReportContext, linhas[, totais])`. `DRE.gerar(detalhar=True)` põe uma linha por conta sob cada degrau; `DFC.gerar(metodo="direto"\|"indireto")` e `DFC.gerar_ambos()`. |
| `classificacao.Classificador` | A classificação única das contas de uma ECD: grupo do balanço (AC, RLP, PC, PNC, PL…), seção (ativo/passivo/PL), caixa e equivalentes, categoria da DRE, categoria de fluxo da DFC, linha do método indireto. |
| `estrutura.correcoes_pelo_balanco_publicado` | As analíticas cujo superior no I050 o J100 contradiz (ADR 0012); usado por `FilterEngine.hierarquia`. |
| `documentos` | `montar(session, ecd_id, tipo, criterios, visao=, responsavel=, assinar=)` → `Documento`; `exportar(documento, "pdf"\|"xlsx"\|"txt")`, `texto`, `html`, `gravar_pdf`, `gravar_xlsx`. É o caminho da CLI e do painel. |
| `assinaturas` | `assinaturas(session, ecd_id, responsavel)` → `[responsável legal, contador]`; `Responsavel`, `formatar_cpf`. |
| `Balancete.totais(linhas)` / `Balancete.conferir(linhas)` | Totais sem dobrar a conta; conferência SI+D−C=SF linha a linha. |
| `ExportEngine` | `render_html`, `export_pdf`, `export_xlsx`, `export_xlsx_to_buffer`. |
| `WhiteLabel` | Marca do escritório: nome, cor primária, cor clara, logo. |
| `base.py` | `ReportContext`, `valor_sinalizado`, `saldo_por_natureza`, `fmt_moeda/fmt_data/fmt_data_hora`. |
| `saldos.py` | A consolidação única de saldos: `consolidar(engine, criterios)` → `SaldosConsolidados`; `consolidar_periodos`, `saldos_por_periodo`, `somar_resultado`; `Hierarquia` (árvore `COD_CTA → COD_CTA_SUP`, à prova de ciclo: ancestrais, descendentes, ordem do plano, contas maximais, contas-base, rollup) e `Saldo`. |

Templates em `templates/`: `base.html` (moldura comum), `_assinaturas.html`
(linhas de assinatura, incluídas quando o documento as traz), um HTML por
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
  publicação e o comparativo), DRE, DFC, validações e o painel usam esse
  único caminho — antes cada um tinha o seu, e cada um errava num ponto
  (saldos de doze meses somados, ATIVO zerado, centro de custo
  sobrescrito).
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
- **Uma classificação só, na ordem referencial → nomes → natureza**
  (`classificacao.py`). O referencial (I051) decide só onde é inequívoco:
  3.01.01.01.01 receita bruta, 3.01.01.01.02 deduções, 3.01.01.03 custos,
  1.01.01 caixa e equivalentes. O resto sai do nome da conta e dos
  superiores, do mais próximo ao topo — numa ECD real todas as despesas
  vinham com o referencial genérico 3.01.01.09.01.99, e só o grupo
  "DESPESAS FINANCEIRAS" dizia que IOF e tarifas eram financeiras. A regra de
  receita ignora nome com DESPESA/CUSTO/"POR PJ" ("SERVIÇOS PRESTADOS POR PJ"
  é despesa). "(-) DEVOLUÇÃO DE MERCADORIAS" dentro do CMV é custo, não
  dedução. O `Mapeamento` da empresa continua valendo por cima.
- **PL de natureza 02.** Muito plano põe o PL sob o PASSIVO, com natureza 02
  (o PVA aceita). A seção do balanço vem do `Classificador`: conta dentro de
  "PATRIMÔNIO LÍQUIDO" é PL. A sintética que soma passivo e PL não é listada
  (o valor dela é o total "Passivo + PL"), e os totais de cada seção somam as
  contas-base exibidas ou cobertas por uma linha exibida.
- **Saldo anterior do balanço**: o SF da ECD anterior, quando importada;
  senão, o SI do primeiro I150 — o balanço de encerramento do exercício
  anterior, que é o `VL_CTA_INI` do J100. `totais["origem_anterior"]` diz
  qual, e as colunas levam as datas (31/12/AAAA).
- **Visão de publicação = o J100.** Com J100 na ECD, a visão de publicação
  lista as linhas dele, com o saldo anterior publicado; sem J100, cai na
  aglutinação por código convencional (J100/J150… no I052).
- **DRE: o valor da linha é o saldo com o sinal trocado**, para receita e
  para despesa (crédito soma, débito subtrai); `totais["receita_bruta"]`
  vem com o sinal da DRE. Cada conta com I355 entra uma vez, na categoria
  dela ou do superior mapeado mais próximo — mapear a sintética leva as
  filhas. O período do filtro vale para a data do I350. Degraus: receita
  bruta, deduções, custos, despesas operacionais, **outras receitas e
  despesas operacionais**, resultado antes do resultado financeiro,
  receitas e despesas financeiras, IRPJ/CSLL. O período anterior vem da ECD
  anterior ou, sem ela, da DRE publicada na própria ECD (J150,
  `VL_CTA_INI`): cada linha de detalhe vai para a categoria das contas que
  o I052 aglutina nela.
- **Sinal contábil**: os valores no banco seguem D positivo / C negativo
  (`valor_sinalizado`); a natureza da conta (`saldo_por_natureza`) decide a
  exibição. Relatório novo deve passar por essas funções, nunca refazer o
  sinal na mão.
- **DFC pelos lançamentos, métodos direto e indireto (ADR 0011).** Só entra
  o que passou por caixa e equivalentes (`Classificador.eh_caixa`: referencial
  1.01.01, nome da conta e do grupo, mapeamento "caixa"; conta de passagem
  "TRANSFERÊNCIAS ENTRE CONTAS" é numerário em trânsito). Lançamento só entre
  contas de caixa é transferência interna e fica fora (CPC 03, item 9);
  lançamento sem caixa não é fluxo (item 43). O caixa de cada lançamento é
  repartido entre as contrapartidas do lado oposto, na proporção de cada uma
  (`ratear`). O indireto parte do lucro dos lançamentos e ajusta pelo que
  não teve caixa — a parte operacional que casa com investimento ou
  financiamento sem caixa (depreciação, juros apropriados) — e pela variação
  do capital de giro, líquida dessas partes; o caixa das operações é o mesmo
  nos dois métodos por construção (`totais["diferenca_metodos"]`).
  Investimento e financiamento são brutos e iguais nos dois (item 21); juros
  e IR/CSLL nas operacionais (itens 34A e 35). Lançamentos de encerramento
  (indicador "E", conta de natureza 09, ou resultado contra PL sem caixa)
  não entram no indireto. O total é conciliado com o SI/SF das contas de
  caixa (item 45), com a composição conta a conta em
  `totais["composicao_caixa"]`; lucro dos lançamentos diferente do I355 gera
  aviso no PDF. ECD sem lançamentos cai na variação dos saldos, sem método
  direto. Mapeamento "dfc" antigo é traduzido para as categorias de fluxo.
  Dos filtros, vale o período (datas dos lançamentos).
- **Índices para licitação (Lei 14.133/2021, art. 69).** LG = (AC + RLP) /
  (PC + PNC), SG = AT / (PC + PNC), LC = AC / PC — os usuais, referência
  maior que 1 —, mais liquidez seca e imediata, endividamento, CCL e PL. O
  exercício anterior é o SI (ou a ECD anterior). Conta sem grupo
  identificado é listada; passivo sem grupo vai para o circulante, o lado
  prudente. Sem índice de rentabilidade (vedado pelo § 2º).
- **Assinaturas.** Balancete, balanço, DRE, DFC e índices saem com a linha
  do responsável legal e a do contador. O contador vem do J930 (código 900 ou
  CRC); o responsável, do que for informado na exportação, do cadastro da
  empresa (`Empresa.responsavel_*`) ou do J930 quando é pessoa física — a
  ECD assinada com e-CNPJ não diz o nome do sócio, e a linha sai em branco.
- **Plano de contas** é o I050 como declarado (ordem e recuo pelo
  `COD_CTA_SUP` do arquivo), com referencial, aglutinação e, onde o J100
  diverge, o superior publicado. Não leva assinatura.
- **Bloco "Filtros" só quando há filtro** (`ReportContext.tem_filtros`). O
  painel montava o contexto com a descrição vazia, e o template só escondia
  o bloco para o texto "Nenhum filtro aplicado". Hoje painel e CLI montam o
  documento pelo mesmo `documentos.montar`: período em pt-BR, CNPJ
  formatado, hash da ECD e filtros iguais nos dois.
- **TXT**: colunas alinhadas pela maior célula, valores em pt-BR à direita,
  recuo pelo nível, sem truncar nome; totais e assinaturas no rodapé. A
  planilha leva rótulos em português e a data do balanço no cabeçalho;
  qualquer `float` sai no formato de moeda.
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
- **O formato de moeda do XLSX é `#,##0.00;(#,##0.00);"-"`**
  (`FORMATO_MOEDA_XLSX`). O código de formato do OOXML é gravado em notação
  en-US — "." decimal, "," milhar — e o Excel/LibreOffice o exibem com os
  separadores de quem abre (1.234,56 em pt-BR). O `#.##0,00` de antes,
  escrito "à brasileira", não era um formato de moeda.
- O XLSX usa Calibri de propósito: a planilha abre na máquina do cliente,
  onde as fontes da identidade não estão instaladas. A paleta acompanha a
  identidade; a tipografia não.

## Como testar isoladamente

```bash
pytest tests/test_reports.py tests/test_fase2.py tests/test_identidade_export.py -q
pytest tests/test_saldos_consolidados.py tests/test_razao_diario.py tests/test_dfc_metodo_indireto.py -q
pytest tests/test_cli.py -q -k exportar
pytest tests/test_demonstracoes_ecd_real.py -q
```

`tests/fixtures/ecd_demonstracoes.py` reproduz, com números pequenos, os
defeitos de uma ECD real: superior trocado no I050 (com o J100 certo), PL de
natureza 02, CMV e despesas financeiras com referencial genérico, devolução
de compra no CMV, transferência entre bancos por conta de passagem,
transações sem caixa, J150 com o ano anterior e J930.

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
