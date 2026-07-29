"""Motor de Filtros F7 — Query builder único para todos os relatórios.

16 tipos de filtro combináveis (AND entre tipos, OR entre valores do mesmo tipo).
Visões salvas em filter_views.
"""

import datetime
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import (
    Lancamento,
    Partida,
    PlanoConta,
    SaldoPeriodico,
    SaldoResultado,
)


@dataclass
class FilterCriteria:
    """Critérios de filtro serializáveis (JSON)."""

    # Conta
    cod_cta_exato: list[str] = field(default_factory=list)
    cod_cta_prefixo: list[str] = field(default_factory=list)
    cod_cta_intervalo: tuple[str, str] | None = None
    nome_cta: str | None = None  # busca textual parcial

    # Natureza / Classificação
    cod_nat: list[str] = field(default_factory=list)  # 01-05, 09
    ind_cta: list[str] = field(default_factory=list)  # S/A

    # Nível hierárquico
    nivel_exato: int | None = None
    nivel_ate: int | None = None

    # Subárvore
    subarvore_de: str | None = None  # cod_cta da sintética raiz

    # Conta referencial
    cod_cta_ref: list[str] = field(default_factory=list)

    # Aglutinação
    cod_agl: list[str] = field(default_factory=list)

    # Centro de custo
    cod_ccus: list[str] = field(default_factory=list)

    # Período
    dt_ini: datetime.date | None = None
    dt_fin: datetime.date | None = None

    # Valor
    vl_min: float | None = None
    vl_max: float | None = None
    somente_debitos: bool = False
    somente_creditos: bool = False

    # Histórico
    hist_texto: str | None = None
    cod_hist_pad: list[str] = field(default_factory=list)
    sem_historico: bool = False

    # Tipo de lançamento
    ind_lcto: list[str] = field(default_factory=list)  # N/E/X

    # Participante
    cod_part: list[str] = field(default_factory=list)

    # Saldo/movimento
    ocultar_sem_movimento: bool = False
    ocultar_saldo_zero: bool = False

    # Flags de auditoria
    vl_redondo_acima: float | None = None  # ex.: 10000.00
    fins_de_semana: bool = False

    def to_dict(self) -> dict:
        """Serializa para JSON (visões salvas)."""
        d = {}
        for k, v in self.__dict__.items():
            if v is not None and v != [] and v != {} and v != "" and v is not False:
                if isinstance(v, datetime.date):
                    d[k] = v.isoformat()
                elif isinstance(v, tuple):
                    d[k] = list(v)
                else:
                    d[k] = v
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FilterCriteria":
        """Desserializa de JSON."""
        c = cls()
        for k, v in d.items():
            if k in ("dt_ini", "dt_fin") and isinstance(v, str):
                v = datetime.date.fromisoformat(v)
            if k == "cod_cta_intervalo" and isinstance(v, list):
                v = tuple(v)
            if hasattr(c, k):
                setattr(c, k, v)
        return c


