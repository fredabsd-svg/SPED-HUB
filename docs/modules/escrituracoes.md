# escrituracoes

## O que faz

Gera arquivos SPED a partir dos documentos importados. É o caminho inverso de
`parsers`, que **lê** arquivos prontos.

Hoje existe a **EFD ICMS/IPI**: blocos 0, C, E e 9, montados a partir da camada
efetiva — o normalizado mais os ajustes. O que o operador corrigiu na tela é o
que vai para o Fisco, e o XML original continua intocado para conferência.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `GeradorEFDICMS(session, empresa=, data_inicio=, data_fim=, cod_fin=)` | Monta o arquivo do período. |
| `.gerar()` | Devolve `ResultadoGeracao`; levanta `CampoObrigatorioAusente`. |
| `ResultadoGeracao.texto()` | O arquivo, com CRLF. |
| `ResultadoGeracao.avisos` | O que a apuração não cobre — para ser lido antes de transmitir. |
| `ResultadoGeracao.contagem_por_tipo()` | Quantos registros de cada tipo. |
| `Registro` | Uma linha, com os campos ainda em lista. |
| `formatar_valor` / `formatar_data` | Vírgula decimal e `ddmmaaaa`. |
| `CampoObrigatorioAusente` | Falta cadastro sem o qual o arquivo sairia errado. |
| `COD_VER` | Versão do leiaute declarada no 0000. |

## O que não faz

Muito, e é preciso saber antes de usar:

- **inventário (bloco H), ativo imobilizado (bloco G) e o bloco 1 inteiro**;
- **documentos de serviço, energia, comunicação e transporte** — C500, D100 e
  vizinhos. Só o C100 (mercadorias) está coberto;
- **ajustes de apuração pela tabela 5.1.1** (E111 e seguintes);
- **substituição tributária apurada** (E200 e seguintes);
- **saldo credor de período anterior**;
- **EFD-Contribuições** — segue no `docs/roadmap.md`.

A apuração do E110 é a soma direta dos débitos e créditos dos documentos
escriturados. Empresa com ajuste, benefício ou saldo credor precisa conferir e
complementar; o `ResultadoGeracao` traz aviso explícito sobre isso.

## Depende de / quem depende

Depende de `db.models` e de `documentos.ajustes` (a camada efetiva); da stdlib,
`decimal` e `collections`. Quem depende: nada ainda — nenhuma rota ou comando
expõe o gerador.

## Decisões não óbvias e armadilhas

- **As contagens do bloco 9 se contam.** O `9900` conta os registros do próprio
  bloco 9, inclusive os `9900` que ainda vão ser escritos, o `9990` e o `9999`;
  o `X990` de cada bloco conta a si mesmo; o `9999` conta a própria linha. É o
  erro mais comum de gerador próprio, e o validador recusa o arquivo inteiro
  sem apontar a linha. Há teste que confere cada `9900` contra o que está no
  arquivo, não contra o gerador.
- **O C190 sai dos mesmos valores que alimentaram os C170.** O validador
  confere o consolidado contra a soma dos itens; uma segunda leitura poderia
  divergir da primeira.
- **Zero vira campo vazio.** O leiaute trata ausente e zero como a mesma coisa
  na maioria dos campos, e `0,00` onde se espera vazio gera advertência.
- **Arredondamento é meio para cima, não para o par.** O padrão do
  `Decimal.quantize` — e do `round` do Python — arredondaria 2,665 para 2,66.
  Note que 2,675 **não** discrimina os dois modos: nos dois dá 2,68.
- **O arquivo usa CRLF.** Alguns validadores recusam o arquivo inteiro com LF,
  sem dizer por quê.
- **`ind_perfil` e `ind_ativ` são cadastro, não default.** O validador aceita um
  enquadramento errado, porque não tem como saber qual é o certo — o erro só
  aparece em intimação. O gerador recusa gerar sem eles.
- **Participantes, unidades e itens são derivados dos documentos.** Já estão
  dentro das notas; pedir recadastro seria pedir para divergir. Quando o mesmo
  código aparece com descrições diferentes, prevalece a **primeira** ocorrência
  — sem essa regra a escolha dependeria da ordem do banco, e o mesmo período
  geraria arquivos diferentes.

## Como testar isoladamente

```bash
pytest tests/test_gerador_efd_icms.py -q
```
