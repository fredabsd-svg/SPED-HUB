"""Webhooks passam a disparar de verdade (Fase 26).

Até a 0.17.0 este módulo tinha CRUD, proteção contra SSRF, assinatura HMAC e
entrega com retry — e **nenhum ponto do código chamava `dispatch()`**. Os três
eventos de `EVENTOS_DISPONIVEIS` estavam documentados no módulo e no README, o
cliente cadastrava o endpoint, e nada chegava nele. Estava registrado em
`docs/status.md` como decisão de produto pendente.

Os testes abaixo cobrem, para cada um dos três eventos, o par que importa:
**o evento sai** quando há assinante, e **a operação de negócio sobrevive**
quando a entrega falha. Notificação não pode ter poder de veto sobre
escrituração.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.db.models import WebhookRegistration, criar_engine, get_session, init_db

FIXTURE = Path(__file__).parent / "fixtures" / "ecd_sample.txt"


@pytest.fixture
def banco(tmp_path, monkeypatch) -> str:
    """Banco isolado, já apontado pelas settings."""
    caminho = str(tmp_path / "webhooks.db")
    engine = criar_engine(caminho)
    try:
        init_db(engine)
    finally:
        engine.dispose()
    monkeypatch.setenv("SPED_HUB_DB", caminho)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from src.settings import reset_settings_cache

    reset_settings_cache()
    return caminho


def _registrar(banco: str, eventos: list[str], *, ativo: bool = True) -> int:
    """Cadastra um webhook direto no banco (sem passar pela validação de URL)."""
    engine = criar_engine(banco)
    try:
        with get_session(engine) as sessao:
            wh = WebhookRegistration(
                url="https://exemplo.com.br/hook",
                eventos=json.dumps(eventos),
                ativo=ativo,
                descricao="teste",
            )
            sessao.add(wh)
            sessao.commit()
            return wh.id
    finally:
        engine.dispose()


@pytest.fixture
def entrega_inline(monkeypatch):
    """Emissão passa a ser síncrona.

    Em produção a entrega vai para thread — é o certo, porque o backoff
    chega a 60 s por tentativa. Num teste isso é uma corrida: a asserção
    roda antes da thread. Substituir a costura por execução inline é
    determinístico; dormir esperando a thread seria teste que falha sozinho
    sob carga.
    """
    import src.webhooks as webhooks

    monkeypatch.setattr(webhooks, "_em_segundo_plano", lambda alvo, nome: alvo())


@pytest.fixture
def capturar(monkeypatch, entrega_inline):
    """Intercepta a entrega e devolve a lista de eventos que passaram por ela.

    Substitui `_enviar_com_retry`, não o httpx: assim o teste exercita
    `dispatch` de verdade — inclusive o filtro por tipo de evento inscrito.
    """
    from src.webhooks import WebhookService

    recebidos: list[tuple[str, dict]] = []

    async def falso_envio(self, wh, evento):
        recebidos.append((evento.tipo, evento.dados))
        return True

    monkeypatch.setattr(WebhookService, "_enviar_com_retry", falso_envio)
    return recebidos


class TestEmitir:
    def test_sem_assinante_nao_entrega_nem_cria_thread(self, banco, capturar):
        """Custo zero é requisito: `emitir` está no caminho de toda importação."""
        from src.webhooks import emitir

        assert emitir("ecd.importada", {"ecd_id": 1}, aguardar=True) is None
        assert capturar == []

    def test_webhook_inativo_nao_recebe(self, banco, capturar):
        from src.webhooks import emitir

        _registrar(banco, ["ecd.importada"], ativo=False)
        assert emitir("ecd.importada", {"ecd_id": 1}, aguardar=True) is None
        assert capturar == []

    def test_assinante_de_outro_evento_nao_recebe(self, banco, capturar):
        from src.webhooks import emitir

        _registrar(banco, ["relatorio.gerado"])
        assert emitir("ecd.importada", {"ecd_id": 1}, aguardar=True) is None
        assert capturar == []

    def test_assinante_recebe_com_os_dados(self, banco, capturar):
        from src.webhooks import emitir

        _registrar(banco, ["ecd.importada"])
        resultado = emitir("ecd.importada", {"ecd_id": 7, "empresa": "ACME"}, aguardar=True)
        assert resultado == {"sucessos": 1, "falhas": 0}
        assert capturar == [("ecd.importada", {"ecd_id": 7, "empresa": "ACME"})]

    def test_evento_desconhecido_e_erro_de_programacao(self, banco):
        """Errar o nome do evento é defeito de código, não de dado: falha alto."""
        from src.webhooks import emitir

        with pytest.raises(ValueError, match="Evento desconhecido"):
            emitir("ecd.inventada", {}, aguardar=True)

    def test_json_de_eventos_corrompido_nao_impede_os_demais(self, banco, capturar):
        engine = criar_engine(banco)
        try:
            with get_session(engine) as sessao:
                sessao.add(
                    WebhookRegistration(
                        url="https://a.com.br/h", eventos="{isto não é json", ativo=True
                    )
                )
                sessao.add(
                    WebhookRegistration(
                        url="https://b.com.br/h",
                        eventos=json.dumps(["ecd.importada"]),
                        ativo=True,
                    )
                )
                sessao.commit()
        finally:
            engine.dispose()

        from src.webhooks import emitir

        resultado = emitir("ecd.importada", {"ecd_id": 1}, aguardar=True)
        assert resultado == {
            "sucessos": 1,
            "falhas": 0,
        }, "um registro com JSON ilegível derrubou a entrega dos outros"

    def test_falha_na_entrega_nao_propaga(self, banco, monkeypatch):
        """`emitir` engole exceção da entrega — quem chamou não pode quebrar."""
        from src.webhooks import WebhookService, emitir

        _registrar(banco, ["ecd.importada"])

        async def explode(self, wh, evento):
            raise RuntimeError("endpoint do cliente pegou fogo")

        monkeypatch.setattr(WebhookService, "_enviar_com_retry", explode)
        assert emitir("ecd.importada", {"ecd_id": 1}, aguardar=True) is None


class TestEventoImportacao:
    def test_importacao_emite_ecd_importada(self, banco, capturar):
        from src.ecd_importer import ECDImportService

        _registrar(banco, ["ecd.importada"])
        engine = criar_engine(banco)
        try:
            with get_session(engine) as sessao:
                resultado = ECDImportService(sessao).importar(FIXTURE)
        finally:
            engine.dispose()

        assert len(capturar) == 1, "a importação não emitiu o evento"
        tipo, dados = capturar[0]
        assert tipo == "ecd.importada"
        assert dados["ecd_id"] == resultado.ecd_id
        assert dados["contas"] == resultado.contas
        assert dados["hash_arquivo"] == resultado.hash_arquivo

    def test_importacao_recusada_nao_emite(self, banco, capturar, tmp_path):
        """Evento é de fato consumado: ECD recusada não gera notificação."""
        from src.ecd_importer import ECDImportError, ECDImportService

        _registrar(banco, ["ecd.importada"])
        ciclica = tmp_path / "ciclo.txt"
        ciclica.write_text(
            "\n".join(
                [
                    "|0000|LECD|01012024|31122024|EMPRESA CICLO|00123456000199|SP||1234567||0|0|1|0|0|E||1|0||",
                    "|I001|0|",
                    "|I010|G|009|",
                    "|I030|TERMO DE ABERTURA|1|Diario|500|EMPRESA TESTE|31123456789|11111111000191|01012015||BELO HORIZONTE|31122023|",
                    "|I050|01012024|01|A|3|1|1|CONTA UM|",
                    "|I990|99|",
                    "|9999|6|",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        engine = criar_engine(banco)
        try:
            with get_session(engine) as sessao:
                with pytest.raises(ECDImportError):
                    ECDImportService(sessao).importar(ciclica)
        finally:
            engine.dispose()

        assert capturar == [], "notificou importação que foi recusada e revertida"

    def test_webhook_quebrado_nao_derruba_importacao(self, banco, monkeypatch):
        """A garantia que importa: notificação não veta escrituração."""
        from src.ecd_importer import ECDImportService
        from src.webhooks import WebhookService

        _registrar(banco, ["ecd.importada"])

        async def explode(self, wh, evento):
            raise RuntimeError("endpoint fora do ar")

        monkeypatch.setattr(WebhookService, "_enviar_com_retry", explode)

        engine = criar_engine(banco)
        try:
            with get_session(engine) as sessao:
                resultado = ECDImportService(sessao).importar(FIXTURE)
        finally:
            engine.dispose()

        assert resultado.ecd_id, "a importação falhou por causa do webhook"
        assert resultado.contas > 0


class TestEventoValidacao:
    def test_validacao_emite_ecd_validada(self, banco, capturar):
        from src.ecd_importer import ECDImportService
        from src.validators.integridade import ValidadorIntegridade

        engine = criar_engine(banco)
        try:
            with get_session(engine) as sessao:
                resultado = ECDImportService(sessao).importar(FIXTURE)
                # Só assina a validação: o evento de importação já passou.
                _registrar(banco, ["ecd.validada"])
                capturar.clear()
                ValidadorIntegridade(sessao, resultado.ecd_id).validar_todas()
        finally:
            engine.dispose()

        assert len(capturar) == 1
        tipo, dados = capturar[0]
        assert tipo == "ecd.validada"
        assert dados["ecd_id"] == resultado.ecd_id
        assert dados["status"] in {"OK", "ERROS"}
        assert "erros" in dados and "alertas" in dados


class TestEventoRelatorio:
    def test_export_pdf_emite_relatorio_gerado(self, banco, capturar, tmp_path):
        from src.reports.base import ReportContext
        from src.reports.export_engine import ExportEngine, WhiteLabel

        _registrar(banco, ["relatorio.gerado"])
        ctx = ReportContext(
            titulo="Balancete de Verificação",
            empresa_nome="EMPRESA TESTE LTDA",
            periodo_ref="2024",
        )
        saida = tmp_path / "balancete.pdf"
        ExportEngine().export_pdf(
            "balancete.html",
            str(saida),
            ctx,
            WhiteLabel(),
            linhas=[],
            totais={"saldo_inicial": 0.0, "debitos": 0.0, "creditos": 0.0, "saldo_final": 0.0},
            conferencia={
                "total_contas": 0,
                "contas_com_divergencia": 0,
                "soma_divergencias": 0.0,
                "status": "OK",
            },
        )

        assert saida.exists()
        assert len(capturar) == 1
        tipo, dados = capturar[0]
        assert tipo == "relatorio.gerado"
        assert dados["formato"] == "pdf"
        assert dados["arquivo"] == "balancete.pdf"
        assert dados["empresa"] == "EMPRESA TESTE LTDA"

    def test_evento_nao_carrega_a_escrituracao(self, banco, capturar, tmp_path):
        """Webhook sai para terceiro: leva metadado, nunca o conteúdo contábil."""
        from src.reports.base import ReportContext
        from src.reports.export_engine import ExportEngine, WhiteLabel

        _registrar(banco, ["relatorio.gerado"])
        ExportEngine().export_xlsx(
            str(tmp_path / "b.xlsx"),
            ReportContext(titulo="Balancete", empresa_nome="ACME", periodo_ref="2024"),
            [{"cod_cta": "1.1.1", "saldo": 999999.99}],
            ["cod_cta", "saldo"],
            "Balancete",
            WhiteLabel(),
        )

        assert len(capturar) == 1
        _tipo, dados = capturar[0]
        corpo = json.dumps(dados)
        assert "999999.99" not in corpo, "o evento levou saldo contábil para fora"
        assert "1.1.1" not in corpo, "o evento levou código de conta para fora"


class TestSettingsAgoraTemEfeito:
    """As duas variáveis que existiam sem consumidor (§2.2)."""

    def test_timeout_e_max_retries_vem_das_settings(self, banco, monkeypatch, entrega_inline):
        # A guarda de SSRF resolve o hostname, e o ambiente de teste não tem
        # DNS. Ela tem testes próprios em test_review_regressions.py; aqui o
        # alvo é o cliente HTTP.
        import src.webhooks as webhooks

        monkeypatch.setattr(webhooks, "validate_webhook_url", lambda url, **_: url)
        monkeypatch.setenv("SPED_HUB_WEBHOOK_TIMEOUT", "3")
        monkeypatch.setenv("SPED_HUB_WEBHOOK_DEFAULT_MAX_RETRIES", "1")
        from src.settings import reset_settings_cache

        reset_settings_cache()

        capturado: dict = {}

        class ClienteFalso:
            def __init__(self, *, timeout):
                capturado["timeout"] = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def post(self, *_a, **_kw):
                capturado["tentativas"] = capturado.get("tentativas", 0) + 1
                raise RuntimeError("sem rede")

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", ClienteFalso)

        # `registrar` sem max_retries explícito pega o default da setting —
        # é essa a semântica de "DEFAULT_MAX_RETRIES" (a coluna é NOT NULL,
        # então cada registro carrega o próprio valor).
        from src.webhooks import WebhookService, emitir

        servico = WebhookService(banco)
        wh = servico.registrar(url="https://exemplo.com.br/hook", eventos=["ecd.importada"])
        assert wh.max_retries == 1, (
            "registro novo não herdou SPED_HUB_WEBHOOK_DEFAULT_MAX_RETRIES: "
            f"gravou {wh.max_retries}"
        )

        emitir("ecd.importada", {"ecd_id": 1}, aguardar=True)

        assert (
            capturado["timeout"] == 3.0
        ), "SPED_HUB_WEBHOOK_TIMEOUT continua sem efeito no cliente HTTP"
        assert capturado["tentativas"] == 1, (
            "SPED_HUB_WEBHOOK_DEFAULT_MAX_RETRIES continua sem efeito: "
            f"{capturado['tentativas']} tentativas em vez de 1"
        )
