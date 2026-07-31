# Reforma Tributária do Consumo no SPED-HUB

**Legislação consultada em:** 2026-07-31
**Grau de segurança das informações desta página:** **alto para o leiaute** —
a NT 2025.002 v1.50 foi obtida do portal DF-e da SVRS e conferida campo a
campo; **médio para a tabela de classificação tributária** — ver "Procedência"
ao final.

Como o sistema representa CBS, IBS e Imposto Seletivo, e por quê.

## O prazo que importa

A partir de **03/08/2026**, pela Nota Técnica 2025.002 (versão **1.50**, de
02/06/2026), a NF-e e a NFC-e passam a **rejeitar** documentos sem os grupos de
IBS e CBS. Não é advertência: é rejeição na autorização. Para Simples Nacional
e MEI o prazo é **04/01/2027**; em homologação a exigência vale desde
01/07/2026.

A v1.50 **reformulou o leiaute da tributação monofásica de combustíveis**,
separando ad rem de ad valorem em quatro grupos. Quem tenha lido a v1.40 e
parado ali está lendo um leiaute que a NT substituiu.

Para o SPED-HUB isso quer dizer que todo XML importado a partir dessa data traz
os grupos novos, e um importador que os ignore perde informação que a
escrituração vai precisar.

## Os três tributos

| Tributo | Competência | Substitui |
|---|---|---|
| **CBS** — Contribuição sobre Bens e Serviços | União | PIS e Cofins |
| **IBS** — Imposto sobre Bens e Serviços | Estados, DF e Municípios | ICMS e ISS |
| **IS** — Imposto Seletivo | União | — (extrafiscal, sobre bens e serviços prejudiciais à saúde e ao meio ambiente) |

## Cronograma da transição

| Período | O que vale |
|---|---|
| 2026 | Ano de teste: CBS a 0,9% e IBS a 0,1%, destacados no documento. PIS, Cofins, ICMS, ISS e IPI seguem inalterados |
| 2027 | CBS em alíquota plena; PIS e Cofins extintos; IPI reduzido a zero, exceto para manufaturados na Zona Franca de Manaus |
| 2029–2032 | ICMS e ISS reduzidos progressivamente: 10%, 20%, 30% e 40% |
| 2033 | ICMS e ISS extintos; sistema pleno |

**A consequência de projeto:** durante sete anos os dois regimes convivem no
mesmo documento. Por isso `ItemDocumentoFiscal` tem os campos de ICMS, PIS e
Cofins **e** os de IBS, CBS e IS lado a lado — não são alternativas. Modelar
como substituição obrigaria a reescrever o schema na virada de cada ano da
transição.

## O IBS é um tributo com duas destinações

O XML traz alíquota e valor **separados** para a parcela estadual e a
municipal, e o sistema preserva essa separação:

| Campo do modelo | Origem no XML |
|---|---|
| `aliquota_ibs_uf`, `valor_ibs_uf` | `gIBSUF/pIBSUF`, `gIBSUF/vIBSUF` |
| `aliquota_ibs_mun`, `valor_ibs_mun` | `gIBSMun/pIBSMun`, `gIBSMun/vIBSMun` |
| `municipio_fg_ibs` (no **documento**) | `ide/cMunFGIBS` (B12a) |

Somar as duas parcelas numa coluna só pareceria mais simples e destruiria a
informação de que a apuração depende: a partilha entre os entes é o cerne do
imposto. O município do fato gerador pode ainda diferir do município do
destinatário, e é ele que decide para onde vai a parcela municipal — por isso
ele fica no documento, onde a NT o põe, e não no imposto do item.

**E a separação vale também para os benefícios.** A NT repete `gRed`, `gDif` e
`gDevTrib` dentro de cada destinação, com as mesmas tags nos três; o item pode
ter diferimento só na parcela estadual. Por isso são três colunas de cada:

| Campo do modelo | Origem no XML |
|---|---|
| `percentual_reducao_*`, `aliquota_efetiva_*` | `gRed/pRedAliq`, `gRed/pAliqEfet` da destinação |
| `valor_diferido_*` | `gDif/vDif` da destinação |
| `valor_devolucao_*` | `gDevTrib/vDevTrib` da destinação |

onde `*` é `ibs_uf`, `ibs_mun` ou `cbs`. Percentuais, aliás, não somam: três
reduções de 10%, 20% e 30% não são uma de 60%.

## Crédito presumido e monofásico

