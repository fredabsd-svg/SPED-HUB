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
alterações em massa com simulação e os geradores da EFD ICMS/IPI e da
EFD-Contribuições — ver
[`status.md`](status.md). Sobre a Reforma Tributária, ver
[`reforma-tributaria.md`](reforma-tributaria.md).

O que falta:

| Item | Situação | Marcador de ausência |
|---|---|---|
| Importação de NFS-e por provedor | Exige um adaptador por provedor municipal | `src.documentos.adaptadores:AdaptadorNFSe` |
| Recálculo de totais depois de alteração em massa (§12.5) | Alterar a parcela de um item não recompõe o total do documento | `src.documentos.massa:recalcular` |
| Blocos G, H e 1 da EFD ICMS/IPI | O gerador cobre 0, C, E e 9 | `src.escrituracoes.efd_icms:GeradorEFDICMS.bloco_h` |
| Documentos de serviço, energia e transporte na EFD (C500, D100) | Só o C100 de mercadorias é gerado | `src.escrituracoes.efd_icms:GeradorEFDICMS.bloco_d` |
| Ajustes de apuração pela tabela 5.1.1 (E111) | O E110 é soma direta, sem ajuste nem saldo credor anterior | `src.escrituracoes.efd_icms:GeradorEFDICMS.ajustes_de_apuracao` |
| Blocos A, D, F e I da EFD-Contribuições | O gerador cobre 0, C, M e 9. O bloco A depende da importação de NFS-e, acima | `src.escrituracoes.efd_contribuicoes:GeradorEFDContribuicoes.bloco_f` |
| Créditos extemporâneos e ajustes da EFD-Contribuições | Os blocos M são soma direta das saídas menos as entradas | `src.escrituracoes.efd_contribuicoes:GeradorEFDContribuicoes.ajustes_de_apuracao` |
| Monofásico, substituição e alíquota por unidade no PIS/Cofins | A apuração usa o valor destacado no documento, qualquer que seja o CST | `src.escrituracoes.efd_contribuicoes:GeradorEFDContribuicoes.regimes_especiais` |
| Cooperativa e entidade de folha de salários na EFD-Contribuições | O `IND_NAT_PJ` do 0000 sai fixo como `00` (sociedade empresária em geral), com aviso no resultado | `src.db.models:Empresa.ind_nat_pj` |
| Tela de cadastro fiscal da empresa | `ind_perfil`, `ind_ativ`, `ind_ativ_contribuicoes` e `cod_inc_trib` só podem ser preenchidos direto no banco | `src.routes.empresas:cadastro_fiscal` |
| Apuração de CBS, IBS e Imposto Seletivo | Os campos são lidos do documento; nenhuma apuração os consome | `src.escrituracoes.reforma:ApuracaoIBSCBS` |
| Espelhos antes da geração | — | `src.escrituracoes.espelhos:Espelho` |
| Excel bidirecional de documentos | — | `src.documentos.planilha:reimportar` |

## Dívida técnica conhecida

| Item | Situação | Marcador de ausência |
|---|---|---|
| Rate limiting distribuído | Em memória hoje; múltiplas réplicas exigiriam Redis | `src.ratelimit:RedisRateLimiter` |
| Executor de importação fora do processo web | A importação roda em thread dentro do servidor. Encerrar job abandonado na subida pressupõe instância única — ver [`status.md`](status.md) | `src.worker_runner:executar_importacao` |
