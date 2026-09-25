"""Balanço Patrimonial (F9).

Duas visões:
1. Hierárquica — segue a estrutura do plano de contas (I050)
2. Publicação — aglutinação por I052/J100/J150

Ativo = natureza 01; passivo e PL pela natureza e pelo grupo (o PL de
natureza 02, dentro de "PATRIMÔNIO LÍQUIDO", vai para o PL — ver
`Classificador.secao_balanco`).
"""

import datetime
import re
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import ECD, Aglutinacao, DemonstracaoContabil, LinhaDemonstracao, PlanoConta
from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.base import (
    ReportContext,
    saldo_por_natureza,
    valor_sinalizado,
)
from src.reports.classificacao import Classificador, normalizar
from src.reports.saldos import consolidar

_PL_PUBLICADO = re.compile(r"^(?!.*\bPASSIVO\b).*PATRIMONIO LIQUIDO")


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
        sintéticas, a soma das filhas.

        **Saldo anterior**: o SF da ECD anterior, quando ela foi importada;
        sem ela, o saldo inicial do primeiro I150 do intervalo — que é o
        balanço de encerramento do exercício anterior, o mesmo que a ECD
        publica no `VL_CTA_INI` do J100. Antes, sem a ECD anterior, a coluna
        saía toda zerada.

        **Patrimônio líquido**: a seção vem do `Classificador` — natureza 03
        ou conta dentro do grupo "PATRIMÔNIO LÍQUIDO" (muito plano usa
        natureza 02 para o PL). A sintética que junta passivo e PL (o "2
        PASSIVO" de quem põe o PL embaixo dele) não é listada: o valor dela
        não é nem o passivo nem o PL, e aparece no total "Passivo + PL".

        **Totais**: por seção, a soma das contas-base (as analíticas com
        saldo) exibidas ou abaixo de uma conta exibida. Sintética e filha
        nunca entram as duas, e a sintética mista não desfaz o total.
        """
        if criterios is None:
            criterios = FilterCriteria()

        saldos = consolidar(self.engine, criterios)
        plano = self.engine.plano()
        classificador = Classificador(self.engine)
        hierarquia = saldos.hierarquia

        # Saldo anterior: ECD anterior, se houver; senão, o saldo inicial.
        ecd_ant_id = self._get_ecd_anterior()
        if ecd_ant_id:
            saldo_anterior_por_conta = self._get_saldos_anteriores(ecd_ant_id)
            origem_anterior = "ecd_anterior"
        else:
            saldo_anterior_por_conta = {cod: saldo.si for cod, saldo in saldos.saldos.items()}
            origem_anterior = "saldo_inicial"

        nats_permitidas = set(criterios.cod_nat) if criterios.cod_nat else {"01", "02", "03"}

        def secao(cod: str) -> str | None:
            if plano[cod].cod_nat not in nats_permitidas:
                return None
            return classificador.secao_balanco(cod)

        # Sintética com contas-base em mais de uma seção (passivo e PL).
        base = hierarquia.contas_base(saldos.proprios)
        secoes_abaixo: dict[str, set[str]] = defaultdict(set)
        for cod in base:
            sec = secao(cod)
            if sec is None:
                continue
            for superior in (cod, *hierarquia.ancestrais(cod)):
                secoes_abaixo[superior].add(sec)

        grupos: dict[str, list[LinhaBalanco]] = {"ativo": [], "passivo": [], "pl": []}
        visiveis: set[str] = set()
        for cod_cta in saldos.visiveis:
            sec = secao(cod_cta)
            if sec is None or len(secoes_abaixo.get(cod_cta, {sec})) > 1:
                continue
            pc = plano[cod_cta]
            visiveis.add(cod_cta)
            grupos[sec].append(
                LinhaBalanco(
                    cod_cta=cod_cta,
                    nome_cta=pc.nome_cta,
                    nivel=pc.nivel,
                    cod_nat=pc.cod_nat,
                    ind_cta=pc.ind_cta,
                    # `+ 0.0`: saldo credor zerado sai 0,00, não -0,00.
                    saldo_atual=saldo_por_natureza(saldos.saldo(cod_cta).sf, pc.cod_nat) + 0.0,
                    saldo_anterior=saldo_por_natureza(
                        saldo_anterior_por_conta.get(cod_cta, 0.0), pc.cod_nat
                    )
                    + 0.0,
                    ancestrais=hierarquia.ancestrais(cod_cta),
                )
            )

        # Totais pelas contas-base exibidas ou cobertas por uma linha exibida.
        exibidas = set(saldos.visiveis)
        soma = {s: 0.0 for s in grupos}
        soma_ant = {s: 0.0 for s in grupos}
        for cod in base:
            sec = secao(cod)
            if sec is None or not (
                cod in exibidas or any(a in exibidas for a in hierarquia.ancestrais(cod))
            ):
                continue
            natureza = plano[cod].cod_nat
            soma[sec] += saldo_por_natureza(saldos.saldo(cod).sf, natureza)
            if origem_anterior == "saldo_inicial":
                soma_ant[sec] += saldo_por_natureza(saldos.saldo(cod).si, natureza)
        if origem_anterior == "ecd_anterior":
            # A ECD anterior tem as próprias contas: soma as linhas listadas
            # sem superior listado, na seção de cada uma.
            for sec, linhas in grupos.items():
                listadas = {ln.cod_cta for ln in linhas}
                soma_ant[sec] = sum(
                    ln.saldo_anterior
                    for ln in linhas
                    if not any(a in listadas for a in ln.ancestrais)
                )

        total_ativo = round(soma["ativo"], 2) + 0.0
        total_passivo = round(soma["passivo"], 2) + 0.0
        total_pl = round(soma["pl"], 2) + 0.0
        tem_anterior = ecd_ant_id is not None or any(abs(v) > 0.005 for v in soma_ant.values())

        ecd = self.session.get(ECD, self.ecd_id)
        data_atual = criterios.dt_fin or (ecd.dt_fin if ecd else None)
        inicio = criterios.dt_ini or (ecd.dt_ini if ecd else None)
        data_anterior = inicio - datetime.timedelta(days=1) if inicio else None

        totais = {
            "ativo": total_ativo,
            "passivo": total_passivo,
            "pl": total_pl,
            "passivo_pl": round(total_passivo + total_pl, 2),
            "diferenca": round(abs(total_ativo - (total_passivo + total_pl)), 2),
            "ativo_anterior": round(soma_ant["ativo"], 2) + 0.0,
            "passivo_anterior": round(soma_ant["passivo"], 2) + 0.0,
            "pl_anterior": round(soma_ant["pl"], 2) + 0.0,
            "passivo_pl_anterior": round(soma_ant["passivo"] + soma_ant["pl"], 2) + 0.0,
            "tem_anterior": tem_anterior,
            "origem_anterior": origem_anterior,
            "data_atual": data_atual,
            "data_anterior": data_anterior,
        }

        ctx = ReportContext(
            titulo="Balanço Patrimonial",
            filtros_descricao=self.engine.descricao_filtros(criterios),
        )

        return ctx, grupos, totais

    def _linhas_j100(self) -> list[LinhaDemonstracao]:
        """As linhas do J100 da demonstração que fecha no fim da ECD."""
        demonstracoes = list(
            self.session.execute(
                select(DemonstracaoContabil)
                .where(DemonstracaoContabil.ecd_id == self.ecd_id)
                .order_by(DemonstracaoContabil.dt_fin.desc(), DemonstracaoContabil.id_dem)
            ).scalars()
        )
        for demonstracao in demonstracoes:
            linhas = [ln for ln in demonstracao.linhas if ln.registro == "J100"]
            if linhas:
                return sorted(linhas, key=lambda ln: ln.id)
        return []

    def _gerar_do_j100(
        self,
    ) -> tuple[ReportContext, dict[str, list[LinhaBalanco]], dict[str, float]] | None:
        """O balanço **como a empresa o publicou**: as linhas do J100, sem recálculo.

        Os códigos de aglutinação são os da empresa (numa ECD real, o próprio
        código da conta), e o saldo anterior é o `VL_CTA_INI` publicado. O PL
        é a linha "PATRIMÔNIO LÍQUIDO" e o que está abaixo dela; a linha de
        nível 1 do lado do passivo, que soma passivo e PL, não é listada —
        é o total "Passivo + PL". Filtros não se aplicam: é o documento.
        """
        linhas = self._linhas_j100()
        if not linhas:
            return None
        por_codigo = {ln.cod_agl: ln for ln in linhas}

        def cadeia(linha: LinhaDemonstracao) -> list[LinhaDemonstracao]:
            resultado, vistos, atual = [linha], {linha.cod_agl}, linha
            while atual.cod_agl_sup and atual.cod_agl_sup in por_codigo:
                atual = por_codigo[atual.cod_agl_sup]
                if atual.cod_agl in vistos:
                    break
                vistos.add(atual.cod_agl)
                resultado.append(atual)
            return resultado

        def eh_pl(linha: LinhaDemonstracao) -> bool:
            return any(_PL_PUBLICADO.search(normalizar(ln.descricao)) for ln in cadeia(linha))

        def sinalizado(valor: float, indicador: str | None, lado: str) -> float:
            vl = valor_sinalizado(valor or 0.0, indicador or ("D" if lado == "A" else "C"))
            return (vl if lado == "A" else -vl) + 0.0

        pl_abaixo: dict[str, set[bool]] = defaultdict(set)
        for linha in linhas:
            if linha.ind_grp_bal != "P":
                continue
            for superior in cadeia(linha):
                pl_abaixo[superior.cod_agl].add(eh_pl(linha))

        grupos: dict[str, list[LinhaBalanco]] = {"ativo": [], "passivo": [], "pl": []}
        totais_publicados = {"A": [0.0, 0.0], "P": [0.0, 0.0]}
        pl_maximo = [0.0, 0.0]
        for linha in linhas:
            lado = linha.ind_grp_bal or "A"
            atual = sinalizado(linha.vl_cta_fin, linha.ind_dc_cta_fin, lado)
            anterior = sinalizado(linha.vl_cta_ini, linha.ind_dc_cta_ini, lado)
            if (linha.nivel_agl or 0) == 1 and lado in totais_publicados:
                totais_publicados[lado][0] += atual
                totais_publicados[lado][1] += anterior
            if lado == "A":
                secao, natureza = "ativo", "01"
            elif len(pl_abaixo.get(linha.cod_agl, set())) > 1:
                continue  # passivo e PL juntos: é o total "Passivo + PL"
            elif eh_pl(linha):
                secao, natureza = "pl", "03"
                if not any(eh_pl(sup) for sup in cadeia(linha)[1:]):
                    pl_maximo[0] += atual
                    pl_maximo[1] += anterior
            else:
                secao, natureza = "passivo", "02"
            grupos[secao].append(
                LinhaBalanco(
                    cod_cta=linha.cod_agl,
                    nome_cta=linha.descricao or "",
                    nivel=linha.nivel_agl or 1,
                    cod_nat=natureza,
                    ind_cta="S" if (linha.ind_cod_agl or "").upper() == "T" else "A",
                    saldo_atual=atual,
                    saldo_anterior=anterior,
                    ancestrais=tuple(ln.cod_agl for ln in cadeia(linha)[1:]),
                )
            )

        ativo, ativo_ant = totais_publicados["A"]
        passivo_pl, passivo_pl_ant = totais_publicados["P"]
        pl, pl_ant = pl_maximo
        ecd = self.session.get(ECD, self.ecd_id)
        totais = {
            "ativo": round(ativo, 2) + 0.0,
            "passivo": round(passivo_pl - pl, 2) + 0.0,
            "pl": round(pl, 2) + 0.0,
            "passivo_pl": round(passivo_pl, 2) + 0.0,
            "diferenca": round(abs(ativo - passivo_pl), 2),
            "ativo_anterior": round(ativo_ant, 2) + 0.0,
            "passivo_anterior": round(passivo_pl_ant - pl_ant, 2) + 0.0,
            "pl_anterior": round(pl_ant, 2) + 0.0,
            "passivo_pl_anterior": round(passivo_pl_ant, 2) + 0.0,
            "tem_anterior": any(abs(ln.vl_cta_ini or 0.0) > 0.005 for ln in linhas),
            "origem_anterior": "publicado",
            "data_atual": ecd.dt_fin if ecd else None,
            "data_anterior": (ecd.dt_ini - datetime.timedelta(days=1)) if ecd else None,
        }
        ctx = ReportContext(
            titulo="Balanço Patrimonial (Publicação)",
            filtros_descricao="Nenhum filtro aplicado",
        )
        return ctx, grupos, totais

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

        publicado = self._gerar_do_j100()
        if publicado is not None:
            return publicado

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
