# Changelog

Formato [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Atualizado ao fim de cada fase, não só em release (§1.7).

As entradas descrevem o efeito para quem usa o sistema, não o detalhe
interno da implementação.

## [Não publicado]

### Segurança
- **Uma chave de API entregue a um integrador dava a ele controle da
  instância.** Ela podia criar novas chaves para si — e revogar a original não
  tirava o acesso —, listar e revogar as chaves do escritório, derrubando as
  integrações legítimas, e elevar a própria cota de requisições, anulando o
  limite que existe para proteger o sistema. Administrar chaves e cotas passou a
  exigir administrador logado no painel.
- **Uma chave de API lia a escrituração de todos os escritórios.** Agora a chave
  tem escritório dono, e lê apenas o dele — nas listagens e também ao pedir uma
  escrituração pelo identificador direto, que era o caminho que tornava o escopo
  de listagem inútil. Chaves já existentes seguem lendo tudo, para não
  interromper integrações em funcionamento; crie chaves novas com escritório.

### Alterado
- **Mudança que quebra integração existente:** um sistema de terceiro que usava
  a chave de API para criar, listar ou revogar chaves, ou para configurar cota,
  passa a receber 401. Leitura de dados não muda.

## [0.18.0] — 2026-07-30

### Adicionado
- **Os webhooks passaram a disparar.** O cadastro de endpoints, a proteção
  contra SSRF e a entrega com retry já existiam, mas nenhum ponto do sistema
  emitia os eventos: o cliente cadastrava a integração e nada chegava nela.
  Agora `ecd.importada` sai depois de cada importação concluída,
  `ecd.validada` a cada validação de integridade e `relatorio.gerado` a cada
  PDF ou planilha gerada em arquivo.
- `SPED_HUB_WEBHOOK_TIMEOUT` e `SPED_HUB_WEBHOOK_DEFAULT_MAX_RETRIES` passaram
  a ter efeito — antes eram documentadas e ignoradas.
- **`EMAIL_ENABLED=false` passou a desligar o envio de verdade**, mesmo com
  credencial SMTP configurada. Antes o modo era decidido só pela presença de
  usuário e senha: uma homologação apontada para o SMTP de produção mandava
  e-mail real para o cliente do escritório.
- `SPED_HUB_MONITORING_RETENTION_HOURS` passou a limitar a janela de métricas
  (antes 24 h fixas no código) e `SPED_HUB_METRICS_WINDOW_MINUTES` passou a
  ser o período que o painel de monitoramento abre selecionado.
- `SPED_HUB_RATE_LIMIT_DEFAULT` e `SPED_HUB_RATE_LIMIT_WINDOW` passaram a
  valer para API Key sem cota própria cadastrada. Antes a cota era 100/60 s
  fixos e configurar as variáveis não mudava nada. Cota gravada no banco
  continua prevalecendo.

- O reenvio manual de webhook passou a recuperar **entregas interrompidas por
  queda do sistema**. Antes ele só via as que esgotaram todas as tentativas;
  uma entrega cortada no meio por reinício, atualização ou queda do servidor
  não aparecia em lugar nenhum — o assinante perdia o evento em silêncio e não
  havia como recuperar, nem manualmente.

- **`sped-hub migrar-dados` copia o conteúdo de um banco para outro.** Um
  escritório rodando em SQLite que quisesse PostgreSQL só tinha o caminho de
  reimportar todas as ECDs — e perdia usuários, mapeamentos de conta, visões de
  filtro, chaves de API e o histórico de auditoria, que não vêm de arquivo
  nenhum. A cópia é tudo-ou-nada, preserva os identificadores, recusa destino
  que já tenha dados, e confere as contagens no fim.
- **Entrega de webhook interrompida por queda do sistema passou a ser retomada
  sozinha.** Antes ela era recuperável, mas esperava alguém clicar em "Reenviar
  falhas". Vale só para o que a queda interrompeu: entrega que já respondeu mal
  em todas as tentativas continua esperando intervenção, porque insistir de
  hora em hora num endereço quebrado não resolve nada.

### Corrigido
- **Uma importação interrompida por reinício do sistema deixava de existir em
  silêncio.** O processamento roda dentro do servidor web; reinício,
  atualização ou queda o matava no meio, e o que sobrava na tela era
  "Aguardando processamento..." a 0% — uma mensagem dizendo que a escrituração
  estava na fila, quando ninguém mais ia processá-la. O contador esperava
  indefinidamente por uma importação que não existia mais. Agora ela é
  encerrada na subida do sistema, com o aviso de que nada foi gravado e que o
  arquivo precisa ser reenviado.
- O arquivo enviado numa importação interrompida ficava para sempre no
  servidor, ocupando espaço, sem nada que soubesse onde encontrá-lo. Agora sai
  junto.
- A limpeza automática de importações antigas nunca removia as canceladas.
- O `docs/roadmap.md` listava como ausentes duas coisas que já existiam há
  fases: a exportação do balancete em PDF e os testes de navegador no CI. Quem
  lia acreditava que faltava trabalho que não faltava. Cada item do roadmap
  passou a declarar um marcador que o pipeline verifica, então a lista não fica
  mais desatualizada em silêncio.
- **A taxa de sucesso dos webhooks estava errada.** Ela contava tentativas em
  vez de entregas, então um evento que chegou na terceira tentativa aparecia
  como uma entrega bem-sucedida em três. Uma integração instável mas
  funcionando era anunciada no painel com 33% de sucesso.
- O histórico de entregas acumulava linhas paradas em "retrying" para sempre:
  toda tentativa que falhava ficava nesse estado e nunca era resolvida. O
  painel as mostrava como se ainda estivessem em andamento. Bancos existentes
  são reconciliados na atualização, e o que ficou sem desfecho volta a ser
  reenviável.
- Um clique em "Reenviar falhas" com muitas entregas pendentes e o endereço do
  assinante fora do ar deixava a requisição aberta por quase uma hora — o
  navegador desistia e o trabalho seguia no servidor sem ninguém saber. O
  reenvio agora processa um lote por vez e informa quantas ficaram.
- Os estados de entrega no painel deixaram de aparecer em inglês cru: agora
  dizem "entregue", "não entregue", "entregue no reenvio", com explicação ao
  passar o mouse.
- Um webhook com a lista de eventos corrompida no banco impedia a entrega
  para **todos** os outros webhooks.

### Segurança
- **`SPED_HUB_ALLOWED_HOSTS` passou a valer.** A aplicação agora recusa
  requisição com `Host` fora da lista configurada. A variável era documentada
  no README, no `.env.example` e no guia de deploy — que manda trocar o `*`
  pelo domínio real como passo de endurecimento de produção — e nenhum
  componente a lia: quem seguia o guia acreditava ter restringido o acesso e
  não havia restrição nenhuma. `localhost` e `127.0.0.1` seguem aceitos, para
  não quebrar a verificação de saúde do container.
- O evento de webhook de relatório informa formato, nome do arquivo, empresa e
  período — nunca os valores da escrituração, que não devem sair para endpoint
  de terceiro.

### Removido
- `SPED_HUB_DEBUG`, que era lida e não mudava comportamento de nada. Estava
  documentada como reservada; uma opção que não faz nada não deveria existir.
  Com isso, a única variável reservada que resta é `SPED_HUB_SECRET_KEY`.

## [0.17.0] — 2026-07-29

### Adicionado
- A importação passou a recusar ECD com hierarquia de plano de contas em
  ciclo (uma conta que é a própria sintética, ou A→B→A), com o caminho do
  ciclo na mensagem de erro. Antes o arquivo entrava no banco com a
  hierarquia inválida e o problema só aparecia se alguém rodasse a
  validação. Nada da importação recusada é gravado (ADR 0006).
- O dashboard web adotou a identidade "Tinta & Latão" dos relatórios:
  navbar e botões em verde-tinta, destaques em latão, títulos em Source
  Serif 4 sobre corpo em Source Sans 3. As fontes são servidas pela própria
  aplicação — nenhuma requisição externa — e os gráficos passaram a usar a
  paleta da identidade.
- Documento de módulo para os 16 módulos que faltavam — os 24 agora têm
  (`docs/modules/`): o que cada um faz, expõe, armadilhas conhecidas e o que
  explicitamente não faz. O levantamento expôs promessas falsas na própria
  documentação interna, registradas em `docs/status.md`: quatro variáveis de
  ambiente documentadas que nenhum componente lê (`EMAIL_ENABLED`,
  `SPED_HUB_WEBHOOK_TIMEOUT`, `SPED_HUB_WEBHOOK_DEFAULT_MAX_RETRIES`,
  `SPED_HUB_MONITORING_RETENTION_HOURS` — agora marcadas como reservadas),
  um `AuditMiddleware` citado em docstring que não existe, e webhooks que
  nunca disparam: nenhum ponto do código emite os eventos documentados.
- A validação de integridade passou a detectar ciclo na hierarquia do plano
  de contas (`sped-hub validar` e endpoints de validação). Era a origem do
  travamento do dashboard: o arquivo entrava no banco com hierarquia
  inválida e ninguém era avisado. Ciclo é reportado como erro, com o
  caminho completo (`1 → 2 → 1`).
- `REGRAS-DO-PROJETO.md`: as regras de trabalho do repositório. Cada regra
  diz quem a cobra — o pipeline ou a revisão de PR — e cita o defeito real
  que a motivou.
- Documento por módulo para `settings`, `db`, `ecd_importer`, `uploads`,
  `ratelimit` e `logging_config`: o que fazem, o que expõem, as armadilhas
  conhecidas e o que explicitamente **não** fazem.
- Documento de arquitetura da importação de ECD, do upload ao banco, com as
  garantias que valem nos três caminhos de entrada.
- Índice de documentação no README, com ordem de leitura para quem chega
  agora.

### Alterado
- Os testes de navegador voltaram a passar (10 de 10) e a suíte caiu de
  2 min para 14 s. Continuam fora da execução padrão do `pytest`.
- `continuidade.MD` virou um índice. Ele acumulava estado, histórico,
  pendências e próximos passos no mesmo arquivo, e já divergia do código:
  trazia uma contagem fixa de testes e marcava fase concluída sem apontar o
  teste que provava. Cada parte passou para o documento com dono.
- `SPED_HUB_DEBUG` passa a estar documentada como reservada. Ela era lida e
  nenhum componente mudava de comportamento por causa dela — quem a
  configurasse acreditaria ter ligado algo.

### Corrigido
- O repositório carregava 29 MB de fontes órfãs (o zip inteiro do download
  da Inter e as variáveis que só o PDF usava); saíram na troca de
  identidade.
- **O dashboard travava o servidor inteiro** quando a ECD importada tinha
  hierarquia de plano de contas em ciclo (uma conta que é a própria
  sintética, ou A→B→A). Como o servidor atende num único fluxo, ele parava
  de responder para todos os usuários do escritório, e só voltava com
  reinício manual. A hierarquia vem do arquivo do cliente.
- Depois de importar uma escrituração, o usuário via o JSON cru da resposta
  na tela em vez da mensagem de sucesso.
- A imagem Docker voltou a construir. O `python:3.11-slim` migrou para
  Debian trixie, onde o pacote `libgdk-pixbuf2.0-0` deixou de existir com
  esse nome — a construção falhava por completo.
- O build da imagem passa a ser verificado em cada PR, e não só depois do
  merge. Era por isso que a quebra acima chegou ao `main` sem aviso.
- O README informava 17 modelos de banco; são 24.

## [0.16.1] — 2026-07-29

### Alterado
- htmx, Alpine, Chart.js e SortableJS passam a ser servidos pela própria
  aplicação. Antes vinham de CDN, e sem acesso a ele a interface degradava
  em silêncio: os formulários caíam para envio nativo.
- Todas as páginas passam a usar a mesma versão de cada biblioteca. Antes,
  quatro páginas usavam faixas abertas (`@3`, `@4`) e recebiam versões
  diferentes das do dashboard, com mudança automática a cada release.

### Corrigido
- Requisições a `/static/` respondiam 404: o `nginx.conf` apontava para um
  diretório que nunca existiu e a aplicação não servia estáticos.
- O rodapé exibia "0.14.0" como versão quando a variável não chegava ao
  template.

### Segurança
- A política de segurança do navegador (CSP) deixou de liberar domínio
  externo para scripts.

## [0.16.0] — 2026-07-29

### Adicionado
- Suporte a PostgreSQL validado de ponta a ponta, com as 24 tabelas e toda
  a camada de relatórios exercitadas contra um servidor real.
- Migrações de schema versionadas: `sped-hub migrar status|aplicar|adotar`.
  Instalações anteriores adotam o controle de versão sem recriar nada.
- Cancelamento de importação em andamento pela API, revertendo tudo o que
  havia sido lido.
- Limite de requisições por endereço de origem, com cota própria e mais
  restrita para login e registro.
- Registro de log em formato JSON, opcional, para coletores.

### Alterado
- Importação de ECD ficou mais que duas vezes mais rápida: um arquivo de
  8,6 MB caiu de 59 s para 27 s, com uso de memória constante em relação ao
  tamanho do arquivo.
- Toda a aplicação passa a ler configuração de um ponto único. `DATABASE_URL`
  agora vale para o dashboard, a API e os workers — antes valia só em parte
  do código.

### Corrigido
- `SPED_HUB_DB_ECHO=false` ligava o registro de SQL em vez de desligá-lo.
- Caminho absoluto de banco SQLite era convertido em caminho relativo,
  apontando para outro arquivo.
- Busca por histórico de lançamento não encontrava nada em PostgreSQL.
- Cabeçalho `User-Agent` acima de 512 caracteres derrubava o login inteiro
  em PostgreSQL.
- Upload não verificava o conteúdo do arquivo, só a extensão.
- Uploads em Docker iam para um diretório não compartilhado, e a importação
  em segundo plano nunca encontrava o arquivo enviado.
- Verificação de saúde do container consultava rota que exige autenticação,
  marcando o container como não saudável para sempre.

### Segurança
- Comparação de senha passou a ser em tempo constante.
- Dados pessoais (e-mail, CNPJ, CPF, tokens) são mascarados nos logs.
- Login e registro enviam por POST mesmo sem JavaScript. Antes, sem script,
  a senha ia na URL.
- Cabeçalhos de segurança no nginx, incluindo política de conteúdo.
