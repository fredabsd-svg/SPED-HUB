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
import subprocess
import tempfile
import time
import zlib
from pathlib import Path

import pytest

# Skip se Playwright não estiver disponível
try:
    from playwright.sync_api import expect, sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

REPO_ROOT = Path(__file__).resolve().parent.parent


def _chromium_executable() -> str | None:
    """Localiza um Chromium utilizável, ou ``None`` se não houver nenhum.

    Ordem: variável explícita, browsers do Playwright, Chromium do sistema.
    Sem isto os testes ERRAM na coleta em qualquer máquina sem Chromium —
    incluindo o CI —, em vez de simplesmente pularem.
    """
    candidatos = []
    if os.environ.get("SPED_HUB_CHROMIUM"):
        candidatos.append(os.environ["SPED_HUB_CHROMIUM"])
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if browsers_path:
        candidatos.append(str(Path(browsers_path) / "chromium"))
    candidatos += ["/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"]
    for caminho in candidatos:
        if caminho and Path(caminho).exists():
            return caminho
    return None


CHROMIUM = _chromium_executable()

pytestmark = [
    # Tier separado: precisa de navegador real, e o harness ainda tem
    # defeitos próprios (ADR 0004).  Rode com `pytest -m e2e`.
    #
    # Rede externa não é mais requisito: desde a Fase 18 htmx, Alpine,
    # Chart.js e SortableJS são servidos pela própria aplicação.
    pytest.mark.e2e,
    pytest.mark.skipif(not PLAYWRIGHT_AVAILABLE, reason="Playwright não instalado"),
    pytest.mark.skipif(CHROMIUM is None, reason="Chromium não encontrado no sistema"),
]


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _criar_ecd_teste(cnpj: str = "00123456000199", empresa: str = "EMPRESA E2E LTDA") -> str:
    """Cria arquivo ECD de teste.

    O CNPJ é parâmetro porque cada teste precisa da própria escrituração: a
    importação recusa arquivo repetido (dedup por hash) e o banco tem
    unicidade em (empresa, período). Com um arquivo único compartilhado, o
    segundo teste a subir recebia 400 — e isso ficou escondido enquanto o
    servidor travava antes de chegar lá.
    """
    linhas = [
        f"|0000|LECD|01012024|31122024|{empresa}|{cnpj}|SP||1234567||0|0|1|0|0|E||1|0||",
        "|I001|0|",
        "|I010|G|009|",
        "|I030|TERMO DE ABERTURA|1|Diario|500|EMPRESA TESTE|31123456789|11111111000191|01012015||BELO HORIZONTE|31122023|",
    ]
    for i in range(10):
        # Conta 1 é topo (sem sintética): sup=1 para ela mesma era um
        # auto-ciclo — exatamente o defeito que a importação agora recusa.
        sup = "" if i == 0 else "1"
        linhas.append(f"|I050|01012024|01|A|3|{i+1}|{sup}|CONTA_{i+1}|")
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
def ecd_factory():
    """Gera uma ECD nova a cada chamada, com empresa própria.

    Devolve uma função `(sufixo) -> caminho`. Cada teste que sobe arquivo
    pede o seu: escrituração repetida é recusada com 400, e testes que
    dependem da ordem em que rodam são testes que mentem.
    """
    criados: list[str] = []

    def criar(sufixo: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".txt", prefix=f"e2e_ecd_{sufixo}_")
        os.close(fd)
        # CNPJ distinto por teste: dedup é por hash do arquivo, e o banco
        # ainda tem unicidade em (empresa, dt_ini, dt_fin).
        #
        # `hash()` de string é aleatorizado por processo (PYTHONHASHSEED):
        # daria um CNPJ diferente a cada execução, e teste que muda de dado
        # sozinho é teste que um dia falha sem ninguém ter mexido em nada.
        digitos = f"{zlib.crc32(sufixo.encode()) % 10**9:09d}"
        Path(path).write_text(
            _criar_ecd_teste(cnpj=f"00{digitos}999", empresa=f"EMPRESA {sufixo.upper()} LTDA"),
            encoding="utf-8",
        )
        criados.append(path)
        return path

    yield criar

    for path in criados:
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


def _porta_livre() -> int:
    """Porta efêmera concedida pelo sistema.

    A porta era fixa (8765). Duas execuções simultâneas — ou uma execução
    anterior cujo uvicorn não morreu — davam `address already in use`, e o
    servidor novo simplesmente não subia. Como a saída dele ia para
    `DEVNULL`, o sintoma que sobrava era um teste falhando por conexão
    recusada, sem nada explicando o porquê.
    """
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServidorNaoSubiu(RuntimeError):
    """O servidor de teste não respondeu no prazo. Traz o log dele junto."""


