# escrituracoes

## O que faz

Gera arquivos SPED a partir dos documentos importados. É o caminho inverso de
`parsers`, que **lê** arquivos prontos.

Existem dois geradores:

- **EFD ICMS/IPI** — blocos 0, C, E e 9;
- **EFD-Contribuições** — blocos 0, C, M e 9.

Os dois montam o arquivo a partir da camada efetiva — o normalizado mais os
ajustes. O que o operador corrigiu na tela é o que vai para o Fisco, e o XML
original continua intocado para conferência.

Antes de gerar, `espelhos.py` mostra o arquivo em forma de leitura: quais
documentos entraram, quanto deu a apuração, e se o arquivo é coerente consigo
mesmo — as mesmas conferências que o validador do Fisco faz. O espelho é lido
dos **registros**, não do banco; ver as armadilhas.

Gerado o arquivo, `arquivadas.py` guarda o que saiu: é a **terceira camada**,
ao lado do documento original e do tratamento fiscal. O conteúdo é gravado,
não reconstruído — regerar responde "o que eu enviaria hoje", e a pergunta da
intimação é "o que você enviou".

O que os dois têm em comum vive em `base.py`: formatação do leiaute, estrutura
de registro e as contagens do bloco 9. Essa última é a razão principal de a
base existir — é onde gerador próprio erra, e acertá-la numa escrituração e
errá-la na seguinte seria o resultado natural de duplicar o código.

