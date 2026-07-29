# SPED-HUB — Continuidade do Projeto

**Última atualização:** 2026-07-29

## Etapa 1.1 — Saneamento da fundação (2026-07-29)

> Uma análise do projeto mostrou que a Etapa 1 tinha sido entregue apenas
> pela metade e que o CI nunca havia passado.  Esta etapa fecha as duas
> lacunas antes de seguir para a Etapa 2 (PostgreSQL), que dependeria de
> uma fundação que não estava ligada.

### O que estava errado

- **CI vermelho em 14 de 14 execuções**, desde a criação do workflow.
  Falhava sempre no `ruff check`, e como ele vem antes, o `pytest` nunca
  chegou a rodar no CI.  As afirmações de "372 testes 100% passando" no
  README e neste arquivo nunca foram verificadas automaticamente.
  Causa raiz: `ruff>=0.4` e `black>=24.0` sem pin — o CI instalava a
  versão do dia e passou a falhar sozinho no salto para ruff 0.16.
- **`src/settings.py` era código morto.**  Só `src/db/models.py` o
  importava; o resto da aplicação seguia lendo `os.environ` (33 pontos).
  Das 20 variáveis documentadas, apenas `REDIS_URL` e `SMTP_HOST` eram de
  fato consumidas.  O exemplo de PostgreSQL do README subia em SQLite.
- **Nomes divergentes entre doc e código**: `SMTP_PASSWORD` vs `SMTP_PASS`,
  `EMAIL_FROM` vs `SMTP_FROM`, `SPED_HUB_MAX_UPLOAD_MB` vs
  `SPED_HUB_MAX_UPLOAD_BYTES` — configuração documentada era ignorada em
  silêncio.
- **Deploy Docker quebrado** em três pontos (uploads em diretório não
  montado, healthcheck em rota autenticada, constraints do pip sem aspas).
- **`src/cli.py` com 0% de cobertura** — 386 statements na funcionalidade
  que abre o README.

### Bugs encontrados ao ligar a configuração

1. Coerção booleana só existia para `debug`: `SPED_HUB_DB_ECHO=false`
   virava a string `"false"`, verdadeira em Python — desligar o echo do
   SQLAlchemy o **ligava**.  Valia também para `SMTP_USE_TLS` e
   `EMAIL_ENABLED`.
2. Caminho SQLite absoluto era corrompido: `lstrip("./")` comia a barra
   inicial e `/tmp/x.db` virava o relativo `sqlite:///./tmp/x.db`.
3. `verificar_senha` comparava hashes com `==`, vazando por tempo quantos
   bytes foram acertados (`verificar_api_key` já fazia certo).
4. `exportar balancete --formato pdf` gera XLSX, em caminho diferente do
   `--saida`, avisando só em log INFO.  **Ainda em aberto** — ver abaixo.

### Entregue

| Commit | Tema |
|---|---|
| `d61c65d` | CI verde: pin de ruff/black, `[tool.ruff.lint]` explícito, 497 achados → 0, `httpx` nas deps, 2 testes de Postgres reescritos, E2E do Playwright pulando limpo |
| `2485a4f` | settings ligado a toda a aplicação; 33 leituras de `os.environ` eliminadas; aliases legados; 3 bugs acima |
| `7f924ce` | Docker: uploads no volume compartilhado, healthcheck público, constraints com aspas; `tests/test_deploy_config.py` |
| `faf1c3e` | `obter_engine` + `init_db_once`: 4,3 ms → 0,87 ms por request autenticado |
| `c59a8ed` | CLI de 0% → 95% de cobertura (33 testes) |

**Estado:** 465 testes passando, 11 skips.  `ruff`, `black` e `pytest`
verificados localmente com os comandos exatos do workflow.

### Em aberto (decisão de produto)

- `exportar balancete --formato pdf` precisa de um template PDF **ou** de
  recusa explícita da combinação.  Hoje entrega um XLSX em caminho
  diferente do pedido e reporta sucesso.
- 3 testes E2E do Playwright falham por timeout de navegação quando há
  Chromium disponível.  Eles nunca chegaram a rodar antes (erravam na
  fixture, por causa de um caminho absoluto de outra máquina), então são
  falhas recém-expostas, não regressões.  Em CI seguem pulando.
- `SPED_HUB_SECRET_KEY` continua sem consumidor.  Documentada como
  reservada; quando surgir necessidade real de assinatura global, ela vira
  a chave — até lá, ninguém deve contar com ela.

