# filters

## O que faz

Motor de filtros único para todos os relatórios: `FilterCriteria` descreve
16 tipos de filtro combináveis e serializáveis em JSON (visões salvas);
`FilterEngine` os aplica sobre saldos periódicos (I155),
lançamentos/partidas (I250) e saldos de resultado (I355), sempre escopado a
uma ECD. Também gera a descrição legível dos filtros para o cabeçalho dos
relatórios.

## O que expõe

`engine.py` (arquivo único)

| Símbolo | Para quê |
|---|---|
| `FilterCriteria` | Dataclass de critérios; `to_dict()`/`from_dict()` para JSON. |
| `FilterEngine(session, ecd_id)` | `aplicar_saldos` (linhas cruas do I155), `aplicar_lancamentos`, `aplicar_saldos_resultado`, `descricao_filtros`; `plano`, `hierarquia`, `contas_selecionadas` e `linhas_de_saldo`, a matéria-prima de `src.reports.saldos.consolidar`; `correcoes_de_superior` (analíticas que o J100 agrupa noutra sintética, ADR 0012). |
| `tem_criterio_de_conta(criterios)` | Separa "sem critério de conta" de "critério que não casou com nada". |

Os 16 tipos: conta (exata, prefixo, intervalo, nome), natureza, classificação
S/A, nível (exato/até), subárvore, conta referencial (I051), aglutinação
(I052), centro de custo, período, valor (mín/máx, só débitos/só créditos),
histórico (texto, padronizado, sem histórico), tipo de lançamento N/E/X,
participante, ocultar sem movimento/saldo zero, e flags de auditoria (valores
redondos acima de um limite, fins de semana).

## Depende de / quem depende

Depende de `db.models`, SQLAlchemy, `unidecode` e da `Hierarquia` de
`src/reports/saldos.py` (a árvore do plano de contas, usada na subárvore).

Quem depende: os relatórios de `reports/`, `dashboard` (app e services) e
`cli`.

## Decisões não óbvias e armadilhas

- **`hierarquia()` segue o J100 onde ele contradiz o I050** (ADR 0012): só
  para analítica aglutinada no próprio código, com superior do J100 que é
  sintética do plano e da mesma natureza. `plano()` continua devolvendo o
  I050 intacto — o relatório do plano de contas mostra o declarado.

- **Semântica fixa: AND entre tipos, OR entre valores do mesmo tipo.** Não
  há OR entre tipos nem negação.
- **Filtros de conta rodam em Python, não em SQL**: o plano de contas é
  carregado uma vez por instância (`_plano_cache`) e os critérios reduzem um
  `set` de `cod_cta`; só o conjunto final vira `IN` na query.
- **Critério de conta que não casa com nada devolve nada.** Até a correção,
  o `if contas:` pulava a cláusula `IN` quando o conjunto ficava vazio, e o
  filtro pela conta "9.9.9" devolvia a escrituração inteira com a aparência
  de um relatório daquela conta. Hoje `tem_criterio_de_conta` separa "nenhum
  critério de conta" (sem `IN`, devolve tudo) de "critério sem
  correspondência" (lista vazia) — nos saldos, nas partidas e no I355.
- **Subárvore segue o `COD_CTA_SUP`**, pela `Hierarquia` de
  `src/reports/saldos.py`, não o prefixo do código: o código é livre no
  leiaute, e "111001" pode ser filha de "11" sem começar por "11.".
- **`aplicar_saldos` devolve linhas, não saldos.** Uma linha por (conta,
  período, centro de custo), só de analíticas numa ECD conforme o manual.
  Relatório que precisa do saldo de uma conta passa por
  `src.reports.saldos.consolidar`, que lê `linhas_de_saldo` (só centro de
  custo e período, por linha) e aplica os critérios de conta e de valor
  sobre o saldo consolidado.
- **O período vale para o I355** (`aplicar_saldos_resultado`) pela data do
  encerramento (`dt_res`). Antes era ignorado, e a DRE imprimia no
  cabeçalho um período que os números não respeitavam.
- **`hist_texto` usa `ilike`, não `like`**: o LIKE do SQLite é
  case-insensitive para ASCII e o do Postgres não — com `like`, buscar
  "recebi" funcionava em desenvolvimento e devolvia nada em produção.
- **Busca por nome ignora acento e caixa** (`unidecode` dos dois lados).
- **Os flags de auditoria são portáveis.** `fins_de_semana` usa
  `extract("dow", ...)`, que o SQLAlchemy traduz para `STRFTIME('%w')` no
  SQLite e `EXTRACT(dow)` no Postgres (0 = domingo, 6 = sábado nos dois).
  `vl_redondo_acima` compara o valor com o próprio `round()`. Antes, o
  `strftime` só existia no SQLite, e o `vl_dc % 1 == 0` era verdadeiro para
  todo valor no SQLite (o `%` converte para inteiro) e erro de operador no
  Postgres. `tests/test_multibackend.py::TestFiltrosDeAuditoria` roda nos
  dois bancos.
- **`to_dict` só grava o que difere do padrão**: a visão salva é compacta,
  mas um flag explicitamente `False` não sobrevive à ida e volta.
- `FilterCriteria()` vazio significa "sem filtro": devolve tudo da ECD.

## Como testar isoladamente

```bash
pytest tests/test_filters.py -q            # 16 tipos, serialização, combinações
pytest tests/test_multibackend.py -q       # mesmos critérios em SQLite e Postgres
```

A fixture de `tests/test_filters.py` monta banco em memória a partir de
`tests/fixtures/ecd_sample.txt`.

## O que não faz

- Não agrega nem soma — hierarquia e totais são assunto de `reports/`.
- Não pagina nem aceita ordenação configurável.
- Não faz o CRUD das visões salvas (`filter_views`): oferece a serialização;
  a persistência fica fora do módulo.
- Não valida critérios: nível inexistente ou conta errada só devolvem
  resultado vazio.
