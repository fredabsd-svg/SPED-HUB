"""O cofre do certificado A1 — o PFX e a senha, cifrados à parte.

Consultar webservice fiscal em nome do cliente exige guardar o certificado
digital dele.  É o dado mais sensível que este sistema chega a tocar: com o
PFX e a senha, quem os tiver assina documento fiscal como a empresa.

Três decisões que valem estar escritas:

**A chave mestra fica fora do banco** (`SPED_HUB_CERTIFICATE_MASTER_KEY`).
Cifrar o PFX com uma chave guardada ao lado dele protege contra quase nada —
quem lê o banco lê os dois.  Sem a variável no ambiente o cofre **recusa**
operar, em vez de cair numa chave padrão: cifra com chave previsível dá a
aparência de proteção sem a proteção, e aparência de proteção é pior do que
nada, porque ninguém vai atrás.

**PFX e senha vão em envelopes separados.**  São dois segredos com vidas
diferentes — a senha pode ser trocada sem o arquivo mudar — e um vazamento
parcial não entrega o par.

**O envelope carrega a versão da chave.**  Rotação de chave mestra sem isso é
migração de tudo de uma vez, com janela de indisponibilidade; com isso, o
cofre lê o que foi cifrado antes e escreve com a chave nova.

O que este módulo **não** faz: não decifra por conta própria em lugar nenhum
do fluxo normal.  `abrir` existe para o momento da operação que precisa do
certificado, e quem chama tem de descartar o resultado.
"""

from __future__ import annotations

import base64
import binascii
import datetime
import hashlib
import os
import re
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding

from src.settings import get_settings

# AES-256: 32 bytes de chave.  O GCM autentica, então adulterar o texto
# cifrado no banco não passa despercebido — decifrar levanta erro.
TAMANHO_DA_CHAVE = 32
# 96 bits é o nonce recomendado para GCM; sorteado por operação, nunca reusado.
TAMANHO_DO_NONCE = 12
VERSAO_ATUAL = 1

# Quando avisar que o certificado está para vencer.  Sessenta dias é o começo
# porque renovar A1 leva agendamento e comparecimento, não um clique.
AVISOS_DE_VENCIMENTO = (60, 30, 15, 7)


class CofreIndisponivel(RuntimeError):
    """Falta a chave mestra, ou ela não serve."""


class EnvelopeInvalido(ValueError):
    """O envelope não abre: chave errada, versão desconhecida ou adulteração."""


class CertificadoIlegivel(ValueError):
    """O PFX não abre com a senha dada, ou não é um PFX."""


@dataclass(frozen=True)
class Envelope:
    """Texto cifrado e o que é preciso para reabri-lo — menos a chave."""

    versao: int
    nonce: bytes
    cifrado: bytes

    def serializar(self) -> str:
        """`v1.<nonce>.<cifrado>`, em base64 — cabe numa coluna de texto."""
        return ".".join(
            (
                f"v{self.versao}",
                base64.b64encode(self.nonce).decode(),
                base64.b64encode(self.cifrado).decode(),
            )
        )

    @classmethod
    def desserializar(cls, bruto: str) -> Envelope:
        partes = (bruto or "").split(".")
        if len(partes) != 3 or not partes[0].startswith("v"):
            raise EnvelopeInvalido("envelope fora do formato v<n>.<nonce>.<cifrado>")
        try:
            versao = int(partes[0][1:])
            return cls(versao, base64.b64decode(partes[1]), base64.b64decode(partes[2]))
        except (ValueError, binascii.Error) as erro:
            raise EnvelopeInvalido(f"envelope ilegível: {erro}") from erro


def _chave_mestra() -> bytes:
    bruto = (get_settings().certificate_master_key or "").strip()
    if not bruto:
        raise CofreIndisponivel(
            "SPED_HUB_CERTIFICATE_MASTER_KEY não está definida. O cofre não "
            "opera sem ela — gere uma com `openssl rand -base64 32` e ponha "
            "no ambiente, nunca no banco nem no repositório."
        )
    try:
        chave = base64.b64decode(bruto, validate=True)
    except ValueError as erro:
        # `binascii.Error` é subclasse de `ValueError`, mas b64decode também
        # levanta `ValueError` puro para entrada não-ASCII — uma chave com
        # acento, por exemplo. Pegar só a primeira deixava escapar um erro
        # cru onde deveria sair a orientação de como gerar a chave.
        raise CofreIndisponivel(f"a chave mestra não é base64 válido: {erro}") from erro
    if len(chave) != TAMANHO_DA_CHAVE:
        raise CofreIndisponivel(
            f"a chave mestra tem {len(chave)} bytes; AES-256 exige {TAMANHO_DA_CHAVE}"
        )
    return chave


