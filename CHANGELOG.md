# Changelog

Formato [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Atualizado ao fim de cada fase, não só em release (§1.7).

As entradas descrevem o efeito para quem usa o sistema, não o detalhe
interno da implementação.

## [Não publicado]

### Adicionado
- **Os grupos da Reforma passaram a ser lidos de onde a Nota Técnica os põe.**
  Três dias antes de a NF-e começar a rejeitar documentos sem IBS e CBS
  (03/08/2026), a NT 2025.002 v1.50 foi baixada do portal da SVRS e conferida
  campo a campo contra o leitor. O leitor procurava redução, diferimento,
  devolução, crédito presumido e monofásico como filhos diretos de `gIBSCBS`,
  e nenhum deles está ali: os três primeiros existem **uma vez dentro de cada
  destinação** do imposto, o crédito presumido é um grupo irmão, e o monofásico
  foi reformulado pela v1.50 em quatro variantes. Procurar uma tag no nó errado
  não dá erro — devolve zero. Em nota emitida como a NT manda, **todo grupo
  opcional da Reforma vinha zerado**, e a medição do que a apuração não consome
  media zero. Os testes concordavam porque a nota de teste era montada a partir
  do leitor, não da Nota Técnica.
- **O município de consumo do IBS voltou para o documento.** `cMunFGIBS` é
  campo do `ide` — nunca esteve no imposto do item — e é ele que decide para
  qual município vai a parcela municipal do IBS.
- **Diferimento, devolução e redução agora têm um valor por destinação.** Um
  item pode ter diferimento só na parcela estadual, e um total somado esconderia
  exatamente isso; percentuais, além do mais, não somam.
- **O monofásico distingue o que soma do que já foi cobrado.** A Nota Técnica
  nomeia quase igual duas coisas opostas — o imposto sobre o biocombustível a
  ser misturado (que soma ao que se recolhe) e o cobrado anteriormente —, e o
  sistema guardava o primeiro no campo do segundo.
- **Correção item a item passou a caber numa planilha.** A alteração em massa
  resolve "todos os itens com NCM 2203 viram CFOP 2102"; não resolve o caso
  mais comum do saneamento, em que cada linha tem um valor diferente e quem
  sabe qual é uma pessoa olhando — item a item na tela é inviável num mês com
  mil notas. `sped-hub fiscal planilha --empresa 1 --saida itens.xlsx` exporta
  os itens já com as correções que existem aplicadas, e `sped-hub fiscal
  planilha --arquivo itens.xlsx` traz de volta o que foi corrigido. A volta
  **não grava**: mostra o que mudaria, como toda alteração em massa, e só com
  `--confirmar` vira um lote que `desfazer` reverte inteiro. Cada linha carrega
  a identidade do item e a chave da nota é reconferida contra o banco, de modo
  que planilha de outro mês ou com os identificadores editados é recusada linha
  a linha, com o motivo, em vez de escrever no documento errado. Só as colunas
  editáveis voltam: a chave, o número da nota e a descrição vão junto porque
  sem elas ninguém sabe o que está editando, mas mudá-las ali não muda nada.
- **Benefício fiscal, crédito outorgado e estorno agora entram na apuração do
  ICMS.** A apuração era a soma dos documentos e mais nada — e empresa com
  incentivo tem valores que não estão em nota nenhuma, o que fazia o imposto
  sair a menos quando faltava um crédito e a mais quando faltava um estorno.
  `sped-hub fiscal ajuste --empresa 1 --de … --ate … --codigo TO020007 --valor
  1.234,56` cadastra, e o comando diz para onde o valor vai antes de gravar. O
  código é o da tabela do **seu estado**: o sistema confere a estrutura — a UF,
  o tipo de apuração e o tipo de ajuste — e não a lista de códigos, que muda
  por ato normativo e é diferente em cada estado. Valor negativo é recusado:
  o sinal está no código, não no número.
- **O cadastro fiscal da empresa deixou de exigir acesso ao banco.** Os campos
  que decidem o enquadramento declarado no arquivo — perfil, indicador de
  atividade (que tem tabela diferente em cada escrituração), regime de
  apuração e natureza jurídica — só podiam ser preenchidos escrevendo direto
  no banco de dados, o que na prática deixava a geração de EFD fora do alcance
  de quem não é programador. `sped-hub fiscal cadastro --empresa 1` mostra o
  que está preenchido, o que cada obrigação ainda exige e o significado de
  cada código; com os campos, preenche, recusando valor fora da tabela e
  mostrando a tabela inteira na recusa.
- **Cooperativa e entidade que apura sobre a folha podem ser declaradas.** A
  EFD-Contribuições saía sempre como "sociedade empresária em geral", e o
  validador aceita porque não tem como saber — o erro só apareceria depois. As
  seis naturezas jurídicas agora são cadastráveis; quando a empresa não
  declara, o comportamento é o de antes (o geral, com aviso). As três
  naturezas de sociedade em conta de participação avisam que o registro que
  identifica a SCP não é gerado.
- **Agora dá para dizer qual arquivo foi o entregue.** O sistema guardava todas
  as gerações do mês — a primeira, a de depois da correção, a que se fez só
  para conferir — e não sabia qual delas foi transmitida. `sped-hub fiscal
  transmitida --escrituracao 3 --recibo REC-2026-0001` registra a entrega, e
  `fiscal historico` passa a mostrar a data ao lado de cada geração
  (`--transmitidas` filtra só as entregues). Nenhuma é marcada sozinha: quem
  transmite é o programa da Receita, e adivinhar pela mais recente diria que
  foi entregue justamente a que se acabou de gerar para olhar. Marcar **não se
  desfaz** — arquivo entregue errado se corrige com retificadora, e as duas
  ficam registradas na ordem em que saíram. Tentar marcar uma segunda entrega
  *original* do mesmo período é recusado, porque ou o arquivo devia ser
  retificadora ou a marca anterior está errada; `--forcar` passa por cima para
  o caso de entrega rejeitada pelo Fisco e reenviada.
- **`sped-hub fiscal espelho` mostra o arquivo em português antes de
  transmitir.** Um arquivo SPED é ilegível — `|C100|0|1|55|00|...` —, e até
  agora só dava para saber se ele estava certo depois de entregue. O espelho
  lista os documentos que entraram, o total de entradas e saídas, quanto deu a
  apuração, e roda as mesmas conferências que o Fisco faz: a soma dos itens
  contra o total de cada nota, o consolidado contra os itens, a apuração
  contra os documentos e as contagens do bloco 9. O que não bater aparece com
  o número do documento e os dois valores lado a lado. O comando **não** gera
  nem registra escrituração — é leitura — e sai com código 2 quando alguma
  conferência falha, para caber em rotina de fechamento.
- **A apuração da Reforma passou a dizer o que fica de fora, com valor.** Ela
  avisava, em toda apuração, que não cobre monofásico, diferimento, crédito
  presumido e devolução de tributo — a mesma frase para quem tem esses valores
  e para quem não tem, o que ensina a ignorar avisos. Agora esses valores são
  somados e contados a partir dos próprios documentos e aparecem numa seção
  "fora do total", com o número e em quantos itens. Quando não há nenhum, não
  há aviso. Os códigos de situação tributária do IBS/CBS diferentes de
  tributação integral também passam a ser listados — listados, e não
  interpretados: a nota técnica que define a tabela ainda está em revisão, e o
  valor destacado continua sendo somado como antes.
- **A apuração dos tributos da Reforma já funciona.** CBS, IBS e Imposto
  Seletivo são somados por período, a partir dos documentos importados. Os
  grupos passam a ser exigidos na NF-e em 03/08/2026, e até aqui os valores
  entravam no sistema sem que nada os consumisse. Três pontos que o resultado
  deixa explícito: o **Imposto Seletivo não gera crédito** (o que veio na
  compra é custo); as parcelas **estadual e municipal do IBS** são apuradas em
  separado, porque vão para entes diferentes e uma não abate a outra; e o
  total de **2026 não é o valor a recolher** — é ano de teste, com compensação
  e dispensa que o sistema não calcula. Nota antiga, sem os grupos novos, não
  quebra a apuração: os dois regimes convivem até 2033. `sped-hub fiscal
  apurar --empresa 1 --de 2026-07-01 --ate 2026-07-31` mostra o quadro, com os
  avisos junto — o número sozinho engana.
- **`sped-hub fiscal`: dá para importar XML de nota, gerar a EFD e conferir o
  que foi entregue.** A Central de Documentos, os geradores da EFD ICMS/IPI e
  da EFD-Contribuições e o registro do que foi enviado já existiam por dentro,
  e não havia como chegar até eles. Agora a sequência inteira roda pelo
  terminal: `fiscal importar` recebe uma pasta de XML (varre sozinho e ignora
  o que não é nota), `fiscal documentos` mostra o que entrou, `fiscal gerar`
  produz o arquivo, e `fiscal historico` lista o que já foi gerado.
- **`sped-hub fiscal conferir` responde se o que foi entregue ainda bate com o
  sistema.** Quando alguém corrige uma nota depois do fechamento, o arquivo
  que já foi transmitido deixa de corresponder ao que o sistema tem — e não
  havia como perceber isso. O comando mostra o que mudou, por tipo de
  registro, e sai com código 2 quando divergiu, para caber em rotina
  automática. O arquivo entregue continua guardado como saiu, intocado.
- Gerar **sempre** registra a escrituração; não há como produzir um arquivo
  sem deixar rastro do que saiu. Gerar de novo cria um registro novo, sem
  apagar o anterior.
- **`sped-hub fiscal regras` cadastra as regras de classificação sem precisar
  de programador.** `--se ncm:comeca_com:2203 --entao cfop:2102` guarda o que
  antes só existia na cabeça de alguém. A listagem mostra as condições e as
  ações de cada regra, para conferência. Remover **desativa** em vez de
  apagar: os ajustes que a regra já gerou guardam o nome dela, e quem for
  auditar o mês precisa poder ver qual era a condição.
- **`sped-hub fiscal classificar` mostra o que as regras propõem, sem aplicar
  nada.** A lista traz o valor de agora, o sugerido e qual regra propôs, para
  ser conferida antes. Só grava com `--aplicar`, e num lote que pode ser
  desfeito inteiro. Regras de mesma prioridade disputando o mesmo campo
  aparecem como conflito, em vez de o sistema escolher uma por conta própria.
- **`sped-hub fiscal alterar` corrige vários documentos de uma vez, mostrando
  antes o que mudaria.** Quantos documentos, quantos itens e quanto muda em
  reais; só grava com `--confirmar`. Dá para recortar por período e por
  qualquer campo (`--filtro ncm:comeca_com:2203`), e preencher só o que está
  vazio sem tocar no que já tem valor.
- **`sped-hub fiscal desfazer --lote` reverte um lote inteiro**, seja da
  classificação ou de uma alteração em massa. O documento volta ao que era: o
  que veio no XML nunca foi alterado.

### Corrigido
- **A apuração do PIS/Cofins ignorava o CST e podia recolher a menos.** O
  sistema somava o valor destacado em cada item, qualquer que fosse o código de
  situação tributária. Compra que não dá direito a crédito — aquisição isenta,
  suspensa, a alíquota zero, por substituição — entrava como crédito se o
  fornecedor tivesse destacado o valor na nota, e o resultado era contribuição
  devida a menor, num arquivo que o validador aceita sem reclamar. Venda com
  contribuição já paga no início da cadeia (monofásica) ou sem incidência
  entrava como débito pelo mesmo motivo. Agora o código de situação decide, nos
  dois sentidos. Quando o valor destacado é descartado, a geração diz quanto e
  por quê. E como o código que vem no XML de uma compra é o do fornecedor, nota
  ainda não classificada continua entrando na conta como antes — mas com aviso
  apontando `sped-hub fiscal classificar`, que é o que resolve.
- **Empresa com saldo credor de ICMS recolhia a mais.** A apuração do mês
  ignorava o crédito acumulado do mês anterior: o E110 saía com o campo de
  saldo credor anterior vazio, e o "ICMS a recolher" — que é o número que vai
  para a guia — vinha maior do que o devido. Agora esse saldo é buscado na
  escrituração **transmitida** do período anterior e lido do próprio arquivo
  que foi entregue. Geração que ninguém marcou como entregue não estabelece
  saldo, e período com um mês sem entrega no meio também não: nos dois casos o
  valor sai zerado e a geração avisa dizendo o que fazer. Mês sem nota nenhuma
  mas com crédito acumulado passa a gerar a apuração assim mesmo, senão o
  saldo desaparecia da cadeia.
- **A EFD saía com os valores do cabeçalho da nota uma casa fora do lugar.**
  Faltava um campo no meio do registro C100 — o indicador de quem paga o frete
  — e, como os campos do SPED são posicionais, tudo o que vem depois dele
  escorregava: o valor do frete ia parar no campo do indicador, a base do ICMS
  no campo de outras despesas, e assim por diante até o fim da linha. O arquivo
  saía com aparência normal, e a recusa do validador não diz qual campo faltou.
  Faltavam também o último campo do C170 e o último do E110. Os três estão
  corrigidos, e o indicador do frete agora vem do próprio XML da nota. Nota
  importada antes desta versão não traz esse dado: quando ela tem frete, o
  arquivo sai com "sem frete" e a geração avisa, nomeando os documentos a
  corrigir — reimportar o XML resolve. Consequência esperada: escrituração
  gerada antes desta versão passa a aparecer como divergente em `sped-hub
  fiscal conferir`, e a divergência é verdadeira — o arquivo entregue estava
  mesmo com os campos fora de posição.
- **Corrigir o valor de vários itens deixava o cabeçalho da nota para trás.**
  Quem usava a alteração em massa para ajustar valores dos itens gerava um
  arquivo em que o total do documento dizia uma coisa e a soma dos itens dizia
  outra — e é exatamente isso que o validador do Fisco confere. Agora a
  simulação já mostra os totais recompostos junto com as alterações, antes de
  confirmar. O total geral da nota (vNF) continua sendo o declarado, com aviso
  na tela: ele não é soma de parcela, e recalculá-lo com os dados que o sistema
  tem daria um número errado com cara de certo.
- **Quem constrói no Windows não conseguia subir o nginx.** O Git converte a
  quebra de linha dos arquivos ao baixar o repositório, e o script que o
  container executa parava de funcionar por causa disso — com uma mensagem que
  culpava o arquivo errado (`no such file or directory`, apontando para um
  arquivo que está lá). O container ficava reiniciando sem parar. Nenhuma
  verificação automática pegava: todas rodam em Linux, onde a conversão não
  acontece.

## [0.19.0] — 2026-07-30

### Adicionado
- `sped-hub usuario criar` e `sped-hub usuario listar`, para administrar as
  contas do painel. Sem `--senha`, a senha é pedida sem eco, para não ficar no
  histórico do shell.

### Corrigido
- **Nenhuma mensagem de erro aparecia na tela.** Errar a senha no login, ou
  tentar se cadastrar com um e-mail já em uso, deixava a tela parada: nenhum
  aviso, nenhuma pista do que tinha acontecido. O código que monta o alerta
  existia e era executado — só descartava o resultado, porque o htmx não
  substitui o conteúdo quando a resposta é de erro sem que se peça
  explicitamente.
- **Um worker cujo canal de tarefas quebrasse consumia um núcleo inteiro, em
  silêncio, para sempre.** O laço tratava "fila vazia" e "fila quebrada" do
  mesmo jeito: esperar um segundo e tentar de novo. Mas numa fila quebrada o
  erro vem na hora, sem a espera — o que dava 1,2 milhão de voltas por segundo
  por worker, quatro deles no deploy padrão, sem uma linha de log. Agora o
  worker registra a falha e encerra. Ele também passou a notar o desligamento
  da fila mesmo quando o aviso de encerramento não chega até ele.
- **Aplicar migração emudecia o resto do processo.** A configuração de log do
  Alembic desativava todos os loggers da aplicação já criados, então nada mais
  era registrado depois de uma migração.
- **`docker compose up` não subia numa instalação nova.** O nginx apontava
  direto para o certificado do Let's Encrypt, que ainda não existe na primeira
  execução; ele recusava subir (`cannot load certificate`) e o container
  entrava em laço de reinício. Não havia saída pelo próprio compose: o certbot
  configurado só renova, e emitir o primeiro certificado exige o nginx já
  respondendo na porta 80. Agora o nginx gera um certificado autoassinado
  quando não encontra o real, sobe, avisa no log que é autoassinado e serve a
  aplicação em `http://localhost/` — e passa a redirecionar para HTTPS assim
  que o certificado de verdade aparece. O primeiro passo do próprio guia de
  deploy (`docker compose up -d nginx`) era o passo que falhava.
- **Depois de recriar o container da aplicação, o site respondia 502 em tudo.**
  O nginx resolvia o endereço do backend uma única vez, ao subir, e guardava o
  IP; o container recriado ganha IP novo, e o nginx seguia mandando para o
  antigo — com a aplicação saudável ao lado — até alguém reiniciá-lo na mão.
  Agora o endereço é resolvido a cada requisição.
- O domínio do certificado virou configuração (`SPED_HUB_DOMINIO`); antes era
  preciso editar o `nginx.conf` à mão.
- O aviso `the "listen ... http2" directive is deprecated` deixou de aparecer
  a cada subida.

### Segurança
- **Qualquer pessoa que alcançasse o servidor criava conta e via a
  escrituração de todos os clientes.** A tela de registro era pública e sem
  restrição, e é o único caminho que existe para criar usuário. Numa
  instalação de escritório único ninguém tem escritório — nem o contador que
  se cadastra primeiro, nem as empresas que ele importa —, então toda conta
  nova caía no mesmo grupo e enxergava tudo: as empresas, as escriturações, os
  relatórios. Como o servidor de produção fica publicado na internet com
  domínio e certificado, bastava conhecer o endereço.
  O registro segue aberto enquanto não existe nenhum usuário, para criar o
  administrador inicial, e fecha em seguida. As contas seguintes são criadas
  por quem administra o servidor, com `sped-hub usuario criar`. Quem quiser o
  auto-serviço de volta — rede interna fechada, por exemplo — liga
  `SPED_HUB_REGISTRO_ABERTO=true`.
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
