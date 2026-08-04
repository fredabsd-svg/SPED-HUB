# Roadmap

O que **não** existe hoje. Regra §1.1: funcionalidade futura vive aqui, nunca
na documentação técnica.

O estado do que já existe está em [`status.md`](status.md).

## Como este documento é cobrado

Documento de "o que falta" apodrece na direção mais fácil de não notar: o item
é feito e ninguém volta para tirá-lo daqui. Aconteceu duas vezes — a exportação
do balancete em PDF e os testes de navegador no CI seguiram listados como
ausentes depois de existirem, com teste passando e job no pipeline.

Por isso cada item declara um **marcador de ausência**: um caminho de arquivo ou
`módulo:símbolo` que só passa a existir quando o item for feito.
`tests/test_regras_projeto.py::TestRoadmap` falha se algum marcador existir —
o pipeline avisa quando este documento fica desatualizado.

Item bloqueado por credencial, contrato ou dado de terceiro não tem marcador
possível: não é código que falta. Esses declaram `externo` seguido da razão, e a
verificação exige que a razão esteja escrita.

## Depende de decisão de produto

| Item | O que falta decidir | Marcador de ausência |
|---|---|---|
| Retomada de importação interrompida a partir de onde parou | Como representar "escrituração incompleta" sem que os relatórios a tratem como completa. Hoje a importação interrompida é encerrada e pede reenvio do arquivo | `src.ecd_importer:retomar` |

## Depende de contrato ou credencial

| Item | Bloqueio | Marcador de ausência |
|---|---|---|
| Deploy em servidor real com domínio e SSL | Credenciais e decisão operacional. O checklist está pronto em [`deploy.md`](deploy.md) | `externo` — depende de servidor, DNS e `.env` de produção do escritório |
| Integração com Domínio, Questor e Alterdata | Contrato e documentação das APIs | `externo` — depende de contrato com os fornecedores |
| Validação com ECDs reais de clientes | A importação foi exercitada com arquivos sintéticos de até 240 mil registros. Arquivos reais trazem variações de leiaute que só aparecem em campo | `externo` — depende de arquivo real de cliente, que não pode ser versionado |

## Central de Documentos Fiscais e suíte fiscal

Já existem o modelo de dados, o adaptador de NF-e/NFC-e (lendo os dois
regimes tributários), o importador em lote com deduplicação, a camada
efetiva (ajustes com reversão por lote), o motor de classificação, as
alterações em massa com simulação, os geradores da EFD ICMS/IPI e da
EFD-Contribuições, a escrituração arquivada — a terceira camada, o arquivo
que efetivamente saiu —, a apuração de CBS, IBS e IS, o espelho legível antes
de gerar, a marca de qual escrituração foi transmitida, os ajustes de apuração
(E111), a planilha de mão dupla para corrigir item a item fora da tela, as
telas que cobrem o fluxo inteiro — importar, documentos, classificar, corrigir,
gerar e cadastro fiscal — e o comando `sped-hub fiscal` que alcança tudo isso; ver [`status.md`](status.md). Sobre a Reforma
Tributária, ver [`reforma-tributaria.md`](reforma-tributaria.md).

O que falta:

| Item | Situação | Marcador de ausência |
|---|---|---|
| Importação de NFS-e por provedor | Exige um adaptador por provedor municipal | `src.documentos.adaptadores:AdaptadorNFSe` |
| Blocos G, H e 1 da EFD ICMS/IPI | O gerador cobre 0, C, E e 9 | `src.escrituracoes.efd_icms:GeradorEFDICMS.bloco_h` |
| Documentos de serviço, energia e transporte na EFD (C500, D100) | Só o C100 de mercadorias é gerado | `src.escrituracoes.efd_icms:GeradorEFDICMS.bloco_d` |
| Ajustes que nascem de um documento (C197/D197) | Os do período já entram pelo E111; os de documento compõem os campos `VL_AJ_DEBITOS` e `VL_AJ_CREDITOS` do E110 e seguem fora. O impedimento é a **tabela 5.3**, e o Guia Prático 3.2.2 diz qual é: ela é publicada **por cada UF**, não pela RFB — "este registro somente deve ser informado para as UF que publicarem a tabela constante no item 5.3" —, e a validação do campo confere o código "de acordo com a Tabela da UF do informante do arquivo". A estrutura posicional está documentada (3º caractere = tipo do ajuste, 4º = tipo de apuração); o que falta é a lista de códigos de cada estado | `externo` — depende da tabela 5.3 publicada pela UF de cada empresa atendida |
| Blocos A, D, F e I da EFD-Contribuições | O gerador cobre 0, C, M e 9. O bloco A depende da importação de NFS-e, acima | `src.escrituracoes.efd_contribuicoes:GeradorEFDContribuicoes.bloco_f` |
| Créditos extemporâneos e ajustes da EFD-Contribuições | Os blocos M são soma direta das saídas menos as entradas | `src.escrituracoes.efd_contribuicoes:GeradorEFDContribuicoes.ajustes_de_apuracao` |
| Bases próprias do monofásico e da alíquota por unidade no PIS/Cofins | O CST já decide se o valor destacado entra na apuração; o que falta é **calcular** base e alíquota próprias em vez de usar o destacado | `src.escrituracoes.efd_contribuicoes:GeradorEFDContribuicoes.regimes_especiais` |
| Registro 0035 (identificação da SCP) na EFD-Contribuições | Exigido quando `IND_NAT_PJ` é 03, 04 ou 05; o gerador avisa e não escreve | `src.escrituracoes.efd_contribuicoes:GeradorEFDContribuicoes.registro_0035` |
| Escrituração de CBS, IBS e IS em obrigação acessória | A apuração soma os tributos; nenhuma obrigação acessória os declara | `src.escrituracoes.reforma:GeradorObrigacaoIBSCBS` |
| Monofásico, diferimento, crédito presumido e split payment na Reforma | A apuração **mede e relata** esses valores, fora do total, e confere a classificação contra a tabela oficial (`dados/oficiais/`); o que falta é **consumi-los** — cada um exige decidir a que competência o valor pertence, e a tabela diz o enquadramento, não a apuração de cada regime | `src.escrituracoes.reforma:ApuracaoIBSCBS.regimes_especiais` |

## ECD

Já existem a importação, o plano de contas, os lançamentos e partidas, os
saldos periódicos e de resultado, os relatórios e as validações de
integridade; ver [`status.md`](status.md).

O que falta:

| Item | Situação | Marcador de ausência |
|---|---|---|
| Importar o balanço e a DRE publicados (bloco J) | O `J100`, o `J150` e o `J210` trazem as demonstrações **como foram declaradas** — a camada "documento original" da contabilidade. Hoje são lidos pelo parser e descartados na importação: o programa recalcula a DRE a partir dos saldos (I355) e nunca a compara com a publicada. Guardá-las permitiria acusar a divergência entre o que se apurou e o que se declarou, que é o achado mais caro de um trabalho de revisão | `src.ecd_importer:ECDImportService.importar_bloco_j` |

## Dívida técnica conhecida

| Item | Situação | Marcador de ausência |
|---|---|---|
| Rate limiting distribuído | Em memória hoje; múltiplas réplicas exigiriam Redis | `src.ratelimit:RedisRateLimiter` |
| Executor de importação fora do processo web | A importação roda em thread dentro do servidor. Encerrar job abandonado na subida pressupõe instância única — ver [`status.md`](status.md) | `src.worker_runner:executar_importacao` |
