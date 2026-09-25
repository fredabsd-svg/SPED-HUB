<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-escuro.svg">
    <img src="docs/assets/logo.svg" alt="SPED-HUB" width="340">
  </picture>
</p>

<p align="center">
  Plataforma multiempresa de conformidade fiscal e contábil para escritórios de contabilidade.
</p>

<p align="center">
  <a href="#o-que-funciona-hoje">O que funciona hoje</a> ·
  <a href="#instalação">Instalação</a> ·
  <a href="#uso">Uso</a> ·
  <a href="#desenvolvimento">Desenvolvimento</a> ·
  <a href="#documentação">Documentação</a>
</p>

---

O SPED-HUB lê as escriturações que o escritório já produz e devolve o que ele
precisa conferir e entregar:

- **Contábil** — importa a ECD e a transforma em Balancete, Razão, Balanço
  Patrimonial, DRE, DFC, Livro Diário e notas explicativas, com validações de
  integridade e exportação em PDF e Excel.
- **Fiscal** — importa o XML das notas (NF-e e NFC-e), classifica e corrige os
  documentos com trilha de auditoria e gera a EFD ICMS/IPI e a
  EFD-Contribuições, com espelho de conferência antes de gerar.
- **Multiempresa** — cada escritório enxerga só as próprias empresas, pela
  tela, pela linha de comando e pelas APIs.

<p align="center">
  <img src="docs/assets/tela-painel.png" alt="Painel contábil do SPED-HUB com indicadores, evolução patrimonial e composição do ativo" width="860">
</p>

## O que funciona hoje

Cada item abaixo tem teste passando; a evidência de cada fase está em
[`docs/status.md`](docs/status.md). O que ainda **não** existe está em
[`docs/roadmap.md`](docs/roadmap.md).

### Escrituração contábil (ECD)

- Importação do leiaute 9 em fluxo, com memória constante e conferência do
  leiaute linha a linha; a importação interrompida é revertida inteira.
- Balancete, Razão, Balanço Patrimonial (visão hierárquica e de publicação,
  J100/J150), DRE e Livro Diário, com coluna do exercício anterior — tirada da
  própria ECD (saldo de abertura, DRE publicada) quando a do ano anterior não
  foi importada. A DRE classifica cada conta pelo plano referencial e pelo
  grupo (CMV, despesas financeiras, outras receitas).
- DFC pelos métodos direto e indireto, calculada lançamento a lançamento: só
  entra o que passou pelo caixa e pelos bancos; transferência entre contas da
  própria empresa e transação sem caixa ficam fora.
- Índices de habilitação econômico-financeira para licitação (Lei
  14.133/2021, art. 69) e relatório do plano de contas.
- Demonstrações com linha de assinatura do contador (lido da ECD) e do sócio.
- Notas explicativas automáticas e validações de integridade contábil,
  incluindo a recusa de plano de contas com hierarquia cíclica.
- Filtros combináveis por natureza, nível, período, conta, histórico e valor,
  com visões salvas.
- Exportação em PDF (identidade "Tinta & Latão", com cor e logo do escritório
  pela linha de comando), XLSX e TXT; ZIP com PDF, XLSX e CSV; exportação em
  lote.
- Leitura dos resumos da EFD-Contribuições e da ECF.

### Central fiscal

- Importação de XML de NF-e e NFC-e, avulso ou por pasta, com o XML original
  guardado como chegou.
- Classificação por regras revisável antes de aplicar, com exportação das
  propostas em CSV.
- Correção em massa com simulação, desfazer por lote e planilha de revisão.
- Cadastro fiscal da empresa, ajustes de apuração (E111) e apuração de CBS,
  IBS e IS da reforma tributária, com as tabelas oficiais datadas.
- Geração da EFD ICMS/IPI (blocos 0, C, E e 9) e da EFD-Contribuições (blocos
  0, C, M e 9); cada geração é arquivada, marcada quando transmitida e
  conferida contra o que sairia agora.
- Certificado A1 guardado cifrado, com a chave mestra fora do banco.

### Painel web

- FastAPI, Jinja2, htmx, Alpine.js e Chart.js servidos pela própria aplicação,
  sem CDN.
- Login com sessão; o primeiro usuário vira administrador e o registro
  público fecha em seguida.
- Menu lateral com as telas agrupadas por área e acesso rápido com `Ctrl+K`
  ou `Cmd+K`; em tela estreita o menu recolhe atrás de um botão.
- Indicadores, gráficos com tabela equivalente para leitura acessível e um
  cartão de destaques que lê a escrituração: se o balanço fecha, resultado e
  margem, endividamento e variação contra o exercício anterior — calculados
  dos saldos importados, sem estimativa.
