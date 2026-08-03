# ADR 0009 — ECD de outro leiaute é avisada, não recusada

## Contexto

O `ECDParser` carrega sempre o `src/layouts/ecd_v9.yml`, seja qual for a
versão que o próprio arquivo declara no campo `COD_VER_LC` do registro I010.
O importador **guardava** essa versão no banco (`ECD.cod_ver_lc`) sem nunca
compará-la com o leiaute que estava usando para ler.

O risco não é teórico. Este ADR nasce da fase 74, que conferiu o `ecd_v9.yml`
contra o Manual da Receita (Anexo ao ADE Cofis nº 01/2026) e encontrou nove
registros descritos em posições erradas — inclusive o balanço patrimonial,
cujos valores estavam quatro colunas à esquerda do lugar certo. Ler um
arquivo com o leiaute errado não dá erro: o arquivo é uma sequência de campos
separados por `|`, e qualquer descrição encaixa. O dado errado entra na
coluna certa, em silêncio, e sai depois num balancete que parece normal.

Um arquivo que declara leiaute 8 e é lido com o 9 corre exatamente esse
risco, sem que ninguém tenha como perceber.

## Decisão

A importação **avisa e segue**. `ECDImportService._avisar_leiaute_diferente`
compara o `COD_VER_LC` do arquivo com a versão do leiaute carregado e, quando
não batem, emite um aviso dizendo as duas versões e que os campos podem estar
em outras posições.

`009` e `9` são a mesma versão: a comparação ignora os zeros à esquerda, para
o aviso não virar ruído em todo arquivo normal.

O aviso **não** trava a importação. É a razão da §8.2, aplicada a outro
objeto: uma ECD antiga ainda é melhor que ECD nenhuma, e recusar a leitura de
um arquivo de 2019 tiraria uma capacidade real para evitar um risco que o
aviso já põe na frente de quem importa.

## Alternativas descartadas

**Recusar, como o ciclo do ADR 0006.** O ciclo é diferente em espécie: uma
hierarquia cíclica não tem leitura contábil possível, em nenhum leiaute, e já
travou o produto. Um leiaute anterior tem leitura — só não temos como
garantir que é a que fizemos. Recusar transformaria "pode estar errado" em
"não dá para abrir", e escritório que precisa consultar uma escrituração
antiga ficaria sem saída dentro do programa.

**Carregar o leiaute que o arquivo pede.** É o certo, e é o que se deve fazer
quando houver um segundo yml. Hoje só existe o do leiaute 9: escrever o
despacho por versão sem ter para onde despachar seria estrutura sem
conteúdo — e o `ecd_v9.yml` levou até agora para ser conferido contra o
documento oficial uma vez.

**Não fazer nada (status quo).** Era o estado até aqui. O campo era guardado
no banco e nunca lido, o que é a forma mais silenciosa de não verificar: a
informação existe, parece conferida, e não é.

## Custo

Quem importar uma ECD de leiaute anterior verá um aviso em toda importação, e
o aviso não some enquanto o arquivo não for regerado no leiaute corrente —
que muitas vezes não é possível, porque a escrituração já foi transmitida. É
um incômodo recorrente por um risco que continua existindo; a alternativa era
o silêncio.
