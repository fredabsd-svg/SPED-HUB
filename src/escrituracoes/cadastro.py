"""O cadastro fiscal da empresa — os campos que só o contribuinte sabe.

São cinco códigos que não estão em nota nenhuma e que o validador do Fisco
**não** confere, porque não tem como saber qual é o certo: perfil de
escrituração, indicador de atividade (que tem tabela diferente em cada
obrigação), regime de apuração das contribuições e natureza jurídica. Errados,
o arquivo é aceito e o problema aparece meses depois.

Este módulo existe para que a linha de comando e a tela não tenham cada uma a
sua cópia da regra. Uma tela que importasse `src.cli_fiscal` estaria dependendo
de uma interface para saber uma regra de negócio, e a segunda cópia da tabela
diverge da primeira no primeiro ato normativo.

O que ele **não** faz é decidir por ninguém. Valor fora da tabela é recusado
com a tabela inteira na mensagem — o certo é quem escritura que sabe.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.db.models import Empresa
from src.escrituracoes.efd_contribuicoes import (
    ATIVIDADES_CONTRIBUICOES,
    NATUREZAS_PJ,
    REGIMES,
)
from src.escrituracoes.efd_icms import ATIVIDADES_ICMS, PERFIS

# Campo → (onde ele aparece no arquivo, tabela de valores válidos).
CADASTRO_FISCAL = {
    "ind_perfil": ("IND_PERFIL do 0000 da EFD ICMS/IPI", PERFIS),
    "ind_ativ": ("IND_ATIV do 0000 da EFD ICMS/IPI", ATIVIDADES_ICMS),
    "ind_ativ_contribuicoes": (
        "IND_ATIV do 0000 da EFD-Contribuições — tabela DIFERENTE da de cima",
        ATIVIDADES_CONTRIBUICOES,
    ),
    "cod_inc_trib": ("COD_INC_TRIB do 0110 — decide se há crédito", REGIMES),
    "ind_nat_pj": ("IND_NAT_PJ do 0000 — natureza jurídica", NATUREZAS_PJ),
}

# O que cada obrigação exige antes de gerar.  `ind_nat_pj` fica fora: tem
# default, e por isso não impede a geração.
EXIGIDOS = {
    "efd_icms": ("ind_perfil", "ind_ativ"),
    "efd_contribuicoes": ("cod_inc_trib", "ind_ativ_contribuicoes"),
}


class CadastroInvalido(ValueError):
    """Valor que não está na tabela oficial do campo."""


@dataclass(frozen=True)
class Campo:
    """Um campo do cadastro, como a tela precisa exibi-lo."""

    nome: str
    rotulo: str
    valor: str | None
    descricao: str
    opcoes: tuple[tuple[str, str], ...]

    @property
    def fora_da_tabela(self) -> bool:
        """Preenchido com valor que a tabela não tem.

        Acontece com cadastro gravado direto no banco, antes de existir a
        validação. Mostrar como "vazio" esconderia o problema; mostrar o valor
        com o aviso é o que leva alguém a corrigi-lo.
        """
        return self.valor is not None and self.valor not in dict(self.opcoes)


def campos(empresa: Empresa) -> list[Campo]:
    """O cadastro da empresa, campo a campo, com a tabela de cada um."""
    lista = []
    for nome, (rotulo, tabela) in CADASTRO_FISCAL.items():
        valor = getattr(empresa, nome)
        lista.append(
            Campo(
                nome=nome,
                rotulo=rotulo,
                valor=valor,
                descricao=tabela.get(valor, "—" if valor is None else "VALOR FORA DA TABELA"),
                opcoes=tuple(sorted(tabela.items())),
            )
        )
    return lista


def pendencias(empresa: Empresa) -> dict[str, list[str]]:
    """Por obrigação, os campos que ainda impedem a geração.

    Obrigação sem pendência entra com lista vazia, e não fica de fora: quem lê
    precisa ver que a EFD ICMS está pronta, não deduzir isso da ausência.
    """
    return {
        tipo: [c for c in exigidos if getattr(empresa, c) not in CADASTRO_FISCAL[c][1]]
        for tipo, exigidos in EXIGIDOS.items()
    }


def validar(campo: str, valor: str) -> None:
    """Levanta `CadastroInvalido` com a tabela inteira, ou não faz nada.

    A tabela inteira na mensagem é deliberada: quem errou o código não sabe
    qual é o certo, e uma recusa que só diz "inválido" obriga a procurar no
    Guia Prático o que o programa já tem na memória.
    """
    if campo not in CADASTRO_FISCAL:
        raise CadastroInvalido(
            f"{campo!r} não é campo do cadastro fiscal — "
            f"os campos são: {', '.join(sorted(CADASTRO_FISCAL))}"
        )
    rotulo, tabela = CADASTRO_FISCAL[campo]
    if valor not in tabela:
        opcoes = "; ".join(f"{c} = {d}" for c, d in sorted(tabela.items()))
        raise CadastroInvalido(
            f"{valor!r} não é um valor válido de {campo} ({rotulo}). Os válidos são: {opcoes}"
        )


def preencher(empresa: Empresa, informados: dict[str, str]) -> list[str]:
    """Grava os campos informados; devolve os nomes do que mudou.

    **Confere tudo antes de atribuir qualquer coisa.** Hoje quem chama descarta
    a sessão ao levantar, mas depender disso é depender de quem chama não
    commitar — e este módulo não é chamado de um lugar só. Meio cadastro
    gravado é pior que nenhum: a empresa passaria a parecer pronta para uma
    obrigação e não para a outra, sem que ninguém tivesse decidido isso.
    """
    for campo, valor in informados.items():
        validar(campo, valor)
    for campo, valor in informados.items():
        setattr(empresa, campo, valor)
    return list(informados)
