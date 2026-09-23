(() => {
    const form = document.getElementById("upload-ecd");
    if (!form) return;

    const fileInput = form.querySelector('input[type="file"]');
    const fileName = document.getElementById("file-name-ecd");
    const result = document.getElementById("upload-result");
    const submitButton = form.querySelector("[data-upload-submit]");
    const spinner = form.querySelector("[data-upload-spinner]");
    const cancelButton = form.querySelector("[data-upload-cancel]");
    let activeJobId = null;

    function setNotice(message, type = "info") {
        const notice = document.createElement("div");
        notice.className = `alert alert-${type} upload-status-message`;
        notice.textContent = message;
        result.replaceChildren(notice);
    }

    function setProgress(message, percent = null) {
        const container = document.createElement("div");
        const notice = document.createElement("p");
        const progress = document.createElement("progress");
        const detail = document.createElement("p");

        notice.className = "alert alert-info upload-status-message";
        notice.textContent = "Importando ECD";
        progress.className = "upload-progress";
        progress.max = 100;
        progress.setAttribute("aria-label", "Progresso da importação ECD");
        if (percent === null) {
            progress.removeAttribute("value");
        } else {
            progress.value = Math.max(0, Math.min(100, Number(percent) || 0));
        }
        detail.className = "text-muted";
        detail.textContent = message;
        container.append(notice, progress, detail);
        result.replaceChildren(container);
    }

    async function readJson(response) {
        let data;
        try {
            data = await response.json();
        } catch {
            throw new Error("O servidor devolveu uma resposta inválida. Tente novamente.");
        }
        if (!response.ok || data.status === "erro") {
            const message = data.mensagem || data.detail;
            throw new Error(
                typeof message === "string" ? message : "Não foi possível processar o arquivo."
            );
        }
        return data;
    }

    function formatCount(value) {
        return new Intl.NumberFormat("pt-BR").format(Number(value) || 0);
    }

    function showSuccess(data) {
        const counts = [
            `${formatCount(data.contas)} contas`,
            `${formatCount(data.lancamentos)} lançamentos`,
            `${formatCount(data.partidas)} partidas`,
        ];
        setNotice(`ECD importada com sucesso: ${counts.join(", ")}.`, "success");

        const link = document.createElement("a");
        link.href = "/";
        link.textContent = "Abrir painel contábil";
        link.className = "btn btn-outline";
        link.style.marginTop = "4px";
        result.append(link);
    }

    async function waitForJob(jobId, pollUrl) {
        while (activeJobId === jobId) {
            await new Promise((resolve) => window.setTimeout(resolve, 850));
            const response = await fetch(pollUrl, {
                credentials: "same-origin",
                headers: { Accept: "application/json" },
            });
            const job = await readJson(response);
            setProgress(job.mensagem || "Processando arquivo ECD…", job.progresso);

            if (job.status === "completed") {
                showSuccess(job.resultado || {});
                return;
            }
            if (job.status === "failed") {
                throw new Error(job.erro || "A importação não foi concluída.");
            }
            if (job.status === "cancelled") {
                setNotice(job.erro || "Importação cancelada. Nenhum dado foi gravado.");
                return;
            }
            if (job.status === "interrupted") {
                throw new Error(job.mensagem || "A importação foi interrompida. Envie o arquivo novamente.");
            }
        }
    }

    fileInput.addEventListener("change", () => {
        const file = fileInput.files && fileInput.files[0];
        fileName.textContent = file ? file.name : "";
        submitButton.disabled = !file || Boolean(activeJobId);
        result.replaceChildren();
    });

    cancelButton.addEventListener("click", async () => {
        if (!activeJobId) return;
        cancelButton.disabled = true;
        setProgress("Solicitando cancelamento seguro…", null);
        try {
            const response = await fetch(`/api/jobs/${activeJobId}/cancelar`, {
                method: "POST",
                credentials: "same-origin",
                headers: { Accept: "application/json" },
            });
            const data = await readJson(response);
            setProgress(data.mensagem || "Aguardando o cancelamento seguro…", null);
        } catch (error) {
            setNotice(error.message || "Não foi possível solicitar o cancelamento.", "error");
            cancelButton.disabled = false;
        }
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (activeJobId || !fileInput.files || !fileInput.files.length) return;

        const formData = new FormData(form);
        submitButton.disabled = true;
        fileInput.disabled = true;
        spinner.hidden = false;
        form.setAttribute("aria-busy", "true");
        setProgress("Enviando arquivo para validação…");

        try {
            const response = await fetch(form.action, {
                method: "POST",
                body: formData,
                credentials: "same-origin",
                headers: { Accept: "application/json" },
            });
            const started = await readJson(response);
            if (!started.job_id || !started.poll_url) {
                throw new Error("O servidor não iniciou o processamento da ECD.");
            }

            activeJobId = started.job_id;
            cancelButton.hidden = false;
            cancelButton.disabled = false;
            spinner.hidden = true;
            setProgress(started.mensagem || "Processamento iniciado…", 0);
            await waitForJob(activeJobId, started.poll_url);
        } catch (error) {
            setNotice(error.message || "Não foi possível processar o arquivo.", "error");
        } finally {
            activeJobId = null;
            cancelButton.hidden = true;
            cancelButton.disabled = false;
            spinner.hidden = true;
            fileInput.disabled = false;
            submitButton.disabled = !fileInput.files || !fileInput.files.length;
            form.removeAttribute("aria-busy");
        }
    });
})();