def guardar(segredo: bytes | str) -> Envelope:
    """Cifra um segredo com a chave mestra atual."""
    if isinstance(segredo, str):
        segredo = segredo.encode("utf-8")
    nonce = _sortear_nonce()
    cifrado = AESGCM(_chave_mestra()).encrypt(nonce, segredo, None)
    return Envelope(VERSAO_ATUAL, nonce, cifrado)


def _sortear_nonce() -> bytes:
    return os.urandom(TAMANHO_DO_NONCE)


def abrir(envelope: Envelope | str) -> bytes:
    """Decifra — só para o momento da operação que precisa do segredo."""
    if isinstance(envelope, str):
        envelope = Envelope.desserializar(envelope)
    if envelope.versao != VERSAO_ATUAL:
        raise EnvelopeInvalido(
            f"envelope da versão {envelope.versao}; este cofre lê a {VERSAO_ATUAL}"
        )
    try:
        return AESGCM(_chave_mestra()).decrypt(envelope.nonce, envelope.cifrado, None)
    except Exception as erro:  # InvalidTag e afins
        raise EnvelopeInvalido(
            "o envelope não abre com a chave mestra atual — chave trocada "
            "sem rotação, ou conteúdo adulterado"
        ) from erro


@dataclass(frozen=True)
class DadosDoCertificado:
    """O que se pode mostrar de um certificado sem expor nada.

    Nada aqui permite assinar coisa nenhuma: são os campos públicos do
    certificado, os mesmos que qualquer navegador exibe.
    """

    titular: str
    emissor: str
    valido_de: datetime.date
    valido_ate: datetime.date
    fingerprint: str
    cnpj: str | None

    @property
    def vencido(self) -> bool:
        return self.valido_ate < datetime.date.today()

    def dias_para_vencer(self, hoje: datetime.date | None = None) -> int:
        return (self.valido_ate - (hoje or datetime.date.today())).days

    def aviso_de_vencimento(self, hoje: datetime.date | None = None) -> str | None:
        """A frase a mostrar, ou `None` quando ainda há folga.

        Certificado vencido é aviso diferente de certificado vencendo: um
        pede providência para semana que vem, o outro já parou a automação.
        """
        dias = self.dias_para_vencer(hoje)
        if dias < 0:
            return f"o certificado de {self.titular} venceu há {-dias} dia(s)"
        for limite in sorted(AVISOS_DE_VENCIMENTO):
            if dias <= limite:
                return (
                    f"o certificado de {self.titular} vence em {dias} dia(s) "
                    f"({self.valido_ate:%d/%m/%Y}) — renovar A1 leva agendamento"
                )
        return None


# O CNPJ vem do titular no formato `NOME:CNPJ` (ICP-Brasil), ou de um campo
# separado.  Catorze posições alfanuméricas, porque desde 31/07/2026 elas
# podem ter letra — ver `src/cnpj.py`.
_CNPJ_NO_TITULAR = re.compile(r"[:\s]([0-9A-Z]{12}\d{2})\b")


def ler(pfx: bytes, senha: str) -> DadosDoCertificado:
    """Os metadados públicos do PFX, sem guardar nada.

    Levanta `CertificadoIlegivel` quando a senha não abre — que é a diferença
    entre "o arquivo está corrompido" e "digitaram a senha errada", e quem
    cadastra precisa saber qual dos dois.
    """
    from cryptography.hazmat.primitives.serialization import pkcs12

    try:
        _, certificado, _ = pkcs12.load_key_and_certificates(
            pfx, senha.encode("utf-8") if senha else None
        )
    except Exception as erro:
        raise CertificadoIlegivel("o arquivo não abre como PFX/P12 com a senha informada") from erro
    if certificado is None:
        raise CertificadoIlegivel("o PFX não traz certificado, só chave")

    titular = certificado.subject.rfc4514_string()
    return DadosDoCertificado(
        titular=titular,
        emissor=certificado.issuer.rfc4514_string(),
        valido_de=_data_de(certificado, "not_valid_before"),
        valido_ate=_data_de(certificado, "not_valid_after"),
        fingerprint=hashlib.sha256(certificado.public_bytes(Encoding.DER)).hexdigest(),
        cnpj=_cnpj_do_titular(titular),
    )


def _data_de(certificado, campo: str) -> datetime.date:
    """A data de validade, com ou sem o sufixo `_utc`.

    A partir da versão 42 a biblioteca passou a expor `not_valid_before_utc`
    e a avisar de depreciação no nome sem sufixo; antes disso só existe o
    nome sem sufixo.  O `pyproject` não fixa a versão, então o módulo aceita
    as duas em vez de escolher uma e quebrar na outra.
    """
    valor = getattr(certificado, f"{campo}_utc", None) or getattr(certificado, campo)
    return valor.date()


def _cnpj_do_titular(titular: str) -> str | None:
    achado = _CNPJ_NO_TITULAR.search(titular.upper())
    return achado.group(1) if achado else None
