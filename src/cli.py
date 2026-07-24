"""CLI do SPED-HUB — argparse com subcomandos.

Subcomandos:
    importar-ecd   — Importa arquivo ECD para o banco
    relatorio      — Gera relatórios (balancete, razao, balanco, dre, diario)
    exportar       — Exporta relatório para PDF/XLSX
    validar        — Executa validações de integridade
    filtros        — Gerencia visões salvas de filtros
    info           — Exibe informações do banco
"""

import argparse
import datetime
import hashlib
import logging
import sys
from pathlib import Path

from src.db.models import criar_engine, get_session, init_db
from src.db.repository import Repository
from src.parsers.ecd import ECDParser
from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.balancete import Balancete
from src.reports.razao import Razao
from src.reports.balanco import BalancoPatrimonial
from src.reports.dre import DRE
from src.reports.diario import LivroDiario
from src.reports.export_engine import ExportEngine, WhiteLabel
from src.validators.integridade import ValidadorIntegridade

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("sped-hub.log"), logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("sped-hub")


def _parse_data(valor: str) -> datetime.date:
    """Parse de data flexível: DDMMAAAA ou AAAA-MM-DD."""
    if len(valor) == 8 and valor.isdigit():
        return datetime.date(int(valor[4:8]), int(valor[2:4]), int(valor[0:2]))
    return datetime.date.fromisoformat(valor)


def _get_ecd(session, repo, ecd_id=None):
    """Busca ECD por ID ou última importada."""
    if ecd_id:
        return repo.get_ecd(ecd_id)
    from sqlalchemy import select, desc
    from src.db.models import ECD
    return session.execute(
        select(ECD).order_by(desc(ECD.importado_em)).limit(1)
    ).scalar_one_or_none()


def _build_criterios(args) -> FilterCriteria:
    """Constrói FilterCriteria a partir dos argumentos da CLI."""
    c = FilterCriteria()
    if hasattr(args, "conta") and args.conta:
        c.cod_cta_exato = [args.conta]
    if hasattr(args, "natureza") and args.natureza:
        c.cod_nat = args.natureza.split(",")
    if hasattr(args, "nivel_ate") and args.nivel_ate:
        c.nivel_ate = int(args.nivel_ate)
    if hasattr(args, "dt_ini") and args.dt_ini:
        c.dt_ini = _parse_data(args.dt_ini)
    if hasattr(args, "dt_fin") and args.dt_fin:
        c.dt_fin = _parse_data(args.dt_fin)
    return c


# ═══════════════════════════════════════════════════════════════════════════
# importar-ecd
# ═══════════════════════════════════════════════════════════════════════════

