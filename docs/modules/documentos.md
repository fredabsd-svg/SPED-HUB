# documentos

## O que faz

Lê documento fiscal de terceiro e o grava normalizado, preservando o original.
Hoje entende NF-e e NFC-e (leiaute 4.00), lendo os dois regimes tributários —
ICMS/IPI/PIS/Cofins e IBS/CBS/IS da reforma.

As três camadas que a suíte separa:

| Camada | Onde vive | Mutável? |
|---|---|---|
| **Original** | `DocumentoFiscal.xml_original` | Nunca |
| **Normalizado** | colunas de `DocumentoFiscal` e `ItemDocumentoFiscal` | Nunca |
| **Efetivo** | calculado: normalizado + `AjusteFiscal` aplicados | É o resultado |

## O que expõe

| Símbolo | Para quê |
|---|---|
| `AdaptadorNFe` | NF-e (55) e NFC-e (65), leiaute 4.00, com ou sem os grupos da reforma. |
| `adaptador_para(conteudo)` | Escolhe o adaptador; levanta `OrigemNaoReconhecida`. |
| `registrar_adaptador(a)` | Põe um adaptador na fila; o primeiro que reconhecer vence. |
| `DocumentoNormalizado` / `ItemNormalizado` | A estrutura única para onde toda origem converge. |
| `carregar_xml(conteudo)` | Lê o XML recusando `DOCTYPE`; levanta `XMLPerigoso`. |
| `ImportadorDeDocumentos(session, escritorio_id=, politica=)` | Grava, deduplica e resolve o sentido. |
| `.importar(conteudo)` / `.importar_lote(arquivos)` | Um documento ou vários; devolve `Ocorrencia` / `ResultadoImportacao`. |
| `PoliticaDeDuplicidade` | `IGNORAR` (padrão), `SUBSTITUIR`, `ERRO`. |
| `Desfecho` | `importado`, `duplicado`, `substituido`, `rejeitado`. |
| `Sentido` | `entrada` / `saida`, relativo à empresa que escritura. |
| `valor_efetivo(alvo, campo, ajustes)` | O valor que vai para o SPED. Recebe os ajustes já carregados. |
| `efetivo(session, documento)` | `VisaoEfetiva` do documento inteiro, numa consulta só. |
| `aplicar_ajuste(session, ...)` | Registra a alteração; devolve `None` se o valor já era o efetivo. |
| `desfazer_lote(session, lote)` | Apaga os ajustes do lote; devolve quantos saíram. |
| `historico(session, doc, campo=None)` | Os ajustes que alcançam o campo, em ordem. |
| `novo_lote()` | Identificador para desfazer uma massa inteira. |
| `ORIGEM_REGRA` / `ORIGEM_USUARIO` | Separa sugestão de decisão. |
| `MotorDeClassificacao(session, obrigacao=).avaliar(doc)` | Propõe, sem tocar no banco. Devolve `ResultadoClassificacao`. |
| `Sugestao` | Regra, valor anterior, sugerido, justificativa, confiança e `impacto` em reais. |
| `Conflito` | Duas regras de mesma prioridade no mesmo campo — o motor não escolhe. |
| `aplicar(session, doc, sugestoes, lote=)` | Vira `AjusteFiscal` de origem `regra`; devolve o lote. |
| `criar_regra(session, ...)` / `validar_regra(regra)` | Cadastra já validada. |
| `regras_aplicaveis(session, doc)` | As que valem, da maior prioridade à menor. |
| `OPERADORES` | `igual`, `em`, `comeca_com`, `vazio`, `maior_que`… |
| `Selecao(escritorio_id=, empresa_id=, data_inicio=, data_fim=, filtros=)` | O recorte da massa. Recusa seleção sem filtro. |
| `Filtro(campo, operador, valor)` | Uma condição do recorte; todas são E. |
| `Alteracao(campo, valor, apenas_vazios=)` | O que fazer com o selecionado. |
| `simular(session, selecao, alteracoes, recompor_totais=)` | O que mudaria — **não toca no banco**. Já traz os totais do cabeçalho recompostos. |
| `recalcular(session, documentos, mudancas=)` | Os totais que são soma de parcela, refeitos a partir dos itens. |
| `confirmar(session, simulacao, motivo=, forcar=)` | Grava num lote reversível. |
| `Simulacao` | Contagens, `impacto_total` em reais, `por_campo()`, `avisos`. |
| `Aviso` | Problema detectado, com `impeditivo` separando recusa de sinalização. |
| `exportar(session, selecao)` | Os itens do recorte como `.xlsx`, com a camada **efetiva** aplicada. |
| `reimportar(session, conteudo)` | Lê a planilha corrigida e devolve o que ela mudaria — **sem gravar**. |
| `Reimportacao` | `simulacao` (a mesma de `simular`), `divergencias` e `linhas_lidas`. |
| `Divergencia` | Linha que não virou alteração, com o número da linha e o motivo. |
| `COLUNAS` / `EDITAVEIS` | As colunas da planilha, e quais delas a volta aceita. |
| `PlanilhaInvalida` | Arquivo que não abre, ou sem as colunas que ligam a linha ao banco. |
| `tabelas_ibscbs.tabelas()` | As tabelas oficiais de CST, `cClassTrib` e `cCredPres`, com a data de publicação. |
| `tabelas_ibscbs.conferir(item, data_emissao=, modelo=, valor=)` | Os problemas de classificação do item, em português. Lista vazia é "sem problema". |
| `tabelas_ibscbs.aliquotas_padrao(ano)` | As alíquotas do ano, ou `None` quando a legislação ainda não as fixou. |
| `tabelas_ibscbs.TabelaAusente` | O JSON gerado não está no lugar — instalação incompleta. |

