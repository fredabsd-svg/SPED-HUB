# Estado real do projeto

Primeiro arquivo a ler depois de `REGRAS-DO-PROJETO.md`.

Regra §1.8: nada é marcado como concluído sem os testes daquela fase
passando. A coluna "Evidência" aponta o teste que prova.

**Última atualização:** 2026-07-29 · **Versão:** 0.16.1

## Fases

| Fase | Tema | Estado | Evidência | Pendências |
|---|---|---|---|---|
| 1–8 | Parsers, relatórios, exportação, dashboard | concluída | `tests/test_parsers.py`, `tests/test_reports.py`, `tests/test_fase2.py` | — |
| 9 | GraphQL v2, exportação multi-formato | concluída | `tests/test_fase9.py` | — |
| 10 | Webhooks, multi-ECD, layout customizável | concluída | `tests/test_fase10.py` | — |
| 11 | Dashboard de webhooks, multi-tenancy | concluída | `tests/test_fase11.py` | — |
| 12 | Middleware multi-tenant, API keys | concluída | `tests/test_fase12.py` | — |
| 13 | Rate limiting, auditoria | concluída | `tests/test_fase13.py` | — |
| 14 | Jobs assíncronos, cache | concluída | `tests/test_fase14.py` | — |
| 15 | Redis, fila de workers, e-mail | concluída | `tests/test_fase15.py` | Redis não roda no CI; fallback para memória é o que se testa |
| 16 | Observabilidade e monitoramento | concluída | `tests/test_fase16.py`, `tests/test_review_regressions.py` | — |
| 17 | Fundação de produção | concluída | `tests/test_settings.py`, `tests/test_multibackend.py`, `tests/test_migrations.py`, `tests/test_ecd_grande.py`, `tests/test_hardening.py`, `tests/test_deploy_config.py`, `tests/test_cli.py` | ver "Em aberto" |
| 18 | Front-end sem CDN | concluída | `tests/test_vendor_assets.py` | testes de navegador seguem opt-in (§3.5) |
| 19 | Regras do projeto verificáveis | concluída | `tests/test_regras_projeto.py` | 18 dos 24 módulos ainda sem documento (§1.4, por adoção) |
| 20 | Testes de navegador | concluída | `tests/test_e2e_playwright.py`, `tests/test_hierarquia_ciclica.py` | seguem opt-in sob o marcador `e2e` (ADR 0004) |

## Em aberto — decisões de produto

Itens conhecidos que não são defeito de implementação, mas escolha pendente.
Ver `docs/roadmap.md` para o que ainda não existe.

| Item | Situação |
|---|---|
| `exportar balancete --formato pdf` gera XLSX, em caminho diferente do `--saida` | Não existe template PDF para o balancete. Corrigir exige criar o template ou recusar a combinação com erro explícito. Comportamento atual fixado em `tests/test_cli.py`. |
| Retomada de importação a partir de offset | Exigiria commits parciais, o que viola a §6.1. Precisaria vir com estado explícito de "importação incompleta" que os relatórios respeitem. |
| `SPED_HUB_SECRET_KEY` sem consumidor | Documentada como reservada (§2.2). Sessões e tokens usam CSPRNG; o webhook assina com o segredo do próprio registro. |
| `SPED_HUB_DEBUG` sem consumidor | Documentada como reservada (§2.2). O campo é lido e coagido, mas nenhum componente muda de comportamento por causa dele. Remover exige decidir se o projeto quer um modo de diagnóstico. |
| Ciclo na hierarquia do plano de contas | O dashboard agora para de subir e registra aviso, em vez de travar. A ECD segue importada com a hierarquia inválida, e `validators/integridade.py` ainda não detecta o ciclo — quem recebe o arquivo não é avisado no momento da validação. |

## Passivo de documentação de módulo (§1.4)

A regra vale por adoção: módulo novo ou alterado exige o documento. A lista
abaixo diminui a cada PR que toca nesses módulos.

**Documentados:** `settings`, `db`, `ecd_importer`, `uploads`, `ratelimit`,
`logging_config`.

**Pendentes:** `api`, `async_jobs`, `audit`, `auth`, `cache`, `cli`,
`dashboard`, `email_service`, `filters`, `monitoring`, `parsers`, `reports`,
`validators`, `version`, `watchdog`, `webhooks`, `worker_queue`,
`worker_runner`.

`src/layouts/` não entra na conta: é diretório de dados (YAML de layout de
ECD), sem código e sem `__init__.py`.

## Pendências técnicas conhecidas

- Rate limiting (por chave e por IP) é em memória: não persiste entre
  reinícios nem é compartilhado entre réplicas. Aceitável para instância
  única; múltiplas réplicas exigiriam Redis.
- Redis não está disponível no CI — o fallback para memória é o que os
  testes exercitam.
- Testes de navegador exigem Chromium; sem ele, pulam.
