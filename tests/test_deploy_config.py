"""Consistência entre o código e os arquivos de deploy (Dockerfile, compose, nginx).

Estes testes existem porque as três falhas abaixo chegaram a produção sem que
nada acusasse: são divergências entre arquivos que ninguém executa junto.

  * o healthcheck do Dockerfile apontava para uma rota que passou a exigir
    autenticação, marcando o container como unhealthy para sempre;
  * o diretório de upload do código não era o volume montado no compose, e
    web e worker (containers distintos) nunca viam o mesmo arquivo;
  * o fallback de dependências do Dockerfile usava `>=` sem aspas, que o
    shell interpreta como redirecionamento.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE = REPO_ROOT / "docker-compose.yml"
NGINX = REPO_ROOT / "nginx.conf"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


class TestHealthcheck:
    def test_dockerfile_usa_rota_publica(self):
        """A URL do healthcheck precisa ser uma rota que dispensa autenticação."""
        texto = DOCKERFILE.read_text(encoding="utf-8")
        urls = re.findall(r"HEALTHCHECK.*?urlopen\('([^']+)'\)", texto, re.S)
        assert urls, "Dockerfile sem HEALTHCHECK reconhecível"

        from fastapi.testclient import TestClient

        from src.dashboard.app import app

        client = TestClient(app)
        for url in urls:
            caminho = "/" + url.split("/", 3)[-1]
            resposta = client.get(caminho)
            assert resposta.status_code == 200, (
                f"healthcheck bate em {caminho}, que responde "
                f"{resposta.status_code} — o container ficaria unhealthy"
            )

    def test_compose_usa_a_mesma_rota_do_dockerfile(self, compose):
        texto = DOCKERFILE.read_text(encoding="utf-8")
        do_dockerfile = re.findall(r"HEALTHCHECK.*?urlopen\('([^']+)'\)", texto, re.S)
        teste = compose["services"]["web"]["healthcheck"]["test"]
        do_compose = re.findall(r"urlopen\('([^']+)'\)", " ".join(teste))
        assert do_compose == do_dockerfile


class TestDiretorioDeUpload:
    def test_web_e_worker_compartilham_o_mesmo_diretorio(self, compose):
        web = _env(compose, "web").get("SPED_HUB_UPLOAD_DIR")
        worker = _env(compose, "worker").get("SPED_HUB_UPLOAD_DIR")
        assert web, "serviço web sem SPED_HUB_UPLOAD_DIR"
        assert web == worker, (
            "web e worker precisam apontar para o mesmo diretório: um grava o "
            "upload e o outro o processa"
        )

    @pytest.mark.parametrize("servico", ["web", "worker"])
    def test_diretorio_esta_montado_como_volume(self, compose, servico):
        alvo = _env(compose, servico)["SPED_HUB_UPLOAD_DIR"]
        montagens = [v.split(":")[1] for v in compose["services"][servico]["volumes"]]
        assert alvo in montagens, (
            f"{servico}: SPED_HUB_UPLOAD_DIR={alvo} não corresponde a nenhum volume "
            f"montado ({montagens}) — os arquivos ficariam dentro do container"
        )

    def test_dockerfile_declara_o_mesmo_diretorio(self, compose):
        texto = DOCKERFILE.read_text(encoding="utf-8")
        declarado = re.search(r"^ENV SPED_HUB_UPLOAD_DIR=(\S+)", texto, re.M)
        assert declarado, "Dockerfile não declara SPED_HUB_UPLOAD_DIR"
        assert declarado.group(1) == _env(compose, "web")["SPED_HUB_UPLOAD_DIR"]


class TestConstraintsDoPip:
    def test_constraints_estao_entre_aspas(self):
        """`pip install pacote>=1.0` sem aspas vira redirecionamento de shell.

        O shell consome o `>` como redirect, grava um arquivo chamado `=1.0` e
        entrega ao pip só o nome do pacote — a versão mínima é silenciosamente
        descartada.
        """
        for linha in DOCKERFILE.read_text(encoding="utf-8").split("\n"):
            despido = linha.strip()
            if ">=" not in despido or despido.startswith("#"):
                continue
            if despido.startswith("ENV") or "HEALTHCHECK" in despido:
                continue
            for token in re.findall(r"\S*>=\S*", despido):
                assert token.startswith('"') and token.endswith('"'), (
                    f"constraint sem aspas no Dockerfile: {token!r} " f"(linha: {despido!r})"
                )

    def test_fallback_cobre_todas_as_dependencias_principais(self):
        """O fallback do pip precisa acompanhar `[project].dependencies`."""
        import tomllib

        pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        esperadas = {
            re.split(r"[><=\[]", dep)[0].strip().lower()
            for dep in pyproject["project"]["dependencies"]
        }
        texto = DOCKERFILE.read_text(encoding="utf-8").lower()
        faltando = {pacote for pacote in esperadas if pacote not in texto}
        assert not faltando, f"fallback do Dockerfile não instala: {sorted(faltando)}"


class TestLimiteDeUpload:
    def test_nginx_nao_e_mais_restritivo_que_a_aplicacao(self, compose):
        """O nginx corta com 413 antes de a aplicação validar — não pode ser menor."""
        bruto = re.search(r"client_max_body_size\s+(\d+)([MG])", NGINX.read_text(encoding="utf-8"))
        assert bruto, "nginx.conf sem client_max_body_size"
        limite_nginx_mb = int(bruto.group(1)) * (1024 if bruto.group(2) == "G" else 1)

        declarado = _env(compose, "web").get("SPED_HUB_MAX_UPLOAD_MB", "")
        # Formato do compose: ${VAR:-default}
        default = re.search(r":-(\d+)\}", declarado)
        limite_app_mb = int(default.group(1)) if default else 200

        assert limite_nginx_mb >= limite_app_mb, (
            f"nginx aceita {limite_nginx_mb}MB mas a aplicação promete "
            f"{limite_app_mb}MB — uploads no meio do intervalo levam 413"
        )


def _env(compose: dict, servico: str) -> dict[str, str]:
    """Normaliza a lista ``KEY=value`` do compose em dicionário."""
    entradas = compose["services"][servico].get("environment", [])
    if isinstance(entradas, dict):
        return entradas
    resultado = {}
    for entrada in entradas:
        chave, _, valor = entrada.partition("=")
        resultado[chave] = valor
    return resultado
