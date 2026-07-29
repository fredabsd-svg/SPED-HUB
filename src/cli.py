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
import logging
import sys
from pathlib import Path

from src.db.models import criar_engine, get_session, init_db
from src.db.repository import Repository
from src.ecd_importer import ECDImportService
from src.filters.engine import FilterCriteria
from src.reports.balancete import Balancete
from src.reports.balanco import BalancoPatrimonial
from src.reports.diario import LivroDiario
from src.reports.dre import DRE
from src.reports.export_engine import ExportEngine, WhiteLabel
from src.reports.razao import Razao
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
    from sqlalchemy import desc, select

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
    """Importa arquivo ECD de forma incremental."""
    caminho = Path(args.arquivo)
    if not caminho.is_file():
        logger.error("Arquivo não encontrado: %s", caminho)
        raise SystemExit(1)

    engine = criar_engine(args.db)
    init_db(engine)
    session = get_session(engine)
    try:
        logger.info("Importando %s em modo incremental...", caminho.name)
        result = ECDImportService(session).importar(
            caminho,
            progress=lambda pct, msg: logger.info("%.0f%% — %s", pct, msg),
        )
        logger.info(
            "Importação concluída: ECD #%d, %d contas, %d lançamentos, %d partidas",
            result.ecd_id,
            result.contas,
            result.lancamentos,
            result.partidas,
        )
        return result
    except Exception:
        logger.exception("Falha ao importar %s", caminho.name)
        # O traceback já foi registrado; o encadeamento só poluiria a saída da CLI.
        raise SystemExit(1) from None
    finally:
        session.close()


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
    print(
        f"{'Conta':<20} {'Nome':<35} {'Nív':>3} {'Sld Inicial':>16} {'Débitos':>16} {'Créditos':>16} {'Sld Final':>16} {'Div':>10}"
    )
    print(f"{'-'*20} {'-'*35} {'-'*3} {'-'*16} {'-'*16} {'-'*16} {'-'*16} {'-'*10}")

    for ln in linhas:
        indent = "  " * (ln.nivel - 1)
        div = f"R$ {ln.divergencia:,.2f}" if ln.tem_divergencia else "–"
        print(
            f"{indent}{ln.cod_cta:<20} {ln.nome_cta[:35]:<35} {ln.nivel:>3} "
            f"{ln.saldo_inicial:>16,.2f} {ln.debitos:>16,.2f} {ln.creditos:>16,.2f} "
            f"{ln.saldo_final:>16,.2f} {div:>10}"
        )

    conf = balancete.conferir(linhas)
    print(
        f"\nConferência: {conf['status']} — {conf['contas_com_divergencia']}/{conf['total_contas']} contas com divergência"
    )


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
    print(
        f"{'Data':<12} {'Nº Lcto':<15} {'Histórico':<30} {'Contrapartidas':<25} {'Débito':>14} {'Crédito':>14} {'Saldo':>14}"
    )
    print(f"{'-'*12} {'-'*15} {'-'*30} {'-'*25} {'-'*14} {'-'*14} {'-'*14}")

    for ln in linhas:
        print(
            f"{ln.data.isoformat():<12} {ln.num_lcto:<15} {ln.historico[:30]:<30} "
            f"{ln.contrapartidas[:25]:<25} "
            f"{ln.debito:>14,.2f} {ln.credito:>14,.2f} {ln.saldo_corrente:>14,.2f}"
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
        for ln in grupos[secao]:
            indent = "  " * (ln.nivel - 1)
            print(f"  {indent}{ln.cod_cta:<20} {ln.nome_cta[:40]:<40} {ln.saldo_atual:>18,.2f}")
        total = totais[secao]
        print(f"  {'─'*20} {'─'*40} {'─'*18}")
        print(f"  {'TOTAL':<20} {'':<40} {total:>18,.2f}")

    print(f"\n  Ativo = {totais['ativo']:,.2f}  |  Passivo + PL = {totais['passivo_pl']:,.2f}")
    if totais["diferenca"] > 0.01:
        print(f"  ⚠ Diferença: {totais['diferenca']:,.2f}")
    else:
        print("  ✓ Balanço fecha!")


def _cmd_dre(session, ecd, criterios):
    dre = DRE(session, ecd.id)
    ctx, linhas, totais = dre.gerar(criterios)

    print(f"\n{'='*80}")
    print(f"  {ctx.titulo}")
    print(f"  Período: {ecd.dt_ini} a {ecd.dt_fin}")
    print(f"{'='*80}")
    print(f"  {'Descrição':<50} {'Valor':>18}")
    print(f"  {'-'*50} {'-'*18}")

    for ln in linhas:
        marker = ""
        if ln.tipo == "subtotal":
            marker = "  "
        elif ln.tipo == "total":
            marker = "══"
        print(f"  {marker}{ln.descricao:<50} {ln.valor_atual:>18,.2f}")

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
        export.export_pdf(
            "diario.html", output_path, ctx, wl, lancamentos=lancamentos, totais=totais
        )

    elif args.tipo == "balancete":
        balancete = Balancete(session, ecd.id)
        ctx_rel, linhas = balancete.gerar(criterios)
        ctx.titulo = ctx_rel.titulo
        ctx.filtros_descricao = ctx_rel.filtros_descricao
        # Balancete usa template genérico — renderiza como tabela
        linhas_dict = balancete.to_dict(linhas)
        colunas = [
            "cod_cta",
            "nome_cta",
            "nivel",
            "saldo_inicial",
            "debitos",
            "creditos",
            "saldo_final",
        ]
        export.export_xlsx(
            output_path.replace(".pdf", ".xlsx"), ctx, linhas_dict, colunas, ctx.titulo, wl
        )
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
        colunas = [
            "cod_cta",
            "nome_cta",
            "nivel",
            "saldo_inicial",
            "debitos",
            "creditos",
            "saldo_final",
            "divergencia",
        ]
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
            for ln in grupos[secao]:
                linhas_dict.append(
                    {
                        "secao": nome,
                        "cod_cta": ln.cod_cta,
                        "nome_cta": ln.nome_cta,
                        "saldo_atual": ln.saldo_atual,
                    }
                )
        colunas = ["secao", "cod_cta", "nome_cta", "saldo_atual"]
        export.export_xlsx(output_path, ctx, linhas_dict, colunas, ctx.titulo, wl)

    elif args.tipo == "dre":
        dre = DRE(session, ecd.id)
        ctx_rel, linhas, totais = dre.gerar(criterios)
        ctx.titulo = ctx_rel.titulo
        linhas_dict = [
            {"tipo": ln.tipo, "descricao": ln.descricao, "valor_atual": ln.valor_atual}
            for ln in linhas
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
                linhas_dict.append(
                    {
                        "num_lcto": lanc.num_lcto,
                        "data": lanc.data,
                        "cod_cta": p.cod_cta,
                        "historico": p.historico,
                        "debito": p.debito if p.debito else "",
                        "credito": p.credito if p.credito else "",
                    }
                )
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

    from sqlalchemy import func, select

    from src.db.models import ECD, Empresa, Lancamento, Partida, PlanoConta

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
        print(
            f"  ECD #{e.id}: {emp.nome if emp else '?'} | {e.dt_ini}–{e.dt_fin} | "
            f"{n_contas} contas | {n_lancs} lançamentos | {n_partidas} partidas"
        )


# ═══════════════════════════════════════════════════════════════════════════
# migrar
# ═══════════════════════════════════════════════════════════════════════════


def cmd_migrar(args):
    """Aplica migrações de schema (Alembic)."""
    from src.db.migrations import revisao_atual, revisao_head, stamp_head, upgrade_head

    # `--db` aceita caminho de arquivo ou URL; sem `--db`, vale DATABASE_URL.
    url = args.db if args.db != "sped_hub.db" else None
    engine = criar_engine(url) if url else criar_engine()
    try:
        atual = revisao_atual(engine)
    finally:
        engine.dispose()
    head = revisao_head()

    if args.acao == "status":
        print(f"\nRevisão do banco: {atual or '(nenhuma — nunca migrado)'}")
        print(f"Revisão disponível: {head}")
        if atual == head:
            print("Schema em dia.")
        elif atual is None:
            print("Rode `sped-hub migrar aplicar` (banco novo) ou")
            print("`sped-hub migrar adotar` (banco já criado por versões anteriores).")
        else:
            print("Há migrações pendentes: rode `sped-hub migrar aplicar`.")
        return

    if args.acao == "adotar":
        if atual is not None:
            print(f"Banco já está sob controle do Alembic (revisão {atual}).")
            return
        stamp_head(url)
        print(f"Banco adotado na revisão {head}, sem executar migrações.")
        return

    nova = upgrade_head(url)
    if atual == nova:
        print(f"Nada a fazer — schema já estava em {nova}.")
    else:
        print(f"Schema migrado de {atual or '(vazio)'} para {nova}.")


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
    p_rel.add_argument(
        "tipo", choices=["balancete", "razao", "balanco", "dre", "diario"], help="Tipo de relatório"
    )
    p_rel.add_argument("--conta", help="Código da conta (obrigatório para razão)")
    p_rel.add_argument("--natureza", help="Filtrar por natureza (01-05,09)")
    p_rel.add_argument("--nivel-ate", help="Profundidade máxima")
    p_rel.add_argument("--dt-ini", help="Data inicial (DDMMAAAA ou AAAA-MM-DD)")
    p_rel.add_argument("--dt-fin", help="Data final (DDMMAAAA ou AAAA-MM-DD)")
    p_rel.add_argument(
        "--visao",
        choices=["hierarquica", "publicacao"],
        default="hierarquica",
        help="Visão do balanço (default: hierarquica)",
    )
    p_rel.add_argument("--ecd-id", type=int, help="ID da ECD (default: última importada)")
    p_rel.add_argument("--db", default="sped_hub.db", help="Banco SQLite")

    # exportar
    p_exp = sub.add_parser("exportar", help="Exportar relatório para PDF/XLSX")
    p_exp.add_argument(
        "tipo", choices=["balancete", "balanco", "dre", "diario"], help="Tipo de relatório"
    )
    p_exp.add_argument(
        "--formato", choices=["pdf", "xlsx"], default="pdf", help="Formato de saída (default: pdf)"
    )
    p_exp.add_argument("--saida", help="Caminho do arquivo de saída")
    p_exp.add_argument("--conta", help="Código da conta (filtro)")
    p_exp.add_argument("--natureza", help="Filtrar por natureza")
    p_exp.add_argument("--nivel-ate", help="Profundidade máxima")
    p_exp.add_argument("--dt-ini", help="Data inicial")
    p_exp.add_argument("--dt-fin", help="Data final")
    p_exp.add_argument(
        "--visao",
        choices=["hierarquica", "publicacao"],
        default="hierarquica",
        help="Visão do balanço",
    )
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

    # migrar
    p_mig = sub.add_parser("migrar", help="Aplicar migrações de schema (Alembic)")
    p_mig.add_argument(
        "acao",
        nargs="?",
        default="status",
        choices=["status", "aplicar", "adotar"],
        help="status (default) | aplicar | adotar (banco pré-existente)",
    )
    p_mig.add_argument("--db", default="sped_hub.db", help="Banco (URL ou caminho SQLite)")

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
    elif args.comando == "migrar":
        cmd_migrar(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
