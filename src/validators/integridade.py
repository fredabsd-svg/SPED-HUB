"""Validações de Integridade Contábil (F17).

Validações:
(a) Partidas dobradas — Σ débitos = Σ créditos por lançamento
(b) SI + D − C = SF por conta (recomputado vs I155)
(c) Σ movimentos do I250 por conta/período vs I155
(d) DRE recomputada vs I355
(e) Ativo = Passivo + PL
(f) Contas analíticas órfãs de sintética
(g) Lançamentos em contas sintéticas
(h) Ciclo na hierarquia do plano de contas
"""

from collections import defaultdict
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
from src.reports.base import valor_sinalizado


@dataclass
class Inconsistencia:
    """Uma inconsistência encontrada."""

    tipo: str
    severidade: str
    descricao: str
    detalhes: dict = field(default_factory=dict)


class ValidadorIntegridade:
    """Executa todas as validações de integridade contábil."""

    def __init__(self, session: Session, ecd_id: int):
        self.session = session
        self.ecd_id = ecd_id

    def validar_todas(self) -> list[Inconsistencia]:
        resultados = []
        resultados.extend(self._validar_partidas_dobradas())
        resultados.extend(self._validar_saldo_si_d_c_sf())
        resultados.extend(self._validar_movimentos_vs_i155())
        resultados.extend(self._validar_dre_vs_i355())
        resultados.extend(self._validar_ativo_passivo_pl())
        resultados.extend(self._validar_analiticas_orfas())
        resultados.extend(self._validar_lancamentos_sinteticas())
        resultados.extend(self._validar_hierarquia_ciclica())
        return resultados

    def _validar_partidas_dobradas(self) -> list[Inconsistencia]:
        """(a) Σ débitos = Σ créditos por lançamento."""
        inconsistencias = []

        lancamentos = list(
            self.session.execute(
                select(Lancamento).where(Lancamento.ecd_id == self.ecd_id)
            ).scalars()
        )

        for lanc in lancamentos:
            partidas = list(
                self.session.execute(
                    select(Partida).where(Partida.lancamento_id == lanc.id)
                ).scalars()
            )

            total_debitos = sum(p.vl_dc for p in partidas if p.ind_dc == "D")
            total_creditos = sum(p.vl_dc for p in partidas if p.ind_dc == "C")

            if abs(total_debitos - total_creditos) > 0.005:
                inconsistencias.append(
                    Inconsistencia(
                        tipo="partidas_dobradas",
                        severidade="erro",
                        descricao=f"Lançamento {lanc.num_lcto} em {lanc.dt_lcto}: "
                        f"Débitos R$ {total_debitos:,.2f} ≠ Créditos R$ {total_creditos:,.2f}",
                        detalhes={
                            "num_lcto": lanc.num_lcto,
                            "dt_lcto": lanc.dt_lcto.isoformat(),
                            "total_debitos": round(total_debitos, 2),
                            "total_creditos": round(total_creditos, 2),
                            "diferenca": round(total_debitos - total_creditos, 2),
                        },
                    )
                )

        return inconsistencias

    def _validar_saldo_si_d_c_sf(self) -> list[Inconsistencia]:
        """(b) SI + D − C = SF por conta (recomputado vs I155)."""
        inconsistencias = []

        saldos = list(
            self.session.execute(
                select(SaldoPeriodico).where(SaldoPeriodico.ecd_id == self.ecd_id)
            ).scalars()
        )

        por_conta = defaultdict(lambda: {"si": 0.0, "d": 0.0, "c": 0.0, "sf": 0.0})
        for s in saldos:
            acc = por_conta[s.cod_cta]
            acc["si"] += valor_sinalizado(s.vl_sld_ini, s.ind_dc_ini)
            acc["d"] += s.vl_deb
            acc["c"] += s.vl_cred
            acc["sf"] += valor_sinalizado(s.vl_sld_fin, s.ind_dc_fin)

        for cod_cta, acc in por_conta.items():
            sc = acc["si"] + acc["d"] - acc["c"]
            div = acc["sf"] - sc
            if abs(div) > 0.005:
                inconsistencias.append(
                    Inconsistencia(
                        tipo="saldo_inconsistente",
                        severidade="erro",
                        descricao=f"Conta {cod_cta}: SI+D−C = {sc:,.2f} ≠ SF I155 = {acc['sf']:,.2f}",
                        detalhes={
                            "cod_cta": cod_cta,
                            "si": round(acc["si"], 2),
                            "debitos": round(acc["d"], 2),
                            "creditos": round(acc["c"], 2),
                            "sf_calculado": round(sc, 2),
                            "sf_i155": round(acc["sf"], 2),
                            "diferenca": round(div, 2),
                        },
                    )
                )

        return inconsistencias

    def _validar_movimentos_vs_i155(self) -> list[Inconsistencia]:
        """(c) Σ movimentos do I250 por conta/período vs I155."""
        inconsistencias = []

        movimento_i250 = defaultdict(lambda: {"d": 0.0, "c": 0.0})
        resultados = self.session.execute(
            select(Partida.cod_cta, Partida.ind_dc, func.sum(Partida.vl_dc))
            .join(Lancamento, Partida.lancamento_id == Lancamento.id)
            .where(Lancamento.ecd_id == self.ecd_id)
            .group_by(Partida.cod_cta, Partida.ind_dc)
        ).all()

        for cod_cta, ind_dc, total in resultados:
            if ind_dc == "D":
                movimento_i250[cod_cta]["d"] = total or 0.0
            else:
                movimento_i250[cod_cta]["c"] = total or 0.0

        saldos = list(
            self.session.execute(
                select(SaldoPeriodico).where(SaldoPeriodico.ecd_id == self.ecd_id)
            ).scalars()
        )

        movimento_i155 = defaultdict(lambda: {"d": 0.0, "c": 0.0})
        for s in saldos:
            movimento_i155[s.cod_cta]["d"] += s.vl_deb
            movimento_i155[s.cod_cta]["c"] += s.vl_cred

        todas_contas = set(movimento_i250.keys()) | set(movimento_i155.keys())
        for cod_cta in sorted(todas_contas):
            d250 = movimento_i250[cod_cta]["d"]
            c250 = movimento_i250[cod_cta]["c"]
            d155 = movimento_i155[cod_cta]["d"]
            c155 = movimento_i155[cod_cta]["c"]

            if abs(d250 - d155) > 0.01 or abs(c250 - c155) > 0.01:
                inconsistencias.append(
                    Inconsistencia(
                        tipo="movimento_divergente",
                        severidade="alerta",
                        descricao=f"Conta {cod_cta}: I250 débitos={d250:,.2f} créditos={c250:,.2f} "
                        f"vs I155 débitos={d155:,.2f} créditos={c155:,.2f}",
                        detalhes={
                            "cod_cta": cod_cta,
                            "i250_debitos": round(d250, 2),
                            "i250_creditos": round(c250, 2),
                            "i155_debitos": round(d155, 2),
                            "i155_creditos": round(c155, 2),
                        },
                    )
                )

        return inconsistencias

    def _validar_dre_vs_i355(self) -> list[Inconsistencia]:
        """(d) DRE recomputada vs I355."""
        inconsistencias = []

        i355 = list(
            self.session.execute(
                select(SaldoResultado).where(SaldoResultado.ecd_id == self.ecd_id)
            ).scalars()
        )

        total_i355 = sum(valor_sinalizado(s.vl_sld_fin, s.ind_dc_fin) for s in i355)

        movimento_resultado = 0.0
        plano = {
            c.cod_cta: c
            for c in self.session.execute(
                select(PlanoConta).where(PlanoConta.ecd_id == self.ecd_id)
            ).scalars()
        }

        resultados = self.session.execute(
            select(Partida, Lancamento)
            .join(Lancamento, Partida.lancamento_id == Lancamento.id)
            .where(
                Lancamento.ecd_id == self.ecd_id,
                Lancamento.ind_lcto != "E",
            )
        ).all()

        for partida, _lancamento in resultados:
            pc = plano.get(partida.cod_cta)
            if pc and pc.cod_nat == "04":
                movimento_resultado += valor_sinalizado(partida.vl_dc, partida.ind_dc)

        if abs(movimento_resultado - total_i355) > 0.01:
            inconsistencias.append(
                Inconsistencia(
                    tipo="dre_divergente",
                    severidade="alerta",
                    descricao=f"Resultado via I250 (s/ encerramento) = {movimento_resultado:,.2f} "
                    f"vs I355 = {total_i355:,.2f}",
                    detalhes={
                        "resultado_i250": round(movimento_resultado, 2),
                        "resultado_i355": round(total_i355, 2),
                        "diferenca": round(movimento_resultado - total_i355, 2),
                    },
                )
            )

        return inconsistencias

    def _validar_ativo_passivo_pl(self) -> list[Inconsistencia]:
        """(e) Ativo = Passivo + PL."""
        inconsistencias = []

        plano = {
            c.cod_cta: c
            for c in self.session.execute(
                select(PlanoConta).where(PlanoConta.ecd_id == self.ecd_id)
            ).scalars()
        }

        saldos = list(
            self.session.execute(
                select(SaldoPeriodico).where(SaldoPeriodico.ecd_id == self.ecd_id)
            ).scalars()
        )

        por_conta = {}
        for s in saldos:
            key = s.cod_cta
            if key not in por_conta or s.dt_fin > por_conta[key].dt_fin:
                por_conta[key] = s

        ativo = 0.0
        passivo = 0.0
        pl = 0.0

        for cod_cta, s in por_conta.items():
            pc = plano.get(cod_cta)
            if pc is None:
                continue
            vl = valor_sinalizado(s.vl_sld_fin, s.ind_dc_fin)
            if pc.cod_nat == "01":
                ativo += vl
            elif pc.cod_nat == "02":
                passivo += -vl
            elif pc.cod_nat == "03":
                pl += -vl

        if abs(ativo - (passivo + pl)) > 0.01:
            inconsistencias.append(
                Inconsistencia(
                    tipo="balanco_nao_fecha",
                    severidade="erro",
                    descricao=f"Ativo = {ativo:,.2f} ≠ Passivo + PL = {passivo + pl:,.2f}",
                    detalhes={
                        "ativo": round(ativo, 2),
                        "passivo": round(passivo, 2),
                        "pl": round(pl, 2),
                        "passivo_mais_pl": round(passivo + pl, 2),
                        "diferenca": round(ativo - (passivo + pl), 2),
                    },
                )
            )

        return inconsistencias

    def _validar_analiticas_orfas(self) -> list[Inconsistencia]:
        """(f) Contas analíticas órfãs de sintética."""
        inconsistencias = []

        contas = list(
            self.session.execute(
                select(PlanoConta).where(PlanoConta.ecd_id == self.ecd_id)
            ).scalars()
        )

        codigos = {c.cod_cta for c in contas}

        for c in contas:
            if c.ind_cta == "A" and c.cod_cta_sup:
                if c.cod_cta_sup not in codigos:
                    inconsistencias.append(
                        Inconsistencia(
                            tipo="analitica_orfa",
                            severidade="alerta",
                            descricao=f"Conta analítica {c.cod_cta} ({c.nome_cta[:40]}) "
                            f"referencia sintética {c.cod_cta_sup} inexistente",
                            detalhes={
                                "cod_cta": c.cod_cta,
                                "cod_cta_sup": c.cod_cta_sup,
                            },
                        )
                    )

        return inconsistencias

    def _validar_lancamentos_sinteticas(self) -> list[Inconsistencia]:
        """(g) Lançamentos em contas sintéticas."""
        inconsistencias = []

        plano = {
            c.cod_cta: c
            for c in self.session.execute(
                select(PlanoConta).where(PlanoConta.ecd_id == self.ecd_id)
            ).scalars()
        }

        resultados = self.session.execute(
            select(Partida, Lancamento)
            .join(Lancamento, Partida.lancamento_id == Lancamento.id)
            .where(Lancamento.ecd_id == self.ecd_id)
        ).all()

        sinteticas_com_lancamento = set()
        for partida, _lancamento in resultados:
            pc = plano.get(partida.cod_cta)
            if pc and pc.ind_cta == "S":
                sinteticas_com_lancamento.add(partida.cod_cta)

        for cod_cta in sorted(sinteticas_com_lancamento):
            inconsistencias.append(
                Inconsistencia(
                    tipo="lancamento_sintetica",
                    severidade="alerta",
                    descricao=f"Conta sintética {cod_cta} possui lançamentos "
                    "(apenas analíticas deveriam)",
                    detalhes={"cod_cta": cod_cta},
                )
            )

        return inconsistencias

    def _validar_hierarquia_ciclica(self) -> list[Inconsistencia]:
        """(h) Ciclo na hierarquia do plano de contas.

        A cadeia ``COD_CTA → COD_CTA_SUP`` precisa terminar numa conta de
        topo. Uma conta que é a própria sintética — ou ``A→B→A`` — torna a
        hierarquia impossível de interpretar: qualquer agrupamento que suba
        por ela não tem resposta definida.

        Não é hipótese: uma ECD assim travava o dashboard para todos os
        usuários (laço infinito em ``get_composicao_ativo``, corrigido no
        PR #7). O travamento foi resolvido lá; aqui é onde quem recebe o
        arquivo fica sabendo que a escrituração veio com hierarquia inválida.

        É erro, não alerta: as demais validações comparam números e podem
        divergir por centavos, mas um ciclo não tem leitura correta possível.

        Cada ciclo é reportado uma única vez, pelo seu menor código de conta,
        com o caminho completo nos detalhes.
        """
        inconsistencias = []

        sup = {
            c.cod_cta: c.cod_cta_sup
            for c in self.session.execute(
                select(PlanoConta).where(PlanoConta.ecd_id == self.ecd_id)
            ).scalars()
        }

        # Cada conta tem no máximo um ponteiro para cima, então o grafo é
        # funcional: percorrer com memoização classifica cada nó uma única
        # vez (O(n)).  ``estado``: 1 = na cadeia atual, 2 = já resolvido.
        estado: dict[str, int] = {}
        ciclos_vistos: set[frozenset] = set()

        for inicio in sup:
            if estado.get(inicio):
                continue
            cadeia: list[str] = []
            atual: str | None = inicio
            while atual is not None and atual in sup and estado.get(atual) is None:
                estado[atual] = 1
                cadeia.append(atual)
                atual = sup[atual]

            if atual is not None and estado.get(atual) == 1:
                # ``atual`` reapareceu dentro da cadeia em construção: tudo
                # a partir da primeira ocorrência dele é o ciclo.
                ciclo = cadeia[cadeia.index(atual) :]
                chave = frozenset(ciclo)
                if chave not in ciclos_vistos:
                    ciclos_vistos.add(chave)
                    ancora = min(ciclo)
                    inconsistencias.append(
                        Inconsistencia(
                            tipo="hierarquia_ciclica",
                            severidade="erro",
                            descricao=f"Ciclo na hierarquia do plano de contas: "
                            f"{' → '.join(ciclo + [ciclo[0]])} — nenhuma dessas contas "
                            "chega a uma conta de topo",
                            detalhes={
                                "cod_cta": ancora,
                                "ciclo": ciclo,
                                "tamanho": len(ciclo),
                            },
                        )
                    )

            for cod in cadeia:
                estado[cod] = 2

        return inconsistencias

    def relatorio(self, inconsistencias: list[Inconsistencia]) -> dict:
        """Gera relatório sumarizado das validações."""
        erros = [i for i in inconsistencias if i.severidade == "erro"]
        alertas = [i for i in inconsistencias if i.severidade == "alerta"]

        return {
            "total_inconsistencias": len(inconsistencias),
            "erros": len(erros),
            "alertas": len(alertas),
            "status": "OK" if len(erros) == 0 else "ERROS",
            "detalhes": [
                {
                    "tipo": i.tipo,
                    "severidade": i.severidade,
                    "descricao": i.descricao,
                }
                for i in inconsistencias
            ],
        }
