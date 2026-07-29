# ADR 0003 — Bibliotecas de front-end servidas pela aplicação

## Contexto

htmx, Alpine, Chart.js e SortableJS vinham do `cdn.jsdelivr.net` em tempo de
execução. Três problemas decorriam disso.

Sem acesso ao CDN — firewall corporativo, situação concreta em escritório
contábil — o htmx não carregava e os formulários caíam para envio nativo.
Como não declaravam `method`, o navegador usava GET, e **a senha de login ia
para a query string**: histórico do navegador, log de acesso do servidor e
cabeçalho `Referer`.

Apenas o `base.html` fixava versão. Quatro páginas usavam faixas abertas
(`@3`, `@4`, `@1`), que resolvem para o último release do major a cada
carregamento. Na prática o dashboard rodava Alpine 3.14.1 e a página de
webhooks, 3.15.12 — divergência que muda sozinha.

A política de conteúdo (CSP) era obrigada a liberar um domínio externo em
`script-src`.

## Decisão

Os arquivos ficam versionados em `src/dashboard/static/vendor/`, com
`SHA256SUMS` registrado e verificado por teste. Nenhum template referencia
domínio externo.

As versões adotadas são as que o `base.html` já declarava, para que o
dashboard não mude de comportamento — são as outras páginas que passam a
acompanhá-lo.

## Alternativas descartadas

**Fixar as versões no CDN, sem versionar os arquivos.** Resolve a divergência
de versão, não a indisponibilidade nem a CSP.

**Gerenciador de pacotes de front-end (npm) com passo de build.** Introduz
Node na cadeia de build de um projeto que hoje é só Python. O ganho seria
atualização automatizada; o custo, uma dependência de ecossistema inteira.

**Subresource Integrity no CDN.** Protege contra adulteração, não contra
indisponibilidade.

## Consequências

**Mais fácil:** a aplicação funciona em rede restrita; a CSP não precisa de
origem externa; os testes de navegador deixam de depender de rede.

**Mais difícil:** atualizar biblioteca passa a ser manual — baixar, trocar o
nome nos templates, regenerar o `SHA256SUMS`. O repositório carrega 348 KB de
código de terceiros.

**Não resolveu o que se esperava:** a expectativa era que remover a
dependência de rede destravasse os testes de navegador para voltarem ao CI.
Não destravou — eles seguem falhando por defeitos de harness independentes.
