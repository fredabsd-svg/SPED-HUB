# SPED-HUB — Continuidade do Projeto

**Última atualização:** 2026-07-29

## Fase 17 — Fundação de Produção ✅ CONCLUÍDA

Versão `0.16.0`.  **562 testes passando, 28 skips** — `ruff`, `black` e
`pytest` verificados com os comandos exatos do workflow; 39 testes rodam
contra um PostgreSQL 16 real.

### Etapa 1.1 — Saneamento da fundação

A Etapa 1 tinha sido entregue pela metade e o CI nunca havia passado.

- **CI vermelho em 14 de 14 execuções.**  Falhava no `ruff`, e como ele vem
  antes, o `pytest` nunca rodou no CI.  Causa raiz: `ruff`/`black` sem pin,
  então o CI passou a falhar sozinho no salto para ruff 0.16.  Agora as
  ferramentas estão pinadas e `[tool.ruff.lint].select` é explícito.
- **`src/settings.py` era código morto** — só `models.py` o importava, e 33
  leituras de `os.environ` continuavam espalhadas.  Das 20 variáveis
  documentadas, apenas 2 eram consumidas.  O exemplo de PostgreSQL do README
  subia em SQLite.
- Nomes divergentes entre doc e código (`SMTP_PASSWORD`/`SMTP_PASS`,
  `EMAIL_FROM`/`SMTP_FROM`, `MAX_UPLOAD_MB`/`MAX_UPLOAD_BYTES`).
- Deploy Docker quebrado em três pontos; `src/cli.py` com 0% de cobertura.

Bugs encontrados ao ligar a configuração: coerção booleana só existia para
`debug` (`SPED_HUB_DB_ECHO=false` **ligava** o echo); `lstrip("./")`
corrompia caminho SQLite absoluto; `verificar_senha` comparava hashes com
`==`, vazando por tempo.

### Etapa 2 — PostgreSQL ✅

Schema completo (24 tabelas), importação, relatórios, filtros, validações,
dashboard, auth, API keys, auditoria, rate limit, jobs e webhooks
exercitados contra Postgres 16 real.  A camada ORM porta sem alteração.
Duas divergências, ambas **silenciosas em SQLite**:

1. `LIKE` é case-insensitive no SQLite e case-sensitive no Postgres — a busca
   por histórico devolvia 2 resultados em um banco e 0 no outro.
2. `String(n)` é ignorado pelo SQLite e imposto pelo Postgres — um
   `User-Agent` acima de 512 caracteres derrubava o **login inteiro**.

`tests/test_multibackend.py` + job `postgres` no CI.

### Etapa 3 — Migrações Alembic ✅

Política em `docs/migrations.md`: Postgres versionado por migração, SQLite
com `create_all`.  `sped-hub migrar status|aplicar|adotar`.  Upgrade sob
`pg_advisory_xact_lock` (web e worker sobem juntos) e serviço `migrate`
dedicado no compose.  `adotar` traz instalações das fases 1–16 para o
Alembic sem recriar nada.

O teste central compara, coluna a coluna e índice a índice, o schema
produzido por `upgrade head` com o produzido pelos modelos — é o que impede
a divergência silenciosa quando alguém esquece de gerar a revisão.

### Etapa 4 — ECDs grandes ✅

Medição antes de mexer: a memória já estava correta (+8 MB independente do
tamanho), mas eram **59 s para 8,6 MB** — ~11 min para 100 MB.  O profiler
apontou 20.267 flushes para 20.000 lançamentos: um round-trip por I200, 76%
do tempo.  Passando a anexar pelas relações ORM, caiu para 23 flushes e
**59 s → 27 s**.

`SPED_HUB_ECD_CHUNK_ROWS` e `_BYTES` passaram a ter consumidor.
Cancelamento por token (`POST /api/jobs/{id}/cancelar`) que reverte a
transação inteira — escrituração pela metade é pior que nenhuma.

### Etapa 5 — Hardening ✅

Validação de conteúdo no upload (assinatura `|0000|` antes de gravar);
rate limit por IP com escopo separado para login (10/min) e API (300/min);
CSP e demais cabeçalhos no nginx, escritos a partir do que a aplicação de
fato carrega; logs com PII mascarada e formato JSON opcional.

`X-Forwarded-For` só é lido com `SPED_HUB_TRUST_PROXY` — sem proxy à frente,
o cliente escreve o cabeçalho e o limite viraria decoração.

### Etapa 6 — CI/CD de release ✅

`release.yml`: build multi-arch (amd64 + arm64) e push para GHCR ao criar
tag `vX.Y.Z`, reaproveitando a suíte do `ci.yml` via `workflow_call` e
conferindo que a tag bate com `src/version.py`.  **Não faz deploy** — há
teste que falha se um passo de deploy for introduzido.

`docs/deploy.md` traz o checklist manual: DNS, SSL, PostgreSQL, backup
(antes da migração, porque migração aplicada não volta sozinha), rollback e
observabilidade.

## Em aberto — decisões de produto

- `exportar balancete --formato pdf` **gera XLSX**, em caminho diferente do
  `--saida`, avisando só em log INFO.  Não existe template PDF para o
  balancete.  Corrigir exige escolher entre criar o template ou recusar a
  combinação com erro explícito.
- **Retomada de importação a partir de offset** (constava na Etapa 4) não
  foi implementada: exigiria commits parciais, e uma ECD parcialmente
  comprometida ficaria visível para relatórios, com balanço que não fecha e
  nada indicando que está incompleta.  Se for desejada, precisa vir com um
  estado explícito de "importação incompleta" que os relatórios respeitem.
- 3 testes E2E do Playwright falham por timeout de navegação quando há
  Chromium disponível.  Nunca chegaram a rodar antes (erravam na fixture,
  por um caminho absoluto de outra máquina), então são falhas recém-expostas,
  não regressões.  Em CI seguem pulando.
- `SPED_HUB_SECRET_KEY` continua sem consumidor, documentada como reservada.

## Próximos passos (dependem de autorização ou contrato)

1. Deploy real em VPS/cloud com domínio e SSL — o checklist está pronto,
   falta a decisão operacional e as credenciais.
2. Migração de dados SQLite → PostgreSQL para instalações existentes.  Hoje
   o caminho é reimportar as ECDs (a importação recusa duplicatas, então
   repetir é seguro); um migrador de dados seria trabalho próprio.
3. Integração nativa com Domínio, Questor e Alterdata — depende de contrato
   e documentação das APIs.
4. ECDs reais de clientes.  A importação foi validada com arquivos
   sintéticos de até 240 mil registros; arquivos reais trazem variações de
   leiaute que só aparecem em campo.

## Pendências técnicas conhecidas

- Rate limiting (por chave e por IP) é em memória: não persiste entre
  restarts nem é compartilhado entre réplicas.  Aceitável para instância
  única; múltiplas réplicas exigiriam Redis.
- Redis não disponível no CI — o fallback para memória funciona e é o que os
  testes exercitam.
- Playwright E2E requer Chromium (procurado em `SPED_HUB_CHROMIUM`,
  `PLAYWRIGHT_BROWSERS_PATH` e caminhos do sistema); sem ele, pula.

---

## Histórico anterior (Fases 1–16)

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
