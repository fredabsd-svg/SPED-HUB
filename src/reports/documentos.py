"""Documentos exportáveis — um caminho só para a linha de comando, o painel e a API.

Cada exportação (PDF, XLSX, TXT) montava o próprio contexto em dois lugares,
a CLI e o painel, e os dois divergiam: o painel deixava a descrição de
filtros vazia — e o PDF saía com o rótulo FILTROS e nada embaixo —, mostrava
o período em ISO e não levava o hash da ECD. Aqui o documento é montado uma
vez: o gerador do relatório, o contexto do cabeçalho, as linhas da planilha e
as assinaturas. Quem exporta só escolhe o formato.
"""

from __future__ import annotations

import datetime
import io
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from src.cnpj import formatar as formatar_cnpj
from src.db.models import ECD, Empresa
from src.filters.engine import FilterCriteria
from src.reports.assinaturas import Assinatura, Responsavel, assinaturas
from src.reports.balancete import Balancete
from src.reports.balanco import BalancoPatrimonial
from src.reports.base import ReportContext, fmt_data, fmt_moeda
from src.reports.dfc import DFC
from src.reports.diario import LivroDiario
from src.reports.dre import DRE
from src.reports.export_engine import ExportEngine, WhiteLabel
from src.reports.indices import IndicesFinanceiros
from src.reports.plano_contas import PlanoDeContas

TIPOS = ("balancete", "balanco", "dre", "dfc", "indices", "plano", "diario")

# As demonstrações saem com a linha de assinatura; o plano de contas e o
# diário (que tem termos próprios) não.
COM_ASSINATURA = {"balancete", "balanco", "dre", "dfc", "indices"}

FORMATOS = ("pdf", "xlsx", "txt")

# Rótulo de cada coluna na planilha e no texto.
ROTULOS = {
    "secao": "Seção",
    "metodo": "Método",
    "tipo": "Tipo",
    "cod_cta": "Código",
    "nome_cta": "Nome",
    "descricao": "Descrição",
    "nivel": "Nível",
    "ind_cta": "S/A",
    "cod_nat": "Natureza",
    "natureza": "Natureza",
    "cod_cta_sup": "Superior (I050)",
    "superior_publicado": "Superior (J100)",
    "referencial": "Referencial (I051)",
    "aglutinacao": "Aglutinação (I052)",
    "saldo_inicial": "Saldo inicial",
    "debitos": "Débitos",
    "creditos": "Créditos",
    "saldo_final": "Saldo final",
    "divergencia": "Divergência",
    "saldo_atual": "Saldo atual",
    "saldo_anterior": "Saldo anterior",
    "valor": "Valor",
    "valor_atual": "Período atual",
    "valor_anterior": "Período anterior",
    "indice": "Índice",
    "formula": "Fórmula",
    "referencia": "Referência",
    "situacao": "Situação",
    "num_lcto": "Lançamento",
    "data": "Data",
    "historico": "Histórico",
    "debito": "Débito",
    "credito": "Crédito",
}


@dataclass
class Documento:
    """Um relatório pronto para qualquer formato."""

    tipo: str
    template: str
    ctx: ReportContext
    dados: dict[str, Any]
    colunas: list[str]
    linhas: list[dict[str, Any]]
    assinaturas: list[Assinatura] = field(default_factory=list)
    rodape_texto: list[str] = field(default_factory=list)
    # Rótulo de coluna próprio deste documento (sobre `ROTULOS`).
    rotulos: dict[str, str] = field(default_factory=dict)
    # O texto pode ter colunas e linhas próprias: recuo na descrição em vez
    # de uma coluna "tipo", seção sem valor.
    colunas_texto: list[str] | None = None
    linhas_texto: list[dict[str, Any]] | None = None

    def rotulo(self, coluna: str) -> str:
        return self.rotulos.get(coluna) or ROTULOS.get(coluna, coluna)

    @property
    def nome_arquivo(self) -> str:
        return self.tipo


class TipoDeRelatorioDesconhecido(ValueError):
    pass


def contexto(
    session: Session, ecd: ECD, titulo: str, criterios: FilterCriteria | None = None
) -> ReportContext:
    """Cabeçalho comum: empresa, CNPJ formatado, período em pt-BR e hash da ECD."""
    empresa = session.get(Empresa, ecd.empresa_id)
    inicio = (criterios.dt_ini if criterios else None) or ecd.dt_ini
    fim = (criterios.dt_fin if criterios else None) or ecd.dt_fin
    return ReportContext(
        titulo=titulo,
        empresa_nome=empresa.nome if empresa else "",
        empresa_cnpj=formatar_cnpj(empresa.cnpj) if empresa else "",
        periodo_ref=f"{fmt_data(inicio)} a {fmt_data(fim)}",
        hash_ecd=ecd.hash_arquivo or "",
    )


