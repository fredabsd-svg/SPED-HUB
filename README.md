# SPED-HUB
Plataforma multiempresa de conformidade fiscal para escritórios contábeis. Importa ECDs e XMLs fiscais, gera e valida SPED Contribuições, ECF e ECD, e transforma a escrituração em Balancete, Balanço, DRE, Razão, DFC e indicadores — com filtros avançados, conciliação automática e exportação em PDF e Excel com a marca do escritório.

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
- Dashboard com KPIs (Ativo Total, PL, Endividamento, Resultado, Margem, Lançamentos)
- Gráficos interativos: Evolução Patrimonial, Composição do Ativo, DRE Waterfall
- Upload de ECD via interface web com drag & drop
- Visualização de Balanço Patrimonial, DRE e Livro Diário com tabs
- Navegação entre múltiplas ECDs importadas
- Design responsivo com tema profissional

## Instalação

```bash
pip install -e .
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
```

## Estrutura do Projeto

```
src/
├── cli.py              # CLI principal
├── parsers/            # Parsers de arquivos SPED
│   └── ecd.py          # Parser ECD (leiaute 9)
├── db/                 # Modelos e repositório
│   ├── models.py       # 14 modelos SQLAlchemy
│   └── repository.py   # CRUD + consultas
├── filters/            # Motor de filtros
│   └── engine.py       # 16 tipos de filtro
├── reports/            # Relatórios contábeis
│   ├── base.py         # Convenções e formatação
│   ├── balancete.py    # Balancete
│   ├── razao.py        # Razão
│   ├── balanco.py      # Balanço Patrimonial
│   ├── dre.py          # DRE
│   ├── diario.py       # Livro Diário
│   ├── export_engine.py # Export PDF/XLSX
│   └── templates/      # Templates HTML
├── validators/         # Validações
│   └── integridade.py  # 7 validações
├── dashboard/          # Dashboard Web (Fase 3)
│   ├── app.py          # FastAPI app
│   ├── services.py     # Serviços de dados
│   └── templates/      # Templates Jinja2
│       ├── base.html
│       ├── dashboard.html
│       ├── upload.html
│       └── partials/
│           ├── kpis.html
│           ├── balanco.html
│           ├── dre.html
│           └── diario.html
└── layouts/            # Layouts de registros
    └── ecd_v9.yml      # 30 registros ECD
```