# dashboard

## O que faz

É a interface web da plataforma: FastAPI + Jinja2 + HTMX + Alpine.js. Serve
as páginas (dashboard, upload, comparação, auditoria, monitoramento),
responde parciais HTMX e JSON para gráficos, e monta o app único que também
inclui a API REST v1 e o GraphQL v2. A agregação de dados (KPIs, evolução,
composição, comparativos) fica em `services.py`; o cálculo contábil em si
vem de `reports/`.

## O que expõe

`app.py` — o `app` FastAPI (entry point do Docker e do script
`sped-hub-dashboard`), com rotas em grupos:

| Grupo | Rotas |
|---|---|
| Autenticação | `/login`, `/register`, `POST /api/login`, `POST /api/register`, `/logout` |
| Páginas | `/`, `/upload`, `/comparar`, `/layout`, `/api-keys`, `/webhooks`, `/auditoria`, `/monitoring` |
| Upload | `POST /api/upload` (ECD, importa), `/api/upload-efd`, `/api/upload-ecf` (só resumo), `/api/upload-async` + `/api/jobs/*` |
| Dados (parciais HTMX/JSON) | `/api/kpis`, `/api/balanco`, `/api/dre`, `/api/dfc`, `/api/diario`, `/api/graficos`, `/api/ecds`, `/api/filtros/aplicar`, `/api/multi-ecd`, `/api/comparar`, `/api/notas` |
| Exportação | `/api/export/pdf`, `/xlsx`, `/multi-formato` (ZIP), `/lote` |
| Administração (admin) | `/api/audit/*`, `/api/email/*`, `/api/worker/status`, `/api/monitoring/*`, `/api/health/full` |

`services.py` — `DashboardService` (KPIs, evolução patrimonial e
multi-período, composição do ativo, waterfall DRE, DFC, comparativos, notas
explicativas automáticas) e os dataclasses `KPICard`/`DashboardData`. Em
`static/vendor/` vivem htmx, Alpine, Chart.js e SortableJS versionados.

## Depende de / quem depende

Depende de quase tudo: `api`, `auth`, `audit`, `ratelimit`, `cache`,
`db.models`, `ecd_importer`, `uploads`, `filters.engine`,
`parsers.efd/ecf`, `reports/*`, `monitoring`, `email_service`, `settings`,
`logging_config`.

Ninguém importa o módulo em produção — quem o consome é o servidor ASGI
(`uvicorn src.dashboard.app:app`) e os testes. É o topo da pilha.

## Decisões não óbvias e armadilhas

- **Multi-tenancy por `escritorio_id`**: o middleware extrai
  `ecd_id`/`ecd_ids` da query string e valida com
  `usuario_pode_acessar_ecd`; ECD de outro escritório responde **404, não
  403** — não vaza que ela existe. Listagens passam por
  `aplicar_escopo_empresas`; o upload grava o `escritorio_id` do usuário
  logado.
- **Três middlewares em camadas**: métricas (sem query string nem payload),
  rate limit por IP e auth. O escopo `login` tem cota própria, bem mais
  apertada — `/api/login` e `/api/register` são públicos, então sem limite
  por IP varrer senhas não custaria nada. Os headers `X-RateLimit-*` usam
  `setdefault` para não sobrescrever a cota anunciada pelo limitador por API
  Key.
- **Sessão**: cookie `sped_hub_session` httponly, `samesite=lax`, 24 h.
  Login, falha de login e logout vão para a auditoria.
- **O handler `htmx:beforeSwap` no `base.html`** converte respostas JSON:
  `redirect` vira navegação, erro vira alerta escapado, sucesso com mensagem
  vira `.alert-success`. Sem ele o HTMX injetava o JSON cru na página. Os
  formulários de login/registro declaram `method="post" action=...` — o
  fallback que impede a senha de ir para a URL se o script não carregar.
- **Assets vendorizados, nunca CDN**: sem acesso externo a aplicação
  degradava em silêncio e cada página carregava versão diferente.
- **`get_composicao_ativo` tem trava de ciclo** ao subir a hierarquia do
  plano de contas. A hierarquia vem do arquivo do cliente; um ciclo A→B→A
  fazia o laço rodar para sempre e o dashboard inteiro parava para todos os
  usuários (event loop único).
- **O banco vem de `database_reference()`** e é relido a cada uso — antes só
  `SPED_HUB_DB` era lido e `DATABASE_URL` era ignorada em silêncio.
- As APIs externas (`/api/v1`, `/api/v2/graphql`) têm autenticação própria
  por API Key — o middleware do dashboard as ignora de propósito.

## Como testar isoladamente

```bash
pytest tests/test_fase13.py tests/test_fase14.py tests/test_fase16.py -q
pytest tests/test_hardening.py tests/test_vendor_assets.py -q
pytest tests/test_hierarquia_ciclica.py -q       # trava de ciclo
pytest tests/test_fase7.py tests/test_fase10.py -q   # DashboardService
```

Navegador de verdade: `pytest -m e2e` (`tests/test_e2e_playwright.py`, sobe
uvicorn real).

## O que não faz

- Não implementa hash de senha nem tokens (`auth`), nem o limitador em si
  (`ratelimit`), nem parsing/importação (`parsers`/`ecd_importer`), nem
  cálculo contábil (`reports`).
- Não persiste EFD/ECF: o upload devolve o resumo e apaga o arquivo.
- Não persiste preferências de layout.
- A lógica da API v1 e do GraphQL vive em `src/api/`; aqui só são montadas.
