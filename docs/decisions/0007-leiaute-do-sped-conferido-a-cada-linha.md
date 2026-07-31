# ADR 0007 — O leiaute do SPED é conferido a cada linha escrita

## Contexto

O registro do SPED é posicional: `|C100|0|1|...|`. Não há nome de campo no
arquivo, só a ordem. Isso faz um erro específico ser silencioso de um jeito que
outros formatos não permitem — **esquecer um campo no meio do registro**. O
arquivo continua bem-formado, com as barras nos lugares certos, e todos os
valores depois do campo esquecido passam a ocupar a posição do vizinho.

Aconteceu aqui. O `C100` saía sem o `IND_FRT`, que é o campo 17, logo depois do
`VL_MERC`. Os doze valores seguintes estavam todos deslocados: o valor do frete
no campo do indicador do frete, a base do ICMS em "outras despesas", o ICMS em
"base do ICMS", e assim até o fim da linha. Faltavam também o `VL_ABAT_NT` do
`C170` e o `DEB_ESP` do `E110`, esses no fim do registro.

A suíte tinha 45 testes do gerador da EFD-Contribuições e outros tantos da EFD
ICMS/IPI, incluindo a conferência do bloco 9 contra o próprio arquivo. Todos
passavam. O motivo é que os testes perguntavam **"o arquivo contém 1000,00?"**,
e não **"o campo `VL_MERC` vale 1000,00?"** — e a primeira pergunta continua
respondendo que sim com tudo fora do lugar.

## Decisão

Os campos de cada registro, na ordem oficial, vivem em
`src/escrituracoes/leiaute.py`, por obrigação (`EFD_ICMS`,
`EFD_CONTRIBUICOES`). `GeradorBase._add` confere cada linha contra essa tabela
antes de escrevê-la: registro que não está no leiaute levanta
`RegistroForaDoLeiaute`, contagem diferente levanta `CamposEmDesacordo` dizendo
quais campos faltam.

`LEIAUTE` começa vazio na base, de modo que gerador que não o declare não
consegue escrever nada — o padrão é a conferência, não a ausência dela.

Onde o leiaute é de fato o mesmo nas duas escriturações (`C100`, `C170`,
`0150`, `0200`, bloco 9), a definição é **uma só**, compartilhada por
`_COMUNS`. Onde tem o mesmo nome e leiaute diferente — o `0000` — são duas
definições, pelo mesmo motivo que `ind_ativ` e `ind_ativ_contribuicoes` são
duas colunas.

## Alternativas descartadas

**Cobrir com testes.** Foi o que existia. Teste confere o que alguém lembrou de
exercitar; a conferência no `_add` cobre toda linha que qualquer gerador
escreva, hoje e depois. Os testes de posição continuam existindo
(`tests/test_leiaute_sped.py`), mas como demonstração do defeito, não como a
proteção principal.

**Gerar o registro a partir do leiaute** (o gerador entrega um dicionário
`{campo: valor}` e a base monta a linha). É mais forte: impediria também o
campo trocado de lugar, não só o ausente. Descartado por ora porque exigiria
reescrever os dois geradores inteiros no mesmo passo em que se conserta um
defeito de produção. A conferência de contagem já mata o caso que ocorreu, e o
caminho para essa alternativa continua aberto — a tabela de campos que ela
exigiria é exatamente a que passa a existir agora.

**Validar o arquivo pronto, no fim.** Só diria "o C100 tem 27 campos", sem
apontar onde no código. Conferir na escrita dá a linha do gerador.

**Deduzir o `IND_FRT`.** O campo é obrigatório e o modelo não o tinha. Deduzir
"0 — por conta do remetente" produziria arquivo aceito pelo validador com
afirmação falsa, que é o pior desfecho: ninguém descobre. O campo passou a ser
lido do `modFrete` da NF-e, que tem a mesma tabela de códigos.

## Consequências

**Mais fácil:** acrescentar registro ou campo a um gerador. O erro aparece na
hora, com o nome do registro e a contagem dos dois lados, em vez de virar um
arquivo recusado meses depois com "quantidade de campos inválida".

**Mais difícil:** implementar bloco novo passa a exigir descrever os campos
antes. É trabalho a mais, e é exatamente o trabalho que estava faltando.

**Consequência operacional:** escrituração gerada antes desta decisão fica
divergente em `sped-hub fiscal conferir` — e a divergência é verdadeira. O
arquivo entregue realmente estava com os campos fora de posição, e a terceira
camada continua guardando o que saiu, não o que sairia hoje.

Documento importado antes desta versão não tem `modalidade_frete`. Quando ele
tem frete, o `IND_FRT` sai como `9` (sem frete) e a geração avisa nomeando os
documentos: é afirmação errada, dita em voz alta, em vez de errada e calada.
Reimportar o XML resolve.
