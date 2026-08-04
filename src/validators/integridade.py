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
(i) Balanço publicado fecha — ativo = passivo, no J100
(j) DRE publicada × os saldos que a própria escrituração declara

As duas últimas são as regras do PGE do Sped Contábil, transcritas do Manual
do Leiaute 9 (Anexo ao ADE Cofis nº 01/2026): `REGRA_EXISTEM_2_NIVEIS_1`,
`REGRA_VALIDA_ATIVO_PASSIVO_FIN` e `REGRA_VALIDA_SALDO_COM_DRE`.  Elas olham
o bloco J — o balanço e a DRE **como a empresa os declarou** —, e é a única
parte destas validações que confere documento contra documento em vez de
recomputar.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from src.db.models import (
    Aglutinacao,
    DemonstracaoContabil,
    Lancamento,
    LinhaDemonstracao,
    Partida,
    PlanoConta,
    SaldoPeriodico,
    SaldoResultado,
)
from src.reports.base import valor_sinalizado


def encontrar_ciclos(sup: dict[str, str | None]) -> list[list[str]]:
    """Ciclos no grafo funcional ``cod_cta → cod_cta_sup``.

    Cada conta aponta para no máximo uma sintética, então percorrer com
    memoização classifica cada nó uma única vez (O(n)). Cada ciclo é
    devolvido uma única vez. Função pura, compartilhada entre a validação
    (h) e a recusa na importação — o mesmo fato num lugar só (§1.9).
    """
    estado: dict[str, int] = {}
    ciclos: list[list[str]] = []
    vistos: set[frozenset] = set()

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
            ciclo = cadeia[cadeia.index(atual) :]
            chave = frozenset(ciclo)
            if chave not in vistos:
                vistos.add(chave)
                ciclos.append(ciclo)

        for cod in cadeia:
            estado[cod] = 2

    return ciclos


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
        """Roda as oito validações e emite `ecd.validada`.

        O evento sai daqui, e não de cada chamador, porque os três — CLI,
        REST e GraphQL — passam por este método: emitir em cada um seria o
        mesmo fato em três lugares, e um deles esqueceria (§1.9).

        `emitir` não bloqueia e engole as próprias falhas; sem webhook ativo
        inscrito, custa uma consulta indexada.
        """
        resultados = []
        resultados.extend(self._validar_partidas_dobradas())
        resultados.extend(self._validar_saldo_si_d_c_sf())
        resultados.extend(self._validar_movimentos_vs_i155())
        resultados.extend(self._validar_dre_vs_i355())
        resultados.extend(self._validar_ativo_passivo_pl())
        resultados.extend(self._validar_analiticas_orfas())
        resultados.extend(self._validar_lancamentos_sinteticas())
        resultados.extend(self._validar_hierarquia_ciclica())
        resultados.extend(self._validar_balanco_publicado())
        resultados.extend(self._validar_dre_publicada())

        from src.webhooks import emitir

        emitir(
            "ecd.validada",
            {
                "ecd_id": self.ecd_id,
                "total_inconsistencias": len(resultados),
                "erros": sum(1 for i in resultados if i.severidade == "erro"),
                "alertas": sum(1 for i in resultados if i.severidade == "alerta"),
                "status": "OK" if not any(i.severidade == "erro" for i in resultados) else "ERROS",
            },
        )
        return resultados

    def _validar_partidas_dobradas(self) -> list[Inconsistencia]:
        """(a) Σ débitos = Σ créditos por lançamento.

        Agrega no banco, em UMA consulta. Antes era uma consulta de partidas
        por lançamento: 20.002 consultas para 20.000 lançamentos, medido. Numa
        ECD de 240 mil lançamentos isso é ~240 mil viagens ao banco e ~54 s só
        nesta validação em SQLite local — sobre PostgreSQL em rede, minutos de
        pura latência, para uma das oito validações.

        O `HAVING` também muda o consumo de memória: só volta lançamento
        desbalanceado, então a memória passa a ser proporcional ao número de
        **defeitos**, não ao tamanho da escrituração. A versão anterior
        carregava os 240 mil objetos de lançamento antes de olhar qualquer um.

        `LEFT JOIN` e `COALESCE` são defensivos, não decisivos: com o `CASE`
        tendo `else_=0.0`, a soma só é NULL quando não há linha nenhuma, e aí
        `ABS(NULL - NULL)` também não satisfaz o `HAVING` — o lançamento sem
        partida fica de fora dos dois jeitos, como já ficava antes. Ficam porque
        `INNER JOIN` passaria a esconder esse lançamento do agrupamento, e é
        dele que sairia um "lançamento sem partida" se a validação vier a
        reportá-lo (hoje não reporta, nem a versão antiga reportava).
        """
        inconsistencias = []

        debitos = func.coalesce(
            func.sum(case((Partida.ind_dc == "D", Partida.vl_dc), else_=0.0)), 0.0
        )
        creditos = func.coalesce(
            func.sum(case((Partida.ind_dc == "C", Partida.vl_dc), else_=0.0)), 0.0
        )
        # A expressão é repetida no HAVING em vez de referenciada por rótulo:
        # nem todo backend aceita alias de agregado ali.
        consulta = (
            select(Lancamento.num_lcto, Lancamento.dt_lcto, debitos, creditos)
            .outerjoin(Partida, Partida.lancamento_id == Lancamento.id)
            .where(Lancamento.ecd_id == self.ecd_id)
            .group_by(Lancamento.id, Lancamento.num_lcto, Lancamento.dt_lcto)
            .having(func.abs(debitos - creditos) > 0.005)
        )

        for num_lcto, dt_lcto, total_debitos, total_creditos in self.session.execute(consulta):
            inconsistencias.append(
                Inconsistencia(
                    tipo="partidas_dobradas",
                    severidade="erro",
                    descricao=f"Lançamento {num_lcto} em {dt_lcto}: "
                    f"Débitos R$ {total_debitos:,.2f} ≠ Créditos R$ {total_creditos:,.2f}",
                    detalhes={
                        "num_lcto": num_lcto,
                        "dt_lcto": dt_lcto.isoformat(),
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

    def _linhas_publicadas(self, registro: str) -> list[LinhaDemonstracao]:
        """As linhas de um registro do bloco J, de todas as demonstrações."""
        return list(
            self.session.execute(
                select(LinhaDemonstracao)
                .join(DemonstracaoContabil)
                .where(
                    DemonstracaoContabil.ecd_id == self.ecd_id,
                    LinhaDemonstracao.registro == registro,
                )
            ).scalars()
        )

    def _validar_balanco_publicado(self) -> list[Inconsistencia]:
        """(i) O balanço declarado fecha — `REGRA_VALIDA_ATIVO_PASSIVO_FIN`.

        A validação (e) já confere ativo = passivo + PL, mas a partir dos
        saldos que o programa soma. Esta olha o balanço **publicado**: se o
        J100 não fecha, o que a empresa entregou não fecha, independentemente
        do que os saldos digam.

        Vem junto a `REGRA_EXISTEM_2_NIVEIS_1`: o manual exige exatamente
        duas linhas de nível 1, uma de ativo e uma de passivo. Sem as duas
        não há o que comparar, e a ausência é ela mesma o defeito.
        """
        linhas = [linha for linha in self._linhas_publicadas("J100") if linha.nivel_agl == 1]
        if not linhas:
            return []

        por_grupo = defaultdict(float)
        for linha in linhas:
            por_grupo[linha.ind_grp_bal] += valor_sinalizado(
                linha.vl_cta_fin, linha.ind_dc_cta_fin or "D"
            )

        if set(por_grupo) != {"A", "P"}:
            return [
                Inconsistencia(
                    tipo="balanco_publicado_incompleto",
                    severidade="erro",
                    descricao=(
                        "O balanço publicado (J100) não tem exatamente uma linha de nível 1 "
                        f"de ativo e uma de passivo: encontrados {sorted(por_grupo)}"
                    ),
                    detalhes={"grupos": sorted(g for g in por_grupo if g)},
                )
            ]

        ativo, passivo = por_grupo["A"], por_grupo["P"]
        if abs(abs(ativo) - abs(passivo)) <= 0.01:
            return []
        return [
            Inconsistencia(
                tipo="balanco_publicado_nao_fecha",
                severidade="erro",
                descricao=(
                    f"O balanço publicado não fecha: ativo = {abs(ativo):,.2f} "
                    f"vs passivo + PL = {abs(passivo):,.2f}"
                ),
                detalhes={
                    "ativo": round(abs(ativo), 2),
                    "passivo": round(abs(passivo), 2),
                    "diferenca": round(abs(ativo) - abs(passivo), 2),
                },
            )
        ]

    def _validar_dre_publicada(self) -> list[Inconsistencia]:
        """(j) A DRE publicada × os saldos declarados — `REGRA_VALIDA_SALDO_COM_DRE`.

        O manual manda comparar o valor de cada linha de **detalhe** da DRE
        (`IND_COD_AGL` = "D") com o saldo das contas que o I052 aglutina
        naquele mesmo código. Aqui a soma sai dos I355 — os saldos de
        resultado antes do encerramento —, que é o que a escrituração
        declara para essas contas.

        É a única conferência do conjunto que compara **dois documentos** em
        vez de recomputar um: se as duas não batem, a empresa publicou uma
        DRE que a própria escrituração dela não sustenta.
        """
        detalhes = [
            linha
            for linha in self._linhas_publicadas("J150")
            if (linha.ind_cod_agl or "").upper() == "D"
        ]
        if not detalhes:
            return []

        saldo_por_agl = defaultdict(float)
        for saldo, cod_agl in self.session.execute(
            select(SaldoResultado, Aglutinacao.cod_agl)
            .join(PlanoConta, PlanoConta.cod_cta == SaldoResultado.cod_cta)
            .join(Aglutinacao, Aglutinacao.plano_conta_id == PlanoConta.id)
            .where(
                SaldoResultado.ecd_id == self.ecd_id,
                PlanoConta.ecd_id == self.ecd_id,
            )
        ).all():
            saldo_por_agl[cod_agl] += valor_sinalizado(saldo.vl_sld_fin, saldo.ind_dc_fin)

        inconsistencias = []
        for linha in detalhes:
            if linha.cod_agl not in saldo_por_agl:
                continue
            publicado = valor_sinalizado(linha.vl_cta_fin, linha.ind_dc_cta_fin or "D")
            escriturado = saldo_por_agl[linha.cod_agl]
            if abs(abs(publicado) - abs(escriturado)) <= 0.01:
                continue
            inconsistencias.append(
                Inconsistencia(
                    tipo="dre_publicada_divergente",
                    severidade="alerta",
                    descricao=(
                        f"Linha {linha.cod_agl} da DRE publicada = {abs(publicado):,.2f} "
                        f"vs saldos escriturados = {abs(escriturado):,.2f}"
                    ),
                    detalhes={
                        "cod_agl": linha.cod_agl,
                        "descricao": linha.descricao,
                        "publicado": round(abs(publicado), 2),
                        "escriturado": round(abs(escriturado), 2),
                        "diferenca": round(abs(publicado) - abs(escriturado), 2),
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
        hierarquia impossível de interpretar.

        Desde a Fase 25 a importação RECUSA arquivo com ciclo; esta
        validação continua existindo para bancos que importaram antes disso.
        É erro, não alerta: um ciclo não tem leitura correta possível.
        """
        sup = {
            c.cod_cta: c.cod_cta_sup
            for c in self.session.execute(
                select(PlanoConta).where(PlanoConta.ecd_id == self.ecd_id)
            ).scalars()
        }

        inconsistencias = []
        for ciclo in encontrar_ciclos(sup):
            inconsistencias.append(
                Inconsistencia(
                    tipo="hierarquia_ciclica",
                    severidade="erro",
                    descricao=f"Ciclo na hierarquia do plano de contas: "
                    f"{' → '.join(ciclo + [ciclo[0]])} — nenhuma dessas contas "
                    "chega a uma conta de topo",
                    detalhes={
                        "cod_cta": min(ciclo),
                        "ciclo": ciclo,
                        "tamanho": len(ciclo),
                    },
                )
            )
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
