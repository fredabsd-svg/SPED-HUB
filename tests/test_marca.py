"""O logo e o ícone do SPED-HUB (versão 0.20.0).

Três garantias:

1. **Os SVGs versionados são exatamente o que o gerador produz** (§1.9).
   Mudar cor ou geometria à mão num dos arquivos faria o favicon, o README e
   a prévia social divergirem entre si — e ninguém veria, porque cada um
   continuaria "funcionando".
2. **O texto do logo está em curvas.**  O GitHub mostra o README como
   imagem, sem acesso às fontes da aplicação: um `<text>` cairia para Times
   sem erro nenhum.
3. **O ícone chega ao navegador.**  Sem ele, cada página aberta pedia
   `/favicon.ico` e recebia 404.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "src" / "dashboard" / "templates"


def _gerador():
    spec = importlib.util.spec_from_file_location("gerar_logo", REPO / "scripts" / "gerar_logo.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def arquivos() -> dict[Path, str]:
    return _gerador().arquivos()


class TestLogoGerado:
    def test_versionado_bate_com_o_gerador(self, arquivos):
        divergentes = [
            str(destino.relative_to(REPO))
            for destino, conteudo in arquivos.items()
            if not destino.exists() or destino.read_text("utf-8") != conteudo
        ]
        assert not divergentes, (
            f"{divergentes} divergem de scripts/gerar_logo.py — o desenho foi mudado sem "
            "regerar (ou editado à mão). Rode `python scripts/gerar_logo.py`."
        )

    def test_texto_em_curvas(self, arquivos):
        com_texto = [str(d.relative_to(REPO)) for d, c in arquivos.items() if "<text" in c]
        assert not com_texto, (
            f"{com_texto} usam <text>: fora da aplicação a fonte não existe e a marca "
            "cai para Times sem aviso"
        )

    def test_declara_que_e_gerado(self, arquivos):
        aviso = "ARQUIVO GERADO — não edite; fonte: scripts/gerar_logo.py"
        sem_aviso = [str(d.relative_to(REPO)) for d, c in arquivos.items() if aviso not in c]
        assert not sem_aviso, f"{sem_aviso} não dizem de onde vêm (§1.9)"

    def test_svg_bem_formado_e_com_titulo(self, arquivos):
        import xml.etree.ElementTree as ET

        for destino, conteudo in arquivos.items():
            raiz = ET.fromstring(conteudo)
            titulo = raiz.find("{http://www.w3.org/2000/svg}title")
            assert titulo is not None and titulo.text, f"{destino.name} sem <title> acessível"


class TestReadme:
    def test_imagens_do_readme_existem(self):
        """O verificador de links da §1.2 só lê `[..](..)`; o `<picture>` do
        topo usa `src`/`srcset`, e imagem quebrada ali é a primeira coisa que
        quem abre o repositório vê."""
        readme = (REPO / "README.md").read_text("utf-8")
        caminhos = re.findall(r'(?:src|srcset)="([^"]+)"', readme)
        quebrados = [c for c in caminhos if not c.startswith("http") and not (REPO / c).exists()]
        assert not quebrados, f"imagem do README apontando para o vazio: {quebrados}"


@pytest.fixture(scope="module")
def cliente():
    from src.dashboard.app import app

    return TestClient(app)


class TestIcone:
    def test_icone_e_servido_sem_autenticacao(self, cliente):
        resposta = cliente.get("/static/marca.svg")

        assert resposta.status_code == 200
        assert "svg" in resposta.headers["content-type"]

    def test_favicon_ico_leva_ao_icone(self, cliente):
        resposta = cliente.get("/favicon.ico", follow_redirects=False)

        assert resposta.status_code in (301, 308)
        assert resposta.headers["location"] == "/static/marca.svg"

    def test_tela_de_entrada_declara_o_icone(self, cliente):
        html = cliente.get("/login").text

        assert 'rel="icon" href="/static/marca.svg"' in html

    @pytest.mark.parametrize("pagina", ["webhooks", "comparar", "layout", "api_keys"])
    def test_paginas_avulsas_tambem_declaram(self, pagina):
        """As quatro páginas que não herdam o base.html montam o próprio <head>."""
        texto = (TEMPLATES / f"{pagina}.html").read_text("utf-8")

        assert 'rel="icon" href="/static/marca.svg"' in texto, f"{pagina}.html sem ícone"
