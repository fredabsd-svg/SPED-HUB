# Changelog

Formato [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Atualizado ao fim de cada fase, não só em release (§1.7).

As entradas descrevem o efeito para quem usa o sistema, não o detalhe
interno da implementação.

## [Não publicado]

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

### Alterado
- Os testes de navegador voltaram a passar (10 de 10) e a suíte caiu de
  2 min para 14 s. Continuam fora da execução padrão do `pytest`.

### Adicionado
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
- `continuidade.MD` virou um índice. Ele acumulava estado, histórico,
  pendências e próximos passos no mesmo arquivo, e já divergia do código:
  trazia uma contagem fixa de testes e marcava fase concluída sem apontar o
  teste que provava. Cada parte passou para o documento com dono.
- `SPED_HUB_DEBUG` passa a estar documentada como reservada. Ela era lida e
  nenhum componente mudava de comportamento por causa dela — quem a
  configurasse acreditaria ter ligado algo.

### Corrigido
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
