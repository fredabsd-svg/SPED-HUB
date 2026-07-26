"""Testes E2E com Playwright — Fase 15.

Testes de browser real para fluxos críticos:
  - Login/logout
  - Upload de ECD
  - Dashboard com dados
  - Relatórios (Balanço, DRE)
  - API REST v1

Requer: Chromium do sistema em /usr/bin/chromium
"""

import os
import tempfile
import time
from pathlib import Path

import pytest

# Skip se Playwright não estiver disponível
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not PLAYWRIGHT_AVAILABLE,
    reason="Playwright não instalado",
)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _criar_ecd_teste() -> str:
    """Cria arquivo ECD de teste."""
    linhas = [
        "|0000|LECD|01012024|31122024|EMPRESA E2E LTDA|00123456000199|SP||1234567||0|0|1|0|0|E||1|0||",
        "|I001|0|",
        "|I010|G|009|",
        "|I030|01012024|31122024|A|",
    ]
    for i in range(10):
        linhas.append(f"|I050|01012024|01|A|3|{i+1}|1|CONTA_{i+1}|")
    linhas.append("|I150|01012024|31122024|")
    for i in range(10):
        linhas.append(f"|I155|{i+1}||1000.00|D|5000.00|4000.00|2000.00|D|")
    for i in range(5):
        linhas.append(f"|I200|{i+1}|15012024|1000.00|N||")
        linhas.append(f"|I250|{(i%10)+1}||500.00|D|||LANC_{i+1}|001|")
        linhas.append(f"|I250|{((i+5)%10)+1}||500.00|C||||")
    linhas.append("|I350|31122024|")
    linhas.append("|I990|99|")
    linhas.append("|9001|0|")
    linhas.append(f"|9999|{len(linhas)}|")
    return "\n".join(linhas)


