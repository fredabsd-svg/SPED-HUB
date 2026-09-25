"""Demonstração do Resultado do Exercício — DRE (F10).

Estrutura de degraus com mapeamento configurável por empresa.
Default: estrutura da Lei 6.404/76 (art. 187) e da NBC TG 26 / CPC 26.

Degraus:
  1. Receita Operacional Bruta
  2. (-) Deduções da Receita
  3. = Receita Operacional Líquida
  4. (-) Custos (CMV/CPV/CSP)
  5. = Lucro Bruto
  6. (-) Despesas Operacionais
  7. (+/-) Outras Receitas e Despesas Operacionais
  8. = Resultado Antes do Resultado Financeiro
  9. (+) Receitas Financeiras
  10. (-) Despesas Financeiras
  11. = Resultado Antes IRPJ/CSLL
  12. (-) IRPJ/CSLL
  13. = Resultado Líquido do Exercício

Sem mapeamento da empresa, cada conta vai para o degrau que o
`Classificador` indica: plano referencial (receita bruta, deduções, custos)
e nome da conta e dos superiores (CMV, despesas e receitas financeiras,
outras receitas, IRPJ/CSLL).  Antes, só o nome da própria conta contava, e
numa ECD real COMPRAS DE MERCADORIAS, IOF e TARIFAS BANCÁRIAS iam todas para
"Despesas Operacionais" — a DRE saía com custo zero.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    ECD,
    Aglutinacao,
    DemonstracaoContabil,
    LinhaDemonstracao,
    Mapeamento,
    PlanoConta,
)
from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.base import ReportContext, valor_sinalizado
from src.reports.classificacao import Classificador, categoria_dre_pelo_nome
from src.reports.saldos import somar_resultado


@dataclass
class LinhaDRE:
    tipo: str  # step, detail, subtotal, total
    descricao: str
    valor_atual: float = 0.0
    valor_anterior: float = 0.0
    ordem: int = 0
    # Só nas linhas de detalhe: a conta que a linha mostra.
    cod_cta: str = ""


# ── Estrutura default da DRE ──
DRE_DEFAULT = [
    {
        "tipo": "step",
        "descricao": "Receita Operacional Bruta",
        "categoria": "receita_bruta",
        "sinal": 1,
    },
    {"tipo": "step", "descricao": "(-) Deduções da Receita", "categoria": "deducoes", "sinal": -1},
    {
        "tipo": "subtotal",
        "descricao": "= Receita Operacional Líquida",
        "categoria": None,
        "sinal": 0,
    },
    {"tipo": "step", "descricao": "(-) Custos", "categoria": "custos", "sinal": -1},
    {"tipo": "subtotal", "descricao": "= Lucro Bruto", "categoria": None, "sinal": 0},
    {
        "tipo": "step",
        "descricao": "(-) Despesas Operacionais",
        "categoria": "despesas_operacionais",
        "sinal": -1,
    },
    {
        "tipo": "step",
        "descricao": "(+/-) Outras Receitas e Despesas Operacionais",
        "categoria": "outras_operacionais",
        "sinal": 1,
    },
    {
        "tipo": "subtotal",
        "descricao": "= Resultado Antes do Resultado Financeiro",
        "categoria": None,
        "sinal": 0,
    },
    {
        "tipo": "step",
        "descricao": "(+) Receitas Financeiras",
        "categoria": "receitas_financeiras",
        "sinal": 1,
    },
    {
        "tipo": "step",
        "descricao": "(-) Despesas Financeiras",
        "categoria": "despesas_financeiras",
        "sinal": -1,
    },
    {"tipo": "subtotal", "descricao": "= Resultado Antes IRPJ/CSLL", "categoria": None, "sinal": 0},
    {"tipo": "step", "descricao": "(-) IRPJ / CSLL", "categoria": "irpj_csll", "sinal": -1},
    {
        "tipo": "total",
        "descricao": "= Resultado Líquido do Exercício",
        "categoria": None,
        "sinal": 0,
    },
]

CATEGORIAS = [d["categoria"] for d in DRE_DEFAULT if d["categoria"]]


class DRE:
    """Gerador de DRE."""

    def __init__(self, session: Session, ecd_id: int):
        self.session = session
        self.ecd_id = ecd_id
        self.engine = FilterEngine(session, ecd_id)

    def _get_mapeamentos(self, empresa_id: int) -> dict[str, list[str]]:
        """Mapeamento DRE da empresa ou, sem ele, a classificação do plano."""
        maps = list(
            self.session.execute(
                select(Mapeamento)
                .where(
                    Mapeamento.empresa_id == empresa_id,
                    Mapeamento.tipo == "dre",
                )
                .order_by(Mapeamento.ordem)
            ).scalars()
        )

        if maps:
            result: dict[str, list[str]] = {}
            for m in maps:
                result.setdefault(m.categoria, []).append(m.cod_cta)
            return result

        return self._classificacao_do_plano(self.engine)

    @staticmethod
    def _classificacao_do_plano(engine: FilterEngine) -> dict[str, list[str]]:
        """`{categoria: [contas]}` pelo `Classificador` — referencial e nomes."""
        classificador = Classificador(engine)
        result: dict[str, list[str]] = {categoria: [] for categoria in CATEGORIAS}
        for cod_cta in engine.plano():
            categoria = classificador.categoria_dre(cod_cta)
            if categoria is not None:
                result.setdefault(categoria, []).append(cod_cta)
        return result

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

    @staticmethod
    def _valores_por_conta(
        engine: FilterEngine,
        criterios: FilterCriteria,
        mapeamentos: dict[str, list[str]],
    ) -> dict[str, tuple[str, float]]:
        """`{conta: (categoria, saldo)}` do I355, cada conta uma única vez.

        1. O I355 de cada conta soma os centros de custo e os encerramentos
           (antes ficava a última linha lida: o CC2 apagava o CC1).
        2. Só entram as contas com I355 próprio sem superior que também
           tenha — numa ECD conforme o manual, as analíticas.
        3. Cada uma vai para a categoria dela ou, se não for mapeada, do
           superior mapeado mais próximo: mapear a sintética "DESPESAS"
           leva todas as filhas, que não têm I355 na sintética.
        """
        proprios = somar_resultado(engine.aplicar_saldos_resultado(criterios))
        hierarquia = engine.hierarquia()
        categoria_da_conta: dict[str, str] = {}
        for categoria, contas in mapeamentos.items():
            for cod in contas:
                categoria_da_conta.setdefault(cod, categoria)

        valores: dict[str, tuple[str, float]] = {}
        for cod in hierarquia.contas_base(proprios):
            categoria = hierarquia.atribuir(cod, categoria_da_conta)
            if categoria is not None:
                valores[cod] = (categoria, proprios[cod])
        return valores

    @classmethod
    def _valores_por_categoria(
        cls,
        engine: FilterEngine,
        criterios: FilterCriteria,
        mapeamentos: dict[str, list[str]],
    ) -> dict[str, float]:
        """Soma do I355 por categoria da DRE (ver `_valores_por_conta`)."""
        valores: dict[str, float] = {cat: 0.0 for cat in mapeamentos}
        for categoria, saldo in cls._valores_por_conta(engine, criterios, mapeamentos).values():
            valores[categoria] = valores.get(categoria, 0.0) + saldo
        return valores

    def _anterior_publicado(
        self, mapeamentos: dict[str, list[str]]
    ) -> tuple[dict[str, float], dict[str, float]]:
        """A DRE do exercício anterior como a própria ECD a publica.

        O J150 traz em `VL_CTA_INI` o valor de cada linha no período
        anterior. Cada linha de detalhe vai para a categoria das contas que
        o I052 aglutina nela; linha sem conta no plano atual, pela descrição
        dela e das linhas superiores. Devolve `(por categoria, por conta)` —
        por conta só quando a linha aglutina uma conta só.
        """
        demonstracao = self.session.execute(
            select(DemonstracaoContabil)
            .where(DemonstracaoContabil.ecd_id == self.ecd_id)
            .order_by(DemonstracaoContabil.dt_fin.desc(), DemonstracaoContabil.id_dem)
        ).scalars()
        linhas: list[LinhaDemonstracao] = []
        for dem in demonstracao:
            linhas = [ln for ln in dem.linhas if ln.registro == "J150"]
            if linhas:
                break
        if not linhas:
            return {}, {}

        contas_do_codigo: dict[str, list[str]] = {}
        for cod_cta, cod_agl in self.session.execute(
            select(PlanoConta.cod_cta, Aglutinacao.cod_agl)
            .join(Aglutinacao, Aglutinacao.plano_conta_id == PlanoConta.id)
            .where(PlanoConta.ecd_id == self.ecd_id)
        ):
            contas_do_codigo.setdefault(cod_agl, []).append(cod_cta)

        categoria_da_conta: dict[str, str] = {}
        for categoria, contas in mapeamentos.items():
            for cod in contas:
                categoria_da_conta.setdefault(cod, categoria)
        hierarquia = self.engine.hierarquia()
        por_codigo = {ln.cod_agl: ln for ln in linhas}

        def categoria_pela_descricao(linha: LinhaDemonstracao) -> str | None:
            vistos, atual = set(), linha
            while atual is not None and atual.cod_agl not in vistos:
                vistos.add(atual.cod_agl)
                categoria = categoria_dre_pelo_nome(atual.descricao or "")
                if categoria is not None:
                    return categoria
                atual = por_codigo.get(atual.cod_agl_sup or "")
            return None

        por_categoria: dict[str, float] = {}
        por_conta: dict[str, float] = {}
        for linha in linhas:
            if (linha.ind_cod_agl or "").upper() != "D" or not linha.vl_cta_ini:
                continue
            valor = valor_sinalizado(linha.vl_cta_ini, linha.ind_dc_cta_ini or "D")
            contas = contas_do_codigo.get(linha.cod_agl, [])
            categoria = next(
                (
                    c
                    for c in (hierarquia.atribuir(cod, categoria_da_conta) for cod in contas)
                    if c is not None
                ),
                None,
            ) or categoria_pela_descricao(linha)
            if categoria is None:
                continue
            por_categoria[categoria] = por_categoria.get(categoria, 0.0) + valor
            if len(contas) == 1:
                por_conta[contas[0]] = por_conta.get(contas[0], 0.0) + valor
        return por_categoria, por_conta

    def gerar(
        self,
        criterios: FilterCriteria | None = None,
        empresa_id: int | None = None,
        detalhar: bool = False,
    ) -> tuple[ReportContext, list[LinhaDRE], dict[str, float]]:
        """Gera a DRE.

        Args:
            criterios: Filtros F7
            empresa_id: ID da empresa para mapeamentos (None = usa default)
            detalhar: inclui, abaixo de cada degrau, uma linha por conta —
                é o que deixa conferir a classificação

        Returns:
            (contexto, linhas, totais)

        O período anterior vem da ECD anterior, se importada; senão, da DRE
        publicada na própria ECD (J150, `VL_CTA_INI`).
        """
        if criterios is None:
            criterios = FilterCriteria()

        # Determina empresa_id
        if empresa_id is None:
            ecd = self.session.get(ECD, self.ecd_id)
            empresa_id = ecd.empresa_id if ecd else 0

        mapeamentos = self._get_mapeamentos(empresa_id)

        # Valores por conta e por categoria (atual e anterior), pelo I355.
        por_conta = self._valores_por_conta(self.engine, criterios, mapeamentos)
        cat_valores: dict[str, float] = {}
        for categoria, saldo in por_conta.values():
            cat_valores[categoria] = cat_valores.get(categoria, 0.0) + saldo

        ecd_ant_id = self._get_ecd_anterior()
        cat_valores_ant: dict[str, float] = {}
        conta_ant: dict[str, float] = {}
        origem_anterior = None
        if ecd_ant_id:
            engine_ant = FilterEngine(self.session, ecd_ant_id)
            mapeamentos_ant = (
                mapeamentos
                if self._tem_mapeamento(empresa_id)
                else self._classificacao_do_plano(engine_ant)
            )
            for cod, (categoria, saldo) in self._valores_por_conta(
                engine_ant, FilterCriteria(), mapeamentos_ant
            ).items():
                cat_valores_ant[categoria] = cat_valores_ant.get(categoria, 0.0) + saldo
                conta_ant[cod] = saldo
            origem_anterior = "ecd_anterior"
        else:
            cat_valores_ant, conta_ant = self._anterior_publicado(mapeamentos)
            if cat_valores_ant:
                origem_anterior = "publicado"

        plano = self.engine.plano()
        ordem_do_plano = {cod: i for i, cod in enumerate(self.engine.hierarquia().em_ordem())}

        # Monta linhas da DRE
        linhas: list[LinhaDRE] = []
        running = 0.0
        running_ant = 0.0

        for i, degrau in enumerate(DRE_DEFAULT):
            if degrau["categoria"] is None:
                # Subtotal ou total
                linhas.append(
                    LinhaDRE(
                        tipo=degrau["tipo"],
                        descricao=degrau["descricao"],
                        valor_atual=round(running, 2) + 0.0,
                        valor_anterior=round(running_ant, 2) + 0.0,
                        ordem=i,
                    )
                )
                continue

            vl = cat_valores.get(degrau["categoria"], 0.0)
            vl_ant = cat_valores_ant.get(degrau["categoria"], 0.0)
            # Na DRE, crédito soma e débito subtrai: o valor é o saldo
            # interno (débito +, crédito −) com o sinal trocado, para
            # receita e para despesa.  O `abs()` de antes mostrava uma
            # despesa com saldo credor (recuperação maior que o gasto)
            # como despesa, e o resultado não batia com o I355.
            # `+ 0.0` normaliza o -0.0 da categoria vazia (sairia "-0.0"
            # na API).
            vl_dre = -vl + 0.0
            vl_dre_ant = -vl_ant + 0.0

            running += vl_dre
            running_ant += vl_dre_ant

            linhas.append(
                LinhaDRE(
                    tipo=degrau["tipo"],
                    descricao=degrau["descricao"],
                    valor_atual=vl_dre,
                    valor_anterior=vl_dre_ant,
                    ordem=i,
                )
            )
            if detalhar:
                contas = {
                    cod
                    for cod, (categoria, _saldo) in por_conta.items()
                    if categoria == degrau["categoria"]
                }
                contas |= {
                    cod
                    for cod in conta_ant
                    if cod in plano
                    and self.engine.hierarquia().atribuir(cod, self._categorias(mapeamentos))
                    == degrau["categoria"]
                }
                for cod in sorted(contas, key=lambda c: (ordem_do_plano.get(c, 1 << 30), c)):
                    atual = -por_conta.get(cod, ("", 0.0))[1] + 0.0
                    anterior = -conta_ant.get(cod, 0.0) + 0.0
                    if abs(atual) < 0.005 and abs(anterior) < 0.005:
                        continue
                    linhas.append(
                        LinhaDRE(
                            tipo="detail",
                            descricao=plano[cod].nome_cta if cod in plano else cod,
                            valor_atual=atual,
                            valor_anterior=anterior,
                            ordem=i,
                            cod_cta=cod,
                        )
                    )

        resultado_liquido = round(running, 2) + 0.0

        def _subtotal(texto: str, campo: str) -> float:
            return next((getattr(ln, campo) for ln in linhas if texto in ln.descricao), 0.0)

        totais = {
            "resultado_liquido": resultado_liquido,
            "resultado_liquido_anterior": round(running_ant, 2) + 0.0,
            # Com o sinal da DRE (receita positiva). Era o saldo interno,
            # negativo, e o painel nunca mostrava a margem líquida.
            "receita_bruta": -cat_valores.get("receita_bruta", 0.0) + 0.0,
            "lucro_bruto": _subtotal("Lucro Bruto", "valor_atual"),
            "receita_liquida": _subtotal("Receita Operacional Líquida", "valor_atual"),
            "tem_anterior": origem_anterior is not None,
            "origem_anterior": origem_anterior,
        }

        ctx = ReportContext(
            titulo="Demonstração do Resultado do Exercício",
            filtros_descricao=self.engine.descricao_filtros(criterios),
        )

        return ctx, linhas, totais

    def _tem_mapeamento(self, empresa_id: int) -> bool:
        return (
            self.session.execute(
                select(Mapeamento.id)
                .where(Mapeamento.empresa_id == empresa_id, Mapeamento.tipo == "dre")
                .limit(1)
            ).first()
            is not None
        )

    @staticmethod
    def _categorias(mapeamentos: dict[str, list[str]]) -> dict[str, str]:
        categoria_da_conta: dict[str, str] = {}
        for categoria, contas in mapeamentos.items():
            for cod in contas:
                categoria_da_conta.setdefault(cod, categoria)
        return categoria_da_conta
