# dashboard

## O que faz

É a interface web da plataforma: FastAPI + Jinja2 + HTMX + Alpine.js. Serve
as páginas (dashboard, upload, comparação, auditoria, monitoramento),
responde parciais HTMX e JSON para gráficos, e monta o app único que também
inclui a API REST v1 e o GraphQL v2. A agregação de dados (KPIs, evolução,
composição, comparativos) fica em `services.py`; o cálculo contábil em si
vem de `reports/`.

## O que expõe

`app.py` — o `app` FastAPI (entry point do Docker e do script
`sped-hub-dashboard`), com rotas em grupos:

| Grupo | Rotas |
|---|---|
| Autenticação | `/login`, `/register`, `POST /api/login`, `POST /api/register`, `/logout` |
| Páginas | `/`, `/upload`, `/fiscal/importar`, `/fiscal/documentos`, `/fiscal/documentos/{id}`, `/fiscal/classificar`, `/fiscal/corrigir`, `/fiscal/gerar`, `/fiscal/cadastro`, `/comparar`, `/layout`, `/api-keys`, `/webhooks`, `/auditoria`, `/monitoring` |
| Upload | `POST /api/upload` (ECD, importa), `/api/upload-efd`, `/api/upload-ecf` (só resumo), `/api/upload-async` + `/api/jobs/*` |
| Dados (parciais HTMX/JSON) | `/api/kpis`, `/api/balanco`, `/api/dre`, `/api/dfc`, `/api/diario`, `/api/graficos`, `/api/ecds`, `/api/filtros/aplicar`, `/api/multi-ecd`, `/api/comparar`, `/api/notas` |
| Exportação | `/api/export/pdf`, `/xlsx`, `/multi-formato` (ZIP), `/lote` |
| Administração (admin) | `/api/audit/*`, `/api/email/*`, `/api/worker/status`, `/api/monitoring/*`, `/api/health/full` |

`services.py` — `DashboardService` (KPIs, evolução patrimonial e
multi-período, composição do ativo, waterfall DRE, DFC, comparativos, notas
explicativas automáticas) e os dataclasses `KPICard`/`DashboardData`. Em
`static/vendor/` vivem htmx, Alpine, Chart.js e SortableJS versionados.

## Depende de / quem depende

Depende de quase tudo: `api`, `auth`, `audit`, `ratelimit`, `cache`,
`db.models`, `ecd_importer`, `uploads`, `filters.engine`,
`parsers.efd/ecf`, `reports/*`, `monitoring`, `email_service`, `settings`,
`logging_config`.

Ninguém importa o módulo em produção — quem o consome é o servidor ASGI
(`uvicorn src.dashboard.app:app`) e os testes. É o topo da pilha.

## Decisões não óbvias e armadilhas

- **Multi-tenancy por `escritorio_id`**: o middleware extrai
  `ecd_id`/`ecd_ids` da query string e valida com
  `usuario_pode_acessar_ecd`; ECD de outro escritório responde **404, não
  403** — não vaza que ela existe. Listagens passam por
  `aplicar_escopo_empresas`; o upload grava o `escritorio_id` do usuário
  logado.
- **O escopo do cadastro fiscal é aplicado na consulta, não conferido depois.**
  `_empresa_do_usuario` monta o `select` já com `aplicar_escopo_empresas`; um
  `session.get` seguido de `if empresa.escritorio_id != ...` seria a mesma
  proteção até alguém acrescentar um caminho que esquece o `if`. Empresa de
  outro escritório responde igual a empresa inexistente — a mesma frase, para
  a tela não virar oráculo de quais ids existem no banco.
- **Os formulários são testados como o navegador os enviaria.** Teste de rota
  monta o `POST` à mão; teste de página confere o HTML. Entre os dois cabe um
  `name=` divergindo do que a rota lê — e aí os dois passam com a página
  quebrada. `tests/test_formularios_batem_com_as_rotas.py` lê o formulário da
  página e envia o que ele declara.
