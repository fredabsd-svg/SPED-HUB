"""Classificação das contas de uma ECD — um lugar só para balanço, DRE, DFC e índices.

Cada demonstração precisa perguntar a mesma coisa de cada conta: é caixa? é
patrimônio líquido? é circulante? é custo ou despesa financeira?  Antes,
cada relatório respondia do seu jeito, e com uma ECD real as respostas
erravam juntas:

* o PL com natureza 02 (passivo) — comum, e aceito pelo PVA — ia para o
  passivo, e o balanço mostrava "Total do Patrimônio Líquido 0,00";
* a DRE só olhava o nome da própria conta: COMPRAS DE MERCADORIAS, IOF e
  TARIFAS BANCÁRIAS caíam todas em "Despesas Operacionais", e o "(-)
  DEVOLUÇÃO DE MERCADORIAS" do CMV virava dedução da receita;
* a DFC tratava a PECLD pendurada em APLICAÇÕES como caixa.

A ordem das fontes é a mesma para todas as perguntas:

1. **o plano referencial (I051)**, onde ele é inequívoco — só os prefixos do
   referencial da RFB que não admitem leitura dupla (receita bruta,
   deduções, custos; caixa e equivalentes);
2. **o nome da conta e dos superiores**, do mais próximo ao topo: a primeira
   regra que casa decide.  O nome da própria conta vem antes do grupo, mas
   cada regra é específica o bastante para não confundir "SERVIÇOS
   PRESTADOS POR PJ" (despesa) com receita de serviços;
3. **a natureza** (`COD_NAT`), como último recurso.

O mapeamento configurado pela empresa (`Mapeamento`) continua valendo por
cima disto, em cada relatório.
"""

from __future__ import annotations

import re
from collections import defaultdict
from functools import lru_cache
from typing import TYPE_CHECKING

from sqlalchemy import select
from unidecode import unidecode

from src.db.models import ContaReferencial, PlanoConta

if TYPE_CHECKING:
    from src.filters.engine import FilterEngine


def normalizar(nome: str | None) -> str:
    """Maiúsculas, sem acento, hífen e barra como espaço, espaços simples."""
    texto = unidecode(nome or "").upper().replace("-", " ")
    return re.sub(r"\s+", " ", texto).strip()


def _re(padrao: str) -> re.Pattern:
    return re.compile(padrao)


# ── Grupos do balanço ──────────────────────────────────────────────────────

# Grupo do balanço: ac, rlp, investimentos, imobilizado, intangivel, anc
# (não circulante sem subgrupo identificado), pc, pnc, pl.
GRUPOS_DO_ATIVO = ("ac", "rlp", "investimentos", "imobilizado", "intangivel", "anc")
GRUPOS_DO_PASSIVO = ("pc", "pnc")

# "PASSIVO E PATRIMÔNIO LÍQUIDO" é a raiz do lado direito, não o PL.
_PL = _re(r"^(?!.*\bPASSIVO\b).*PATRIMONIO LIQUIDO")
_PNC = _re(r"PASSIVO NAO CIRCULANTE|EXIGIVEL A LONGO PRAZO|PASSIVO A LONGO PRAZO")
_PC = _re(r"PASSIVO CIRCULANTE")
_ANC = _re(r"ATIVO NAO CIRCULANTE|ATIVO PERMANENTE")
_AC = _re(r"ATIVO CIRCULANTE")
_SUBGRUPOS_ANC = [
    ("rlp", _re(r"REALIZAVEL A LONGO PRAZO|ATIVO A LONGO PRAZO")),
    ("imobilizado", _re(r"\bIMOBILIZADO\b|DEPRECIAC(AO|OES) ACUMULADA")),
    ("intangivel", _re(r"\bINTANGIVE(L|IS)\b|\bDIFERIDO\b")),
    (
        "investimentos",
        _re(
            r"^INVESTIMENTOS?$|INVESTIMENTOS? PERMANENTES?|PARTICIPAC(AO|OES) "
            r"(SOCIETARIAS?|EM (COLIGADAS|CONTROLADAS|OUTRAS))|PROPRIEDADES? PARA INVESTIMENTO"
        ),
    ),
]

