# cli_fiscal

## O que faz

O subcomando `sped-hub fiscal` — a cadeia da Central de Documentos pela linha
de comando, na ordem em que ela acontece:

```
cadastro → regras → importar → documentos → classificar → alterar → ajuste → apurar → espelho → gerar → transmitida → conferir
                                             ↘ desfazer ↙
```

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
| `_filtro(bruto)` | `campo:valor`, `campo:operador:valor` ou `campo:operador`. |
| `_valor_tipado(campo, bruto)` | O texto do terminal no tipo que a coluna espera. |
| `GERADORES` | Os tipos de escrituração que o comando gera. |
| `EXTENSOES` | O que `importar` recolhe ao varrer uma pasta. |
| `DIVERGENTE` | O código de saída 2, de `conferir` e de `espelho`. |

Ações:

| Ação | Para quê |
|---|---|
| `empresas` | As cadastradas, com o cadastro fiscal que decide se podem gerar. |
| `cadastro --empresa [--ind-perfil --ind-ativ --ind-ativ-contribuicoes --cod-inc-trib --ind-nat-pj]` | Mostra ou preenche o cadastro fiscal. Sem campos, é diagnóstico. |
| `importar CAMINHO…` | XML avulso ou pasta, varrida recursivamente por `.xml`. |
| `documentos --empresa [--de --ate]` | Os documentos da Central, com o total. |
| `regras [--acao-regra listar\|criar\|remover]` | Cadastra, lista e desativa as regras de classificação. |
| `classificar --empresa [--de --ate --aplicar]` | O que as regras propõem. Sem `--aplicar`, **não grava**. |
| `alterar --empresa --campo --valor [--filtro --apenas-vazios --confirmar --forcar --motivo]` | Alteração em massa. Sem `--confirmar`, **só simula**. |
| `desfazer --lote` | Reverte um lote inteiro de ajustes. |
| `ajuste --empresa --de --ate [--codigo --valor --descricao]` | Ajustes de apuração (E111). Sem `--codigo`, lista. |
| `apurar --empresa --de --ate` | CBS, IBS e IS do período. Só leitura: **não grava nada**. |
| `espelho --empresa --de --ate [--tipo --saida]` | O arquivo em forma de leitura, **antes** de gerar. Não arquiva. |
| `gerar --empresa --de --ate [--tipo --saida]` | Gera a EFD **e arquiva** a escrituração. |
| `historico [--empresa --transmitidas]` | As escriturações geradas, com hash e a data de entrega. |
| `transmitida --escrituracao [--recibo --forcar]` | Registra qual geração foi a entregue. |
| `conferir --escrituracao [--diff]` | O entregue contra o que sairia agora. |

## Códigos de saída

Isto entra em script de fechamento, então o código é contrato:

| Código | Quando |
|---|---|
| `0` | Correu bem. |
| `1` | Erro — cadastro faltando, empresa inexistente, arquivo ilegível, banco sem schema. |
| `2` | Em `conferir`, o arquivo entregue divergiu do que sairia agora; em `espelho`, alguma conferência falhou. |

O `2` é distinto do `1` de propósito: divergência não é falha. É o que permite
alertar que alguém mexeu num documento depois da entrega, sem confundir as
duas coisas.

## Depende de / quem depende

Depende de `db.models`, `documentos` (importador e ajustes), `escrituracoes`
(geradores e escrituração arquivada) e de `reports.base` para o formato pt-BR
dos valores. Quem depende: `cli.py`, que registra o parser e despacha.

## Decisões não óbvias e armadilhas

- **A condição de uma regra usa a mesma sintaxe do `--filtro`.** As duas
  coisas são a mesma pergunta — "quais documentos casam com isto" —, e duas
  sintaxes para a mesma pergunta acabariam divergindo, com quem usa tendo de
  lembrar qual vale onde. A ação é `campo:valor`, sem operador: ela atribui.
- **Condição sem valor não grava `"valor": None`.** `vazio` e `preenchido` não
  comparam com nada; gravar o nulo faria a regra parecer comparar com nulo.
- **`regras remover` desativa, não apaga.** Uma regra apagada deixaria sem
  explicação os ajustes que ela gerou: o `AjusteFiscal` guarda o nome da
  regra, e quem for auditar o mês vai querer saber qual era a condição. A
  regra desativada continua na listagem, com a coluna `Ativa` em "não" —
  sumir da lista faria parecer que ela nunca existiu.
- **`classificar` e `alterar` não gravam por padrão.** O motor de
  classificação nunca aplica sozinho, e o módulo de massa separa `simular` de
  `confirmar` — inverter isso na CLI desfaria, na porta de entrada, a proteção
  que os motores têm por dentro. Os dois dizem em voz alta que não gravaram: o
  silêncio faria parecer que a operação aconteceu.