- **O menu só mostra o que o usuário pode abrir.** Link que devolve 403 é pior
  que link ausente: a mensagem fala de permissão, e quem clicou não pediu
  permissão nenhuma.
- **O espelho não gera; gerar sempre arquiva.** Não existe prévia que escreva
  arquivo: a prévia é o espelho, que é prosa e não arquivo transmissível —
  ninguém o entrega por engano. E o download de uma escrituração serve o
  conteúdo **guardado**, nunca um recém-gerado: gerar de novo produziria um
  arquivo parecido e possivelmente diferente, porque a camada efetiva pode ter
  mudado, e isso responderia outra pergunta.
- **Na importação, o escritório vem do usuário logado — nunca do formulário.**
  É a única tela fiscal em que não existe "empresa escolhida": o documento traz
  a empresa dentro dele, pelo CNPJ. Nas outras, um id alheio é recusado porque
  o escopo não alcança; aqui não haveria nada a recusar, e o documento nasceria
  no acervo de outro escritório sem que ninguém precisasse enxergá-lo.
- **Classificar e corrigir mandam de volta o total que mostraram.** O POST
  re-simula e compara: divergiu, nada é gravado. Entre ver e confirmar cabe uma
  importação ou outra pessoa corrigindo, e o lote reversível não ajuda quem não
  percebeu que aprovou trinta e gravou trezentas.
- **A tela do documento mostra as três camadas separadas, e não só o valor
  final.** Mostrar só o efetivo faria a tela desmentir o modelo de dados: o
  sistema guarda as três porque a resposta a uma intimação depende de saber
  qual é qual. O XML é servido do que foi guardado (`xml_original`), nunca
  remontado das colunas — a camada original só vale enquanto for o que o
  emitente assinou.
- **A divergência de classificação fica no item, não na página.** A apuração
  já apontava CST e `cClassTrib` fora da tabela oficial, mas ela é do mês
  inteiro: achar *qual* nota tem o problema exigiria sair procurando. Aqui o
  apontamento aparece ao lado da tabela do item, com a data de publicação da
  tabela — sem ela, quem lê não sabe se a divergência é do documento ou do
  sistema. E aparece **só quando existe**: seção que sai sempre treina quem
  lê a ignorá-la. A conferência é sobre os valores **efetivos**, que são os
  que vão para o arquivo.
- **Campo corrigido entra na tabela mesmo fora da lista de revisão.** Revisar
  os 68 campos de cada item seria ilegível, mas um ajuste que a tela não
  mostrasse seria correção invisível — e é isso que a tela existe para
  impedir. Os campos revisados por padrão são os mesmos que a planilha leva e
  traz (`documentos.planilha:EDITAVEIS`): uma segunda lista divergiria da
  primeira dizendo a mesma coisa.
- **As seções da tela têm `data-secao`.** É o que deixa o teste dizer *onde*
  espera encontrar cada coisa. Sem isso, o histórico — que mostra campo, valor
  anterior e valor novo — dá falso positivo em quase toda asserção sobre a
  tabela de camadas, e ela poderia sumir inteira sem nenhum teste reclamar.
- **A tela do cadastro não tem tabela própria.** `CADASTRO_FISCAL` e a
  validação vêm de `escrituracoes.cadastro`, o mesmo módulo que a linha de
  comando usa. Uma segunda cópia divergiria da primeira no primeiro ato
  normativo, e a tela é onde ninguém iria conferir.
- **`SPED_HUB_ALLOWED_HOSTS` valida o cabeçalho `Host`.** Fora da lista, 400;
  `*` aceita qualquer um. Aceita curinga de subdomínio (`*.dominio`, que
  também cobre o domínio nu) e ignora a porta. Antes a variável era
  documentada em três lugares — inclusive no `docs/deploy.md`, que manda pôr o
  domínio real "**não** `*`" como passo de endurecimento — e **nada a lia**:
  quem seguia o guia acreditava ter restringido o Host (§2.2).
