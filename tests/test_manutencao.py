"""Retenção de histórico que realmente executa (Fase 31).

Duas tabelas cresciam sem limite, por motivos diferentes:

1. `async_jobs` **tinha** o expurgo escrito (`limpar_antigos`) e o docstring do
   módulo prometia "Jobs concluídos expiram após 24h (limpeza automática)".
   Nenhum ponto do sistema chamava a função. É a mesma forma de defeito dos
   webhooks que nunca disparavam: código pronto, promessa documentada, e
   ninguém acionando.
2. `webhook_deliveries` não tinha expurgo nenhum, e guarda uma linha por
   **tentativa** — integração instável enche a tabela rápido.

`audit_logs` fica fora da manutenção automática de propósito: é o registro de
quem mexeu em escrituração fiscal, e apagá-lo por conta própria não é decisão
que o sistema possa tomar sozinho.
"""

from __future__ import annotations

import asyncio
import datetime
import json

import pytest

import src.dashboard.app as app_mod
import src.webhooks as wh_mod
from src.async_jobs import AsyncJobService, JobStatus
from src.db.models import (
    AsyncJob,
    AuditLog,
    WebhookDelivery,
    criar_engine,
    get_session,
    init_db,
)
from src.settings import reset_settings_cache
from src.webhooks import WebhookService


@pytest.fixture
def referencia(tmp_path, monkeypatch) -> str:
    alvo = f"sqlite:///{tmp_path / 'manutencao.db'}"
    engine = criar_engine(alvo)
    try:
        init_db(engine)
    finally:
        engine.dispose()
    monkeypatch.setenv("DATABASE_URL", alvo)
    monkeypatch.delenv("SPED_HUB_DB", raising=False)
    for chave in (
        "SPED_HUB_JOB_RETENTION_HOURS",
        "SPED_HUB_WEBHOOK_RETENTION_DAYS",
        "SPED_HUB_MAINTENANCE_INTERVAL_MINUTES",
    ):
        monkeypatch.delenv(chave, raising=False)
    reset_settings_cache()
    return alvo


@pytest.fixture
def webhooks(referencia, monkeypatch) -> WebhookService:
    monkeypatch.setattr(wh_mod, "validate_webhook_url", lambda url, resolve=False: url)
    return WebhookService(referencia)


def _dias_atras(dias: int) -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(days=dias)


def _semear_entregas(referencia: str, webhook_id: int, casos: list[tuple[str, int, int]]) -> None:
    """`(status, dias_atras, ecd_id)` direto no banco."""
    engine = criar_engine(referencia)
    with get_session(engine) as sessao:
        for status, dias, ecd_id in casos:
            sessao.add(
                WebhookDelivery(
                    webhook_id=webhook_id,
                    evento="ecd.importada",
                    status=status,
                    request_body=json.dumps(
                        {
                            "dados": {"ecd_id": ecd_id},
                            "timestamp": "2026-01-01T00:00:00+00:00",
                        }
                    ),
                    tentativa=1,
                    criado_em=_dias_atras(dias),
                )
            )
        sessao.commit()


def _contar(referencia: str, modelo) -> int:
    import sqlalchemy

    engine = criar_engine(referencia)
    with get_session(engine) as sessao:
        return sessao.execute(sqlalchemy.select(sqlalchemy.func.count(modelo.id))).scalar() or 0


# ═══════════════════════════════════════════════════════════════════════════
# 1. Expurgo de entregas de webhook
# ═══════════════════════════════════════════════════════════════════════════