## O que não faz

Não gera escrituração — quem gera é `escrituracoes`, lendo a camada efetiva
daqui. Nenhuma tela mostra a Central: o acesso é pelo `sped-hub fiscal`. O
motor **não escolhe** entre regras empatadas e `simular` **não grava**. O
recálculo de totais (§12.5) refaz o que é soma de parcela e **não** refaz o
`valor_total` (vNF) — ver as armadilhas abaixo. Não lê NFS-e: cada provedor
municipal precisa do seu adaptador. A planilha **não grava**: reimportar
devolve uma `Simulacao`, e quem grava é `confirmar`. **Não calcula tributo
nenhum**: os valores de CBS, IBS e IS são lidos do XML, nunca presumidos — e a
conferência contra a tabela oficial diz que a classificação está errada, nunca
qual seria a certa.

## Depende de / quem depende

Depende de `db.models`, da stdlib (`xml.etree.ElementTree`, `hashlib`, `json`)
e, só na planilha, de `openpyxl` — que o projeto já usava para os relatórios. Quem depende: `escrituracoes` (lê o efetivo para gerar) e
`cli_fiscal` (a única porta de entrada humana hoje).

## Decisões não óbvias e armadilhas

- **A tabela oficial é derivada da planilha da SVRS, nunca digitada.** As
  planilhas ficam em `dados/oficiais/` e `scripts/gerar_tabelas_ibscbs.py`
  produz o JSON que o programa lê (§1.9). São 164 classificações; digitá-las
  seria errar sem que ninguém conferisse, e a tabela **muda** — a versão 1.10
  do IT incluiu seis códigos, dividiu o 620004 em dois e renumerou o antigo
  620005. A geração é lida por **nome de coluna**: a planilha tem 82 colunas,
  dezenas vazias, e uma coluna inserida no meio deslocaria tudo em silêncio.
- **A conferência aponta o erro, não a correção.** A tabela diz que a
  classificação está inválida; qual seria a válida depende do enquadramento
  legal do item, que é decisão de quem escritura. Sugerir um código seria
  dar palpite com cara de resposta.
