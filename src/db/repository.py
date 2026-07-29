"""Repositório — camada de acesso a dados.

Operações CRUD e consultas agregadas sobre os modelos SQLAlchemy.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    ECD,
    Aglutinacao,
    CentroCusto,
    ContaReferencial,
    Empresa,
    FilterView,
    HistoricoPadrao,
    Lancamento,
    Mapeamento,
    Participante,
    Partida,
    PlanoConta,
    SaldoPeriodico,
    SaldoResultado,
)


class Repository:
    """Repositório unificado para operações no banco."""

    def __init__(self, session: Session):
        self.session = session

    # ── Empresa ─────────────────────────────────────────────────────────

    def upsert_empresa(self, dados: dict) -> Empresa:
        """Insere ou atualiza empresa por CNPJ."""
        emp = self.session.execute(
            select(Empresa).where(Empresa.cnpj == dados["cnpj"])
        ).scalar_one_or_none()

        if emp:
            for k, v in dados.items():
                if hasattr(emp, k) and v is not None:
                    setattr(emp, k, v)
        else:
            emp = Empresa(**dados)
            self.session.add(emp)

        self.session.flush()
        return emp

    def get_empresa_por_cnpj(self, cnpj: str) -> Empresa | None:
        return self.session.execute(
            select(Empresa).where(Empresa.cnpj == cnpj)
        ).scalar_one_or_none()

    # ── ECD ─────────────────────────────────────────────────────────────

    def criar_ecd(self, empresa_id: int, dados: dict) -> ECD:
        ecd = ECD(empresa_id=empresa_id, **dados)
        self.session.add(ecd)
        self.session.flush()
        return ecd

    def get_ecd(self, ecd_id: int) -> ECD | None:
        return self.session.get(ECD, ecd_id)

    def get_ecd_por_periodo(self, empresa_id: int, dt_ini, dt_fin) -> ECD | None:
        return self.session.execute(
            select(ECD).where(
                ECD.empresa_id == empresa_id,
                ECD.dt_ini == dt_ini,
                ECD.dt_fin == dt_fin,
            )
        ).scalar_one_or_none()

    # ── Plano de Contas ─────────────────────────────────────────────────

    def inserir_plano_contas(self, ecd_id: int, contas: list[dict]) -> int:
        """Inserção em lote do plano de contas."""
        objetos = [PlanoConta(ecd_id=ecd_id, **c) for c in contas]
        self.session.add_all(objetos)
        self.session.flush()
        return len(objetos)

    def get_plano_contas(self, ecd_id: int) -> list[PlanoConta]:
        return list(
            self.session.execute(
                select(PlanoConta).where(PlanoConta.ecd_id == ecd_id).order_by(PlanoConta.cod_cta)
            ).scalars()
        )

    def get_conta_por_codigo(self, ecd_id: int, cod_cta: str) -> PlanoConta | None:
        return self.session.execute(
            select(PlanoConta).where(
                PlanoConta.ecd_id == ecd_id,
                PlanoConta.cod_cta == cod_cta,
            )
        ).scalar_one_or_none()

    # ── Contas Referenciais ─────────────────────────────────────────────

    def inserir_contas_referenciais(self, ecd_id: int, refs: list[dict]) -> int:
        # Precisa resolver plano_conta_id
        contas_map = {
            c.cod_cta: c.id
            for c in self.session.execute(
                select(PlanoConta).where(PlanoConta.ecd_id == ecd_id)
            ).scalars()
        }
        objetos = []
        for r in refs:
            pc_id = contas_map.get(r["cod_cta"])
            if pc_id:
                objetos.append(
                    ContaReferencial(
                        plano_conta_id=pc_id,
                        cod_ccus=r.get("cod_ccus"),
                        cod_cta_ref=r["cod_cta_ref"],
                    )
                )
        self.session.add_all(objetos)
        self.session.flush()
        return len(objetos)

    # ── Aglutinações ────────────────────────────────────────────────────

    def inserir_aglutinacoes(self, ecd_id: int, agls: list[dict]) -> int:
        contas_map = {
            c.cod_cta: c.id
            for c in self.session.execute(
                select(PlanoConta).where(PlanoConta.ecd_id == ecd_id)
            ).scalars()
        }
        objetos = []
        for a in agls:
            pc_id = contas_map.get(a["cod_cta"])
            if pc_id:
                objetos.append(
                    Aglutinacao(
                        plano_conta_id=pc_id,
                        cod_ccus=a.get("cod_ccus"),
                        cod_agl=a["cod_agl"],
                    )
                )
        self.session.add_all(objetos)
        self.session.flush()
        return len(objetos)

    # ── Centros de Custo ────────────────────────────────────────────────

    def inserir_centros_custo(self, ecd_id: int, centros: list[dict]) -> int:
        objetos = [CentroCusto(ecd_id=ecd_id, **c) for c in centros]
        self.session.add_all(objetos)
        self.session.flush()
        return len(objetos)

    # ── Participantes ───────────────────────────────────────────────────

    def inserir_participantes(self, ecd_id: int, parts: list[dict]) -> int:
        objetos = [Participante(ecd_id=ecd_id, **p) for p in parts]
        self.session.add_all(objetos)
        self.session.flush()
        return len(objetos)

    # ── Históricos Padronizados ─────────────────────────────────────────

    def inserir_historicos_padrao(self, ecd_id: int, hists: list[dict]) -> int:
        objetos = [HistoricoPadrao(ecd_id=ecd_id, **h) for h in hists]
        self.session.add_all(objetos)
        self.session.flush()
        return len(objetos)

    # ── Saldos Periódicos ───────────────────────────────────────────────

    def inserir_saldos_periodicos(self, ecd_id: int, saldos: list[dict]) -> int:
        objetos = [SaldoPeriodico(ecd_id=ecd_id, **s) for s in saldos]
        self.session.add_all(objetos)
        self.session.flush()
        return len(objetos)

    def get_saldos_periodicos(self, ecd_id: int, dt_ini=None, dt_fin=None) -> list[SaldoPeriodico]:
        stmt = select(SaldoPeriodico).where(SaldoPeriodico.ecd_id == ecd_id)
        if dt_ini:
            stmt = stmt.where(SaldoPeriodico.dt_ini >= dt_ini)
        if dt_fin:
            stmt = stmt.where(SaldoPeriodico.dt_fin <= dt_fin)
        stmt = stmt.order_by(SaldoPeriodico.cod_cta, SaldoPeriodico.dt_ini)
        return list(self.session.execute(stmt).scalars())

    # ── Saldos Resultado ────────────────────────────────────────────────

    def inserir_saldos_resultado(self, ecd_id: int, saldos: list[dict]) -> int:
        objetos = [SaldoResultado(ecd_id=ecd_id, **s) for s in saldos]
        self.session.add_all(objetos)
        self.session.flush()
        return len(objetos)

    def get_saldos_resultado(self, ecd_id: int) -> list[SaldoResultado]:
        return list(
            self.session.execute(
                select(SaldoResultado)
                .where(SaldoResultado.ecd_id == ecd_id)
                .order_by(SaldoResultado.cod_cta)
            ).scalars()
        )

    # ── Lançamentos ─────────────────────────────────────────────────────

    def inserir_lancamentos(self, ecd_id: int, lancs: list[dict]) -> int:
        objetos = [Lancamento(ecd_id=ecd_id, **ln) for ln in lancs]
        self.session.add_all(objetos)
        self.session.flush()
        return len(objetos)

    def get_lancamentos(self, ecd_id: int) -> list[Lancamento]:
        return list(
            self.session.execute(
                select(Lancamento)
                .where(Lancamento.ecd_id == ecd_id)
                .order_by(Lancamento.dt_lcto, Lancamento.num_lcto)
            ).scalars()
        )

    # ── Partidas ────────────────────────────────────────────────────────

    def inserir_partidas(self, ecd_id: int, partidas: list[dict]) -> int:
        # Precisa resolver lancamento_id via (num_lcto, dt_lcto)
        lancs_map = {}
        for ln in self.session.execute(
            select(Lancamento).where(Lancamento.ecd_id == ecd_id)
        ).scalars():
            key = (ln.num_lcto, ln.dt_lcto.isoformat())
            lancs_map[key] = ln.id

        objetos = []
        for p in partidas:
            key = (p["num_lcto"], p["dt_lcto"])
            l_id = lancs_map.get(key)
            if l_id:
                objetos.append(
                    Partida(
                        lancamento_id=l_id,
                        cod_cta=p["cod_cta"],
                        cod_ccus=p.get("cod_ccus"),
                        vl_dc=p["vl_dc"],
                        ind_dc=p["ind_dc"],
                        num_arq=p.get("num_arq"),
                        cod_hist_pad=p.get("cod_hist_pad"),
                        hist=p.get("hist"),
                        cod_part=p.get("cod_part"),
                    )
                )
        self.session.add_all(objetos)
        self.session.flush()
        return len(objetos)

    def get_partidas_por_conta(self, ecd_id: int, cod_cta: str) -> list[Partida]:
        return list(
            self.session.execute(
                select(Partida)
                .join(Lancamento)
                .where(
                    Lancamento.ecd_id == ecd_id,
                    Partida.cod_cta == cod_cta,
                )
                .order_by(Lancamento.dt_lcto, Lancamento.num_lcto)
            ).scalars()
        )

    # ── Filter Views ────────────────────────────────────────────────────

    def salvar_filter_view(self, nome: str, criterios: dict) -> FilterView:
        fv = FilterView(nome=nome)
        fv.set_criterios(criterios)
        self.session.add(fv)
        self.session.flush()
        return fv

    def get_filter_views(self) -> list[FilterView]:
        return list(self.session.execute(select(FilterView).order_by(FilterView.nome)).scalars())

    def get_filter_view(self, nome: str) -> FilterView | None:
        return self.session.execute(
            select(FilterView).where(FilterView.nome == nome)
        ).scalar_one_or_none()

    # ── Mapeamentos ─────────────────────────────────────────────────────

    def upsert_mapeamento(
        self, empresa_id: int, tipo: str, cod_cta: str, categoria: str, ordem: int = 0
    ) -> Mapeamento:
        m = self.session.execute(
            select(Mapeamento).where(
                Mapeamento.empresa_id == empresa_id,
                Mapeamento.tipo == tipo,
                Mapeamento.cod_cta == cod_cta,
            )
        ).scalar_one_or_none()

        if m:
            m.categoria = categoria
            m.ordem = ordem
        else:
            m = Mapeamento(
                empresa_id=empresa_id,
                tipo=tipo,
                cod_cta=cod_cta,
                categoria=categoria,
                ordem=ordem,
            )
            self.session.add(m)

        self.session.flush()
        return m

    def get_mapeamentos(self, empresa_id: int, tipo: str) -> list[Mapeamento]:
        return list(
            self.session.execute(
                select(Mapeamento)
                .where(
                    Mapeamento.empresa_id == empresa_id,
                    Mapeamento.tipo == tipo,
                )
                .order_by(Mapeamento.ordem)
            ).scalars()
        )

    # ── Commit ──────────────────────────────────────────────────────────

    def commit(self):
        self.session.commit()

    def rollback(self):
        self.session.rollback()
