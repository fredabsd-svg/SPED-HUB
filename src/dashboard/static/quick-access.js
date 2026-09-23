(function () {
    "use strict";

    const dialog = document.querySelector("[data-quick-dialog]");
    if (!dialog || typeof dialog.showModal !== "function") return;

    const filter = dialog.querySelector("[data-quick-filter]");
    const entries = Array.from(dialog.querySelectorAll("[data-quick-entry]"));
    const empty = dialog.querySelector("[data-quick-empty]");
    const status = dialog.querySelector("[data-quick-status]");
    const shortcutHint = document.querySelector("[data-shortcut-hint]");
    let invoker = null;

    if (shortcutHint && /Mac|iPhone|iPad|iPod/.test(navigator.platform || "")) {
        shortcutHint.textContent = "⌘ K";
    }

    function normalize(value) {
        return value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
    }

    function filtrar() {
        const termo = normalize(filter.value.trim());
        let encontrados = 0;
        entries.forEach(function (entry) {
            const corresponde = normalize(entry.textContent).includes(termo);
            entry.hidden = !corresponde;
            if (corresponde) encontrados += 1;
        });
        empty.hidden = encontrados !== 0;
        status.textContent = encontrados === 1
            ? "Uma tela encontrada."
            : encontrados + " telas encontradas.";
    }

    function abrir(botao) {
        invoker = botao || document.activeElement;
        filter.value = "";
        filtrar();
        dialog.showModal();
        filter.focus();
    }

    document.querySelectorAll("[data-quick-open]").forEach(function (botao) {
        botao.addEventListener("click", function () { abrir(botao); });
    });

    document.addEventListener("keydown", function (evento) {
        if ((evento.ctrlKey || evento.metaKey) && evento.key.toLowerCase() === "k") {
            const alvo = evento.target;
            if (alvo.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(alvo.tagName)) return;
            evento.preventDefault();
            if (!dialog.open) abrir();
        }
    });

    filter.addEventListener("input", filtrar);
    filter.addEventListener("keydown", function (evento) {
        if (evento.key === "ArrowDown") {
            const primeiro = entries.find(function (entry) { return !entry.hidden; });
            const link = primeiro && primeiro.querySelector("a");
            if (link) {
                evento.preventDefault();
                link.focus();
            }
        }
    });

    entries.forEach(function (entry) {
        const link = entry.querySelector("a");
        link.addEventListener("keydown", function (evento) {
            if (evento.key !== "ArrowDown" && evento.key !== "ArrowUp") return;
            const visible = entries.filter(function (item) { return !item.hidden; });
            const current = visible.indexOf(entry);
            const next = evento.key === "ArrowDown" ? visible[current + 1] : visible[current - 1];
            evento.preventDefault();
            if (next) next.querySelector("a").focus();
            else if (evento.key === "ArrowUp") filter.focus();
        });
    });

    dialog.addEventListener("click", function (evento) {
        if (evento.target === dialog) dialog.close();
    });
    dialog.addEventListener("close", function () {
        if (invoker && typeof invoker.focus === "function") invoker.focus();
    });

    document.querySelectorAll(".nav-group").forEach(function (grupo) {
        grupo.addEventListener("toggle", function () {
            if (!grupo.open) return;
            document.querySelectorAll(".nav-group[open]").forEach(function (outro) {
                if (outro !== grupo) outro.open = false;
            });
        });
    });

    document.addEventListener("click", function (evento) {
        document.querySelectorAll(".nav-group[open]").forEach(function (grupo) {
            if (!grupo.contains(evento.target)) grupo.open = false;
        });
    });
})();