def montar(
    session: Session,
    ecd_id: int,
    tipo: str,
    criterios: FilterCriteria | None = None,
    *,
    visao: str = "hierarquica",
    responsavel: Responsavel | None = None,
    assinar: bool = True,
) -> Documento:
    """Gera o relatório `tipo` da ECD e o empacota para exportação."""
    if tipo not in TIPOS:
        raise TipoDeRelatorioDesconhecido(
            f"Relatório desconhecido: {tipo!r}. Use um de: {', '.join(TIPOS)}"
        )
    ecd = session.get(ECD, ecd_id)
    if ecd is None:
        raise LookupError(f"ECD #{ecd_id} não encontrada")
    criterios = criterios or FilterCriteria()

    documento = _MONTADORES[tipo](session, ecd, criterios, visao)
    if assinar and tipo in COM_ASSINATURA:
        documento.assinaturas = assinaturas(session, ecd_id, responsavel)
        documento.dados["assinaturas"] = documento.assinaturas
    return documento


# ── Montadores ─────────────────────────────────────────────────────────────


def _balancete(session, ecd, criterios, _visao) -> Documento:
    balancete = Balancete(session, ecd.id)
    ctx_rel, linhas = balancete.gerar(criterios, nivel_max=criterios.nivel_ate)
    ctx = contexto(session, ecd, ctx_rel.titulo, criterios)
    ctx.filtros_descricao = ctx_rel.filtros_descricao
    return Documento(
        tipo="balancete",
        template="balancete.html",
        ctx=ctx,
        dados={
            "linhas": linhas,
            "totais": balancete.totais(linhas),
            "conferencia": balancete.conferir(linhas),
        },
        colunas=[
            "cod_cta",
            "nome_cta",
            "nivel",
            "saldo_inicial",
            "debitos",
            "creditos",
            "saldo_final",
            "divergencia",
        ],
        linhas=balancete.to_dict(linhas),
    )


def _balanco(session, ecd, criterios, visao) -> Documento:
    balanco = BalancoPatrimonial(session, ecd.id)
    if visao == "publicacao":
        ctx_rel, grupos, totais = balanco.gerar_publicacao(criterios)
    else:
        ctx_rel, grupos, totais = balanco.gerar(criterios)
    ctx = contexto(session, ecd, ctx_rel.titulo, criterios)
    ctx.filtros_descricao = ctx_rel.filtros_descricao
    linhas = [
        {
            "secao": nome,
            "cod_cta": ln.cod_cta,
            "nome_cta": ln.nome_cta,
            "saldo_atual": ln.saldo_atual,
            "saldo_anterior": ln.saldo_anterior,
        }
        for secao, nome in (("ativo", "Ativo"), ("passivo", "Passivo"), ("pl", "PL"))
        for ln in grupos[secao]
    ]
    rodape = [
        f"Total do Ativo: {fmt_moeda(totais['ativo'])}",
        f"Total do Passivo: {fmt_moeda(totais['passivo'])}",
        f"Total do Patrimônio Líquido: {fmt_moeda(totais['pl'])}",
        f"Total do Passivo + PL: {fmt_moeda(totais['passivo_pl'])}",
    ]
    anterior = ["saldo_anterior"] if totais["tem_anterior"] else []
    rotulos = {}
    if totais.get("data_atual"):
        rotulos["saldo_atual"] = fmt_data(totais["data_atual"])
    if totais.get("data_anterior"):
        rotulos["saldo_anterior"] = fmt_data(totais["data_anterior"])
    return Documento(
        tipo="balanco",
        template="balanco.html",
        ctx=ctx,
        dados={"grupos": grupos, "totais": totais},
        colunas=["secao", "cod_cta", "nome_cta", "saldo_atual", *anterior],
        linhas=linhas,
        rodape_texto=rodape,
        rotulos=rotulos,
        colunas_texto=["secao", "cod_cta", "nome_cta", "saldo_atual", *anterior],
        linhas_texto=[
            {
                "secao": nome,
                "cod_cta": "  " * max(ln.nivel - 1, 0) + ln.cod_cta,
                "nome_cta": ln.nome_cta,
                "saldo_atual": ln.saldo_atual,
                "saldo_anterior": ln.saldo_anterior,
            }
            for secao, nome in (("ativo", "Ativo"), ("passivo", "Passivo"), ("pl", "PL"))
            for ln in grupos[secao]
        ],
    )