def cmd_importar_ecd(args):
    """Importa arquivo ECD."""
    engine = criar_engine(args.db)
    init_db(engine)
    session = get_session(engine)
    repo = Repository(session)

    caminho = Path(args.arquivo)
    if not caminho.exists():
        logger.error("Arquivo não encontrado: %s", caminho)
        sys.exit(1)

    logger.info("Importando %s...", caminho.name)

    with open(caminho, "rb") as f:
        hash_arquivo = hashlib.sha256(f.read()).hexdigest()

    parser = ECDParser()
    registros = parser.parse_todos(caminho)
    logger.info("Parse concluído: %d registros de interesse", len(registros))

    from collections import defaultdict
    grupos = defaultdict(list)
    for r in registros:
        grupos[r["_reg"]].append(r)

    # ── 0000: Empresa ──
    r0000 = grupos["0000"][0] if grupos["0000"] else {}
    empresa = repo.upsert_empresa({
        "cnpj": str(int(r0000.get("CNPJ", 0))).zfill(14),
        "nome": r0000.get("NOME", ""),
        "uf": r0000.get("UF", ""),
        "ie": r0000.get("IE", ""),
        "cod_mun": str(int(r0000.get("COD_MUN", 0))).zfill(7) if r0000.get("COD_MUN") else None,
        "im": r0000.get("IM", ""),
        "ind_sit_esp": int(r0000.get("IND_SIT_ESP", 0)) if r0000.get("IND_SIT_ESP") else None,
        "ind_nire": int(r0000.get("IND_NIRE", 0)) if r0000.get("IND_NIRE") else None,
        "ind_fin_esc": int(r0000.get("IND_FIN_ESC", 0)) if r0000.get("IND_FIN_ESC") else None,
        "ind_grande_por": int(r0000.get("IND_GRANDE_POR", 0)) if r0000.get("IND_GRANDE_POR") else None,
        "tip_ecd": r0000.get("TIP_ECD", ""),
        "ident_mf": r0000.get("IDENT_MF", ""),
        "ind_esc_cons": r0000.get("IND_ESC_CONS", ""),
    })
    logger.info("Empresa: %s", empresa.nome)

    rI010 = grupos["I010"][0] if grupos["I010"] else {}
    leiaute = rI010.get("COD_VER_LC", "009")

    dt_ini = _parse_data(str(int(r0000.get("DT_INI", 0))).zfill(8))
    dt_fin = _parse_data(str(int(r0000.get("DT_FIN", 0))).zfill(8))

    ecd = repo.criar_ecd(empresa.id, {
        "leiaute": leiaute,
        "dt_ini": dt_ini,
        "dt_fin": dt_fin,
        "ind_esc": rI010.get("IND_ESC", ""),
        "cod_ver_lc": leiaute,
        "hash_arquivo": hash_arquivo,
        "nome_arquivo": caminho.name,
    })
    logger.info("ECD id=%d: %s a %s", ecd.id, dt_ini, dt_fin)

    # ── I050: Plano de Contas ──
    contas = []
    for r in grupos["I050"]:
        contas.append({
            "cod_cta": r.get("COD_CTA", ""),
            "cod_cta_sup": r.get("COD_CTA_SUP", ""),
            "nome_cta": r.get("NOME_CTA", ""),
            "cod_nat": r.get("COD_NAT", "01"),
            "ind_cta": r.get("IND_CTA", "A"),
            "nivel": int(r.get("NIVEL", 0)),
            "dt_alt": _parse_data(str(int(r.get("DT_ALT", 0))).zfill(8)) if r.get("DT_ALT") else None,
        })
    n = repo.inserir_plano_contas(ecd.id, contas)
    logger.info("Plano de contas: %d contas", n)

    # ── I051: Contas Referenciais ──
    refs = []
    for r in grupos["I051"]:
        refs.append({
            "cod_cta": r.get("COD_CTA", ""),
            "cod_ccus": r.get("COD_CCUS", ""),
            "cod_cta_ref": r.get("COD_CTA_REF", ""),
        })
    n = repo.inserir_contas_referenciais(ecd.id, refs)
    logger.info("Contas referenciais: %d", n)

    # ── I052: Aglutinações ──
    agls = []
    for r in grupos["I052"]:
        agls.append({
            "cod_cta": r.get("COD_CTA", ""),
            "cod_ccus": r.get("COD_CCUS", ""),
            "cod_agl": r.get("COD_AGL", ""),
        })
    n = repo.inserir_aglutinacoes(ecd.id, agls)
    logger.info("Aglutinações: %d", n)

    # ── I100: Centros de Custo ──
    centros = []
    for r in grupos["I100"]:
        centros.append({
            "cod_ccus": r.get("COD_CCUS", ""),
            "ccus": r.get("CCUS", ""),
            "dt_alt": _parse_data(str(int(r.get("DT_ALT", 0))).zfill(8)) if r.get("DT_ALT") else None,
        })
    n = repo.inserir_centros_custo(ecd.id, centros)
    logger.info("Centros de custo: %d", n)

    # ── I075: Históricos Padronizados ──
    hists = []
    for r in grupos["I075"]:
        hists.append({
            "cod_hist": r.get("COD_HIST", ""),
            "descr_hist": r.get("DESCR_HIST", ""),
        })
    n = repo.inserir_historicos_padrao(ecd.id, hists)
    logger.info("Históricos padronizados: %d", n)

    # ── I150/I155: Saldos Periódicos ──
    saldos = []
    for r in grupos["I155"]:
        saldos.append({
            "cod_cta": r.get("COD_CTA", ""),
            "cod_ccus": r.get("COD_CCUS", ""),
            "dt_ini": dt_ini,
            "dt_fin": dt_fin,
            "vl_sld_ini": r.get("VL_SLD_INI", 0.0) or 0.0,
            "ind_dc_ini": r.get("IND_DC_INI", "D"),
            "vl_deb": r.get("VL_DEB", 0.0) or 0.0,
            "vl_cred": r.get("VL_CRED", 0.0) or 0.0,
            "vl_sld_fin": r.get("VL_SLD_FIN", 0.0) or 0.0,
            "ind_dc_fin": r.get("IND_DC_FIN", "D"),
        })
    n = repo.inserir_saldos_periodicos(ecd.id, saldos)
    logger.info("Saldos periódicos: %d", n)

    # ── I350/I355: Saldos Resultado ──
    saldos_res = []
    for r in grupos["I355"]:
        saldos_res.append({
            "cod_cta": r.get("COD_CTA", ""),
            "cod_ccus": r.get("COD_CCUS", ""),
            "dt_res": dt_fin,
            "vl_sld_fin": r.get("VL_SLD_FIN", 0.0) or 0.0,
            "ind_dc_fin": r.get("IND_DC_FIN", "D"),
        })
    n = repo.inserir_saldos_resultado(ecd.id, saldos_res)
    logger.info("Saldos resultado: %d", n)

    # ── I200/I250: Lançamentos e Partidas ──
    lancs = []
    for r in grupos["I200"]:
        lancs.append({
            "num_lcto": r.get("NUM_LCTO", ""),
            "dt_lcto": _parse_data(str(int(r.get("DT_LCTO", 0))).zfill(8)) if r.get("DT_LCTO") else dt_ini,
            "vl_lcto": r.get("VL_LCTO", 0.0) or 0.0,
            "ind_lcto": r.get("IND_LCTO", "N"),
            "num_arq": int(r.get("NUM_ARQ", 0)) if r.get("NUM_ARQ") else None,
        })
    n = repo.inserir_lancamentos(ecd.id, lancs)
    logger.info("Lançamentos: %d", n)

    partidas = []
    for r in grupos["I250"]:
        partidas.append({
            "num_lcto": r.get("NUM_LCTO", ""),
            "dt_lcto": _parse_data(str(int(r.get("DT_LCTO", 0))).zfill(8)).isoformat() if r.get("DT_LCTO") else dt_ini.isoformat(),
            "cod_cta": r.get("COD_CTA", ""),
            "cod_ccus": r.get("COD_CCUS", ""),
            "vl_dc": r.get("VL_DC", 0.0) or 0.0,
            "ind_dc": r.get("IND_DC", "D"),
            "num_arq": int(r.get("NUM_ARQ", 0)) if r.get("NUM_ARQ") else None,
            "cod_hist_pad": r.get("COD_HIST_PAD", ""),
            "hist": r.get("HIST", ""),
            "cod_part": r.get("COD_PART", ""),
        })
    n = repo.inserir_partidas(ecd.id, partidas)
    logger.info("Partidas: %d", n)

    repo.commit()
    logger.info("Importação concluída com sucesso. ECD id=%d", ecd.id)


