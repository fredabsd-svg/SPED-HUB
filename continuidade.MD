# SPED-HUB — Continuidade do Projeto

**Data:** 2025-07-26

## Objetivo da Alteração Atual

**Fase 10 — Webhooks, Multi-ECD Frontend, Layout Customizável e Cron:**
1. Sistema de Webhooks — novo módulo `src/webhooks/`, modelo `WebhookRegistration`, endpoints CRUD na API REST v1, dispatch assíncrono com HMAC
2. Frontend Multi-ECD lado a lado — rota `/comparar` com seleção de até 5 ECDs, gráficos Chart.js comparativos de Balanço, DRE e DFC
3. Layout customizável — rota `/layout` com toggle de colunas visíveis por tipo de relatório, persistência em localStorage
4. Agendamento cron — script `scripts/cron-import.sh` para importação automática via crontab
5. Bump v0.8.0 — 162 testes (100% passando)

## Status Técnico

| Componente | Status |
|---|---|
| `pyproject.toml` | ✅ Atualizado (v0.8.0) |
| `src/layouts/ecd_v9.yml` | ✅ 30 registros (Fase 1) |
| `src/parsers/ecd.py` | ✅ Streaming + herança + parse_em_lotes + contar_registros (Fase 9) |
| `src/parsers/efd.py` | ✅ Parser EFD-Contribuições (Fase 4) |
| `src/parsers/ecf.py` | ✅ Parser ECF (Fase 4) |
| `src/db/models.py` | ✅ 19 modelos + ApiKey + WebhookRegistration (Fase 10) |
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
| `src/dashboard/app.py` | ✅ FastAPI app — 39 rotas incluindo /comparar, /layout, /api/comparar (Fase 10) |
| `src/dashboard/services.py` | ✅ +multi-ECD, +layout customizável (Fase 7) |
| `src/dashboard/templates/*` | ✅ Auth, upload, partials, notas, +comparar, +layout (Fase 10) |
| `src/api/__init__.py` | ✅ API Key auth (Fase 7) |
| `src/api/routes.py` | ✅ 14 endpoints REST v1 + webhooks CRUD + eventos (Fase 10) |
| `src/api/graphql.py` | ✅ GraphQL API v2 — 14 queries (Fase 9) |
| `src/webhooks/__init__.py` | ✅ WebhookService + WebhookEvent + dispatch HMAC (Fase 10) |
| `src/watchdog.py` | ✅ Importação automática por polling (Fase 7) |
| `scripts/cron-import.sh` | ✅ Script de agendamento cron (Fase 10) |
| `Dockerfile` | ✅ Multi-stage + watchdog (Fase 7) |
| `docker-compose.yml` | ✅ Produção com nginx + certbot (Fase 7) |
| `nginx.conf` | ✅ Proxy reverso + SSL hardening (Fase 7) |
| `.github/workflows/ci.yml` | ✅ CI/CD pipeline (Fase 5) |
| `tests/test_fase10.py` | ✅ 28 testes (Fase 10) |
| **Total** | **162 testes — 100% passando** |

## O que foi entregue na Fase 10

### 1. Sistema de Webhooks (`src/webhooks/__init__.py`)
- Modelo `WebhookRegistration` no banco (url, eventos JSON, secret HMAC, descricao, ativo)
- `WebhookService`: registrar, listar, atualizar, remover, dispatch assíncrono
- `WebhookEvent`: dataclass com tipo, dados e timestamp
- Dispatch via httpx com timeout de 10s, header `X-SPED-HUB-Signature` com HMAC-SHA256
- 3 eventos suportados: `ecd.importada`, `ecd.validada`, `relatorio.gerado`

### 2. API REST v1 — Webhooks CRUD
- `GET /api/v1/webhooks/eventos` — lista eventos disponíveis
- `GET /api/v1/webhooks` — lista webhooks registrados
- `POST /api/v1/webhooks` — registra novo webhook
- `PUT /api/v1/webhooks/{id}` — atualiza webhook
- `DELETE /api/v1/webhooks/{id}` — remove webhook

### 3. Frontend Multi-ECD (`/comparar`)
- Template `comparar.html` com Alpine.js + Chart.js
- Seleção de até 5 ECDs via checkboxes
- Gráficos de barras comparativos: Balanço (Ativo/Passivo/PL), DRE (Receita/Resultado), DFC (Operacional/Investimento/Financiamento)
- Tabelas comparativas com totais

### 4. Layout Customizável (`/layout`)
- Template `layout.html` com Alpine.js
- 4 abas: Balanço, DRE, DFC, Diário
- Toggle de visibilidade por coluna
- Persistência em localStorage
- Botão "Restaurar Padrão"

### 5. Agendamento Cron
- Script `scripts/cron-import.sh` com suporte a variáveis de ambiente
- Modo `--once` integrado ao watchdog existente
- Log com timestamp

## Próximos Passos (Fase 11+)

1. Deploy real em VPS/cloud com domínio e SSL
2. Suporte a ECDs reais de clientes (testes com arquivos >100MB)
3. Relatórios customizáveis com drag & drop de colunas no frontend
4. Dashboard de webhooks (monitoramento de entregas, retries)
5. Integração nativa com sistemas de terceiros (Domínio, Questor, Alterdata)
6. Modo multi-tenancy com isolamento por escritório

## Pendências

- Deploy real em VPS/cloud (configuração de domínio + SSL)
- Suporte a ECDs reais de clientes (arquivos grandes)
- Dashboard de monitoramento de webhooks
- Modo multi-tenancy com isolamento por escritório