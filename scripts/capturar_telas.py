#!/usr/bin/env python3
"""Gera as capturas de tela do README a partir da ECD de exemplo dos testes.

As imagens mostram o que o código faz hoje (§1.5): sobe o painel com um banco
descartável, cria o administrador, importa `tests/fixtures/ecd_sample.txt`
pela própria API e fotografa as telas.  Nada é montado à mão — refazer as
imagens depois de mudar o layout é rodar isto de novo (§1.9):

    python scripts/capturar_telas.py
    python scripts/capturar_telas.py --chromium /caminho/do/chrome

Escreve em `docs/assets/`:

    tela-entrada.png   a tela de login
    tela-painel.png    o painel contábil: indicadores e gráficos
    tela-balanco.png   o Balanço Patrimonial na aba de relatórios
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DESTINO = REPO / "docs" / "assets"
AMOSTRA = REPO / "tests" / "fixtures" / "ecd_sample.txt"

EMAIL = "ana@escritorio.com.br"
SENHA = "senha-de-exemplo"


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _esperar(base: str, processo: subprocess.Popen, prazo: float = 30) -> None:
    limite = time.time() + prazo
    while time.time() < limite:
        if processo.poll() is not None:
            raise SystemExit(f"o servidor morreu com código {processo.returncode}")
        try:
            with urllib.request.urlopen(f"{base}/api/v1/health", timeout=2):
                return
        except OSError:
            time.sleep(0.3)
    raise SystemExit("o servidor não respondeu a tempo")


def capturar(chromium: str | None) -> None:
    from playwright.sync_api import sync_playwright

    DESTINO.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        base = f"http://127.0.0.1:{_porta_livre()}"
        ambiente = {
            **os.environ,
            "SPED_HUB_DB": str(Path(tmp) / "telas.db"),
            "SPED_HUB_UPLOAD_DIR": str(Path(tmp) / "uploads"),
        }
        ambiente.pop("DATABASE_URL", None)
        porta = base.rsplit(":", 1)[1]
        servidor = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "src.dashboard.app:app", "--port", porta],
            cwd=REPO,
            env=ambiente,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _esperar(base, servidor)
            with sync_playwright() as p:
                navegador = p.chromium.launch(executable_path=chromium)
                contexto = navegador.new_context(viewport={"width": 1280, "height": 800})
                pagina = contexto.new_page()

                pagina.goto(f"{base}/login")
                pagina.screenshot(path=str(DESTINO / "tela-entrada.png"))

                api = contexto.request
                api.post(
                    f"{base}/api/register", form={"email": EMAIL, "nome": "Ana", "senha": SENHA}
                )
                api.post(f"{base}/api/login", form={"email": EMAIL, "senha": SENHA})
                resposta = api.post(
                    f"{base}/api/upload",
                    multipart={
                        "file": {
                            "name": "ecd.txt",
                            "mimeType": "text/plain",
                            "buffer": AMOSTRA.read_bytes(),
                        }
                    },
                )
                if not resposta.ok:
                    raise SystemExit(f"a importação da amostra falhou: {resposta.text()}")

                pagina.set_viewport_size({"width": 1280, "height": 1000})
                pagina.goto(f"{base}/")
                pagina.locator("[data-chart-data-status]").filter(has_text="atualizados").wait_for(
                    state="attached", timeout=15_000
                )
                pagina.wait_for_timeout(800)  # animação de entrada dos gráficos
                pagina.screenshot(path=str(DESTINO / "tela-painel.png"))

                relatorios = pagina.locator("#tab-balanco table")
                relatorios.wait_for(timeout=15_000)
                cartao = pagina.locator(".card", has=relatorios)
                cartao.screenshot(path=str(DESTINO / "tela-balanco.png"))

                navegador.close()
        finally:
            servidor.terminate()
            servidor.wait(timeout=10)

    for nome in ("tela-entrada.png", "tela-painel.png", "tela-balanco.png"):
        print(f"escrito {(DESTINO / nome).relative_to(REPO)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--chromium", help="executável do Chromium, se não for o do Playwright")
    capturar(parser.parse_args().chromium)


if __name__ == "__main__":
    main()