`leiaute.py` guarda os campos de cada registro na ordem oficial, e `_add`
confere cada linha escrita contra essa lista. É estrutural, não teste: campo
esquecido no meio de um registro desloca todos os seguintes e produz um arquivo
que parece certo — o `C100` saiu sem o `IND_FRT` até que essa conferência
existisse.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `CADASTRO_FISCAL` / `EXIGIDOS` | Os cinco campos do cadastro, com a tabela de cada um, e o que cada obrigação exige. |
| `campos(empresa)` | O cadastro campo a campo, com a tabela — para a tela montar o formulário. |
| `pendencias(empresa)` | Por obrigação, o que ainda impede a geração. Obrigação pronta entra com lista vazia. |
| `validar(campo, valor)` / `preencher(empresa, informados)` | Recusam valor fora da tabela; `preencher` confere tudo antes de atribuir qualquer coisa. |
| `GeradorEFDICMS(session, empresa=, data_inicio=, data_fim=, cod_fin=)` | Monta a EFD ICMS/IPI do período. |
| `GeradorEFDContribuicoes(session, empresa=, data_inicio=, data_fim=, tipo_escrituracao=)` | Monta a EFD-Contribuições do período. |
| `.gerar()` | Devolve `ResultadoGeracao`; levanta `CampoObrigatorioAusente`. |
| `ResultadoGeracao.texto()` | O arquivo, com CRLF. |
| `ResultadoGeracao.avisos` | O que a apuração não cobre — para ser lido antes de transmitir. |
| `ResultadoGeracao.contagem_por_tipo()` | Quantos registros de cada tipo. |
| `GeradorBase` | O que os geradores compartilham; base de um gerador novo. Precisa declarar `LEIAUTE`. |
| `EFD_ICMS` / `EFD_CONTRIBUICOES` | Os campos de cada registro, na ordem do leiaute. |
| `conferir(leiaute, tipo, campos)` | Levanta `RegistroForaDoLeiaute` ou `CamposEmDesacordo`. |
| `MODALIDADES_DE_FRETE` | Os códigos válidos de `modFrete` / `IND_FRT`. |
| `Registro` | Uma linha, com os campos ainda em lista. |
| `formatar_valor` / `formatar_data` | Vírgula decimal e `ddmmaaaa`. |
| `CampoObrigatorioAusente` | Falta cadastro sem o qual o arquivo sairia errado. |
| `COD_VER` | Versão do leiaute da EFD ICMS/IPI declarada no 0000. |
| `REGIMES` | Os valores válidos de `cod_inc_trib` (registro 0110). |
| `CST_ENTRADA_COM_CREDITO` / `CST_ENTRADA_SEM_CREDITO` | Quais aquisições geram crédito (tabela 4.3.4). |
| `CST_SAIDA_TRIBUTADA` / `CST_SAIDA_SEM_DEBITO` | Quais saídas geram contribuição (tabela 4.3.3). |
| `ATIVIDADES_CONTRIBUICOES` | Os valores válidos de `ind_ativ_contribuicoes` (IND_ATIV do 0000). |
| `ATIVIDADES_ICMS` | Os valores válidos de `ind_ativ` — tabela **diferente** da de cima. |
| `PERFIS` | Os valores válidos de `ind_perfil` (IND_PERFIL do 0000). |
| `NATUREZAS_PJ` | Os valores válidos de `ind_nat_pj` (IND_NAT_PJ do 0000). |
| `espelho(resultado, tipo=)` | O arquivo em forma de leitura, antes de gerar; levanta `TipoSemLeiaute`. |
| `Espelho.texto()` / `.divergencias()` | O espelho legível; as conferências que falharam. |
| `Conferencia` / `LinhaDocumento` | Uma conferência com `ok` e detalhe; um documento do arquivo. |
| `arquivar(session, resultado=, empresa=, tipo=, data_inicio=, data_fim=)` | Guarda o arquivo que saiu, com os documentos que entraram nele. |
| `marcar_transmitida(session, escrituracao, recibo=, quando=, usuario_id=, forcar=)` | Diz qual geração foi entregue; levanta `TransmissaoInvalida`. |
| `criar_ajuste(session, empresa=, data_inicio=, data_fim=, cod_aj=, valor=)` | Cadastra um ajuste da apuração; levanta `AjusteInvalido`. |
| `ajustes_do_periodo(session, empresa_id=, data_inicio=, data_fim=)` | Os ajustes daquele período exato. |
| `totais_por_campo(ajustes)` | Quanto cada campo do E110 recebe. |
| `utilizacao(cod_aj)` / `validar_codigo(cod_aj, uf=)` | Como se lê o código e onde ele entra. |
| `UTILIZACOES` / `APURACOES` | A 4ª e a 3ª posição do código da tabela 5.1.1. |
| `campo_do_registro(escrituracao, tipo, nome)` | Um campo, pelo nome, do arquivo que foi guardado. |
| `ultima_transmitida_antes(session, empresa_id=, tipo=, data=)` | A última entrega que termina antes da data. |
| `existe_geracao_antes(session, empresa_id=, tipo=, data=)` | Se há qualquer geração anterior, entregue ou não. |
| `transmitidas_do_periodo(session, escrituracao)` | As já entregues do mesmo período, empresa e obrigação. |
| `comparar(escrituracao, resultado)` | O que mudou entre o arquivado e uma geração nova. |
| `escrituracoes_do_documento(session, documento)` | Em que arquivos esta nota entrou. |
| `avisos_de(escrituracao)` | Os avisos como estavam na hora de gerar. |
| `hash_do_conteudo(texto)` | SHA-256 do arquivo como sai, com CRLF. |
| `TIPOS` | As obrigações que podem ser arquivadas. |
| `TipoDesconhecido` | Tipo fora de `TIPOS` — arquivar assim tornaria o arquivo inencontrável. |

## O que não faz

Muito, e é preciso saber antes de usar.

**Na EFD ICMS/IPI:**

- **inventário (bloco H), ativo imobilizado (bloco G) e o bloco 1 inteiro**;
- **documentos de serviço, energia, comunicação e transporte** — C500, D100 e
  vizinhos. Só o C100 (mercadorias) está coberto;
- **ajustes que nascem de um documento** (`C197`/`D197`), que compõem os campos
  `VL_TOT_AJ_*` do E110. Os ajustes do período, esses o `E111` cobre;
- **substituição tributária apurada** (E200 e seguintes).

**Na EFD-Contribuições:**

- **bloco A (serviços/NFS-e)** — a Central ainda não importa NFS-e;
- **blocos D (transporte), F (demais operações) e I (financeiras)**;
- **créditos extemporâneos, ajustes e o bloco 1 inteiro**;
- **bases próprias do monofásico e da alíquota por unidade** — o CST já decide
  se o valor destacado entra na apuração, mas a apuração usa o valor
  destacado, não uma base calculada;
- **retenções na fonte**.

A apuração da EFD ICMS/IPI soma os documentos, carrega o saldo credor da
escrituração transmitida do período anterior e aplica os ajustes cadastrados
(E111). A da EFD-Contribuições é soma direta, respeitando o CST de cada item.
Em ambas, o `ResultadoGeracao` traz aviso explícito do que não cobre.

