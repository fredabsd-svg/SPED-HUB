# ADR 0002 — Versão exata para ferramentas de lint e format

## Contexto

`pyproject.toml` declarava `ruff>=0.4` e `black>=24.0`. O CI instalava a
versão do dia. Quando o ruff 0.16 ampliou o conjunto de regras default e o
black 26 mudou o estilo, o pipeline passou a falhar sozinho, sem ninguém
tocar em código.

O efeito foi pior que uma falha isolada: o CI ficou vermelho em 14 execuções
consecutivas. Como o passo de lint roda antes dos testes, o `pytest` nunca
chegou a executar — e as afirmações de "372 testes 100% passando" no README
e no arquivo de continuidade nunca foram verificadas por ninguém.

## Decisão

`ruff` e `black` são declarados com `==`. O conjunto de regras é declarado
explicitamente em `[tool.ruff.lint].select`, em vez de herdado dos defaults
da versão instalada.

Atualização de qualquer uma das duas é decisão deliberada, em commit próprio.

## Alternativas descartadas

**Manter as faixas abertas e corrigir quando quebrar.** Foi o que estava em
vigor. O custo não é a correção, é o intervalo em que o pipeline vermelho
deixa de significar alguma coisa.

**Remover o lint do CI.** Elimina o sintoma e perde a verificação.

## Consequências

**Mais fácil:** o pipeline só fica vermelho por causa do código.

**Mais difícil:** atualizar as ferramentas passa a ser uma tarefa explícita.
Sem alguém fazendo isso periodicamente, o projeto congela em versões antigas.