## Fase 17 — Fundação de Produção (em andamento)

> Trilha aprovada: **fundação de produção primeiro**.  PostgreSQL, deploy,
> integrações com terceiros e CI/CD de publicação são itens posteriores;
> esta fase prepara o terreno sem assumir credenciais ou domínios externos.

### Etapa 1 — Configuração por ambiente ✅ (entregue — commit `1ad08ef`)

- `src/settings.py` com `Settings` (dataclass `frozen`), `get_settings()`
  cacheado por snapshot do ambiente, `with_overrides()` e
  `reset_settings_cache()` (usado em testes).
- Variáveis lidas: `DATABASE_URL`, `SPED_HUB_DB` (alias legado),
  `SPED_HUB_SECRET_KEY`, `SPED_HUB_DEBUG`, `SPED_HUB_LOG_LEVEL`,
  `SPED_HUB_ALLOWED_HOSTS`, `SPED_HUB_MAX_UPLOAD_MB`,
  `SPED_HUB_ECD_CHUNK_ROWS`, `SPED_HUB_ECD_CHUNK_BYTES`,
  `SPED_HUB_MONITORING_RETENTION_HOURS`, `SPED_HUB_METRICS_WINDOW_MINUTES`,
  `SMTP_HOST/PORT/USER/PASSWORD/USE_TLS`, `EMAIL_FROM/ENABLED`,
  `REDIS_URL`, `SPED_HUB_WEBHOOK_*`, `SPED_HUB_RATE_LIMIT_*`.
- `src/db/models.py:criar_engine` aceita caminho SQLite legado **ou** URL
  completa (`sqlite:///...`, `postgresql+psycopg://...`).  Os PRAGMAs
  `journal_mode=WAL` e `foreign_keys=ON` são ativados só em SQLite de
  arquivo (`:memory:` continua funcionando).
- `.env.example` lista todas as variáveis com defaults e comentários;
  `.gitignore` agora bloqueia `.env` (mas mantém `.env.example`).
- `tests/test_settings.py` — 27 testes (defaults, override por env,
  precedência `DATABASE_URL` > `SPED_HUB_DB`, PRAGMAs, inicialização
  sem engine explícita, imutabilidade).
- Ajustes compatíveis: testes das Fases 13/16 com `0.14.0` hardcoded
  passaram a usar `APP_VERSION` (não quebra nas próximas fases).
- **Total de testes:** 372 (100% passando, 30 s por bloco).
- `README.md` — nova seção "Configuração" e tabela de variáveis.
- Versão bumped para `0.15.0` (`src/version.py` e `pyproject.toml`).

#### Não-objetivos desta etapa (vão para próximas)

- Não troquei nenhum chamador de `criar_engine(db_path=...)` por
  `criar_engine(url=...)` — a refatoração agregada fica para a etapa de
  migração para PostgreSQL.
- Não introduzi migrações com Alembic (SQLAlchemy `create_all` segue
  sendo o entrypoint; `Base.metadata.create_all` continua idempotente).
- Não troquei o SMTP por email com templates HTML ricos (somente os
  defaults foram expostos via settings).

### Etapas 2-6 — sequência acordada (planejadas)

> Cada etapa terá um commit próprio, validação por bloco da suíte
> (parsing / auth / relatórios / integrações / e2e) e atualização deste
> `continuidade.MD`.  Nada é publicado (`git push`) sem autorização do
> usuário; branches/PRs também dependem dessa autorização.

| Etapa | Tema principal | Entregas previstas |
|---|---|---|
| 2 | Migração para PostgreSQL | `criar_engine` parametrizado por todos os serviços; testes multi-backend (Postgres opcional via `TEST_DATABASE_URL`); ajustes de tipos/text que não portam para Postgres (`Text`/`String(255)` revisados, datas como `Date`); `Repository` idempotente em ambos os backends. |
| 3 | Migrações Alembic + `Base.metadata.create_all` como fallback | `alembic/` no repositório, autogeração revisada manualmente, `init_db` chama `alembic upgrade head` quando a URL for Postgres; policy de versionamento de schema em `docs/migrations.md`. |
| 4 | Upload de ECDs >100MB | Parser em chunks com `ecd_import_chunk_rows`/`ecd_import_chunk_bytes` (já nas settings), `importar` retorna iterator/gerador, cancelamento por token, retomada a partir de offset; cobertura com ECD sintético. |
| 5 | Hardening de produção | Validação de upload (MIME, magic number ECD), rate limit por IP além do de API Key, headers de segurança (CSP/HSTS) no `nginx.conf`, mensagens de erro sem vazar PII, logs estruturados (JSON opcional). |
| 6 | CI/CD de deploy preparado | Workflow `release.yml` com build multi-arch + push GHCR; **NÃO** aciona deploy automático; `docs/deploy.md` com checklist manual de produção (DNS, SSL, Postgres, backup, observabilidade). |

