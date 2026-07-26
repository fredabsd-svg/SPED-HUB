# SPED-HUB
Plataforma multiempresa de conformidade fiscal para escritórios contábeis. Importa ECDs, EFD-Contribuições e ECFs, gera e valida SPED, e transforma a escrituração em Balancete, Balanço, DRE, Razão, DFC e indicadores — com filtros avançados, conciliação automática e exportação em PDF e Excel com a marca do escritório.

## Funcionalidades

### CLI (`sped-hub`)
- **importar-ecd** — Importa arquivo ECD (leiaute 9) para banco SQLite
- **relatorio** — Balancete, Razão, Balanço Patrimonial, DRE, Livro Diário
- **exportar** — Exporta relatórios para PDF (WeasyPrint) e XLSX (openpyxl)
- **validar** — 7 validações de integridade contábil
- **filtros** — 16 tipos de filtro combináveis com visões salvas
- **info** — Estatísticas do banco

### Dashboard Web (`sped-hub-dashboard`)
- **FastAPI + Jinja2 + HTMX + Alpine.js + Chart.js**
- **Autenticação** — Login/registro com PBKDF2, sessões por token, middleware de proteção
- **Dashboard com KPIs** — Ativo Total, PL, Endividamento, Resultado, Margem, Lançamentos
- **Gráficos interativos** — Evolução Patrimonial, Composição do Ativo, DRE Waterfall, DFC, Comparativo entre Empresas, Evolução Multi-Período
- **Upload multi-formato** — ECD, EFD-Contribuições (PIS/COFINS) e ECF (IRPJ/CSLL) com drag & drop
- **Filtros interativos** — Por natureza, nível, período, conta, nome e saldo zero
- **Exportação direta** — PDF e XLSX para Balanço, DRE e DFC com um clique
- **Exportação de lote** — Múltiplas ECDs em ZIP (até 10 por lote)
- **Exportação multi-formato** — ZIP com PDF + XLSX + CSV para balanço, DRE e DFC em um clique
- **5 abas de relatórios** — Balanço Patrimonial, DRE, DFC, Livro Diário e Notas Explicativas
- **Comparativo entre exercícios** — Coluna de período anterior no Balanço, DRE e DFC
- **Visão de publicação** — Balanço hierárquico J100/J150 conforme Lei 6.404/76
- **Notas explicativas automáticas** — Contexto operacional, práticas contábeis, capital social, imobilizado, eventos subsequentes
- **Evolução multi-período** — Gráfico temporal com Ativo, Passivo, PL e Resultado através de múltiplos exercícios
- **Fontes profissionais** — Inter (Google Fonts) nos PDFs exportados
- **API REST v1** — Endpoints versionados com autenticação por API Key (X-API-Key)
- **GraphQL API v2** — Schema completo com strawberry-graphql em  (14 queries: empresas, ECDs, balanço, DRE, DFC, diário, KPIs, notas, validações)
- **Multi-ECD lado a lado** — Comparação de até 5 ECDs simultaneamente
- **Watchdog** — Importação automática de ECDs por polling
- **Importação assíncrona** — Upload em background com tracking de progresso e polling
- **Cache inteligente** — Cache em memória com TTL, invalidação e estatísticas
- **Layout customizável** — Configuração de colunas visíveis por tipo de relatório
- **Deploy produção** — nginx + SSL (Let's Encrypt) + docker-compose pronto
- **Navegação entre múltiplas ECDs** importadas
- **Design responsivo** com tema profissional

### Docker
- **Dockerfile** multi-stage (builder + runtime) otimizado
- **docker-compose.yml** para produção e desenvolvimento
- Volume persistente para banco de dados

### CI/CD
- **GitHub Actions** — lint (ruff), format (black), test (pytest) em Python 3.11/3.12
- Build da imagem Docker na branch main

## Instalação

```bash
pip install -e ".[dev]"
```

### Docker
```bash
docker compose up -d
# Acesse http://localhost:8000
```

## Uso

### CLI
```bash
# Importar ECD
sped-hub importar-ecd arquivo_ecd.txt

# Gerar relatórios
sped-hub relatorio balanco
sped-hub relatorio dre
sped-hub relatorio diario

# Exportar
sped-hub exportar balanco --formato pdf --saida balanco.pdf
sped-hub exportar dre --formato xlsx --saida dre.xlsx

# Validar
sped-hub validar

# Info
sped-hub info
```

### Dashboard Web
```bash
sped-hub-dashboard
# Acesse http://localhost:8000
# Registre-se em /register e faça login

# Watchdog (importação automática)
sped-hub-watchdog --dir ./uploads --db sped_hub.db --interval 30
```

## Testes

```bash
pytest tests/ -v
# 287 testes — 100% passando
```

## Estrutura do Projeto

```
src/
├── cli.py              # CLI principal
├── auth/               # Autenticação (Fase 4)
│   └── __init__.py     # AuthService, middleware, sessões
├── parsers/            # Parsers de arquivos SPED
│   ├── ecd.py          # Parser ECD (leiaute 9)
│   ├── efd.py          # Parser EFD-Contribuições (Fase 4)
│   └── ecf.py          # Parser ECF (Fase 4)
├── db/                 # Modelos e repositório
│   ├── models.py       # 17 modelos SQLAlchemy (inclui auth)
│   └── repository.py   # CRUD + consultas
├── filters/            # Motor de filtros
│   └── engine.py       # 16 tipos de filtro
├── reports/            # Relatórios contábeis
│   ├── base.py         # Convenções e formatação
│   ├── balancete.py    # Balancete
│   ├── razao.py        # Razão
│   ├── balanco.py      # Balanço Patrimonial (+período anterior, +J100/J150)
│   ├── dre.py          # DRE (+período anterior)
│   ├── dfc.py          # DFC (+período anterior — Fase 6)
│   ├── diario.py       # Livro Diário
│   ├── export_engine.py # Export PDF/XLSX (+fontes Inter)
│   └── templates/      # Templates HTML para PDF
│       └── fonts/      # Inter Variable (Fase 5)
├── validators/         # Validações
│   └── integridade.py  # 7 validações
├── dashboard/          # Dashboard Web
│   ├── app.py          # FastAPI app — 28 rotas (Fase 6)
│   ├── services.py     # Serviços de dados + KPIs + gráficos + notas (Fase 6)
│   └── templates/      # Templates Jinja2
│       ├── base.html
│       ├── dashboard.html
│       ├── upload.html
│       ├── login.html
│       ├── register.html
│       └── partials/
│           ├── kpis.html
│           ├── balanco.html
│           ├── dre.html
│           ├── dfc.html
│           ├── diario.html
│           └── notas.html       # (Fase 6)
├── async_jobs/         # Jobs assíncronos (Fase 14)
│   └── __init__.py      # AsyncJobService + polling
├── cache/               # Cache layer (Fase 14)
│   └── __init__.py      # CacheService + @cached
├── watchdog.py         # Importação automática (Fase 7)
├── api/                 # APIs (Fase 7-9)
│   ├── __init__.py      # API Key auth
│   ├── routes.py        # 12 endpoints REST v1
│   └── graphql.py       # GraphQL API v2 — 14 queries (Fase 9)
└── layouts/            # Layouts de registros
    └── ecd_v9.yml      # 30 registros ECD

tests/
├── test_fase2.py       # Balanço, DRE, Diário, Export (17 testes)
├── test_fase7.py       # API v1, Multi-ECD, Watchdog, Layout (21 testes)
├── test_fase9.py       # GraphQL, Multi-formato, Parser streaming (27 testes)
├── test_filters.py     # Filtros (13 testes)
├── test_parsers.py     # Parsers (16 testes)
├── test_reports.py     # Balancete, Razão, Formatação (17 testes)
├── test_validators.py  # Validações (9 testes)
└── test_integracao.py  # Integração + Fase 6 (14 testes)
```