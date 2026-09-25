# ADR 0012 — O balanço publicado prevalece sobre o I050 no superior da conta

## Contexto

O I050 declara, para cada conta, a sintética a que ela pertence
(`COD_CTA_SUP`). O PVA confere que o superior existe, não que ele faz
sentido. Uma ECD real de 2025, validada e transmitida, pendurava:

- CLIENTES A RECEBER e a PECLD em APLICAÇÕES FINANCEIRAS DE LIQUIDEZ
  IMEDIATA (dentro do DISPONÍVEL);
- INVESTIMENTOS dentro de EMPRÉSTIMOS MÚTUOS, no realizável a longo prazo.

Seguido à risca, esse plano põe todo o saldo de clientes no disponível. O
balanço sai com o total certo e os grupos errados — e daí erram juntos a
liquidez imediata e o realizável a longo prazo dos índices de licitação e a
DFC.

O balanço publicado da mesma ECD (J100), montado conta a conta pelo I052, tem
a estrutura certa: é o documento que a empresa assinou.

## Decisão

Os relatórios (`FilterEngine.hierarquia`) usam o superior do J100 quando,
e só quando, não há o que interpretar:

- a conta é **analítica** e o I052 a aglutina **no próprio código**;
- o J100 tem linha de **detalhe** com esse código, cujo superior é uma
  **sintética do próprio plano**, da **mesma natureza**;
- os J100 da ECD não discordam entre si sobre essa conta.

Cada correção vira **alerta** na validação (`superior_divergente_do_publicado`)
e marca no relatório do plano de contas, que continua mostrando o I050 como
foi declarado. O banco guarda o I050 intacto; a correção é feita na leitura.

## Alternativas descartadas

**Seguir o I050 sempre.** É o que o arquivo diz, mas produz um balanço que a
própria empresa não publicou e índices errados num documento de licitação.

**Corrigir na importação, gravando o superior do J100.** Apagaria o que o
arquivo declara: o relatório do plano de contas deixaria de mostrar o erro
que precisa ser corrigido na origem, e a ECD reimportada depois de
corrigida não teria como ser comparada.

**Inferir o superior pelo código da conta ("1.1.20.200.1" começa com
"1.1.2").** Os planos numeram de mil formas; a estrutura do código não é
garantia de nada. O J100 é declaração da empresa, o prefixo não.

**Usar o plano referencial (I051).** O referencial diz o grupo da RFB, não a
sintética do plano da empresa — e a mesma ECD mapeava empréstimo de longo
prazo para um código de circulante.

## Consequências

- Balanço, DFC, índices e painel da ECD real batem com o J100.
- O balancete também passa a agrupar a conta sob a sintética do J100; o
  alerta da validação explica por quê.
- Sem J100 com aglutinação conta a conta — o caso de quem publica o balanço
  por grupos —, nada muda: o I050 vale como está. Os classificadores do
  `src/reports/classificacao.py` ainda olham o nome da própria conta primeiro
  (CLIENTES, PERDAS…), o que protege a DFC mesmo sem a correção.
