# ADR 0008 — Pronto é o que a porta de entrada alcança

## Contexto

A tabela de fases do `docs/status.md` marcava 64 fases como concluídas, com a
suíte inteira verde. O critério de "concluída", desde a §1.8, era: existe um
teste citado, e ele passa.

Ao aplicar esse critério com uma pergunta a mais — *o teste citado chega à
linha de comando ou à tela?* — 29 das fases concluídas não tinham nenhuma
evidência que chegasse. As fases 9 a 12, por exemplo, citam testes que chamam
a corrotina do handler direto (`asyncio.run(_call_registrar(payload))`): isso
pula o roteamento, a autenticação e o middleware multi-tenant. O handler podia
estar correto e a rota não existir, ou existir sem exigir credencial, e a fase
fechava mesmo assim.

O caso não é hipotético. Ao escrever a primeira leva de testes de porta, um
defeito apareceu na hora: `sped-hub importar-ecd --db outro.db` emitia o
evento `ecd.importada` contra o banco **configurado no processo**, não contra
aquele em que a ECD tinha acabado de entrar. Nenhum assinante lá, evento
nenhum, e nenhum erro em lugar nenhum. Vinte e um testes de webhook, todos
verdes, nenhum passando pela CLI.

Suíte de módulo verde é condição necessária. Nunca foi suficiente.

## Decisão

Uma fase só pode constar como `concluída` no `docs/status.md` quando pelo
menos um dos testes citados como evidência **chamar** uma porta de entrada:
`main([...])` da CLI, a aplicação montada via `TestClient`, ou o navegador.
Importar o módulo da porta não conta; chamar a função da rota direto conta
menos ainda.

Fase que entrega garantia interna — e não capacidade ao usuário — declara isso
com a marca `[interno: motivo]` na célula de evidência. O motivo é
obrigatório: marca vazia é carimbo, e carimbo se aplica sem pensar.

A lista de portas é derivada do repositório (§1.9): os `[project.scripts]` do
`pyproject.toml` mais os módulos com `if __name__ == "__main__"`. Mantida à
mão, ela viraria o lugar onde se acrescenta o módulo órfão para calar o teste.

As duas travas são verificadas no CI, em
`tests/test_regras_projeto.py::TestDefinicaoDePronto`.

## Alternativas descartadas

**Rebaixar as 29 fases para "em andamento".** É o que o critério novo diria ao
pé da letra, e seria a informação errada: as capacidades existem e funcionam.
O que faltava era prova de que funcionam pelo caminho do usuário. Rebaixar
trocaria uma imprecisão por outra, e apagaria a distinção entre "não está
pronto" e "está pronto e mal provado".

**Valer só para fases novas.** Regra que não cobra o passado não cobra nada: o
passado é onde a divergência já está. Foi o que motivou escrever
`tests/test_portas_de_entrada.py` em vez de datar a regra.

**Exigir um teste de porta para cada fase, sem exceção.** O laço do
`worker_runner` não retorna; um teste que entrasse por ele travaria a suíte.
Regra que a máquina não consegue cumprir vira promessa vazia — e promessa
vazia em documentação é o defeito que estas regras existem para evitar. Daí a
marca `[interno: motivo]`, que é declaração, não dispensa.

**Confiar em casamento por substring para detectar a porta.** A primeira
versão do detector procurava `"TestClient"` e `"from src.cli import main"` no
texto do arquivo. Dava falso negativo (`from src import cli` + `cli.main(...)`
escapava) e falso positivo ao mesmo tempo (a mera presença de `src.cli_fiscal`
bastava, mesmo quando o teste só chamava `cli_fiscal.gravar`). A detecção é
por chamada, na árvore sintática.

## Consequências

Fica mais fácil confiar na tabela de fases: "concluída" passa a significar que
alguém percorreu o caminho do usuário e ele funcionou. As 29 fases foram
reavaliadas uma a uma — a maioria já tinha um teste de ponta em outro arquivo,
e ganhou a citação; o que faltava virou `tests/test_portas_de_entrada.py`;
cinco declararam `[interno]` com motivo.

Fica mais difícil fechar fase: além dos testes do módulo, é preciso um que
entre pela CLI ou pela tela. É custo real, e é o custo que separa código
testado de produto que funciona.

Fica mais caro criar módulo novo desligado do produto: ou ele é alcançado a
partir de uma porta, ou ganha um `[project.scripts]`/`__main__` — isto é, uma
forma de ser rodado. Não há mais como declará-lo porta escrevendo uma linha
numa lista.