# ═══════════════════════════════════════════════════════════════════════════
# relatorio
# ═══════════════════════════════════════════════════════════════════════════

def cmd_relatorio(args):
    """Gera relatórios."""
    engine = criar_engine(args.db)
    session = get_session(engine)
    repo = Repository(session)

    ecd = _get_ecd(session, repo, args.ecd_id)
    if ecd is None:
        logger.error("Nenhuma ECD encontrada. Importe uma ECD primeiro.")
        sys.exit(1)

    criterios = _build_criterios(args)

    if args.tipo == "balancete":
        _cmd_balancete(session, ecd, criterios)
    elif args.tipo == "razao":
        _cmd_razao(session, ecd, criterios, args)
    elif args.tipo == "balanco":
        _cmd_balanco(session, ecd, criterios, args)
    elif args.tipo == "dre":
        _cmd_dre(session, ecd, criterios)
    elif args.tipo == "diario":
        _cmd_diario(session, ecd, criterios)


def _cmd_balancete(session, ecd, criterios):
    balancete = Balancete(session, ecd.id)
    ctx, linhas = balancete.gerar(criterios, nivel_max=criterios.nivel_ate)

    print(f"\n{'='*80}")
    print(f"  {ctx.titulo}")
    print(f"  Período: {ecd.dt_ini} a {ecd.dt_fin}")
    if ctx.filtros_descricao != "Nenhum filtro aplicado":
        print(f"  Filtros: {ctx.filtros_descricao}")
    print(f"{'='*80}")
    print(f"{'Conta':<20} {'Nome':<35} {'Nív':>3} {'Sld Inicial':>16} {'Débitos':>16} {'Créditos':>16} {'Sld Final':>16} {'Div':>10}")
    print(f"{'-'*20} {'-'*35} {'-'*3} {'-'*16} {'-'*16} {'-'*16} {'-'*16} {'-'*10}")

    for l in linhas:
        indent = "  " * (l.nivel - 1)
        div = f"R$ {l.divergencia:,.2f}" if l.tem_divergencia else "–"
        print(
            f"{indent}{l.cod_cta:<20} {l.nome_cta[:35]:<35} {l.nivel:>3} "
            f"{l.saldo_inicial:>16,.2f} {l.debitos:>16,.2f} {l.creditos:>16,.2f} "
            f"{l.saldo_final:>16,.2f} {div:>10}"
        )

    conf = balancete.conferir(linhas)
    print(f"\nConferência: {conf['status']} — {conf['contas_com_divergencia']}/{conf['total_contas']} contas com divergência")


