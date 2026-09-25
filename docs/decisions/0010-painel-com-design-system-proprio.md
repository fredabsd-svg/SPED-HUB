# ADR 0010 — O painel ganha um design system próprio e menu lateral

## Contexto

O painel nasceu com uma barra superior verde-tinta, títulos na Source Serif 4
e a paleta "Tinta & Latão" copiada no `<style>` de cada página: o
`base.html` e as quatro páginas avulsas (comparar, layout, chaves de API,
webhooks) declaravam as mesmas cores, cada uma por conta própria, e dezenas de
estilos inline dos templates liam variáveis como `--gray-500` e `--primary`.

Quem usa achou o resultado datado — "quero algo com cara de SaaS moderno". E
a revisão da interface achou problemas que não eram só de gosto: gráficos que
cresciam sem limite, valores fora do formato brasileiro, endividamento
saudável pintado de vermelho, e a navegação por menus suspensos que, com
catorze telas, escondia metade delas atrás de um clique.

## Decisão

1. **Um design system só, em `src/dashboard/static/app-shell.css`.** Tokens
   no topo (neutros derivados da tinta, verde vivo para o que é clicável,
   latão só para o que é gerado automaticamente, cores semânticas, escala
   tipográfica, grade de 4 px, raios e sombras) e os componentes em seguida.
   As variáveis antigas continuam existindo, apontando para os tokens novos:
   os estilos inline das telas fiscais acompanham o tema sem ser reescritos.
2. **Menu lateral fixo + barra superior com a trilha da página.** As catorze
   telas ficam visíveis, agrupadas em Contábil, Fiscal e Sistema, com ícones
   de traço desenhados para o projeto (`partials/icones.html`, SVG inline). Em
   tela estreita o menu recolhe atrás de um botão (`static/app-shell.js`).
3. **Uma família na interface.** Títulos e texto em Source Sans 3; a Source
   Serif 4 fica para o nome da marca e para os PDFs. A identidade dos
   relatórios exportados (Fase 22) não muda.
4. **Destaques automáticos no painel** (`src/dashboard/destaques.py`): frases
   sobre os mesmos totais dos cartões — o balanço fecha, houve lucro e com que
   margem, quanto pesa o passivo, como o ativo variou. É conta sobre valor
   importado, nunca estimativa, e o rodapé do cartão diz isso.

## Alternativas descartadas

**Framework de CSS (Tailwind, Bootstrap).** Pelo CDN, é o que a §4.3 proíbe —
e o ADR 0003 conta o que acontece quando o CDN não carrega. Vendorizado com
etapa de build, traria Node para um projeto que hoje não tem nenhum passo de
compilação de front-end; o ganho sobre um arquivo de tokens é pequeno para
catorze telas.

**Biblioteca de ícones (Lucide, Font Awesome).** Uma fonte de ícones é mais um
arquivo para versionar com checksum e um peso fixo por página; trinta ícones
desenhados na mesma grade cabem num partial e herdam a cor do texto.

**Manter a barra superior e só trocar cores.** Resolve o "datado" pela metade:
com catorze destinos, menu suspenso obriga a abrir grupo por grupo para achar
a tela, e é isso que o menu lateral elimina.

## Consequências

- Mudar a aparência é mudar um token em um arquivo; página nova que carregue
  `app-shell.css` nasce no tema. `tests/test_identidade_dashboard.py` cobra
  que as cinco páginas o carreguem e que a tinta siga como cor primária.
- As páginas avulsas continuam com seus `<style>` locais; o design system
  vence por ser carregado depois. Consolidá-las no `base.html` é trabalho
  separado — até lá, estilo novo nelas precisa conferir se não é sobrescrito.
- O shell ocupa 260 px à esquerda em telas largas. Telas muito densas (o
  Diário, a tela do documento) ganham menos largura útil que antes.
- Os destaques são uma superfície nova de texto gerado: regra nova entra com
  teste que confere a conta, não a frase (`tests/test_destaques_do_painel.py`).
