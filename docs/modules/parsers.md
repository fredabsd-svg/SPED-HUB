# parsers

## O que faz

Lê os arquivos texto do SPED em streaming, linha a linha, sem carregar o
arquivo em memória: ECD (leiaute 9, 2020 em diante), EFD-Contribuições e
ECF. Cada linha `|REG|campo|...|` vira um dict; o encoding (UTF-8 ou
ISO-8859-1) é detectado automaticamente. Só a ECD tem campos nomeados —
dirigidos pelo YAML de leiaute — e herança pai→filho.

## O que expõe

| Classe | Para quê |
|---|---|
| `ECDParser` (`ecd.py`) | `parse` (iterador), `parse_todos`, `parse_em_lotes(n)`, `contar_registros`. Campos nomeados via `src/layouts/ecd_v9.yml`; anota `_linha` e `_offset_bytes`; filhos herdam campos do pai. |
| `CampoInvalidoError` (`ecd.py`) | `ValueError` com `registro`, `campo`, `valor` e `linha` do campo monetário (`VL_*`) ilegível. |
| `EFDParser` (`efd.py`) | `parse`, `parse_todos`, `extrair_resumo` (identificação, PIS/COFINS: contribuição, crédito, saldo, a recolher; receita bruta do 0111). Registros genéricos: `_reg` + `_campos` posicionais. |
| `ECFParser` (`ecf.py`) | `parse`, `parse_todos`, `extrair_resumo` (identificação e as linhas declaradas do N630/IRPJ e N670/CSLL). |
| `detectar_encoding` | Nos três: UTF-8 ou ISO-8859-1 pelos primeiros 4096 bytes. |

Cada parser filtra por seu `REGISTROS_INTERESSE` (frozenset).

## Depende de / quem depende

Depende só de stdlib + PyYAML (apenas o ECD).

Quem depende: `ecd_importer` (único caminho de persistência da ECD),
`dashboard.app` (EFD/ECF nos uploads) e as fixtures de vários testes.

## Decisões não óbvias e armadilhas

- **J930 está no `ecd_v9.yml` com a ordem de campos conferida contra uma ECD
  validada pelo PVA**, não contra o manual. `IDENT_CPF_CNPJ` e `DT_CRC` são
  lidos como texto: como número, o CPF perderia o zero à esquerda. As duas
  divergências estão no cabeçalho do yml e em `tests/test_cnpj.py`.

- **Streaming de verdade**: memória constante; `parse_em_lotes` entrega
  lotes para importação incremental e `extrair_resumo` itera sem
  materializar lista — há teste de regressão garantindo que ele **não**
  chama `parse_todos`.
- **Encoding por amostra de 4096 bytes**, decodificação com
  `errors="replace"`: byte inválido no meio do arquivo não derruba a
  importação. ASCII puro é classificado como UTF-8 (é UTF-8 válido).
- **Registros fora de `REGISTROS_INTERESSE` são descartados em silêncio** —
  mas ainda podem servir de pai para os que interessam.
- **Herança declarativa pai→filho** na ECD: I051/I052 herdam `COD_CTA` do
  I050, I250 herda `NUM_LCTO`/`DT_LCTO` do I200, I155 herda
  `DT_INI`/`DT_FIN` do I150. Sem isso o filho não sabe a que
  conta/lançamento pertence.
- **Campo tipo N vira `float` (vírgula→ponto) — inclusive o CNPJ**, que sai
  como `123456000199.0`: perde zeros à esquerda, e o consumidor precisa
  reconstituir com `zfill(14)` (as fixtures fazem exatamente isso).
- **Campo monetário ilegível é erro, não `None`.** Nos campos `VL_*` da ECD,
  valor que não é dígitos com no máximo um separador decimal (vírgula, como
  no leiaute, ou ponto, como nas fixtures) levanta `CampoInvalidoError`
  com o registro, o campo e a linha. "1.234,56" e "1 234,56" viravam
  `None`, e o importador gravava 0,00: a escrituração entrava com um valor
  a menos e aparência de completa. Campo `N` que não é monetário (data,
  nível, indicador) continua virando `None` quando ilegível. Campo vazio
  continua `None`.