class FilterEngine:
    """Query builder único — aplica FilterCriteria sobre queries SQLAlchemy."""

    def __init__(self, session: Session, ecd_id: int):
        self.session = session
        self.ecd_id = ecd_id
        self._plano_cache: dict[str, PlanoConta] | None = None

    def _get_plano(self) -> dict[str, PlanoConta]:
        if self._plano_cache is None:
            contas = self.session.execute(
                select(PlanoConta).where(PlanoConta.ecd_id == self.ecd_id)
            ).scalars()
            self._plano_cache = {c.cod_cta: c for c in contas}
        return self._plano_cache

    def _filtrar_contas(self, criterios: FilterCriteria) -> set[str]:
        """Retorna conjunto de cod_cta que passam pelos filtros de conta."""
        plano = self._get_plano()
        contas = set(plano.keys())

        # Código exato
        if criterios.cod_cta_exato:
            contas &= set(criterios.cod_cta_exato)

        # Prefixo
        if criterios.cod_cta_prefixo:
            prefixos = criterios.cod_cta_prefixo
            contas = {c for c in contas if any(c.startswith(p) for p in prefixos)}

        # Intervalo
        if criterios.cod_cta_intervalo:
            ini, fim = criterios.cod_cta_intervalo
            contas = {c for c in contas if ini <= c <= fim}

        # Nome
        if criterios.nome_cta:
            from unidecode import unidecode

            busca = unidecode(criterios.nome_cta.lower())
            contas = {c for c in contas if busca in unidecode(plano[c].nome_cta.lower())}

        # Natureza
        if criterios.cod_nat:
            contas = {c for c in contas if plano[c].cod_nat in criterios.cod_nat}

        # Classificação
        if criterios.ind_cta:
            contas = {c for c in contas if plano[c].ind_cta in criterios.ind_cta}

        # Nível exato
        if criterios.nivel_exato is not None:
            contas = {c for c in contas if plano[c].nivel == criterios.nivel_exato}

        # Nível até
        if criterios.nivel_ate is not None:
            contas = {c for c in contas if plano[c].nivel <= criterios.nivel_ate}

        # Subárvore
        if criterios.subarvore_de:
            raiz = criterios.subarvore_de
            prefixo = raiz + "." if not raiz.endswith(".") else raiz
            # Inclui a própria raiz + todas que começam com o prefixo
            contas = {c for c in contas if c == raiz or c.startswith(prefixo)}

        # Conta referencial (via I051)
        if criterios.cod_cta_ref:
            refs = set(criterios.cod_cta_ref)
            contas_com_ref = set()
            for c in contas:
                pc = plano[c]
                for ref in pc.contas_referenciais:
                    if ref.cod_cta_ref in refs:
                        contas_com_ref.add(c)
                        break
            contas &= contas_com_ref

        # Aglutinação (via I052)
        if criterios.cod_agl:
            agls = set(criterios.cod_agl)
            contas_com_agl = set()
            for c in contas:
                pc = plano[c]
                for agl in pc.aglutinacoes:
                    if agl.cod_agl in agls:
                        contas_com_agl.add(c)
                        break
            contas &= contas_com_agl

        return contas

    def aplicar_saldos(self, criterios: FilterCriteria) -> list[SaldoPeriodico]:
        """Aplica filtros sobre saldos periódicos (I155)."""
        contas = self._filtrar_contas(criterios)

        stmt = select(SaldoPeriodico).where(SaldoPeriodico.ecd_id == self.ecd_id)

        if contas:
            stmt = stmt.where(SaldoPeriodico.cod_cta.in_(contas))

        # Centro de custo
        if criterios.cod_ccus:
            stmt = stmt.where(SaldoPeriodico.cod_ccus.in_(criterios.cod_ccus))

        # Período
        if criterios.dt_ini:
            stmt = stmt.where(SaldoPeriodico.dt_ini >= criterios.dt_ini)
        if criterios.dt_fin:
            stmt = stmt.where(SaldoPeriodico.dt_fin <= criterios.dt_fin)

        # Valor
        if criterios.vl_min is not None:
            stmt = stmt.where(SaldoPeriodico.vl_sld_fin >= criterios.vl_min)
        if criterios.vl_max is not None:
            stmt = stmt.where(SaldoPeriodico.vl_sld_fin <= criterios.vl_max)
        if criterios.somente_debitos:
            stmt = stmt.where(SaldoPeriodico.ind_dc_fin == "D")
        if criterios.somente_creditos:
            stmt = stmt.where(SaldoPeriodico.ind_dc_fin == "C")

        # Saldo zero
        if criterios.ocultar_saldo_zero:
            stmt = stmt.where(SaldoPeriodico.vl_sld_fin != 0.0)

        # Sem movimento
        if criterios.ocultar_sem_movimento:
            stmt = stmt.where((SaldoPeriodico.vl_deb != 0.0) | (SaldoPeriodico.vl_cred != 0.0))

        stmt = stmt.order_by(SaldoPeriodico.cod_cta, SaldoPeriodico.dt_ini)
        return list(self.session.execute(stmt).scalars())

    def aplicar_lancamentos(self, criterios: FilterCriteria) -> list[Partida]:
        """Aplica filtros sobre partidas (I250), retornando com dados do lançamento."""
        contas = self._filtrar_contas(criterios)

        stmt = (
            select(Partida, Lancamento)
            .join(Lancamento, Partida.lancamento_id == Lancamento.id)
            .where(Lancamento.ecd_id == self.ecd_id)
        )

        if contas:
            stmt = stmt.where(Partida.cod_cta.in_(contas))

        # Centro de custo
        if criterios.cod_ccus:
            stmt = stmt.where(Partida.cod_ccus.in_(criterios.cod_ccus))

        # Período
        if criterios.dt_ini:
            stmt = stmt.where(Lancamento.dt_lcto >= criterios.dt_ini)
        if criterios.dt_fin:
            stmt = stmt.where(Lancamento.dt_lcto <= criterios.dt_fin)

        # Valor
        if criterios.vl_min is not None:
            stmt = stmt.where(Partida.vl_dc >= criterios.vl_min)
        if criterios.vl_max is not None:
            stmt = stmt.where(Partida.vl_dc <= criterios.vl_max)
        if criterios.somente_debitos:
            stmt = stmt.where(Partida.ind_dc == "D")
        if criterios.somente_creditos:
            stmt = stmt.where(Partida.ind_dc == "C")

        # Histórico
        if criterios.hist_texto:
            busca = f"%{criterios.hist_texto}%"
            # `ilike`, não `like`: o LIKE do SQLite é case-insensitive para
            # ASCII e o do Postgres não é.  Com `like`, buscar "recebi"
            # devolvia resultados em SQLite e nenhum em Postgres — o filtro
            # simplesmente parava de funcionar ao migrar de banco.
            stmt = stmt.where(Partida.hist.ilike(busca))
        if criterios.cod_hist_pad:
            stmt = stmt.where(Partida.cod_hist_pad.in_(criterios.cod_hist_pad))
        if criterios.sem_historico:
            from sqlalchemy import or_

            stmt = stmt.where(or_(Partida.hist.is_(None), Partida.hist == ""))

        # Tipo de lançamento
        if criterios.ind_lcto:
            stmt = stmt.where(Lancamento.ind_lcto.in_(criterios.ind_lcto))

        # Participante
        if criterios.cod_part:
            stmt = stmt.where(Partida.cod_part.in_(criterios.cod_part))

        # Flags de auditoria
        if criterios.vl_redondo_acima is not None:
            limite = criterios.vl_redondo_acima
            stmt = stmt.where((Partida.vl_dc >= limite) & (Partida.vl_dc % 1 == 0))
        if criterios.fins_de_semana:
            # SQLite: strftime('%w', date) — 0=Domingo, 6=Sábado
            stmt = stmt.where(func.strftime("%w", Lancamento.dt_lcto).in_(["0", "6"]))

        stmt = stmt.order_by(Lancamento.dt_lcto, Lancamento.num_lcto, Partida.cod_cta)
        return list(self.session.execute(stmt).all())

    def aplicar_saldos_resultado(self, criterios: FilterCriteria) -> list[SaldoResultado]:
        """Aplica filtros sobre saldos de resultado (I355)."""
        contas = self._filtrar_contas(criterios)

        stmt = select(SaldoResultado).where(SaldoResultado.ecd_id == self.ecd_id)

        if contas:
            stmt = stmt.where(SaldoResultado.cod_cta.in_(contas))

        if criterios.cod_ccus:
            stmt = stmt.where(SaldoResultado.cod_ccus.in_(criterios.cod_ccus))

        if criterios.vl_min is not None:
            stmt = stmt.where(SaldoResultado.vl_sld_fin >= criterios.vl_min)
        if criterios.vl_max is not None:
            stmt = stmt.where(SaldoResultado.vl_sld_fin <= criterios.vl_max)
        if criterios.somente_debitos:
            stmt = stmt.where(SaldoResultado.ind_dc_fin == "D")
        if criterios.somente_creditos:
            stmt = stmt.where(SaldoResultado.ind_dc_fin == "C")
        if criterios.ocultar_saldo_zero:
            stmt = stmt.where(SaldoResultado.vl_sld_fin != 0.0)

        stmt = stmt.order_by(SaldoResultado.cod_cta)
        return list(self.session.execute(stmt).scalars())

    def descricao_filtros(self, criterios: FilterCriteria) -> str:
        """Gera descrição legível dos filtros aplicados (para cabeçalho de relatório)."""
        partes = []

        if criterios.cod_cta_exato:
            partes.append(f"Contas: {', '.join(criterios.cod_cta_exato)}")
        if criterios.cod_cta_prefixo:
            partes.append(f"Prefixo: {', '.join(criterios.cod_cta_prefixo)}")
        if criterios.cod_cta_intervalo:
            partes.append(
                f"Contas de {criterios.cod_cta_intervalo[0]} a {criterios.cod_cta_intervalo[1]}"
            )
        if criterios.nome_cta:
            partes.append(f'Nome contém: "{criterios.nome_cta}"')
        if criterios.cod_nat:
            nat_map = {
                "01": "Ativo",
                "02": "Passivo",
                "03": "PL",
                "04": "Resultado",
                "05": "Compensação",
                "09": "Outras",
            }
            partes.append(f"Natureza: {', '.join(nat_map.get(n, n) for n in criterios.cod_nat)}")
        if criterios.ind_cta:
            partes.append(f"Classificação: {', '.join(criterios.ind_cta)}")
        if criterios.nivel_exato is not None:
            partes.append(f"Nível {criterios.nivel_exato}")
        if criterios.nivel_ate is not None:
            partes.append(f"Até nível {criterios.nivel_ate}")
        if criterios.subarvore_de:
            partes.append(f"Subárvore de {criterios.subarvore_de}")
        if criterios.cod_cta_ref:
            partes.append(f"Referencial: {', '.join(criterios.cod_cta_ref)}")
        if criterios.cod_agl:
            partes.append(f"Aglutinação: {', '.join(criterios.cod_agl)}")
        if criterios.cod_ccus:
            partes.append(f"Centro de custo: {', '.join(criterios.cod_ccus)}")
        if criterios.dt_ini or criterios.dt_fin:
            ini = criterios.dt_ini.isoformat() if criterios.dt_ini else "início"
            fim = criterios.dt_fin.isoformat() if criterios.dt_fin else "fim"
            partes.append(f"Período: {ini} a {fim}")
        if criterios.vl_min is not None or criterios.vl_max is not None:
            v = []
            if criterios.vl_min is not None:
                v.append(f"≥ {criterios.vl_min:,.2f}")
            if criterios.vl_max is not None:
                v.append(f"≤ {criterios.vl_max:,.2f}")
            partes.append(f"Valor: {' e '.join(v)}")
        if criterios.somente_debitos:
            partes.append("Somente débitos")
        if criterios.somente_creditos:
            partes.append("Somente créditos")
        if criterios.hist_texto:
            partes.append(f'Histórico contém: "{criterios.hist_texto}"')
        if criterios.cod_hist_pad:
            partes.append(f"Hist. padronizado: {', '.join(criterios.cod_hist_pad)}")
        if criterios.sem_historico:
            partes.append("Sem histórico")
        if criterios.ind_lcto:
            lcto_map = {"N": "Normal", "E": "Encerramento", "X": "Extemporâneo"}
            partes.append(
                f"Tipo lançamento: {', '.join(lcto_map.get(t, t) for t in criterios.ind_lcto)}"
            )
        if criterios.cod_part:
            partes.append(f"Participante: {', '.join(criterios.cod_part)}")
        if criterios.ocultar_sem_movimento:
            partes.append("Com movimento")
        if criterios.ocultar_saldo_zero:
            partes.append("Saldo ≠ 0")
        if criterios.vl_redondo_acima is not None:
            partes.append(f"Valores redondos ≥ {criterios.vl_redondo_acima:,.2f}")
        if criterios.fins_de_semana:
            partes.append("Fins de semana")

        if not partes:
            return "Nenhum filtro aplicado"
        return " · ".join(partes)
