"""O cofre do certificado A1.

É o dado mais sensível que este sistema toca: com o PFX e a senha, quem os
tiver assina documento fiscal como a empresa. Os testes aqui olham menos para
"funciona" e mais para "o que acontece quando alguém erra ou mexe" — chave
ausente, chave trocada, envelope adulterado, senha errada.
"""

from __future__ import annotations

import base64
import datetime

import pytest

from src import certificados
from src.certificados import (
    CertificadoIlegivel,
    CofreIndisponivel,
    Envelope,
    EnvelopeInvalido,
)
from src.settings import reset_settings_cache
from tests.fixtures_certificado import CNPJ_DO_TITULAR, SENHA_PADRAO, pfx_de_teste

OUTRA_CHAVE = base64.b64encode(b"\x02" * 32).decode()


@pytest.fixture
def cofre_aberto(monkeypatch):
    """Uma chave mestra válida no ambiente, como em produção."""
    monkeypatch.setenv("SPED_HUB_CERTIFICATE_MASTER_KEY", base64.b64encode(b"\x01" * 32).decode())
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture(scope="module")
def pfx() -> bytes:
    """Gerado uma vez por módulo: RSA de 2048 bits não é barato."""
    return pfx_de_teste()


class TestOCofreSemChave:
    """Sem a chave mestra o cofre para, em vez de inventar uma."""

    def test_guardar_sem_chave_recusa(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_CERTIFICATE_MASTER_KEY", "")
        reset_settings_cache()

        with pytest.raises(CofreIndisponivel, match="MASTER_KEY"):
            certificados.guardar(b"segredo")

    def test_a_mensagem_diz_como_gerar_a_chave(self, monkeypatch):
        """Erro que não diz o que fazer vira chamado de suporte."""
        monkeypatch.setenv("SPED_HUB_CERTIFICATE_MASTER_KEY", "")
        reset_settings_cache()

        with pytest.raises(CofreIndisponivel, match="openssl rand"):
            certificados.guardar(b"segredo")

    def test_chave_que_nao_e_base64_recusa(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_CERTIFICATE_MASTER_KEY", "isto não é base64!!")
        reset_settings_cache()

        with pytest.raises(CofreIndisponivel):
            certificados.guardar(b"segredo")

    def test_chave_do_tamanho_errado_recusa(self, monkeypatch):
        """AES-256 exige 32 bytes; 16 abriria o cofre com metade da força."""
        monkeypatch.setenv("SPED_HUB_CERTIFICATE_MASTER_KEY", base64.b64encode(b"x" * 16).decode())
        reset_settings_cache()

        with pytest.raises(CofreIndisponivel, match="32"):
            certificados.guardar(b"segredo")


class TestOCofre:
    def test_guarda_e_abre(self, cofre_aberto):
        envelope = certificados.guardar("senha-do-pfx")

        assert certificados.abrir(envelope) == b"senha-do-pfx"

    def test_o_segredo_nao_aparece_no_envelope(self, cofre_aberto):
        serializado = certificados.guardar("senha-do-pfx").serializar()

        assert "senha-do-pfx" not in serializado
        assert base64.b64encode(b"senha-do-pfx").decode() not in serializado

    def test_guardar_duas_vezes_da_envelopes_diferentes(self, cofre_aberto):
        """Nonce sorteado por operação: dois iguais no GCM quebram a cifra."""
        um = certificados.guardar("mesmo segredo").serializar()
        outro = certificados.guardar("mesmo segredo").serializar()

        assert um != outro
        assert certificados.abrir(um) == certificados.abrir(outro)

    def test_o_envelope_atravessa_a_serializacao(self, cofre_aberto):
        bruto = "binário \x00\xff\x01".encode()
        serializado = certificados.guardar(bruto).serializar()

        assert certificados.abrir(serializado) == bruto

    def test_outra_chave_mestra_nao_abre(self, cofre_aberto, monkeypatch):
        serializado = certificados.guardar("segredo").serializar()
        monkeypatch.setenv("SPED_HUB_CERTIFICATE_MASTER_KEY", OUTRA_CHAVE)
        reset_settings_cache()

        with pytest.raises(EnvelopeInvalido):
            certificados.abrir(serializado)

    def test_envelope_adulterado_nao_abre(self, cofre_aberto):
        """O GCM autentica: mexer no texto cifrado no banco é detectado."""
        envelope = certificados.guardar("segredo")
        adulterado = Envelope(
            envelope.versao,
            envelope.nonce,
            envelope.cifrado[:-1] + bytes([envelope.cifrado[-1] ^ 1]),
        )

        with pytest.raises(EnvelopeInvalido):
            certificados.abrir(adulterado)

    def test_envelope_de_versao_desconhecida_nao_abre(self, cofre_aberto):
        envelope = certificados.guardar("segredo")
        do_futuro = Envelope(99, envelope.nonce, envelope.cifrado)

        with pytest.raises(EnvelopeInvalido, match="99"):
            certificados.abrir(do_futuro)

    def test_envelope_fora_do_formato_nao_abre(self, cofre_aberto):
        with pytest.raises(EnvelopeInvalido):
            certificados.abrir("isto não é um envelope")


class TestLerOPfx:
    def test_os_metadados_publicos(self, pfx):
        dados = certificados.ler(pfx, SENHA_PADRAO)

        assert "COMERCIO EXEMPLO LTDA" in dados.titular
        assert "ICP-Brasil" in dados.emissor
        assert len(dados.fingerprint) == 64

    def test_o_cnpj_sai_do_titular(self, pfx):
        """A ICP-Brasil põe o CNPJ no CN, depois do nome e de dois-pontos."""
        assert certificados.ler(pfx, SENHA_PADRAO).cnpj == CNPJ_DO_TITULAR

    def test_o_cnpj_alfanumerico_tambem(self):
        """Catorze posições com letra — o formato de 31/07/2026 em diante."""
        alfa = pfx_de_teste(cnpj="12ABC34501DE35")

        assert certificados.ler(alfa, SENHA_PADRAO).cnpj == "12ABC34501DE35"

    def test_senha_errada_diz_que_e_senha(self, pfx):
        """Distinguir "arquivo corrompido" de "senha errada" poupa horas."""
        with pytest.raises(CertificadoIlegivel, match="senha"):
            certificados.ler(pfx, "senha-que-nao-e")

    def test_arquivo_que_nao_e_pfx(self):
        with pytest.raises(CertificadoIlegivel):
            certificados.ler(b"isto e um txt qualquer", SENHA_PADRAO)

    def test_ler_nao_guarda_nada(self, pfx, monkeypatch):
        """`ler` é só leitura: não pode exigir cofre nem chave mestra."""
        monkeypatch.setenv("SPED_HUB_CERTIFICATE_MASTER_KEY", "")
        reset_settings_cache()

        assert certificados.ler(pfx, SENHA_PADRAO).cnpj == CNPJ_DO_TITULAR


class TestAvisoDeVencimento:
    @staticmethod
    def _dados(dias: int) -> certificados.DadosDoCertificado:
        hoje = datetime.date(2026, 8, 20)
        return certificados.DadosDoCertificado(
            titular="COMERCIO EXEMPLO LTDA",
            emissor="ICP-Brasil",
            valido_de=hoje - datetime.timedelta(days=400),
            valido_ate=hoje + datetime.timedelta(days=dias),
            fingerprint="a" * 64,
            cnpj=CNPJ_DO_TITULAR,
        )

    HOJE = datetime.date(2026, 8, 20)

    @pytest.mark.parametrize("dias", [90, 61])
    def test_com_folga_nao_avisa(self, dias):
        assert self._dados(dias).aviso_de_vencimento(self.HOJE) is None

    @pytest.mark.parametrize("dias", [60, 30, 15, 7, 1, 0])
    def test_perto_do_vencimento_avisa(self, dias):
        aviso = self._dados(dias).aviso_de_vencimento(self.HOJE)

        assert aviso and "vence em" in aviso

    def test_vencido_avisa_diferente(self):
        """Vencido é outro problema: a automação já parou."""
        aviso = self._dados(-3).aviso_de_vencimento(self.HOJE)

        assert aviso and "venceu há 3" in aviso

    def test_vencido_e_reconhecido(self):
        assert self._dados(-1).vencido is True
        assert self._dados(1).vencido is False