def _cmd_razao(session, ecd, criterios, args):
    if not args.conta:
        logger.error("Razão requer --conta COD_CTA")
        sys.exit(1)

    razao = Razao(session, ecd.id)
    ctx, linhas = razao.gerar(args.conta, criterios)

    print(f"\n{'='*80}")
    print(f"  {ctx.titulo}")
    print(f"  Período: {ecd.dt_ini} a {ecd.dt_fin}")
    if ctx.filtros_descricao != "Nenhum filtro aplicado":
        print(f"  Filtros: {ctx.filtros_descricao}")
    print(f"{'='*80}")
    print(f"{'Data':<12} {'Nº Lcto':<15} {'Histórico':<30} {'Contrapartidas':<25} {'Débito':>14} {'Crédito':>14} {'Saldo':>14}")
    print(f"{'-'*12} {'-'*15} {'-'*30} {'-'*25} {'-'*14} {'-'*14} {'-'*14}")

    for l in linhas:
        print(
            f"{l.data.isoformat():<12} {l.num_lcto:<15} {l.historico[:30]:<30} "
            f"{l.contrapartidas[:25]:<25} "
            f"{l.debito:>14,.2f} {l.credito:>14,.2f} {l.saldo_corrente:>14,.2f}"
        )


def _cmd_balanco(session, ecd, criterios, args):
    balanco = BalancoPatrimonial(session, ecd.id)
    visao = getattr(args, "visao", "hierarquica")

    if visao == "publicacao":
        ctx, grupos, totais = balanco.gerar_publicacao(criterios)
    else:
        ctx, grupos, totais = balanco.gerar(criterios, visao=visao)

    print(f"\n{'='*80}")
    print(f"  {ctx.titulo}")
    print(f"  Período: {ecd.dt_ini} a {ecd.dt_fin}")
    print(f"{'='*80}")

    for secao, titulo in [("ativo", "ATIVO"), ("passivo", "PASSIVO"), ("pl", "PATRIMÔNIO LÍQUIDO")]:
        print(f"\n  ── {titulo} ──")
        print(f"  {'Conta':<20} {'Nome':<40} {'Saldo':>18}")
        print(f"  {'-'*20} {'-'*40} {'-'*18}")
        for l in grupos[secao]:
            indent = "  " * (l.nivel - 1)
            print(f"  {indent}{l.cod_cta:<20} {l.nome_cta[:40]:<40} {l.saldo_atual:>18,.2f}")
        total = totais[secao]
        print(f"  {'─'*20} {'─'*40} {'─'*18}")
        print(f"  {'TOTAL':<20} {'':<40} {total:>18,.2f}")

    print(f"\n  Ativo = {totais['ativo']:,.2f}  |  Passivo + PL = {totais['passivo_pl']:,.2f}")
    if totais["diferenca"] > 0.01:
        print(f"  ⚠ Diferença: {totais['diferenca']:,.2f}")
    else:
        print(f"  ✓ Balanço fecha!")


