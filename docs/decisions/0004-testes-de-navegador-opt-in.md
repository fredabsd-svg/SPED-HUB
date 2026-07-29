# ADR 0004 — Testes de navegador fora da execução padrão

## Contexto

`tests/test_e2e_playwright.py` nunca fez parte de um CI que passasse. Antes
das correções da Fase 17 os testes erravam já na coleta, por um caminho
absoluto de outra máquina (`/workspace/repo/SPED-HUB`). Corrigido isso, eles
passaram a rodar nos runners do GitHub — que têm `google-chrome`
pré-instalado — e a falhar.

Parte das falhas era defeito real do produto, e foi corrigida: login e
registro nunca redirecionavam, e a senha ia para a URL sem JavaScript.
Outra parte é defeito de harness: locators ambíguos e um servidor de teste
que para de responder no meio da suíte, por causa ainda não diagnosticada.

## Decisão

Os testes de navegador rodam sob o marcador `e2e`, fora do `pytest` padrão:

```bash
pytest            # suíte normal
pytest -m e2e     # só navegador
pytest -m ""      # tudo
```

## Alternativas descartadas

**Mantê-los bloqueando o CI.** Deixaria o pipeline vermelho por defeitos de
harness, e pipeline permanentemente vermelho deixa de significar alguma
coisa — foi exatamente o que aconteceu com o lint (ADR 0002).

**Removê-los.** Eles encontraram dois defeitos reais de produto. Descartar a
única cobertura de fluxo completo seria perder mais do que se ganha.

**Marcá-los como `xfail`.** Esconderia a falha em vez de separar o tier.

## Consequências

**Mais fácil:** o pipeline volta a significar "o código está bom".

**Mais difícil:** a cobertura de fluxo completo depende de alguém rodar
`pytest -m e2e` deliberadamente. Enquanto o harness não for consertado, uma
regressão de interface pode passar.

Esta decisão é explicitamente temporária. A dívida está em
`docs/roadmap.md`.