# Prefixos do referencial da RFB para os grupos (L100A): usados só quando o
# nome do plano não diz o grupo.
_GRUPO_POR_REFERENCIAL = [
    ("1.01", "ac"),
    ("1.02.01", "rlp"),
    ("1.02.02", "investimentos"),
    ("1.02.03", "imobilizado"),
    ("1.02.04", "intangivel"),
    ("1.02", "anc"),
    ("2.01", "pc"),
    ("2.02", "pnc"),
    ("2.03", "pl"),
]

# ── Caixa e equivalentes ───────────────────────────────────────────────────

# Referencial da RFB: 1.01.01 é "Caixa e equivalentes de caixa".
REFERENCIAL_CAIXA = "1.01.01"

_CAIXA = _re(
    r"\bCAIXA\b|\bBANCOS?\b|DISPONIBILIDADES?|\bDISPONIVE(L|IS)\b|EQUIVALENTES? DE CAIXA"
    r"|NUMERARIO|LIQUIDEZ IMEDIATA|CONTAS? MOVIMENTO|FUNDO FIXO"
)
# O que o nome da própria conta diz que não é caixa, mesmo pendurada no
# disponível (o caso da PECLD sob APLICAÇÕES FINANCEIRAS).
_NAO_CAIXA = _re(
    r"CLIENTE|DUPLICATA|\bRECEBER\b|RECEBIVE|PERDAS|PECLD|PROVISA|ESTOQUE|MERCADORIA"
    r"|TRIBUT|IMPOSTO|RECUPERAR|COMPENSAR|ADIANTAMENTO|EMPRESTIMO|MUTUO|JUDICIA|BLOQUEAD"
    r"|VINCULAD|CAUC|GARANTIA"
)
# Contas de passagem entre contas da própria empresa: o PIX de um banco para
# outro lançado contra "TRANSFERÊNCIAS ENTRE CONTAS". Fazem parte da gestão
# do caixa (CPC 03, item 9) — o dinheiro está em trânsito, não saiu.
_TRANSITO = _re(
    r"TRANSFERENCIAS? ENTRE (AS )?CONTAS|NUMERARIO EM TRANSITO|DINHEIRO EM TRANSITO"
    r"|TRANSFERENCIAS? BANCARIAS|TRANSITORIA (DE |ENTRE )?(CAIXA|BANCOS?|CONTAS)"
)

# ── Categorias da DRE ──────────────────────────────────────────────────────

CATEGORIAS_DRE = (
    "receita_bruta",
    "deducoes",
    "custos",
    "despesas_operacionais",
    "outras_operacionais",
    "receitas_financeiras",
    "despesas_financeiras",
    "irpj_csll",
)

# Referencial da RFB (L300A e equivalentes): só os prefixos sem leitura dupla.
_DRE_POR_REFERENCIAL = [
    ("3.01.01.01.01", "receita_bruta"),
    ("3.01.01.01.02", "deducoes"),
    ("3.01.01.03", "custos"),
]

_NAO_E_RECEITA = _re(r"DESPESA|CUSTO|PAGO|PAGAMENTO|\bPOR PJ\b|\bPOR PF\b|TOMADOS?")