def _dre(session, ecd, criterios, _visao) -> Documento:
    ctx_rel, linhas, totais = DRE(session, ecd.id).gerar(criterios, detalhar=True)
    ctx = contexto(session, ecd, ctx_rel.titulo, criterios)
    ctx.filtros_descricao = ctx_rel.filtros_descricao
    anterior = ["valor_anterior"] if totais["tem_anterior"] else []
    return Documento(
        tipo="dre",
        template="dre.html",
        ctx=ctx,
        dados={"linhas": linhas, "totais": totais},
        colunas=["tipo", "cod_cta", "descricao", "valor_atual", "valor_anterior"],
        linhas=[
            {
                "tipo": ln.tipo,
                "cod_cta": ln.cod_cta,
                "descricao": ln.descricao,
                "valor_atual": ln.valor_atual,
                "valor_anterior": ln.valor_anterior,
            }
            for ln in linhas
        ],
        colunas_texto=["descricao", "valor_atual", *anterior],
        linhas_texto=[
            {
                "descricao": ("    " if ln.tipo == "detail" else "")
                + ln.descricao
                + (f" ({ln.cod_cta})" if ln.cod_cta else ""),
                "valor_atual": ln.valor_atual,
                "valor_anterior": ln.valor_anterior,
            }
            for ln in linhas
        ],
        rodape_texto=[f"Resultado líquido: {fmt_moeda(totais['resultado_liquido'])}"],
    )


def _dfc(session, ecd, criterios, _visao) -> Documento:
    ctx_rel, por_metodo, totais = DFC(session, ecd.id).gerar_ambos(criterios)
    ctx = contexto(session, ecd, ctx_rel.titulo, criterios)
    ctx.filtros_descricao = ctx_rel.filtros_descricao
    anterior = ["valor_anterior"] if totais["tem_anterior"] else []

    def linha(metodo: str, ln) -> dict:
        sem_valor = ln.tipo in ("section", "grupo")
        recuo = {"step": "    ", "grupo": "  "}.get(ln.tipo, "")
        return {
            "metodo": metodo.capitalize(),
            "descricao": recuo + (ln.descricao.upper() if ln.tipo == "section" else ln.descricao),
            "valor": "" if sem_valor else ln.valor,
            "valor_anterior": "" if sem_valor else ln.valor_anterior,
        }

    linhas = [linha(metodo, ln) for metodo in ("direto", "indireto") for ln in por_metodo[metodo]]
    rodape = [
        f"Transferências entre contas da própria empresa, fora da DFC (CPC 03, item 9): "
        f"{totais['transferencias_internas_qtd']} lançamento(s), "
        f"{fmt_moeda(totais['transferencias_internas'])}",
        f"Lançamentos sem movimento de caixa, fora da DFC (item 43): "
        f"{totais['lancamentos_sem_caixa']}",
    ] + [
        f"Caixa e equivalentes — {c['cod_cta']} {c['nome_cta']}: "
        f"{fmt_moeda(c['inicial'])} → {fmt_moeda(c['final'])}"
        for c in totais["composicao_caixa"]
    ]
    return Documento(
        tipo="dfc",
        template="dfc.html",
        ctx=ctx,
        dados={"linhas_por_metodo": por_metodo, "linhas": por_metodo["direto"], "totais": totais},
        colunas=["metodo", "descricao", "valor", *anterior],
        linhas=linhas,
        rotulos={"valor": "Período atual"},
        rodape_texto=rodape,
    )


def _situacao(atende: bool | None) -> str:
    return "" if atende is None else ("Atende" if atende else "Não atende")


