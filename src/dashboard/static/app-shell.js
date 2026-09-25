/* Menu lateral em telas estreitas: abre por cima do conteúdo, fecha com
   Esc, com o fundo escurecido ou com o botão de fechar, e devolve o foco a
   quem abriu. Em tela larga o menu fica sempre visível e nada disto age. */
(function () {
    "use strict";

    const raiz = document.documentElement;
    const abrir = document.querySelector("[data-menu-abrir]");
    const menu = document.getElementById("app-sidebar");
    const fundo = document.querySelector(".sidebar-backdrop");
    if (!abrir || !menu) return;

    function definir(aberto) {
        raiz.classList.toggle("menu-aberto", aberto);
        abrir.setAttribute("aria-expanded", aberto ? "true" : "false");
        if (fundo) fundo.hidden = !aberto;
        if (aberto) {
            const primeiro = menu.querySelector(".nav-item, a, button");
            if (primeiro) primeiro.focus();
        } else {
            abrir.focus();
        }
    }

    abrir.addEventListener("click", function () { definir(true); });
    document.querySelectorAll("[data-menu-fechar]").forEach(function (alvo) {
        alvo.addEventListener("click", function () { definir(false); });
    });
    document.addEventListener("keydown", function (evento) {
        if (evento.key === "Escape" && raiz.classList.contains("menu-aberto")) definir(false);
    });
    // Abrir o acesso rápido pelo menu móvel fecha o menu por baixo do diálogo.
    menu.querySelectorAll("[data-quick-open]").forEach(function (botao) {
        botao.addEventListener("click", function () {
            if (!raiz.classList.contains("menu-aberto")) return;
            raiz.classList.remove("menu-aberto");
            abrir.setAttribute("aria-expanded", "false");
            if (fundo) fundo.hidden = true;
        });
    });
})();
