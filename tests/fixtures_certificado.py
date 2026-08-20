"""Um PFX de teste, gerado na hora.

Não há certificado real neste repositório e não deve haver: PFX no Git é o
primeiro item da lista do que não fazer. O certificado abaixo é autoassinado,
serve só para exercitar leitura e cofre, e não assina nada perante ninguém.
"""

from __future__ import annotations

import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# `pkcs12` é submódulo, não atributo: sem este import ele só existe se
# algum outro módulo já o tiver importado no mesmo processo.  Foi o que
# escondeu o defeito aqui e o mostrou no CI, com outra ordem de coleta.
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

SENHA_PADRAO = "senha-do-pfx"
CNPJ_DO_TITULAR = "12345678000195"


def pfx_de_teste(
    *,
    senha: str = SENHA_PADRAO,
    cnpj: str = CNPJ_DO_TITULAR,
    nome: str = "COMERCIO EXEMPLO LTDA",
    dias_de_validade: int = 365,
) -> bytes:
    """Um PKCS#12 autoassinado, com o CNPJ no titular como a ICP-Brasil faz.

    `dias_de_validade` negativo produz certificado já vencido — é como se
    testa o aviso sem esperar um ano.
    """
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    identidade = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ICP-Brasil"),
            x509.NameAttribute(NameOID.COMMON_NAME, f"{nome}:{cnpj}"),
        ]
    )
    agora = datetime.datetime.now(datetime.UTC)
    certificado = (
        x509.CertificateBuilder()
        .subject_name(identidade)
        .issuer_name(identidade)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - datetime.timedelta(days=400))
        .not_valid_after(agora + datetime.timedelta(days=dias_de_validade))
        .sign(chave, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        b"teste",
        chave,
        certificado,
        None,
        serialization.BestAvailableEncryption(senha.encode()),
    )
