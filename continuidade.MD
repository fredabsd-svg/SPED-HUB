# SPED-HUB — Continuidade do Projeto

**Data:** 2025-07-25

## Objetivo da Alteração Atual

**Fase 7 — API REST, Deploy Produção e Maturidade Operacional:**
1. API REST v1 integrada ao app principal (12 endpoints versionados)
2. Deploy em produção — nginx + SSL (Let's Encrypt) + docker-compose
3. Dashboard multi-ECD lado a lado (até 5 ECDs simultâneas)
4. Watchdog para importação automática (polling configurável)
5. Layout customizável de colunas por tipo de relatório
6. Bump v0.6.0 — 107 testes (100% passando)

## Status Técnico

| Componente | Status |
|---|---|
| `pyproject.toml` | ✅ Atualizado (v0.6.0) |
| `src/layouts/ecd_v9.yml` | ✅ 30 registros (Fase 1) |
| `src/parsers/ecd.py` | ✅ Streaming + herança (Fase 1) |
| `src/parsers/efd.py` | ✅ Parser EFD-Contribuições (Fase 4) |
| `src/parsers/ecf.py` | ✅ Parser ECF (Fase 4) |
| `src/db/models.py` | ✅ 18 modelos + ApiKey (Fase 7) |
| `src/db/repository.py` | ✅ CRUD + consultas (Fase 1) |
| `src/auth/__init__.py` | ✅ AuthService + middleware (Fase 5) |
| `src/filters/engine.py` | ✅ 16 tipos de filtro (Fase 1) |
| `src/reports/base.py` | ✅ Convenção de sinais (Fase 1) |
| `src/reports/balancete.py` | ✅ Balancete F8 (Fase 1) |
| `src/reports/razao.py` | ✅ Razão F11 (Fase 1) |
| `src/reports/balanco.py` | ✅ +período anterior, +J100/J150 (Fase 5) |
| `src/reports/dre.py` | ✅ +período anterior comparativo (Fase 5) |
| `src/reports/dfc.py` | ✅ +período anterior comparativo (Fase 6) |
| `src/reports/diario.py` | ✅ Livro Diário F12 (Fase 2) |
| `src/reports/export_engine.py` | ✅ +base_url para fontes (Fase 5) |
| `src/reports/templates/*` | ✅ +fontes Inter, +colunas período anterior (Fase 6) |
| `src/validators/integridade.py` | ✅ 7 validações (Fase 1) |
| `src/cli.py` | ✅ CLI completa (Fase 1-2) |
| `src/dashboard/app.py` | ✅ FastAPI app — **32 rotas (Fase 7)** |
| `src/dashboard/services.py` | ✅ +multi-ECD, +layout customizável (Fase 7) |
| `src/dashboard/templates/*` | ✅ Auth, upload, partials, notas (Fase 6) |
| `src/api/__init__.py` | ✅ API Key auth (Fase 7) |
| `src/api/routes.py` | ✅ **12 endpoints REST v1 (Fase 7)** |
| `src/watchdog.py` | ✅ **Importação automática por polling (Fase 7)** |
| `Dockerfile` | ✅ Multi-stage + watchdog (Fase 7) |
| `docker-compose.yml` | ✅ **Produção com nginx + certbot (Fase 7)** |
| `nginx.conf` | ✅ **Proxy reverso + SSL hardening (Fase 7)** |
| `.github/workflows/ci.yml` | ✅ CI/CD pipeline (Fase 5) |
| `tests/test_fase7.py` | ✅ **21 novos testes (Fase 7)** |
| **Total** | **107 testes — 100% passando** |

## O que foi entregue na Fase 7

### 1. API REST v1 Integrada
- Router `src/api/routes.py` incluído no app principal (`app.include_router`)
- 12 endpoints: health, empresas (list/detail), ECDs (list/detail), balanco, dre, dfc, diario, kpis, notas, validar, evolucao-multi
- Autenticação por X-API-Key com hash SHA-256
- Modelo `ApiKey` no banco com prefixo, expiração e contador de uso
- Health check público (`/api/v1/health`) sem autenticação
- Paginação em listas (empresas, ECDs, diário)

### 2. Deploy Produção (nginx + SSL)
- `nginx.conf` — proxy reverso com HTTP→HTTPS redirect, SSL hardening (TLS 1.2/1.3), security headers (HSTS, X-Frame-Options, etc.), gzip, upload 100M
- `docker-compose.yml` — 3 serviços: web (FastAPI), nginx (proxy), certbot (Let's Encrypt auto-renewal)
- Volume persistente para banco e certificados

### 3. Dashboard Multi-ECD Lado a Lado
- `DashboardService.get_multi_ecd_comparison()` — compara até 5 ECDs
- Matrizes de Balanço (Ativo/Passivo/PL), DRE (Receita/Resultado/Margem) e DFC (Operacional/Investimento/Financiamento/Variação)
- Nova rota: `GET /api/multi-ecd?ecd_ids=1,2,3`

### 4. Watchdog (Importação Automática)
- `src/watchdog.py` — polling configurável (--interval)
- Hash SHA-256 para evitar reprocessamento
- Entry point CLI: `sped-hub-watchdog`
- Modo `--once` para execução única

### 5. Layout Customizável
- `DashboardService.get_layout_customizavel()` — colunas configuráveis por relatório
- Suporte a balanco, dre, dfc e diario
- Cada coluna: key, label, visible, width
- Nova rota: `GET /api/layout?ecd_id=X&relatorio=balanco`

### 6. Testes Ampliados (86 → 107)
- `TestApiKey` — 5 testes (geração, verificação, hash, persistência)
- `TestMultiECD` — 5 testes (comparação 3 ECDs, limite, metadados, crescimento)
- `TestLayoutCustomizavel` — 5 testes (balanco, dre, dfc, diario, colunas)
- `TestWatchdog` — 3 testes (import, hash determinístico, hash diferente)
- `TestApiV1Health` — 1 teste
- `TestEvolucaoMultiPeriodoFase7` — 2 testes (3 períodos, valores crescentes)

## Próximos Passos (Fase 8+)

1. Deploy real em VPS/cloud com domínio e SSL
2. Suporte a ECDs reais de clientes (testes com arquivos >100MB)
3. API REST v2 com GraphQL
4. Agendamento de importação via cron (alternativa ao watchdog)
5. Relatórios customizáveis com drag & drop de colunas no frontend
6. Exportação de lote multi-formato (PDF+XLSX+CSV)
7. Integração com sistemas de terceiros via webhooks

## Pendências

- Deploy real em VPS/cloud (configuração de domínio + SSL)
- Suporte a ECDs reais de clientes (arquivos grandes)
- Frontend para multi-ECD lado a lado (endpoint já existe)
- Frontend para layout customizável (endpoint já existe)
- python-multipart não disponível no sandbox (não afeta testes)