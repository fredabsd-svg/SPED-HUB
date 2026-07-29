"""Bibliotecas de front-end servidas pela própria aplicação (Fase 18).

Antes, htmx/Alpine/Chart.js/SortableJS vinham do `cdn.jsdelivr.net` em tempo
de execução.  Isso trazia três problemas, todos verificados por estes testes:

1. **Quebra silenciosa sem o CDN.**  Firewall corporativo bloqueando jsdelivr
   — situação real em escritório contábil — fazia o htmx não carregar, e os
   formulários caíam para submit nativo.  Foi assim que a senha de login
   acabava na query string.
2. **Versões divergentes entre páginas.**  Só o `base.html` pinava; as demais
   usavam `@3`/`@4`/`@1`, que resolvem para o último release do major.  Na
   prática o dashboard rodava Alpine 3.14.1 e a página de webhooks, 3.15.12,
   sem nenhuma alteração de código.
3. **CSP frouxa**, obrigada a liberar um domínio externo em `script-src`.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "src" / "dashboard" / "templates"
VENDOR = REPO_ROOT / "src" / "dashboard" / "static" / "vendor"
NGINX = REPO_ROOT / "nginx.conf"


@pytest.fixture(scope="module")
def cliente() -> TestClient:
    from src.dashboard.app import app

    return TestClient(app)


class TestSemCDN:
    def test_nenhum_template_carrega_de_dominio_externo(self):
        ofensores = {}
        for html in TEMPLATES.rglob("*.html"):
            externos = re.findall(r'(?:src|href)="(https?://[^"]+)"', html.read_text("utf-8"))
            if externos:
                ofensores[html.name] = externos
        assert not ofensores, (
            f"origem externa em template: {ofensores} — sem acesso a ela a "
            "aplicação degrada em silêncio"
        )

    def test_csp_nao_libera_origem_externa_de_script(self):
        csp = re.search(
            r'add_header Content-Security-Policy "([^"]+)"', NGINX.read_text("utf-8")
        ).group(1)
        script_src = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
        assert "http" not in script_src, f"script-src ainda aceita origem externa: {script_src}"


class TestArquivosVersionados:
    def test_checksums_conferem(self):
        """Arquivo trocado sem atualizar o SHA256SUMS não passa despercebido."""
        registrados = {}
        for linha in (VENDOR / "SHA256SUMS").read_text("utf-8").split("\n"):
            if linha.strip():
                digest, nome = linha.split()
                registrados[nome] = digest

        for arquivo in sorted(VENDOR.glob("*.js")):
            assert arquivo.name in registrados, f"{arquivo.name} não está no SHA256SUMS"
            atual = hashlib.sha256(arquivo.read_bytes()).hexdigest()
            assert atual == registrados[arquivo.name], f"{arquivo.name} divergiu do checksum"

        assert set(registrados) == {
            a.name for a in VENDOR.glob("*.js")
        }, "SHA256SUMS lista arquivo que não existe no diretório"

    def test_todo_script_referenciado_existe(self):
        """Nome trocado sem o arquivo vira 404 em produção; aqui quebra o teste."""
        faltando = []
        for html in TEMPLATES.rglob("*.html"):
            for src in re.findall(r'src="(/static/[^"]+)"', html.read_text("utf-8")):
                caminho = REPO_ROOT / "src" / "dashboard" / src.lstrip("/")
                if not caminho.is_file():
                    faltando.append((html.name, src))
        assert not faltando, f"referências sem arquivo correspondente: {faltando}"

    def test_uma_unica_versao_de_cada_biblioteca(self):
        """Páginas rodando versões diferentes da mesma lib foi o defeito original."""
        por_lib: dict[str, set[str]] = {}
        for html in TEMPLATES.rglob("*.html"):
            for src in re.findall(r'src="/static/vendor/([^"]+)"', html.read_text("utf-8")):
                por_lib.setdefault(src.split("-")[0], set()).add(src)
        divergentes = {k: v for k, v in por_lib.items() if len(v) > 1}
        assert not divergentes, f"mais de uma versão referenciada: {divergentes}"


class TestServidoPelaAplicacao:
    @pytest.mark.parametrize(
        "arquivo",
        [
            "htmx-1.9.12.min.js",
            "alpine-3.14.1.min.js",
            "chart-4.4.4.umd.min.js",
            "sortable-1.15.7.min.js",
        ],
    )
    def test_rota_estatica_serve_o_arquivo(self, cliente, arquivo):
        resposta = cliente.get(f"/static/vendor/{arquivo}")
        assert resposta.status_code == 200
        assert len(resposta.content) > 1000

    def test_estaticos_dispensam_autenticacao(self, cliente):
        """Se caíssem no middleware de auth, a página de login ficaria sem JS."""
        assert cliente.get("/static/vendor/htmx-1.9.12.min.js").status_code == 200

    def test_arquivo_inexistente_da_404(self, cliente):
        assert cliente.get("/static/vendor/nao-existe.js").status_code == 404


class TestConfiguracaoDoNginx:
    def test_alias_aponta_para_o_diretorio_real(self):
        """O alias apontava para `/app/static/`, que nunca existiu: 404 em produção."""
        # Varre linha a linha em vez de regex sobre o bloco inteiro: a palavra
        # "alias" também aparece nos comentários do arquivo.
        dentro_do_bloco = False
        caminho = None
        for linha in NGINX.read_text("utf-8").split("\n"):
            despido = linha.strip()
            if despido.startswith("#"):
                continue
            if despido.startswith("location /static/"):
                dentro_do_bloco = True
            elif dentro_do_bloco and despido.startswith("}"):
                break
            elif dentro_do_bloco and despido.startswith("alias "):
                caminho = despido.removeprefix("alias ").rstrip(";").strip().rstrip("/")
                break

        assert caminho, "nginx.conf sem alias para /static/"
        # WORKDIR=/app no Dockerfile, então o alias é /app/<caminho no repo>.
        relativo = caminho.removeprefix("/app/")
        assert (REPO_ROOT / relativo).is_dir(), f"alias aponta para {caminho}, que não existe"
