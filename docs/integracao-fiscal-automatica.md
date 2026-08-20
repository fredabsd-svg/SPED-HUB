# Importação fiscal automática — leitura do prompt mestre e plano de adaptação

**Documento de origem:** "Prompt Mestre — Plataforma de Importação Fiscal com
ACBr Pro", recebido em 2026-08-19, com data de referência técnica 25/07/2026.

Este documento existe para responder a uma pergunta: **o que daquele
documento entra neste produto, e como.** Ele não substitui o roadmap — o que
for aceito vira item de lá, com marcador de ausência (§1.1).

## A leitura, sem rodeio

O documento de origem não descreve funcionalidades para acrescentar ao
SPED-HUB. Ele descreve **outro produto**: um monorepositório com Next.js,
NestJS, uma ponte em C# para a ACBrLib, Redis, MinIO e Traefik, construído do
zero a partir de uma Fase 0 de descoberta.

Isso não o torna inútil — ao contrário. Descontada a stack, o que sobra é uma
especificação boa de **capacidades**, e várias delas o SPED-HUB não tem. A
adaptação consiste em separar as duas coisas: o que é capacidade fiscal
(aproveitável) do que é decisão de infraestrutura de outro projeto
(descartável aqui).

Trocar a stack do SPED-HUB por aquela seria reescrever um sistema que
funciona, tem 2000 testes e quatro documentos oficiais conferidos campo a
campo. Não é adaptação, é substituição — e não foi o que se pediu.

## O que já existe aqui

Boa parte do que o documento pede como novidade o SPED-HUB tem, com outro
nome:

| O documento pede | Aqui já existe |
|---|---|
| multi-tenant, escritórios, empresas, RBAC | `auth`, `Escritorio`, `Empresa`, sessões revogáveis |
| XML original imutável, identificado por hash | camada ORIGINAL da arquitetura de três camadas |
| não salvar só o JSON normalizado | ORIGINAL → NORMALIZADO → EFETIVO → ARQUIVADO |
| deduplicação por hash e por chave | `ImportadorDeDocumentos` |
| importação por XML e diretório | `sped-hub fiscal importar` |
| dashboard, filtros, exportação | `dashboard`, `filters`, exportações |
| auditoria de operações sensíveis | `audit` |
| logs estruturados com mascaramento de PII | `logging_config` |
| rate limit, proteção de upload, XXE | `ratelimit`, `uploads`, parsers |
| CNPJ como texto | sempre foi `String(14)`, e agora também alfanumérico |
| preparo para IBS/CBS e Reforma | apuração, tabelas oficiais, NT 2025.002 |

## O que foi aceito e já está feito

**CNPJ alfanumérico** (§3.3 do documento de origem). Era o único item cuja
ausência já era **defeito em produção**: a Receita emitiu a primeira inscrição
alfanumérica em 31/07/2026, e o programa não conseguia ler uma ECD dessas
empresas — o CNPJ virava nulo, sem erro. Ver `docs/modules/cnpj.md`.

## O que foi aceito e virou item de roadmap

Estes entram no `roadmap.md` com marcador. Nenhum foi implementado:

- **Certificado digital A1 guardado com segurança** (§9). Cifra autenticada
  para o PFX, senha cifrada à parte, chave mestra fora do banco, aviso de
  vencimento. É pré-requisito de qualquer consulta automática.
- **Cursor de sincronização por NSU** (§6.1 e §6.2). O modelo de dados, o
  travamento por empresa e a máquina de estados são construíveis aqui e agora;
  só a chamada ao serviço depende do resto.
- **Matriz de capacidades por município/provedor** (§8.1 e §8.2). É a §8.1
  deste projeto aplicada a integração em vez de tabela: declarar o que foi
  homologado, contra o quê e quando — e mostrar "não suportado" quando for o
  caso, em vez de deixar o usuário descobrir no fechamento.
- **Importação de ZIP com as proteções** (§15). Path traversal, zip bomb e
  limite por lote.
- **Taxonomia de erros de integração** (§13). Distinguir bloqueio fiscal de
  falha de rede muda o que o programa faz em seguida.

## O que foi recusado, e por quê

**A stack e o monorepositório** (§4). Reescrita, não adaptação.

**A ponte em C# para a ACBrLib** (§4.2, §5). A razão dada — isolar a
biblioteca nativa — é boa, mas o custo é um serviço a mais em outra linguagem.
Se e quando a ACBrLib entrar, o isolamento aqui é um processo separado em
Python com a mesma fronteira: o argumento vale, a implementação não precisa
ser aquela.

**Contratar ou pressupor o ACBr Pro.** Os binários vêm de assinatura paga e
não estão disponíveis nesta sessão. O próprio documento manda parar nesse
ponto em vez de fingir: *"se não houver acesso aos binários do ACBr Pro ou a
certificado de teste, não finja que validou"*. Não há spike, não há relatório
de spike, e nada aqui afirma que a ACBrLib foi testada.

**Kubernetes, microsserviços, scraping de portal** — o próprio documento os
descarta (§20), e vale registrar que concordamos.

## O que depende de decisão de quem toca o produto

A ordem dos itens de roadmap acima não é óbvia e não é minha para decidir. Em
particular: a consulta automática de NF-e por Distribuição de DF-e é o item de
maior valor do documento de origem **e** o que depende de mais coisas —
certificado, biblioteca, assinatura e homologação. Começar por ele significa
parar no primeiro obstáculo externo; começar pelos outros entrega valor sem
depender de terceiro.

## O que o documento de origem acerta e vale copiar como postura

Duas coisas, que já são a prática desta base de código e merecem estar
escritas:

> A existência de um método na biblioteca não comprova que todos os provedores
> municipais implementem esse serviço.

> Nunca mostrar "integração disponível" quando a operação não tiver sido
> homologada.

É a mesma regra que este projeto aplica a tabela de terceiro (§8.1) e a leiaute
(procedência declarada, com data e fonte). Vale para integração pelo mesmo
motivo: **informação sem procedência não dá para conferir.**