class TestExpurgoDeEntregas:
    def test_entrega_antiga_e_removida(self, webhooks, referencia):
        wh = webhooks.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear_entregas(referencia, wh.id, [("success", 60, 1)])

        assert webhooks.purgar_deliveries() == 1
        assert _contar(referencia, WebhookDelivery) == 0

    def test_entrega_recente_fica(self, webhooks, referencia):
        wh = webhooks.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear_entregas(referencia, wh.id, [("success", 3, 1)])

        assert webhooks.purgar_deliveries() == 0
        assert _contar(referencia, WebhookDelivery) == 1

    def test_entrega_em_voo_nunca_e_removida(self, webhooks, referencia):
        """`pending` antiga é entrega abandonada — apagá-la perde o evento."""
        wh = webhooks.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear_entregas(referencia, wh.id, [("pending", 90, 1)])

        assert webhooks.purgar_deliveries() == 0

    def test_entrega_abandonada_nunca_e_removida(self, webhooks, referencia):
        """É justamente a que o operador ainda pode recuperar pelo reenvio.

        Apagá-la transformaria "evento recuperável" em "evento perdido para
        sempre" — e a idade dela é grande por definição, então um expurgo por
        idade a pegaria primeiro.
        """
        wh = webhooks.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear_entregas(referencia, wh.id, [("superseded", 90, 1)])
        assert len(webhooks.deliveries_abandonadas()) == 1, "o cenário precisa ser de abandonada"

        assert webhooks.purgar_deliveries() == 0
        assert len(webhooks.deliveries_abandonadas()) == 1, "segue recuperável"

    def test_entrega_em_voo_de_evento_resolvido_tambem_fica(self, webhooks, referencia):
        """`pending` antiga cujo evento JÁ teve desfecho não é abandonada.

        Aqui só o filtro de estado terminal a protege — o de abandonada não
        alcança este caso. Sem este teste, remover o filtro de estado passaria
        sem nada acusar, e uma tentativa em voo seria apagada debaixo de quem
        a está executando.
        """
        wh = webhooks.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear_entregas(referencia, wh.id, [("pending", 90, 5), ("success", 90, 5)])
        assert webhooks.deliveries_abandonadas() == [], "o cenário não é de abandonada"

        assert webhooks.purgar_deliveries() == 1, "só a `success` sai"
        assert _contar(referencia, WebhookDelivery) == 1

    def test_tentativa_de_entrega_resolvida_e_removida(self, webhooks, referencia):
        """`superseded` cujo evento teve desfecho é só histórico: pode sair."""
        wh = webhooks.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear_entregas(referencia, wh.id, [("superseded", 90, 7), ("success", 90, 7)])
        assert webhooks.deliveries_abandonadas() == []

        assert webhooks.purgar_deliveries() == 2
        assert _contar(referencia, WebhookDelivery) == 0

    def test_retencao_configurada_vale(self, webhooks, referencia, monkeypatch):
        wh = webhooks.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear_entregas(referencia, wh.id, [("success", 10, 1)])
        assert webhooks.purgar_deliveries() == 0, "10 dias < 30 do default"

        monkeypatch.setenv("SPED_HUB_WEBHOOK_RETENTION_DAYS", "5")
        reset_settings_cache()

        assert webhooks.purgar_deliveries() == 1

    def test_retencao_zero_desliga_o_expurgo(self, webhooks, referencia, monkeypatch):
        wh = webhooks.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear_entregas(referencia, wh.id, [("success", 999, 1)])

        monkeypatch.setenv("SPED_HUB_WEBHOOK_RETENTION_DAYS", "0")
        reset_settings_cache()

        assert webhooks.purgar_deliveries() == 0
        assert _contar(referencia, WebhookDelivery) == 1

    def test_argumento_explicito_vence_a_configuracao(self, webhooks, referencia):
        wh = webhooks.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear_entregas(referencia, wh.id, [("success", 10, 1)])

        assert webhooks.purgar_deliveries(dias=5) == 1

    def test_expurgo_grande_sai_em_lotes(self, webhooks, referencia):
        """O `IN` é fatiado, e a verificação conta os DELETE emitidos.

        Um `IN` único com milhares de itens estoura o limite de parâmetros por
        statement — 999 em SQLite antigo, e há teto também nos drivers de
        Postgres. Contar linhas removidas não prova nada: este SQLite aguenta
        1200 num `IN` só, então o teste passaria com o lote removido. O que se
        verifica é o **número de statements**.
        """
        import sqlalchemy

        wh = webhooks.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        total = 1200
        _semear_entregas(referencia, wh.id, [("success", 60, i) for i in range(total)])

        deletes: list[str] = []

        def espiar(conn, cursor, statement, parameters, context, executemany):
            if statement.strip().upper().startswith("DELETE"):
                deletes.append(statement)

        # O ouvinte vai na classe `Engine`, não numa instância: o serviço cria
        # a própria engine, e escutar a do teste não pegaria nada.
        sqlalchemy.event.listen(sqlalchemy.engine.Engine, "before_cursor_execute", espiar)
        try:
            assert webhooks.purgar_deliveries() == total
        finally:
            sqlalchemy.event.remove(sqlalchemy.engine.Engine, "before_cursor_execute", espiar)

        assert _contar(referencia, WebhookDelivery) == 0
        assert len(deletes) >= 2, (
            f"{total} linhas saíram em {len(deletes)} statement(s) — sem fatiar, "
            "um banco com limite menor de parâmetros recusaria o expurgo inteiro"
        )

    def test_banco_vazio_nao_quebra(self, webhooks):
        assert webhooks.purgar_deliveries() == 0


