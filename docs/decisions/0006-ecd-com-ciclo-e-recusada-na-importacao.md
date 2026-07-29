# ADR 0006 — ECD com hierarquia cíclica é recusada na importação

## Contexto

A hierarquia do plano de contas (`COD_CTA → COD_CTA_SUP`) vem do arquivo do
cliente e precisa ser uma árvore. Uma ECD em que uma conta é a própria
sintética — ou A→B→A — já travou o dashboard inteiro para todos os usuários
(laço infinito, corrigido no PR #7) e ganhou detecção na validação de
integridade (validação h, PR #8).

Mas a validação só roda quando alguém pede. O arquivo continuava **entrando
no banco** com a hierarquia inválida: relatórios agrupavam errado ou não
agrupavam, e o defeito só aparecia se o operador rodasse `sped-hub validar`.

## Decisão

A importação recusa o arquivo. `ECDImportService.importar` verifica a
hierarquia antes do commit e levanta `ECDImportError` com o caminho do ciclo
(`1 → 2 → 1`) e a instrução de corrigir o `COD_CTA_SUP` no sistema de origem.
Pela transação única (§6.1), **nada** da importação fica: nem empresa, nem
ECD, nem plano.

A detecção é a mesma função pura da validação (h) — `encontrar_ciclos` em
`validators.integridade` — para o mesmo fato não viver em dois lugares
(§1.9). A validação (h) continua existindo, para bancos que importaram antes
desta decisão.

## Alternativas descartadas

**Aceitar e validar depois (status quo).** Deixava escrituração inválida
visível em relatórios; o aviso dependia de alguém rodar a validação.

**Sanear na entrada (quebrar o ciclo e importar).** Adivinhar qual
`COD_CTA_SUP` está errado é decisão contábil, não do importador — qualquer
escolha automática produziria agrupamento silenciosamente errado, que é pior
que recusar.

**Recusar já no upload (antes do parse completo).** Exigiria parsear o plano
duas vezes ou manter estado no fluxo de upload; a recusa antes do commit tem
o mesmo efeito prático (nada entra) com um caminho só.

## Consequências

**Mais fácil:** escrituração no banco passa a ter hierarquia acíclica por
construção; consumidores novos não precisam da trava de ciclo que o
dashboard carrega.

**Mais difícil:** cliente com arquivo defeituoso é bloqueado na entrada e
precisa corrigir no sistema de origem — antes ele "conseguia importar" e
descobria depois. A mensagem de erro carrega o caminho do ciclo exatamente
para esse conserto ser direto.

Bancos que importaram ECD cíclica antes desta decisão continuam com ela;
a validação (h) e a trava do dashboard seguem cobrindo esse legado.
