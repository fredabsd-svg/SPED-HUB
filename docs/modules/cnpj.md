# cnpj

## O que faz

Normaliza, formata e confere o CNPJ — inclusive o **alfanumérico**, cuja
primeira inscrição a Receita emitiu em **31 de julho de 2026** (IN RFB
2.229/2024). O formato mantém as catorze posições; o que muda é que as doze
primeiras passam a aceitar letras maiúsculas de A a Z, e os dois dígitos
verificadores seguem numéricos.

O ponto perigoso não é o cálculo, é a normalização. Tirar "tudo que não é
dígito" de um CNPJ alfanumérico não devolve um CNPJ errado: devolve oito
posições que, completadas, são a inscrição de **outra** pessoa jurídica, com
aparência perfeita. Por isso `normalizar` remove só a pontuação que o formato
prevê — ponto, barra, hífen e espaço — e nada mais.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `normalizar(bruto)` | Sem pontuação e em maiúsculas, preservando letras e zeros à esquerda. Não valida. |
| `digitos_verificadores(base)` | Os dois DV das doze primeiras posições, pelo módulo 11 sobre valores ASCII. |
| `bem_formado(cnpj)` | Catorze posições, as doze primeiras alfanuméricas e o DV numérico. |
| `valido(cnpj)` | Bem formado **e** com o dígito fechando. |
| `alfanumerico(cnpj)` | Tem letra na base — ou seja, é do formato novo. |
| `formatar(cnpj)` | `12ABC34501DE35` → `12.ABC.345/01DE-35`. |

## Procedência

Algoritmo e exemplo conferidos contra "CNPJ Alfanumérico — Perguntas e
Respostas", da Receita Federal, pergunta 14, em 2026-08-20 (§8.1). O exemplo
resolvido pela própria Receita — `12.ABC.345/01DE` com DV `35` — está em
`tests/test_cnpj.py` como âncora.

A regra `resto < 2 → 0` **não** está escrita na cartilha. Ela é forçada por
outra afirmação oficial: "os atuais números permanecerão válidos assim como os
seus dígitos verificadores". Sem essa regra o dígito seria 10 ou 11 em duas
casas, e todo CNPJ existente que caia nesse caso deixaria de validar. A
propriedade virou teste.

## Depende de / quem depende

Não depende de nada do projeto — só de `re`.

Usado por `ecd_importer` (o CNPJ do registro 0000 é o que **cria** a empresa
no banco) e por `logging_config` indiretamente, cujo sanitizador precisou
aprender o formato novo para não deixar o CNPJ inteiro no log.

## O que não faz

Não recusa CNPJ inválido em lugar nenhum: `valido` existe e não é chamado no
caminho de importação. É deliberado — arquivo de terceiro se guarda como veio,
e duas das fixtures do próprio projeto têm dígito que não fecha. Quando houver
uma tela de cadastro manual de empresa, é ali que a conferência entra.

## Decisões não óbvias e armadilhas

**`normalizar` não valida, e é de propósito.** Quem importa arquivo de
terceiro precisa guardar o que veio mesmo quando o dígito não fecha —
inclusive porque duas fixtures deste próprio repositório têm DV inválido.
Normalizar e conferir são passos separados.

**O `tipo` do `ecd_v9.yml` diz ao parser como ler, não o que o manual
declara.** Nos três casos em que as duas coisas divergem de propósito
(`TIP_ECD`, `IND_CENTRALIZADA`, `IND_TIP` — indicadores de uma posição que
lidos como número virariam `0.0` e `1.0`), a divergência está escrita no
cabeçalho do arquivo e é cobrada por `tests/test_cnpj.py`. Divergência não
declarada é defeito: foi assim que o CNPJ do `0000` ficou como numérico.

**Mascarar de mais no log é a troca certa.** Aceitar letra no sanitizador
amplia o que casa, e algum identificador de catorze posições terminado em dois
algarismos pode ser mascarado sem ser CNPJ. Log mascarado demais se relê pela
origem; log de menos não se desfaz.

## Como testar isoladamente

```
python -m pytest tests/test_cnpj.py
```

São trinta e quatro asserções sem banco, sem rede e sem fixture: o módulo é
uma função pura de texto. O caminho de ponta a ponta — uma ECD de empresa com
CNPJ alfanumérico importada pela linha de comando — está em
`tests/test_portas_de_entrada.py::TestCnpjAlfanumericoPelaCLI`.
