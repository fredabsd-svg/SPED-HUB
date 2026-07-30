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

import os
import re
import shutil
import subprocess
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


class TestIntegridadeDoCompose:
    """Erros que o compose só acusa na hora de subir, no servidor."""

    def test_todo_volume_nomeado_esta_declarado(self, compose):
        """Volume usado por um serviço e ausente do topo aborta o `up` inteiro.

        `docker compose` recusa o arquivo com "service refers to undefined
        volume" — e derruba todos os serviços, não só o que errou.
        """
        declarados = set(compose.get("volumes") or {})
        for nome_servico, servico in compose["services"].items():
            for montagem in servico.get("volumes") or []:
                if not isinstance(montagem, str) or ":" not in montagem:
                    continue
                origem = montagem.split(":")[0]
                # Bind mount (./x, /x, ../x) não precisa de declaração.
                if origem.startswith((".", "/", "~")):
                    continue
                assert origem in declarados, (
                    f"o serviço {nome_servico} monta o volume nomeado "
                    f"'{origem}', que não está declarado no topo do compose"
                )


class TestNginxSobeSemCertificado:
    """`docker compose up` tem de funcionar na primeira execução.

    O `nginx.conf` apontava direto para
    `/etc/letsencrypt/live/<dominio>/fullchain.pem`. Numa instalação nova o
    arquivo não existe, o nginx recusa subir, e o container entra em laço:

        nginx: [emerg] cannot load certificate ".../fullchain.pem"
        sped-hub-nginx exited with code 1 (restarting)

    E não havia saída: o certbot do compose só roda `renew`, que não emite nada
    na primeira vez, e a emissão precisa do nginx já servindo o desafio ACME na
    porta 80. Ovo e galinha — inclusive o primeiro passo do próprio guia de
    deploy não funcionava.
    """

    @pytest.fixture
    def entrypoint(self) -> str:
        return (REPO_ROOT / "deploy" / "nginx" / "entrypoint.sh").read_text("utf-8")

    def test_nginx_conf_nao_aponta_para_certificado_fixo(self):
        """A causa direta do laço de reinício."""
        nginx = NGINX.read_text("utf-8")
        diretas = re.findall(r"^\s*ssl_certificate(?:_key)?\s+(\S+);", nginx, re.M)
        assert not diretas, (
            f"o nginx.conf aponta direto para {diretas} — se o arquivo não existir, "
            "o nginx recusa subir e o container entra em laço de reinício"
        )

    def test_certificado_vem_de_include(self):
        nginx = NGINX.read_text("utf-8")
        assert "include /etc/nginx/ssl/certificado.conf;" in nginx

    def test_entrypoint_gera_autoassinado_quando_falta(self, entrypoint):
        assert "openssl req -x509" in entrypoint
        assert "autoassinado" in entrypoint

    def test_entrypoint_avisa_que_e_autoassinado(self, entrypoint):
        """Subir com autoassinado sem avisar seria pior que falhar."""
        assert "AVISO" in entrypoint
        assert "NÃO para produção" in entrypoint

    def test_entrypoint_entrega_ao_entrypoint_oficial(self, entrypoint):
        """Sem o `exec`, os scripts de /docker-entrypoint.d/ não rodam."""
        assert "exec /docker-entrypoint.sh" in entrypoint

    def test_imagem_do_nginx_traz_openssl(self):
        """O entrypoint depende do binário; a imagem oficial não o garante.

        Assumir que está lá seria apostar numa suposição que só quebraria no
        servidor do escritório, na primeira subida.
        """
        dockerfile = (REPO_ROOT / "deploy" / "nginx" / "Dockerfile").read_text("utf-8")
        assert "openssl" in dockerfile
        assert "apk add" in dockerfile

    def test_compose_constroi_a_imagem_do_nginx(self, compose):
        nginx = compose["services"]["nginx"]
        assert "build" in nginx, "o nginx precisa da imagem própria, com openssl"
        assert nginx["build"]["context"] == "./deploy/nginx"

    def test_volume_de_ssl_e_gravavel(self, compose):
        """O autoassinado e os includes são escritos ali.

        `/etc/letsencrypt` segue somente-leitura, que é o certo — o nginx não
        tem por que escrever no diretório do certbot.
        """
        montagens = compose["services"]["nginx"]["volumes"]
        ssl = [m for m in montagens if "/etc/nginx/ssl" in m]
        assert ssl, "falta o volume de /etc/nginx/ssl"
        assert not ssl[0].endswith(":ro"), "o volume de ssl precisa ser gravável"
        letsencrypt = [m for m in montagens if "/etc/letsencrypt" in m]
        assert letsencrypt and letsencrypt[0].endswith(
            ":ro"
        ), "/etc/letsencrypt deve seguir somente-leitura para o nginx"

    def test_http2_nao_usa_diretiva_depreciada(self):
        """`listen ... http2` gerava aviso em toda subida desde o nginx 1.25."""
        nginx = NGINX.read_text("utf-8")
        assert "listen 443 ssl http2" not in nginx
        assert "http2 on;" in nginx

    def test_imagem_base_suporta_a_diretiva_http2(self):
        """`http2 on;` só existe a partir do nginx 1.25.1.

        Em versão anterior não é aviso, é erro fatal — `unknown directive
        "http2"` — e o container volta ao laço de reinício que este trabalho
        acabou de resolver. Descoberto validando o `nginx.conf` com o nginx
        1.24, que rejeita a diretiva.
        """
        dockerfile = (REPO_ROOT / "deploy" / "nginx" / "Dockerfile").read_text("utf-8")
        base = re.search(r"^FROM\s+nginx:(\d+)\.(\d+)", dockerfile, re.M)
        assert base, "não deu para ler a versão do nginx no FROM"
        maior, menor = int(base.group(1)), int(base.group(2))
        assert (maior, menor) >= (1, 25), (
            f"o nginx.conf usa `http2 on;`, que exige nginx >= 1.25, mas a "
            f"imagem base é {maior}.{menor}"
        )

    def test_nginx_nao_resolve_o_backend_na_subida(self):
        """Nome de host resolvido na subida prende o nginx a um IP morto.

        Com `upstream { server web:8000; }` — ou com o nome escrito direto no
        `proxy_pass` — o nginx consulta o DNS uma vez, ao ler a configuração, e
        guarda o IP para sempre. Recriar o `web` lhe dá um IP novo, e o nginx
        seguia mandando para o antigo: 502 em tudo, com a aplicação saudável ao
        lado, até alguém reiniciar o nginx na mão.

        E, se o nome não resolve na subida, não é 502 — é erro fatal
        (`host not found in upstream`) e o mesmo laço de reinício do
        certificado ausente.
        """
        proxy = (REPO_ROOT / "deploy" / "nginx" / "proxy.conf").read_text("utf-8")
        destino = re.search(r"^\s*proxy_pass\s+(\S+);", proxy, re.M)
        assert destino, "proxy.conf sem proxy_pass"
        assert "$" in destino.group(1), (
            f"proxy_pass aponta para {destino.group(1)} sem variável — o nginx "
            "resolve o nome na subida e recusa subir sem o backend no ar"
        )

        nginx = NGINX.read_text("utf-8")
        assert not re.search(
            r"^\s*upstream\s+\S+\s*\{", nginx, re.M
        ), "bloco `upstream` volta a resolver o nome do backend na subida"
        assert re.search(r"^\s*resolver\s+\S+", nginx, re.M), (
            "proxy_pass com variável exige `resolver` declarado, senão toda "
            "requisição falha com 'no resolver defined to resolve'"
        )

    def test_ci_sobe_o_nginx_sem_certificado_e_sem_backend(self):
        """O único lugar onde o cenário do bug roda de verdade.

        Os testes acima conferem arquivos e executam o entrypoint; nenhum sobe
        o nginx. O bug original — container em laço de reinício na instalação
        nova — só aparece com o nginx de fato subindo, e para isso é preciso
        Docker. O job `docker` do CI tem.
        """
        ci = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
        passos = ci["jobs"]["docker"]["steps"]
        script = "\n".join(str(p.get("run", "")) for p in passos)
        assert "deploy/nginx" in script, "o CI não constrói a imagem do nginx"
        assert "docker run" in script, "o CI não sobe o container do nginx"

    def test_dominio_e_configuravel(self, compose, entrypoint):
        """Sem isso, o operador tinha de editar o nginx.conf à mão."""
        assert "SPED_HUB_DOMINIO" in entrypoint
        ambiente = compose["services"]["nginx"].get("environment") or []
        assert any("SPED_HUB_DOMINIO" in str(v) for v in ambiente)

    def test_guia_explica_que_a_emissao_inicial_e_manual(self):
        """`certbot renew` não emite na primeira vez — o guia tem de dizer."""
        deploy = (REPO_ROOT / "docs" / "deploy.md").read_text("utf-8")
        assert "certonly" in deploy
        assert "renew" in deploy

    def test_todo_include_fixo_chega_na_imagem(self, compose):
        """`include` de arquivo ausente derruba o nginx igual ao certificado.

        Os includes de `/etc/nginx/ssl/` o entrypoint escreve em tempo de
        execução. Os demais têm de vir na imagem (COPY) ou montados pelo
        compose — não há terceira origem.
        """
        fontes = [NGINX, REPO_ROOT / "deploy" / "nginx" / "entrypoint.sh"]
        referidos = set()
        for fonte in fontes:
            referidos |= set(re.findall(r"include\s+(/etc/nginx/\S+?);", fonte.read_text("utf-8")))
        em_tempo_de_execucao = {c for c in referidos if c.startswith("/etc/nginx/ssl/")}
        assert em_tempo_de_execucao, "nenhum include gerado pelo entrypoint"

        dockerfile = (REPO_ROOT / "deploy" / "nginx" / "Dockerfile").read_text("utf-8")
        montagens = compose["services"]["nginx"]["volumes"]
        for caminho in referidos - em_tempo_de_execucao:
            copiado = re.search(rf"^COPY\s+\S+\s+{re.escape(caminho)}\s*$", dockerfile, re.M)
            montado = any(m.split(":")[1] == caminho for m in montagens if ":" in m)
            assert copiado or montado, (
                f"o nginx inclui {caminho}, mas nem o Dockerfile o copia nem o "
                "compose o monta — o nginx recusa subir"
            )


