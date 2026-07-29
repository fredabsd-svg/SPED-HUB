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
| `EFDParser` (`efd.py`) | `parse`, `parse_todos`, `extrair_resumo` (PIS/COFINS, receita bruta). Registros genéricos: `_reg` + `_campos` posicionais. |
| `ECFParser` (`ecf.py`) | `parse`, `parse_todos`, `extrair_resumo` (IRPJ/CSLL, lucro). |
| `detectar_encoding` | Nos três: UTF-8 ou ISO-8859-1 pelos primeiros 4096 bytes. |

Cada parser filtra por seu `REGISTROS_INTERESSE` (frozenset).

## Depende de / quem depende

Depende só de stdlib + PyYAML (apenas o ECD).

Quem depende: `ecd_importer` (único caminho de persistência da ECD),
`dashboard.app` (EFD/ECF nos uploads) e as fixtures de vários testes.

## Decisões não óbvias e armadilhas

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
- **Assimetria proposital**: só a ECD tem leiaute YAML e campos nomeados;
  EFD e ECF entregam `_campos` posicionais — quem consome conta posições na
  mão (é o que `extrair_resumo` faz).
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
