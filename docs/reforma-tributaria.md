# Reforma Tributária do Consumo no SPED-HUB

**Legislação consultada em:** 2026-08-02
**Grau de segurança das informações desta página:** **alto** — a NT 2025.002
**v1.51**, o IT 2025.002 v1.50 e as planilhas de `cClassTrib` e `cCredPres`
foram obtidos do portal DF-e da SVRS e conferidos campo a campo. As planilhas
estão versionadas em `dados/oficiais/`; ver "Procedência" ao final.

Como o sistema representa CBS, IBS e Imposto Seletivo, e por quê.

## O prazo que importa

A partir de **03/08/2026**, pela Nota Técnica 2025.002 (versão **1.51**, de
julho de 2026), a NF-e e a NFC-e passam a **rejeitar** documentos sem os grupos
de IBS e CBS. Não é advertência: é rejeição na autorização. Para Simples
Nacional e MEI o prazo é **04/01/2027**; em homologação a exigência vale desde
01/07/2026.

A **v1.51 não mexeu no leiaute**: alterou regras de validação (UB13, UB18,
UB22, UB26, UB37, UB40, UB45, UB56, UB59, UB64, UB112, UB116, UB131, VC02,
entre outras) e antecipou o cronograma da regra UB12-10 — a que exige os
novos tributos — para 03/08/2026. Os campos e o aninhamento continuam os da
v1.50, que **reformulou o leiaute da tributação monofásica de combustíveis**,
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

E a diferença entre as duas decide o que a apuração mede. A regra UB105a-10 da
NT dá a conta do total do item:

    vTotIBSMonoItem = vIBSMono + vIBSMonoReten - vIBSMonoDif

ou seja, **o sujeito à retenção já está dentro do total**, e o retido
anteriormente não está. Por isso o `reten` é guardado mas **não** entra na
lista de valores que a apuração não consome: quem lê essa lista soma o que vê,
e o item de R$ 12,00 apareceria como R$ 14,00. O `retido` entra, porque é a
única parcela que o total realmente deixa de fora.

## Transferência, ajuste e estorno

Três grupos que não são tributo do item, mas valor destacado nele:

| Grupo | Campos | O que é |
|---|---|---|
| `gTransfCred` (UB106) | `vIBS`, `vCBS` | crédito transferido |
| `gAjusteCompet` (UB112) | `competApur`, `vIBS`, `vCBS` | ajuste que pertence a outra apuração, possivelmente retroativa |
| `gEstornoCred` (UB116) | `vIBSEstCred`, `vCBSEstCred` | crédito a estornar |

Os dois primeiros são **alternativas a `gIBSCBS`** na mesma escolha do schema
(UB14k): um item que transfere crédito não traz grupo de tributo nenhum. O
terceiro é filho opcional de `IBSCBS`, fora da escolha, e acompanha um item
tributado normalmente.

Todos são lidos, guardados e **medidos** pela apuração — que não os consome,
porque consumi-los exigiria decidir a que competência cada um pertence, e isso
é decisão de quem escritura.

## Classificação: CST e cClassTrib

O enquadramento de cada item vem de dois códigos que andam juntos:

- **CST do IBS/CBS** — três dígitos, campo `cst_ibscbs`;
- **cClassTrib** — código de classificação tributária, campo
  `class_trib_ibscbs`. Os três primeiros dígitos repetem o CST; os seguintes
  detalham o enquadramento legal, e cada código corresponde a um dispositivo
  específico da LC 214/2025.

**A tabela está no programa, derivada da planilha oficial.** As planilhas da
SVRS ficam versionadas em `dados/oficiais/` e
`scripts/gerar_tabelas_ibscbs.py` produz o JSON que `documentos.tabelas_ibscbs`
lê (§1.9). Hoje: **18 CST, 164 classificações, 13 códigos de crédito
presumido**, publicados em **2026-06-22**.

`sped-hub fiscal tabelas` mostra a tabela e a data dela; `--codigo` consulta um
código nas três.

Os dezoito CST, com o que cada um **exige** no documento:

| CST | Situação | Exige |
|---|---|---|
| 000 | Tributação integral | `gIBSCBS` |
| 010 | Tributação com alíquotas uniformes | `gIBSCBS` |
| 011 | Tributação com alíquotas uniformes reduzidas | `gIBSCBS`, `gRed` |
| 200 | Alíquota reduzida | `gIBSCBS`, `gRed` |
| 220 | Alíquota fixa | `gIBSCBS` |
| 221 | Alíquota fixa proporcional | `gIBSCBS` |
| 222 | Redução de base de cálculo | `gIBSCBS`, redutor de BC |
| 400 | Isenção | — |
| 410 | Imunidade e não incidência | — |
| 510 | Diferimento | `gIBSCBS`, `gDif` |
| 515 | Diferimento com redução de alíquota | `gIBSCBS`, `gRed`, `gDif` |
| 550 | Suspensão | `gIBSCBS` |
| 620 | Tributação monofásica | `gIBSCBSMono` |
| 800 | Transferência de crédito | `gTransfCred` |
| 810 | Ajuste de IBS na ZFM | `gCredPresIBSZFM` |
| 811 | Ajustes | `gAjusteCompet` |
| 820 | Tributação em documento específico | — |
| 830 | Exclusão de base de cálculo | `gIBSCBS` |