def _indices(session, ecd, criterios, _visao) -> Documento:
    ctx_rel, indices, totais = IndicesFinanceiros(session, ecd.id).gerar(criterios)
    ctx = contexto(session, ecd, ctx_rel.titulo, criterios)
    ctx.filtros_descricao = ctx_rel.filtros_descricao
    rotulos = {}
    if totais.get("data_atual"):
        rotulos["valor"] = fmt_data(totais["data_atual"])
    if totais.get("data_anterior"):
        rotulos["valor_anterior"] = fmt_data(totais["data_anterior"])
    anterior = ["valor_anterior"] if totais["tem_anterior"] else []
    return Documento(
        tipo="indices",
        template="indices.html",
        rotulos=rotulos,
        ctx=ctx,
        dados={"indices": indices, "totais": totais},
        colunas=["indice", "formula", "valor", *anterior, "referencia", "situacao"],
        linhas=[
            {
                "indice": i.nome,
                "formula": i.formula,
                "valor": i.valor if i.valor is not None else "",
                "valor_anterior": i.valor_anterior if i.valor_anterior is not None else "",
                "referencia": i.referencia,
                "situacao": _situacao(i.atende),
            }
            for i in indices
        ],
    )


def _plano(session, ecd, criterios, _visao) -> Documento:
    ctx_rel, linhas, totais = PlanoDeContas(session, ecd.id).gerar(criterios)
    ctx = contexto(session, ecd, ctx_rel.titulo, criterios)
    ctx.filtros_descricao = ctx_rel.filtros_descricao
    rodape = [
        f"Contas: {totais['total']} ({totais['sinteticas']} sintéticas, "
        f"{totais['analiticas']} analíticas)",
        "Por natureza: "
        + ", ".join(f"{nome} {qtd}" for nome, qtd in totais["por_natureza"].items()),
    ]
    return Documento(
        tipo="plano",
        template="plano.html",
        ctx=ctx,
        dados={"linhas": linhas, "totais": totais},
        colunas=[
            "cod_cta",
            "nome_cta",
            "nivel",
            "ind_cta",
            "natureza",
            "cod_cta_sup",
            "superior_publicado",
            "referencial",
            "aglutinacao",
        ],
        linhas=[
            {
                "cod_cta": ln.cod_cta,
                "nome_cta": ln.nome_cta,
                "nivel": ln.nivel,
                "ind_cta": ln.ind_cta,
                "natureza": ln.natureza,
                "cod_cta_sup": ln.cod_cta_sup,
                "superior_publicado": ln.superior_publicado,
                "referencial": ln.referencial,
                "aglutinacao": ln.aglutinacao,
            }
            for ln in linhas
        ],
        rodape_texto=rodape,
        linhas_texto=[
            {
                "cod_cta": "  " * max(ln.nivel - 1, 0) + ln.cod_cta,
                "nome_cta": ln.nome_cta,
                "nivel": ln.nivel,
                "ind_cta": ln.ind_cta,
                "natureza": ln.natureza,
                "cod_cta_sup": ln.cod_cta_sup,
                "superior_publicado": ln.superior_publicado,
                "referencial": ln.referencial,
                "aglutinacao": ln.aglutinacao,
            }
            for ln in linhas
        ],
    )


def _diario(session, ecd, criterios, _visao) -> Documento:
    ctx_rel, lancamentos, totais = LivroDiario(session, ecd.id).gerar(criterios)
    ctx = contexto(session, ecd, ctx_rel.titulo, criterios)
    ctx.filtros_descricao = ctx_rel.filtros_descricao
    linhas = [
        {
            "num_lcto": lanc.num_lcto,
            "data": lanc.data,
            "cod_cta": p.cod_cta,
            "historico": p.historico,
            "debito": p.debito if p.debito else "",
            "credito": p.credito if p.credito else "",
        }
        for lanc in lancamentos
        for p in lanc.partidas
    ]
    return Documento(
        tipo="diario",
        template="diario.html",
        ctx=ctx,
        dados={"lancamentos": lancamentos, "totais": totais},
        colunas=["num_lcto", "data", "cod_cta", "historico", "debito", "credito"],
        linhas=linhas,
    )


_MONTADORES = {
    "balancete": _balancete,
    "balanco": _balanco,
    "dre": _dre,
    "dfc": _dfc,
    "indices": _indices,
    "plano": _plano,
    "diario": _diario,
}


# ── Formatos ───────────────────────────────────────────────────────────────


def _linhas_rotuladas(documento: Documento) -> tuple[list[str], list[dict]]:
    colunas = [documento.rotulo(c) for c in documento.colunas]
    linhas = [
        {documento.rotulo(c): linha.get(c, "") for c in documento.colunas}
        for linha in documento.linhas
    ]
    return colunas, linhas


def html(documento: Documento, white_label: WhiteLabel | None = None) -> str:
    return ExportEngine().render_html(
        documento.template, documento.ctx, white_label, **documento.dados
    )


