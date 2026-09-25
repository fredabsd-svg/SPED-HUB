"""Linhas de assinatura das demonstrações: contador e responsável legal.

As demonstrações contábeis saem assinadas pelo contador e por quem responde
pela empresa (Código Civil, art. 1.184, § 2º; NBC TG 26). A ECD já diz quem
são, no J930 (`Signatario`):

* **contador** — o signatário com código de qualificação 900 ou com CRC;
* **responsável legal** — o de `IND_RESP_LEGAL` = "S".

Quando a ECD é assinada com e-CNPJ, o responsável legal do J930 é a própria
empresa, e o nome do sócio não está em registro nenhum. Para esse caso
existem, em ordem de precedência: o que for informado na exportação
(`--socio` na linha de comando), o cadastro da empresa
(`Empresa.responsavel_*`) e, sem nada disso, a linha em branco com a
qualificação — para assinar à mão.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import ECD, Empresa, Signatario

COD_CONTADOR = "900"
QUALIFICACAO_PADRAO_RESPONSAVEL = "Sócio administrador"


@dataclass
class Assinatura:
    """Uma linha de assinatura: nome (pode ser vazio), cargo e documento."""

    nome: str
    cargo: str
    documento: str = ""


@dataclass
class Responsavel:
    """Quem assina pela empresa, informado fora da ECD."""

    nome: str = ""
    cpf: str = ""
    qualificacao: str = ""

    @property
    def informado(self) -> bool:
        return bool(self.nome.strip())


def formatar_cpf(cpf: str | None) -> str:
    digitos = "".join(c for c in (cpf or "") if c.isdigit())
    if len(digitos) != 11:
        return cpf or ""
    return f"{digitos[:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:]}"


def _eh_pessoa_fisica(documento: str | None) -> bool:
    return len("".join(c for c in (documento or "") if c.isalnum())) == 11


def _crc(signatario: Signatario) -> str:
    if not signatario.crc:
        return ""
    uf = f"-{signatario.uf_crc}" if signatario.uf_crc else ""
    return f"CRC{uf} {signatario.crc}"


def _documentos(*partes: str) -> str:
    return " · ".join(p for p in partes if p)


def assinaturas(
    session: Session, ecd_id: int, responsavel: Responsavel | None = None
) -> list[Assinatura]:
    """Responsável legal e contador, nessa ordem."""
    signatarios = list(
        session.execute(
            select(Signatario).where(Signatario.ecd_id == ecd_id).order_by(Signatario.id)
        ).scalars()
    )
    ecd = session.get(ECD, ecd_id)
    empresa = session.get(Empresa, ecd.empresa_id) if ecd else None

    return [
        _responsavel(signatarios, empresa, responsavel),
        _contador(signatarios),
    ]


def _responsavel(
    signatarios: list[Signatario], empresa: Empresa | None, informado: Responsavel | None
) -> Assinatura:
    if informado is not None and informado.informado:
        return Assinatura(
            nome=informado.nome.strip(),
            cargo=informado.qualificacao.strip() or QUALIFICACAO_PADRAO_RESPONSAVEL,
            documento=_documentos(f"CPF {formatar_cpf(informado.cpf)}" if informado.cpf else ""),
        )
    if empresa is not None and (empresa.responsavel_nome or "").strip():
        return Assinatura(
            nome=empresa.responsavel_nome.strip(),
            cargo=(empresa.responsavel_qualificacao or "").strip()
            or QUALIFICACAO_PADRAO_RESPONSAVEL,
            documento=(
                f"CPF {formatar_cpf(empresa.responsavel_cpf)}" if empresa.responsavel_cpf else ""
            ),
        )
    pessoas = [
        s
        for s in signatarios
        if _eh_pessoa_fisica(s.cpf_cnpj) and s.cod_assin != COD_CONTADOR and not s.crc
    ]
    legal = next((s for s in pessoas if (s.ind_resp_legal or "").upper() == "S"), None)
    escolhido = legal or (pessoas[0] if pessoas else None)
    if escolhido is not None:
        return Assinatura(
            nome=escolhido.nome,
            cargo=(escolhido.qualificacao or "").strip() or QUALIFICACAO_PADRAO_RESPONSAVEL,
            documento=f"CPF {formatar_cpf(escolhido.cpf_cnpj)}",
        )
    return Assinatura(nome="", cargo=f"{QUALIFICACAO_PADRAO_RESPONSAVEL} / Responsável legal")


def _contador(signatarios: list[Signatario]) -> Assinatura:
    contador = next(
        (s for s in signatarios if s.cod_assin == COD_CONTADOR),
        next((s for s in signatarios if s.crc), None),
    )
    if contador is None:
        return Assinatura(nome="", cargo="Contador(a)", documento="CRC")
    return Assinatura(
        nome=contador.nome,
        cargo=(contador.qualificacao or "").strip() or "Contador(a)",
        documento=_documentos(
            _crc(contador),
            (
                f"CPF {formatar_cpf(contador.cpf_cnpj)}"
                if _eh_pessoa_fisica(contador.cpf_cnpj)
                else ""
            ),
        ),
    )
