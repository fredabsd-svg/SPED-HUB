# escrituracoes

## O que faz

Gera arquivos SPED a partir dos documentos importados. É o caminho inverso de
`parsers`, que **lê** arquivos prontos.

Existem dois geradores:

- **EFD ICMS/IPI** — blocos 0, C, E e 9;
- **EFD-Contribuições** — blocos 0, C, M e 9.

Os dois montam o arquivo a partir da camada efetiva — o normalizado mais os
ajustes. O que o operador corrigiu na tela é o que vai para o Fisco, e o XML
original continua intocado para conferência.

O que os dois têm em comum vive em `base.py`: formatação do leiaute, estrutura
de registro e as contagens do bloco 9. Essa última é a razão principal de a
base existir — é onde gerador próprio erra, e acertá-la numa escrituração e
errá-la na seguinte seria o resultado natural de duplicar o código.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `GeradorEFDICMS(session, empresa=, data_inicio=, data_fim=, cod_fin=)` | Monta a EFD ICMS/IPI do período. |
| `GeradorEFDContribuicoes(session, empresa=, data_inicio=, data_fim=, tipo_escrituracao=)` | Monta a EFD-Contribuições do período. |
| `.gerar()` | Devolve `ResultadoGeracao`; levanta `CampoObrigatorioAusente`. |
| `ResultadoGeracao.texto()` | O arquivo, com CRLF. |
| `ResultadoGeracao.avisos` | O que a apuração não cobre — para ser lido antes de transmitir. |
| `ResultadoGeracao.contagem_por_tipo()` | Quantos registros de cada tipo. |
| `GeradorBase` | O que os geradores compartilham; base de um gerador novo. |
| `Registro` | Uma linha, com os campos ainda em lista. |
| `formatar_valor` / `formatar_data` | Vírgula decimal e `ddmmaaaa`. |
| `CampoObrigatorioAusente` | Falta cadastro sem o qual o arquivo sairia errado. |
| `COD_VER` | Versão do leiaute da EFD ICMS/IPI declarada no 0000. |
| `REGIMES` | Os valores válidos de `cod_inc_trib` (registro 0110). |
| `ATIVIDADES` | Os valores válidos de `ind_ativ_contribuicoes` (IND_ATIV do 0000). |

## O que não faz

Muito, e é preciso saber antes de usar.

**Na EFD ICMS/IPI:**

- **inventário (bloco H), ativo imobilizado (bloco G) e o bloco 1 inteiro**;
- **documentos de serviço, energia, comunicação e transporte** — C500, D100 e
  vizinhos. Só o C100 (mercadorias) está coberto;
- **ajustes de apuração pela tabela 5.1.1** (E111 e seguintes);
- **substituição tributária apurada** (E200 e seguintes);
- **saldo credor de período anterior**.

**Na EFD-Contribuições:**

- **bloco A (serviços/NFS-e)** — a Central ainda não importa NFS-e;
- **blocos D (transporte), F (demais operações) e I (financeiras)**;
- **créditos extemporâneos, ajustes e o bloco 1 inteiro**;
- **monofásico, substituição, alíquota por unidade e regimes especiais** — a
  apuração usa o valor destacado no documento, qualquer que seja o CST;
- **retenções na fonte**.

Em ambos, a apuração é a soma direta dos documentos escriturados. Empresa com
ajuste, benefício ou saldo credor precisa conferir e complementar; o
`ResultadoGeracao` traz aviso explícito sobre isso.

## Depende de / quem depende

Depende de `db.models` e de `documentos.ajustes` (a camada efetiva); da stdlib,
`decimal` e `collections`. Quem depende: nada ainda — nenhuma rota ou comando
expõe os geradores.

## Decisões não óbvias e armadilhas

- **No regime cumulativo não há crédito.** A empresa que apura pelo lucro
  presumido paga PIS e Cofins sobre a receita e não desconta nada das compras.
  Um gerador que somasse os créditos das entradas ali produziria contribuição a
  menor num arquivo **estruturalmente válido** — o validador aceita, porque não
  sabe o regime da empresa, e a diferença volta como cobrança com multa. Por
  isso `cod_inc_trib` é cadastro obrigatório e não tem default: não há palpite
  razoável, e o palpite errado é caro. Quando o regime é cumulativo o resultado
  traz aviso dizendo que os créditos **não** foram descontados — o silêncio
  faria parecer esquecimento.
- **As contagens do bloco 9 se contam.** O `9900` conta os registros do próprio
  bloco 9, inclusive os `9900` que ainda vão ser escritos, o `9990` e o `9999`;
  o `X990` de cada bloco conta a si mesmo; o `9999` conta a própria linha. É o
  erro mais comum de gerador próprio, e o validador recusa o arquivo inteiro
  sem apontar a linha. Há teste que confere cada `9900` contra o que está no
  arquivo, não contra o gerador — nos dois geradores.
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
- **`IND_ATIV` tem o mesmo nome nas duas escriturações e tabelas diferentes.**
  Na EFD ICMS/IPI a resposta é binária: 0 = industrial ou equiparado, 1 =
  outros. Na EFD-Contribuições são seis valores — 0 industrial, **1 prestador
  de serviços**, 2 comércio, 3 PJ dos §§ 6º, 8º e 9º do art. 3º da Lei
  9.718/98, 4 imobiliária, 9 outros. Copiar a resposta de uma para a outra
  declararia prestadora de serviços toda empresa de comércio que respondeu
  "1 = outros" pensando na EFD ICMS/IPI, e o validador aceitaria. Por isso são
  duas colunas: `ind_ativ` e `ind_ativ_contribuicoes`, cada uma com sua tabela,
  sem conversão automática entre elas.
- **`IND_NAT_PJ` sai fixo como `00`** (sociedade empresária em geral).
  Cooperativa (`01`) e entidade que apura o PIS/Pasep sobre a folha de salários
  (`02`) precisam de correção à mão — o resultado avisa em toda geração.
- **Participantes, unidades e itens são derivados dos documentos.** Já estão
  dentro das notas; pedir recadastro seria pedir para divergir. Quando o mesmo
  código aparece com descrições diferentes, prevalece a **primeira** ocorrência
  — sem essa regra a escolha dependeria da ordem do banco, e o mesmo período
  geraria arquivos diferentes.

## Como testar isoladamente

```bash
pytest tests/test_gerador_efd_icms.py tests/test_gerador_efd_contribuicoes.py -q
```