def _cmd_dre(session, ecd, criterios):
    dre = DRE(session, ecd.id)
    ctx, linhas, totais = dre.gerar(criterios)

    print(f"\n{'='*80}")
    print(f"  {ctx.titulo}")
    print(f"  Período: {ecd.dt_ini} a {ecd.dt_fin}")
    print(f"{'='*80}")
    print(f"  {'Descrição':<50} {'Valor':>18}")
    print(f"  {'-'*50} {'-'*18}")

    for l in linhas:
        marker = ""
        if l.tipo == "subtotal":
            marker = "  "
        elif l.tipo == "total":
            marker = "══"
        print(f"  {marker}{l.descricao:<50} {l.valor_atual:>18,.2f}")

    print(f"\n  Resultado Líquido: {totais['resultado_liquido']:,.2f}")


def _cmd_diario(session, ecd, criterios):
    diario = LivroDiario(session, ecd.id)
    ctx, lancamentos, totais = diario.gerar(criterios)

    print(f"\n{'='*80}")
    print(f"  {ctx.titulo}")
    print(f"  Período: {ecd.dt_ini} a {ecd.dt_fin}")
    print(f"  Total: {totais['num_lancamentos']} lançamentos")
    print(f"{'='*80}")

    for lanc in lancamentos[:10]:  # Limita a 10 na tela
        print(f"\n  ── Lançamento {lanc.num_lcto} — {lanc.data} ({lanc.ind_lcto}) ──")
        for p in lanc.partidas:
            deb = f"{p.debito:,.2f}" if p.debito else "–"
            cred = f"{p.credito:,.2f}" if p.credito else "–"
            print(f"  {p.cod_cta:<15} {p.historico[:40]:<40} {deb:>14} {cred:>14}")
        print(f"  {'─'*15} {'─'*40} {'─'*14} {'─'*14}")
        print(f"  {'Totais':<15} {'':<40} {lanc.total_debito:>14,.2f} {lanc.total_credito:>14,.2f}")

    if len(lancamentos) > 10:
        print(f"\n  ... e mais {len(lancamentos) - 10} lançamentos.")


# ═══════════════════════════════════════════════════════════════════════════
# exportar
# ═══════════════════════════════════════════════════════════════════════════

def cmd_exportar(args):
    """Exporta relatório para PDF ou XLSX."""
    engine = criar_engine(args.db)
    session = get_session(engine)
    repo = Repository(session)

    ecd = _get_ecd(session, repo, args.ecd_id)
    if ecd is None:
        logger.error("Nenhuma ECD encontrada.")
        sys.exit(1)

    # Busca empresa
    from src.db.models import Empresa
    empresa = session.get(Empresa, ecd.empresa_id)

    criterios = _build_criterios(args)

    # White-label
    wl = WhiteLabel(
        escritorio_nome=args.escritorio or "SPED-HUB",
        cor_primaria=args.cor or "#0B4F6C",
        cor_primaria_clara=args.cor_clara or "#E8F3F7",
        logo_path=args.logo,
    )

    # Carrega logo se fornecida
    if args.logo and Path(args.logo).exists():
        import base64
        with open(args.logo, "rb") as f:
            wl.logo_base64 = f"data:image/png;base64,{base64.b64encode(f.read()).decode()}"

    export = ExportEngine()

    # Preenche contexto
    from src.reports.base import ReportContext
    ctx = ReportContext(
        titulo="",
        empresa_nome=empresa.nome if empresa else "",
        empresa_cnpj=empresa.cnpj if empresa else "",
        periodo_ref=f"{ecd.dt_ini} a {ecd.dt_fin}",
        hash_ecd=ecd.hash_arquivo or "",
    )

    output_path = args.saida
    if not output_path:
        ext = ".pdf" if args.formato == "pdf" else ".xlsx"
        output_path = f"sped_hub_{args.tipo}_{ecd.id}{ext}"

    if args.formato == "pdf":
        _export_pdf(args, session, ecd, ctx, criterios, wl, export, output_path)
    elif args.formato == "xlsx":
        _export_xlsx(args, session, ecd, ctx, criterios, wl, export, output_path)