@pytest.mark.skipif(
    shutil.which("openssl") is None or shutil.which("sh") is None,
    reason="precisa de sh e openssl para executar o entrypoint",
)
class TestEntrypointDoNginxExecutado:
    """Roda o entrypoint de verdade, em vez de conferir o texto dele.

    Grep no script prova que uma linha existe, não que o script produz os
    arquivos que o `nginx.conf` inclui. Se o entrypoint deixar de escrever um
    include, o nginx volta a recusar subir — `include` de arquivo ausente é
    erro fatal, exatamente como o certificado ausente era.
    """

    @staticmethod
    def _executar(raiz: Path, dominio: str = "sped-hub") -> str:
        """Executa o entrypoint com as raízes absolutas redirecionadas.

        Só troca prefixos de caminho e neutraliza o `exec` final (que trocaria
        o processo pelo nginx). A lógica de decisão do certificado é a real.
        """
        script = (REPO_ROOT / "deploy" / "nginx" / "entrypoint.sh").read_text("utf-8")
        script = script.replace('"/etc/letsencrypt', f'"{raiz}/etc/letsencrypt')
        script = script.replace('"/etc/nginx', f'"{raiz}/etc/nginx')
        script = script.replace("exec /docker-entrypoint.sh", ": /docker-entrypoint.sh")
        alvo = raiz / "entrypoint.sh"
        alvo.write_text(script, encoding="utf-8")
        concluido = subprocess.run(
            ["sh", str(alvo), "nginx", "-g", "daemon off;"],
            capture_output=True,
            text=True,
            env={"PATH": os.environ.get("PATH", ""), "SPED_HUB_DOMINIO": dominio},
        )
        assert (
            concluido.returncode == 0
        ), f"o entrypoint falhou (código {concluido.returncode}): {concluido.stderr}"
        return concluido.stdout

    @staticmethod
    def _includes_de_ssl_no_nginx_conf() -> set[str]:
        nginx = NGINX.read_text("utf-8")
        return set(re.findall(r"include\s+/etc/nginx/ssl/(\S+);", nginx))

    def test_escreve_todo_include_de_ssl_que_o_nginx_conf_espera(self, tmp_path):
        """`include` de arquivo inexistente é erro fatal no nginx, como o cert."""
        esperados = self._includes_de_ssl_no_nginx_conf()
        assert esperados, "nenhum include de /etc/nginx/ssl no nginx.conf"

        self._executar(tmp_path)
        gerado = tmp_path / "etc" / "nginx" / "ssl"
        faltando = {nome for nome in esperados if not (gerado / nome).is_file()}
        assert not faltando, (
            f"o nginx.conf inclui {sorted(faltando)}, mas o entrypoint não escreve "
            "esses arquivos — o nginx recusa subir"
        )

    def test_sem_certificado_real_usa_autoassinado_valido(self, tmp_path):
        saida = self._executar(tmp_path, dominio="escritorio.exemplo")
        assert "AUTOASSINADO" in saida

        conf = (tmp_path / "etc" / "nginx" / "ssl" / "certificado.conf").read_text("utf-8")
        caminhos = dict(re.findall(r"(ssl_certificate(?:_key)?)\s+(\S+);", conf))
        assert set(caminhos) == {"ssl_certificate", "ssl_certificate_key"}

        pem = Path(caminhos["ssl_certificate"])
        assert pem.is_file(), "o certificado apontado não existe"
        assert Path(caminhos["ssl_certificate_key"]).is_file()
        texto = subprocess.run(
            ["openssl", "x509", "-in", str(pem), "-noout", "-subject"],
            capture_output=True,
            text=True,
        )
        assert texto.returncode == 0, f"certificado ilegível: {texto.stderr}"
        assert "escritorio.exemplo" in texto.stdout

    def test_sem_certificado_real_o_http_serve_a_aplicacao(self, tmp_path):
        """Redirecionar para um HTTPS autoassinado só rende aviso do navegador."""
        self._executar(tmp_path)
        http = (tmp_path / "etc" / "nginx" / "ssl" / "http-raiz.conf").read_text("utf-8")
        assert "proxy.conf" in http
        assert "301" not in http

    def test_com_certificado_real_o_http_redireciona(self, tmp_path):
        vivo = tmp_path / "etc" / "letsencrypt" / "live" / "escritorio.exemplo"
        vivo.mkdir(parents=True)
        (vivo / "fullchain.pem").write_text("cert", encoding="utf-8")
        (vivo / "privkey.pem").write_text("chave", encoding="utf-8")

        saida = self._executar(tmp_path, dominio="escritorio.exemplo")
        assert "AUTOASSINADO" not in saida

        conf = (tmp_path / "etc" / "nginx" / "ssl" / "certificado.conf").read_text("utf-8")
        assert str(vivo / "fullchain.pem") in conf
        assert str(vivo / "privkey.pem") in conf
        http = (tmp_path / "etc" / "nginx" / "ssl" / "http-raiz.conf").read_text("utf-8")
        assert "301 https://$host$request_uri" in http

    def test_reinicio_preserva_o_autoassinado(self, tmp_path):
        """Regerar a cada subida invalidaria a sessão TLS de quem já aceitou."""
        self._executar(tmp_path)
        pem = tmp_path / "etc" / "nginx" / "ssl" / "autoassinado.pem"
        antes = pem.read_bytes()

        saida = self._executar(tmp_path)
        assert "gerado" not in saida, "o entrypoint regerou o certificado no reinício"
        assert pem.read_bytes() == antes


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

    def test_caminho_do_certificado_confere_com_o_entrypoint(self, deploy):
        """O caminho real vive no entrypoint, que é quem decide o certificado.

        Antes vivia no `nginx.conf` — e apontar direto para o Let's Encrypt era
        justamente o que fazia o nginx recusar subir em instalação nova.
        """
        entrypoint = (REPO_ROOT / "deploy" / "nginx" / "entrypoint.sh").read_text("utf-8")
        assert "/etc/letsencrypt/live/" in entrypoint
        assert (
            "/etc/letsencrypt/live/" in deploy
        ), "o guia não menciona o caminho onde o nginx procura o certificado"

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