@pytest.fixture(scope="module")
def live_server(db_path, tmp_path_factory):
    """Servidor FastAPI para os testes E2E.

    Três defeitos deste fixture escondiam as falhas da suíte:

    1. **A saída do servidor ia para `DEVNULL`.** Quando ele quebrava no meio
       da suíte, não sobrava rastro nenhum — era literalmente impossível
       diagnosticar. Agora vai para arquivo, e o conteúdo entra na mensagem
       de erro.
    2. **A espera de readiness não falhava.** O laço tentava por 10 s e, no
       estouro, seguia em frente e entregava a URL assim mesmo. Os testes
       então falhavam com erro de conexão em vez de dizer "o servidor não
       subiu".
    3. **Porta fixa**, ver :func:`_porta_livre`.
    """
    import sys
    import urllib.request

    porta = _porta_livre()
    base_url = f"http://127.0.0.1:{porta}"
    log = tmp_path_factory.mktemp("e2e") / "uvicorn.log"

    ambiente = {**os.environ, "SPED_HUB_DB": db_path}

    with log.open("wb") as saida:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "src.dashboard.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(porta),
                "--log-level",
                "info",
            ],
            cwd=str(REPO_ROOT),
            env=ambiente,
            stdout=saida,
            stderr=subprocess.STDOUT,
        )

        try:
            _esperar_servidor(proc, base_url, log, urllib.request)
            yield base_url
        finally:
            _encerrar(proc)

    # O log só é útil quando algo deu errado; em execução limpa ele some com
    # o tmp_path do pytest.