- **A data de publicação viaja com a tabela.** `sped-hub fiscal tabelas` a
  mostra, e ela entra no texto de cada apontamento. Tabela velha responde
  exatamente como tabela nova, e o erro só aparece na rejeição da SEFAZ, um
  mês depois — a data é o que torna a defasagem visível antes disso.
- **`conferir` recebe como ler o campo (`valor=`), por causa das camadas.**
  Sem isso ela olharia o original — que a SEFAZ já autorizou, e onde uma
  classificação inválida não teria virado documento. Quem confere a
  escrituração passa o leitor da camada efetiva, que é o que vai sair no
  arquivo.
- **O vNF não é soma de parcela: tem fórmula, e três exceções.** A regra
  W16-10 do MOC 7.0 soma doze termos, e o sistema só passou a recompô-lo
  depois de carregar todos — calcular com metade produziria um total errado
  apresentado como certo, que é pior que um desatualizado (o desatualizado ao
  menos é o número que o emitente declarou). Onde a regra não vale — operação
  de importação (CFOP 3xxx **efetivo**) — não se recompõe: avisa-se.
- **A convenção do ICMS desonerado é lida do documento original.** A própria
  regra diz que o Fisco não rejeita quem deixou de subtraí-lo, de modo que
  dois totais diferentes são igualmente válidos para a mesma nota. Não há como
  escolher um por fora; há como descobrir qual o emitente usou, porque a
  primeira camada guardou o documento como ele veio. É o uso mais direto que
  as três camadas têm dentro do próprio domínio.
- **A terceira camada é calculada, não gravada.** Gravar o valor final numa
  coluna faria as três camadas divergirem no primeiro `UPDATE` escrito fora do
  fluxo. Calculando, desfazer um lote é apagar seus ajustes, e "por que este
  registro saiu assim?" se responde listando os ajustes daquele campo.
- **A planilha volta sem gravar.** `reimportar` devolve a mesma `Simulacao`
  que `simular` — quem confirma é `confirmar`, num lote reversível. Uma
  planilha que gravasse ao ser lida seria a única escrita do sistema sem que
  ninguém visse o que vai mudar, e é a que mais tem como dar errado: passou
  por um programa que não é este.
- **A identidade viaja e é reconferida.** Cada linha leva `documento_id` e
  `item_id`, e a volta confere a chave da nota contra o banco. Planilha
  reordenada, com linha apagada ou colada de outro mês é o caso normal, não o
  excepcional: casar por posição faria a correção de um documento cair em
  outro, e o erro só apareceria na intimação.
- **A comparação da volta tolera meio centavo.** O Excel guarda
  `1000.0000000001` para um `1000,00` digitado; comparar por igualdade exata
  faria toda linha intocada voltar como alteração, e ninguém leria a lista.
- **Não há coerção de `float` para `int` na leitura da planilha.** O risco
  aparente é um CFOP voltar `2102.0` e virar `"2102.0"`; só que openpyxl
  normaliza inteiro para `int` ao ler, e o ramo de coerção seria código que
  nenhuma entrada alcança. Código morto com comentário é pior que código
  nenhum: parece cobrir algo.
- **`valor_efetivo` recebe os ajustes, não os busca.** Buscar por campo daria
  uma consulta para cada um dos 68 campos de cada item — a geração de um mês
  viraria centenas de milhares de consultas.
- **A ordem é `(criado_em, id)`.** O `id` não é redundante: um lote de
  alteração em massa nasce todo no mesmo instante, e sem o desempate qual
  ajuste vale passaria a depender da ordem em que o banco devolveu as linhas.
- **Ajuste que não muda nada não é gravado.** Poluiria o histórico e faria a
  simulação de uma massa relatar impacto que não existe.
- **`valor_anterior` é o efetivo, não o normalizado.** O segundo ajuste de um
  campo parte de onde o primeiro deixou; gravar o normalizado faria o
  histórico mentir.
- **Valor de ajuste que não converte para o tipo da coluna vira aviso, não
  exceção.** Um ajuste corrompido não pode impedir o mês inteiro de sair.
