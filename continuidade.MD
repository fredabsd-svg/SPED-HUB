# SPED-HUB — Continuidade do Projeto

**Data:** 2025-07-26

## Objetivo da Alteração Atual

**Fase 13 — Rate Limiting, Logs de Auditoria e Testes E2E:**
1. Rate Limiting por API Key — `RateLimiter` com janela deslizante em memória, `RateLimitService` para configuração persistente, `RateLimitConfig` no banco, headers `X-RateLimit-*`, HTTP 429 quando excedido, endpoints REST de configuração (`GET/PUT/DELETE /api/v1/api-keys/{id}/rate-limit`, `GET .../rate-limit/status`)
2. Logs de Auditoria — modelo `AuditLog` no banco, `AuditService` com `registrar()`, `listar()`, `contar()`, `estatisticas()`, `limpar_antigos()`, auditoria automática em login (sucesso/falha), logout, registro, upload de ECD, exportação PDF/XLSX, criação/revogação de API Keys, dashboard de auditoria em `/auditoria` com filtros e estatísticas
3. Testes E2E — suite completa via `FastAPI TestClient`: fluxo de autenticação (registro→login→acesso→logout), API REST com API Key (incluindo rate limiting 429), dashboard de auditoria, configuração de rate limit via API
4. Bump v0.11.0 — 257 testes (100% passando)

## Status Técnico

