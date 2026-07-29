"""GraphQL API v2 — Fase 9.

Schema completo com strawberry-graphql:
  - Queries: empresas, ecds, relatórios (balanco, dre, dfc, diario), KPIs, notas, validações
  - Tipos: Empresa, ECD, Balanco, DRE, DFC, Diario, KPIs, Notas, Validacao

Integração: app.include_router no dashboard/app.py ou standalone com uvicorn.
"""

import logging

import strawberry
from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from strawberry.fastapi import GraphQLRouter
from strawberry.types import Info

from src.api import requer_api_key
from src.dashboard.services import DashboardService
from src.db.models import (
    ECD,
    Empresa,
    Lancamento,
    Partida,
    PlanoConta,
    criar_engine,
    get_session,
    init_db,
)
from src.reports.balanco import BalancoPatrimonial
from src.reports.dfc import DFC
from src.reports.diario import LivroDiario
from src.reports.dre import DRE
from src.settings import database_reference
from src.validators.integridade import ValidadorIntegridade

logger = logging.getLogger("sped-hub.graphql")


def _get_db_path() -> str:

    return database_reference()


def _get_session() -> Session:
    engine = criar_engine(_get_db_path())
    init_db(engine)
    return get_session(engine)


# ── Tipos GraphQL ───────────────────────────────────────────────────────────


@strawberry.type
class EmpresaType:
    id: int
    cnpj: str
    nome: str
    uf: str | None = None
    ie: str | None = None
    cod_mun: str | None = None
    im: str | None = None
    ind_sit_esp: int | None = None
    ind_nire: int | None = None
    ind_fin_esc: int | None = None
    ind_grande_por: int | None = None
    tip_ecd: str | None = None
    ident_mf: str | None = None
    ind_esc_cons: str | None = None


@strawberry.type
class ECDType:
    id: int
    empresa_id: int
    empresa_nome: str
    leiaute: str
    dt_ini: str
    dt_fin: str
    ind_esc: str | None = None
    cod_ver_lc: str | None = None
    nome_arquivo: str | None = None
    hash_arquivo: str | None = None
    importado_em: str | None = None
    n_contas: int = 0
    n_lancamentos: int = 0
    n_partidas: int = 0


@strawberry.type
class LinhaBalancoType:
    cod_cta: str
    nome_cta: str
    nivel: int
    saldo_atual: float
    saldo_anterior: float


@strawberry.type
class BalancoType:
    titulo: str
    ativo: list[LinhaBalancoType]
    passivo: list[LinhaBalancoType]
    patrimonio_liquido: list[LinhaBalancoType]
    total_ativo: float
    total_passivo: float
    total_pl: float
    total_passivo_pl: float
    diferenca: float
    fecha: bool


@strawberry.type
class LinhaDREType:
    tipo: str
    descricao: str
    valor_atual: float
    valor_anterior: float


@strawberry.type
class DREType:
    titulo: str
    linhas: list[LinhaDREType]
    resultado_liquido: float
    receita_bruta: float
    lucro_bruto: float


@strawberry.type
class LinhaDFCType:
    tipo: str
    descricao: str
    valor: float
    valor_anterior: float


@strawberry.type
class DFCType:
    titulo: str
    linhas: list[LinhaDFCType]
    variacao_caixa: float
    operacional: float
    investimento: float
    financiamento: float


@strawberry.type
class PartidaDiarioType:
    cod_cta: str
    historico: str
    debito: float
    credito: float


@strawberry.type
class LancamentoDiarioType:
    num_lcto: str
    data: str
    ind_lcto: str
    partidas: list[PartidaDiarioType]
    total_debito: float
    total_credito: float


@strawberry.type
class DiarioType:
    titulo: str
    lancamentos: list[LancamentoDiarioType]
    total_lancamentos: int
    total_debitos: float
    total_creditos: float


@strawberry.type
class KPICardType:
    titulo: str
    valor: float
    formato: str
    tendencia: str
    descricao: str