O crédito presumido fica em `gCredPresOper` (UB120), **irmão** de `gIBSCBS` e
não filho: o código (`cCredPres`) é um só para a operação, e o percentual e o
valor vêm separados em `gIBSCredPres` (UB123) e `gCBSCredPres` (UB127).

O monofásico de combustíveis foi reformulado na v1.50 em quatro variantes —
IBS e CBS, cada um ad rem ou ad valorem —, escolhidas por tributo e por ano.
Um item pode ter IBS ad rem e CBS ad valorem ao mesmo tempo, e aí carrega as
duas bases: quantidade (`qBCMono`) por um e valor (`vBCMono`) pelo outro.

O que a apuração usa é o total do item, que a própria NT fecha em
`vTotIBSMonoItem` e `vTotCBSMonoItem` (UB105a/UB105b) — filhos diretos de
`gIBSCBSMono`, iguais qualquer que tenha sido a variante. Refazer essa soma a
partir das variantes seria recalcular, com menos informação, o que já veio
pronto.

**`gMonoReten` e `gMonoRet` são coisas opostas com nomes quase iguais:** o
primeiro é o imposto sobre o biocombustível a ser misturado, que **soma** ao
que se recolhe (art. 178 da LC 214/2025); o segundo é o que já foi cobrado
antes. Daí `valor_*_mono_reten` e `valor_*_mono_retido` serem colunas
distintas — trocar uma pela outra erra o sinal do monofásico inteiro.

## Classificação: CST e cClassTrib

O enquadramento de cada item vem de dois códigos que andam juntos:

- **CST do IBS/CBS** — três dígitos, campo `cst_ibscbs`;
- **cClassTrib** — código de classificação tributária, campo
  `class_trib_ibscbs`. Os três primeiros dígitos repetem o CST; os seguintes
  detalham o enquadramento legal.

Os CST identificados na consulta:

| CST | Situação |
|---|---|
| 000 | Tributação integral |
| 010 | Tributação com alíquotas uniformes (setor financeiro) |
| 011 | Tributação com alíquotas uniformes reduzidas |
| 200 | Alíquota zero ou reduzida |
| 220 | Alíquota fixa |
| 221 | Alíquota fixa proporcional |
| 222 | Redução de base de cálculo |
| 400 | Isenção |
| 410 | Imunidade e não incidência |
| 510 | Diferimento |
| 515 | Diferimento com redução de alíquota |
| 550 | Suspensão |
| 620 | Tributação monofásica |
| 800 | Transferência de crédito |
| 810 | Ajustes de IBS na ZFM |
| 811 | Ajustes |
| 820 | Tributação em documento específico |
| 830 | Exclusão de base de cálculo |

**O sistema não embute esta tabela como regra de validação.** Ela é publicada e
atualizada pela SVRS, em tabela interativa própria, e uma cópia congelada no
código viraria fonte de erro no primeiro ato normativo. Os campos
`class_trib_ibscbs` e `class_trib_is` têm largura folgada (10) justamente
porque a tabela cresce.

## Imposto Seletivo: duas formas de alíquota

O IS pode ser **ad valorem** (percentual sobre a base) ou **específico** (valor
por unidade tributável) — bebidas e cigarros usam a segunda. Por isso a unidade
e a quantidade tributável viajam junto com os valores:

| Campo do modelo | Origem no XML |
|---|---|
| `cst_is` | `CSTIS` |
| `class_trib_is` | `cClassTribIS` |
| `base_is` | `vBCIS` |
| `aliquota_is` | `pIS` |
| `aliquota_is_especifica` | `pISEspec` |
| `unidade_tributavel_is` | `uTrib` |
| `quantidade_tributavel_is` | `qTrib` |
| `valor_is` | `vIS` |

## Procedência das informações

**O leiaute está conferido contra o documento oficial.** Em 2026-07-31 o
portal DF-e da SVRS voltou a responder e a NT 2025.002 v1.50 foi baixada e
lida: o aninhamento de cada grupo desta página vem dela, com o identificador
do campo (UB…) anotado no código onde a decisão dependeu dele.

`nfe.fazenda.gov.br` seguiu fora do ar; o mirror que respondeu foi:

- Portal DF-e da SVRS — documentos técnicos da NF-e:
  <https://dfe-portal.svrs.rs.gov.br/Nfe/Documentos>
- Tabela de Classificação Tributária (SVRS):
  <https://dfe-portal.svrs.rs.gov.br/DFE/TabelaClassificacaoTributaria>
- Tabela de Crédito Presumido (SVRS):
  <https://dfe-portal.svrs.rs.gov.br/DFE/TabelaCreditoPresumido>