## Depende de / quem depende

Depende de `db.models`, de `documentos.ajustes` (a camada efetiva) e de
`reports.base` (a formatação de moeda do espelho); da stdlib, `decimal`,
`collections`, `difflib`, `hashlib` e `json`. Quem depende: `cli_fiscal`, que é
a porta de entrada humana de tudo isto.

## Decisões não óbvias e armadilhas

- **`leiaute.py` declara contra qual versão foi conferido (§8.1).** É cópia
  de documento de terceiro, e sem a versão ninguém sabe se os registros
  descrevem o leiaute que o arquivo diz declarar. Os registros que o gerador
  emite foram conferidos campo a campo contra a NT 2025.001 (leiaute 020) —
  contagem e ordem, que é o que decide se o arquivo é aceito. Acrescentar uma
  versão em `VERSOES_DO_LEIAUTE` sem reconferir os registros derruba o CI: as
  duas coisas precisam andar juntas, porque uma diz o que o arquivo declara
  ser e a outra diz o que ele é.
- **Duas armadilhas para quem reconferir**, as duas encontradas na
  conferência de 2026-08-03: o PDF da NT **perde linhas** na extração — no
  `C100` ele salta de "15 VL_ABAT_NT" para "17 IND_FRT", e concluir pela
  ausência apagaria um campo e deslocaria os doze seguintes (a prova de que a
  posição existe é o próprio `IND_FRT` estar numerado 17); e o documento
  **repete nomes** — no `C170` os campos 27 e 29 se chamam os dois
  `ALIQ_PIS`, um "em percentual" e outro "em reais".
- **O `COD_VER` do 0000 depende do período, e estava fixo.** O validador
  confere o código contra o `DT_FIN` e recusa o arquivo inteiro quando ele não
  vale para o período — "A versão do leiaute não é válida para o período
  informado". Fixo em `018`, todo arquivo de 2025 em diante saía recusado, e
  nada acusava: o arquivo é bem-formado, e a recusa só aparece no validador do
  Fisco, depois de o fechamento estar pronto. Cada faixa de
  `VERSOES_DO_LEIAUTE` cita a Nota Técnica que a instituiu.
- **Período anterior ao leiaute mais antigo levanta, não escolhe o mais
  velho.** Devolver `018` para um arquivo de 2020 repetiria o mesmo defeito em
  menor escala: outro código que o validador recusa, escrito com a confiança
  de quem sabe.
- **A EFD ICMS/IPI não leva IBS, CBS nem IS.** É decisão do GT48 da COTEPE, e
  a consequência prática é que o `VL_DOC` do C100 deixou de ter de bater com a
  soma dos `VL_OPR` dos C190 — a validação que cobrava a igualdade foi
  desativada em 01/2026. A geração diz **quanto** de cada tributo ficou de
  fora, porque a diferença tem exatamente a cara de um defeito do gerador e é
  a primeira coisa que quem confere vai investigar.
- **O `COD_VER` da EFD-Contribuições continua `006`**, e isso foi conferido,
  não presumido: o leiaute 006 vale para períodos a partir de abril de 2021.


- **O espelho é lido dos registros, não do banco.** É a decisão que dá sentido
  ao módulo. Um espelho montado a partir dos documentos responderia "o que eu
  acredito que vai sair" — e concordaria com o banco mesmo quando o gerador
  discorda dele, escondendo exatamente o erro que ele existe para mostrar.
  Pelo mesmo motivo as conferências **recalculam a partir do arquivo**:
  perguntar ao gerador se ele somou certo é aceitar a resposta dele.
- **O espelho não arquiva, e isso não contradiz "gerar sempre arquiva".** A
  regra vale para o que pode ser transmitido. O espelho é prosa; ninguém o
  entrega por engano, e arquivá-lo encheria o histórico de linhas que ninguém
  entregou.
- **O regime que o espelho lê é o do arquivo, não o do cadastro.** São a mesma
  coisa quando tudo está certo — e quando não estão, o que vale para o Fisco é
  o que está no arquivo. No cumulativo a contribuição sai num campo diferente e
  os créditos não entram; conferir sempre pelo mesmo campo acusaria de errada
  toda empresa do lucro presumido.