def pdf(documento: Documento, white_label: WhiteLabel | None = None) -> bytes:
    """O PDF em memória (WeasyPrint)."""
    from weasyprint import HTML

    from src.reports.export_engine import TEMPLATES_DIR

    return HTML(string=html(documento, white_label), base_url=str(TEMPLATES_DIR)).write_pdf()


def gravar_pdf(documento: Documento, destino: str, white_label: WhiteLabel | None = None) -> str:
    return ExportEngine().export_pdf(
        documento.template, destino, documento.ctx, white_label, **documento.dados
    )


def xlsx(documento: Documento, white_label: WhiteLabel | None = None) -> bytes:
    colunas, linhas = _linhas_rotuladas(documento)
    buffer = io.BytesIO()
    ExportEngine().export_xlsx_to_buffer(
        buffer, documento.ctx, linhas, colunas, documento.ctx.titulo, white_label
    )
    return buffer.getvalue()


def gravar_xlsx(documento: Documento, destino: str, white_label: WhiteLabel | None = None) -> str:
    colunas, linhas = _linhas_rotuladas(documento)
    return ExportEngine().export_xlsx(
        destino, documento.ctx, linhas, colunas, documento.ctx.titulo, white_label
    )


def _celula(valor: Any) -> str:
    if isinstance(valor, bool):
        return "Sim" if valor else "Não"
    if isinstance(valor, float):
        return fmt_moeda(valor)
    if isinstance(valor, datetime.date):
        return fmt_data(valor)
    return "" if valor is None else str(valor)


def texto(documento: Documento) -> str:
    """O relatório em texto puro, colunas alinhadas — para ler, colar ou arquivar.

    Números saem no formato pt-BR (1.234,56), alinhados à direita; o resto
    à esquerda, sem truncar. Largura de cada coluna = o maior conteúdo dela.
    """
    ctx = documento.ctx
    colunas = documento.colunas_texto or documento.colunas
    fonte = documento.linhas_texto if documento.linhas_texto is not None else documento.linhas
    rotulos = [documento.rotulo(c) for c in colunas]
    celulas = [[_celula(linha.get(c, "")) for c in colunas] for linha in fonte]
    numericas = [
        any(isinstance(linha.get(c), (int, float)) for linha in fonte)
        and not any(isinstance(linha.get(c), str) and linha.get(c) for linha in fonte)
        for c in colunas
    ]
    larguras = [
        max([len(rotulos[i])] + [len(linha[i]) for linha in celulas]) for i in range(len(colunas))
    ]

    def formatar(valores: list[str]) -> str:
        partes = [
            valor.rjust(larguras[i]) if numericas[i] else valor.ljust(larguras[i])
            for i, valor in enumerate(valores)
        ]
        return "  ".join(partes).rstrip()

    largura_total = max(sum(larguras) + 2 * (len(larguras) - 1), 60)
    saida = [
        ctx.titulo.upper(),
        f"{ctx.empresa_nome} — CNPJ {ctx.empresa_cnpj}",
        f"Período: {ctx.periodo_ref}    Emitido: {ctx.data_emissao}",
    ]
    if ctx.tem_filtros:
        saida.append(f"Filtros: {ctx.filtros_descricao}")
    saida += ["=" * largura_total, formatar(rotulos), "-" * largura_total]
    saida += [formatar(linha) for linha in celulas]
    saida.append("=" * largura_total)
    saida += documento.rodape_texto
    for assinatura in documento.assinaturas:
        saida += [
            "",
            "",
            "_" * 45,
            assinatura.nome or "",
            assinatura.cargo,
        ]
        if assinatura.documento:
            saida.append(assinatura.documento)
    if ctx.hash_ecd:
        saida += ["", f"Hash da ECD: {ctx.hash_ecd[:12]}"]
    return "\n".join(saida) + "\n"


def exportar(
    documento: Documento, formato: str, white_label: WhiteLabel | None = None
) -> tuple[bytes, str, str]:
    """`(conteúdo, media type, extensão)` do documento no formato pedido."""
    if formato == "pdf":
        return pdf(documento, white_label), "application/pdf", "pdf"
    if formato == "xlsx":
        return (
            xlsx(documento, white_label),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xlsx",
        )
    if formato == "txt":
        return texto(documento).encode("utf-8"), "text/plain; charset=utf-8", "txt"
    raise ValueError(f"Formato desconhecido: {formato!r}. Use um de: {', '.join(FORMATOS)}")
