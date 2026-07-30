# Estado real do projeto

Primeiro arquivo a ler depois de `REGRAS-DO-PROJETO.md`.

Regra §1.8: nada é marcado como concluído sem os testes daquela fase
passando. A coluna "Evidência" aponta o teste que prova.

**Última atualização:** 2026-07-29 · **Versão:** 0.17.0

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
| 19 | Regras do projeto verificáveis | concluída | `tests/test_regras_projeto.py` | — |
| 20 | Testes de navegador | concluída | `tests/test_e2e_playwright.py`, `tests/test_hierarquia_ciclica.py` | seguem opt-in sob o marcador `e2e` (ADR 0004) |
| 21 | Validação de hierarquia cíclica | concluída | `tests/test_validators.py` | recusar o arquivo na importação segue como decisão de produto |
| 22 | Identidade "Tinta & Latão" nos exportados | concluída | `tests/test_identidade_export.py`, `tests/test_cli.py` | — (o dashboard aderiu na Fase 24) |
| 23 | Documentação de módulo completa (24/24) | concluída | `tests/test_regras_projeto.py` | conteúdo × código é item de revisão (§1.12) |
| 24 | Identidade "Tinta & Latão" no dashboard web | concluída | `tests/test_identidade_dashboard.py`, `tests/test_e2e_playwright.py` | — |
| 25 | Recusa de ECD com hierarquia cíclica (ADR 0006) | concluída | `tests/test_hierarquia_ciclica.py` | bancos legados seguem cobertos pela validação (h) |
| 26 | Webhooks emitem os eventos documentados | concluída | `tests/test_webhooks_emissao.py` | sem fila persistente; recuperação é manual |
| 27 | Configuração documentada com efeito real (§2.2) | concluída | `tests/test_config_com_efeito.py`, `tests/test_regras_projeto.py` | `SPED_HUB_SECRET_KEY` segue reservada, por decisão de produto |
| 28 | Contabilidade de entregas de webhook | concluída | `tests/test_webhooks_entregas_orfas.py`, `tests/test_migrations.py` | o reenvio segue manual e sequencial |
| 29 | Importação interrompida por reinício é encerrada | concluída | `tests/test_jobs_interrompidos.py` | retomar de onde parou segue fora (§6.1) |
| 30 | Roadmap com marcador de ausência verificável (§1.13) | concluída | `tests/test_regras_projeto.py::TestRoadmap`, `tests/test_regras_projeto.py::TestResolucaoDeMarcador` | — |
| 31 | Retenção de histórico que realmente executa | concluída | `tests/test_manutencao.py` | auditoria segue com limpeza manual, por escolha |
| 32 | Migração de dados entre bancos (`sped-hub migrar-dados`) | concluída | `tests/test_migracao_de_dados.py` | exercitada contra PostgreSQL real no CI |
| 33 | Reenvio automático de entrega interrompida | concluída | `tests/test_manutencao.py::TestReenvioAutomatico` | `failed` segue no reenvio manual, por escolha |

## Limites do comportamento atual

O que o sistema **faz hoje** e que alguém poderia esperar diferente. Não é
defeito: é escolha, com a razão registrada. O que **não existe** e mudaria
cada um destes limites está em [`roadmap.md`](roadmap.md) — aqui não se
descreve funcionalidade futura (§1.1).

| Limite | Por que é assim | O que mudaria |
|---|---|---|
| Importação interrompida pede reenvio do arquivo | Retomar de onde parou exigiria commits parciais, que a §6.1 proíbe. O job é encerrado com aviso de que nada foi gravado | Retomada de importação interrompida |
| Encerrar job abandonado na subida pressupõe instância única | O executor é uma thread `daemon` dentro do processo web. Com mais de uma réplica, a subida de uma encerraria o job em andamento da outra. O deploy documentado é de instância única, e o limite por IP já pressupõe isso | Executor de importação fora do processo web |
| Log de auditoria não é expurgado automaticamente | É o registro de quem mexeu em escrituração fiscal. Apagá-lo por conta própria não é decisão que o sistema possa tomar sozinho; a limpeza é manual, por rota de administrador | — (é escolha, não pendência) |
| Entrega de webhook que esgotou as tentativas espera intervenção | O reenvio automático retoma só o que uma queda interrompeu no meio. Entrega que respondeu mal em **todas** as tentativas não é reenviada sozinha: martelar de hora em hora um endereço quebrado não resolve, e ali o que falta é alguém olhar. O botão "Reenviar falhas" alcança essas, e é aguardado dentro da requisição — daí o lote limitado por tempo (`LOTE_DE_REENVIO`) | — (é escolha, não pendência) |
| `SPED_HUB_SECRET_KEY` não tem consumidor | A única variável reservada que sobrou (§2.2). Sessões e tokens usam CSPRNG; o webhook assina com o segredo do próprio registro. Ligá-la exigiria decidir *qual* segredo ela é, e hoje nenhum componente precisa de um | — |

## Passivo de documentação de módulo (§1.4)

A regra vale por adoção: módulo novo ou alterado exige o documento. A lista
abaixo diminui a cada PR que toca nesses módulos.

**Documentados:** `api`, `async_jobs`, `audit`, `auth`, `cache`, `cli`,
`dashboard`, `db`, `ecd_importer`, `email_service`, `filters`,
`logging_config`, `monitoring`, `parsers`, `ratelimit`, `reports`,
`settings`, `uploads`, `validators`, `version`, `watchdog`, `webhooks`,
`worker_queue`, `worker_runner`.

**Pendentes:** nenhum — o passivo foi zerado na Fase 23. Módulo novo entra
com documento no mesmo PR (§1.4).

`src/layouts/` não entra na conta: é diretório de dados (YAML de layout de
ECD), sem código e sem `__init__.py`.

## Pendências técnicas conhecidas

- Rate limiting (por chave e por IP) é em memória: não persiste entre
  reinícios nem é compartilhado entre réplicas. Aceitável para instância
  única; múltiplas réplicas exigiriam Redis.
- Redis não está disponível no CI — o fallback para memória é o que os
  testes exercitam.
- Testes de navegador exigem Chromium; sem ele, pulam.