**O que segue vindo de fonte secundária é a semântica dos códigos** — o que
cada CST do IBS/CBS e cada `cClassTrib` significa. A IT 2025.002 v1.50 existe
no portal, mas o sistema não a embute: ela é atualizada por ato normativo, e
uma cópia congelada no código viraria fonte de erro na primeira revisão.

O que está **verificado no código** é a *estrutura*: quais campos existem, em
que grupo cada um vive, que o IBS tem duas parcelas, que o IS aceita alíquota
específica, e que os dois regimes convivem. Os **valores** de alíquota e os
**códigos** de classificação são dado de entrada, lido do XML — o sistema não
os calcula nem os presume.

### O que a conferência encontrou

A leitura estava errada em cinco pontos, todos com o mesmo formato: o campo era
procurado num nó que não é o pai dele. Procurar uma tag no nó errado não levanta
erro — devolve `None`, que vira `0,0`. Em NF-e montada como a NT manda, **todo
grupo opcional da reforma lia zero**:

| Grupo | Onde era procurado | Onde a NT o põe |
|---|---|---|
| `gRed`, `gDif`, `gDevTrib` | filhos de `gIBSCBS` | um de cada dentro de `gIBSUF`, `gIBSMun` e `gCBS` |
| crédito presumido | `gCredPres` em `gIBSCBS` | `gCredPresOper` (UB120), irmão de `gIBSCBS`, com `gIBSCredPres` e `gCBSCredPres` |
| monofásico | filhos de `gIBSCBSMono` | dentro de `gMonoPadrao`/`gMonoReten`/`gMonoRet`, sob uma das quatro variantes ad rem/ad valorem |
| `cMunFGIBS` | dentro de `gIBSCBS`, no item | campo B12a do `ide`, do documento |
| `vIBSMonoReten` | gravado como "retido" | é o **sujeito à** retenção; o retido anteriormente é `vIBSMonoRet` |

Os testes passavam porque a fixture de NF-e era montada a partir do leitor, e
não a partir da NT: reproduzia o mesmo engano, e concordava com ele. A fixture
agora segue o documento oficial, e `tests/test_leitura_reforma_nt.py` fixa o
aninhamento de cada grupo.

## A apuração

`src/escrituracoes/reforma.py` soma CBS, IBS e IS de um período, a partir da
camada efetiva — os documentos normalizados mais os ajustes. Três decisões
carregam o resultado:

**O Imposto Seletivo não gera crédito.** É extrafiscal e monofásico: incide
uma vez na cadeia, e quem revende não credita o que veio na entrada. Por isso
`ResultadoApuracao.seletivo` é um número, não um `Tributo` — dar-lhe um campo
`credito` seria convidar alguém a preenchê-lo. Tratá-lo como CBS e IBS
reduziria o imposto devido pelo valor do IS das compras, num resultado com a
mesma cara de uma apuração correta.

**As duas parcelas do IBS são apuradas em separado.** A estadual e a municipal
vão para entes diferentes, e uma pode ter saldo credor enquanto a outra tem
imposto a pagar. Compensar uma com a outra seria pagar o estado com dinheiro
do município. `ibs_total_devido` existe só para exibição, e soma os dois
*devidos* — não os débitos e créditos brutos.

**O total de 2026 não é o valor a recolher.** No ano de teste, CBS a 0,9% e
IBS a 0,1% são destacados no documento, com mecanismo de compensação e
dispensa para quem cumpre as obrigações acessórias — que o sistema não modela.
O resultado avisa isso enquanto o período tocar 2026, e só enquanto tocar: um
aviso que sai sempre não informa nada.

**Nota sem os grupos novos não quebra a apuração.** A transição dura sete
anos e o mesmo período mistura documentos com e sem IBS/CBS/IS; os campos
ausentes valem zero.

## O que ainda não existe

Esta página descreve o que o modelo de dados representa. Não existe ainda:

- escrituração dos tributos novos em obrigação acessória;
- monofásico, retenção, diferimento, crédito presumido e devolução de tributo
  na apuração — os campos são lidos e **medidos**, e a soma direta não os
  consome;
- os grupos `gTransfCred` (UB106), `gAjusteCompet` (UB112), `gEstornoCred`
  (UB116) e `gpBioDiferenca`, que o leitor ainda não lê;
- validação de CST contra a tabela oficial;
- tratamento do split payment;
- regimes específicos e diferenciados.

Ver [`roadmap.md`](roadmap.md).
