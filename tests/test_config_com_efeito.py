"""§2.2 — cada variável documentada muda comportamento observável.

Não basta a variável chegar ao `Settings`: sete delas chegavam e nenhum
componente lia o campo, então quem as configurava acreditava ter configurado
algo.  `tests/test_regras_projeto.py::TestConfiguracao` cobra a estrutura (há
leitor?); aqui se cobra o efeito (o comportamento muda?).

Cada teste segue a mesma forma: mede com o default, muda a variável, mede de
novo e exige valores diferentes.  Um teste que só afirmasse o valor
configurado passaria mesmo com o default igual por coincidência.
"""

from __future__ import annotations

import pytest

from src.settings import get_settings, reset_settings_cache

_VARIAVEIS = [
    "EMAIL_ENABLED",
    "SMTP_HOST",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SPED_HUB_ALLOWED_HOSTS",
    "SPED_HUB_METRICS_WINDOW_MINUTES",
    "SPED_HUB_MONITORING_RETENTION_HOURS",
    "SPED_HUB_RATE_LIMIT_DEFAULT",
    "SPED_HUB_RATE_LIMIT_WINDOW",
]


@pytest.fixture(autouse=True)
def _ambiente_limpo(monkeypatch):
    """Sem isto, o valor exportado na máquina de quem roda decide o resultado."""
    for chave in _VARIAVEIS:
        monkeypatch.delenv(chave, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL_ENABLED — chave-geral do envio
# ═══════════════════════════════════════════════════════════════════════════


class TestEmailEnabled:
    """Com `false`, nada sai — mesmo havendo credencial SMTP completa.

    O risco concreto: homologação apontada para o SMTP de produção mandando
    e-mail de verdade para o cliente do escritório.
    """

    @staticmethod
    def _com_credencial(monkeypatch, habilitado: str):
        monkeypatch.setenv("SMTP_HOST", "smtp.exemplo.com")
        monkeypatch.setenv("SMTP_USER", "usuario")
        monkeypatch.setenv("SMTP_PASSWORD", "segredo")
        monkeypatch.setenv("EMAIL_ENABLED", habilitado)
        reset_settings_cache()
        from src.email_service import EmailService

        return EmailService()

    def test_desligado_forca_modo_log_apesar_da_credencial(self, monkeypatch):
        assert self._com_credencial(monkeypatch, "false")._modo == "log"

    def test_ligado_com_credencial_usa_smtp(self, monkeypatch):
        assert self._com_credencial(monkeypatch, "true")._modo == "smtp"

    def test_desligado_nao_chama_smtp(self, monkeypatch):
        """O modo é só a intenção; o que importa é o socket não ser aberto."""
        servico = self._com_credencial(monkeypatch, "false")

        def falhar(_mensagem):
            raise AssertionError("EMAIL_ENABLED=false e o envio SMTP foi tentado")

        monkeypatch.setattr(servico, "_smtp_send", falhar)
        mensagem = servico.enviar(
            para="cliente@exemplo.com",
            assunto="Fechamento",
            corpo="Corpo",
            async_mode=False,
        )
        assert mensagem.status == "enviado"  # registrado em log, não entregue

    def test_modo_explicito_vence_a_flag(self, monkeypatch):
        """A flag governa o modo automático, não veta quem pediu modo por código.

        Os próprios testes desta suíte constroem `EmailService(modo="smtp")`
        com um SMTP falso; se a flag vetasse isso, seria impossível exercitar
        o caminho de envio.
        """
        monkeypatch.setenv("EMAIL_ENABLED", "false")
        reset_settings_cache()
        from src.email_service import EmailService

        assert EmailService(modo="smtp")._modo == "smtp"


# ═══════════════════════════════════════════════════════════════════════════
# SPED_HUB_MONITORING_RETENTION_HOURS — teto da janela de métricas
# ═══════════════════════════════════════════════════════════════════════════


class TestRetencaoDeMetricas:
    def test_default_e_24h(self):
        from src.monitoring import MetricsCollector

        assert MetricsCollector().retention_hours == 24

    def test_variavel_muda_a_retencao(self, monkeypatch):
        from src.monitoring import MetricsCollector

        monkeypatch.setenv("SPED_HUB_MONITORING_RETENTION_HOURS", "6")
        reset_settings_cache()
        assert MetricsCollector().retention_hours == 6

    def test_retencao_limita_a_janela_pedida(self, monkeypatch):
        """Pedir 10 h de dados com 6 h de retenção devolve 6 h, não 10."""
        from src.monitoring import MetricsCollector

        monkeypatch.setenv("SPED_HUB_MONITORING_RETENTION_HOURS", "6")
        reset_settings_cache()
        coletor = MetricsCollector()
        assert coletor.snapshot(minutes=600)["window_minutes"] == 360

    def test_janela_dentro_da_retencao_passa_intacta(self, monkeypatch):
        from src.monitoring import MetricsCollector

        monkeypatch.setenv("SPED_HUB_MONITORING_RETENTION_HOURS", "6")
        reset_settings_cache()
        assert MetricsCollector().snapshot(minutes=30)["window_minutes"] == 30

    def test_argumento_explicito_ignora_o_ambiente(self, monkeypatch):
        """Um coletor com retenção fixada no construtor não olha as settings."""
        from src.monitoring import MetricsCollector

        monkeypatch.setenv("SPED_HUB_MONITORING_RETENTION_HOURS", "6")
        reset_settings_cache()
        assert MetricsCollector(retention_hours=2).retention_hours == 2

    def test_retencao_nao_e_congelada_no_import(self, monkeypatch):
        """A instância global nasce no import; a retenção não pode nascer com ela.

        É o defeito que o `worker_runner` carrega e que não vale repetir: com
        leitura no `__init__`, mudar a configuração depois não mudaria nada.
        """
        from src.monitoring import metrics_collector

        antes = metrics_collector.retention_hours
        monkeypatch.setenv("SPED_HUB_MONITORING_RETENTION_HOURS", "3")
        reset_settings_cache()
        assert metrics_collector.retention_hours == 3 != antes


# ═══════════════════════════════════════════════════════════════════════════
# SPED_HUB_METRICS_WINDOW_MINUTES — janela padrão das métricas
# ═══════════════════════════════════════════════════════════════════════════


class TestJanelaPadraoDeMetricas:
    def test_default_e_60_minutos(self):
        from src.monitoring import janela_padrao_minutos

        assert janela_padrao_minutos() == 60

    def test_variavel_muda_a_janela_padrao(self, monkeypatch):
        from src.monitoring import janela_padrao_minutos

        monkeypatch.setenv("SPED_HUB_METRICS_WINDOW_MINUTES", "15")
        reset_settings_cache()
        assert janela_padrao_minutos() == 15

    def test_snapshot_sem_argumento_usa_a_janela_configurada(self, monkeypatch):
        from src.monitoring import MetricsCollector

        monkeypatch.setenv("SPED_HUB_METRICS_WINDOW_MINUTES", "15")
        reset_settings_cache()
        assert MetricsCollector().snapshot()["window_minutes"] == 15

    def test_argumento_explicito_vence_a_configuracao(self, monkeypatch):
        """Quem pede janela na URL manda; a variável é só o padrão."""
        from src.monitoring import MetricsCollector

        monkeypatch.setenv("SPED_HUB_METRICS_WINDOW_MINUTES", "15")
        reset_settings_cache()
        assert MetricsCollector().snapshot(minutes=45)["window_minutes"] == 45

    def test_janela_padrao_nao_e_congelada_no_import(self, monkeypatch):
        from src.monitoring import metrics_collector

        monkeypatch.setenv("SPED_HUB_METRICS_WINDOW_MINUTES", "15")
        reset_settings_cache()
        assert metrics_collector.snapshot()["window_minutes"] == 15


# ═══════════════════════════════════════════════════════════════════════════
# SPED_HUB_RATE_LIMIT_DEFAULT / _WINDOW — cota de API Key sem configuração
# ═══════════════════════════════════════════════════════════════════════════


class TestCotaPadraoDeApiKey:
    def test_default_e_100_por_60s(self):
        from src.ratelimit import limite_padrao

        assert limite_padrao() == (100, 60)

    def test_variaveis_mudam_a_cota_padrao(self, monkeypatch):
        from src.ratelimit import limite_padrao

        monkeypatch.setenv("SPED_HUB_RATE_LIMIT_DEFAULT", "7")
        monkeypatch.setenv("SPED_HUB_RATE_LIMIT_WINDOW", "30")
        reset_settings_cache()
        assert limite_padrao() == (7, 30)

    def test_limiter_aplica_a_cota_configurada(self, monkeypatch, tmp_path):
        """A cota tem de chegar ao contador, não só à função que a lê."""
        from src.ratelimit import RateLimiter

        monkeypatch.setenv("SPED_HUB_RATE_LIMIT_DEFAULT", "3")
        monkeypatch.setenv("SPED_HUB_RATE_LIMIT_WINDOW", "30")
        reset_settings_cache()

        limiter = RateLimiter(f"sqlite:///{tmp_path / 'cota.db'}")
        resultados = [limiter.verificar(api_key_id=4242)[0] for _ in range(4)]
        assert resultados == [True, True, True, False], "a 4ª requisição passou do limite 3"

        _, info = limiter.verificar(api_key_id=4242)
        assert (info.limite, info.janela) == (3, 30)

    def test_cota_por_chave_vence_o_default_global(self, monkeypatch, tmp_path):
        """Configuração no banco continua prevalecendo sobre a variável."""
        from src.db.models import ApiKey, RateLimitConfig, criar_engine, get_session, init_db
        from src.ratelimit import RateLimiter

        monkeypatch.setenv("SPED_HUB_RATE_LIMIT_DEFAULT", "3")
        reset_settings_cache()

        referencia = f"sqlite:///{tmp_path / 'cota2.db'}"
        engine = criar_engine(referencia)
        init_db(engine)
        sessao = get_session(engine)
        try:
            chave = ApiKey(nome="cota própria", key_hash="h" * 64, prefixo="spd_cota")
            sessao.add(chave)
            sessao.flush()
            sessao.add(RateLimitConfig(api_key_id=chave.id, limite=50, janela=120))
            sessao.commit()
            key_id = chave.id
        finally:
            sessao.close()

        _, info = RateLimiter(referencia).verificar(api_key_id=key_id)
        assert (info.limite, info.janela) == (50, 120)


# ═══════════════════════════════════════════════════════════════════════════
# SPED_HUB_ALLOWED_HOSTS — validação do cabeçalho Host
# ═══════════════════════════════════════════════════════════════════════════


class TestAllowedHosts:
    """`docs/deploy.md` manda pôr o domínio real, "não `*`", como passo de
    endurecimento — e nada lia a variável.  Quem seguia o guia acreditava ter
    restringido o Host e não havia restrição nenhuma."""

    @staticmethod
    def _resposta(host: str):
        from fastapi.testclient import TestClient

        from src.dashboard.app import app

        with TestClient(app) as cliente:
            return cliente.get("/login", headers={"Host": host})

    def test_com_asterisco_qualquer_host_passa(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_ALLOWED_HOSTS", "*")
        reset_settings_cache()
        assert self._resposta("qualquer-coisa.invalido").status_code == 200

    def test_host_fora_da_lista_e_recusado(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_ALLOWED_HOSTS", "escritorio.com.br")
        reset_settings_cache()
        resposta = self._resposta("atacante.invalido")
        assert resposta.status_code == 400
        # A resposta não conta qual domínio responde aqui.
        assert "escritorio" not in resposta.text

    def test_host_da_lista_passa(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_ALLOWED_HOSTS", "escritorio.com.br")
        reset_settings_cache()
        assert self._resposta("escritorio.com.br").status_code == 200

    def test_porta_nao_entra_na_comparacao(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_ALLOWED_HOSTS", "escritorio.com.br")
        reset_settings_cache()
        assert self._resposta("escritorio.com.br:8000").status_code == 200

    def test_lista_com_varios_hosts(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_ALLOWED_HOSTS", "a.com.br,b.com.br")
        reset_settings_cache()
        assert self._resposta("b.com.br").status_code == 200
        assert self._resposta("c.com.br").status_code == 400

    def test_curinga_de_subdominio(self, monkeypatch):
        monkeypatch.setenv("SPED_HUB_ALLOWED_HOSTS", "*.escritorio.com.br")
        reset_settings_cache()
        assert self._resposta("app.escritorio.com.br").status_code == 200
        assert self._resposta("escritorio.com.br").status_code == 200
        assert self._resposta("escritorio.com.br.invalido").status_code == 400

    def test_healthcheck_do_container_continua_funcionando(self, monkeypatch):
        """Loopback é sempre aceito.

        O `HEALTHCHECK` do Dockerfile chama `http://localhost:8000/...`.
        Recusar esse Host marcaria o container como não saudável para sempre —
        o mesmo defeito que a 0.16.0 já corrigiu por outro caminho.
        """
        from fastapi.testclient import TestClient

        from src.dashboard.app import app

        monkeypatch.setenv("SPED_HUB_ALLOWED_HOSTS", "escritorio.com.br")
        reset_settings_cache()
        with TestClient(app) as cliente:
            for host in ("localhost:8000", "127.0.0.1:8000"):
                resposta = cliente.get("/api/v1/health", headers={"Host": host})
                assert resposta.status_code == 200, f"healthcheck recusado com Host {host}"


class TestNenhumaReservadaSobrou:
    """A lista de "reservadas" da §2.2 ficou só com `SPED_HUB_SECRET_KEY`.

    Ela é reservada de verdade: sessões e tokens usam CSPRNG e o webhook
    assina com o segredo do próprio registro.  Qualquer nova entrada nessa
    lista é uma variável documentada sem efeito, e este teste força a
    justificativa a passar por aqui.
    """

    def test_apenas_secret_key_e_reservada(self):
        from pathlib import Path

        from tests.test_regras_projeto import variaveis_reservadas

        texto = (Path(__file__).resolve().parents[1] / ".env.example").read_text("utf-8")
        reservadas = variaveis_reservadas(texto)
        assert reservadas == {"SPED_HUB_SECRET_KEY"}, (
            f"conjunto de variáveis reservadas mudou: {sorted(reservadas)} — "
            "variável documentada sem efeito precisa de justificativa (§2.2)"
        )


def test_settings_expoe_todos_os_campos_usados_aqui():
    """Guarda contra renomear campo e este arquivo virar teste de nada."""
    cfg = get_settings()
    for campo in (
        "email_enabled",
        "monitoring_retention_hours",
        "metrics_window_minutes",
        "rate_limit_default",
        "rate_limit_window_seconds",
        "allowed_hosts",
    ):
        assert hasattr(cfg, campo), f"campo {campo} desapareceu de Settings"