# ═══════════════════════════════════════════════════════════════════════════
# 2. A manutenção agrega os expurgos — e não toca a auditoria
# ═══════════════════════════════════════════════════════════════════════════


def _semear_job_antigo(referencia: str) -> int:
    engine = criar_engine(referencia)
    with get_session(engine) as sessao:
        job = AsyncJob(
            tipo="ecd_import",
            status=JobStatus.COMPLETED.value,
            progresso=100.0,
            criado_em=_dias_atras(5),
            concluido_em=_dias_atras(5),
        )
        sessao.add(job)
        sessao.commit()
        return job.id


class TestManutencao:
    def test_remove_job_antigo(self, referencia):
        _semear_job_antigo(referencia)

        assert app_mod.executar_manutencao()["jobs"] == 1
        assert _contar(referencia, AsyncJob) == 0

    def test_remove_entrega_antiga(self, referencia, webhooks):
        wh = webhooks.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear_entregas(referencia, wh.id, [("success", 60, 1)])

        assert app_mod.executar_manutencao()["entregas_de_webhook"] == 1

    def test_nao_toca_o_log_de_auditoria(self, referencia):
        """Registro de quem mexeu em escrituração fiscal não é lixo de sistema.

        Apagá-lo por conta própria é decisão que o sistema não pode tomar
        sozinho; a limpeza segue manual, por rota de administrador.
        """
        engine = criar_engine(referencia)
        with get_session(engine) as sessao:
            sessao.add(
                AuditLog(
                    acao="ecd.importada",
                    recurso="ECD #1",
                    criado_em=_dias_atras(3650),
                )
            )
            sessao.commit()

        app_mod.executar_manutencao()

        assert _contar(referencia, AuditLog) == 1, "auditoria de 10 anos atrás foi apagada"

    def test_retencao_de_job_zero_desliga_so_esse_expurgo(self, referencia, webhooks, monkeypatch):
        _semear_job_antigo(referencia)
        wh = webhooks.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear_entregas(referencia, wh.id, [("success", 60, 1)])

        monkeypatch.setenv("SPED_HUB_JOB_RETENTION_HOURS", "0")
        reset_settings_cache()

        resultado = app_mod.executar_manutencao()

        assert resultado["jobs"] == 0
        assert resultado["entregas_de_webhook"] == 1, "o outro expurgo continua"

    def test_falha_num_expurgo_nao_impede_o_outro(self, referencia, monkeypatch):
        """Isolar importa: um erro no webhook não pode travar a limpeza de jobs."""
        _semear_job_antigo(referencia)

        def explodir(self, dias=None):
            raise RuntimeError("banco de webhooks indisponível")

        monkeypatch.setattr(WebhookService, "purgar_deliveries", explodir)

        resultado = app_mod.executar_manutencao()

        assert resultado["jobs"] == 1
        assert resultado["entregas_de_webhook"] == 0

    def test_falha_no_expurgo_de_jobs_nao_impede_o_de_webhooks(
        self, referencia, webhooks, monkeypatch
    ):
        """O espelho do teste acima. Sem os dois lados, metade do isolamento
        pode ser removida sem nada acusar."""
        wh = webhooks.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear_entregas(referencia, wh.id, [("success", 60, 1)])

        def explodir(self, horas=24):
            raise RuntimeError("banco de jobs indisponível")

        monkeypatch.setattr(AsyncJobService, "limpar_antigos", explodir)

        resultado = app_mod.executar_manutencao()

        assert resultado["jobs"] == 0
        assert resultado["entregas_de_webhook"] == 1

    def test_e_idempotente(self, referencia):
        """Duas réplicas rodando junto não podem duplicar nem quebrar."""
        _semear_job_antigo(referencia)

        assert app_mod.executar_manutencao()["jobs"] == 1
        assert app_mod.executar_manutencao()["jobs"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# 3. O laço periódico — o que faltava para a promessa ser verdade
# ═══════════════════════════════════════════════════════════════════════════


class TestLacoPeriodico:
    @staticmethod
    async def _rodar_voltas(monkeypatch, voltas: int) -> list[float]:
        """Executa o laço `voltas` vezes e devolve os intervalos dormidos."""
        dormidos: list[float] = []

        async def sleep_falso(segundos):
            dormidos.append(segundos)
            if len(dormidos) >= voltas:
                raise asyncio.CancelledError

        monkeypatch.setattr(app_mod.asyncio, "sleep", sleep_falso)
        with pytest.raises(asyncio.CancelledError):
            await app_mod._laco_de_manutencao()
        return dormidos

    def test_dorme_o_intervalo_configurado(self, referencia, monkeypatch):
        async def _corpo():
            monkeypatch.setenv("SPED_HUB_MAINTENANCE_INTERVAL_MINUTES", "15")
            reset_settings_cache()

            dormidos = await self._rodar_voltas(monkeypatch, 1)

            assert dormidos == [15 * 60]

        asyncio.run(_corpo())

    def test_intervalo_zero_encerra_o_laco(self, referencia, monkeypatch):
        async def _corpo():
            """Desligar é escolha válida — quem prefere cron, por exemplo."""
            monkeypatch.setenv("SPED_HUB_MAINTENANCE_INTERVAL_MINUTES", "0")
            reset_settings_cache()

            chamadas = []
            monkeypatch.setattr(app_mod, "executar_manutencao", lambda: chamadas.append(1))
            # O `sleep` corta o laço por conta própria: sem isso, um laço que
            # ignore o intervalo 0 giraria para sempre em vez de falhar.
            voltas = []

            async def sleep_falso(_segundos):
                voltas.append(1)
                if len(voltas) >= 2:
                    raise asyncio.CancelledError

            monkeypatch.setattr(app_mod.asyncio, "sleep", sleep_falso)

            await app_mod._laco_de_manutencao()  # retorna, não pendura
            assert voltas == [], "o laço dormiu apesar do intervalo 0"

            assert chamadas == []

        asyncio.run(_corpo())

    def test_intervalo_e_relido_a_cada_volta(self, referencia, monkeypatch):
        async def _corpo():
            """A instância global nasce no import; congelar o intervalo ali valeria
            para o processo inteiro — o defeito documentado no `worker_runner`."""
            monkeypatch.setenv("SPED_HUB_MAINTENANCE_INTERVAL_MINUTES", "10")
            reset_settings_cache()
            dormidos: list[float] = []

            async def sleep_falso(segundos):
                dormidos.append(segundos)
                if len(dormidos) == 1:
                    monkeypatch.setenv("SPED_HUB_MAINTENANCE_INTERVAL_MINUTES", "45")
                    reset_settings_cache()
                if len(dormidos) >= 2:
                    raise asyncio.CancelledError

            monkeypatch.setattr(app_mod.asyncio, "sleep", sleep_falso)
            monkeypatch.setattr(app_mod.asyncio, "to_thread", lambda fn, *a: _feito())
            with pytest.raises(asyncio.CancelledError):
                await app_mod._laco_de_manutencao()

            assert dormidos == [600, 2700]

        asyncio.run(_corpo())

    def test_volta_que_falha_nao_mata_o_laco(self, referencia, monkeypatch):
        async def _corpo():
            """Laço morto = histórico voltando a crescer sem ninguém perceber."""
            monkeypatch.setenv("SPED_HUB_MAINTENANCE_INTERVAL_MINUTES", "5")
            reset_settings_cache()
            dormidos: list[float] = []

            async def sleep_falso(segundos):
                dormidos.append(segundos)
                if len(dormidos) >= 3:
                    raise asyncio.CancelledError

            async def to_thread_que_explode(_fn, *_a):
                raise RuntimeError("banco caiu")

            monkeypatch.setattr(app_mod.asyncio, "sleep", sleep_falso)
            monkeypatch.setattr(app_mod.asyncio, "to_thread", to_thread_que_explode)
            with pytest.raises(asyncio.CancelledError):
                await app_mod._laco_de_manutencao()

            assert len(dormidos) == 3, "o laço seguiu depois da volta que falhou"

        asyncio.run(_corpo())

    def test_cancelamento_propaga(self, referencia, monkeypatch):
        async def _corpo():
            """No encerramento o laço tem de sair, não engolir o cancelamento."""
            monkeypatch.setenv("SPED_HUB_MAINTENANCE_INTERVAL_MINUTES", "5")
            reset_settings_cache()

            voltas = []

            async def to_thread_cancelado(_fn, *_a):
                raise asyncio.CancelledError

            async def sleep_falso(_segundos):
                # Corta aqui também: se o laço deixar de usar `to_thread`, o
                # cancelamento nunca chegaria e o teste giraria para sempre.
                voltas.append(1)
                if len(voltas) >= 3:
                    raise asyncio.CancelledError

            monkeypatch.setattr(app_mod.asyncio, "sleep", sleep_falso)
            monkeypatch.setattr(app_mod.asyncio, "to_thread", to_thread_cancelado)

            with pytest.raises(asyncio.CancelledError):
                await app_mod._laco_de_manutencao()

        asyncio.run(_corpo())

    def test_expurgo_nao_roda_no_laco_de_eventos(self, referencia, monkeypatch):
        async def _corpo():
            """Ele toca o banco: no laço de eventos bloquearia toda requisição."""
            monkeypatch.setenv("SPED_HUB_MAINTENANCE_INTERVAL_MINUTES", "5")
            reset_settings_cache()
            via_thread = []
            voltas = []

            async def to_thread_espiao(fn, *a):
                via_thread.append(fn)
                return None

            async def sleep_falso(_segundos):
                # O corte fica AQUI, não no espião: se o laço parar de usar
                # `to_thread`, o espião nunca roda e o teste giraria para
                # sempre em vez de falhar.
                voltas.append(1)
                if len(voltas) >= 2:
                    raise asyncio.CancelledError

            monkeypatch.setattr(app_mod.asyncio, "sleep", sleep_falso)
            monkeypatch.setattr(app_mod.asyncio, "to_thread", to_thread_espiao)

            with pytest.raises(asyncio.CancelledError):
                await app_mod._laco_de_manutencao()

            assert via_thread == [app_mod.executar_manutencao], (
                "a manutenção precisa ir para um thread: no laço de eventos "
                "ela bloquearia toda requisição enquanto toca o banco"
            )

        asyncio.run(_corpo())


def _feito():
    """Coroutine já resolvida, para substituir `to_thread` em teste."""

    async def _nada():
        return None

    return _nada()


class TestLigacaoNoCicloDeVida:
    def test_o_laco_sobe_com_a_aplicacao(self, referencia, monkeypatch):
        """Sem esta ligação, a manutenção existiria e nunca rodaria — a forma
        de defeito que este projeto já teve três vezes."""
        from fastapi.testclient import TestClient

        tarefas: list[str] = []
        original = asyncio.create_task

        def espiao(coro, *a, **k):
            nome = getattr(coro, "__qualname__", "") or getattr(coro, "cr_code", None)
            tarefas.append(str(nome))
            return original(coro, *a, **k)

        monkeypatch.setattr(app_mod.asyncio, "create_task", espiao)

        with TestClient(app_mod.app):
            pass

        assert any(
            "_laco_de_manutencao" in t for t in tarefas
        ), f"o laço de manutenção não foi iniciado no lifespan: {tarefas}"

    def test_o_laco_e_cancelado_no_encerramento(self, referencia):
        """Tarefa pendurada vira "task was destroyed but it is pending".

        O `lifespan` é dirigido à mão, sem `TestClient`: pelo cliente, o
        encerramento do laço de eventos cancela a tarefa de qualquer forma, e a
        asserção não distinguiria o cancelamento explícito do acidental.
        """

        async def _corpo():
            criadas: list[asyncio.Task] = []
            original = asyncio.create_task

            def espiao(coro, *a, **k):
                tarefa = original(coro, *a, **k)
                criadas.append(tarefa)
                return tarefa

            asyncio.create_task = espiao
            try:
                async with app_mod.ciclo_de_vida(app_mod.app):
                    assert criadas, "nenhuma tarefa criada no lifespan"
                    assert not criadas[0].done(), "a tarefa devia estar rodando aqui"
            finally:
                asyncio.create_task = original

            assert criadas[0].cancelled(), (
                "o laço de manutenção não foi cancelado no encerramento do "
                "lifespan — a tarefa fica pendurada"
            )

        asyncio.run(_corpo())


def test_docstring_do_modulo_nao_promete_o_que_nao_faz():
    """O módulo dizia "limpeza automática" e nada chamava a função.

    Se a promessa voltar sem o laço, isto quebra.
    """
    import src.async_jobs as modulo

    doc = modulo.__doc__ or ""
    if "automática" in doc or "automatica" in doc:
        assert (
            "manutenção" in doc.lower() or "manutencao" in doc.lower()
        ), "o módulo promete limpeza automática sem apontar quem a executa"
