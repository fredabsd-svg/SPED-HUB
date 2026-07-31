# Estado real do projeto

Primeiro arquivo a ler depois de `REGRAS-DO-PROJETO.md`.

Regra §1.8: nada é marcado como concluído sem os testes daquela fase
passando. A coluna "Evidência" aponta o teste que prova.

**Última atualização:** 2026-07-30 · **Versão:** 0.19.0

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
| 34 | Escopo e poder da API Key | concluída | `tests/test_escopo_de_api_key.py` | chave sem dono segue lendo tudo, por retrocompatibilidade |
| 35 | `docker compose up` funciona na primeira execução | concluída | `tests/test_deploy_config.py::TestEntrypointDoNginxExecutado` | emissão do certificado real segue manual (ver `docs/deploy.md`) |
| 36 | Worker encerra em vez de girar em vazio com a fila quebrada | concluída | `tests/test_worker_fila_quebrada.py`, `tests/test_migrations.py::TestLoggingSobreviveAMigracao` | — |
| 37 | Registro público fecha depois do primeiro usuário | concluída | `tests/test_registro_publico.py` | tela de gestão de usuários segue fora; a criação é por CLI |
| 38 | Mensagem de erro chega à tela | concluída | `tests/test_registro_publico.py::TestMensagemDeErroChegaNaTela`, `tests/test_e2e_playwright.py` | — |
| 39 | Central de Documentos Fiscais: modelo em três camadas, adaptador de NF-e e importação em lote | concluída | `tests/test_documentos_fiscais.py`, `tests/test_migrations.py` | NFS-e, classificação, alterações em massa e geradores de SPED seguem no roadmap |
| 40 | Camada efetiva: ajustes com histórico e reversão por lote | concluída | `tests/test_camada_efetiva.py` | — |
| 41 | Motor de classificação fiscal com regras, prioridade e conflito | concluída | `tests/test_classificacao_fiscal.py` | nenhuma tela mostra as sugestões ainda; a aplicação é por chamada |
| 42 | Alterações em massa: seleção, simulação com impacto, proteções e reversão | concluída | `tests/test_alteracoes_em_massa.py` | recálculo de totais (§12.5) segue no roadmap |
| 43 | Gerador da EFD ICMS/IPI (blocos 0, C, E, 9) | concluída | `tests/test_gerador_efd_icms.py` | inventário, ativo, serviços, ajustes 5.1.1 e ST seguem fora — ver `docs/modules/escrituracoes.md` |
| 44 | Gerador da EFD-Contribuições (blocos 0, C, M, 9), com regime e atividade como cadastro obrigatório | concluída | `tests/test_gerador_efd_contribuicoes.py` | no regime cumulativo os créditos das entradas **não** são descontados, e o resultado avisa; o `IND_ATIV` tem tabela própria, diferente da EFD ICMS/IPI; blocos A, D, F e I, créditos extemporâneos e regimes especiais seguem fora |
| 45 | Terceira camada: a escrituração arquivada — o arquivo que efetivamente saiu | concluída | `tests/test_escrituracao_arquivada.py` | o conteúdo é guardado, não reconstruído, e a linha nunca é alterada; nenhuma tela mostra o histórico ainda |
| 47 | Apuração de CBS, IBS e Imposto Seletivo | concluída | `tests/test_apuracao_reforma.py` | o IS **não** gera crédito e as duas parcelas do IBS são apuradas em separado; o total de 2026 não é o valor a recolher, e o resultado avisa. Monofásico, diferimento e split payment seguem fora |
| 46 | `sped-hub fiscal`: a cadeia da Central pela linha de comando | concluída | `tests/test_cli_fiscal.py` | regras, importar, listar, classificar, alterar, desfazer, gerar e conferir. `classificar` e `alterar` **não gravam** sem que se peça; gerar **sempre** arquiva; `conferir` sai com 2 quando o entregue divergiu. Telas web seguem fora |

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
| Chave de API sem dono lê todos os escritórios | Toda chave criada antes da coluna `escritorio_id` ficou com dono nulo, e nulo significa "chave de instância". Preenchê-las com um escritório arbitrário quebraria integração em produção; com o errado, seria pior — a integração pararia de ver os dados certos sem explicação. Chave nova deve ser criada com dono | — (é escolha de retrocompatibilidade) |
| `SPED_HUB_SECRET_KEY` não tem consumidor | A única variável reservada que sobrou (§2.2). Sessões e tokens usam CSPRNG; o webhook assina com o segredo do próprio registro. Ligá-la exigiria decidir *qual* segredo ela é, e hoje nenhum componente precisa de um | — |
| Criar usuário depois do primeiro exige acesso ao servidor | O `/register` é público: enquanto esteve aberto, qualquer um que alcançasse o servidor criava conta e caía no mesmo grupo do contador — numa instalação de escritório único ninguém tem `escritorio_id`, nem os usuários nem as empresas importadas. Fechá-lo foi a correção; a alternativa (tela de convite com papel e escritório) é trabalho de front-end que ninguém pediu ainda. `sped-hub usuario criar` resolve o caso real | Tela de gestão de usuários no painel |
| Documento entre duas empresas do mesmo escritório é escriturado por uma só | Transferência entre filiais deveria entrar como saída numa e entrada na outra, e o modelo só admite uma `empresa_id` por documento. Fica com o emitente, por ser quem tem a obrigação de emitir, e o log avisa que a contraparte precisa de escrituração própria | Documento com escrituração por ambas as pontas |
| A primeira subida usa certificado autoassinado, e o navegador reclama | O certificado do Let's Encrypt só pode ser emitido depois de o nginx responder na porta 80 no domínio real, com DNS já apontado — nada disso existe quando alguém roda `docker compose up` pela primeira vez. Antes o nginx recusava subir; agora ele sobe, avisa no log que é autoassinado e serve em `http://localhost/`. A emissão do certificado real é um comando manual, documentado em `docs/deploy.md` | — (é escolha: emitir sozinho exigiria domínio e DNS que o sistema não tem como adivinhar) |

## Passivo de documentação de módulo (§1.4)

A regra vale por adoção: módulo novo ou alterado exige o documento. A lista
abaixo diminui a cada PR que toca nesses módulos.

**Documentados:** `api`, `async_jobs`, `audit`, `auth`, `cache`, `cli`,
`cli_fiscal`, `dashboard`, `db`, `documentos`, `escrituracoes`,
`ecd_importer`, `email_service`, `filters`,
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
