"""Identidade "Tinta & Latão" no dashboard web (Fase 24).

Três garantias:

1. **As fontes servidas em `/static/fonts` existem e são byte a byte
   idênticas às dos relatórios.** São cópias deliberadas (o nginx serve
   `/static/` direto do disco, sem passar pela aplicação) — e cópia sem
   trava diverge em silêncio (§1.9).
2. **Todo `@font-face` dos templates aponta para arquivo que existe.**
   Navegador com fonte ausente degrada para a pilha do sistema sem nenhum
   erro — a mesma classe de defeito do CDN bloqueado.
3. **A paleta antiga saiu por completo.** Identidade pela metade é pior que
   nenhuma: metade das páginas numa cor, metade na outra.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "src" / "dashboard" / "templates"
STATIC_FONTS = REPO / "src" / "dashboard" / "static" / "fonts"
REPORT_FONTS = REPO / "src" / "reports" / "templates" / "fonts"

PALETA_ANTIGA = ("#0B4F6C", "#0b4f6c", "#E8F3F7", "#073A4F", "#7DD3FC")


class TestFontesServidas:
    def test_copias_identicas_as_dos_relatorios(self):
        """A cópia do dashboard não pode divergir da fonte dos relatórios.

        Se alguém atualizar a fonte de um lado e esquecer o outro, PDF e
        tela passam a usar versões diferentes da mesma família — e ninguém
        percebe, porque os dois continuam "funcionando".
        """
        ttfs = sorted(STATIC_FONTS.glob("*.ttf"))
        assert ttfs, "nenhuma fonte em src/dashboard/static/fonts"
        divergentes = []
        for ttf in ttfs:
            origem = REPORT_FONTS / ttf.name
            assert origem.exists(), (
                f"{ttf.name} servida no dashboard sem original nos relatórios — "
                "a cópia perdeu a referência"
            )
            if ttf.read_bytes() != origem.read_bytes():
                divergentes.append(ttf.name)
        assert not divergentes, (
            f"cópia do dashboard diverge da fonte dos relatórios: {divergentes} — "
            "atualize as duas juntas"
        )

    def test_font_face_aponta_para_arquivo_existente(self):
        problemas: dict[str, list[str]] = {}
        for html in TEMPLATES.rglob("*.html"):
            urls = re.findall(r"url\('/static/fonts/([^']+)'\)", html.read_text("utf-8"))
            ausentes = [u for u in urls if not (STATIC_FONTS / u).exists()]
            if ausentes:
                problemas[html.name] = ausentes
        assert not problemas, f"@font-face para arquivo inexistente: {problemas}"

    def test_paginas_avulsas_tambem_declaram_as_fontes(self):
        """As quatro páginas que não herdam o base.html precisam declarar as
        fontes por conta própria — foi assim que as versões de biblioteca
        divergiram entre páginas na era do CDN."""
        for nome in ("webhooks", "comparar", "layout", "api_keys"):
            texto = (TEMPLATES / f"{nome}.html").read_text("utf-8")
            assert (
                "Source Serif 4" in texto and "Source Sans 3" in texto
            ), f"{nome}.html ficou fora da identidade tipográfica"


class TestPaletaUnica:
    def test_paleta_antiga_saiu_por_completo(self):
        sobras: dict[str, list[str]] = {}
        for html in TEMPLATES.rglob("*.html"):
            texto = html.read_text("utf-8")
            achadas = [cor for cor in PALETA_ANTIGA if cor in texto]
            if achadas:
                sobras[html.name] = achadas
        assert not sobras, (
            f"cor da paleta anterior ainda em uso: {sobras} — identidade pela "
            "metade: metade das páginas numa cor, metade na outra"
        )

    def test_tinta_e_a_cor_primaria_em_todas_as_paginas(self):
        """base.html + as quatro avulsas: todas com --primary na tinta."""
        paginas = ["base.html", "webhooks.html", "comparar.html", "layout.html", "api_keys.html"]
        fora = [
            nome
            for nome in paginas
            if "--primary: #0C3A30;" not in (TEMPLATES / nome).read_text("utf-8")
        ]
        assert not fora, f"páginas fora da paleta da identidade: {fora}"


class TestNavbarAnonima:
    """Tela de login não mostra navegação de área logada.

    Apontado em revisão: a navbar completa (Dashboard, Upload, …, Auditoria)
    aparecia para o visitante anônimo. Nenhum link funcionava — todos
    redirecionavam de volta ao /login — mas a tela de entrada anunciava o
    mapa inteiro da aplicação para quem ainda não provou quem é.
    """

    @staticmethod
    def _cliente():
        from fastapi.testclient import TestClient

        from src.dashboard.app import app

        return TestClient(app)

    def test_login_sem_links_de_navegacao(self):
        html = self._cliente().get("/login").text
        for rotulo in ("Auditoria", "API Keys", "Webhooks", "Comparar"):
            assert (
                rotulo not in html
            ), f"tela de login expõe o link '{rotulo}' para visitante anônimo"
        assert "SPED" in html, "a marca sumiu junto — o condicional cortou demais"

    def test_register_sem_links_de_navegacao(self):
        html = self._cliente().get("/register").text
        assert "Auditoria" not in html and "Webhooks" not in html

    def test_pagina_logada_mostra_navegacao(self, tmp_path, monkeypatch):
        """O outro lado: logado, a navegação inteira volta."""
        caminho = str(tmp_path / "nav.db")
        monkeypatch.setenv("SPED_HUB_DB", caminho)
        from src.db.models import criar_engine, init_db
        from src.settings import reset_settings_cache

        reset_settings_cache()
        # O schema precisa existir antes do primeiro request: em execução de
        # suíte o serviço de auth já foi inicializado por outro teste e não
        # vai criar as tabelas deste banco novo.
        init_db(criar_engine(caminho))
        cliente = self._cliente()
        cliente.post(
            "/api/register",
            data={"email": "nav@teste.com", "nome": "Nav", "senha": "senha123"},
        )
        cliente.post("/api/login", data={"email": "nav@teste.com", "senha": "senha123"})
        html = cliente.get("/").text
        for rotulo in ("Dashboard", "Upload", "Auditoria", "Sair"):
            assert rotulo in html, f"logado e sem o link '{rotulo}' na navegação"