# Ordem importa: numa mesma conta, a primeira que casa decide.
_REGRAS_DRE: list[tuple[str, re.Pattern]] = [
    (
        "irpj_csll",
        _re(
            r"\bIRPJ\b|\bCSLL\b|IMPOSTO DE RENDA|CONTRIBUICAO SOCIAL S(OBRE|/) ?(O )?LUCRO"
            r"|PROVISAO (PARA |P/ ?)?(O )?(IR|CSLL|IMPOSTO DE RENDA)\b|TRIBUTOS SOBRE O LUCRO"
        ),
    ),
    (
        "despesas_financeiras",
        _re(
            r"DESPESAS? FINANCEIRAS?|ENCARGOS FINANCEIROS|JUROS PASSIVOS|JUROS PAGOS"
            r"|JUROS (E ENCARGOS )?S(OBRE|/) ?(EMPRESTIMOS|FINANCIAMENTOS)|ENCARGOS E JUROS"
            r"|JUROS DE MORA|JUROS E MULTAS|\bIOF\b|TARIFAS? BANCARIAS?|DESPESAS? BANCARIAS?"
            r"|DESCONTOS? CONCEDIDOS?|VARIAC(AO|OES) (MONETARIAS?|CAMBIA(L|IS)) PASSIVAS?"
        ),
    ),
    (
        "receitas_financeiras",
        _re(
            r"RECEITAS? FINANCEIRAS?|JUROS (ATIVOS|RECEBIDOS|AUFERIDOS)"
            r"|RENDIMENTOS? (DE |SOBRE |S/ ?)?(APLICAC|FINANCEIR|POUPANCA)"
            r"|DESCONTOS? OBTIDOS?|VARIAC(AO|OES) (MONETARIAS?|CAMBIA(L|IS)) ATIVAS?"
        ),
    ),
    (
        "outras_operacionais",
        _re(
            r"OUTRAS RECEITAS|RECEITAS? NAO OPERACIONA|OUTRAS DESPESAS|DESPESAS? NAO OPERACIONA"
            r"|OUTROS RESULTADOS|(GANHOS?|PERDAS?|RESULTADO) (NA|DE|COM) (VENDA|ALIENACAO|BAIXA)"
            r"|RECUPERAC(AO|OES) DE DESPESAS|RECEITAS? EVENTUA"
            r"|CUSTO (DE|DO|DOS) (ATIVOS?|IMOBILIZADO|BENS DO ATIVO)"
        ),
    ),
    (
        "deducoes",
        _re(
            r"DEDUC(AO|OES)|DEVOLUC(AO|OES) DE VENDAS?|VENDAS? (CANCELADAS|DEVOLVIDAS)"
            r"|CANCELAMENTOS? DE VENDAS?|ABATIMENTOS?|DESCONTOS? INCONDICIONA"
            r"|(IMPOSTOS?|TRIBUTOS?)( E CONTRIBUICOES)?( INCIDENTES)? S(OBRE|/) ?(AS )?"
            r"(VENDAS|FATURAMENTO|RECEITAS?|SERVICOS)|SIMPLES NACIONAL"
            r"|\b(ICMS|PIS|COFINS|ISS|IPI) S(OBRE|/) ?(AS )?(VENDAS|FATURAMENTO|RECEITA|SERVICOS)"
        ),
    ),
    (
        "custos",
        _re(
            r"\bCMV\b|\bCPV\b|\bCSP\b|CUSTOS? D(AS|OS|E) (MERCADORIAS?|PRODUTOS?|SERVICOS?|VENDAS?)"
            r"|CUSTOS? (DE |DA )?PRODUCAO|COMPRAS? DE MERCADORIAS?|CUSTOS? (OPERACIONAIS|DIRETOS"
            r"|INDIRETOS)|MATERIA PRIMA CONSUMIDA|^(\(\s*\)\s*)?CUSTOS?\b(?! E DESPESAS)"
        ),
    ),
    (
        "receita_bruta",
        _re(
            r"RECEITAS? BRUTAS?|RECEITAS? (OPERACIONA(L|IS)|DE VENDAS?|COM VENDAS|DE SERVICOS"
            r"|DA ATIVIDADE|DE PRESTACAO)|RECEITAS? (DE )?VENDA|VENDAS? (DE|DO|DA) (MERCADORIAS?"
            r"|PRODUTOS?|SERVICOS?)|VENDAS? (A VISTA|A PRAZO|NO MERCADO)|FATURAMENTO"
            r"|PRESTAC(AO|OES) DE SERVICOS?|^RECEITAS?$|^VENDAS?$"
        ),
    ),
    ("despesas_operacionais", _re(r"\bDESPESAS?\b(?! E CUSTOS)|\bGASTOS?\b")),
]


def categoria_dre_pelo_nome(nome: str) -> str | None:
    """Categoria da DRE que um nome (já normalizado) indica, ou `None`."""
    return _dre_pelo_nome(normalizar(nome))


def _dre_pelo_nome(nome: str) -> str | None:
    for categoria, padrao in _REGRAS_DRE:
        if categoria == "receita_bruta" and _NAO_E_RECEITA.search(nome):
            continue
        if padrao.search(nome):
            return categoria
    return None


# ── Categorias da DFC ──────────────────────────────────────────────────────

ATIVIDADE_OPERACIONAL = "operacional"
ATIVIDADE_INVESTIMENTO = "investimento"
ATIVIDADE_FINANCIAMENTO = "financiamento"