- **Assimetria proposital**: só a ECD tem leiaute YAML e campos nomeados;
  EFD e ECF entregam `_campos` posicionais — quem consome conta posições na
  mão (é o que `extrair_resumo` faz). `_campos` não traz o REG: o campo nº
  N do leiaute está em `_campos[N - 2]`, e os dois `extrair_resumo` leem
  pelo número do campo (`campo(campos, N)`), não pelo índice.
- **Posições do resumo da EFD-Contribuições**, conferidas no Guia Prático
  da EFD-Contribuições (tabelas de campos dos registros, reproduzidas em
  vriconsulting.com.br/guias, idGuia 351, 356, 492, 496, 508 e 512, em
  2026-09-25): 0000 — 06 DT_INI, 07 DT_FIN, 08 NOME, 09 CNPJ; M100/M500 —
  08 VL_CRED ("valor total do crédito apurado no período"); M200/M600 — 02
  VL_TOT_CONT_NC_PER + 09 VL_TOT_CONT_CUM_PER (contribuição do período,
  o "débito"), 13 VL_TOT_CONT_REC (a recolher); 0111 — 06 REC_BRU_TOTAL.
  A leitura anterior tirava o CNPJ do DT_FIN, as datas do
  NUM_REC_ANTERIOR/DT_INI, o débito de PIS do VL_TOT_CRED_DESC (crédito
  descontado), o crédito do IND_CRED_ORI e a "receita bruta" do DT_OPER
  do F100 — uma data.
- **Receita bruta da EFD só com 0111.** O 0111 é o único registro com a
  receita bruta total, e só é obrigatório no rateio proporcional do crédito
  (0110, IND_APRO_CRED = 2). Sem ele, `receita_bruta` é `None`: o M210 só
  tem a receita tributada, e o F100 só as "demais operações".
- **Posições do resumo da ECF**, conferidas no Manual de Orientação do
  Leiaute da ECF (0000: REG, NOME_ESC, COD_VER, CNPJ, NOME,
  IND_SIT_INI_PER, SIT_ESPECIAL, PAT_REMAN_CIS, DT_SIT_ESP, DT_INI,
  DT_FIN, RETIFICADORA, NUM_REC, TIP_ECF, COD_SCP — o exemplo do manual é
  `|0000|LECF|1.00|11111111000191|EMPRESA TESTE|0|0|||01012014|31122014|N||0||`);
  N630 (cálculo do IRPJ, lucro real) e N670 (cálculo da CSLL, lucro real):
  02 CODIGO, 03 DESCRICAO, 04 VALOR. A leitura anterior tirava o CNPJ do
  PAT_REMAN_CIS e somava todas as linhas do N670 (CSLL) como "irpj".
- **A ECF não totaliza IRPJ, CSLL nem lucro.** Qual linha do N630/N670 é
  "o imposto devido" depende do código da tabela dinâmica que a RFB publica
  por leiaute e ano-calendário, e o leiaute do registro não diz; sem a
  tabela embutida, `irpj`, `csll`, `lucro_contabil` e `lucro_tributavel`
  são `None` e as linhas declaradas saem em `apuracao_irpj`/`apuracao_csll`
  (código, descrição, valor, linha do arquivo).
- O ECD anota `_offset_bytes` por registro — insumo para progresso em
  arquivos grandes.

## Como testar isoladamente

```bash
pytest tests/test_parsers.py -q            # encoding, campos, herança, streaming
pytest tests/test_ecd_grande.py -q         # memória constante e lotes
pytest tests/test_review_regressions.py -q # resumo sem materializar
```

## O que não faz

- Não grava nada no banco — persistir é papel de `ecd_importer` (ECD); EFD e
  ECF hoje só geram resumo em memória.
- Não valida a escrituração: totalizadores e consistência são assunto de
  `validators`.
- Não suporta leiautes antigos da ECD (só v9, ano-calendário 2020+).
- Não nomeia campos de EFD/ECF nem interpreta registros fora do conjunto de
  interesse.
