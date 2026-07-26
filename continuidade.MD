# SPED-HUB — Continuidade do Projeto

**Data:** 2025-07-26

## Objetivo da Alteração Atual

**Fase 11 — Dashboard de Webhooks, Drag & Drop Layout e Multi-Tenancy:**
1. Dashboard de Webhooks — nova rota `/webhooks` com template Alpine.js + Chart.js, estatísticas agregadas (taxa de sucesso, entregas 24h), tabela de entregas com status e filtros
2. Retry com exponential backoff — `WebhookService` com retry automático (2s, 4s, 8s...), tracking via `WebhookDelivery`, retry manual via API
3. Drag & drop de colunas — SortableJS integrado ao template `layout.html`, reordenação persistente em localStorage
4. Multi-tenancy — modelo `Escritorio` com slug único, `escritorio_id` em `Empresa`, `ECD` e `Usuario`, isolamento por escritório com retrocompatibilidade (null = sem escritório)
5. Bump v0.9.0 — 183 testes (100% passando)

## Status Técnico

| Componente | Status |
|---|---|
| `pyproject.toml` | ✅ Atualizado (v0.9.0) |
| `src/layouts/ecd_v9.yml` | ✅ 30 registros (Fase 1) |
| `src/parsers/ecd.py` | ✅ Streaming + herança + parse_em_lotes + contar_registros (Fase 9) |
| `src/parsers/efd.py` | ✅ Parser EFD-Contribuições (Fase 4) |
| `src/parsers/ecf.py` | ✅ Parser ECF (Fase 4) |
| `src/db/models.py` | ✅ 21 modelos + Escritorio + WebhookDelivery (Fase 11) |
| `src/db/repository.py` | ✅ CRUD + consultas (Fase 1) |
| `src/auth/__init__.py` | ✅ AuthService + middleware (Fase 5) |
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
| `src/dashboard/app.py` | ✅ FastAPI app — 40 rotas incluindo /webhooks (Fase 11) |
| `src/dashboard/services.py` | ✅ +multi-ECD, +layout customizável (Fase 7) |
| `src/dashboard/templates/*` | ✅ Auth, upload, partials, notas, +comparar, +layout com SortableJS, +webhooks (Fase 11) |
| `src/api/__init__.py` | ✅ API Key auth (Fase 7) |
| `src/api/routes.py` | ✅ 17 endpoints REST v1 + webhooks dashboard/deliveries/retry (Fase 11) |
| `src/api/graphql.py` | ✅ GraphQL API v2 — 14 queries (Fase 9) |
| `src/webhooks/__init__.py` | ✅ WebhookService + retry backoff + WebhookDelivery tracking (Fase 11) |
| `src/watchdog.py` | ✅ Importação automática por polling (Fase 7) |
| `scripts/cron-import.sh` | ✅ Script de agendamento cron (Fase 10) |
| `Dockerfile` | ✅ Multi-stage + watchdog (Fase 7) |
| `docker-compose.yml` | ✅ Produção com nginx + certbot (Fase 7) |
| `nginx.conf` | ✅ Proxy reverso + SSL hardening (Fase 7) |
| `.github/workflows/ci.yml` | ✅ CI/CD pipeline (Fase 5) |
| `tests/test_fase11.py` | ✅ 21 testes (Fase 11) |
| **Total** | **183 testes — 100% passando** |

## O que foi entregue na Fase 11

### 1. Dashboard de Webhooks (`/webhooks`)
- Template `webhooks.html` com Alpine.js + Chart.js (gráfico doughnut sucesso/falha)
- Estatísticas agregadas: total de webhooks, ativos, taxa de sucesso, entregas 24h
- Tabela de entregas recentes com status (pending/success/failed/retrying), HTTP code, tentativa
- Tabela de webhooks registrados com total de envios e falhas
- API endpoints: `GET /api/v1/webhooks/dashboard`, `GET /api/v1/webhooks/deliveries`, `POST /api/v1/webhooks/retry`

### 2. Retry com Exponential Backoff
- `WebhookService._enviar_com_retry()` — até N tentativas com backoff 2s, 4s, 8s, 16s, 32s (máx 60s)
- Modelo `WebhookDelivery` — histórico completo de cada tentativa (request/response body, status code, erro)
- `WebhookRegistration.max_retries` e `total_falhas` configuráveis
- `retry_failed()` — reenvio manual de entregas com falha

### 3. Drag & Drop de Colunas no Layout
- SortableJS integrado ao `layout.html` com Alpine.js
- Reordenação via drag handle (⋮⋮) com animação e feedback visual
- Persistência da ordem e visibilidade em localStorage
- Botão "Restaurar Padrão" funcional

### 4. Multi-Tenancy
- Modelo `Escritorio` (id, nome, slug único, cnpj, ativo)
- `escritorio_id` (nullable) em `Empresa`, `ECD` e `Usuario`
- Retrocompatibilidade: dados sem escritório continuam funcionando
- Base para isolamento completo entre escritórios contábeis

## Próximos Passos (Fase 12+)

1. Deploy real em VPS/cloud com domínio e SSL
2. Suporte a ECDs reais de clientes (testes com arquivos >100MB)
3. Middleware de isolamento multi-tenant (filtro automático por escritório)
4. Integração nativa com sistemas de terceiros (Domínio, Questor, Alterdata)
5. API de geração de API Keys com UI
6. Testes de carga e stress com ECDs grandes

## Pendências

- Deploy real em VPS/cloud (configuração de domínio + SSL)
- Suporte a ECDs reais de clientes (arquivos grandes)
- Middleware de isolamento multi-tenant ativo
- Integração com sistemas de terceiros