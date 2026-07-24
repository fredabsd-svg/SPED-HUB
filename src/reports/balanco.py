"""Balanço Patrimonial (F9).

Duas visões:
1. Hierárquica — segue a estrutura do plano de contas (I050)
2. Publicação — aglutinação por I052/J100/J150

Conforme Seção 3.2 do prompt: contas de natureza 01 = Ativo, 02 = Passivo, 03 = PL.
"""

import datetime
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Aglutinacao, PlanoConta, SaldoPeriodico
from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.base import (
    ReportContext,
    fmt_moeda,
    saldo_por_natureza,
    valor_sinalizado,
)


@dataclass
class LinhaBalanco:
    cod_cta: str
    nome_cta: str
    nivel: int
    cod_nat: str
    ind_cta: str
    saldo_atual: float = 0.0
    saldo_anterior: float = 0.0


class BalancoPatrimonial:
    """Gerador de Balanço Patrimonial."""

    def __init__(self, session: Session, ecd_id: int):
        self.session = session
        self.ecd_id = ecd_id
        self.engine = FilterEngine(session, ecd_id)

    def gerar(
        self,
        criterios: FilterCriteria | None = None,
        visao: str = "hierarquica",
    ) -> tuple[ReportContext, dict[str, list[LinhaBalanco]], dict[str, float]]:
        """Gera o Balanço Patrimonial.

        Args:
            criterios: Filtros F7
            visao: "hierarquica" (I050) ou "publicacao" (I052/J100/J150)

        Returns:
            (contexto, grupos, totais)
        """
        if criterios is None:
            criterios = FilterCriteria()

        # Busca plano de contas
        plano = {
            c.cod_cta: c
            for c in self.session.execute(
                select(PlanoConta).where(PlanoConta.ecd_id == self.ecd_id)
            ).scalars()
        }

        # Busca saldos
        saldos = self.engine.aplicar_saldos(criterios)

        # Agrupa saldos por conta (último período)
        saldo_por_conta: dict[str, float] = {}
        for s in saldos:
            vl = valor_sinalizado(s.vl_sld_fin, s.ind_dc_fin)
            saldo_por_conta[s.cod_cta] = vl

        # Monta linhas por natureza
        ativo: list[LinhaBalanco] = []
        passivo: list[LinhaBalanco] = []
        pl: list[LinhaBalanco] = []

        # Se há filtro de natureza, restringe o plano
        nats_permitidas = set(criterios.cod_nat) if criterios.cod_nat else {"01", "02", "03"}

        for cod_cta, pc in sorted(plano.items()):
            if pc.cod_nat not in nats_permitidas:
                continue

            vl = saldo_por_conta.get(cod_cta, 0.0)
            vl_exposicao = saldo_por_natureza(vl, pc.cod_nat)

            linha = LinhaBalanco(
                cod_cta=cod_cta,
                nome_cta=pc.nome_cta,
                nivel=pc.nivel,
                cod_nat=pc.cod_nat,
                ind_cta=pc.ind_cta,
                saldo_atual=vl_exposicao,
                saldo_anterior=0.0,  # TODO: período anterior
            )

            if pc.cod_nat == "01":
                ativo.append(linha)
            elif pc.cod_nat == "02":
                passivo.append(linha)
            elif pc.cod_nat == "03":
                pl.append(linha)

        # Totais
        total_ativo = sum(l.saldo_atual for l in ativo)
        total_passivo = sum(l.saldo_atual for l in passivo)
        total_pl = sum(l.saldo_atual for l in pl)

        totais = {
            "ativo": total_ativo,
            "passivo": total_passivo,
            "pl": total_pl,
            "passivo_pl": total_passivo + total_pl,
            "diferenca": abs(total_ativo - (total_passivo + total_pl)),
            "ativo_anterior": 0.0,
            "passivo_anterior": 0.0,
            "pl_anterior": 0.0,
        }

        ctx = ReportContext(
            titulo="Balanço Patrimonial",
            filtros_descricao=self.engine.descricao_filtros(criterios),
        )

        return ctx, {"ativo": ativo, "passivo": passivo, "pl": pl}, totais

    def gerar_publicacao(
        self,
        criterios: FilterCriteria | None = None,
    ) -> tuple[ReportContext, dict[str, list[LinhaBalanco]], dict[str, float]]:
        """Visão de publicação — aglutinação por I052/J100/J150.

        Agrupa contas analíticas pelos códigos de aglutinação configurados.
        """
        if criterios is None:
            criterios = FilterCriteria()

        # Busca aglutinações
        agls = list(
            self.session.execute(
                select(Aglutinacao)
                .join(PlanoConta)
                .where(PlanoConta.ecd_id == self.ecd_id)
            ).scalars()
        )

        # Mapeia cod_agl → lista de cod_cta
        agl_map: dict[str, list[str]] = {}
        for a in agls:
            agl_map.setdefault(a.cod_agl, []).append(a.conta.cod_cta)

        # Busca saldos
        saldos = self.engine.aplicar_saldos(criterios)
        saldo_por_conta: dict[str, float] = {}
        for s in saldos:
            vl = valor_sinalizado(s.vl_sld_fin, s.ind_dc_fin)
            saldo_por_conta[s.cod_cta] = vl

        # Agrupa por aglutinação
        plano = {
            c.cod_cta: c
            for c in self.session.execute(
                select(PlanoConta).where(PlanoConta.ecd_id == self.ecd_id)
            ).scalars()
        }

        ativo: list[LinhaBalanco] = []
        passivo: list[LinhaBalanco] = []
        pl: list[LinhaBalanco] = []

        for cod_agl, contas in sorted(agl_map.items()):
            vl_total = sum(saldo_por_conta.get(c, 0.0) for c in contas)
            if abs(vl_total) < 0.005:
                continue

            # Determina natureza pela primeira conta do grupo
            primeira = plano.get(contas[0]) if contas else None
            nat = primeira.cod_nat if primeira else "01"
            nome = primeira.nome_cta if primeira else cod_agl

            linha = LinhaBalanco(
                cod_cta=cod_agl,
                nome_cta=nome,
                nivel=1,
                cod_nat=nat,
                ind_cta="S",
                saldo_atual=abs(vl_total),
                saldo_anterior=0.0,
            )

            if nat == "01":
                ativo.append(linha)
            elif nat == "02":
                passivo.append(linha)
            elif nat == "03":
                pl.append(linha)

        total_ativo = sum(l.saldo_atual for l in ativo)
        total_passivo = sum(l.saldo_atual for l in passivo)
        total_pl = sum(l.saldo_atual for l in pl)

        totais = {
            "ativo": total_ativo,
            "passivo": total_passivo,
            "pl": total_pl,
            "passivo_pl": total_passivo + total_pl,
            "diferenca": abs(total_ativo - (total_passivo + total_pl)),
            "ativo_anterior": 0.0,
            "passivo_anterior": 0.0,
            "pl_anterior": 0.0,
        }

        ctx = ReportContext(
            titulo="Balanço Patrimonial (Publicação)",
            filtros_descricao=self.engine.descricao_filtros(criterios),
        )

        return ctx, {"ativo": ativo, "passivo": passivo, "pl": pl}, totais