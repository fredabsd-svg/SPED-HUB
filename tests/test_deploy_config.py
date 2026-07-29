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


class TestMigracaoNoCompose:
    """Migração precisa acontecer antes de web e worker subirem.

    Ver docs/migrations.md: o advisory lock protege contra a corrida, mas o
    serviço dedicado é o que dá um log legível quando a migração falha.
    """

    def test_existe_servico_de_migracao(self, compose):
        assert "migrate" in compose["services"]
        comando = compose["services"]["migrate"]["command"]
        assert "migrar" in comando and "aplicar" in comando

    @pytest.mark.parametrize("servico", ["web", "worker"])
    def test_web_e_worker_esperam_a_migracao(self, compose, servico):
        deps = compose["services"][servico].get("depends_on", {})
        assert "migrate" in deps, f"{servico} pode subir antes do schema existir"
        assert deps["migrate"]["condition"] == "service_completed_successfully"

    def test_migracao_usa_o_mesmo_banco_dos_servicos(self, compose):
        assert _env(compose, "migrate")["DATABASE_URL"] == _env(compose, "web")["DATABASE_URL"]

    def test_url_do_alembic_vem_das_settings(self):
        """`alembic.ini` não pode ter URL própria — seria um segundo lugar de config."""
        import configparser

        parser = configparser.ConfigParser()
        parser.read(REPO_ROOT / "alembic.ini")
        assert not parser.get("alembic", "sqlalchemy.url", fallback="").strip()
        assert "database_reference" in (REPO_ROOT / "alembic" / "env.py").read_text(
            encoding="utf-8"
        )


class TestBuildDaImagemNoCI:
    """O build da imagem precisa ser exercitado ANTES do merge.

    Enquanto o job `docker` era `if: github.ref == 'refs/heads/main'`, ele só
    rodava depois do merge: nenhum PR podia ser barrado por quebrar o
    Dockerfile — só o main podia ficar vermelho depois do fato. Foi assim que
    a renomeação do pacote gdk-pixbuf no Debian trixie entrou sem aviso.
    """

    @pytest.fixture
    def ci(self) -> dict:
        return yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))

    def test_ci_roda_em_pull_request(self, ci):
        gatilhos = ci[True] if True in ci else ci["on"]
        assert "pull_request" in gatilhos, "a suíte deixou de rodar em PR"

    def test_build_da_imagem_nao_e_restrito_ao_main(self, ci):
        job = ci["jobs"]["docker"]
        assert "if" not in job, (
            f"job `docker` condicionado por {job.get('if')!r}: com isso o build "
            "volta a ser verificado só depois do merge, e quebrar o Dockerfile "
            "deixa de barrar o PR"
        )

    def test_build_depende_da_suite(self, ci):
        """Construir imagem de código que não passa nos testes é desperdício."""
        assert set(ci["jobs"]["docker"]["needs"]) >= {"test", "postgres"}


class TestPacotesDoDockerfile:
    def test_gdk_pixbuf_usa_o_nome_do_debian_atual(self):
        """`libgdk-pixbuf2.0-0` não tem candidato a instalação no trixie.

        O `python:3.11-slim` migrou de bookworm para trixie e o nome do
        pacote ganhou um hífen. Com o nome antigo, `apt-get install` sai com
        100 e a imagem não constrói de forma alguma.
        """
        # Só as linhas de comando: o nome antigo aparece de propósito no
        # comentário que explica a troca.
        instrucoes = "\n".join(
            linha
            for linha in DOCKERFILE.read_text("utf-8").splitlines()
            if not linha.lstrip().startswith("#")
        )
        assert "libgdk-pixbuf-2.0-0" in instrucoes, "o Dockerfile não instala mais o gdk-pixbuf"
        assert not re.search(r"libgdk-pixbuf2\.0-0(?![\w.-])", instrucoes), (
            "`libgdk-pixbuf2.0-0` (sem hífen) não existe no Debian trixie, "
            "base do python:3.11-slim — o build falha com exit code 100"
        )


