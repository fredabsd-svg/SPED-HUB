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

## O que não faz

Não classifica, não altera em massa e não gera escrituração — ver
`docs/roadmap.md`. Não lê NFS-e: cada provedor municipal precisa do seu
adaptador. Não valida códigos fiscais contra as tabelas oficiais, e **não
calcula tributo nenhum**: os valores de CBS, IBS e IS são lidos do XML, nunca
presumidos.

## Depende de / quem depende

Depende de `db.models` e da stdlib (`xml.etree.ElementTree`, `hashlib`) — sem
dependência nova. Quem depende: nada ainda; o dashboard não expõe a Central.

## Decisões não óbvias e armadilhas

- **A terceira camada é calculada, não gravada.** Gravar o valor final numa
  coluna faria as três camadas divergirem no primeiro `UPDATE` escrito fora do
  fluxo. Calculando, desfazer um lote é apagar seus ajustes, e "por que este
  registro saiu assim?" se responde listando os ajustes daquele campo.
- **`AjusteFiscal` é aditivo.** Cada linha guarda o valor anterior, a origem
  (`regra` ou `usuario`) e o lote. Nenhum ajuste sobrescreve outro; o efetivo
  é o mais recente que alcança o campo.
- **Adaptadores, não um parser único.** A NF-e é nacional e estável; a NFS-e
  varia por município e por provedor. Um parser único viraria uma cascata de
  condicionais que ninguém altera sem quebrar outro município.
- **Os tributos da reforma convivem com os antigos** em `ItemDocumentoFiscal`,
  não os substituem: os dois regimes coexistem de 2026 a 2032. Ver
  [`../reforma-tributaria.md`](../reforma-tributaria.md), inclusive quanto à
  procedência das informações — o portal oficial da NF-e não respondeu na
  consulta, e os códigos vieram de fontes secundárias.
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
- **O ICMS vem embrulhado na variante** (`ICMS00`, `ICMS60`, `ICMSSN102`…). O
  adaptador desce no primeiro filho em vez de listar as ~20 formas, que mudam
  a cada nota técnica.

## Como testar isoladamente

```bash
pytest tests/test_documentos_fiscais.py -q  # adaptador, reforma, XML hostil, duplicidade
pytest tests/test_migrations.py -q          # o schema da migração bate com os modelos
```