def _export_pdf(args, session, ecd, ctx, criterios, wl, export, output_path):
    """Exporta para PDF."""
    if args.tipo == "balanco":
        balanco = BalancoPatrimonial(session, ecd.id)
        visao = getattr(args, "visao", "hierarquica")
        if visao == "publicacao":
            ctx_rel, grupos, totais = balanco.gerar_publicacao(criterios)
        else:
            ctx_rel, grupos, totais = balanco.gerar(criterios, visao=visao)
        ctx.titulo = ctx_rel.titulo
        ctx.filtros_descricao = ctx_rel.filtros_descricao
        export.export_pdf("balanco.html", output_path, ctx, wl, grupos=grupos, totais=totais)

    elif args.tipo == "dre":
        dre = DRE(session, ecd.id)
        ctx_rel, linhas, totais = dre.gerar(criterios)
        ctx.titulo = ctx_rel.titulo
        ctx.filtros_descricao = ctx_rel.filtros_descricao
        export.export_pdf("dre.html", output_path, ctx, wl, linhas=linhas, totais=totais)

    elif args.tipo == "diario":
        diario = LivroDiario(session, ecd.id)
        ctx_rel, lancamentos, totais = diario.gerar(criterios)
        ctx.titulo = ctx_rel.titulo
        ctx.filtros_descricao = ctx_rel.filtros_descricao
        export.export_pdf("diario.html", output_path, ctx, wl, lancamentos=lancamentos, totais=totais)

    elif args.tipo == "balancete":
        balancete = Balancete(session, ecd.id)
        ctx_rel, linhas = balancete.gerar(criterios)
        ctx.titulo = ctx_rel.titulo
        ctx.filtros_descricao = ctx_rel.filtros_descricao
        # Balancete usa template genérico — renderiza como tabela
        linhas_dict = balancete.to_dict(linhas)
        colunas = ["cod_cta", "nome_cta", "nivel", "saldo_inicial", "debitos", "creditos", "saldo_final"]
        export.export_xlsx(output_path.replace(".pdf", ".xlsx"), ctx, linhas_dict, colunas, ctx.titulo, wl)
        logger.info("Balancete exportado como XLSX (formato tabular)")
        return

    logger.info("PDF exportado: %s", output_path)


def _export_xlsx(args, session, ecd, ctx, criterios, wl, export, output_path):
    """Exporta para XLSX."""
    if args.tipo == "balancete":
        balancete = Balancete(session, ecd.id)
        ctx_rel, linhas = balancete.gerar(criterios)
        ctx.titulo = ctx_rel.titulo
        linhas_dict = balancete.to_dict(linhas)
        colunas = ["cod_cta", "nome_cta", "nivel", "saldo_inicial", "debitos", "creditos", "saldo_final", "divergencia"]
        export.export_xlsx(output_path, ctx, linhas_dict, colunas, ctx.titulo, wl)

    elif args.tipo == "balanco":
        balanco = BalancoPatrimonial(session, ecd.id)
        visao = getattr(args, "visao", "hierarquica")
        if visao == "publicacao":
            ctx_rel, grupos, totais = balanco.gerar_publicacao(criterios)
        else:
            ctx_rel, grupos, totais = balanco.gerar(criterios, visao=visao)
        ctx.titulo = ctx_rel.titulo

        linhas_dict = []
        for secao, nome in [("ativo", "Ativo"), ("passivo", "Passivo"), ("pl", "PL")]:
            for l in grupos[secao]:
                linhas_dict.append({
                    "secao": nome,
                    "cod_cta": l.cod_cta,
                    "nome_cta": l.nome_cta,
                    "saldo_atual": l.saldo_atual,
                })
        colunas = ["secao", "cod_cta", "nome_cta", "saldo_atual"]
        export.export_xlsx(output_path, ctx, linhas_dict, colunas, ctx.titulo, wl)

    elif args.tipo == "dre":
        dre = DRE(session, ecd.id)
        ctx_rel, linhas, totais = dre.gerar(criterios)
        ctx.titulo = ctx_rel.titulo
        linhas_dict = [
            {"tipo": l.tipo, "descricao": l.descricao, "valor_atual": l.valor_atual}
            for l in linhas
        ]
        colunas = ["tipo", "descricao", "valor_atual"]
        export.export_xlsx(output_path, ctx, linhas_dict, colunas, ctx.titulo, wl)

    elif args.tipo == "diario":
        diario = LivroDiario(session, ecd.id)
        ctx_rel, lancamentos, totais = diario.gerar(criterios)
        ctx.titulo = ctx_rel.titulo
        linhas_dict = []
        for lanc in lancamentos:
            for p in lanc.partidas:
                linhas_dict.append({
                    "num_lcto": lanc.num_lcto,
                    "data": lanc.data,
                    "cod_cta": p.cod_cta,
                    "historico": p.historico,
                    "debito": p.debito if p.debito else "",
                    "credito": p.credito if p.credito else "",
                })
        colunas = ["num_lcto", "data", "cod_cta", "historico", "debito", "credito"]
        export.export_xlsx(output_path, ctx, linhas_dict, colunas, ctx.titulo, wl)

    logger.info("XLSX exportado: %s", output_path)


