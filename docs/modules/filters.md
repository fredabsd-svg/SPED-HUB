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
| `FilterEngine(session, ecd_id)` | `aplicar_saldos`, `aplicar_lancamentos`, `aplicar_saldos_resultado`, `descricao_filtros`. |

Os 16 tipos: conta (exata, prefixo, intervalo, nome), natureza, classificação
S/A, nível (exato/até), subárvore, conta referencial (I051), aglutinação
(I052), centro de custo, período, valor (mín/máx, só débitos/só créditos),
histórico (texto, padronizado, sem histórico), tipo de lançamento N/E/X,
participante, ocultar sem movimento/saldo zero, e flags de auditoria (valores
redondos acima de um limite, fins de semana).

## Depende de / quem depende

Depende de `db.models`, SQLAlchemy e `unidecode`.

Quem depende: os relatórios de `reports/`, `dashboard` (app e services) e
`cli`.

## Decisões não óbvias e armadilhas

- **Semântica fixa: AND entre tipos, OR entre valores do mesmo tipo.** Não
  há OR entre tipos nem negação.
- **Filtros de conta rodam em Python, não em SQL**: o plano de contas é
  carregado uma vez por instância (`_plano_cache`) e os critérios reduzem um
  `set` de `cod_cta`; só o conjunto final vira `IN` na query.
- **Conjunto de contas vazio desliga o `IN`**: critérios de conta que não
  casam com nenhuma conta devolvem *todos* os saldos, não zero — o
  `if contas:` pula a cláusula.
- **`hist_texto` usa `ilike`, não `like`**: o LIKE do SQLite é
  case-insensitive para ASCII e o do Postgres não — com `like`, buscar
  "recebi" funcionava em desenvolvimento e devolvia nada em produção.
- **Busca por nome ignora acento e caixa** (`unidecode` dos dois lados).
- **`fins_de_semana` usa `func.strftime('%w', ...)`** — função do SQLite;
  esse filtro não é portável para Postgres como está.
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