# Categoria da DFC → atividade.  "caixa" não é categoria de fluxo: é o que a
# DFC explica.
ATIVIDADE_DA_CATEGORIA = {
    "clientes": ATIVIDADE_OPERACIONAL,
    "estoques": ATIVIDADE_OPERACIONAL,
    "fornecedores": ATIVIDADE_OPERACIONAL,
    "empregados": ATIVIDADE_OPERACIONAL,
    "tributos": ATIVIDADE_OPERACIONAL,
    "juros_recebidos": ATIVIDADE_OPERACIONAL,
    "juros_pagos": ATIVIDADE_OPERACIONAL,
    "ir_csll": ATIVIDADE_OPERACIONAL,
    "outros_op": ATIVIDADE_OPERACIONAL,
    "imobilizado": ATIVIDADE_INVESTIMENTO,
    "intangivel": ATIVIDADE_INVESTIMENTO,
    "investimentos": ATIVIDADE_INVESTIMENTO,
    "emprestimos_concedidos": ATIVIDADE_INVESTIMENTO,
    "aplicacoes": ATIVIDADE_INVESTIMENTO,
    "outros_inv": ATIVIDADE_INVESTIMENTO,
    "emprestimos": ATIVIDADE_FINANCIAMENTO,
    "capital": ATIVIDADE_FINANCIAMENTO,
    "distribuicao": ATIVIDADE_FINANCIAMENTO,
    "outros_fin": ATIVIDADE_FINANCIAMENTO,
}

# As categorias do mapeamento "dfc" antigo (por variação de saldo) continuam
# aceitas: cada uma vira a categoria de fluxo equivalente.
CATEGORIA_DO_MAPEAMENTO_ANTIGO = {
    "depreciacao": "imobilizado",
    "var_contas_receber": "clientes",
    "var_estoques": "estoques",
    "var_fornecedores": "fornecedores",
    "var_obrig_fiscais": "tributos",
    "outros_operacionais": "outros_op",
    "aquisicao_imobilizado": "imobilizado",
    "venda_imobilizado": "imobilizado",
    "aquisicao_intangivel": "intangivel",
    "outros_investimentos": "outros_inv",
    "aumento_capital": "capital",
    "emprestimos": "emprestimos",
    "distribuicao_lucros": "distribuicao",
}