# ═══════════════════════════════════════════════════════════════════════════
# validar
# ═══════════════════════════════════════════════════════════════════════════

def cmd_validar(args):
    """Executa validações de integridade."""
    engine = criar_engine(args.db)
    session = get_session(engine)
    repo = Repository(session)

    ecd = _get_ecd(session, repo, args.ecd_id)
    if ecd is None:
        logger.error("Nenhuma ECD encontrada.")
        sys.exit(1)

    validador = ValidadorIntegridade(session, ecd.id)
    inconsistencias = validador.validar_todas()
    relatorio = validador.relatorio(inconsistencias)

    print(f"\n{'='*60}")
    print(f"  Validação de Integridade — ECD #{ecd.id}")
    print(f"  Período: {ecd.dt_ini} a {ecd.dt_fin}")
    print(f"{'='*60}")
    print(f"  Status: {relatorio['status']}")
    print(f"  Total de inconsistências: {relatorio['total_inconsistencias']}")
    print(f"  Erros: {relatorio['erros']} | Alertas: {relatorio['alertas']}")
    print()

    if relatorio["detalhes"]:
        for d in relatorio["detalhes"]:
            tag = "ERRO" if d["severidade"] == "erro" else "ALERTA"
            print(f"  [{tag}] [{d['tipo']}] {d['descricao']}")


# ═══════════════════════════════════════════════════════════════════════════
# filtros
# ═══════════════════════════════════════════════════════════════════════════

def cmd_filtros(args):
    """Gerencia visões salvas de filtros."""
    engine = criar_engine(args.db)
    session = get_session(engine)
    repo = Repository(session)

    if args.acao == "listar":
        views = repo.get_filter_views()
        if not views:
            print("Nenhuma visão de filtro salva.")
        for v in views:
            print(f"  {v.nome} — criada em {v.criado_em}")

    elif args.acao == "salvar":
        import json
        criterios = json.loads(args.criterios) if args.criterios else {}
        fv = repo.salvar_filter_view(args.nome, criterios)
        repo.commit()
        print(f"Visão '{fv.nome}' salva com sucesso.")

    elif args.acao == "mostrar":
        fv = repo.get_filter_view(args.nome)
        if fv:
            import json
            print(json.dumps(fv.get_criterios(), indent=2, ensure_ascii=False))
        else:
            print(f"Visão '{args.nome}' não encontrada.")


# ═══════════════════════════════════════════════════════════════════════════
# info
# ═══════════════════════════════════════════════════════════════════════════

def cmd_info(args):
    """Exibe informações do banco."""
    engine = criar_engine(args.db)
    session = get_session(engine)

    from sqlalchemy import select, func
    from src.db.models import Empresa, ECD, PlanoConta, Lancamento, Partida, SaldoPeriodico

    print(f"\nBanco: {args.db}")
    print(f"Empresas: {session.execute(select(func.count(Empresa.id))).scalar()}")
    print(f"ECDs importadas: {session.execute(select(func.count(ECD.id))).scalar()}")

    ecds = session.execute(select(ECD).order_by(ECD.importado_em.desc()).limit(5)).scalars()
    for e in ecds:
        emp = session.get(Empresa, e.empresa_id)
        n_contas = session.execute(
            select(func.count(PlanoConta.id)).where(PlanoConta.ecd_id == e.id)
        ).scalar()
        n_lancs = session.execute(
            select(func.count(Lancamento.id)).where(Lancamento.ecd_id == e.id)
        ).scalar()
        n_partidas = session.execute(
            select(func.count(Partida.id)).join(Lancamento).where(Lancamento.ecd_id == e.id)
        ).scalar()
        print(f"  ECD #{e.id}: {emp.nome if emp else '?'} | {e.dt_ini}–{e.dt_fin} | "
              f"{n_contas} contas | {n_lancs} lançamentos | {n_partidas} partidas")


