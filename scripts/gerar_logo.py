#!/usr/bin/env python3
"""Gera o logo do SPED-HUB a partir das fontes da identidade "Tinta & Latão".

O símbolo é o registro do SPED visto de perto: toda linha de um arquivo SPED
começa e termina com `|`.  As duas barras de latão são esses delimitadores, e
o nó entre elas é o hub — o ponto onde ECD, EFD e ECF se encontram.

O texto sai em **curvas**, extraídas da Source Serif 4 Semibold que já está
versionada em `src/reports/templates/fonts/`.  Um SVG com `<text>` depende da
fonte instalada em quem abre: no GitHub, que exibe o README como imagem, a
marca cairia para Times ou Georgia sem aviso nenhum.

Tudo o que este script escreve é derivado dele (§1.9) — cores, geometria e
fonte ficam aqui, não nos SVGs:

    python scripts/gerar_logo.py          # regera os SVGs
    python scripts/gerar_logo.py --png    # também a prévia social (Chromium)

A prévia social (`docs/assets/previa-social.png`, 1280×640) é a imagem que o
GitHub mostra quando o link do repositório é compartilhado.  Ela não sobe
sozinha: é enviada em Settings → General → Social preview.

`tests/test_logo.py` refaz a geração e compara com os arquivos versionados:
mudar o desenho sem regerar derruba o CI.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

REPO = Path(__file__).resolve().parent.parent
FONTES = REPO / "src" / "reports" / "templates" / "fonts"
SERIFADA = FONTES / "SourceSerif4-Semibold.ttf"
SEM_SERIFA = FONTES / "SourceSans3-Regular.ttf"

ASSETS = REPO / "docs" / "assets"
STATIC = REPO / "src" / "dashboard" / "static"

# Paleta — a mesma de `--primary` e `--accent` no base.html.
TINTA = "#0C3A30"
TINTA_CLARA = "#134A3D"
LATAO = "#A9812F"
LATAO_CLARO = "#C9A75B"
PAPEL = "#F5F2EA"

AVISO = "ARQUIVO GERADO — não edite; fonte: scripts/gerar_logo.py"


# ── Texto em curvas ────────────────────────────────────────────────────────


def _texto_em_curvas(
    fonte: TTFont, texto: str, x: float, baseline: float, tamanho: float, tracking: float = 0
) -> tuple[str, float]:
    """Devolve (path `d`, x final) do texto posto na linha de base dada.

    `tracking` é em unidades do em (1000), somado ao avanço de cada glifo.
    """
    escala = tamanho / fonte["head"].unitsPerEm
    cmap = fonte.getBestCmap()
    glifos = fonte.getGlyphSet()
    partes: list[str] = []
    cursor = x
    for caractere in texto:
        nome = cmap[ord(caractere)]
        caneta = SVGPathPen(glifos)
        # A fonte tem o eixo y para cima; o SVG, para baixo.
        glifos[nome].draw(TransformPen(caneta, (escala, 0, 0, -escala, cursor, baseline)))
        comandos = caneta.getCommands()
        if comandos:
            partes.append(comandos)
        cursor += (glifos[nome].width + tracking) * escala
    return " ".join(partes), cursor - tracking * escala


def _arredondar(d: str) -> str:
    """Duas casas bastam para um logo e deixam o diff do SVG legível."""
    saida = []
    for token in d.replace(",", " ").split():
        try:
            numero = float(token)
        except ValueError:
            saida.append(token)
            continue
        texto = f"{numero:.2f}".rstrip("0").rstrip(".")
        saida.append("0" if texto == "-0" else texto)
    return " ".join(saida)


# ── Símbolo ────────────────────────────────────────────────────────────────


def _simbolo(x: float = 0, y: float = 0, lado: float = 64, fundo: str | None = TINTA) -> str:
    """O símbolo num quadrado de `lado` px, com canto em (x, y).

    Desenhado numa grade de 64 e escalado: é essa grade que mantém as barras
    nítidas no favicon de 16 px, onde cada 4 unidades viram 1 pixel.
    """
    e = lado / 64
    partes = [f'<g transform="translate({x:g} {y:g}) scale({e:g})">']
    if fundo:
        partes.append(f'<rect width="64" height="64" rx="14" fill="{fundo}"/>')
    partes += [
        # Os dois delimitadores do registro: |  |
        f'<rect x="12" y="12" width="7" height="40" rx="3.5" fill="{LATAO_CLARO}"/>',
        f'<rect x="45" y="12" width="7" height="40" rx="3.5" fill="{LATAO_CLARO}"/>',
        # Os campos do registro, em papel sobre tinta.
        f'<rect x="24" y="17" width="16" height="4" rx="2" fill="{PAPEL}" opacity=".55"/>',
        f'<rect x="24" y="43" width="11" height="4" rx="2" fill="{PAPEL}" opacity=".55"/>',
        # O hub: liga os dois delimitadores.
        f'<rect x="19" y="30" width="26" height="4" fill="{LATAO_CLARO}"/>',
        f'<circle cx="32" cy="32" r="8.5" fill="{LATAO_CLARO}"/>',
        f'<circle cx="32" cy="32" r="3.5" fill="{fundo or TINTA}"/>',
        "</g>",
    ]
    return "".join(partes)


def _svg(largura: float, altura: float, titulo: str, corpo: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {largura:g} {altura:g}" '
        f'width="{largura:g}" height="{altura:g}" role="img" aria-labelledby="t">'
        f"<!-- {AVISO} -->"
        f'<title id="t">{titulo}</title>'
        f"{corpo}</svg>\n"
    )


# ── Peças ──────────────────────────────────────────────────────────────────


def marca() -> str:
    """Só o símbolo — favicon e ícone de aplicativo."""
    return _svg(64, 64, "SPED-HUB", _simbolo())


def logo(escuro: bool = False) -> str:
    """Símbolo + nome.  `escuro` troca a tinta do texto por papel.

    O README usa as duas versões num `<picture>`: o GitHub escolhe pela
    preferência de tema de quem lê, e texto em tinta sobre o fundo escuro
    do GitHub simplesmente desapareceria.
    """
    serifada = TTFont(SERIFADA)
    tamanho = 46
    baseline = 32 + 0.67 * tamanho / 2  # centra a altura das maiúsculas no símbolo
    x0 = 64 + 18
    sped, x1 = _texto_em_curvas(serifada, "SPED", x0, baseline, tamanho, tracking=8)
    hub, x2 = _texto_em_curvas(serifada, "-HUB", x1 + 1, baseline, tamanho, tracking=8)
    largura = round(x2 + 4)
    cor_nome = PAPEL if escuro else TINTA
    cor_hub = LATAO_CLARO if escuro else LATAO
    corpo = (
        _simbolo(fundo=TINTA_CLARA if escuro else TINTA)
        + f'<path fill="{cor_nome}" d="{_arredondar(sped)}"/>'
        + f'<path fill="{cor_hub}" d="{_arredondar(hub)}"/>'
    )
    return _svg(largura, 64, "SPED-HUB", corpo)


def previa_social() -> str:
    """Imagem 1280×640 para a prévia do repositório (Settings → Social preview)."""
    serifada = TTFont(SERIFADA)
    sem_serifa = TTFont(SEM_SERIFA)
    tamanho = 104
    baseline = 300
    x0 = 96 + 136 + 44
    sped, x1 = _texto_em_curvas(serifada, "SPED", x0, baseline, tamanho, tracking=8)
    hub, _ = _texto_em_curvas(serifada, "-HUB", x1 + 2, baseline, tamanho, tracking=8)
    linha1, _ = _texto_em_curvas(
        sem_serifa, "Conformidade fiscal e contábil", 96, 430, 44, tracking=0
    )
    linha2, _ = _texto_em_curvas(
        sem_serifa, "para escritórios de contabilidade", 96, 490, 44, tracking=0
    )
    rodape, _ = _texto_em_curvas(
        sem_serifa, "ECD  ·  EFD ICMS/IPI  ·  EFD-Contribuições  ·  ECF", 96, 574, 26, tracking=40
    )
    corpo = (
        f'<rect width="1280" height="640" fill="{TINTA}"/>'
        f'<rect x="0" y="0" width="12" height="640" fill="{LATAO}"/>'
        + _simbolo(96, 186, 136, fundo=TINTA_CLARA)
        + f'<path fill="{PAPEL}" d="{_arredondar(sped)}"/>'
        + f'<path fill="{LATAO_CLARO}" d="{_arredondar(hub)}"/>'
        + f'<path fill="{PAPEL}" d="{_arredondar(linha1)}"/>'
        + f'<path fill="{PAPEL}" opacity=".78" d="{_arredondar(linha2)}"/>'
        + f'<path fill="{LATAO_CLARO}" d="{_arredondar(rodape)}"/>'
    )
    return _svg(1280, 640, "SPED-HUB — conformidade fiscal e contábil", corpo)


def arquivos() -> dict[Path, str]:
    """Destino → conteúdo.  O teste usa esta função para conferir o versionado."""
    return {
        ASSETS / "logo.svg": logo(),
        ASSETS / "logo-escuro.svg": logo(escuro=True),
        ASSETS / "previa-social.svg": previa_social(),
        STATIC / "marca.svg": marca(),
    }


def _png(svg: Path, destino: Path, chromium: str | None = None) -> None:
    """Rasteriza no Chromium do Playwright, que já é dependência do projeto."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        navegador = p.chromium.launch(executable_path=chromium)
        pagina = navegador.new_page(viewport={"width": 1280, "height": 640})
        pagina.goto(svg.resolve().as_uri())
        pagina.screenshot(path=str(destino))
        navegador.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--png", action="store_true", help="também gera a prévia social em PNG")
    parser.add_argument("--chromium", help="executável do Chromium, se não for o do Playwright")
    args = parser.parse_args()

    for destino, conteudo in arquivos().items():
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(conteudo, encoding="utf-8")
        print(f"escrito {destino.relative_to(REPO)}")
    if args.png:
        _png(ASSETS / "previa-social.svg", ASSETS / "previa-social.png", args.chromium)
        print(f"escrito {(ASSETS / 'previa-social.png').relative_to(REPO)}")


if __name__ == "__main__":
    main()