_EMPREGADOS = _re(
    r"SALARIO|ORDENADO|FERIAS|13 ?O? SALARIO|DECIMO TERCEIRO|PRO ?LABORE|\bINSS\b|\bFGTS\b"
    r"|RESCIS|OBRIGAC(AO|OES) (TRABALHISTAS?|SOCIA(L|IS))|ENCARGOS SOCIAIS|FOLHA"
    r"|CONTRIBUIC(AO|OES) SINDICA|VALE TRANSPORTE|VALE ALIMENTACAO|VALE REFEICAO"
    r"|\bPESSOAL\b|FUNCIONARIOS|EMPREGADOS|BENEFICIOS"
)
_IR_CSLL = _re(
    r"\bIRPJ\b|\bCSLL\b|IMPOSTO DE RENDA (A PAGAR|A RECOLHER|PESSOA JURIDICA)"
    r"|CONTRIBUICAO SOCIAL (A PAGAR|A RECOLHER|S(OBRE|/) ?(O )?LUCRO)"
)
_TRIBUTOS = _re(
    r"TRIBUT|IMPOSTO|\bICMS\b|\bPIS\b|COFINS|\bIPI\b|\bISS\b|\bISSQN\b|\bIRRF\b|\bCSRF\b"
    r"|SIMPLES|PARCELAMENTO|\bREFIS\b|\bDAS\b|\bDIFAL\b|\bTAXAS?\b|\bIPTU\b|\bIPVA\b"
)
_JUROS_A_PAGAR = _re(r"\bJUROS\b")
_EMPRESTIMOS = _re(
    r"EMPRESTIMO|FINANCIAMENTO|\bMUTUO|DEBENTURE|CONSORCIO|LEASING|ARRENDAMENTO"
    r"|CONTA GARANTIDA|CHEQUE ESPECIAL|(DUPLICATAS|TITULOS) DESCONT"
    r"|DESCONTO DE (DUPLICATAS|TITULOS)|CAPITAL DE GIRO|\bBNDES\b|\bFINAME\b"
    r"|ANTECIPAC(AO|OES) DE RECEBIVEIS"
)
_DISTRIBUICAO = _re(r"DIVIDENDO|LUCROS? A (DISTRIBUIR|PAGAR)|JUROS SOBRE (O )?CAPITAL")
_CLIENTES = _re(
    r"CLIENTE|DUPLICATAS A RECEBER|CONTAS A RECEBER|CARTO(ES|AO) (DE CREDITO )?A RECEBER"
    r"|RECEBIVE|PECLD|PERDAS ESTIMADAS|CREDITOS DE LIQUIDACAO|DEVEDORES DUVIDOSOS"
)
_ESTOQUES = _re(
    r"ESTOQUE|MERCADORIAS? PARA REVENDA|MATERIA PRIMA|PRODUTOS? (ACABADOS|EM ELABORACAO)"
    r"|MATERIAIS? DE (CONSUMO|EMBALAGEM)|IMPORTAC(AO|OES) EM ANDAMENTO"
)
_FORNECEDORES = _re(
    r"FORNECEDOR|ADIANTAMENTOS? A FORNECEDORES|DESPESAS? ANTECIPADAS|SEGUROS A APROPRIAR"
    r"|CARTAO DE CREDITO|CONTAS A PAGAR|ALUGUE(L|IS) A PAGAR"
)
_APLICACOES = _re(r"APLICAC(AO|OES)|TITULOS E VALORES MOBILIARIOS|\bCDB\b|\bLCI\b|\bLCA\b")
_ADIANTAMENTO_DE_CLIENTES = _re(r"ADIANTAMENTOS? DE CLIENTES")
_ADIANTAMENTO_A_EMPREGADOS = _re(r"ADIANTAMENTOS? (A|DE) (EMPREGADOS|FUNCIONARIOS|SALARIO|FERIAS)")
_EMPRESTIMO_CONCEDIDO = _re(r"EMPRESTIMO|\bMUTUO|PARTES RELACIONADAS|SOCIOS")
_CAPITAL = _re(r"CAPITAL|\bAFAC\b|FUTURO AUMENTO|ACOES EM TESOURARIA|QUOTAS EM TESOURARIA")
_DEPRECIACAO = _re(r"DEPRECIA|AMORTIZA|EXAUST")
_VENDA_DE_ATIVO = _re(
    r"(GANHOS?|PERDAS?|RESULTADO|RECEITAS?) (NA|DE|COM) (VENDA|ALIENACAO|BAIXA)"
    r"|VENDA DE (IMOBILIZADO|ATIVOS?|BENS)|ALIENACAO DE|CUSTO (DE|DO|DOS) (ATIVOS?|IMOBILIZADO"
    r"|BENS DO ATIVO)"
)


