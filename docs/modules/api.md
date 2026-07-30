# api

## O que faz

Implementa a API externa em duas camadas: REST versionada em `/api/v1`
(`routes.py`) e GraphQL em `/api/v2/graphql` (`graphql.py`). O pacote
(`__init__.py`) concentra a autenticação por API Key — geração, hash,
verificação e a dependência FastAPI `requer_api_key` — além do CRUD de
chaves. Cada requisição autenticada passa por rate limiting por chave e
registra auditoria quando bloqueada.

## O que expõe

| Símbolo | Onde | Para quê |
|---|---|---|
| `gerar_api_key()` | `__init__.py` | Gera par (chave `spd_...`, hash SHA-256). |
| `verificar_api_key(chave, hash)` | `__init__.py` | Comparação com `hmac.compare_digest`. |
| `validar_requisicao_api(request, db_path)` | `__init__.py` | Valida chave, expiração e rate limit; aceita sessão admin do dashboard como fallback. |
| `requer_api_key(request)` | `__init__.py` | Dependência FastAPI de todas as rotas protegidas. |
| `ApiKeyService` | `__init__.py` | `criar`, `listar`, `revogar`, `excluir`. |
| `router` (`/api/v1`) | `routes.py` | `/health`, `/empresas`, `/ecds`, `/ecds/{id}/{balanco,dre,dfc,diario,kpis,notas,validar,evolucao-multi}`, `/webhooks/*`, `/api-keys/*`, `/audit/*`. |
| `schema`, `graphql_router` | `graphql.py` | Queries `health`, `empresas`, `ecds`, `balanco`, `dre`, `dfc`, `diario`, `kpis`, `notas`, `validar`, `evolucaoMulti`. |

## Depende de / quem depende

Depende de `db.models`, `settings` (`database_reference`), `ratelimit`,
`audit`, `auth` (fallback de sessão), `webhooks`, `reports/*`,
`dashboard.services`, `validators.integridade`, `version`; externas:
FastAPI, SQLAlchemy, strawberry.

Consumido por `dashboard.app`, que monta os dois routers na aplicação.

## Decisões não óbvias e armadilhas

- **API Key lê; sessão de administrador administra.** As rotas de `/api-keys*`
  e as de cota exigem admin logado no dashboard e **recusam** API Key. Antes
  aceitavam, e a cadeia alcançável com a chave que se entrega a um integrador
  era: criar chaves novas para si (nem revogar a original tirava o acesso),
  listar e revogar as chaves do escritório (derrubando integrações legítimas),
  e elevar a própria cota de rate limit (anulando o limite que a protege).
  Estava registrado como simples lacuna — "não tem escopo por chave" — o que
  subdimensionava: era administração total da instância.
- **`ecd_autorizada` é dependência, não verificação por rota.** São nove rotas
  com `/{ecd_id}` e a décima esqueceria. Escopar só a listagem seria cosmético:
  quem quisesse a escrituração do vizinho pediria o id direto. Há teste por AST
  que falha se uma rota nova não passar pela dependência.
- **Escopo responde 404, nunca 403.** Confirmar que a ECD existe e é de outro
  escritório já é informação; a resposta é idêntica à de ECD inexistente.
- **A contagem também é escopada.** Total sem escopo anunciaria empresas que a
  página nunca mostra, e revelaria quantas o vizinho tem.

- **A chave nunca é armazenada em claro.** Só o SHA-256 vai ao banco; a
  comparação usa `hmac.compare_digest`. A chave completa aparece uma única
  vez, na resposta de `ApiKeyService.criar` — `listar` devolve só o prefixo.
- **Expiração tolera datetime naive do SQLite.** `expira_em` sem tzinfo
  recebe `tzinfo=UTC` antes de comparar; sem isso a comparação naive×aware
  lançava `TypeError`. Coberto em `tests/test_review_regressions.py`.
- **Sessão do dashboard vale como credencial, mas só para admin.** Sem
  `X-API-Key`: admin logado passa, usuário comum recebe 403, anônimo 401.
- **O caminho do banco nunca vem do cliente**: `requer_api_key` existe
  exatamente para isso — o `db_path` sai sempre de `database_reference()`,
  nunca do request.
- **Rate limit bloqueado gera auditoria.** O 429 devolve `X-RateLimit-*` e
  `Retry-After` e registra `api.rate_limited` antes de recusar; sucesso
  grava `ultimo_uso` e incrementa `total_requisicoes`.
- **GraphQL não aceita query via GET** (`allow_queries_via_get=False`) e o
  router inteiro exige `requer_api_key` — sem chave nem sessão, 401.
- **A query `ecds` do GraphQL faz 3 counts por ECD dentro do loop** — N+1
  deliberadamente simples; com muitas ECDs por página o custo cresce linear.
- **Cada request abre engine nova** (`criar_engine` + `init_db` por chamada)
  — não há pool compartilhado no nível do módulo.

## Como testar isoladamente

```bash
pytest tests/test_fase7.py -q      # API Key + REST v1
pytest tests/test_fase9.py -q      # GraphQL
pytest tests/test_fase12.py -q     # ApiKeyService (CRUD)
pytest tests/test_fase13.py -q     # rate limit por chave
pytest tests/test_review_regressions.py -k ApiKey -q   # expiração naive
```

## O que não faz

- Não escreve dados contábeis: as rotas de ECD/relatórios são somente
  leitura; importação é do dashboard/CLI.
- Não escopa chave **sem dono** (`escritorio_id` nulo): ela lê tudo. É o
  comportamento de toda chave criada antes da coluna existir, preservado para
  não invalidar integração em produção. Chave nova deve ser criada com dono.
- Não pagina o Livro Diário no banco: a paginação é em memória.
- Não versiona schema GraphQL nem oferece mutations — só queries.
