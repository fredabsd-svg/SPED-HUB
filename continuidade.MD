# SPED-HUB — Continuidade do Projeto

**Data:** 2025-07-26

## Objetivo da Alteração Atual

**Fase 12 — Middleware Multi-Tenant, API Keys UI e Testes de Carga:**
1. Middleware de isolamento multi-tenant — `MultiTenantMiddleware` com `contextvars`, `TenantFilter` para queries SQLAlchemy, injeção automática de `escritorio_id` via token de sessão
2. API Keys UI — nova rota `/api-keys` com template Alpine.js, CRUD completo (`POST/GET/DELETE /api/v1/api-keys`), geração de chave com prefixo `spd_`, exibição única da chave, revogação
3. Testes de carga — gerador de ECDs sintéticas configuráveis (até 3000 contas, 10000 lançamentos), benchmark de parsing e importação, assertions de performance
4. Bump v0.10.0 — 214 testes (100% passando)

## Status Técnico

| Componente | Status |
|---|---|
| `pyproject.toml` | ✅ Atualizado (v0.10.0) |
| `src/layouts/ecd_v9.yml` | ✅ 30 registros (Fase 1) |
| `src/parsers/ecd.py` | ✅ Streaming + herança + parse_em_lotes + contar_registros (Fase 9) |
| `src/parsers/efd.py` | ✅ Parser EFD-Contribuições (Fase 4) |
| `src/parsers/ecf.py` | ✅ Parser ECF (Fase 4) |
| `src/db/models.py` | ✅ 21 modelos + Escritorio + WebhookDelivery (Fase 11) |
| `src/db/repository.py` | ✅ CRUD + consultas (Fase 1) |
| `src/auth/__init__.py` | ✅ AuthService + MultiTenantMiddleware + TenantFilter + contextvars (Fase 12) |
| `src/filters/engine.py` | ✅ 16 tipos de filtro (Fase 1) |
| `src/reports/base.py` | ✅ Convenção de sinais (Fase 1) |
| `src/reports/balancete.py` | ✅ Balancete F8 (Fase 8) |
| `src/reports/razao.py` | ✅ Razão F11 (Fase 1) |
| `src/reports/balanco.py` | ✅ +período anterior, +J100/J150 (Fase 5) |
| `src/reports/dre.py` | ✅ +período anterior comparativo (Fase 5) |
| `src/reports/dfc.py` | ✅ +período anterior comparativo (Fase 6) |
| `src/reports/diario.py` | ✅ Livro Diário F12 (Fase 2) |
| `src/reports/export_engine.py` | ✅ +base_url, +export_xlsx_to_buffer (Fase 9) |
| `src/reports/templates/*` | ✅ +fontes Inter, +colunas período anterior (Fase 6) |
| `src/validators/integridade.py` | ✅ 7 validações (Fase 1) |
| `src/cli.py` | ✅ CLI completa (Fase 1-2) |
| `src/dashboard/app.py` | ✅ FastAPI app — 41 rotas incluindo /api-keys (Fase 12) |
| `src/dashboard/services.py` | ✅ +multi-ECD, +layout customizável (Fase 7) |
| `src/dashboard/templates/*` | ✅ Auth, upload, partials, notas, +comparar, +layout com SortableJS, +webhooks, +api_keys (Fase 12) |
| `src/api/__init__.py` | ✅ API Key auth + ApiKeyService (Fase 12) |
| `src/api/routes.py` | ✅ 20 endpoints REST v1 + api-keys CRUD (Fase 12) |
| `src/api/graphql.py` | ✅ GraphQL API v2 — 14 queries (Fase 9) |
| `src/webhooks/__init__.py` | ✅ WebhookService + retry backoff + WebhookDelivery tracking (Fase 11) |
| `src/watchdog.py` | ✅ Importação automática por polling (Fase 7) |
| `scripts/cron-import.sh` | ✅ Script de agendamento cron (Fase 10) |
| `Dockerfile` | ✅ Multi-stage + watchdog (Fase 7) |
| `docker-compose.yml` | ✅ Produção com nginx + certbot (Fase 7) |
| `nginx.conf` | ✅ Proxy reverso + SSL hardening (Fase 7) |
| `.github/workflows/ci.yml` | ✅ CI/CD pipeline (Fase 5) |
| `tests/test_fase12.py` | ✅ 31 testes (Fase 12) |
| **Total** | **214 testes — 100% passando** |

## O que foi entregue na Fase 12

### 1. Middleware de Isolamento Multi-Tenant
- `MultiTenantMiddleware` — middleware ASGI que extrai `escritorio_id` do token de sessão e injeta no `contextvars`
- `TenantFilter` — aplica `WHERE escritorio_id = ?` automaticamente em queries SQLAlchemy (Empresa, ECD, PlanoConta, Lancamento, Partida)
- `set_tenant_id()` / `get_tenant_id()` — API de contexto thread-safe e async-safe
- `requer_autenticacao` atualizado para definir tenant automaticamente no request
- Retrocompatibilidade: sem tenant (None) = sem filtro, dados existentes continuam funcionando

### 2. API Keys UI (`/api-keys`)
- Template `api_keys.html` com Alpine.js — formulário de criação, lista de chaves, revogação
- `ApiKeyService` — criar (com expiração opcional), listar (sem expor chave), revogar, excluir
- Endpoints REST: `GET /api/v1/api-keys`, `POST /api/v1/api-keys`, `DELETE /api/v1/api-keys/{id}`
- Chave exibida apenas uma vez na criação (formato `spd_<64 hex chars>`)
- Navbar atualizada em todos os templates com link para API Keys

### 3. Testes de Carga e Stress
- Gerador de ECDs sintéticas com tamanho configurável (contas, lançamentos)
- Benchmark de parsing: 100 contas/500 lançamentos < 5s, 500/2000 < 15s, 3000/10000 < 60s
- Benchmark de importação completa no banco: 200 contas/1000 lançamentos < 10s
- Teste de contagem de registros em ECD grande
- Testes de integridade do modelo Escritorio (slug único, relações)

## Próximos Passos (Fase 13+)

1. Deploy real em VPS/cloud com domínio e SSL
2. Suporte a ECDs reais de clientes (testes com arquivos >100MB)
3. Integração nativa com sistemas de terceiros (Domínio, Questor, Alterdata)
4. Testes end-to-end com Playwright (dashboard, upload, exportação)
5. Rate limiting nas API Keys
6. Logs de auditoria (quem acessou o quê, quando)

## Pendências

- Deploy real em VPS/cloud (configuração de domínio + SSL)
- Suporte a ECDs reais de clientes (arquivos grandes)
- Integração com sistemas de terceiros