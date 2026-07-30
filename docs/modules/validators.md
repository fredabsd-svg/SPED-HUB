# validators

## O que faz

Confere a consistência interna de uma ECD já importada e devolve a lista do
que não fecha. São oito validações: partidas dobradas, SI+D−C=SF, movimentos
I250 vs I155, DRE vs I355, Ativo = Passivo + PL, analíticas órfãs,
lançamentos em sintéticas e ciclo na hierarquia do plano de contas. Não
altera nada — só lê e reporta.

## O que expõe

`src/validators/integridade.py`

| Símbolo | Para quê |
|---|---|
| `ValidadorIntegridade(session, ecd_id)` | O serviço. `validar_todas()` roda as oito. |
| `Inconsistencia` | Dataclass: `tipo`, `severidade` (`erro`/`alerta`), `descricao`, `detalhes`. |
| `relatorio(inconsistencias)` | Sumário: totais, status `OK`/`ERROS`, detalhes. |

Os métodos `_validar_*` são internos, mas testados individualmente — cada
validação tem contrato próprio.

## Depende de / quem depende

Depende de `src.db.models` (consultas) e `src.reports.base`
(`valor_sinalizado`).

Consumido por: `cli` (comando `validar`), `api.routes` (REST) e
`api.graphql`.

## Decisões não óbvias e armadilhas

- **A validação (a) agrega no banco, em uma consulta.** Era uma consulta de
  partidas por lançamento: 20.002 consultas para 20.000 lançamentos, medido.
  Numa ECD de 240 mil isso são ~240 mil viagens e ~54 s só nesta validação em
  SQLite local — sobre PostgreSQL em rede, minutos de pura latência. O `HAVING`
  também troca o consumo de memória: só volta lançamento desbalanceado, então a
  memória passa a ser proporcional ao número de defeitos, não ao tamanho da
  escrituração.
- **`LEFT JOIN` e `COALESCE` ali são defensivos, não decisivos.** Com
  `else_=0.0` no `CASE`, a soma só é NULL sem linha nenhuma, e aí o `HAVING`
  também não passa: lançamento sem partida fica de fora dos dois jeitos, como
  já ficava. Ficam porque `INNER JOIN` esconderia esse lançamento do
  agrupamento, e é dele que sairia um "lançamento sem partida" se a validação
  vier a reportá-lo.

- **`erro` versus `alerta` é uma distinção de natureza.** Divergência de
  valor (saldos, DRE, movimentos) pode ser centavo de arredondamento;
  algumas são `alerta`. Partida descasada, balanço que não fecha e
  hierarquia cíclica são `erro`: não têm leitura correta possível.
- **Tolerâncias numéricas são 0,005/0,01** — meio centavo por comparação.
  Apertar demais acusa arredondamento legítimo do arquivo; afrouxar esconde
  lançamento perdido.
- **A validação (h) existe por causa de um travamento real.** Uma ECD com
  conta que era a própria sintética derrubava o dashboard para todos os
  usuários (laço infinito, PR #7). Desde o ADR 0006 a importação recusa
  arquivo com ciclo; a (h) continua para bancos que importaram antes. A
  detecção vive na função pura `encontrar_ciclos` (exportada), percorre o
  grafo funcional `COD_CTA → COD_CTA_SUP` com memoização (O(n)) e reporta
  cada ciclo uma única vez — é a mesma função que o importador usa.
- **(f) e (h) são disjuntas de propósito:** sintética inexistente é órfã,
  não ciclo. Um arquivo pode ter as duas.
- **Validar não bloqueia importar.** A importação aceita o arquivo mesmo com
  inconsistências; a validação é passo separado, chamado por quem quer
  saber. Um `ERROS` no relatório não desfaz nada.

## Como testar isoladamente

```bash
pytest tests/test_validators.py -q
```

A fixture principal importa `tests/fixtures/ecd_sample.txt` (arquivo
consistente — as validações devolvem vazio). `TestHierarquiaCiclica` monta
planos de conta defeituosos diretamente, sem arquivo.

## O que não faz

- Não valida o **arquivo** SPED (layout, campos, assinatura) — isso é
  `parsers` e `uploads`.
- Não corrige nada: reporta e para.
- Não bloqueia importação nem remove ECD inválida do banco.
- Não confere regra fiscal (alíquota, CFOP, prazo de entrega) — só
  consistência contábil interna.
