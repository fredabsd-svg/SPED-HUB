"""Hardening de produção (Fase 17, Etapa 5).

Cinco frentes, todas verificadas pelo efeito e não pela presença do código:
validação de conteúdo no upload, rate limit por IP, cabeçalhos de segurança
no nginx, saneamento de PII nos logs e formato JSON opcional.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from src.logging_config import FiltroPII, FormatadorJSON, sanitizar
from src.ratelimit import IPRateLimiter, ip_do_request
from src.settings import reset_settings_cache, with_overrides
from src.uploads import save_upload

REPO_ROOT = Path(__file__).resolve().parent.parent
NGINX = REPO_ROOT / "nginx.conf"

ECD_VALIDA = b"|0000|LECD|01012024|31122024|EMPRESA|00123456000199|SP||\n|I001|0|\n"


def _upload(conteudo: bytes, nome: str = "arquivo.txt") -> UploadFile:
    return UploadFile(file=io.BytesIO(conteudo), filename=nome)


@pytest.fixture(autouse=True)
def _uploads_isolados(tmp_path, monkeypatch):
    monkeypatch.setenv("SPED_HUB_UPLOAD_DIR", str(tmp_path / "uploads"))
    reset_settings_cache()
    yield
    reset_settings_cache()


class TestValidacaoDeConteudo:
    """A extensão não diz nada sobre o conteúdo."""

    def test_arquivo_sped_valido_passa(self):
        salvo = asyncio.run(save_upload(_upload(ECD_VALIDA), (".txt",)))
        try:
            assert salvo.size_bytes == len(ECD_VALIDA)
        finally:
            salvo.path.unlink(missing_ok=True)

    @pytest.mark.parametrize(
        "conteudo,descricao",
        [
            (b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, "PNG renomeado para .txt"),
            (b"PK\x03\x04" + b"\x00" * 100, "ZIP renomeado"),
            (b"<?php system($_GET['c']); ?>", "script PHP"),
            (b"texto qualquer sem estrutura sped", "texto solto"),
        ],
    )
    def test_conteudo_que_nao_e_sped_e_recusado(self, conteudo, descricao):
        with pytest.raises(HTTPException) as erro:
            asyncio.run(save_upload(_upload(conteudo), (".txt",)))
        assert erro.value.status_code == 400, descricao

    def test_arquivo_vazio_e_recusado(self):
        with pytest.raises(HTTPException) as erro:
            asyncio.run(save_upload(_upload(b""), (".txt",)))
        assert erro.value.status_code == 400

    def test_bom_utf8_e_linhas_em_branco_sao_tolerados(self):
        """Alguns sistemas contábeis emitem BOM ou linha em branco no topo."""
        salvo = asyncio.run(save_upload(_upload(b"\xef\xbb\xbf\r\n" + ECD_VALIDA), (".txt",)))
        try:
            assert salvo.path.exists()
        finally:
            salvo.path.unlink(missing_ok=True)

    def test_recusa_nao_deixa_arquivo_em_disco(self, tmp_path):
        with pytest.raises(HTTPException):
            asyncio.run(save_upload(_upload(b"nao e sped"), (".txt",)))
        diretorio = tmp_path / "uploads"
        assert not diretorio.exists() or list(diretorio.iterdir()) == []


class TestRateLimitPorIP:
    def test_bloqueia_ao_estourar_a_cota(self):
        limitador = IPRateLimiter()
        resultados = [limitador.verificar("1.2.3.4", "login", 3, 60)[0] for _ in range(5)]
        assert resultados == [True, True, True, False, False]

    def test_ips_distintos_nao_se_afetam(self):
        limitador = IPRateLimiter()
        for _ in range(3):
            limitador.verificar("1.1.1.1", "login", 3, 60)
        permitido, _ = limitador.verificar("2.2.2.2", "login", 3, 60)
        assert permitido is True

    def test_escopos_distintos_tem_cotas_separadas(self):
        """Uma rajada legítima na API não pode consumir a cota de login."""
        limitador = IPRateLimiter()
        for _ in range(3):
            limitador.verificar("1.2.3.4", "api", 3, 60)
        permitido, _ = limitador.verificar("1.2.3.4", "login", 3, 60)
        assert permitido is True

    def test_reset_libera(self):
        limitador = IPRateLimiter()
        for _ in range(3):
            limitador.verificar("1.2.3.4", "login", 3, 60)
        limitador.reset("1.2.3.4")
        assert limitador.verificar("1.2.3.4", "login", 3, 60)[0] is True

    def test_limpeza_descarta_janelas_velhas(self):
        """Sem expurgo, o dicionário cresce com todo IP já visto."""
        limitador = IPRateLimiter()
        limitador.verificar("9.9.9.9", "login", 3, 60)
        assert limitador.limpar_expirados(janela_maxima=0) == 1
        assert limitador._counters == {}


class TestOrigemDaRequisicao:
    """`X-Forwarded-For` é escrito pelo cliente quando não há proxy à frente."""

    class _Req:
        def __init__(self, host, headers):
            self.client = type("C", (), {"host": host})()
            self.headers = headers

    def test_sem_trust_proxy_o_cabecalho_e_ignorado(self, monkeypatch):
        monkeypatch.delenv("SPED_HUB_TRUST_PROXY", raising=False)
        reset_settings_cache()
        req = self._Req("10.0.0.1", {"X-Forwarded-For": "1.2.3.4"})
        assert ip_do_request(req) == "10.0.0.1", (
            "confiar no cabeçalho sem proxy transforma o limite por IP em "
            "decoração: basta trocar o header a cada tentativa"
        )

    def test_com_trust_proxy_o_cabecalho_vale(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_TRUST_PROXY", "true")
        reset_settings_cache()
        req = self._Req("10.0.0.1", {"X-Forwarded-For": "1.2.3.4, 10.0.0.9"})
        assert ip_do_request(req) == "1.2.3.4"
        reset_settings_cache()

    def test_trust_proxy_e_desligado_por_padrao(self):
        assert with_overrides().trust_proxy is False


class TestSaneamentoDePII:
    @pytest.mark.parametrize(
        "entrada,nao_pode_conter",
        [
            ("Login falho para joao.silva@empresa.com.br", "joao.silva"),
            ("Empresa 12.345.678/0001-95", "12.345.678"),
            ("CNPJ 12345678000195 sem pontos", "12345678000195"),
            ("CPF 123.456.789-01", "123.456"),
            ("token a" * 40, None),
            ("chave spd_deadbeefcafe1234", "deadbeefcafe1234"),
        ],
    )
    def test_identificadores_nao_sobrevivem(self, entrada, nao_pode_conter):
        saida = sanitizar(entrada)
        if nao_pode_conter:
            assert nao_pode_conter not in saida

    def test_cauda_do_documento_e_preservada(self):
        """Precisa dar para casar a linha com o registro certo numa investigação."""
        assert sanitizar("Empresa 12.345.678/0001-95") == "Empresa **.***.***/0001-95"
        assert sanitizar("CPF 123.456.789-01") == "CPF ***.***.789-01"

    def test_token_de_sessao_some(self):
        token = "a3f2b8c9d0e1f2a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b"
        assert token not in sanitizar(f"sessao {token}")

    def test_texto_sem_pii_passa_intacto(self):
        mensagem = "Importação concluída: 23 contas, 9 lançamentos"
        assert sanitizar(mensagem) == mensagem

    def test_filtro_aplica_no_registro_de_log(self):
        registro = logging.LogRecord(
            "t", logging.WARNING, __file__, 1, "email %s", ("ana@x.com.br",), None
        )
        FiltroPII().filter(registro)
        assert "ana@x.com.br" not in registro.getMessage()
        assert "a***@x.com.br" in registro.getMessage()


class TestLogEstruturado:
    def test_saida_e_json_valido(self):
        registro = logging.LogRecord("app", logging.INFO, __file__, 1, "ok", (), None)
        evento = json.loads(FormatadorJSON().format(registro))
        assert evento["nivel"] == "INFO"
        assert evento["logger"] == "app"
        assert evento["mensagem"] == "ok"
        assert "ts" in evento

    def test_json_tambem_sanitiza(self):
        registro = logging.LogRecord(
            "app", logging.ERROR, __file__, 1, "falha para bob@x.com", (), None
        )
        evento = json.loads(FormatadorJSON().format(registro))
        assert "bob@x.com" not in evento["mensagem"]

    def test_excecao_entra_no_evento(self):
        try:
            raise ValueError("erro com email ana@x.com")
        except ValueError:
            import sys

            registro = logging.LogRecord(
                "app", logging.ERROR, __file__, 1, "falhou", (), sys.exc_info()
            )
        evento = json.loads(FormatadorJSON().format(registro))
        assert "excecao" in evento
        assert "ana@x.com" not in evento["excecao"]

    def test_json_desligado_por_padrao(self):
        assert with_overrides().log_json is False


class TestCabecalhosDeSeguranca:
    @pytest.fixture
    def nginx(self) -> str:
        return NGINX.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "cabecalho",
        [
            "Strict-Transport-Security",
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Referrer-Policy",
            "Content-Security-Policy",
            "Permissions-Policy",
        ],
    )
    def test_cabecalho_presente(self, nginx, cabecalho):
        assert re.search(rf'add_header\s+{cabecalho}\s+"', nginx), f"falta {cabecalho}"

    def test_csp_bloqueia_o_essencial(self, nginx):
        csp = re.search(r'add_header Content-Security-Policy "([^"]+)"', nginx).group(1)
        assert "frame-ancestors 'none'" in csp, "clickjacking"
        assert "object-src 'none'" in csp, "plugins legados"
        assert "form-action 'self'" in csp, "envio de formulário para fora"
        assert "base-uri 'self'" in csp, "sequestro de URL base"
        assert "default-src 'self'" in csp

    def test_csp_permite_o_que_a_aplicacao_usa(self, nginx):
        """Uma CSP que quebra a aplicação seria removida no primeiro incidente."""
        csp = re.search(r'add_header Content-Security-Policy "([^"]+)"', nginx).group(1)
        origens = set()
        for html in (REPO_ROOT / "src" / "dashboard" / "templates").rglob("*.html"):
            origens.update(re.findall(r'src="(https://[^/"]+)', html.read_text(encoding="utf-8")))
        for origem in origens:
            assert origem in csp, f"{origem} é carregada pelos templates mas não está na CSP"

    def test_headers_sempre_aplicados(self, nginx):
        """Sem `always`, o nginx omite o cabeçalho em respostas de erro."""
        for linha in nginx.split("\n"):
            if linha.strip().startswith("add_header") and "Cache-Control" not in linha:
                assert linha.rstrip().endswith("always;"), linha.strip()
