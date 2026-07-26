# SPED-HUB — Continuidade do Projeto

**Data:** 2025-07-26

## Objetivo da Alteração Atual

**Fase 9 — GraphQL API v2, Exportação Multi-formato e Parser Streaming:**
1. GraphQL API v2 com strawberry-graphql — schema completo (empresas, ECDs, balanço, DRE, DFC, diário, KPIs, notas, validações, evolução multi-período)
2. Exportação multi-formato em lote — endpoint `/api/export/multi-formato` que gera ZIP com PDF, XLSX e CSV para uma ECD
3. Parser streaming com `parse_em_lotes()` e `contar_registros()` para arquivos >100MB
4. `export_xlsx_to_buffer()` no ExportEngine para geração de XLSX em memória
5. DB_PATH configurável via env var `SPED_HUB_DB` (GraphQL e REST v1)
6. Bump v0.7.0 — 134 testes (100% passando)

## Status Técnico

| Componente | Status |
|---|---|
| `pyproject.toml` | ✅ Atualizado (v0.7.0, +strawberry-graphql) |
| `src/layouts/ecd_v9.yml` | ✅ 30 registros (Fase 1) |
| `src/parsers/ecd.py` | ✅ Streaming + herança + **parse_em_lotes + contar_registros (Fase 9)** |
| `src/parsers/efd.py` | ✅ Parser EFD-Contribuições (Fase 4) |
| `src/parsers/ecf.py` | ✅ Parser ECF (Fase 4) |
| `src/db/models.py` | ✅ 18 modelos + ApiKey (Fase 7) |
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
| `src/reports/export_engine.py` | ✅ +base_url + **export_xlsx_to_buffer (Fase 9)** |
| `src/reports/templates/*` | ✅ +fontes Inter, +colunas período anterior (Fase 6) |
| `src/validators/integridade.py` | ✅ 7 validações (Fase 1) |
| `src/cli.py` | ✅ CLI completa (Fase 1-2) |
| `src/dashboard/app.py` | ✅ FastAPI app — **+GraphQL router, +multi-formato, 35 rotas (Fase 9)** |
| `src/dashboard/services.py` | ✅ +multi-ECD, +layout customizável (Fase 7) |
| `src/dashboard/templates/*` | ✅ Auth, upload, partials, notas (Fase 6) |
| `src/api/__init__.py` | ✅ API Key auth (Fase 7) |
| `src/api/routes.py` | ✅ 12 endpoints REST v1 — **DB_PATH configurável (Fase 9)** |
| `src/api/graphql.py` | ✅ **GraphQL API v2 — 14 queries (Fase 9)** |
| `src/watchdog.py` | ✅ Importação automática por polling (Fase 7) |
| `Dockerfile` | ✅ Multi-stage + watchdog (Fase 7) |
| `docker-compose.yml` | ✅ Produção com nginx + certbot (Fase 7) |
| `nginx.conf` | ✅ Proxy reverso + SSL hardening (Fase 7) |
| `.github/workflows/ci.yml` | ✅ CI/CD pipeline (Fase 5) |
| `tests/test_fase9.py` | ✅ **27 testes (Fase 9)** |
| **Total** | **134 testes — 100% passando** |

## O que foi entregue na Fase 9

### 1. GraphQL API v2 (`src/api/graphql.py`)
- Schema strawberry-graphql com 14 queries: `health`, `empresas`, `empresa`, `ecds`, `ecd`, `balanco`, `dre`, `dfc`, `diario`, `kpis`, `notas`, `validar`, `evolucaoMulti`
- Tipagem forte com strawberry types: `EmpresaType`, `ECDType`, `BalancoType`, `DREType`, `DFCType`, `DiarioType`, `KPIsType`, `NotaType`, `ValidacaoType`, `EvolucaoMultiType`
- Paginação via `PageInfo` em `empresas` e `ecds`
- Integrado ao FastAPI em `/api/v2/graphql` (GraphiQL incluso)
- DB_PATH configurável via `SPED_HUB_DB` (env var)

### 2. Exportação Multi-formato (`/api/export/multi-formato`)
- Endpoint REST que gera ZIP com PDF, XLSX e CSV para balanço, DRE e DFC
- Query param `formatos` (pdf,xlsx,csv) para selecionar formatos
- `export_xlsx_to_buffer()` no ExportEngine para geração em memória

### 3. Parser Streaming (`src/parsers/ecd.py`)
- `parse_em_lotes(caminho, tamanho_lote)` — generator de lotes para arquivos grandes
- `contar_registros(caminho)` — contagem sem armazenar em memória

### 4. Validação
- 134/134 testes passando (107 anteriores + 27 novos)
- Nenhuma regressão detectada

## Próximos Passos (Fase 10+)

1. Deploy real em VPS/cloud com domínio e SSL
2. Suporte a ECDs reais de clientes (testes com arquivos >100MB)
3. Agendamento de importação via cron (alternativa ao watchdog)
4. Relatórios customizáveis com drag & drop de colunas no frontend
5. Integração com sistemas de terceiros via webhooks
6. Frontend para multi-ECD lado a lado (endpoint já existe)
7. Frontend para layout customizável (endpoint já existe)

## Pendências

- Deploy real em VPS/cloud (configuração de domínio + SSL)
- Suporte a ECDs reais de clientes (arquivos grandes)
- Frontend para multi-ECD lado a lado (endpoint já existe)
- Frontend para layout customizável (endpoint já existe)