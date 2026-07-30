# documentos

## O que faz

**Nada, ainda.** O pacote está vazio de código executável — só a docstring que
fixa a arquitetura da Central de Documentos Fiscais antes de ela ser
preenchida.

O que já existe da Central é o **modelo de dados**, em `db.models`:
`DocumentoFiscal`, `ItemDocumentoFiscal` e `AjusteFiscal`, criados pela
migração `d5969a68dba0`.

O pacote existe agora, e não depois, porque a decisão que ele registra — as
três camadas — é a que precisa estar certa antes de qualquer importador
escrever a primeira linha no banco. Retrofitar separação de camadas em cima de
dados já gravados é o tipo de coisa que não se faz.

## O que expõe

Nenhum símbolo. A docstring do `__init__.py` descreve:

| Camada | Onde vive | Mutável? |
|---|---|---|
| **Original** | `DocumentoFiscal.xml_original` | Nunca |
| **Normalizado** | colunas de `DocumentoFiscal` e `ItemDocumentoFiscal` | Nunca |
| **Efetivo** | calculado: normalizado + `AjusteFiscal` aplicados | É o resultado |

## O que não faz

Não importa, não normaliza, não classifica e não gera escrituração — nada
disso existe ainda. O pacote também não valida códigos fiscais contra as
tabelas oficiais, e não calcula tributo nenhum: os valores de CBS, IBS e IS
serão lidos do XML, não presumidos.

## Depende de / quem depende

Depende de nada hoje. Quem depende: nada — nenhum módulo o importa.

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

## Como testar isoladamente

```bash
pytest tests/test_migrations.py -q          # o schema da migração bate com os modelos
```

Não há teste do pacote em si: não há comportamento a testar ainda.
