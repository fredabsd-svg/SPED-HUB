"""Identidade "Tinta & Latão" nos relatórios exportados (Fase 22).

Três garantias:

1. **O balancete em PDF existe e sai no caminho pedido** — o contrato que
   faltava está em `tests/test_cli.py`; aqui ficam os detalhes do template.
2. **Os totais do balancete não dobram a conta.** Sintética agrega as
   analíticas; somar todas as linhas duplicaria cada valor.
3. **As fontes da identidade estão no repositório e o CSS só referencia
   fonte que existe** — um `@font-face` apontando para arquivo ausente não
   quebra o WeasyPrint: ele cai no fallback em silêncio e o PDF sai com a
   cara errada.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.reports.balancete import Balancete, LinhaBalancete
from src.reports.export_engine import ExportEngine, WhiteLabel

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "src" / "reports" / "templates"


def _linha(cod, nivel, ind_cta, si, d, c, sf, divergente=False):
    return LinhaBalancete(
        cod_cta=cod,
        nome_cta=f"CONTA {cod}",
        nivel=nivel,
        cod_nat="01",
        ind_cta=ind_cta,
        saldo_inicial=si,
        debitos=d,
        creditos=c,
        saldo_final=sf,
        tem_divergencia=divergente,
    )


class TestTotaisDoBalancete:
    """`Balancete.totais` soma só o menor nível presente."""

    def test_nao_soma_sintetica_com_analitica(self):
        """1 (S) agrega 1.1 e 1.2 (A): o total certo é o da sintética, uma vez.

        Somar as três linhas daria o dobro — é o defeito que este teste
        existe para impedir.
        """
        linhas = [
            _linha("1", 1, "S", 100.0, 60.0, 40.0, 120.0),
            _linha("1.1", 2, "A", 70.0, 30.0, 20.0, 80.0),
            _linha("1.2", 2, "A", 30.0, 30.0, 20.0, 40.0),
        ]
        totais = Balancete.totais(None, linhas)
        assert totais == {
            "saldo_inicial": 100.0,
            "debitos": 60.0,
            "creditos": 40.0,
            "saldo_final": 120.0,
        }, "os totais dobraram (ou sumiram): a soma deve usar só o menor nível presente"

    def test_listagem_sem_nivel_1_usa_o_topo_disponivel(self):
        """Com filtro por nível, o topo da listagem pode ser o nível 2."""
        linhas = [
            _linha("1.1", 2, "S", 70.0, 30.0, 20.0, 80.0),
            _linha("1.1.1", 3, "A", 70.0, 30.0, 20.0, 80.0),
        ]
        totais = Balancete.totais(None, linhas)
        assert totais["saldo_final"] == 80.0

    def test_listagem_vazia(self):
        totais = Balancete.totais(None, [])
        assert totais == {
            "saldo_inicial": 0.0,
            "debitos": 0.0,
            "creditos": 0.0,
            "saldo_final": 0.0,
        }


class TestTemplateBalancete:
    @pytest.fixture
    def engine(self) -> ExportEngine:
        return ExportEngine()

    def _render(self, engine, linhas, **kw):
        from src.reports.base import ReportContext

        ctx = ReportContext(
            titulo="Balancete de Verificação",
            empresa_nome="EMPRESA TESTE LTDA",
            empresa_cnpj="00123456000199",
            periodo_ref="2024",
        )
        balancete_totais = kw.pop("totais", Balancete.totais(None, linhas))
        conferencia = kw.pop(
            "conferencia",
            {
                "total_contas": len(linhas),
                "contas_com_divergencia": sum(1 for x in linhas if x.tem_divergencia),
                "soma_divergencias": 0.0,
                "status": "OK",
            },
        )
        return engine.render_html(
            "balancete.html",
            ctx,
            WhiteLabel(),
            linhas=linhas,
            totais=balancete_totais,
            conferencia=conferencia,
        )

    def test_linhas_e_totais_no_html(self, engine):
        linhas = [
            _linha("1", 1, "S", 100.0, 60.0, 40.0, 120.0),
            _linha("1.1", 2, "A", 100.0, 60.0, 40.0, 120.0),
        ]
        html = self._render(engine, linhas)
        assert "CONTA 1.1" in html
        assert "TOTAIS" in html
        assert "kicker" in html, "o sobretítulo da identidade sumiu do template"

    def test_divergencia_vira_alerta_visivel(self, engine):
        """Divergência de conferência não pode ficar só no log."""
        linhas = [_linha("1", 1, "A", 0.0, 10.0, 0.0, 99.0, divergente=True)]
        html = self._render(
            engine,
            linhas,
            conferencia={
                "total_contas": 1,
                "contas_com_divergencia": 1,
                "soma_divergencias": 89.0,
                "status": "DIVERGÊNCIAS",
            },
        )
        # O CSS é inlinado no HTML, então a DEFINIÇÃO `.callout-error`
        # aparece sempre — o que se procura é o uso, na marcação.
        assert (
            'class="callout callout-error"' in html
        ), "1 conta divergente e nenhum alerta visível no documento"

    def test_sem_divergencia_sem_alerta(self, engine):
        html = self._render(engine, [_linha("1", 1, "A", 0.0, 10.0, 10.0, 0.0)])
        assert 'class="callout callout-error"' not in html


class TestFontesDaIdentidade:
    def test_todo_font_face_aponta_para_arquivo_existente(self):
        """WeasyPrint não erra com fonte ausente: degrada em silêncio.

        O PDF sairia com Times/Helvetica e ninguém saberia — a mesma classe
        de defeito do CDN bloqueado (§4.3): a aparência muda sem nenhum
        erro. Este teste transforma a degradação silenciosa em falha.
        """
        css = (TEMPLATES / "print.css").read_text("utf-8")
        urls = re.findall(r"url\('([^']+\.ttf)'\)", css)
        assert urls, "print.css não declara nenhuma fonte — a identidade sumiu"
        ausentes = [u for u in urls if not (TEMPLATES / u).exists()]
        assert not ausentes, f"@font-face apontando para arquivo inexistente: {ausentes}"

    def test_familias_da_identidade_declaradas(self):
        css = (TEMPLATES / "print.css").read_text("utf-8")
        assert "Source Serif 4" in css and "Source Sans 3" in css

    def test_fontes_licenciadas(self):
        """Fonte sem licença junto é passivo jurídico, não asset."""
        fonts = TEMPLATES / "fonts"
        assert (fonts / "LICENSE-SourceSerif4.md").exists()
        assert (fonts / "LICENSE-SourceSans3.md").exists()
        for licenca in ("LICENSE-SourceSerif4.md", "LICENSE-SourceSans3.md"):
            assert "SIL Open Font License" in (fonts / licenca).read_text("utf-8")

    def test_inter_saiu_por_completo(self):
        """A troca deixou de referenciar a Inter — arquivo órfão de 27 MB
        (inter.zip) já morou aqui sem ninguém notar."""
        fonts = TEMPLATES / "fonts"
        orfaos = [p.name for p in fonts.iterdir() if "inter" in p.name.lower()]
        assert not orfaos, f"restos da fonte anterior no repositório: {orfaos}"
        css = (TEMPLATES / "print.css").read_text("utf-8")
        assert "Inter" not in css