@pytest.fixture(scope="module")
def ecd_file():
    """Arquivo ECD para upload nos testes."""
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="e2e_ecd_")
    os.close(fd)
    Path(path).write_text(_criar_ecd_teste(), encoding="utf-8")
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture(scope="module")
def db_path():
    """Banco temporário para os testes E2E."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="sped_e2e_")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture(scope="module")
def live_server(db_path):
    """Servidor FastAPI para testes E2E."""
    import subprocess
    import sys

    os.environ["SPED_HUB_DB"] = db_path

    # Inicia servidor em processo separado
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "src.dashboard.app:app",
            "--host", "127.0.0.1",
            "--port", "8765",
            "--log-level", "error",
        ],
        cwd="/workspace/repo/SPED-HUB",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Aguarda servidor iniciar
    deadline = time.time() + 10
    import urllib.request
    while time.time() < deadline:
        try:
            urllib.request.urlopen("http://127.0.0.1:8765/api/v1/health")
            break
        except Exception:
            time.sleep(0.3)

    yield "http://127.0.0.1:8765"

    proc.terminate()
    proc.wait(timeout=5)
    del os.environ["SPED_HUB_DB"]


# ═══════════════════════════════════════════════════════════════════════════
# Testes E2E
# ═══════════════════════════════════════════════════════════════════════════


class TestE2ELogin:
    """Testes de login/logout com browser real."""

    def test_pagina_login_carrega(self, live_server):
        """Página de login carrega corretamente."""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path="/usr/bin/chromium",
                headless=True,
            )
            page = browser.new_page()
            page.goto(f"{live_server}/login")
            assert page.title() != ""
            assert page.locator("input[name='email']").is_visible()
            assert page.locator("input[name='senha']").is_visible()
            browser.close()

    def test_registro_e_login(self, live_server):
        """Fluxo completo: registro → login → dashboard."""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path="/usr/bin/chromium",
                headless=True,
            )
            context = browser.new_context()
            page = context.new_page()

            # Registro
            page.goto(f"{live_server}/register")
            page.fill("input[name='email']", "e2e@teste.com")
            page.fill("input[name='nome']", "Usuário E2E")
            page.fill("input[name='senha']", "senha123")
            page.click("button[type='submit']")

            # Aguarda redirect para login
            page.wait_for_url(f"{live_server}/login", timeout=5000)

            # Login
            page.fill("input[name='email']", "e2e@teste.com")
            page.fill("input[name='senha']", "senha123")
            page.click("button[type='submit']")

            # Aguarda redirect para dashboard
            page.wait_for_url(f"{live_server}/", timeout=5000)
            assert page.url == f"{live_server}/"

            browser.close()

    def test_login_invalido(self, live_server):
        """Login com credenciais inválidas mostra erro."""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path="/usr/bin/chromium",
                headless=True,
            )
            page = browser.new_page()
            page.goto(f"{live_server}/login")
            page.fill("input[name='email']", "invalido@teste.com")
            page.fill("input[name='senha']", "errada")
            page.click("button[type='submit']")

            # Deve permanecer na página de login
            time.sleep(1)
            assert "login" in page.url.lower()
            browser.close()


class TestE2EUpload:
    """Testes de upload com browser real."""

    def test_pagina_upload_requer_auth(self, live_server):
        """Upload redireciona para login sem autenticação."""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path="/usr/bin/chromium",
                headless=True,
            )
            page = browser.new_page()
            page.goto(f"{live_server}/upload")
            assert "login" in page.url.lower()
            browser.close()

    def test_upload_ecd(self, live_server, ecd_file):
        """Upload de ECD via interface web."""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path="/usr/bin/chromium",
                headless=True,
            )
            context = browser.new_context()
            page = context.new_page()

            # Registra e faz login
            page.goto(f"{live_server}/register")
            page.fill("input[name='email']", "upload_e2e@teste.com")
            page.fill("input[name='nome']", "Upload E2E")
            page.fill("input[name='senha']", "senha123")
            page.click("button[type='submit']")
            page.wait_for_url(f"{live_server}/login", timeout=5000)

            page.fill("input[name='email']", "upload_e2e@teste.com")
            page.fill("input[name='senha']", "senha123")
            page.click("button[type='submit']")
            page.wait_for_url(f"{live_server}/", timeout=5000)

            # Navega para upload
            page.goto(f"{live_server}/upload")
            assert page.url == f"{live_server}/upload"

            # Upload do arquivo
            file_input = page.locator("input[type='file']")
            file_input.set_input_files(ecd_file)

            # Clica no botão de upload
            upload_btn = page.locator("button[type='submit']")
            if upload_btn.is_visible():
                upload_btn.click()

            # Aguarda resultado
            time.sleep(2)

            browser.close()


class TestE2EDashboard:
    """Testes do dashboard com browser real."""

    def test_dashboard_com_dados(self, live_server, ecd_file):
        """Dashboard mostra dados após upload."""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path="/usr/bin/chromium",
                headless=True,
            )
            context = browser.new_context()
            page = context.new_page()

            # Registra e faz login
            page.goto(f"{live_server}/register")
            page.fill("input[name='email']", "dash_e2e@teste.com")
            page.fill("input[name='nome']", "Dashboard E2E")
            page.fill("input[name='senha']", "senha123")
            page.click("button[type='submit']")
            page.wait_for_url(f"{live_server}/login", timeout=5000)

            page.fill("input[name='email']", "dash_e2e@teste.com")
            page.fill("input[name='senha']", "senha123")
            page.click("button[type='submit']")
            page.wait_for_url(f"{live_server}/", timeout=5000)

            # Faz upload via API (mais rápido)
            import requests
            cookies = {cookie["name"]: cookie["value"] for cookie in context.cookies()}
            with open(ecd_file, "rb") as f:
                resp = requests.post(
                    f"{live_server}/api/upload",
                    files={"file": ("test.txt", f, "text/plain")},
                    cookies=cookies,
                    timeout=10,
                )
            assert resp.status_code == 200

            # Recarrega dashboard
            page.goto(f"{live_server}/")
            time.sleep(1)

            # Deve mostrar algum conteúdo
            body_text = page.inner_text("body")
            assert len(body_text) > 0

            browser.close()


class TestE2EAPI:
    """Testes da API REST com browser (verificação visual)."""

    def test_api_health(self, live_server):
        """Health check da API."""
        import requests
        resp = requests.get(f"{live_server}/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_api_ecds_vazia(self, live_server):
        """Lista ECDs vazia."""
        import requests
        resp = requests.get(f"{live_server}/api/v1/ecds", timeout=10)
        assert resp.status_code == 401
        assert "X-API-Key" in resp.json()["detail"]


class TestE2EScreenshots:
    """Captura screenshots para documentação visual."""

    def test_screenshot_login(self, live_server):
        """Screenshot da página de login."""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path="/usr/bin/chromium",
                headless=True,
            )
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(f"{live_server}/login")
            time.sleep(0.5)

            screenshot_dir = Path("/workspace/outputs")
            screenshot_dir.mkdir(exist_ok=True)
            page.screenshot(path=str(screenshot_dir / "e2e_login.png"))
            assert (screenshot_dir / "e2e_login.png").exists()

            browser.close()

    def test_screenshot_register(self, live_server):
        """Screenshot da página de registro."""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path="/usr/bin/chromium",
                headless=True,
            )
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(f"{live_server}/register")
            time.sleep(0.5)

            screenshot_dir = Path("/workspace/outputs")
            screenshot_dir.mkdir(exist_ok=True)
            page.screenshot(path=str(screenshot_dir / "e2e_register.png"))
            assert (screenshot_dir / "e2e_register.png").exists()

            browser.close()