- **A tolerância das conferências cresce com a quantidade de itens.** Cada
  valor do arquivo já vem arredondado ao centavo, e a soma de N itens
  arredondados pode afastar-se do total arredondado em até meio centavo por
  item. Tolerância fixa acusaria documento correto de 40 itens; frouxa demais
  engoliria erro real num de dois.
- **Campo esquecido no meio de um registro não parece erro nenhum.** O `C100`
  saía sem o `IND_FRT`, que é o campo 17, logo depois do `VL_MERC`. O arquivo
  saía bem-formado, com as barras nos lugares certos — e os doze valores
  seguintes ocupando a posição do vizinho: o frete no campo do indicador de
  frete, a base do ICMS em "outras despesas", e assim até o fim da linha. A
  suíte inteira passava, porque os testes procuravam os números na linha e
  nenhum olhava para a **posição**. Daí `leiaute.py` e a conferência dentro de
  `_add`: teste confere o que alguém lembrou de exercitar, e isto confere toda
  linha escrita. Faltavam também o `VL_ABAT_NT` do `C170` e o `DEB_ESP` do
  `E110`, os dois no fim do registro.
- **O `IND_FRT` é repasse do documento, não dedução do gerador.** O `modFrete`
  da NF-e e o `IND_FRT` do C100 têm a mesma tabela desde 01/01/2018, então o
  campo viaja sem conversão. Quando o documento não traz a modalidade e também
  não tem frete, `9` (sem frete) é o único código possível e sai calado; quando
  há frete e não se sabe quem pagou, sai `9` do mesmo jeito — o campo é
  obrigatório, e vazio só trocaria um erro por outro — mas o resultado avisa
  nomeando os documentos. Escolher `0` afirmaria que o remetente pagou, e
  afirmação errada num campo que o validador aceita é o pior desfecho: ninguém
  descobre.
- **`modFrete` fora da tabela não é repassado.** O grupo `transp` vem de quem
  emitiu a nota; não há razão para confiar nele mais do que na ausência dele.
- **Nenhuma tabela de código se chama só `ATIVIDADES`.** `IND_ATIV` existe nas
  duas escriturações com o mesmo nome e tabelas diferentes — na EFD ICMS/IPI é
  binário, na EFD-Contribuições são seis valores e o `1` quer dizer prestador
  de serviços. Um nome sem a obrigação é o convite exato para o erro, e por
  isso são `ATIVIDADES_ICMS` e `ATIVIDADES_CONTRIBUICOES`.
- **`ind_nat_pj` tem default; os outros campos de cadastro não.** `00`
  (sociedade empresária em geral) vale para a imensa maioria, então exigir a
  resposta de todo mundo travaria quem não tem o que declarar — o gerador usa
  o default e avisa. Já `ind_perfil`, `ind_ativ`, `ind_ativ_contribuicoes` e
  `cod_inc_trib` não têm palpite razoável, e por isso fazem o gerador **parar**.
- **`IND_NAT_PJ` 03, 04 e 05 exigem o registro 0035**, que identifica a SCP e
  que este gerador não escreve. Declarar uma dessas naturezas produz aviso
  dizendo isso; as demais não.
- **O CST decide se o valor destacado entra na apuração.** O CST não é
  decoração: ele diz o tratamento tributário, e o valor destacado sozinho não.
  Entrada com CST 70 a 75 não dá direito a crédito, e somá-la produz
  contribuição a MENOR — que volta como cobrança com multa, num arquivo que o
  validador aceita. Saída com CST 04 (monofásica, já paga no início da cadeia),
  06, 07, 08 ou 09 não gera débito.
- **Numa entrada, o CST que veio no XML é o do fornecedor.** O documento é
  dele. Quem escritura tem de classificar a aquisição com o CST do adquirente,
  e é para isso que existe o motor de classificação. O gerador não decide por
  ninguém: item de entrada ainda com CST de saída **soma**, como sempre fez, e
  o aviso aponta `sped-hub fiscal classificar`. É o estado de toda nota
  recém-importada, e por isso tem aviso próprio, separado do de CST indefinido.
- **CST que não decide nada (`49`, `98`, `99`, vazio) soma e avisa.** "Outras
  operações" não diz o tratamento; quem sabe é quem escriturou, e travar o mês
  por causa disso seria pior que somar e dizer.