- Importação em segundo plano com progresso e cancelamento.
- Auditoria, monitoramento, webhooks e chaves de API para administradores.

### APIs

- REST v1 com autenticação por `X-API-Key` e GraphQL v2, ambas com o escopo do
  escritório dono da chave.

### Operação

- SQLite para começar; PostgreSQL com schema versionado por migração
  (Alembic) e cópia de dados entre bancos.
- Imagem Docker multi-stage, `docker compose` com nginx e TLS, e CI com lint,
  testes em Python 3.11 e 3.12, PostgreSQL 16 real, navegador e build da
  imagem.

## Instalação

Requer Python 3.11 ou superior. O PDF usa o WeasyPrint, que depende do Pango:

```bash
# Debian/Ubuntu
sudo apt-get install libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 libffi-dev libcairo2

pip install -e ".[dev]"            # aplicação + ferramentas de desenvolvimento
pip install -e ".[postgres]"       # driver do PostgreSQL, se for usá-lo
```

Com Docker:

```bash
cp .env.example .env               # ajuste domínio, banco e segredos
docker compose up -d
```

O checklist de produção — domínio, certificado, backup e o que conferir
depois de subir — está em [`docs/deploy.md`](docs/deploy.md).

### Configuração

`src/settings.py` é o ponto único de configuração: CLI, painel, APIs,
webhooks, e-mail e workers leem dele, e nenhum outro módulo lê o ambiente.
Todas as variáveis são opcionais; os *defaults* servem para desenvolvimento.

A lista completa, com o efeito de cada uma, está no
[`.env.example`](.env.example) — o CI falha se uma variável documentada ali
não tiver consumidor no código. As mais usadas:

| Variável | Função | Default |
|---|---|---|
| `DATABASE_URL` | URL SQLAlchemy (`sqlite:///./sped_hub.db`, `postgresql+psycopg://...`) | `sqlite:///./sped_hub.db` |
| `SPED_HUB_ALLOWED_HOSTS` | Domínios aceitos no `Host` (`*.dominio` cobre subdomínios) | `*` |
| `SPED_HUB_REGISTRO_ABERTO` | Mantém o `/register` aberto depois do primeiro usuário | `false` |
| `SPED_HUB_CERTIFICATE_MASTER_KEY` | Chave mestra do cofre de certificados A1 | — |
| `SPED_HUB_MAX_UPLOAD_MB` | Tamanho máximo de upload | `200` |
| `SPED_HUB_UPLOAD_DIR` | Diretório dos uploads temporários | `<raiz>/uploads` |
| `SPED_HUB_TRUST_PROXY` | Lê o IP do cliente de `X-Forwarded-For` (só com proxy confiável na frente) | `false` |
| `SPED_HUB_LOG_LEVEL` / `SPED_HUB_LOG_JSON` | Nível e formato do log | `INFO` / `false` |

> Não versione o `.env` — ele já está no `.gitignore`.

Em PostgreSQL o schema é versionado por migração; em SQLite ele nasce de
`create_all`. A política, inclusive como adotar um banco criado antes das
migrações, está em [`docs/migrations.md`](docs/migrations.md):

```bash
sped-hub migrar status       # onde o banco está e o que falta aplicar
sped-hub migrar aplicar      # leva o banco até a revisão mais recente
```

## Uso

### Painel

```bash
sped-hub-dashboard           # http://127.0.0.1:8000
```

Na primeira vez, crie a conta em `/register`: ela vira a do administrador, e
o registro público fecha. Os acessos seguintes são criados pela linha de
comando:

```bash
sped-hub usuario criar --email ana@escritorio.com.br --nome "Ana Souza"
```

<p align="center">
  <img src="docs/assets/tela-balanco.png" alt="Aba de relatórios com o Balanço Patrimonial hierárquico" width="760">
</p>

### Linha de comando — contábil

```bash
sped-hub importar-ecd arquivo_ecd.txt
sped-hub validar
sped-hub relatorio balancete
sped-hub relatorio dre
sped-hub exportar balanco --formato pdf --saida balanco.pdf
sped-hub exportar dre --formato xlsx --saida dre.xlsx
sped-hub exportar balanco --formato pdf --logo logo.png --cor "#0C3A30" --saida balanco.pdf
sped-hub info
```

### Linha de comando — fiscal

A cadeia segue a ordem do trabalho; os passos que gravam só gravam com
`--aplicar` ou `--confirmar`, e sem eles mostram o que fariam:

