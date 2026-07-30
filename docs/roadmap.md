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

## Dívida técnica conhecida

| Item | Situação | Marcador de ausência |
|---|---|---|
| Rate limiting distribuído | Em memória hoje; múltiplas réplicas exigiriam Redis | `src.ratelimit:RedisRateLimiter` |
| Executor de importação fora do processo web | A importação roda em thread dentro do servidor. Encerrar job abandonado na subida pressupõe instância única — ver [`status.md`](status.md) | `src.worker_runner:executar_importacao` |