- **O que grava imprime o lote e como desfazê-lo.** Uma alteração em massa
  errada estraga o mês inteiro de uma vez, e a reversão tem de estar à mão na
  mesma tela — não no manual.
- **`--valor` é convertido para o tipo da coluna.** Argumento de terminal é
  sempre `str`; sem converter, alterar `base_icms` para `1000` mostraria
  **impacto R$ 0,00** na simulação, porque a diferença entre `0.0` e `"1000"`
  não é numérica — e é justamente o impacto que decide se a alteração passa. A
  conversão reusa o `desserializar` da camada efetiva: duas conversões
  diferentes para o mesmo campo acabariam divergindo.
- **Campo inexistente é recusado antes de simular.** Uma alteração em massa
  com nome errado não alcançaria nada, em silêncio, e pareceria "0 mudanças"
  — indistinguível de um filtro que não casou.
- **O filtro usa dois-pontos, não `=`.** Valor fiscal — NCM, CFOP, CST, CNPJ —
  não tem dois-pontos dentro; o `=` apareceria em descrição de produto.
- **Conflitos de classificação aparecem em bloco próprio.** O motor se recusa
  a resolver empate de prioridade por sorteio; esconder isso faria a
  classificação parecer completa quando ela parou no meio.
- **A confiança da sugestão só é impressa quando não é total.** Repeti-la em
  toda linha esconderia justamente a que merece atenção.
- **`apurar` não grava nada, ao contrário de `gerar`.** Os tributos da Reforma
  ainda não têm obrigação acessória neste sistema; apresentar o número como se
  fosse uma escrituração daria a entender que algo foi entregue. É leitura, e
  é por isso que ele não tem contrapartida arquivada.
- **O Seletivo aparece com travessão na coluna de crédito, não com zero.** Ele
  não tem crédito — `0,00` faria parecer que tem e ficou zerado. O saldo credor
  sai na própria linha do tributo: numa linha à parte, pareceria um quarto
  tributo.
- **`gerar` sempre arquiva, e não existe `--sem-arquivar`.** A ausência é
  deliberada. A terceira camada existe para responder "o que você enviou", e
  um arquivo que sai do sistema sem deixar registro é exatamente o buraco que
  ela fecha. Uma "prévia" que grava em disco é indistinguível de uma entrega
  depois que o arquivo está na mão de alguém. Gerar de novo cria outra
  escrituração — o histórico de tentativas é informação real.
- **`espelho` não arquiva, e não é exceção à regra acima.** A regra vale para
  o que pode ser transmitido. O espelho é prosa — nenhum validador o aceita, e
  ninguém o entrega por engano —, então produzi-lo sem registro não abre o
  buraco que a terceira camada fecha. Arquivá-lo, ao contrário, encheria o
  histórico de linhas que ninguém entregou.
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
- **`cadastro` não usa `choices=` do argparse.** O argparse recusaria com
  código de saída 2, que nesta CLI quer dizer "divergiu" e seria lido como
  divergência por um script de fechamento. E a mensagem dele lista os códigos
  sem as descrições, que é justamente o que importa: ninguém erra `2`, erra o
  significado de `2`. A conferência é própria, e a recusa mostra a tabela.
- **`cadastro` confere todos os campos antes de atribuir qualquer um.** Hoje a
  sessão seria descartada de qualquer jeito ao levantar, mas depender disso é
  depender de quem chama não commitar.
- **`transmitida` existe porque o sistema não transmite.** Quem transmite é o
  programa validador da Receita; a marca vem de fora e precisa ser dita. Sem
  ela, a terceira camada guarda candidatos e não o registro do que foi enviado.
- **`historico` marca a não entregue com travessão, não com vazio.** Campo em
  branco se lê como coluna que não se aplica àquela linha.
- **`conferir` usa o gerador do tipo que foi arquivado**, não um fixo:
  comparar uma EFD-Contribuições com o gerador de ICMS acusaria divergência
  inexistente.
- **`empresas` mostra `ind_perfil` e `cod_inc_trib`.** Descobrir que falta
  cadastro fiscal só na hora de fechar o mês é tarde.
- **A conferência de argumentos obrigatórios vive num dicionário**
  (`OBRIGATORIOS`), não espalhada em cada função: é o que dá a mesma mensagem
  para todos os casos, e ela diz qual ação e qual argumento.

## O que não faz

- Não edita regra existente: a correção é desativar e criar outra, o que
  preserva a explicação dos ajustes que a primeira gerou.
- Não marca qual escrituração foi transmitida: todas ficam guardadas, e o
  sistema não tem como saber qual foi entregue (roadmap).
- Não valida o arquivo contra o validador do Fisco.
- Não autentica nem aplica isolamento de tenant: fala direto com o banco, como
  o resto da CLI.

## Como testar isoladamente

```bash
pytest tests/test_cli_fiscal.py -q
```
