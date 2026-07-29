# Fontes dos relatórios exportados — "Tinta & Latão"

Identidade tipográfica dos PDFs: **Source Serif 4** (títulos, números de
destaque) sobre **Source Sans 3** (corpo, tabelas, rodapés). Licença SIL OFL
1.1 — os textos completos estão em `LICENSE-SourceSerif4.md` e
`LICENSE-SourceSans3.md`.

| Família | Versão | Origem |
|---|---|---|
| Source Serif 4 | 4.005R | `github.com/adobe-fonts/source-serif`, release `4.005R`, `TTF/` estáticos |
| Source Sans 3 | 3.052R | `github.com/adobe-fonts/source-sans`, release `3.052R`, `TTF/` estáticos |

Pesos versionados: Regular, Semibold, Bold e Italic de cada família — os que
o `print.css` declara em `@font-face`. Não versione o zip do download nem a
família variável: só o WeasyPrint consome estes arquivos, e ele resolve os
pesos a partir dos estáticos.

As fontes ficam **no repositório** pela mesma razão das bibliotecas de
front-end em `static/vendor/` (§4.3): o PDF precisa sair idêntico numa
máquina sem acesso à internet.

O dashboard web não usa estas fontes — as páginas caem na pilha do sistema.