- **O recálculo recompõe o que é soma de parcela e para aí.** Alterar em massa
  o valor dos itens sem mexer no cabeçalho gera um arquivo em que o `C100` diz
  uma coisa e a soma dos `C170` diz outra — que é justamente o que o validador
  do Fisco confere. Já o `valor_total` (vNF) **não** é soma de parcela: a
  fórmula legal soma frete, seguro, despesas e IPI e desconta o ICMS
  desonerado, e o modelo não carrega todos esses termos. Recalculá-lo com o que
  existe daria um número errado com cara de certo, então ele fica como
  declarado e a simulação avisa — aviso **não impeditivo**, porque quem sabe o
  número correto é o usuário.
- **O recálculo parte das mudanças simuladas, não do banco.** Se lesse os itens
  gravados, a simulação mostraria o cabeçalho recomposto a partir dos valores
  antigos — coerente com nada.
- **Cabeçalho que já estava errado antes não é consertado de carona.** Só entra
  no recálculo o documento que teve item mexido. Consertar os outros faria a
  simulação exibir mudanças que ninguém pediu, sem que se soubesse de onde
  vieram; para fazer isso de propósito existe `recalcular`.
- **A mudança vinda do recálculo não conta no impacto.** Ela é consequência das
  alterações dos itens, que já contaram; somar as duas relataria o dobro.
- **`AjusteFiscal` é aditivo.** Cada linha guarda o valor anterior, a origem
  (`regra` ou `usuario`) e o lote. Nenhum ajuste sobrescreve outro; o efetivo
  é o mais recente que alcança o campo.
- **Adaptadores, não um parser único.** A NF-e é nacional e estável; a NFS-e
  varia por município e por provedor. Um parser único viraria uma cascata de
  condicionais que ninguém altera sem quebrar outro município.
- **Os tributos da reforma convivem com os antigos** em `ItemDocumentoFiscal`,
  não os substituem: os dois regimes coexistem de 2026 a 2032. Ver
  [`../reforma-tributaria.md`](../reforma-tributaria.md).
- **Cada grupo da reforma é lido do nó em que a NT o põe, e nem sempre é o
  óbvio.** Redução, diferimento e devolução existem uma vez dentro de cada
  destinação (`gIBSUF`, `gIBSMun`, `gCBS`), com as mesmas tags nos três; o
  crédito presumido é `gCredPresOper`, irmão de `gIBSCBS`; o monofásico está a
  dois níveis, sob uma de quatro variantes; `cMunFGIBS` é campo do `ide`, do
  documento. Procurar no nó errado **não levanta erro** — devolve `None`, que
  vira `0.0` —, e foi assim que o leitor passou a existir sem ler nada disso.
  Por isso a fixture de NF-e é montada a partir da Nota Técnica, e não a partir
  do leitor: montada a partir do leitor, ela reproduz o mesmo engano e os
  testes concordam com o erro.
- **A tabela de CST do IBS/CBS não está embutida no código.** É publicada e
  atualizada pela SVRS; uma cópia congelada viraria fonte de erro no primeiro
  ato normativo.
- **`DOCTYPE` é recusado.** `ElementTree` não lê entidade externa, mas
  **expande** entidade interna: quatro níveis já produzem 3.000 caracteres, e
  cada nível multiplica por dez — um XML de 1 KB derruba o processo. NF-e
  legítima não declara `DOCTYPE` (o leiaute é XSD), então recusar a declaração
  elimina a classe de ataque sem custo.
- **O sentido não sai do `tpNF`.** Esse campo é a visão do emitente, e a mesma
  nota é saída para quem emitiu e entrada para quem recebeu. Quem decide é a
  comparação com o CNPJ da empresa que escritura.
- **`SUBSTITUIR` apaga os ajustes do documento antigo** (cascade). Por isso o
  padrão é `IGNORAR`: reimportar uma pasta com a política errada descartaria
  horas de classificação sem avisar.