@strawberry.type
class KPIsType:
    empresa: str
    cnpj: str
    periodo: str
    ativo_total: float
    passivo_total: float
    patrimonio_liquido: float
    receita_liquida: float
    resultado_liquido: float
    num_lancamentos: int
    num_contas: int
    kpis: list[KPICardType]


@strawberry.type
class NotaType:
    numero: int
    titulo: str
    texto: str
    valor: float
    tipo: str


@strawberry.type
class InconsistenciaType:
    tipo: str
    severidade: str
    descricao: str


@strawberry.type
class ValidacaoType:
    status: str
    total_inconsistencias: int
    erros: int
    alertas: int
    detalhes: list[InconsistenciaType]


@strawberry.type
class EvolucaoMultiType:
    labels: list[str]
    ativos: list[float]
    passivos: list[float]
    pls: list[float]
    resultados: list[float]
    num_periodos: int


@strawberry.type
class PageInfo:
    pagina: int
    limite: int
    total: int
    total_paginas: int


@strawberry.type
class EmpresasPage:
    dados: list[EmpresaType]
    page_info: PageInfo


@strawberry.type
class ECDsPage:
    dados: list[ECDType]
    page_info: PageInfo


# ── Queries ─────────────────────────────────────────────────────────────────


@strawberry.type
class Query:
    @strawberry.field
    def health(self) -> str:
        return "ok"

    @strawberry.field
    def empresas(
        self,
        info: Info,
        pagina: int = 1,
        limite: int = 20,
    ) -> EmpresasPage:
        session = _get_session()
        try:
            total = session.execute(select(func.count(Empresa.id))).scalar() or 0
            offset = (pagina - 1) * limite
            empresas = (
                session.execute(select(Empresa).order_by(Empresa.nome).offset(offset).limit(limite))
                .scalars()
                .all()
            )

            return EmpresasPage(
                dados=[
                    EmpresaType(
                        id=e.id,
                        cnpj=e.cnpj,
                        nome=e.nome,
                        uf=e.uf,
                        ie=e.ie,
                        cod_mun=e.cod_mun,
                        im=e.im,
                        ind_sit_esp=e.ind_sit_esp,
                        ind_nire=e.ind_nire,
                        ind_fin_esc=e.ind_fin_esc,
                        ind_grande_por=e.ind_grande_por,
                        tip_ecd=e.tip_ecd,
                        ident_mf=e.ident_mf,
                        ind_esc_cons=e.ind_esc_cons,
                    )
                    for e in empresas
                ],
                page_info=PageInfo(
                    pagina=pagina,
                    limite=limite,
                    total=total,
                    total_paginas=max(1, (total + limite - 1) // limite),
                ),
            )
        finally:
            session.close()

    @strawberry.field
    def empresa(self, info: Info, id: int) -> EmpresaType | None:
        session = _get_session()
        try:
            e = session.get(Empresa, id)
            if not e:
                return None
            return EmpresaType(
                id=e.id,
                cnpj=e.cnpj,
                nome=e.nome,
                uf=e.uf,
                ie=e.ie,
                cod_mun=e.cod_mun,
                im=e.im,
                ind_sit_esp=e.ind_sit_esp,
                ind_nire=e.ind_nire,
                ind_fin_esc=e.ind_fin_esc,
                ind_grande_por=e.ind_grande_por,
                tip_ecd=e.tip_ecd,
                ident_mf=e.ident_mf,
                ind_esc_cons=e.ind_esc_cons,
            )
        finally:
            session.close()

    @strawberry.field
    def ecds(
        self,
        info: Info,
        empresa_id: int | None = None,
        pagina: int = 1,
        limite: int = 20,
    ) -> ECDsPage:
        session = _get_session()
        try:
            query_count = select(func.count(ECD.id))
            if empresa_id:
                query_count = query_count.where(ECD.empresa_id == empresa_id)
            total = session.execute(query_count).scalar() or 0

            offset = (pagina - 1) * limite
            query_ecds = select(ECD, Empresa.nome).join(Empresa).order_by(ECD.importado_em.desc())
            if empresa_id:
                query_ecds = query_ecds.where(ECD.empresa_id == empresa_id)
            ecds = session.execute(query_ecds.offset(offset).limit(limite)).all()

            dados = []
            for ecd, nome in ecds:
                n_contas = (
                    session.execute(
                        select(func.count(PlanoConta.id)).where(PlanoConta.ecd_id == ecd.id)
                    ).scalar()
                    or 0
                )
                n_lancs = (
                    session.execute(
                        select(func.count(Lancamento.id)).where(Lancamento.ecd_id == ecd.id)
                    ).scalar()
                    or 0
                )
                n_partidas = (
                    session.execute(
                        select(func.count(Partida.id))
                        .join(Lancamento)
                        .where(Lancamento.ecd_id == ecd.id)
                    ).scalar()
                    or 0
                )

                dados.append(
                    ECDType(
                        id=ecd.id,
                        empresa_id=ecd.empresa_id,
                        empresa_nome=nome,
                        leiaute=ecd.leiaute,
                        dt_ini=ecd.dt_ini.isoformat() if ecd.dt_ini else "",
                        dt_fin=ecd.dt_fin.isoformat() if ecd.dt_fin else "",
                        ind_esc=ecd.ind_esc,
                        cod_ver_lc=ecd.cod_ver_lc,
                        nome_arquivo=ecd.nome_arquivo,
                        hash_arquivo=ecd.hash_arquivo,
                        importado_em=ecd.importado_em.isoformat() if ecd.importado_em else "",
                        n_contas=n_contas,
                        n_lancamentos=n_lancs,
                        n_partidas=n_partidas,
                    )
                )

            return ECDsPage(
                dados=dados,
                page_info=PageInfo(
                    pagina=pagina,
                    limite=limite,
                    total=total,
                    total_paginas=max(1, (total + limite - 1) // limite),
                ),
            )
        finally:
            session.close()

    @strawberry.field
    def ecd(self, info: Info, id: int) -> ECDType | None:
        session = _get_session()
        try:
            ecd = session.get(ECD, id)
            if not ecd:
                return None
            empresa = session.get(Empresa, ecd.empresa_id)
            n_contas = (
                session.execute(
                    select(func.count(PlanoConta.id)).where(PlanoConta.ecd_id == id)
                ).scalar()
                or 0
            )
            n_lancs = (
                session.execute(
                    select(func.count(Lancamento.id)).where(Lancamento.ecd_id == id)
                ).scalar()
                or 0
            )
            n_partidas = (
                session.execute(
                    select(func.count(Partida.id)).join(Lancamento).where(Lancamento.ecd_id == id)
                ).scalar()
                or 0
            )

            return ECDType(
                id=ecd.id,
                empresa_id=ecd.empresa_id,
                empresa_nome=empresa.nome if empresa else "",
                leiaute=ecd.leiaute,
                dt_ini=ecd.dt_ini.isoformat() if ecd.dt_ini else "",
                dt_fin=ecd.dt_fin.isoformat() if ecd.dt_fin else "",
                ind_esc=ecd.ind_esc,
                cod_ver_lc=ecd.cod_ver_lc,
                nome_arquivo=ecd.nome_arquivo,
                hash_arquivo=ecd.hash_arquivo,
                importado_em=ecd.importado_em.isoformat() if ecd.importado_em else "",
                n_contas=n_contas,
                n_lancamentos=n_lancs,
                n_partidas=n_partidas,
            )
        finally:
            session.close()

    @strawberry.field
    def balanco(self, info: Info, ecd_id: int, visao: str = "hierarquica") -> BalancoType | None:
        session = _get_session()
        try:
            balanco = BalancoPatrimonial(session, ecd_id)
            if visao == "publicacao":
                ctx, grupos, totais = balanco.gerar_publicacao()
            else:
                ctx, grupos, totais = balanco.gerar()

            return BalancoType(
                titulo=ctx.titulo,
                ativo=[
                    LinhaBalancoType(
                        cod_cta=ln.cod_cta,
                        nome_cta=ln.nome_cta,
                        nivel=ln.nivel,
                        saldo_atual=ln.saldo_atual,
                        saldo_anterior=ln.saldo_anterior,
                    )
                    for ln in grupos["ativo"]
                ],
                passivo=[
                    LinhaBalancoType(
                        cod_cta=ln.cod_cta,
                        nome_cta=ln.nome_cta,
                        nivel=ln.nivel,
                        saldo_atual=ln.saldo_atual,
                        saldo_anterior=ln.saldo_anterior,
                    )
                    for ln in grupos["passivo"]
                ],
                patrimonio_liquido=[
                    LinhaBalancoType(
                        cod_cta=ln.cod_cta,
                        nome_cta=ln.nome_cta,
                        nivel=ln.nivel,
                        saldo_atual=ln.saldo_atual,
                        saldo_anterior=ln.saldo_anterior,
                    )
                    for ln in grupos["pl"]
                ],
                total_ativo=totais["ativo"],
                total_passivo=totais["passivo"],
                total_pl=totais["pl"],
                total_passivo_pl=totais["passivo_pl"],
                diferenca=totais["diferenca"],
                fecha=totais["diferenca"] < 0.01,
            )
        finally:
            session.close()

    @strawberry.field
    def dre(self, info: Info, ecd_id: int) -> DREType | None:
        session = _get_session()
        try:
            dre = DRE(session, ecd_id)
            ctx, linhas, totais = dre.gerar()

            return DREType(
                titulo=ctx.titulo,
                linhas=[
                    LinhaDREType(
                        tipo=ln.tipo,
                        descricao=ln.descricao,
                        valor_atual=ln.valor_atual,
                        valor_anterior=ln.valor_anterior,
                    )
                    for ln in linhas
                ],
                resultado_liquido=totais.get("resultado_liquido", 0.0),
                receita_bruta=totais.get("receita_bruta", 0.0),
                lucro_bruto=totais.get("lucro_bruto", 0.0),
            )
        finally:
            session.close()

    @strawberry.field
    def dfc(self, info: Info, ecd_id: int) -> DFCType | None:
        session = _get_session()
        try:
            dfc = DFC(session, ecd_id)
            ctx, linhas, totais = dfc.gerar()

            return DFCType(
                titulo=ctx.titulo,
                linhas=[
                    LinhaDFCType(
                        tipo=ln.tipo,
                        descricao=ln.descricao,
                        valor=ln.valor,
                        valor_anterior=ln.valor_anterior,
                    )
                    for ln in linhas
                ],
                variacao_caixa=totais.get("variacao_caixa", 0.0),
                operacional=totais.get("operacional", 0.0),
                investimento=totais.get("investimento", 0.0),
                financiamento=totais.get("financiamento", 0.0),
            )
        finally:
            session.close()

    @strawberry.field
    def diario(
        self, info: Info, ecd_id: int, pagina: int = 1, limite: int = 50
    ) -> DiarioType | None:
        session = _get_session()
        try:
            diario = LivroDiario(session, ecd_id)
            ctx, lancamentos, totais = diario.gerar()

            total = len(lancamentos)
            total_paginas = max(1, (total + limite - 1) // limite)
            pagina = max(1, min(pagina, total_paginas))
            inicio = (pagina - 1) * limite
            fim = inicio + limite
            pagina_lancs = lancamentos[inicio:fim]

            return DiarioType(
                titulo=ctx.titulo,
                lancamentos=[
                    LancamentoDiarioType(
                        num_lcto=ln.num_lcto,
                        data=str(ln.data),
                        ind_lcto=ln.ind_lcto if hasattr(ln, "ind_lcto") else "",
                        partidas=[
                            PartidaDiarioType(
                                cod_cta=p.cod_cta,
                                historico=p.historico if hasattr(p, "historico") else "",
                                debito=p.debito if hasattr(p, "debito") else 0.0,
                                credito=p.credito if hasattr(p, "credito") else 0.0,
                            )
                            for p in (ln.partidas if hasattr(ln, "partidas") else [])
                        ],
                        total_debito=ln.total_debito if hasattr(ln, "total_debito") else 0.0,
                        total_credito=ln.total_credito if hasattr(ln, "total_credito") else 0.0,
                    )
                    for ln in pagina_lancs
                ],
                total_lancamentos=total,
                total_debitos=totais["total_debitos"],
                total_creditos=totais["total_creditos"],
            )
        finally:
            session.close()

    @strawberry.field
    def kpis(self, info: Info, ecd_id: int) -> KPIsType | None:
        session = _get_session()
        try:
            svc = DashboardService(session, ecd_id)
            data = svc.get_dashboard_data()

            return KPIsType(
                empresa=data.empresa_nome,
                cnpj=data.empresa_cnpj,
                periodo=data.periodo,
                ativo_total=data.ativo_total,
                passivo_total=data.passivo_total,
                patrimonio_liquido=data.pl_total,
                receita_liquida=data.receita_liquida,
                resultado_liquido=data.resultado_liquido,
                num_lancamentos=data.num_lancamentos,
                num_contas=data.num_contas,
                kpis=[
                    KPICardType(
                        titulo=k.titulo,
                        valor=k.valor,
                        formato=k.formato,
                        tendencia=k.tendencia,
                        descricao=k.descricao,
                    )
                    for k in data.kpis
                ],
            )
        finally:
            session.close()

    @strawberry.field
    def notas(self, info: Info, ecd_id: int) -> list[NotaType]:
        session = _get_session()
        try:
            svc = DashboardService(session, ecd_id)
            notas = svc.get_notas_explicativas()
            return [
                NotaType(
                    numero=n["numero"],
                    titulo=n["titulo"],
                    texto=n["texto"],
                    valor=n["valor"],
                    tipo=n["tipo"],
                )
                for n in notas
            ]
        finally:
            session.close()

    @strawberry.field
    def validar(self, info: Info, ecd_id: int) -> ValidacaoType:
        session = _get_session()
        try:
            validador = ValidadorIntegridade(session, ecd_id)
            inconsistencias = validador.validar_todas()
            relatorio = validador.relatorio(inconsistencias)
            return ValidacaoType(
                status=relatorio["status"],
                total_inconsistencias=relatorio["total_inconsistencias"],
                erros=relatorio["erros"],
                alertas=relatorio["alertas"],
                detalhes=[
                    InconsistenciaType(
                        tipo=d["tipo"],
                        severidade=d["severidade"],
                        descricao=d["descricao"],
                    )
                    for d in relatorio["detalhes"]
                ],
            )
        finally:
            session.close()

    @strawberry.field
    def evolucao_multi(self, info: Info, ecd_id: int) -> EvolucaoMultiType | None:
        session = _get_session()
        try:
            svc = DashboardService(session, ecd_id)
            data = svc.get_evolucao_multi_periodo()
            if not data:
                return None
            return EvolucaoMultiType(
                labels=data.get("labels", []),
                ativos=data.get("ativos", []),
                passivos=data.get("passivos", []),
                pls=data.get("pls", []),
                resultados=data.get("resultados", []),
                num_periodos=data.get("num_periodos", 0),
            )
        finally:
            session.close()


# ── Schema e Router ─────────────────────────────────────────────────────────

schema = strawberry.Schema(query=Query)
graphql_router = GraphQLRouter(
    schema,
    path="/api/v2/graphql",
    dependencies=[Depends(requer_api_key)],
    allow_queries_via_get=False,
)
