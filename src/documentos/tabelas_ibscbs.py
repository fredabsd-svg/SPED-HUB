"""As tabelas oficiais de CST, classificação tributária e crédito presumido.

Até aqui o sistema **listava** o CST de IBS/CBS que fugisse da tributação
integral e dizia, honestamente, que não o interpretava: a tabela não estava
verificada e uma cópia adivinhada seria pior que a ausência.

Agora está. As planilhas da SVRS (`dados/oficiais/`) são a fonte, e
`scripts/gerar_tabelas_ibscbs.py` deriva delas o JSON que este módulo lê
(§1.9). O que a tabela permite não é calcular o tributo — o valor vem
destacado no documento — e sim **conferir** a classificação antes de ela
entrar na escrituração:

  * o CST existe? o `cClassTrib` existe e casa com ele?
  * o `cClassTrib` estava vigente na data de emissão?
  * ele pode ser usado neste modelo de documento?
  * os grupos que aquele CST exige vieram no XML?

Cada uma dessas é uma rejeição da SEFAZ esperando para acontecer, e o
escritório descobre antes de transmitir em vez de depois.

O que a conferência **não** faz é dizer qual seria o código certo: isso depende
do enquadramento legal do item, e é decisão de quem escritura. `pRedIBS` e
`pRedCBS` estão na tabela e ficam disponíveis para quem quiser comparar com o
que o emitente declarou; conferi-los aqui exigiria decidir o que fazer quando
divergem, e a resposta não é a mesma para redução legal e para benefício
estadual.

**A tabela muda.** Por isso a procedência viaja com ela: `Tabelas.publicada_em`
sai em `sped-hub fiscal tabelas` e dentro de cada apontamento. Tabela velha que
se apresenta como atual é pior que tabela ausente, porque a resposta errada
parece uma resposta.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ARQUIVO = Path(__file__).with_name("tabelas_ibscbs.json")

# Alíquotas padrão do item 05 do IT 2025.002 v1.50.  Ficam aqui, e não no
# arquivo gerado, porque a fonte delas é o texto do Informe Técnico e não a
# planilha — derivar do que não é a fonte seria inventar procedência.
#
# Em 2026 a parcela municipal do IBS é **zero**: os 0,1% são todos estaduais.
# Repartir "meio a meio" pareceria razoável e mandaria dinheiro para o ente
# errado.  A repartição igual só começa em 2027.
ALIQUOTAS_PADRAO: dict[int, dict[str, float | None]] = {
    2026: {"ibs_uf": 0.1, "ibs_mun": 0.0, "cbs": 0.9},
    2027: {"ibs_uf": 0.05, "ibs_mun": 0.05, "cbs": None},
    2028: {"ibs_uf": 0.05, "ibs_mun": 0.05, "cbs": None},
}

# Os grupos que o adaptador guarda, por nome do grupo na NT.  Um CST que
# exige um grupo ausente é documento que a SEFAZ recusa.
CAMPO_DO_GRUPO = {
    "gIBSCBS": ("valor_ibs_uf", "valor_ibs_mun", "valor_cbs"),
    "gIBSCBSMono": ("valor_ibs_mono", "valor_cbs_mono"),
    "gRed": (
        "percentual_reducao_ibs_uf",
        "percentual_reducao_ibs_mun",
        "percentual_reducao_cbs",
    ),
    "gDif": ("valor_diferido_ibs_uf", "valor_diferido_ibs_mun", "valor_diferido_cbs"),
    "gTransfCred": ("valor_transf_credito_ibs", "valor_transf_credito_cbs"),
    "gAjusteCompet": ("valor_ajuste_compet_ibs", "valor_ajuste_compet_cbs"),
}


class TabelaAusente(RuntimeError):
    """O JSON gerado não está no lugar — instalação incompleta."""


@dataclass(frozen=True)
class Tabelas:
    documento: str
    origem: str
    publicada_em: str
    cst: dict[str, dict]
    class_trib: dict[str, dict]
    cred_pres: dict[str, dict]


@lru_cache(maxsize=1)
def tabelas() -> Tabelas:
    """As tabelas oficiais, lidas uma vez por processo."""
    if not ARQUIVO.is_file():
        raise TabelaAusente(
            f"{ARQUIVO.name} não encontrado; rode `python scripts/gerar_tabelas_ibscbs.py`"
        )
    dados = json.loads(ARQUIVO.read_text("utf-8"))
    publicadas = sorted(fonte["publicado_em"] for fonte in dados["fontes"])
    return Tabelas(
        documento=dados["documento"],
        origem=dados["origem"],
        # A mais **antiga** das fontes: a tabela inteira só é tão nova quanto
        # a planilha mais velha que a compõe.  Anunciar a mais nova esconderia
        # exatamente a que ficou para trás.
        publicada_em=publicadas[0],
        cst=dados["cst"],
        class_trib=dados["class_trib"],
        cred_pres=dados["cred_pres"],
    )


def aliquotas_padrao(ano: int) -> dict[str, float | None] | None:
    """As alíquotas do ano, ou `None` quando a legislação ainda não as fixou."""
    return ALIQUOTAS_PADRAO.get(ano)


def conferir_valor(campo: str, valor) -> list[str]:
    """Um valor destinado a um campo de classificação existe na tabela?

    Sem contexto de documento nenhum, de propósito: isto é chamado tanto por
    quem altera um item quanto por quem **cadastra uma regra**, e a regra não
    tem documento — ela vai valer para os que ainda nem foram importados.
    Fosse essa a diferença que impedisse a conferência, uma regra com código
    inventado seguiria entrando e classificando tudo o que casasse com ela.
    """
    texto = "" if valor is None else str(valor).strip()
    if not texto:
        return []
    tab = tabelas()
    if campo == "cst_ibscbs" and texto not in tab.cst:
        return [
            f"CST do IBS/CBS {texto!r} não está na tabela oficial "
            f"(publicada em {tab.publicada_em})"
        ]
    # Seis dígitos com os três primeiros iguais ao CST é só o formato, e
    # `999999` o cumpre — é a forma mais fácil de preencher um campo
    # obrigatório sem saber o que pôr.
    if campo == "class_trib_ibscbs" and texto not in tab.class_trib:
        return [
            f"cClassTrib {texto!r} não está na tabela oficial "
            f"(publicada em {tab.publicada_em}); "
            "`sped-hub fiscal tabelas` lista os códigos de cada CST"
        ]
    return []


def _vigente(registro: dict, data: dt.date | None) -> bool:
    if data is None:
        return True
    inicio = registro.get("inicio_vigencia")
    fim = registro.get("fim_vigencia")
    if inicio and data < dt.date.fromisoformat(inicio):
        return False
    if fim and data > dt.date.fromisoformat(fim):
        return False
    return True


def conferir(
    item,
    *,
    data_emissao: dt.date | None = None,
    modelo: str = "55",
    valor=None,
) -> list[str]:
    """Os problemas de classificação deste item, em português, ou lista vazia.

    Devolve problemas em vez de levantar: uma nota mal classificada não pode
    impedir a importação — ela já foi autorizada, está no arquivo do cliente,
    e recusá-la só faria o escritório perder o documento junto com o aviso.

    `valor` é como se lê cada campo, e existe por causa das três camadas: sem
    ele, a conferência olharia o **original** — o que a SEFAZ já autorizou e
    ninguém pode mais mudar. Quem confere a escrituração passa o leitor da
    camada efetiva, que é o que vai sair no arquivo.
    """
    ler = valor or (lambda campo: getattr(item, campo, None))
    tab = tabelas()
    cst = (ler("cst_ibscbs") or "").strip()
    codigo = (ler("class_trib_ibscbs") or "").strip()
    if not cst and not codigo:
        return []

    problemas: list[str] = []
    registro_cst = tab.cst.get(cst)
    if cst and registro_cst is None:
        problemas.append(
            f"CST de IBS/CBS {cst} não está na tabela oficial "
            f"({tab.documento}, publicada em {tab.publicada_em})"
        )

    registro = tab.class_trib.get(codigo)
    if codigo and registro is None:
        problemas.append(
            f"cClassTrib {codigo} não está na tabela oficial "
            f"({tab.documento}, publicada em {tab.publicada_em})"
        )
    elif registro is not None:
        if cst and registro["cst"] != cst:
            problemas.append(
                f"cClassTrib {codigo} pertence ao CST {registro['cst']}, "
                f"e o item declara CST {cst}"
            )
        if not _vigente(registro, data_emissao):
            problemas.append(
                f"cClassTrib {codigo} não estava vigente em {data_emissao:%d/%m/%Y} "
                f"(vigência de {registro['inicio_vigencia'] or 'sempre'} "
                f"a {registro['fim_vigencia'] or 'hoje'})"
            )
        permitido = {"55": "na_nfe", "65": "na_nfce"}.get(modelo)
        if permitido and not registro[permitido]:
            problemas.append(
                f"cClassTrib {codigo} ({registro['nome']}) não é permitido " f"no modelo {modelo}"
            )

    if registro_cst is not None:
        for grupo, exigido in registro_cst["exige"].items():
            campos = CAMPO_DO_GRUPO.get(grupo)
            if not exigido or campos is None:
                continue
            if not any(ler(campo) for campo in campos):
                problemas.append(
                    f"CST {cst} ({registro_cst['descricao']}) exige o grupo {grupo}, "
                    "e o documento não o traz"
                )
    return problemas