| Componente | Status |
|---|---|
| `pyproject.toml` | ✅ Atualizado (v0.11.0) |
| `src/layouts/ecd_v9.yml` | ✅ 30 registros (Fase 1) |
| `src/parsers/ecd.py` | ✅ Streaming + herança + parse_em_lotes + contar_registros (Fase 9) |
| `src/parsers/efd.py` | ✅ Parser EFD-Contribuições (Fase 4) |
| `src/parsers/ecf.py` | ✅ Parser ECF (Fase 4) |
| `src/db/models.py` | ✅ 23 modelos + RateLimitConfig + AuditLog (Fase 13) |
| `src/db/repository.py` | ✅ CRUD + consultas (Fase 1) |
| `src/auth/__init__.py` | ✅ AuthService + MultiTenantMiddleware + TenantFilter + contextvars + login retorna id/email (Fase 13) |
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
| `src/ratelimit/__init__.py` | ✅ RateLimiter + RateLimitService + RateLimitInfo (Fase 13) |
| `src/audit/__init__.py` | ✅ AuditService + logs + estatísticas + limpeza (Fase 13) |
| `src/dashboard/app.py` | ✅ FastAPI app — 48 rotas incluindo /auditoria, /api/audit/* (Fase 13) |
| `src/dashboard/services.py` | ✅ +multi-ECD, +layout customizável (Fase 7) |
| `src/dashboard/templates/*` | ✅ Auth, upload, partials, notas, +comparar, +layout, +webhooks, +api_keys, +auditoria (Fase 13) |
| `src/api/__init__.py` | ✅ API Key auth + ApiKeyService + rate limiting integrado + auditoria (Fase 13) |
| `src/api/routes.py` | ✅ 28 endpoints REST v1 + api-keys CRUD + rate-limit config + audit logs (Fase 13) |
| `src/api/graphql.py` | ✅ GraphQL API v2 — 14 queries (Fase 9) |
| `src/webhooks/__init__.py` | ✅ WebhookService + retry backoff + WebhookDelivery tracking (Fase 11) |
| `src/watchdog.py` | ✅ Importação automática por polling (Fase 7) |
| `scripts/cron-import.sh` | ✅ Script de agendamento cron (Fase 10) |
| `Dockerfile` | ✅ Multi-stage + watchdog (Fase 7) |
| `docker-compose.yml` | ✅ Produção com nginx + certbot (Fase 7) |
| `nginx.conf` | ✅ Proxy reverso + SSL hardening (Fase 7) |
| `.github/workflows/ci.yml` | ✅ CI/CD pipeline (Fase 5) |
| `tests/test_fase13.py` | ✅ 43 testes (Fase 13) |
| **Total** | **257 testes — 100% passando** |

## O que foi entregue na Fase 13

### 1. Rate Limiting por API Key
- `RateLimitConfig` — modelo no banco com `limite` e `janela` por API Key
- `RateLimiter` — contador em memória com janela deslizante, thread-safe via `threading.Lock`
  - `verificar(api_key_id)` → `(permitido, RateLimitInfo)` — incrementa e verifica
  - `get_info(api_key_id)` → status sem incrementar
  - `reset(api_key_id)` — limpa contador (para testes/admin)
  - `get_limiter()` / `init_limiter()` — instância global
- `RateLimitService` — CRUD de configurações: `configurar()`, `obter()`, `remover()`, `listar()`
- Integração no `requer_api_key` — verifica rate limit antes de processar a requisição
- HTTP 429 com headers `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, `Retry-After`
- Default: 100 requisições por 60 segundos (quando sem configuração)
- Endpoints REST: `GET/PUT/DELETE /api/v1/api-keys/{id}/rate-limit`, `GET .../rate-limit/status`

### 2. Logs de Auditoria
- `AuditLog` — modelo no banco com `usuario_id`, `usuario_email`, `api_key_id`, `acao`, `recurso`, `metodo`, `ip`, `status_code`, `detalhes` (JSON), `criado_em`
- `AuditService`:
  - `registrar()` — cria log de auditoria
  - `listar()` — lista com filtros (usuario_id, acao, dt_ini, dt_fin) e paginação
  - `contar()` — total com filtros
  - `estatisticas()` — agregações: total, por_acao, por_status, usuarios_unicos, ips_unicos, erros
  - `limpar_antigos(dias)` — remove logs antigos
- Auditoria automática em:
  - Login (sucesso e falha) — `auth.login`
  - Logout — `auth.logout`
  - Registro — `auth.register`
  - Upload de ECD — `ecd.upload`
  - Exportação PDF/XLSX — `relatorio.export`
  - Criação de API Key — `apikey.create`
  - Revogação de API Key — `apikey.revoke`
  - Rate limit excedido — `api.rate_limited`
- Dashboard de auditoria em `/auditoria` com:
  - Cards de estatísticas (total, usuários, IPs, erros)
  - Filtros por ação
  - Tabela paginada com data, ação, recurso, método, usuário, IP, status
  - API: `GET /api/audit/logs`, `GET /api/audit/stats`, `POST /api/audit/limpar`

### 3. Testes E2E
- `TestE2EAuth` — fluxo completo de autenticação (registro→login→acesso→logout, login falho)
- `TestE2EAPI` — API REST com API Key (acesso permitido, 401 sem key, 403 key inválida, 429 rate limit)
- `TestE2EAuditDashboard` — dashboard de auditoria (requer auth, logs, stats)
- `TestE2ERateLimitConfig` — configuração de rate limit via API (criar, obter, status, remover)
- Usa `FastAPI TestClient` para simular fluxo HTTP completo sem browser

### 4. Correções de Bugs
- `Sessao.expirado` — corrigido `TypeError` ao comparar datetimes com e sem timezone (SQLite não preserva tzinfo)
- `AuthService.login()` — agora retorna `(usuario, token, usuario_id, usuario_email)` para evitar `DetachedInstanceError` quando a sessão é fechada
- `requer_api_key()` — agora usa `SPED_HUB_DB` env var em vez de hardcoded `sped_hub.db`

## Próximos Passos (Fase 14+)

1. Deploy real em VPS/cloud com domínio e SSL
2. Suporte a ECDs reais de clientes (testes com arquivos >100MB)
3. Integração nativa com sistemas de terceiros (Domínio, Questor, Alterdata)
4. Testes E2E com Playwright (browser real, screenshots)
5. Notificações por email (alertas de erro, relatórios agendados)
6. API de importação assíncrona (background tasks para ECDs grandes)
7. Cache Redis para queries frequentes

## Pendências

- Deploy real em VPS/cloud (configuração de domínio + SSL)
- Suporte a ECDs reais de clientes (arquivos grandes)
- Integração com sistemas de terceiros
- Rate limiting em memória não persiste entre restarts (aceitável para v1)