class TestWorkflowDeRelease:
    """`release.yml` publica a imagem e para aí — deploy é decisão separada."""

    @pytest.fixture
    def release(self) -> dict:
        return yaml.safe_load(
            (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
        )

    def test_dispara_por_tag_e_manualmente(self, release):
        gatilhos = release[True] if True in release else release["on"]
        assert "push" in gatilhos and gatilhos["push"]["tags"] == ["v*"]
        assert "workflow_dispatch" in gatilhos

    def test_nao_contem_passo_de_deploy(self, release):
        """A garantia central: publicar imagem não pode colocar em produção.

        Deploy depende de janela, backup e migração de schema — nada disso
        pode acontecer porque alguém criou uma tag.
        """
        bruto = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8").lower()
        proibidos = [
            "ssh",
            "scp",
            "rsync",
            "kubectl",
            "helm",
            "docker compose up",
            "docker stack deploy",
            "appleboy/ssh-action",
            "terraform apply",
        ]
        encontrados = [p for p in proibidos if p in bruto]
        assert not encontrados, f"release.yml executa deploy: {encontrados}"

    def test_publica_multi_arquitetura(self, release):
        passos = release["jobs"]["publicar"]["steps"]
        build = next(p for p in passos if "build-push-action" in str(p.get("uses", "")))
        plataformas = build["with"]["platforms"]
        assert "linux/amd64" in plataformas and "linux/arm64" in plataformas

    def test_so_publica_depois_dos_testes(self, release):
        assert set(release["jobs"]["publicar"]["needs"]) == {"verificar-versao", "testes"}
        assert release["jobs"]["testes"]["uses"].endswith("ci.yml")

    def test_ci_pode_ser_chamado_por_outro_workflow(self):
        ci = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
        gatilhos = ci[True] if True in ci else ci["on"]
        assert "workflow_call" in gatilhos, "release.yml não conseguiria reaproveitar a suíte"

    def test_permissoes_minimas(self, release):
        """Só o necessário para publicar no GHCR."""
        assert release["permissions"] == {"contents": "read", "packages": "write"}

    def test_confere_tag_contra_a_versao_do_codigo(self, release):
        """Imagem publicada como v0.16.0 tem de relatar 0.16.0 em /health."""
        passos = release["jobs"]["verificar-versao"]["steps"]
        script = " ".join(str(p.get("run", "")) for p in passos)
        assert "APP_VERSION" in script and "exit 1" in script


class TestDocumentacaoDeDeploy:
    @pytest.fixture
    def deploy(self) -> str:
        return (REPO_ROOT / "docs" / "deploy.md").read_text("utf-8")

    @pytest.mark.parametrize(
        "assunto", ["DNS", "SSL", "PostgreSQL", "Backup", "Rollback", "Observabilidade"]
    )
    def test_checklist_cobre_o_assunto(self, deploy, assunto):
        assert assunto.lower() in deploy.lower(), f"checklist não menciona {assunto}"

    def test_backup_vem_antes_da_migracao(self, deploy):
        """Migração aplicada não volta sozinha — por isso a ordem importa."""
        atualizar = deploy[deploy.index("## 7. Atualizar versão") :]
        assert atualizar.index("BACKUP") < atualizar.index("migrate")

    def test_comandos_citados_existem_de_fato(self, deploy, compose):
        assert "docker compose run --rm migrate" in deploy
        assert "migrate" in compose["services"]
        assert "migrar adotar" in deploy

    def test_caminho_do_certificado_confere_com_o_nginx(self, deploy):
        nginx = NGINX.read_text("utf-8")
        caminho = re.search(r"ssl_certificate\s+(\S+)/fullchain\.pem", nginx).group(1)
        assert caminho in deploy, f"o checklist não menciona {caminho}, usado pelo nginx"

    def test_nao_cita_servico_inexistente_no_compose(self, deploy, compose):
        """Comando de checklist que aponta para serviço que não existe falha na hora errada."""
        import re as _re

        servicos = set(compose["services"])
        citados = set(_re.findall(r"docker compose (?:exec|run) (?:--rm |-T )*(\w+)", deploy))
        # `run --rm` pode ser seguido de flags; filtra o que claramente é serviço.
        desconhecidos = {s for s in citados if s not in servicos and s not in {"rm", "T"}}
        assert (
            not desconhecidos
        ), f"docs/deploy.md usa serviços inexistentes: {sorted(desconhecidos)}"
