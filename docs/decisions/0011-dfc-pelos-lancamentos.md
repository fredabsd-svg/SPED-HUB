# ADR 0011 — A DFC sai dos lançamentos, pelos métodos direto e indireto

## Contexto

Até a fase 85 a DFC era calculada pela **variação dos saldos** (I155): cada
conta patrimonial contribuía com menos a variação do saldo, classificada pelo
nome. Uma ECD real de 2025 mostrou os limites desse cálculo:

- a PECLD e os clientes, pendurados por engano em APLICAÇÕES FINANCEIRAS no
  I050, eram tratados como caixa;
- o PIX entre dois bancos da própria empresa passava por uma conta de
  passagem "TRANSFERÊNCIAS ENTRE CONTAS" no passivo; qualquer saldo nela
  virava fluxo de financiamento;
- o veículo comprado com financiamento aparecia como saída de investimento e
  entrada de financiamento, sem um centavo ter passado pelo banco;
- o método direto — que o CPC 03 (R2) encoraja (item 19) — era impossível:
  a variação do saldo de clientes não diz quanto foi recebido.

O contador pediu as duas coisas explicitamente: "transferências de contas da
mesma titularidade não contam" e "só entra o que passou pelo banco e caixa",
com a DFC pelos dois métodos.

## Decisão

A DFC é calculada **lançamento a lançamento** (I200/I250):

1. Caixa e equivalentes são as contas que o `Classificador` indica (plano
   referencial 1.01.01, nome da conta e do grupo, mapeamento "caixa" da
   empresa), mais as contas de passagem entre contas da empresa, tratadas
   como numerário em trânsito.
2. Lançamento que só movimenta caixa e equivalentes é transferência interna
   e não entra (CPC 03, item 9); lançamento sem caixa não entra no direto
   (item 43).
3. O caixa que um lançamento movimentou é repartido entre as contrapartidas
   **do lado oposto**, na proporção de cada uma. Recebimento de 90 com
   desconto de 10 é recebimento de clientes de 90, não de 100 com 10 de
   juros pagos.
4. O método indireto parte do lucro e o ajusta pelo que não teve caixa, com
   as mesmas repartições: a parte operacional de um lançamento que casa com
   investimento ou financiamento sem passar pelo caixa é ajuste
   (depreciação, juros apropriados); a variação do capital de giro vem dos
   lançamentos, líquida dessas partes. Por construção, o caixa das operações
   é o mesmo pelos dois métodos.
5. Investimento e financiamento são os mesmos nos dois métodos, em
   recebimentos e pagamentos brutos (item 21). Juros pagos e recebidos e
   IR/CSLL ficam nas operacionais (itens 34A e 35).
6. O total é conciliado com a variação de caixa e equivalentes nos saldos
   (item 45), e a diferença, quando há, aparece.

ECD sem lançamentos (livro de balancetes) cai no cálculo antigo pela
variação dos saldos, com a ressalva no relatório; o método direto, nesse
caso, não sai.

## Alternativas descartadas

**Manter a variação dos saldos e acrescentar exceções.** Excluir a conta de
passagem, reclassificar a PECLD e descontar o veículo financiado seriam
remendos por nome de conta, e o próximo plano teria outro nome. A variação
do saldo não separa compra de venda nem caixa de não caixa — a informação
simplesmente não está nela.

**Método direto por histórico ("RECEBIMENTO", "PAGAMENTO").** O histórico é
texto livre e costuma vir vazio ou genérico nas partidas. A contrapartida é
dado estruturado; o histórico não.

**Atribuir a cada contrapartida o valor dela, sem repartir.** Mais simples,
mas inventa fluxo: o desconto concedido sairia como juros pagos e o
recebimento como 100, quando entraram 90.

## Consequências

- A DFC concilia com o caixa numa ECD real: na de 2025, mais de mil
  transferências internas (PIX entre contas da empresa) ficaram fora, e a
  variação bateu com o disponível publicado ao centavo.
- O relatório diz o critério: quantas transferências internas e quantos
  lançamentos sem caixa ficaram de fora, e a composição de caixa e
  equivalentes, conta a conta.
- A DFC agora depende dos lançamentos. ECD de 500 MB leva o tempo de ler as
  partidas uma vez (uma consulta, agrupada em memória por lançamento).
- Uma amostra de teste com lançamentos que não explicam o I155 deixa de
  "conciliar por acaso": a diferença entre lançamentos e saldos aparece na
  conciliação, e o lucro dos lançamentos diferente do I355 gera aviso.
- Mapeamentos "dfc" antigos (categorias de variação de saldo) continuam
  valendo, traduzidos para as categorias de fluxo.
