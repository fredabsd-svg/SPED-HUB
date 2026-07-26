# SPED-HUB — Continuidade do Projeto

**Última atualização:** 2026-07-27

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
| `pyproject.toml` | ✅ v0.15.0 (Fase 17 etapa 1) |
| `src/version.py` | ✅ `APP_VERSION = "0.15.0"` |
| `src/settings.py` | ✅ Configuração centralizada por env (Fase 17 et1) |
| `src/db/models.py` | ✅ 24 modelos + `criar_engine` configurável (Fase 17 et1) |
| `.env.example` | ✅ Lista de variáveis com defaults comentados (Fase 17 et1) |
| `.gitignore` | ✅ `.env` ignorado, `.env.example` versionado (Fase 17 et1) |
| `README.md` | ✅ Seção "Configuração" + tabela de env vars (Fase 17 et1) |
| `tests/test_settings.py` | ✅ 27 testes novos (Fase 17 et1) |
| Demais componentes (Fases 1-16) | ✅ Estáveis |
| **Total** | **372 testes — 100% passando** |

## Próximos Passos (visão de longo prazo)

1. ✅ Fundação de produção (em andamento — Fase 17)
2. ⏳ Deploy real em VPS/cloud com domínio e SSL (externo, mediante autorização)
3. ⏳ Integração nativa com sistemas de terceiros (Domínio, Questor, Alterdata — depende de contrato)
4. ⏳ Suporte a ECDs reais de clientes (testes com arquivos >100MB — Etapa 4 da Fase 17)
5. ⏳ Migração completa para PostgreSQL em produção (Etapas 2-3 da Fase 17)
6. ⏳ CI/CD com deploy automático (Etapa 6 da Fase 17 — apenas preparação)

## Pendências

- Postgres não roda no sandbox de testes (sem psycopg/psycopg2 pré-instalado).
  Cobertura multi-backend fica para a Etapa 2; enquanto isso,
  `criar_engine(url="postgresql+psycopg://...")` é exercitado apenas na
  construção da `Engine`, não na conexão.
- Playwright E2E requer Chromium do sistema (não roda em CI padrão).
- Rate limiting em memória não persiste entre restarts (aceitável para v1).
- Redis server não disponível no CI — fallback para memória funciona.