> Itens **externos** (deploy real em VPS/cloud, domínio, SSL, integrações
> nativas com Domínio/Questor/Alterdata, ECDs reais de clientes) ficam
> fora desta sequência: dependem de credenciais, contrato e plano
> operacional que só o usuário pode autorizar.  Serão retomados quando a
> documentação e as credenciais correspondentes chegarem.

## Objetivo da Alteração Anterior (Fase 16 — referência)

**Fase 16 — Observabilidade, Monitoramento Operacional e Hardening:**
1. `src/monitoring.py` (`MetricsCollector` thread-safe + snapshot
   operacional com janela móvel, banco, cache, e-mail, workers, processo
   e top rotas; `build_operational_snapshot`, `normalize_path`).
2. Endpoints `/monitoring`, `/api/monitoring/summary`,
   `/api/monitoring/reset` e `/api/health/full` (já ampliado na Fase 15).
3. Auto-promoção do primeiro usuário como `admin`, migração para bases
   legadas e gating de páginas/rotas sensíveis.
4. Isolamento por `escritorio_id` em queries,
   uploads e jobs assíncronos.
5. `tests/test_fase16.py` (13 testes) e
   `tests/test_review_regressions.py` (16 testes) — 29 novos.
6. Versão `0.14.0` em `src/version.py`.

## Status Técnico Atualizado

| Componente | Status |
|---|---|
| `pyproject.toml` | ✅ v0.15.1 (Fase 17 etapa 1.1) |
| `src/version.py` | ✅ `APP_VERSION = "0.15.1"` (fonte única; settings deriva daqui) |
| `src/settings.py` | ✅ Configuração centralizada por env (Fase 17 et1) |
| `src/db/models.py` | ✅ 24 modelos + `criar_engine` configurável (Fase 17 et1) |
| `.env.example` | ✅ Lista de variáveis com defaults comentados (Fase 17 et1) |
| `.gitignore` | ✅ `.env` ignorado, `.env.example` versionado (Fase 17 et1) |
| `README.md` | ✅ Seção "Configuração" + tabela de env vars (Fase 17 et1) |
| `tests/test_settings.py` | ✅ 27 testes novos (Fase 17 et1) |
| Demais componentes (Fases 1-16) | ✅ Estáveis |
| **Total** | **465 testes passando, 11 skips — CI verde** |

## Próximos Passos (visão de longo prazo)

1. ✅ Fundação de produção (em andamento — Fase 17)
2. ⏳ Deploy real em VPS/cloud com domínio e SSL (externo, mediante autorização)
3. ⏳ Integração nativa com sistemas de terceiros (Domínio, Questor, Alterdata — depende de contrato)
4. ⏳ Suporte a ECDs reais de clientes (testes com arquivos >100MB — Etapa 4 da Fase 17)
5. ⏳ Migração completa para PostgreSQL em produção (Etapas 2-3 da Fase 17)
6. ⏳ CI/CD com deploy automático (Etapa 6 da Fase 17 — apenas preparação)

## Pendências

- Postgres não roda no sandbox de testes (sem psycopg/psycopg2 instalado).
  Cobertura multi-backend fica para a Etapa 2.  O teste que constrói a
  `Engine` Postgres usa `importorskip` — nota: o SQLAlchemy 2.x importa o
  DBAPI já dentro de `create_engine`, então não há como exercitar a
  construção sem o driver.  A validação da URL, essa sim, roda sempre.
- Playwright E2E requer Chromium (procurado em `SPED_HUB_CHROMIUM`,
  `PLAYWRIGHT_BROWSERS_PATH` e nos caminhos do sistema).  Sem ele, os
  testes pulam — antes erravam na coleta.
- Rate limiting em memória não persiste entre restarts (aceitável para v1).
- Redis server não disponível no CI — fallback para memória funciona.
