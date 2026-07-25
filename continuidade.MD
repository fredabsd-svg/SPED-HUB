# SPED-HUB — Continuidade do Projeto

**Data:** 2025-07-25

## Objetivo da Alteração Atual

**Fase 6 — Maturidade analítica e exportação:**
1. Período anterior na DFC (comparativo entre exercícios)
2. Evolução patrimonial multi-período (gráfico temporal com múltiplas ECDs)
3. Notas explicativas automáticas (J800/J801)
4. Exportação de lote (múltiplas ECDs em ZIP)
5. Deploy em produção — hardening do Dockerfile
6. Testes de integração ampliados (86 testes)

## Status Técnico

| Componente | Status |
|---|---|
| `pyproject.toml` | ✅ Atualizado (v0.5.0) |
| `src/layouts/ecd_v9.yml` | ✅ 30 registros (Fase 1) |
| `src/parsers/ecd.py` | ✅ Streaming + herança (Fase 1) |
| `src/parsers/efd.py` | ✅ Parser EFD-Contribuições (Fase 4) |
| `src/parsers/ecf.py` | ✅ Parser ECF (Fase 4) |
| `src/db/models.py` | ✅ 17 modelos + utcnow corrigido (Fase 5) |
| `src/db/repository.py` | ✅ CRUD + consultas (Fase 1) |
| `src/auth/__init__.py` | ✅ AuthService + middleware + utcnow corrigido (Fase 5) |
| `src/filters/engine.py` | ✅ 16 tipos de filtro (Fase 1) |
| `src/reports/base.py` | ✅ Convenção de sinais (Fase 1) |
| `src/reports/balancete.py` | ✅ Balancete F8 (Fase 1) |
| `src/reports/razao.py` | ✅ Razão F11 (Fase 1) |
| `src/reports/balanco.py` | ✅ +período anterior, +J100/J150 hierárquico (Fase 5) |
| `src/reports/dre.py` | ✅ +período anterior comparativo (Fase 5) |
| `src/reports/dfc.py` | ✅ **+período anterior comparativo (Fase 6)** |
| `src/reports/diario.py` | ✅ Livro Diário F12 (Fase 2) |
| `src/reports/export_engine.py` | ✅ +base_url para fontes (Fase 5) |
| `src/reports/templates/*` | ✅ +fontes Inter, +colunas período anterior DFC (Fase 6) |
| `src/validators/integridade.py` | ✅ 7 validações (Fase 1) |
| `src/cli.py` | ✅ CLI completa (Fase 1-2) |
| `src/dashboard/app.py` | ✅ FastAPI app — **28 rotas (Fase 6)** |
| `src/dashboard/services.py` | ✅ **+evolução multi-período, +notas explicativas (Fase 6)** |
| `src/dashboard/templates/*` | ✅ Auth, upload, partials, **+notas.html (Fase 6)** |
| `Dockerfile` | ✅ Multi-stage build (Fase 4) |
| `docker-compose.yml` | ✅ Produção + dev (Fase 4) |
| `.github/workflows/ci.yml` | ✅ CI/CD pipeline (Fase 5) |
| `tests/test_integracao.py` | ✅ **14 testes de integração (Fase 6)** |
| **Total** | **86 testes — 100% passando** |

## O que foi entregue na Fase 6

### 1. Período Anterior na DFC
- `DFC._get_ecd_anterior()` — busca ECD do exercício anterior
- `DFC._get_saldos_anteriores()` — carrega saldos do período anterior
- `LinhaDFC.valor_anterior` — novo campo no dataclass
- Coluna "Período Anterior" nos templates HTML (relatório e dashboard)
- Totais anteriores: `variacao_caixa_anterior`, `operacional_anterior`, `investimento_anterior`, `financiamento_anterior`
- Flag `tem_anterior` para controle condicional

### 2. Evolução Patrimonial Multi-Período
- `DashboardService.get_evolucao_multi_periodo()` — busca todas as ECDs da mesma empresa
- Calcula Ativo, Passivo, PL e Resultado Líquido de cada período
- Retorna `None` quando há menos de 2 ECDs (sem dados para comparar)
- Nova rota: `GET /api/evolucao-multi?ecd_id=X`
- Integrado ao endpoint `/api/graficos` como `evolucao_multi`

### 3. Notas Explicativas Automáticas (J800/J801)
- `DashboardService.get_notas_explicativas()` — gera notas a partir de eventos contábeis
- Nota 1: Contexto Operacional (dados da empresa)
- Nota 2: Principais Práticas Contábeis
- Nota 3: Capital Social (com detecção de aumento/redução vs período anterior)
- Nota 4: Imobilizado (se relevante, com composição)
- Nota 5: Eventos Subsequentes
- Nova rota: `GET /api/notas?ecd_id=X` (HTMX partial)
- Template: `partials/notas.html`

### 4. Exportação de Lote
- `GET /api/export/lote?ecd_ids=1,2,3&tipo=balanco` — exporta múltiplas ECDs em ZIP
- Limite de 10 ECDs por lote
- Gera PDF individual para cada ECD e empacota em ZIP
- Suporte a balanco, dre e dfc

### 5. Testes Ampliados
- `TestFase6` — 5 novos testes:
  - `test_dfc_valor_anterior` — verifica campo e flag tem_anterior
  - `test_dfc_totais_subtotais` — verifica totais operacional/investimento/financiamento
  - `test_evolucao_multi_periodo_sem_anterior` — retorna None com 1 ECD
  - `test_notas_explicativas` — estrutura e quantidade mínima
  - `test_notas_explicativas_tipos` — verifica tipos contexto/praticas/eventos

## Próximos Passos (Fase 7)

1. Deploy em produção (VPS/cloud) — configuração real com nginx + SSL
2. Suporte a ECDs reais de clientes (testes com arquivos grandes)
3. Dashboard com seleção de múltiplas ECDs lado a lado
4. API REST externa para integração com sistemas de terceiros
5. Agendamento de importação automática (watchdog/polling)
6. Relatórios customizáveis (arrastar colunas, salvar layout)

## Pendências

- Deploy em produção (VPS/cloud)
- Suporte a ECDs reais de clientes
- API REST externa
- Agendamento de importação automática