def _esperar_servidor(proc, base_url: str, log, urllib_request, prazo: float = 30.0) -> None:
    """Bloqueia até o /health responder, ou levanta com o log do servidor."""
    deadline = time.time() + prazo
    ultimo_erro = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise ServidorNaoSubiu(
                f"uvicorn morreu com código {proc.returncode} antes de responder.\n"
                f"--- log do servidor ---\n{_ler(log)}"
            )
        try:
            with urllib_request.urlopen(f"{base_url}/api/v1/health", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception as exc:  # conexão recusada enquanto ele sobe
            ultimo_erro = exc
        time.sleep(0.3)

    raise ServidorNaoSubiu(
        f"servidor não respondeu em {prazo:.0f}s. Último erro: {ultimo_erro!r}\n"
        f"--- log do servidor ---\n{_ler(log)}"
    )


def _ler(log) -> str:
    try:
        texto = Path(log).read_text("utf-8", errors="replace").strip()
    except OSError as exc:
        return f"(não foi possível ler {log}: {exc})"
    return texto or "(vazio — o servidor não escreveu nada)"


def _encerrar(proc) -> None:
    """Encerra o uvicorn sem deixar processo órfão segurando a porta."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


# ═══════════════════════════════════════════════════════════════════════════
# Testes E2E
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def contador(db_path, live_server):
    """O administrador da instalação, criado fora do navegador.

    Antes cada teste criava seu usuário pelo `/register`. Isso deixou de
    funcionar quando o registro público passou a fechar depois do primeiro
    usuário — e já era frágil: banco e servidor são de módulo, então quem
    conseguia se registrar dependia da ordem em que os testes rodassem.

    Criando aqui, o estado é o de uma instalação em uso — administrador
    existente, registro fechado — para todos os testes, em qualquer ordem.
    """
    from src.auth import AuthService

    auth = AuthService(db_path=db_path)
    try:
        auth.registrar(email="contador@e2e.teste", nome="Contador E2E", senha="senha123")
    except ValueError:
        pass  # outro módulo já criou o primeiro; segue válido
    return {"email": "contador@e2e.teste", "senha": "senha123"}


def _entrar(page, live_server, contador):
    """Login pela tela, até o dashboard."""
    page.goto(f"{live_server}/login")
    page.fill("input[name='email']", contador["email"])
    page.fill("input[name='senha']", contador["senha"])
    page.click("button[type='submit']")
    page.wait_for_url(f"{live_server}/", timeout=30000)


class TestE2ELogin:
    """Testes de login/logout com browser real."""

    def test_pagina_login_carrega(self, live_server):
        """Página de login carrega corretamente."""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=CHROMIUM,
                headless=True,
            )
            page = browser.new_page()
            page.goto(f"{live_server}/login")
            assert page.title() != ""
            assert page.locator("input[name='email']").is_visible()
            assert page.locator("input[name='senha']").is_visible()
            browser.close()

    def test_login_leva_ao_dashboard(self, live_server, contador):
        """O caminho de quem já tem conta."""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=CHROMIUM,
                headless=True,
            )
            context = browser.new_context()
            page = context.new_page()

            _entrar(page, live_server, contador)
            assert page.url == f"{live_server}/"

            browser.close()

    def test_registro_fechado_diz_o_motivo_na_tela(self, live_server, contador):
        """Com administrador já criado, o `/register` recusa — e explica.

        Recusar sem dizer nada seria pior que não recusar: o visitante clica em
        "Criar Conta" e a tela fica parada, sem pista do que aconteceu.
        """
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=CHROMIUM,
                headless=True,
            )
            page = browser.new_context().new_page()

            page.goto(f"{live_server}/register")
            page.fill("input[name='email']", "estranho@gmail.com")
            page.fill("input[name='nome']", "Estranho")
            page.fill("input[name='senha']", "senha123")
            page.click("button[type='submit']")
            page.wait_for_selector("#register-result .alert-error", timeout=10000)

            mensagem = page.inner_text("#register-result").strip()
            assert "fechado" in mensagem.lower(), mensagem
            assert page.url.endswith("/register"), "não deveria ter navegado"

            browser.close()

    def test_login_invalido(self, live_server):
        """Login com credenciais inválidas mostra erro."""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=CHROMIUM,
                headless=True,
            )
            page = browser.new_page()
            page.goto(f"{live_server}/login")
            page.fill("input[name='email']", "invalido@teste.com")
            page.fill("input[name='senha']", "errada")
            page.click("button[type='submit']")

            # A mensagem é o que este teste passou a cobrar.  Ele só conferia a
            # URL, e por isso não viu que o alerta de erro NUNCA aparecia:
            # o handler de HTMX montava o texto e o descartava, porque em
            # resposta 4xx não há swap sem `shouldSwap`.  Quem errava a senha
            # via a tela parada, sem explicação nenhuma.
            page.wait_for_selector("#login-result .alert-error", timeout=10000)
            assert "inválid" in page.inner_text("#login-result").lower()
            assert "login" in page.url.lower()
            browser.close()


class TestE2EUpload:
    """Testes de upload com browser real."""

    def test_pagina_upload_requer_auth(self, live_server):
        """Upload redireciona para login sem autenticação."""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=CHROMIUM,
                headless=True,
            )
            page = browser.new_page()
            page.goto(f"{live_server}/upload")
            assert "login" in page.url.lower()
            browser.close()

    def test_upload_ecd(self, live_server, ecd_factory, contador):
        """Upload de ECD via interface web."""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=CHROMIUM,
                headless=True,
            )
            context = browser.new_context()
            page = context.new_page()

            _entrar(page, live_server, contador)

            # Navega para upload
            page.goto(f"{live_server}/upload")
            assert page.url == f"{live_server}/upload"

            page.locator("#file-ecd").set_input_files(ecd_factory("upload"))

            # `button[type='submit']` casa com TRÊS botões nesta página
            # (ECD, EFD e ECF) e viola o modo estrito do Playwright. O
            # botão certo tem id próprio, e nasce `disabled`: só habilita
            # quando o `@change` do input de arquivo dispara.
            botao = page.locator("#btn-ecd")
            expect(botao).to_be_enabled(timeout=10_000)
            botao.click()

            # O efeito, não a passagem do tempo: a importação precisa
            # relatar o que gravou. `time.sleep(2)` seguido de nada passava
            # mesmo com o upload quebrado (§3.1).
            resultado = page.locator("#upload-result")
            expect(resultado).to_contain_text("importada com sucesso", timeout=30_000)
            assert "{" not in resultado.inner_text(), (
                "JSON cru na tela: o handler de resposta não tratou "
                f"`status: ok` com mensagem — {resultado.inner_text()[:200]}"
            )

            browser.close()


class TestE2EDashboard:
    """Testes do dashboard com browser real."""

    def test_dashboard_com_dados(self, live_server, ecd_factory, contador):
        """Dashboard mostra dados após upload."""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=CHROMIUM,
                headless=True,
            )
            context = browser.new_context()
            page = context.new_page()

            _entrar(page, live_server, contador)

            # Faz upload via API (mais rápido)
            import httpx

            cookies = {cookie["name"]: cookie["value"] for cookie in context.cookies()}
            with open(ecd_factory("dashboard"), "rb") as f:
                resp = httpx.post(
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
        import httpx

        resp = httpx.get(f"{live_server}/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_api_ecds_vazia(self, live_server):
        """Lista ECDs vazia."""
        import httpx

        resp = httpx.get(f"{live_server}/api/v1/ecds", timeout=10)
        assert resp.status_code == 401
        assert "X-API-Key" in resp.json()["detail"]


class TestE2EScreenshots:
    """Captura screenshots para documentação visual."""

    def test_screenshot_login(self, live_server):
        """Screenshot da página de login."""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=CHROMIUM,
                headless=True,
            )
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(f"{live_server}/login")
            time.sleep(0.5)

            screenshot_dir = Path(os.environ.get("SPED_HUB_SCREENSHOT_DIR", tempfile.gettempdir()))
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot_dir / "e2e_login.png"))
            assert (screenshot_dir / "e2e_login.png").exists()

            browser.close()

    def test_screenshot_register(self, live_server):
        """Screenshot da página de registro."""
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=CHROMIUM,
                headless=True,
            )
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(f"{live_server}/register")
            time.sleep(0.5)

            screenshot_dir = Path(os.environ.get("SPED_HUB_SCREENSHOT_DIR", tempfile.gettempdir()))
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot_dir / "e2e_register.png"))
            assert (screenshot_dir / "e2e_register.png").exists()

            browser.close()
