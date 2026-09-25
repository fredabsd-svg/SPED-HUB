"""Balanço Patrimonial (F9).

Duas visões:
1. Hierárquica — segue a estrutura do plano de contas (I050)
2. Publicação — aglutinação por I052/J100/J150

Conforme Seção 3.2 do prompt: contas de natureza 01 = Ativo, 02 = Passivo, 03 = PL.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Aglutinacao, PlanoConta
from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.base import (
    ReportContext,
    saldo_por_natureza,
)
from src.reports.saldos import consolidar


@dataclass
class LinhaBalanco:
    cod_cta: str
    nome_cta: str
    nivel: int
    cod_nat: str
    ind_cta: str
    saldo_atual: float = 0.0
    saldo_anterior: float = 0.0
    # Superiores da conta no plano, do mais próximo ao topo.
    ancestrais: tuple[str, ...] = ()


class BalancoPatrimonial:
    """Gerador de Balanço Patrimonial."""

    def __init__(self, session: Session, ecd_id: int):
        self.session = session
        self.ecd_id = ecd_id
        self.engine = FilterEngine(session, ecd_id)

    def _get_ecd_anterior(self) -> int | None:
        """Encontra o ID da ECD do período anterior para a mesma empresa."""
        from src.db.models import ECD

        ecd_atual = self.session.get(ECD, self.ecd_id)
        if not ecd_atual:
            return None
        ecd_ant = self.session.execute(
            select(ECD)
            .where(
                ECD.empresa_id == ecd_atual.empresa_id,
                ECD.dt_ini < ecd_atual.dt_ini,
                ECD.id != self.ecd_id,
            )
            .order_by(ECD.dt_ini.desc())
            .limit(1)
        ).scalar_one_or_none()
        return ecd_ant.id if ecd_ant else None

    def _get_saldos_anteriores(self, ecd_anterior_id: int) -> dict[str, float]:
        """Saldo final de cada conta na ECD anterior, sintéticas incluídas.

        Passa pela mesma consolidação do período atual: SF do último I150,
        centros de custo somados, sintéticas agregando as filhas.
        """
        anteriores = consolidar(FilterEngine(self.session, ecd_anterior_id), FilterCriteria())
        return {cod: saldo.sf for cod, saldo in anteriores.saldos.items()}

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

        O saldo de cada conta é o SF do último I150 do intervalo; o das
        sintéticas, a soma das filhas. O total de cada grupo soma só as
        contas listadas sem superior listado — sintética e filha nunca
        entram as duas.
        """
        if criterios is None:
            criterios = FilterCriteria()

        saldos = consolidar(self.engine, criterios)
        plano = self.engine.plano()

        # Busca saldos do período anterior
        ecd_ant_id = self._get_ecd_anterior()
        saldo_anterior_por_conta: dict[str, float] = {}
        if ecd_ant_id:
            saldo_anterior_por_conta = self._get_saldos_anteriores(ecd_ant_id)

        # Monta linhas por natureza
        grupos: dict[str, list[LinhaBalanco]] = {"01": [], "02": [], "03": []}

        # Se há filtro de natureza, restringe o plano
        nats_permitidas = set(criterios.cod_nat) if criterios.cod_nat else {"01", "02", "03"}

        listadas: list[str] = []
        for cod_cta in saldos.visiveis:
            pc = plano[cod_cta]
            if pc.cod_nat not in nats_permitidas or pc.cod_nat not in grupos:
                continue
            listadas.append(cod_cta)
            grupos[pc.cod_nat].append(
                LinhaBalanco(
                    cod_cta=cod_cta,
                    nome_cta=pc.nome_cta,
                    nivel=pc.nivel,
                    cod_nat=pc.cod_nat,
                    ind_cta=pc.ind_cta,
                    saldo_atual=saldo_por_natureza(saldos.saldo(cod_cta).sf, pc.cod_nat),
                    saldo_anterior=saldo_por_natureza(
                        saldo_anterior_por_conta.get(cod_cta, 0.0), pc.cod_nat
                    ),
                    ancestrais=saldos.hierarquia.ancestrais(cod_cta),
                )
            )
        ativo, passivo, pl = grupos["01"], grupos["02"], grupos["03"]

        # Totais: só as linhas sem superior listado, cada valor uma vez.
        no_total = set(saldos.hierarquia.maximais(listadas))

        def _total(linhas: list[LinhaBalanco], campo: str) -> float:
            return round(sum(getattr(ln, campo) for ln in linhas if ln.cod_cta in no_total), 2)

        total_ativo = _total(ativo, "saldo_atual")
        total_passivo = _total(passivo, "saldo_atual")
        total_pl = _total(pl, "saldo_atual")

        total_ativo_ant = _total(ativo, "saldo_anterior")
        total_passivo_ant = _total(passivo, "saldo_anterior")
        total_pl_ant = _total(pl, "saldo_anterior")

        totais = {
            "ativo": total_ativo,
            "passivo": total_passivo,
            "pl": total_pl,
            "passivo_pl": round(total_passivo + total_pl, 2),
            "diferenca": round(abs(total_ativo - (total_passivo + total_pl)), 2),
            "ativo_anterior": total_ativo_ant,
            "passivo_anterior": total_passivo_ant,
            "pl_anterior": total_pl_ant,
            "tem_anterior": ecd_ant_id is not None,
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

        Estrutura hierárquica conforme Lei 6.404/76:
          - Ativo Circulante (J100)
          - Ativo Não Circulante (J150)
            - Realizável a Longo Prazo
            - Investimentos
            - Imobilizado
            - Intangível
          - Passivo Circulante (J200)
          - Passivo Não Circulante (J250)
          - Patrimônio Líquido (J300)
            - Capital Social
            - Reservas
            - Lucros/Prejuízos Acumulados
        """
        if criterios is None:
            criterios = FilterCriteria()

        # Busca aglutinações
        agls = list(
            self.session.execute(
                select(Aglutinacao).join(PlanoConta).where(PlanoConta.ecd_id == self.ecd_id)
            ).scalars()
        )

        # Mapeia cod_agl → lista de cod_cta
        agl_map: dict[str, list[str]] = {}
        for a in agls:
            agl_map.setdefault(a.cod_agl, []).append(a.conta.cod_cta)

        # Saldos consolidados: sintéticas com a soma das filhas.
        saldos = consolidar(self.engine, criterios)
        visiveis = set(saldos.visiveis)

        # Estrutura hierárquica da publicação
        # Cada seção tem: nome, código_agl (ou None para agrupar sub-seções), sub_secoes
        estrutura = [
            # Ativo
            {
                "nome": "ATIVO",
                "tipo": "grupo",
                "cod_nat": "01",
                "filhos": [
                    {"nome": "Ativo Circulante", "tipo": "secao", "cod_agl": "J100"},
                    {
                        "nome": "Ativo Não Circulante",
                        "tipo": "secao",
                        "cod_agl": "J150",
                        "filhos": [
                            {
                                "nome": "Realizável a Longo Prazo",
                                "tipo": "subsecao",
                                "cod_agl": "J151",
                            },
                            {"nome": "Investimentos", "tipo": "subsecao", "cod_agl": "J152"},
                            {"nome": "Imobilizado", "tipo": "subsecao", "cod_agl": "J153"},
                            {"nome": "Intangível", "tipo": "subsecao", "cod_agl": "J154"},
                        ],
                    },
                ],
            },
            # Passivo
            {
                "nome": "PASSIVO",
                "tipo": "grupo",
                "cod_nat": "02",
                "filhos": [
                    {"nome": "Passivo Circulante", "tipo": "secao", "cod_agl": "J200"},
                    {"nome": "Passivo Não Circulante", "tipo": "secao", "cod_agl": "J250"},
                ],
            },
            # PL
            {
                "nome": "PATRIMÔNIO LÍQUIDO",
                "tipo": "grupo",
                "cod_nat": "03",
                "filhos": [
                    {"nome": "Capital Social", "tipo": "secao", "cod_agl": "J300"},
                    {"nome": "Reservas", "tipo": "secao", "cod_agl": "J310"},
                    {"nome": "Lucros/Prejuízos Acumulados", "tipo": "secao", "cod_agl": "J320"},
                ],
            },
        ]

        def _calcular_saldo_agl(cod_agl: str) -> float:
            """Saldo de um código de aglutinação, sem dobrar a sintética.

            O I052 costuma vir na sintética e nas filhas com o mesmo código.
            Com a sintética agregando as filhas, somar todas dobraria o
            valor: entram só as contas do código sem superior no mesmo código.
            """
            contas = [c for c in agl_map.get(cod_agl, []) if c in visiveis]
            return sum(saldos.saldo(c).sf for c in saldos.hierarquia.maximais(contas))

        def _processar_secao(secao: dict, nat: str) -> tuple[list[LinhaBalanco], float]:
            """Processa uma seção recursivamente, retornando linhas e total."""
            linhas: list[LinhaBalanco] = []
            total = 0.0

            if "filhos" in secao:
                # Tem sub-seções
                for sub in secao["filhos"]:
                    sub_linhas, sub_total = _processar_secao(sub, nat)
                    linhas.extend(sub_linhas)
                    total += sub_total
            elif "cod_agl" in secao and secao["cod_agl"]:
                # Seção com código de aglutinação
                vl = _calcular_saldo_agl(secao["cod_agl"])
                if abs(vl) >= 0.005:
                    linhas.append(
                        LinhaBalanco(
                            cod_cta=secao["cod_agl"],
                            nome_cta=secao["nome"],
                            nivel=1,
                            cod_nat=nat,
                            ind_cta="S",
                            saldo_atual=abs(vl),
                            saldo_anterior=0.0,
                        )
                    )
                total = vl

            return linhas, total

        ativo: list[LinhaBalanco] = []
        passivo: list[LinhaBalanco] = []
        pl: list[LinhaBalanco] = []

        total_ativo = 0.0
        total_passivo = 0.0
        total_pl = 0.0

        for grupo in estrutura:
            nat = grupo["cod_nat"]
            for secao in grupo.get("filhos", []):
                sec_linhas, sec_total = _processar_secao(secao, nat)
                if sec_linhas:
                    # Adiciona cabeçalho da seção
                    if nat == "01":
                        ativo.append(
                            LinhaBalanco(
                                cod_cta="",
                                nome_cta=secao["nome"],
                                nivel=0,
                                cod_nat=nat,
                                ind_cta="S",
                                saldo_atual=0.0,
                                saldo_anterior=0.0,
                            )
                        )
                        ativo.extend(sec_linhas)
                        total_ativo += sec_total
                    elif nat == "02":
                        passivo.append(
                            LinhaBalanco(
                                cod_cta="",
                                nome_cta=secao["nome"],
                                nivel=0,
                                cod_nat=nat,
                                ind_cta="S",
                                saldo_atual=0.0,
                                saldo_anterior=0.0,
                            )
                        )
                        passivo.extend(sec_linhas)
                        total_passivo += sec_total
                    elif nat == "03":
                        pl.append(
                            LinhaBalanco(
                                cod_cta="",
                                nome_cta=secao["nome"],
                                nivel=0,
                                cod_nat=nat,
                                ind_cta="S",
                                saldo_atual=0.0,
                                saldo_anterior=0.0,
                            )
                        )
                        pl.extend(sec_linhas)
                        total_pl += sec_total

        # Fallback: se não há aglutinações, usa a visão hierárquica
        if not agl_map:
            return self.gerar(criterios)

        totais = {
            "ativo": abs(total_ativo),
            "passivo": abs(total_passivo),
            "pl": abs(total_pl),
            "passivo_pl": abs(total_passivo) + abs(total_pl),
            "diferenca": abs(abs(total_ativo) - (abs(total_passivo) + abs(total_pl))),
            "ativo_anterior": 0.0,
            "passivo_anterior": 0.0,
            "pl_anterior": 0.0,
            "tem_anterior": False,
        }

        ctx = ReportContext(
            titulo="Balanço Patrimonial (Publicação)",
            filtros_descricao=self.engine.descricao_filtros(criterios),
        )

        return ctx, {"ativo": ativo, "passivo": passivo, "pl": pl}, totais
