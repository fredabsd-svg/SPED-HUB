"""Demonstração dos Fluxos de Caixa — DFC pelos métodos direto e indireto.

Estrutura conforme a NBC TG 03 / CPC 03 (R2):
  1. Fluxo das atividades operacionais (direto ou indireto)
  2. Fluxo das atividades de investimento
  3. Fluxo das atividades de financiamento
  4. Aumento (redução) líquido de caixa e equivalentes
  5. Conciliação com os saldos de caixa e equivalentes (item 45)

**Só entra o que passou pelo caixa.** Os dois métodos saem dos lançamentos
(I200/I250), não da variação dos saldos: cada lançamento que movimenta uma
conta de caixa ou equivalente tem o valor que entrou ou saiu repartido entre
as contrapartidas — recebimento de clientes, pagamento a fornecedor, compra
de imobilizado, captação de empréstimo...  Assim:

* **transferência entre contas da própria empresa não é fluxo** (item 9): o
  lançamento só com contas de caixa (banco → banco, banco → aplicação de
  liquidez imediata) não entra, e a conta de passagem "TRANSFERÊNCIAS ENTRE
  CONTAS" é tratada como numerário em trânsito — parte do caixa;
* **transação sem caixa não é fluxo** (item 43): comprar imobilizado a prazo,
  depreciar ou provisionar não aparece nas atividades de investimento e
  financiamento, como aparecia quando a DFC era a variação dos saldos.

O método indireto parte do lucro do período e o ajusta pelos itens sem
efeito caixa e pela variação dos ativos e passivos operacionais — mas a
variação e os ajustes também saem dos lançamentos, então o caixa das
operações é o mesmo pelos dois métodos, por construção. Investimento e
financiamento são os mesmos nos dois (item 21: recebimentos e pagamentos
brutos). Juros pagos e recebidos ficam nas operacionais, como o item 34A
recomenda; IR e CSLL também (item 35).

A repartição de um lançamento com várias contrapartidas é proporcional: o
valor que entrou no caixa vai para as contrapartidas do lado oposto, na
proporção de cada uma. Um recebimento de 90 com desconto concedido de 10
(C clientes 100, D banco 90, D descontos 10) é recebimento de clientes de
90, e o desconto, sem caixa, fica como ajuste no método indireto.

A classificação das contas é a do `Classificador`; o mapeamento "dfc" da
empresa (`Mapeamento`) vale por cima dele, pela conta ou pelo superior
mapeado mais próximo — as categorias antigas, de variação de saldo, são
traduzidas para as de fluxo.

ECD sem lançamentos (livro de balancetes, razão auxiliar) cai no cálculo
pela variação dos saldos: o método indireto sai, com a ressalva; o direto,
não há como fazer.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import ECD, Lancamento, Mapeamento, Partida
from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.base import ReportContext, valor_sinalizado
from src.reports.classificacao import (
    ATIVIDADE_DA_CATEGORIA,
    ATIVIDADE_FINANCIAMENTO,
    ATIVIDADE_INVESTIMENTO,
    ATIVIDADE_OPERACIONAL,
    CATEGORIA_DO_MAPEAMENTO_ANTIGO,
    Classificador,
)
from src.reports.saldos import consolidar, somar_resultado

TOLERANCIA_CONCILIACAO = 0.01
_CENTAVO = 0.005


@dataclass
class LinhaDFC:
    tipo: str  # section, grupo, step, subtotal, total, conciliacao
    descricao: str
    valor: float = 0.0
    valor_anterior: float = 0.0
    ordem: int = 0


# (descrição, categorias, parte): "liquido" soma entradas e saídas; "entradas"
# e "saidas" separam os fluxos brutos da mesma categoria (CPC 03, item 21).
LINHAS_OPERACIONAIS_DIRETO = [
    ("Recebimentos de clientes", ("clientes",), "liquido"),
    ("Juros e rendimentos recebidos", ("juros_recebidos",), "liquido"),
    ("Outros recebimentos operacionais", ("outros_op",), "entradas"),
    (
        "Pagamentos a fornecedores de mercadorias e serviços",
        ("fornecedores", "estoques"),
        "liquido",
    ),
    ("Pagamentos a empregados e encargos sociais", ("empregados",), "liquido"),
    ("Pagamentos de tributos (exceto IR e CSLL)", ("tributos",), "liquido"),
    ("Juros e encargos financeiros pagos", ("juros_pagos",), "liquido"),
    ("IR e CSLL pagos", ("ir_csll",), "liquido"),
    ("Outros pagamentos operacionais", ("outros_op",), "saidas"),
]
LINHAS_INVESTIMENTO = [
    ("Aquisição de imobilizado", ("imobilizado",), "saidas"),
    ("Recebimento pela venda de imobilizado", ("imobilizado",), "entradas"),
    ("Aquisição de intangível", ("intangivel",), "liquido"),
    ("Aquisição de investimentos", ("investimentos",), "saidas"),
    ("Recebimento pela venda de investimentos", ("investimentos",), "entradas"),
    ("Empréstimos concedidos a terceiros", ("emprestimos_concedidos",), "saidas"),
    ("Recebimento de empréstimos concedidos", ("emprestimos_concedidos",), "entradas"),
    ("Aplicações financeiras (não equivalentes de caixa)", ("aplicacoes",), "liquido"),
    ("Outros fluxos de investimento", ("outros_inv",), "liquido"),
]
LINHAS_FINANCIAMENTO = [
    ("Captação de empréstimos e financiamentos", ("emprestimos",), "entradas"),
    ("Amortização de empréstimos e financiamentos", ("emprestimos",), "saidas"),
    ("Integralização (restituição) de capital", ("capital",), "liquido"),
    ("Lucros e dividendos distribuídos", ("distribuicao",), "liquido"),
    ("Outros fluxos de financiamento", ("outros_fin",), "liquido"),
]
# (tipo, descrição, chave). "grupo" é subtítulo, sem valor.
LINHAS_OPERACIONAIS_INDIRETO = [
    ("step", "Lucro (prejuízo) líquido do período", "lucro"),
    ("grupo", "Ajustes por itens sem efeito caixa", None),
    ("step", "(+) Depreciação, amortização e exaustão", "adj_depreciacao"),
    ("step", "(+/-) Juros e encargos apropriados sem pagamento", "adj_financeiros"),
    ("step", "(+/-) Resultado na baixa de ativos", "adj_baixa"),
    ("step", "(+/-) Outros itens sem efeito caixa", "adj_outros"),
    ("grupo", "Variações nos ativos e passivos operacionais", None),
    ("step", "(Aumento) redução em clientes", "wc_clientes"),
    ("step", "(Aumento) redução em estoques", "wc_estoques"),
    ("step", "(Aumento) redução em tributos a recuperar", "wc_tributos_ativo"),
    ("step", "(Aumento) redução em outros ativos operacionais", "wc_outros_ativo"),
    ("step", "Aumento (redução) em fornecedores", "wc_fornecedores"),
    ("step", "Aumento (redução) em obrigações trabalhistas", "wc_trabalhistas"),
    ("step", "Aumento (redução) em obrigações tributárias", "wc_tributos_passivo"),
    ("step", "Aumento (redução) em outros passivos operacionais", "wc_outros_passivo"),
]

# Linhas que aparecem mesmo zeradas.
_SEMPRE = {"Lucro (prejuízo) líquido do período"}

SUBTOTAL_OPERACIONAL = "= Caixa líquido das atividades operacionais"
SUBTOTAL_INVESTIMENTO = "= Caixa líquido das atividades de investimento"
SUBTOTAL_FINANCIAMENTO = "= Caixa líquido das atividades de financiamento"
TOTAL = "= Aumento (redução) líquido de caixa e equivalentes"


def _r(valor: float) -> float:
    """Centavos, sem o -0.0 que a troca de sinal de uma variação nula produz."""
    return round(valor, 2) + 0.0


def ratear(total: float, valores: list[float]) -> list[float]:
    """Reparte `total` entre os valores de mesmo sinal que ele, na proporção de cada um.

    Num lançamento balanceado a soma dos valores de mesmo sinal nunca é menor
    que o total, e a repartição é exata. Se for (lançamento desbalanceado), a
    parte que sobra não é atribuída — quem chama decide o que fazer dela.
    """
    if abs(total) <= _CENTAVO or not valores:
        return [0.0] * len(valores)
    mesmo_sinal = [v if (v > 0) == (total > 0) else 0.0 for v in valores]
    soma = sum(mesmo_sinal)
    if abs(soma) <= _CENTAVO:
        return [0.0] * len(valores)
    fator = min(1.0, total / soma)
    return [v * fator for v in mesmo_sinal]


@dataclass
class _Fluxos:
    """Os fluxos de uma ECD, prontos para os dois métodos."""

    entradas: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    saidas: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    indireto: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    caixa_inicial: float = 0.0
    caixa_final: float = 0.0
    # Soma do caixa movimentado pelos lançamentos (entradas − saídas).
    pelos_lancamentos: float = 0.0
    transferencias_valor: float = 0.0
    transferencias_qtd: int = 0
    sem_caixa_qtd: int = 0
    nao_atribuido: float = 0.0
    lucro_dre: float = 0.0
    sem_lancamentos: bool = False
    composicao: list[dict] = field(default_factory=list)

    @property
    def variacao_saldos(self) -> float:
        return self.caixa_final - self.caixa_inicial

    def fluxo(self, categorias: tuple[str, ...], parte: str) -> float:
        entradas = sum(self.entradas.get(c, 0.0) for c in categorias)
        saidas = sum(self.saidas.get(c, 0.0) for c in categorias)
        if parte == "entradas":
            return entradas
        if parte == "saidas":
            return saidas
        return entradas + saidas

    def atividade(self, atividade: str) -> float:
        return sum(
            self.entradas.get(c, 0.0) + self.saidas.get(c, 0.0)
            for c, a in ATIVIDADE_DA_CATEGORIA.items()
            if a == atividade
        )

    @property
    def operacional_indireto(self) -> float:
        return sum(self.indireto.values())


class DFC:
    """Gerador de DFC — Demonstração dos Fluxos de Caixa."""

    def __init__(self, session: Session, ecd_id: int):
        self.session = session
        self.ecd_id = ecd_id
        self.engine = FilterEngine(session, ecd_id)

    def _get_mapeamentos(self, empresa_id: int) -> dict[str, str]:
        """Mapeamento da empresa, `{cod_cta: categoria de fluxo}` (primeiro por ordem)."""
        mapeamento: dict[str, str] = {}
        for m in self.session.execute(
            select(Mapeamento)
            .where(Mapeamento.empresa_id == empresa_id, Mapeamento.tipo == "dfc")
            .order_by(Mapeamento.ordem)
        ).scalars():
            categoria = CATEGORIA_DO_MAPEAMENTO_ANTIGO.get(m.categoria, m.categoria)
            if categoria == "caixa" or categoria in ATIVIDADE_DA_CATEGORIA:
                mapeamento.setdefault(m.cod_cta, categoria)
        return mapeamento

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

    # ── Cálculo ──

    @classmethod
    def _calcular(
        cls, engine: FilterEngine, criterios: FilterCriteria, mapeamento: dict[str, str]
    ) -> _Fluxos:
        classificador = Classificador(engine)
        hierarquia = engine.hierarquia()
        plano = engine.plano()

        def categoria(cod: str) -> str:
            if cod not in plano:
                return "outros_op"
            mapeada = hierarquia.atribuir(cod, mapeamento)
            return mapeada if mapeada is not None else classificador.categoria_dfc(cod)

        fluxos = _Fluxos()
        fluxos.lucro_dre = -sum(
            somar_resultado(engine.aplicar_saldos_resultado(criterios)).values()
        )

        # Caixa e equivalentes pelos saldos (I155): início, fim e composição.
        saldos = consolidar(engine, criterios)
        for cod in hierarquia.contas_base(saldos.proprios):
            if categoria(cod) != "caixa":
                continue
            saldo = saldos.proprios[cod]
            fluxos.caixa_inicial += saldo.si
            fluxos.caixa_final += saldo.sf
            if abs(saldo.si) > _CENTAVO or abs(saldo.sf) > _CENTAVO or saldo.tem_movimento:
                fluxos.composicao.append(
                    {
                        "cod_cta": cod,
                        "nome_cta": plano[cod].nome_cta if cod in plano else cod,
                        "inicial": _r(saldo.si),
                        "final": _r(saldo.sf),
                    }
                )

        consulta = (
            select(
                Lancamento.id, Lancamento.ind_lcto, Partida.cod_cta, Partida.vl_dc, Partida.ind_dc
            )
            .join(Partida, Partida.lancamento_id == Lancamento.id)
            .where(Lancamento.ecd_id == engine.ecd_id)
            .order_by(Lancamento.id, Partida.id)
        )
        if criterios.dt_ini:
            consulta = consulta.where(Lancamento.dt_lcto >= criterios.dt_ini)
        if criterios.dt_fin:
            consulta = consulta.where(Lancamento.dt_lcto <= criterios.dt_fin)

        atual_id = None
        indicador = "N"
        partidas: list[tuple[str, float]] = []
        algum = False
        for lanc_id, ind_lcto, cod, vl_dc, ind_dc in engine.session.execute(consulta):
            algum = True
            if lanc_id != atual_id:
                if partidas:
                    cls._lancamento(fluxos, indicador, partidas, categoria, classificador)
                atual_id, indicador, partidas = lanc_id, ind_lcto or "N", []
            partidas.append((cod, valor_sinalizado(vl_dc or 0.0, ind_dc)))
        if partidas:
            cls._lancamento(fluxos, indicador, partidas, categoria, classificador)

        if not algum and not cls._tem_lancamentos(engine):
            return cls._calcular_por_saldos(engine, criterios, categoria, classificador, fluxos)
        return fluxos

    @staticmethod
    def _tem_lancamentos(engine: FilterEngine) -> bool:
        return (
            engine.session.execute(
                select(Lancamento.id).where(Lancamento.ecd_id == engine.ecd_id).limit(1)
            ).first()
            is not None
        )

    @staticmethod
    def _lancamento(
        fluxos: _Fluxos,
        indicador: str,
        partidas: list[tuple[str, float]],
        categoria,
        classificador: Classificador,
    ) -> None:
        """Um lançamento: o caixa que ele movimentou e o que ele diz ao método indireto."""
        caixa = [v for cod, v in partidas if categoria(cod) == "caixa"]
        outras = [(cod, v, categoria(cod)) for cod, v in partidas if categoria(cod) != "caixa"]
        movimento = sum(caixa)

        cruzado = [0.0] * len(outras)
        if caixa and abs(movimento) <= _CENTAVO:
            # Só caixa com caixa: transferência entre contas da própria
            # empresa, gestão do caixa e não fluxo (CPC 03, item 9).
            fluxos.transferencias_qtd += 1
            fluxos.transferencias_valor += sum(v for v in caixa if v > 0)
        elif caixa:
            fluxos.pelos_lancamentos += movimento
            casados = ratear(-movimento, [v for _cod, v, _cat in outras])
            atribuido = 0.0
            for (_cod, _v, cat), casado in zip(outras, casados, strict=True):
                fluxo = -casado
                atribuido += fluxo
                if fluxo >= 0:
                    fluxos.entradas[cat] += fluxo
                else:
                    fluxos.saidas[cat] += fluxo
            sobra = movimento - atribuido
            if abs(sobra) > _CENTAVO:
                # Lançamento desbalanceado: o caixa andou sem contrapartida.
                fluxos.nao_atribuido += sobra
                if sobra >= 0:
                    fluxos.entradas["outros_op"] += sobra
                else:
                    fluxos.saidas["outros_op"] += sobra
            cruzado = casados
        else:
            fluxos.sem_caixa_qtd += 1

        if DFC._eh_encerramento(indicador, outras, classificador):
            return

        # Método indireto. `resto` é a parte de cada partida que não casou
        # com o caixa; a parte operacional que sobra casa com investimento
        # ou financiamento sem caixa — é o ajuste do lucro.
        operacionais = [
            (i, cod, v - cruzado[i])
            for i, (cod, v, cat) in enumerate(outras)
            if ATIVIDADE_DA_CATEGORIA.get(cat) == ATIVIDADE_OPERACIONAL
        ]
        sem_caixa_operacional = sum(resto for _i, _cod, resto in operacionais)
        ajustes = ratear(sem_caixa_operacional, [resto for _i, _cod, resto in operacionais])
        for (i, cod, _resto), ajuste in zip(operacionais, ajustes, strict=True):
            valor = outras[i][1]
            if classificador.natureza(cod) in ("04", "09"):
                fluxos.indireto["lucro"] -= valor
                if abs(ajuste) > 1e-9:
                    fluxos.indireto[classificador.ajuste_indireto(cod)] += ajuste
            else:
                linha = classificador.linha_capital_de_giro(cod)
                fluxos.indireto[linha] -= valor - ajuste

    @staticmethod
    def _eh_encerramento(
        indicador: str, outras: list[tuple[str, float, str]], classificador: Classificador
    ) -> bool:
        """Lançamento que transfere o resultado para o PL.

        O indicador "E" do I200 diz isso; quem não usa o indicador é pego
        pela forma: toca conta de apuração (natureza 09), ou só junta contas
        de resultado e de PL, sem caixa.
        """
        if indicador == "E":
            return True
        naturezas = {classificador.natureza(cod) for cod, _v, _c in outras}
        if "09" in naturezas:
            return True
        com_pl = any(classificador.eh_pl(cod) for cod, _v, _c in outras)
        so_resultado_e_pl = all(
            classificador.natureza(cod) == "04" or classificador.eh_pl(cod)
            for cod, _v, _c in outras
        )
        return com_pl and so_resultado_e_pl and "04" in naturezas

    @staticmethod
    def _calcular_por_saldos(
        engine: FilterEngine,
        criterios: FilterCriteria,
        categoria,
        classificador: Classificador,
        fluxos: _Fluxos,
    ) -> _Fluxos:
        """ECD sem lançamentos: o indireto pela variação dos saldos, como antes.

        Sem o lançamento não se separa compra de venda nem transação sem
        caixa: imobilizado sai líquido, e a conciliação acusa o que não
        fechar.
        """
        fluxos.sem_lancamentos = True
        saldos = consolidar(engine, criterios)
        fluxos.indireto["lucro"] = fluxos.lucro_dre
        for cod in engine.hierarquia().contas_base(saldos.proprios):
            cat = categoria(cod)
            natureza = classificador.natureza(cod)
            if cat == "caixa" or natureza not in ("01", "02", "03"):
                continue
            saldo = saldos.proprios[cod]
            fluxo = -(saldo.sf - saldo.si)
            atividade = ATIVIDADE_DA_CATEGORIA.get(cat, ATIVIDADE_OPERACIONAL)
            if cat == "imobilizado" and classificador.ajuste_indireto(cod) == "adj_depreciacao":
                fluxos.indireto["adj_depreciacao"] += fluxo
            elif atividade == ATIVIDADE_OPERACIONAL:
                fluxos.indireto[classificador.linha_capital_de_giro(cod)] += fluxo
            else:
                destino = fluxos.entradas if fluxo >= 0 else fluxos.saidas
                destino[cat] += fluxo
        # O lucro entra no PL pelos lucros acumulados: o que a variação dessas
        # contas não explica pelo lucro foi distribuído.
        fluxos.saidas["distribuicao"] -= fluxos.lucro_dre
        operacional = fluxos.operacional_indireto
        fluxos.entradas["outros_op"] += operacional
        fluxos.pelos_lancamentos = operacional + sum(
            fluxos.entradas.get(c, 0.0) + fluxos.saidas.get(c, 0.0)
            for c, a in ATIVIDADE_DA_CATEGORIA.items()
            if a != ATIVIDADE_OPERACIONAL
        )
        return fluxos

    # ── Apresentação ──

    def gerar(
        self,
        criterios: FilterCriteria | None = None,
        empresa_id: int | None = None,
        metodo: str = "indireto",
    ) -> tuple[ReportContext, list[LinhaDFC], dict]:
        """Gera a DFC pelo método pedido ("indireto" ou "direto"), com comparativo.

        Dos critérios, vale o período: DFC de parte das contas não fecha com
        o caixa, então filtro de conta não se aplica (e não aparece no
        cabeçalho).
        """
        if metodo not in ("indireto", "direto"):
            raise ValueError(f"Método da DFC desconhecido: {metodo!r}")
        atual, anterior, tem_anterior, criterios = self._fluxos(criterios, empresa_id)
        linhas = self._linhas(metodo, atual, anterior, tem_anterior)
        totais = self._totais(metodo, atual, anterior, tem_anterior)
        ctx = ReportContext(
            titulo="Demonstração dos Fluxos de Caixa",
            filtros_descricao=self.engine.descricao_filtros(criterios),
        )
        return ctx, linhas, totais

    def gerar_ambos(
        self,
        criterios: FilterCriteria | None = None,
        empresa_id: int | None = None,
    ) -> tuple[ReportContext, dict[str, list[LinhaDFC]], dict]:
        """Os dois métodos de uma vez — o CPC 03 (item 20A) pede a conciliação do
        lucro com o caixa das operações a quem apresenta pelo direto."""
        atual, anterior, tem_anterior, criterios = self._fluxos(criterios, empresa_id)
        linhas = {
            "direto": self._linhas("direto", atual, anterior, tem_anterior),
            "indireto": self._linhas("indireto", atual, anterior, tem_anterior),
        }
        totais = self._totais("direto", atual, anterior, tem_anterior)
        ctx = ReportContext(
            titulo="Demonstração dos Fluxos de Caixa",
            filtros_descricao=self.engine.descricao_filtros(criterios),
        )
        return ctx, linhas, totais

    def _fluxos(
        self, criterios: FilterCriteria | None, empresa_id: int | None
    ) -> tuple[_Fluxos, _Fluxos, bool, FilterCriteria]:
        criterios = criterios or FilterCriteria()
        criterios = FilterCriteria(dt_ini=criterios.dt_ini, dt_fin=criterios.dt_fin)

        if empresa_id is None:
            ecd = self.session.get(ECD, self.ecd_id)
            empresa_id = ecd.empresa_id if ecd else 0

        mapeamento = self._get_mapeamentos(empresa_id)
        atual = self._calcular(self.engine, criterios, mapeamento)

        ecd_ant_id = self._get_ecd_anterior()
        anterior = _Fluxos()
        if ecd_ant_id:
            anterior = self._calcular(
                FilterEngine(self.session, ecd_ant_id), FilterCriteria(), mapeamento
            )
        return atual, anterior, ecd_ant_id is not None, criterios

    @staticmethod
    def _linhas(
        metodo: str, atual: _Fluxos, anterior: _Fluxos, tem_anterior: bool
    ) -> list[LinhaDFC]:
        linhas: list[LinhaDFC] = []

        def adicionar(tipo: str, descricao: str, valor: float = 0.0, ant: float = 0.0):
            linhas.append(LinhaDFC(tipo, descricao, _r(valor), _r(ant), len(linhas)))

        def detalhes(definicoes):
            for descricao, categorias, parte in definicoes:
                valor = atual.fluxo(categorias, parte)
                ant = anterior.fluxo(categorias, parte) if tem_anterior else 0.0
                if abs(valor) > _CENTAVO or abs(ant) > _CENTAVO:
                    adicionar("step", descricao, valor, ant)

        adicionar("section", "Fluxo das atividades operacionais")
        if metodo == "direto" and not atual.sem_lancamentos:
            detalhes(LINHAS_OPERACIONAIS_DIRETO)
            operacional = atual.atividade(ATIVIDADE_OPERACIONAL)
            operacional_ant = anterior.atividade(ATIVIDADE_OPERACIONAL)
        else:
            if metodo == "direto":
                adicionar(
                    "grupo",
                    "ECD sem lançamentos: o método direto não é possível; "
                    "operacional pelo método indireto",
                )
            pendente_grupo = None
            for tipo, descricao, chave in LINHAS_OPERACIONAIS_INDIRETO:
                if tipo == "grupo":
                    pendente_grupo = descricao
                    continue
                valor = atual.indireto.get(chave, 0.0)
                ant = anterior.indireto.get(chave, 0.0) if tem_anterior else 0.0
                if descricao in _SEMPRE or abs(valor) > _CENTAVO or abs(ant) > _CENTAVO:
                    if pendente_grupo:
                        adicionar("grupo", pendente_grupo)
                        pendente_grupo = None
                    adicionar("step", descricao, valor, ant)
            operacional = atual.operacional_indireto
            operacional_ant = anterior.operacional_indireto
        adicionar("subtotal", SUBTOTAL_OPERACIONAL, operacional, operacional_ant)

        adicionar("section", "Fluxo das atividades de investimento")
        detalhes(LINHAS_INVESTIMENTO)
        adicionar(
            "subtotal",
            SUBTOTAL_INVESTIMENTO,
            atual.atividade(ATIVIDADE_INVESTIMENTO),
            anterior.atividade(ATIVIDADE_INVESTIMENTO),
        )

        adicionar("section", "Fluxo das atividades de financiamento")
        detalhes(LINHAS_FINANCIAMENTO)
        adicionar(
            "subtotal",
            SUBTOTAL_FINANCIAMENTO,
            atual.atividade(ATIVIDADE_FINANCIAMENTO),
            anterior.atividade(ATIVIDADE_FINANCIAMENTO),
        )

        total = (
            operacional
            + atual.atividade(ATIVIDADE_INVESTIMENTO)
            + atual.atividade(ATIVIDADE_FINANCIAMENTO)
        )
        total_ant = (
            operacional_ant
            + anterior.atividade(ATIVIDADE_INVESTIMENTO)
            + anterior.atividade(ATIVIDADE_FINANCIAMENTO)
        )
        adicionar("total", TOTAL, total, total_ant)

        adicionar("section", "Conciliação com caixa e equivalentes")
        adicionar(
            "conciliacao",
            "Caixa e equivalentes no início do período",
            atual.caixa_inicial,
            anterior.caixa_inicial,
        )
        adicionar(
            "conciliacao",
            "Caixa e equivalentes no fim do período",
            atual.caixa_final,
            anterior.caixa_final,
        )
        adicionar(
            "conciliacao",
            "Variação de caixa nos saldos (fim − início)",
            atual.variacao_saldos,
            anterior.variacao_saldos,
        )
        adicionar(
            "conciliacao",
            "Diferença não conciliada (fluxos − saldos)",
            total - atual.variacao_saldos,
            total_ant - anterior.variacao_saldos,
        )
        return linhas

    @staticmethod
    def _totais(metodo: str, atual: _Fluxos, anterior: _Fluxos, tem_anterior: bool) -> dict:
        def resumo(fluxos: _Fluxos) -> dict[str, float]:
            if metodo == "direto" and not fluxos.sem_lancamentos:
                operacional = fluxos.atividade(ATIVIDADE_OPERACIONAL)
            else:
                operacional = fluxos.operacional_indireto
            investimento = fluxos.atividade(ATIVIDADE_INVESTIMENTO)
            financiamento = fluxos.atividade(ATIVIDADE_FINANCIAMENTO)
            variacao = operacional + investimento + financiamento
            return {
                "operacional": _r(operacional),
                "investimento": _r(investimento),
                "financiamento": _r(financiamento),
                "variacao_caixa": _r(variacao),
                "caixa_inicial": _r(fluxos.caixa_inicial),
                "caixa_final": _r(fluxos.caixa_final),
                "variacao_caixa_saldos": _r(fluxos.variacao_saldos),
                "diferenca_conciliacao": _r(variacao - fluxos.variacao_saldos),
                "lucro_liquido": _r(fluxos.indireto.get("lucro", 0.0)),
            }

        agora = resumo(atual)
        antes = resumo(anterior)
        diferenca_metodos = (
            0.0
            if atual.sem_lancamentos
            else _r(atual.operacional_indireto - atual.atividade(ATIVIDADE_OPERACIONAL))
        )
        totais: dict = dict(agora)
        totais.update({f"{chave}_anterior": valor for chave, valor in antes.items()})
        totais.update(
            {
                "metodo": metodo,
                "conciliado": abs(agora["diferenca_conciliacao"]) <= TOLERANCIA_CONCILIACAO,
                "tem_anterior": tem_anterior,
                "sem_lancamentos": atual.sem_lancamentos,
                "operacional_direto": (
                    None if atual.sem_lancamentos else _r(atual.atividade(ATIVIDADE_OPERACIONAL))
                ),
                "operacional_indireto": _r(atual.operacional_indireto),
                "diferenca_metodos": diferenca_metodos,
                "transferencias_internas": _r(atual.transferencias_valor),
                "transferencias_internas_qtd": atual.transferencias_qtd,
                "lancamentos_sem_caixa": atual.sem_caixa_qtd,
                "nao_atribuido": _r(atual.nao_atribuido),
                "lucro_dre": _r(atual.lucro_dre),
                "composicao_caixa": atual.composicao,
            }
        )
        return totais