Note que **620 não exige `gIBSCBS`**: o grupo do monofásico é alternativa, não
complemento. Somar os dois contaria o imposto duas vezes.

**O que o sistema faz com a tabela é conferir, não calcular.** `fiscal apurar`
aponta o código que não existe, o par CST × `cClassTrib` que não casa, a
vigência fora da data de emissão, o código proibido no modelo do documento e o
grupo exigido que não veio. Cada um deles é uma rejeição na autorização. O que
ele **não** faz é dizer qual seria o código certo: isso depende do
enquadramento legal do item, e é decisão de quem escritura.

Também não recalcula tributo a partir da redução da tabela. `pRedIBS` e
`pRedCBS` estão no JSON e ficam disponíveis para comparação com o que o
emitente declarou em `pRedAliq`/`pAliqEfet`, mas o sistema **não faz essa
comparação hoje**: decidir o que significa uma divergência exigiria separar
redução legal de benefício estadual, e a resposta não é a mesma. O valor que a
apuração soma continua sendo o destacado no documento.

## Alíquotas padrão

Do item 05 do IT 2025.002 v1.50:

| Ano | IBS estadual | IBS municipal | CBS |
|---|---|---|---|
| 2026 | 0,1% | **0%** | 0,9% |
| 2027 | 0,05% | 0,05% | aguarda legislação |
| 2028 | 0,05% | 0,05% | aguarda legislação |
| 2029 em diante | aguarda legislação | aguarda legislação | aguarda legislação |

**Em 2026 a parcela municipal é zero:** os 0,1% do IBS são todos estaduais.
Repartir "meio a meio" pareceria razoável e mandaria dinheiro para o ente
errado; a repartição igual só começa em 2027. Por isso `aliquotas_padrao(2027)`
devolve `None` para a CBS em vez de zero — zero seria uma alíquota, e o que
existe é uma alíquota ainda não fixada. Cada ente define a sua por lei própria
(art. 14 da LC 214/2025); sem lei, vale a alíquota de referência do Senado
(art. 18).

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
do campo (UB…) anotado no código onde a decisão dependeu dele. Em 2026-08-02 a
v1.51 foi baixada e conferida — ela altera regras de validação e o cronograma
da UB12-10, e não mexe em campo nenhum.

**As tabelas também.** No mesmo dia o portal entregou o IT 2025.002 v1.50 e as
planilhas `cClassTrib 2026-06-22.xlsx` e `cCredPres_2026-06-22.xlsx`, que estão
versionadas em `dados/oficiais/`. É delas que o programa deriva a tabela — não
de transcrição.

`nfe.fazenda.gov.br` seguiu fora do ar; o mirror que respondeu foi:

- Portal DF-e da SVRS — documentos técnicos da NF-e:
  <https://dfe-portal.svrs.rs.gov.br/Nfe/Documentos>
- Tabela de Classificação Tributária (SVRS):
  <https://dfe-portal.svrs.rs.gov.br/DFE/TabelaClassificacaoTributaria>
- Tabela de Crédito Presumido (SVRS):
  <https://dfe-portal.svrs.rs.gov.br/DFE/TabelaCreditoPresumido>

**A semântica dos códigos deixou de ser fonte secundária.** Ela vinha do que
se lia por aí, e por isso os códigos eram listados e nunca interpretados. Agora
vem da planilha oficial. Duas leituras anteriores estavam erradas: o CST 200 é
**"Alíquota reduzida"**, não "alíquota zero ou reduzida" — não existe CST de
alíquota zero —, e o 810 é "Ajuste de IBS na ZFM", no singular, com um único
código de classificação.

**Isso não torna a tabela permanente.** Ela muda por ato normativo, e é por isso
que a versão vem embutida com ela: a defasagem tem de ser visível, porque tabela
velha responde exatamente como tabela nova. `sped-hub fiscal tabelas` mostra a
idade em dias; passados **180 dias**, o CI fica vermelho e a apuração avisa em
todo resultado (REGRA 8). Atualizar é trocar a planilha em `dados/oficiais/` e
rodar `scripts/gerar_tabelas_ibscbs.py`; o CI recusa planilha trocada sem
regerar, e a data vem do **nome do arquivo oficial** — empurrá-la à mão é a
versão em uma linha de mentir sobre a procedência.

Os **valores** de alíquota continuam sendo dado de entrada, lido do XML — o
sistema não os calcula nem os presume. O que a tabela acrescentou foi
conferência, não cálculo.

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
- consumo de `gTransfCred` (UB106), `gAjusteCompet` (UB112), `gEstornoCred`
  (UB116) e `gpBioDiferenca` — eles são lidos e medidos, mas cada um exige
  decidir a que competência pertence, e isso é de quem escritura;
- **correção** da classificação inválida: a conferência aponta o erro e não
  diz qual seria o código certo;
- tratamento do split payment;
- regimes específicos e diferenciados.

Ver [`roadmap.md`](roadmap.md).