- **Valor descartado é dito com o total.** Documento que traz contribuição
  destacada num item cujo CST diz que não há está inconsistente, e quem fecha o
  mês precisa saber antes de transmitir. Descarte de valor zero não vira aviso:
  repeti-lo em todo item monofásico afogaria os que importam.
- **No regime cumulativo não há crédito.** A empresa que apura pelo lucro
  presumido paga PIS e Cofins sobre a receita e não desconta nada das compras.
  Um gerador que somasse os créditos das entradas ali produziria contribuição a
  menor num arquivo **estruturalmente válido** — o validador aceita, porque não
  sabe o regime da empresa, e a diferença volta como cobrança com multa. Por
  isso `cod_inc_trib` é cadastro obrigatório e não tem default: não há palpite
  razoável, e o palpite errado é caro. Quando o regime é cumulativo o resultado
  traz aviso dizendo que os créditos **não** foram descontados — o silêncio
  faria parecer esquecimento.
- **O arquivo arquivado é guardado, não reconstruído.** Um sistema que regera
  sob demanda responde "o que eu enviaria hoje"; a pergunta da intimação é "o
  que você enviou". Basta um ajuste depois da entrega para as duas respostas
  divergirem — e é aí que a diferença importa. Por isso `Escrituracao.conteudo`
  guarda o texto, e a linha nunca é alterada: regerar cria outra escrituração.
- **`arquivar` não sobrescreve o período.** Duas gerações do mesmo mês são dois
  fatos, e apagar a anterior apagaria a única cópia do que saiu antes.
- **Nenhuma escrituração é marcada como transmitida sozinha.** O sistema não
  transmite — quem transmite é o programa validador da Receita —, então a
  informação vem de fora e precisa ser dita. Deduzir pela geração mais recente
  responderia que foi entregue justamente a que se acabou de gerar para olhar.
- **Marcar não se desfaz.** Transmitir é fato do mundo, não estado do sistema;
  apagar a marca apagaria o registro de que aconteceu. Arquivo entregue errado
  se corrige com retificadora — outra escrituração, com o `0000` declarando
  finalidade `1` —, e as duas ficam, na ordem em que saíram.
- **Uma segunda entrega ORIGINAL do mesmo período é recusada.** Ou o arquivo
  devia ter sido gerado como retificadora, ou a marca anterior está errada; nos
  dois casos alguém precisa olhar. `forcar=True` passa por cima porque o caso
  legítimo existe: entrega rejeitada pelo Fisco e reenviada como original.
- **A finalidade é lida do `0000` do arquivo**, não do parâmetro de geração: o
  Fisco recebeu o arquivo. É o campo 2 nas duas escriturações — `COD_FIN` na
  EFD ICMS/IPI e `TIPO_ESCRIT` na EFD-Contribuições, mesma posição e mesmos
  valores.
- **O sistema conhece a estrutura do código de ajuste, não a tabela 5.1.1.**
  Ela é de cada Secretaria da Fazenda, muda por ato normativo e tem centenas de
  entradas; embuti-la seria embutir uma tabela errada para 26 dos 27 estados.
  A estrutura é nacional (Ato COTEPE/ICMS 09/2008) — `PRBCDDDD` —, e a **4ª
  posição decide em que campo do E110 o valor entra**. Quem informa o código
  informa junto o tratamento. O sequencial não é conferido contra nada.
- **O sinal do ajuste está no código, não no número.** Valor negativo é
  recusado: um "outros créditos" negativo seria um débito escrito de um jeito
  que o validador não entende, e a apuração sairia com o sinal trocado.
- **A dedução entra depois do saldo apurado, não dentro dele.** É a diferença
  entre o que se apurou e o que se recolhe; somá-la no saldo daria o mesmo
  total a recolher e um `VL_SLD_APURADO` errado — que é o número conferido
  contra os E111.
- **O período do ajuste casa por igualdade, não por sobreposição.** Um mês
  fechado e uma quinzena começam no mesmo dia; aproximar faria o mesmo valor
  entrar em duas apurações.
- **O saldo credor anterior vem da escrituração transmitida do período
  anterior**, lido do arquivo dela. O leiaute manda: o `VL_SLD_CREDOR_ANT` de
  um período tem de ser igual ao `VL_SLD_CREDOR_TRANSPORTAR` do anterior.
  Recalcular o mês passado hoje pode dar outro número, e o Fisco tem o
  primeiro. Só **transmitida** conta: geração que ninguém entregou não vale
  nada perante o Fisco, e é a que sobra em maior número.