class Classificador:
    """Responde, para cada conta de uma ECD, grupo do balanço, caixa, DRE e DFC.

    Usa a hierarquia do `FilterEngine` — a que já segue o balanço publicado
    onde o I050 diverge dele (`FilterEngine.correcoes_de_superior`).
    """

    def __init__(self, engine: FilterEngine):
        self.engine = engine
        self.plano = engine.plano()
        self.hierarquia = engine.hierarquia()
        self._referenciais: dict[str, list[str]] | None = None
        self._cache: dict[tuple[str, str], object] = {}

    # ── Dados ──

    def referenciais(self, cod: str) -> list[str]:
        """Códigos do referencial (I051) da conta, sem repetição, em ordem."""
        if self._referenciais is None:
            por_conta: dict[str, list[str]] = defaultdict(list)
            for cod_cta, cod_ref in self.engine.session.execute(
                select(PlanoConta.cod_cta, ContaReferencial.cod_cta_ref)
                .join(ContaReferencial, ContaReferencial.plano_conta_id == PlanoConta.id)
                .where(PlanoConta.ecd_id == self.engine.ecd_id)
                .order_by(ContaReferencial.id)
            ):
                if cod_ref and cod_ref not in por_conta[cod_cta]:
                    por_conta[cod_cta].append(cod_ref.strip())
            self._referenciais = dict(por_conta)
        return self._referenciais.get(cod, [])

    def nomes(self, cod: str) -> list[str]:
        """Nome normalizado da conta e dos superiores, do mais próximo ao topo."""
        return [
            _nome_normalizado(self.plano[c].nome_cta)
            for c in (cod, *self.hierarquia.ancestrais(cod))
            if c in self.plano
        ]

    def natureza(self, cod: str) -> str:
        return self.plano[cod].cod_nat if cod in self.plano else ""

    def _memo(self, pergunta: str, cod: str, calcular):
        chave = (pergunta, cod)
        if chave not in self._cache:
            self._cache[chave] = calcular(cod)
        return self._cache[chave]

    # ── Balanço ──

    def grupo_balanco(self, cod: str) -> str | None:
        """ac, rlp, investimentos, imobilizado, intangivel, anc, pc, pnc ou pl."""
        return self._memo("grupo", cod, self._grupo_balanco)

    def _grupo_balanco(self, cod: str) -> str | None:
        natureza = self.natureza(cod)
        if natureza == "03":
            return "pl"
        if natureza not in ("01", "02"):
            return None
        nomes = self.nomes(cod)
        if natureza == "02":
            for nome in nomes:
                if _PL.search(nome):
                    return "pl"
                if _PNC.search(nome):
                    return "pnc"
                if _PC.search(nome):
                    return "pc"
        else:
            subgrupo = None
            for nome in nomes:
                if subgrupo is None:
                    subgrupo = next((g for g, p in _SUBGRUPOS_ANC if p.search(nome)), None)
                if _ANC.search(nome):
                    return subgrupo or "anc"
                if _AC.search(nome):
                    return "ac"
            if subgrupo is not None:
                return subgrupo
        for referencial in self.referenciais(cod):
            for prefixo, grupo in _GRUPO_POR_REFERENCIAL:
                if referencial.startswith(prefixo):
                    return grupo
        return None

    def eh_pl(self, cod: str) -> bool:
        """Conta do patrimônio líquido, com natureza 03 ou dentro do grupo PL."""
        return self.grupo_balanco(cod) == "pl"

    def secao_balanco(self, cod: str) -> str | None:
        """ativo, passivo ou pl — a seção do balanço em que a conta aparece."""
        natureza = self.natureza(cod)
        if natureza == "01":
            return "ativo"
        if natureza in ("02", "03"):
            return "pl" if self.eh_pl(cod) else "passivo"
        return None

    # ── Caixa ──

    def eh_transito(self, cod: str) -> bool:
        """Conta de passagem entre contas de caixa da própria empresa."""
        return self.natureza(cod) in ("01", "02") and bool(
            _TRANSITO.search(_nome_normalizado(self.plano[cod].nome_cta))
        )

    def eh_caixa(self, cod: str) -> bool:
        """Caixa e equivalentes de caixa (CPC 03, itens 6 a 9), trânsito incluído."""
        return self._memo("caixa", cod, self._eh_caixa)

    def _eh_caixa(self, cod: str) -> bool:
        if self.eh_transito(cod):
            return True
        if self.natureza(cod) != "01":
            return False
        proprio = _nome_normalizado(self.plano[cod].nome_cta)
        if _NAO_CAIXA.search(proprio) and not _CAIXA.search(proprio):
            return False
        referenciais = self.referenciais(cod)
        if referenciais:
            return any(r.startswith(REFERENCIAL_CAIXA) for r in referenciais)
        for nome in self.nomes(cod):
            if _NAO_CAIXA.search(nome) and not _CAIXA.search(nome):
                return False
            if _CAIXA.search(nome):
                return True
            if _AC.search(nome) or _ANC.search(nome):
                return False
        return False

    # ── DRE ──

    def categoria_dre(self, cod: str) -> str | None:
        """Categoria da DRE de uma conta de resultado; `None` fora do resultado."""
        return self._memo("dre", cod, self._categoria_dre)

    def _categoria_dre(self, cod: str) -> str | None:
        if self.natureza(cod) != "04":
            return None
        for referencial in self.referenciais(cod):
            for prefixo, categoria in _DRE_POR_REFERENCIAL:
                if referencial.startswith(prefixo):
                    return categoria
        for nome in self.nomes(cod):
            categoria = _dre_pelo_nome(nome)
            if categoria is not None:
                return categoria
        return "despesas_operacionais"

    # ── DFC ──

    def categoria_dfc(self, cod: str) -> str:
        """Categoria de fluxo de caixa: "caixa" ou uma de `ATIVIDADE_DA_CATEGORIA`."""
        return self._memo("dfc", cod, self._categoria_dfc)

    def _primeira(self, cod: str, regras: list[tuple[str, re.Pattern]]) -> str | None:
        for nome in self.nomes(cod):
            for categoria, padrao in regras:
                if padrao.search(nome):
                    return categoria
        return None

    def _categoria_dfc(self, cod: str) -> str:
        if self.eh_caixa(cod):
            return "caixa"
        natureza = self.natureza(cod)
        if natureza == "04":
            return self._dfc_do_resultado(cod)
        if natureza not in ("01", "02", "03"):
            return "outros_op"
        grupo = self.grupo_balanco(cod)
        if grupo == "pl":
            return self._primeira(cod, [("capital", _CAPITAL)]) or "distribuicao"
        if natureza == "02":
            return (
                self._primeira(
                    cod,
                    [
                        ("distribuicao", _DISTRIBUICAO),
                        ("juros_pagos", _JUROS_A_PAGAR),
                        ("emprestimos", _EMPRESTIMOS),
                        ("empregados", _EMPREGADOS),
                        ("ir_csll", _IR_CSLL),
                        ("tributos", _TRIBUTOS),
                        ("clientes", _ADIANTAMENTO_DE_CLIENTES),
                        ("fornecedores", _FORNECEDORES),
                    ],
                )
                or "outros_op"
            )
        # Ativo
        if grupo in ("imobilizado", "intangivel", "investimentos"):
            return grupo
        regras = [
            ("clientes", _CLIENTES),
            ("estoques", _ESTOQUES),
            ("empregados", _ADIANTAMENTO_A_EMPREGADOS),
            ("fornecedores", _FORNECEDORES),
            ("ir_csll", _IR_CSLL),
            ("tributos", _TRIBUTOS),
            ("emprestimos_concedidos", _EMPRESTIMO_CONCEDIDO),
            ("aplicacoes", _APLICACOES),
        ]
        categoria = self._primeira(cod, regras)
        if categoria is not None:
            return categoria
        return "outros_inv" if grupo == "anc" else "outros_op"

    def _dfc_do_resultado(self, cod: str) -> str:
        categoria = self.categoria_dre(cod)
        nomes = self.nomes(cod)
        if categoria == "receita_bruta":
            return "clientes"
        if categoria in ("deducoes",):
            return "tributos"
        if categoria == "custos":
            return "fornecedores"
        if categoria == "receitas_financeiras":
            return "juros_recebidos"
        if categoria == "despesas_financeiras":
            return "juros_pagos"
        if categoria == "irpj_csll":
            return "ir_csll"
        if categoria == "outras_operacionais":
            return "imobilizado" if _VENDA_DE_ATIVO.search(nomes[0]) else "outros_op"
        proprio = nomes[0] if nomes else ""
        if _EMPREGADOS.search(proprio):
            return "empregados"
        if _TRIBUTOS.search(proprio):
            return "tributos"
        return "fornecedores"

    def ajuste_indireto(self, cod: str) -> str:
        """Linha de ajuste do método indireto para item sem efeito caixa numa conta de resultado."""
        proprio = self.nomes(cod)[0] if cod in self.plano else ""
        categoria = self.categoria_dre(cod)
        if _DEPRECIACAO.search(proprio):
            return "adj_depreciacao"
        if categoria in ("receitas_financeiras", "despesas_financeiras"):
            return "adj_financeiros"
        if _VENDA_DE_ATIVO.search(proprio):
            return "adj_baixa"
        return "adj_outros"

    def linha_capital_de_giro(self, cod: str) -> str:
        """Linha de variação do capital de giro (método indireto) de uma conta patrimonial."""
        categoria = self.categoria_dfc(cod)
        if self.natureza(cod) == "01":
            return {
                "clientes": "wc_clientes",
                "estoques": "wc_estoques",
                "tributos": "wc_tributos_ativo",
                "ir_csll": "wc_tributos_ativo",
            }.get(categoria, "wc_outros_ativo")
        return {
            "fornecedores": "wc_fornecedores",
            "empregados": "wc_trabalhistas",
            "tributos": "wc_tributos_passivo",
            "ir_csll": "wc_tributos_passivo",
        }.get(categoria, "wc_outros_passivo")


@lru_cache(maxsize=65536)
def _nome_normalizado(nome: str) -> str:
    return normalizar(nome)