- **Loopback é sempre aceito**, fora da lista. O `HEALTHCHECK` do container
  chama `http://localhost:8000/api/v1/health`; recusar esse `Host` marcaria o
  container como não saudável para sempre — o mesmo defeito que a 0.16.1 já
  corrigiu por outro caminho. Liberar loopback não ajuda atacante: um link
  `http://localhost/...` não leva a nada para ele.
- **Quatro middlewares em camadas**: métricas (sem query string nem payload),
  rate limit por IP, validação de `Host` e auth. A validação de `Host` fica
  por dentro das métricas de propósito: requisição recusada continua visível
  no painel. O escopo `login` tem cota própria, bem mais
  apertada — `/api/login` e `/api/register` são públicos, então sem limite
  por IP varrer senhas não custaria nada. Os headers `X-RateLimit-*` usam
  `setdefault` para não sobrescrever a cota anunciada pelo limitador por API
  Key.
- **Sessão**: cookie `sped_hub_session` httponly, `samesite=lax`, 24 h.
  Login, falha de login e logout vão para a auditoria.
- **O handler `htmx:beforeSwap` no `base.html`** converte respostas JSON:
  `redirect` vira navegação, erro vira alerta escapado, sucesso com mensagem
  vira `.alert-success`. Sem ele o HTMX injetava o JSON cru na página. Os
  formulários de login/registro declaram `method="post" action=...` — o
  fallback que impede a senha de ir para a URL se o script não carregar.
- **Assets vendorizados, nunca CDN**: sem acesso externo a aplicação
  degradava em silêncio e cada página carregava versão diferente.
- **Identidade "Tinta & Latão"**, a mesma dos relatórios exportados: paleta
  em `--primary`/`--accent`, títulos em Source Serif 4, corpo em Source
  Sans 3. As fontes em `static/fonts/` são **cópias** das dos relatórios —
  o nginx serve `/static/` direto do disco — e
  `tests/test_identidade_dashboard.py` falha se as cópias divergirem. As
  quatro páginas que não herdam o `base.html` declaram as fontes por conta
  própria; cor de dado (gráficos) segue a identidade, badge de estado
  (sucesso/erro) mantém o verde/vermelho semântico.
- **`get_composicao_ativo` tem trava de ciclo** ao subir a hierarquia do
  plano de contas. A hierarquia vem do arquivo do cliente; um ciclo A→B→A
  fazia o laço rodar para sempre e o dashboard inteiro parava para todos os
  usuários (event loop único).
- **O banco vem de `database_reference()`** e é relido a cada uso — antes só
  `SPED_HUB_DB` era lido e `DATABASE_URL` era ignorada em silêncio.
- As APIs externas (`/api/v1`, `/api/v2/graphql`) têm autenticação própria
  por API Key — o middleware do dashboard as ignora de propósito.

## Como testar isoladamente

```bash
pytest tests/test_fase13.py tests/test_fase14.py tests/test_fase16.py -q
pytest tests/test_hardening.py tests/test_vendor_assets.py -q
pytest tests/test_hierarquia_ciclica.py -q       # trava de ciclo
pytest tests/test_fase7.py tests/test_fase10.py -q   # DashboardService
```

Navegador de verdade: `pytest -m e2e` (`tests/test_e2e_playwright.py`, sobe
uvicorn real).

## O que não faz

- Não implementa hash de senha nem tokens (`auth`), nem o limitador em si
  (`ratelimit`), nem parsing/importação (`parsers`/`ecd_importer`), nem
  cálculo contábil (`reports`).
- Não persiste EFD/ECF: o upload devolve o resumo e apaga o arquivo.
- Não persiste preferências de layout.
- A lógica da API v1 e do GraphQL vive em `src/api/`; aqui só são montadas.