- **Condições e ações de regra são JSON estruturado, não expressão avaliada.**
  Um campo de texto que o sistema executasse transformaria a tabela de regras
  em superfície de execução de código no servidor — quem escrevesse nela
  rodaria o que quisesse —, a troco de expressividade que o domínio não pede:
  as condições reais são comparações entre um campo e um valor.
- **`escritorio_id IN (1, NULL)` não casa com `escritorio_id IS NULL`.** Em
  SQL o `NULL` não participa de `IN`, e as regras de escopo global — que são a
  maioria — ficavam invisíveis: a classificação simplesmente não acontecia. O
  filtro usa `or_(coluna == valor, coluna.is_(None))`.
- **Empate de prioridade no mesmo campo é conflito, não escolha.** Decidir por
  ordem de chegada faria a mesma importação produzir resultados diferentes
  entre execuções, sem ninguém desconfiar. O motor denuncia e deixa o campo
  como está.
- **A regra lê o efetivo, não o normalizado.** Uma regra que roda depois de
  outra precisa enxergar o que a primeira decidiu, senão a ordem das regras
  deixa de significar o que aparenta.
- **`avaliar` não grava.** Sugerir e aplicar são passos separados: uma
  classificação errada aplicada em silêncio sobre um mês inteiro só se
  descobre na malha fina.
- **O filtro da massa trabalha sobre o efetivo.** Um item já classificado
  tem de aparecer pelo valor novo, senão a segunda passada de saneamento não
  enxerga o que a primeira fez. Como o efetivo não existe em SQL, o recorte é
  em duas etapas: o banco reduz pelo escopo (escritório, empresa, período), o
  conteúdo é conferido em memória.
- **Filtro de item alcança o cabeçalho quando algum item casa.** Sem isso,
  "documentos que tenham item com NCM 2203, mudar a natureza de operação"
  seria impossível — o cabeçalho não tem a coluna `ncm` e nunca casaria.
- **Campo que existe nos dois níveis só atinge o item.** `base_icms` é parcela
  no item e TOTAL no documento; sobrescrever o total com o valor de uma
  parcela deixaria o cabeçalho sem bater com a soma dos itens. Ajustar total é
  recálculo, não substituição.
- **Seleção sem filtro é recusada.** Alcançaria a base inteira do escritório, e
  `desfazer_lote` seria a única saída depois do estrago.
- **As proteções são deliberadamente poucas.** Só o que dá para checar sem
  cadastro que ainda não existe: CFOP contra o sentido do documento, formato de
  NCM/CEST/CST, documento cancelado. CSOSN em empresa não optante exigiria o
  regime tributário cadastrado — fingir que verifica seria pior que não
  verificar.
- **O ICMS vem embrulhado na variante** (`ICMS00`, `ICMS60`, `ICMSSN102`…). O
  adaptador desce no primeiro filho em vez de listar as ~20 formas, que mudam
  a cada nota técnica.

## Como testar isoladamente

```bash
pytest tests/test_documentos_fiscais.py -q  # adaptador, reforma, XML hostil, duplicidade
pytest tests/test_camada_efetiva.py -q      # ajustes, tipos, reversão por lote
pytest tests/test_classificacao_fiscal.py -q  # regras, prioridade, conflito, vigência
pytest tests/test_alteracoes_em_massa.py -q   # seleção, simulação, proteções, reversão
pytest tests/test_migrations.py -q          # o schema da migração bate com os modelos
pytest tests/test_tabelas_ibscbs.py -q      # geração, conteúdo oficial e conferência
```

Para atualizar a tabela quando a SVRS publicar uma versão nova: baixe a
planilha do [portal DF-e](https://dfe-portal.svrs.rs.gov.br/Nfe/Documentos)
(aba Documentos → Diversos), ponha em `dados/oficiais/` com a data no nome,
aponte o script para ela e rode `python scripts/gerar_tabelas_ibscbs.py`. O
teste de geração recusa planilha trocada sem regerar.