class TestQuebraDeLinhaNaoQuebraOContainer:
    """CRLF num script de shell mata o container, e o erro engana.

    O Git no Windows converte LF para CRLF ao fazer checkout — é o padrão de
    lá (`core.autocrlf=true`). Num script copiado para dentro de um container
    Linux, o shebang vira `#!/bin/sh\\r`, e o kernel procura literalmente por um
    programa chamado `/bin/sh\\r`:

        exec /sped-hub-entrypoint.sh: no such file or directory
        sped-hub-nginx exited with code 255 (restarting)

    O arquivo está lá. Quem não existe é o interpretador. Nenhum job do CI
    veria: todos rodam em Linux, onde o checkout mantém LF.
    """

    def test_repositorio_fixa_a_quebra_de_linha_dos_scripts(self):
        atributos = REPO_ROOT / ".gitattributes"
        assert atributos.is_file(), (
            "sem .gitattributes, o Git no Windows entrega os .sh com CRLF e o "
            "container do nginx entra em laço de reinício"
        )
        regras = atributos.read_text("utf-8")
        assert re.search(
            r"^\*\.sh\s+.*eol=lf", regras, re.M
        ), ".gitattributes não fixa `eol=lf` para *.sh"

    def test_nenhum_script_do_repositorio_esta_com_crlf(self):
        """Pega o arquivo que escapou, não só a regra que deveria cobri-lo."""
        com_crlf = [
            caminho.relative_to(REPO_ROOT)
            for caminho in REPO_ROOT.rglob("*.sh")
            if ".git/" not in str(caminho) and b"\r\n" in caminho.read_bytes()
        ]
        assert not com_crlf, f"scripts com CRLF: {com_crlf}"

    def test_imagem_do_nginx_normaliza_a_quebra_de_linha(self):
        """O .gitattributes só vale para checkout novo.

        Quem já clonou no Windows segue com CRLF no arquivo em disco, e o
        build sai do disco. Sem esta linha, `git pull` não conserta nada.
        """
        dockerfile = (REPO_ROOT / "deploy" / "nginx" / "Dockerfile").read_text("utf-8")
        assert re.search(
            r"sed\s+-i\s+'s/\\r\$//'", dockerfile
        ), "o Dockerfile do nginx não tira o CR do entrypoint"

    def test_ci_sobe_o_nginx_com_o_entrypoint_em_crlf(self):
        """A prova real precisa de Docker, e só o CI tem.

        Os testes acima conferem arquivo e regra. Que a imagem construída a
        partir de um checkout do Windows de fato sobe, só subindo.
        """
        ci = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
        script = "\n".join(str(p.get("run", "")) for p in ci["jobs"]["docker"]["steps"])
        assert re.search(
            r"docker run\b[^\n]*--name nginx-crlf", script
        ), "o CI não sobe o container construído a partir do checkout do Windows"
        assert "sed -i 's/$/\\r/'" in script, "o CI não estraga a quebra de linha antes"
        assert re.search(
            r"RestartCount.*nginx-crlf", script
        ), "o CI sobe o container mas não confere que ele não reinicia"

    @pytest.mark.skipif(shutil.which("sh") is None, reason="precisa de sh")
    def test_entrypoint_com_crlf_volta_a_rodar_depois_da_normalizacao(self, tmp_path):
        """Prova o efeito, não a presença da linha."""
        original = (REPO_ROOT / "deploy" / "nginx" / "entrypoint.sh").read_bytes()
        estragado = tmp_path / "entrypoint.sh"
        estragado.write_bytes(original.replace(b"\n", b"\r\n"))
        estragado.chmod(0o755)

        # O erro aponta para o script — que existe. O ausente é o
        # interpretador `/bin/sh\r`. É o mesmo engano que o Docker relata.
        assert estragado.is_file()
        with pytest.raises((FileNotFoundError, OSError)) as falha:
            subprocess.run([str(estragado)], capture_output=True, text=True)
        assert "No such file or directory" in str(falha.value)

        # A mesma normalização que o Dockerfile aplica.
        estragado.write_bytes(estragado.read_bytes().replace(b"\r\n", b"\n"))
        assert estragado.read_bytes() == original