- **Só período contíguo carrega saldo.** Se a última entrega termina antes da
  véspera, há um mês sem entregar no meio e aquele saldo já foi consumido;
  carregá-lo produziria imposto a MENOS com aparência de conta certa. Nesse
  caso sai zerado, com aviso que nomeia as duas datas.
- **Mês sem nota mas com saldo credor ainda emite o E110.** É essa linha que
  transporta o crédito; sem ela, o mês seguinte procura o
  `VL_SLD_CREDOR_TRANSPORTAR` do anterior, não acha, e o saldo acumulado
  evapora sem que ninguém veja.
- **A comparação conta multiconjunto, não conjunto.** Linha repetida é o normal
  num arquivo SPED: o mesmo produto com os mesmos valores em dois documentos
  gera dois C170 idênticos. Perguntar "esta linha continua no arquivo?"
  responderia que sim quando uma das duas mudou, e o resumo diria que o C170
  está intacto justamente quando não está.
- **O hash é do texto com CRLF.** Normalizar antes de somar daria o mesmo hash
  para dois arquivos que o validador do Fisco trata de forma diferente — e o
  hash existe justamente para conferir contra o arquivo entregue.
- **As contagens do bloco 9 se contam.** O `9900` conta os registros do próprio
  bloco 9, inclusive os `9900` que ainda vão ser escritos, o `9990` e o `9999`;
  o `X990` de cada bloco conta a si mesmo; o `9999` conta a própria linha. É o
  erro mais comum de gerador próprio, e o validador recusa o arquivo inteiro
  sem apontar a linha. Há teste que confere cada `9900` contra o que está no
  arquivo, não contra o gerador — nos dois geradores.
- **O C190 sai dos mesmos valores que alimentaram os C170.** O validador
  confere o consolidado contra a soma dos itens; uma segunda leitura poderia
  divergir da primeira.
- **Zero vira campo vazio.** O leiaute trata ausente e zero como a mesma coisa
  na maioria dos campos, e `0,00` onde se espera vazio gera advertência.
- **Arredondamento é meio para cima, não para o par.** O padrão do
  `Decimal.quantize` — e do `round` do Python — arredondaria 2,665 para 2,66.
  Note que 2,675 **não** discrimina os dois modos: nos dois dá 2,68.
- **O arquivo usa CRLF.** Alguns validadores recusam o arquivo inteiro com LF,
  sem dizer por quê.
- **`ind_perfil` e `ind_ativ` são cadastro, não default.** O validador aceita um
  enquadramento errado, porque não tem como saber qual é o certo — o erro só
  aparece em intimação. O gerador recusa gerar sem eles.
- **`IND_ATIV` tem o mesmo nome nas duas escriturações e tabelas diferentes.**
  Na EFD ICMS/IPI a resposta é binária: 0 = industrial ou equiparado, 1 =
  outros. Na EFD-Contribuições são seis valores — 0 industrial, **1 prestador
  de serviços**, 2 comércio, 3 PJ dos §§ 6º, 8º e 9º do art. 3º da Lei
  9.718/98, 4 imobiliária, 9 outros. Copiar a resposta de uma para a outra
  declararia prestadora de serviços toda empresa de comércio que respondeu
  "1 = outros" pensando na EFD ICMS/IPI, e o validador aceitaria. Por isso são
  duas colunas: `ind_ativ` e `ind_ativ_contribuicoes`, cada uma com sua tabela,
  sem conversão automática entre elas.
- **`IND_NAT_PJ` sai fixo como `00`** (sociedade empresária em geral).
  Cooperativa (`01`) e entidade que apura o PIS/Pasep sobre a folha de salários
  (`02`) precisam de correção à mão — o resultado avisa em toda geração.
- **Participantes, unidades e itens são derivados dos documentos.** Já estão
  dentro das notas; pedir recadastro seria pedir para divergir. Quando o mesmo
  código aparece com descrições diferentes, prevalece a **primeira** ocorrência
  — sem essa regra a escolha dependeria da ordem do banco, e o mesmo período
  geraria arquivos diferentes.

## Como testar isoladamente

```bash
pytest tests/test_gerador_efd_icms.py tests/test_gerador_efd_contribuicoes.py \
       tests/test_escrituracao_arquivada.py -q
```
