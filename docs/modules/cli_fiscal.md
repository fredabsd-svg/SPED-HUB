# cli_fiscal

## O que faz

O subcomando `sped-hub fiscal` — a cadeia da Central de Documentos pela linha
de comando: importar XML, listar o que entrou, gerar a EFD e conferir o que
foi entregue contra o que sairia agora.

Existe porque a suíte fiscal (fases 39 a 45) estava completa e inalcançável:
nenhuma rota nem comando chegava até o importador, os geradores ou a
escrituração arquivada.

Mora fora de `cli.py` porque a cadeia é grande o bastante para não caber junto
com os relatórios contábeis; o `cli.py` registra o parser e despacha.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `registrar(sub)` | Acrescenta o parser `fiscal` à CLI. |
| `cmd_fiscal(args)` | Despacha a ação e traduz falhas em mensagem legível. |
| `conferir_argumentos(args)` | A mensagem de erro por argumento faltando, ou `None`. |
| `gravar(destino, texto)` | Escreve o arquivo SPED sem deixar o Python mexer na quebra de linha. |
| `GERADORES` | Os tipos de escrituração que o comando gera. |
| `EXTENSOES` | O que `importar` recolhe ao varrer uma pasta. |
| `DIVERGENTE` | O código de saída 2, de `conferir`. |

Ações:

| Ação | Para quê |
|---|---|
| `empresas` | As cadastradas, com o cadastro fiscal que decide se podem gerar. |
| `importar CAMINHO…` | XML avulso ou pasta, varrida recursivamente por `.xml`. |
| `documentos --empresa [--de --ate]` | Os documentos da Central, com o total. |
| `gerar --empresa --de --ate [--tipo --saida]` | Gera a EFD **e arquiva** a escrituração. |
| `historico [--empresa]` | As escriturações geradas, com hash. |
| `conferir --escrituracao [--diff]` | O entregue contra o que sairia agora. |

## Códigos de saída

Isto entra em script de fechamento, então o código é contrato:

| Código | Quando |
|---|---|
| `0` | Correu bem. |
| `1` | Erro — cadastro faltando, empresa inexistente, arquivo ilegível, banco sem schema. |
| `2` | Só em `conferir`: o arquivo entregue divergiu do que sairia agora. |

O `2` é distinto do `1` de propósito: divergência não é falha. É o que permite
alertar que alguém mexeu num documento depois da entrega, sem confundir as
duas coisas.

## Depende de / quem depende

Depende de `db.models`, `documentos` (importador e ajustes), `escrituracoes`
(geradores e escrituração arquivada) e de `reports.base` para o formato pt-BR
dos valores. Quem depende: `cli.py`, que registra o parser e despacha.

## Decisões não óbvias e armadilhas

- **`gerar` sempre arquiva, e não existe `--sem-arquivar`.** A ausência é
  deliberada. A terceira camada existe para responder "o que você enviou", e
  um arquivo que sai do sistema sem deixar registro é exatamente o buraco que
  ela fecha. Uma "prévia" que grava em disco é indistinguível de uma entrega
  depois que o arquivo está na mão de alguém. Gerar de novo cria outra
  escrituração — o histórico de tentativas é informação real.
- **`gravar` é função própria por causa de uma falha invisível no Linux.**
  `open` em modo texto sem `newline=""` reescreve `\n` como `\r\n` no Windows;
  o texto do leiaute já vem com `\r\n`, e o resultado é `\r\r\n`, que faz o
  validador recusar o arquivo inteiro sem dizer por quê. **No Linux — onde a
  suíte roda — os dois modos gravam os mesmos bytes**, então conferir o
  arquivo não distingue nada e o que sobra é conferir a chamada. Não é
  preciosismo: foi assim que o entrypoint do nginx quebrou para quem constrói
  no Windows, com toda a verificação automática passando.
- **`importar` varre a pasta só por `.xml`.** Sem o filtro, a pasta de
  downloads do contador encheria o relatório de rejeições de PDF e planilha
  que ninguém mandou importar. Um XML ilegível vira rejeição com motivo e não
  derruba o lote — perder mil notas por causa de uma corrompida seria
  inaceitável no fechamento.
- **Banco sem schema vira mensagem, não traceback.** Quem usa isto é contador:
  um `OperationalError` do SQLAlchemy na tela não diz o que fazer, e o caso
  comum tem resposta de uma linha (`sped-hub migrar`).
- **Valores no formato pt-BR**, reusando o `fmt_moeda` de `reports.base`.
  `f"{v:,.2f}"` daria `1,000.00`, que aqui se lê como outro número.
- **Os avisos da geração são impressos em bloco próprio.** São o canal de
  "leia antes de transmitir" que os geradores usam para dizer o que a apuração
  não cobre; engoli-los seria pior que não gerar.
- **`conferir` usa o gerador do tipo que foi arquivado**, não um fixo:
  comparar uma EFD-Contribuições com o gerador de ICMS acusaria divergência
  inexistente.
- **`empresas` mostra `ind_perfil` e `cod_inc_trib`.** Descobrir que falta
  cadastro fiscal só na hora de fechar o mês é tarde.
- **A conferência de argumentos obrigatórios vive num dicionário**
  (`OBRIGATORIOS`), não espalhada em cada função: é o que dá a mesma mensagem
  para todos os casos, e ela diz qual ação e qual argumento.

## O que não faz

- Não classifica nem aplica alterações em massa — o motor de classificação e o
  `documentos.massa` existem e ainda não têm comando.
- Não marca qual escrituração foi transmitida: todas ficam guardadas, e o
  sistema não tem como saber qual foi entregue (roadmap).
- Não valida o arquivo contra o validador do Fisco.
- Não autentica nem aplica isolamento de tenant: fala direto com o banco, como
  o resto da CLI.

## Como testar isoladamente

```bash
pytest tests/test_cli_fiscal.py -q
```