```bash
sped-hub fiscal importar ./xml/                                  # XML avulso ou pasta
sped-hub fiscal cadastro --empresa 1 --ind-perfil A --ind-ativ 1
sped-hub fiscal classificar --empresa 1 --de 2026-07-01 --ate 2026-07-31
sped-hub fiscal espelho --empresa 1 --de 2026-07-01 --ate 2026-07-31
sped-hub fiscal gerar --empresa 1 --de 2026-07-01 --ate 2026-07-31 --saida efd.txt
sped-hub fiscal transmitida --escrituracao 1 --recibo 123456
```

Todas as ações e os códigos de saída estão em
[`docs/modules/cli_fiscal.md`](docs/modules/cli_fiscal.md).

### APIs

```bash
curl -H "X-API-Key: $CHAVE" http://127.0.0.1:8000/api/v1/ecds
curl -H "X-API-Key: $CHAVE" -H "Content-Type: application/json" \
     -d '{"query": "{ empresas { dados { nome cnpj } } }"}' http://127.0.0.1:8000/api/v2/graphql
```

As chaves são criadas pelo administrador na tela **Sistema → Chaves de API**.

## Desenvolvimento

Antes de mudar qualquer coisa, leia
[`REGRAS-DO-PROJETO.md`](REGRAS-DO-PROJETO.md): cada regra diz se é cobrada
pelo CI ou pela revisão.

```bash
pytest                       # suíte padrão
pytest -m e2e                # só os testes de navegador (precisam do Chromium)
pytest -m ""                 # tudo

ruff check src/ tests/       # ruff e black têm versão fixada no pyproject.toml
black --check src/ tests/
```

Os testes de portabilidade e de migração rodam também contra PostgreSQL
quando há um servidor disponível; sem a variável, esses casos pulam:

```bash
TEST_DATABASE_URL=postgresql+psycopg://user:senha@host:5432/sped_hub_test \
  pytest tests/test_multibackend.py tests/test_migrations.py tests/test_migracao_de_dados.py
```

O logo e as capturas de tela deste README são gerados por script, nunca
editados à mão:

```bash
python scripts/gerar_logo.py         # logo, ícone e prévia social (SVG)
python scripts/capturar_telas.py     # capturas a partir da ECD de exemplo
```

### Estrutura

```
src/
├── cli.py, cli_fiscal.py    linha de comando (sped-hub)
├── settings.py              configuração — único leitor do ambiente
├── parsers/                 leitura de ECD, EFD-Contribuições e ECF
├── ecd_importer.py          importação da ECD em fluxo, transacional
├── db/                      modelos SQLAlchemy, repositório e migrações
├── filters/                 motor de filtros dos relatórios
├── reports/                 balancete, razão, balanço, DRE, DFC, índices, plano, diário e exportação
├── validators/              validações de integridade contábil
├── documentos/              Central fiscal: importação, classificação e correção de notas
├── escrituracoes/           geração, espelho e arquivo da EFD ICMS/IPI e da EFD-Contribuições
├── certificados.py          cofre de certificados A1
├── dashboard/               painel web (FastAPI, templates, estáticos)
├── api/                     REST v1 e GraphQL v2
└── auth/, audit/, ratelimit/, webhooks/, async_jobs/, cache/, monitoring.py, ...
alembic/                     migrações do PostgreSQL
tests/                       suíte pytest (unidade, porta de entrada e navegador)
docs/                        estado, roadmap, ADRs e um documento por módulo
scripts/                     geradores de artefatos versionados
```

## Documentação

Ordem de leitura para quem chega agora:

1. [`REGRAS-DO-PROJETO.md`](REGRAS-DO-PROJETO.md) — como se trabalha neste
   repositório.
2. [`docs/status.md`](docs/status.md) — estado real por fase, com o teste que
   prova cada uma.
3. [`docs/roadmap.md`](docs/roadmap.md) — o que ainda **não** existe.

| Onde | O quê |
|---|---|
| [`CHANGELOG.md`](CHANGELOG.md) | Histórico por versão, pelo efeito para quem usa. |
| [`docs/modules/`](docs/modules/) | Um documento por módulo: o que faz, o que expõe, armadilhas e limites. |
| [`docs/decisions/`](docs/decisions/) | ADRs — por que cada decisão estrutural foi tomada. |
| [`docs/architecture/`](docs/architecture/) | Arquitetura real por área. |
| [`docs/deploy.md`](docs/deploy.md) | Checklist manual de produção. |
| [`docs/migrations.md`](docs/migrations.md) | Política de schema e adoção de banco pré-existente. |
| [`docs/reforma-tributaria.md`](docs/reforma-tributaria.md) | O que o sistema faz com CBS, IBS e IS. |

## Licença

MIT, conforme declarado no `pyproject.toml`.
