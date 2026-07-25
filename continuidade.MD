# SPED-HUB — Continuidade do Projeto

**Data:** 2025-07-25

## Objetivo da Alteração Atual

**Fase 5 — Robustez e maturidade:**
1. Período anterior no Balanço/DRE (comparativo real entre exercícios)
2. J100/J150 completo — visão de publicação hierárquica (Lei 6.404/76)
3. Fontes Inter no WeasyPrint — PDFs com identidade profissional
4. Testes de integração com ECD sintético grande (424 contas, 200 lançamentos)
5. CI/CD com GitHub Actions (lint + test + docker build)
6. Correções: `utcnow()` → `now(datetime.UTC)`, `python-multipart` no Dockerfile

## Status Técnico

| Componente | Status |
|---|---|
| `pyproject.toml` | ✅ Atualizado (v0.4.0) |
| `src/layouts/ecd_v9.yml` | ✅ 30 registros (Fase 1) |
| `src/parsers/ecd.py` | ✅ Streaming + herança (Fase 1) |
| `src/parsers/efd.py` | ✅ Parser EFD-Contribuições (Fase 4) |
| `src/parsers/ecf.py` | ✅ Parser ECF (Fase 4) |
| `src/db/models.py` | ✅ 17 modelos + **utcnow corrigido (Fase 5)** |
| `src/db/repository.py` | ✅ CRUD + consultas (Fase 1) |
| `src/auth/__init__.py` | ✅ AuthService + middleware + **utcnow corrigido (Fase 5)** |
| `src/filters/engine.py` | ✅ 16 tipos de filtro (Fase 1) |
| `src/reports/base.py` | ✅ Convenção de sinais (Fase 1) |
| `src/reports/balancete.py` | ✅ Balancete F8 (Fase 1) |
| `src/reports/razao.py` | ✅ Razão F11 (Fase 1) |
| `src/reports/balanco.py` | ✅ **+período anterior, +J100/J150 hierárquico (Fase 5)** |
| `src/reports/dre.py` | ✅ **+período anterior comparativo (Fase 5)** |
| `src/reports/dfc.py` | ✅ DFC — Fluxos de Caixa (Fase 4) |
| `src/reports/diario.py` | ✅ Livro Diário F12 (Fase 2) |
| `src/reports/export_engine.py` | ✅ **+base_url para fontes (Fase 5)** |
| `src/reports/templates/*` | ✅ **+fontes Inter, +colunas período anterior (Fase 5)** |
| `src/validators/integridade.py` | ✅ 7 validações (Fase 1) |
| `src/cli.py` | ✅ CLI completa (Fase 1-2) |
| `src/dashboard/app.py` | ✅ FastAPI app — 24 rotas (Fase 4) |
| `src/dashboard/services.py` | ✅ +DFC, +comparativo (Fase 4) |
| `src/dashboard/templates/*` | ✅ Auth, upload, partials (Fase 4) |
| `Dockerfile` | ✅ Multi-stage build (Fase 4) |
| `docker-compose.yml` | ✅ Produção + dev (Fase 4) |
| `.github/workflows/ci.yml` | ✅ **CI/CD pipeline (Fase 5)** |
| `tests/test_integracao.py` | ✅ **9 testes de integração (Fase 5)** |
| **Total** | **81 testes — 100% passando** |

## O que foi entregue na Fase 5

### 1. Período Anterior no Balanço/DRE
- `BalancoPatrimonial._get_ecd_anterior()` — busca ECD do exercício anterior da mesma empresa
- `BalancoPatrimonial._get_saldos_anteriores()` — carrega saldos do período anterior
- Coluna "Saldo Anterior" em todas as tabelas do Balanço (Ativo, Passivo, PL)
- `DRE._get_ecd_anterior()` e `DRE._get_saldos_resultado_anteriores()` — comparativo na DRE
- Coluna "Período Anterior" em todos os degraus da DRE
- Totais anteriores (`ativo_anterior`, `passivo_anterior`, `pl_anterior`)
- Flag `tem_anterior` para controle condicional nos templates

### 2. J100/J150 — Visão de Publicação Hierárquica
- Estrutura completa conforme Lei 6.404/76:
  - Ativo Circulante (J100) / Ativo Não Circulante (J150)
  - Subgrupos: Realizável a LP, Investimentos, Imobilizado, Intangível
  - Passivo Circulante (J200) / Passivo Não Circulante (J250)
  - PL: Capital Social (J300), Reservas (J310), Lucros/Prejuízos (J320)
- Processamento recursivo de seções e subseções
- Fallback para visão hierárquica quando não há aglutinações

### 3. Fontes Inter no WeasyPrint
- Download da fonte Inter Variable (regular + italic) em `src/reports/templates/fonts/`
- `@font-face` no `print.css` com `font-weight: 100 900`
- `base_url` no `HTML(string=html, base_url=...)` para resolução de caminhos
- PDFs agora renderizam com Inter em vez de fallback do sistema

### 4. Testes de Integração
- Fixture `db_grande`: 424 contas (4 naturezas × 106), 200 lançamentos, 400 partidas
- Testes de performance: Balanço, DRE, DFC, Diário e Validação < 3s cada
- Testes de contagem: verifica integridade dos dados gerados
- Teste de balanço: ativo vs passivo+PL com dados sintéticos

### 5. CI/CD com GitHub Actions
- Pipeline: push/PR na main
- Matrix: Python 3.11 e 3.12
- Etapas: lint (ruff), format (black), test (pytest)
- Job docker: build da imagem (só na main)
- Dependências de sistema: pango, cairo, gdk-pixbuf

### 6. Correções
- `datetime.datetime.utcnow()` → `datetime.datetime.now(datetime.UTC)` em 6 pontos
- Eliminados warnings de depreciação do SQLAlchemy
- `python-multipart` instalado no ambiente de dev

## Próximos Passos (Fase 6)

1. Deploy em produção (VPS/cloud) — configuração real
2. Período anterior na DFC (comparativo)
3. Gráfico de evolução patrimonial multi-período
4. Suporte a ECDs reais de clientes (testes com arquivos grandes)
5. Exportação de lote (múltiplas ECDs em PDF/XLSX)
6. Notas explicativas automáticas (J800/J801)

## Pendências

- Período anterior na DFC (comparativo)
- Gráfico de evolução patrimonial com múltiplos períodos
- Deploy em produção (VPS/cloud)
- Suporte a ECDs reais de clientes
- Notas explicativas automáticas