# ═══════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog="sped-hub",
        description="SPED-HUB — Plataforma de conformidade fiscal",
    )
    sub = parser.add_subparsers(dest="comando")

    # importar-ecd
    p_import = sub.add_parser("importar-ecd", help="Importar arquivo ECD")
    p_import.add_argument("arquivo", help="Caminho do arquivo ECD (.txt)")
    p_import.add_argument("--db", default="sped_hub.db", help="Banco SQLite (default: sped_hub.db)")

    # relatorio
    p_rel = sub.add_parser("relatorio", help="Gerar relatórios")
    p_rel.add_argument("tipo", choices=["balancete", "razao", "balanco", "dre", "diario"],
                       help="Tipo de relatório")
    p_rel.add_argument("--conta", help="Código da conta (obrigatório para razão)")
    p_rel.add_argument("--natureza", help="Filtrar por natureza (01-05,09)")
    p_rel.add_argument("--nivel-ate", help="Profundidade máxima")
    p_rel.add_argument("--dt-ini", help="Data inicial (DDMMAAAA ou AAAA-MM-DD)")
    p_rel.add_argument("--dt-fin", help="Data final (DDMMAAAA ou AAAA-MM-DD)")
    p_rel.add_argument("--visao", choices=["hierarquica", "publicacao"],
                        default="hierarquica", help="Visão do balanço (default: hierarquica)")
    p_rel.add_argument("--ecd-id", type=int, help="ID da ECD (default: última importada)")
    p_rel.add_argument("--db", default="sped_hub.db", help="Banco SQLite")

    # exportar
    p_exp = sub.add_parser("exportar", help="Exportar relatório para PDF/XLSX")
    p_exp.add_argument("tipo", choices=["balancete", "balanco", "dre", "diario"],
                       help="Tipo de relatório")
    p_exp.add_argument("--formato", choices=["pdf", "xlsx"], default="pdf",
                       help="Formato de saída (default: pdf)")
    p_exp.add_argument("--saida", help="Caminho do arquivo de saída")
    p_exp.add_argument("--conta", help="Código da conta (filtro)")
    p_exp.add_argument("--natureza", help="Filtrar por natureza")
    p_exp.add_argument("--nivel-ate", help="Profundidade máxima")
    p_exp.add_argument("--dt-ini", help="Data inicial")
    p_exp.add_argument("--dt-fin", help="Data final")
    p_exp.add_argument("--visao", choices=["hierarquica", "publicacao"],
                        default="hierarquica", help="Visão do balanço")
    p_exp.add_argument("--ecd-id", type=int, help="ID da ECD")
    p_exp.add_argument("--db", default="sped_hub.db", help="Banco SQLite")
    # White-label
    p_exp.add_argument("--escritorio", help="Nome do escritório (white-label)")
    p_exp.add_argument("--cor", help="Cor primária (hex, ex: #0B4F6C)")
    p_exp.add_argument("--cor-clara", help="Cor primária clara (hex, ex: #E8F3F7)")
    p_exp.add_argument("--logo", help="Caminho da logo (PNG)")

    # validar
    p_val = sub.add_parser("validar", help="Validar integridade contábil")
    p_val.add_argument("--ecd-id", type=int, help="ID da ECD")
    p_val.add_argument("--db", default="sped_hub.db", help="Banco SQLite")

    # filtros
    p_filt = sub.add_parser("filtros", help="Gerenciar visões de filtros")
    p_filt.add_argument("acao", choices=["listar", "salvar", "mostrar"])
    p_filt.add_argument("--nome", help="Nome da visão")
    p_filt.add_argument("--criterios", help="Critérios em JSON (para salvar)")
    p_filt.add_argument("--db", default="sped_hub.db", help="Banco SQLite")

    # info
    p_info = sub.add_parser("info", help="Informações do banco")
    p_info.add_argument("--db", default="sped_hub.db", help="Banco SQLite")

    args = parser.parse_args()

    if args.comando == "importar-ecd":
        cmd_importar_ecd(args)
    elif args.comando == "relatorio":
        cmd_relatorio(args)
    elif args.comando == "exportar":
        cmd_exportar(args)
    elif args.comando == "validar":
        cmd_validar(args)
    elif args.comando == "filtros":
        cmd_filtros(args)
    elif args.comando == "info":
        cmd_info(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()