"""Testes da Fase 15 — Redis Cache, Worker Queue, Email e Playwright E2E.

Cobre:
  1. RedisCacheService — get/set, TTL, fallback memória, invalidação
  2. WorkerQueue — enfileirar, processar, retry, shutdown
  3. EmailService — envio modo log, alertas, histórico
  4. Playwright E2E — login, upload, dashboard, relatórios
"""

import time

import pytest

from src.cache.redis_cache import RedisCacheService

# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def redis_cache():
    """Cache Redis com fallback para memória (Redis não disponível no CI)."""
    cache = RedisCacheService(
        redis_url="redis://localhost:6379/15",
        max_entries=100,
        prefix="test:",
    )
    yield cache
    cache.clear()


@pytest.fixture
def email_svc():
    """Email service em modo log."""
    from src.email_service import EmailService

    return EmailService(modo="log")


# ═══════════════════════════════════════════════════════════════════════════
# Testes: RedisCacheService
# ═══════════════════════════════════════════════════════════════════════════


class TestRedisCacheService:
    """Testes do cache com Redis + fallback."""

    def test_set_get(self, redis_cache):
        """Armazena e recupera valor."""
        redis_cache.set("key1", "value1")
        assert redis_cache.get("key1") == "value1"

    def test_get_inexistente(self, redis_cache):
        """Retorna None para chave inexistente."""
        assert redis_cache.get("nao_existe") is None

    def test_ttl_expira(self, redis_cache):
        """Valor expira após TTL."""
        redis_cache.set("key", "value", ttl=1)
        assert redis_cache.get("key") == "value"
        time.sleep(1.2)
        assert redis_cache.get("key") is None

    def test_delete(self, redis_cache):
        """Remove entrada específica."""
        redis_cache.set("key", "value")
        assert redis_cache.delete("key") is True
        assert redis_cache.get("key") is None
        assert redis_cache.delete("key") is False

    def test_invalidate_prefix(self, redis_cache):
        """Invalida entradas por prefixo."""
        redis_cache.set("kpi:ativo", 1000)
        redis_cache.set("kpi:passivo", 500)
        redis_cache.set("balanco:ativo", 2000)

        count = redis_cache.invalidate_prefix("kpi:")
        assert count >= 2  # pode ser mais se Redis estiver ativo
        assert redis_cache.get("kpi:ativo") is None
        assert redis_cache.get("kpi:passivo") is None
        assert redis_cache.get("balanco:ativo") == 2000

    def test_clear(self, redis_cache):
        """Limpa todo o cache."""
        redis_cache.set("a", 1)
        redis_cache.set("b", 2)
        count = redis_cache.clear()
        assert count >= 2
        assert redis_cache.get("a") is None
        assert redis_cache.get("b") is None

    def test_stats(self, redis_cache):
        """Estatísticas do cache."""
        redis_cache.set("a", 1)
        redis_cache.get("a")  # hit
        redis_cache.get("b")  # miss

        stats = redis_cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["sets"] == 1
        assert stats["hit_rate_pct"] == 50.0
        assert "backend" in stats

    def test_valores_complexos(self, redis_cache):
        """Cacheia dicts e listas."""
        redis_cache.set("dict", {"a": 1, "b": [2, 3]})
        assert redis_cache.get("dict") == {"a": 1, "b": [2, 3]}

        redis_cache.set("lista", [1, 2, 3])
        assert redis_cache.get("lista") == [1, 2, 3]

    def test_max_entries_fallback(self):
        """Fallback de memória respeita max_entries."""
        cache = RedisCacheService(
            redis_url="redis://localhost:9999/0",  # porta inválida
            max_entries=3,
            prefix="test:",
        )
        cache.set("a", 1, ttl=100)
        cache.set("b", 2, ttl=100)
        cache.set("c", 3, ttl=100)
        cache.set("d", 4, ttl=100)

        assert cache.get("a") is None
        assert cache.get("d") == 4
        assert cache.stats()["entries"] == 3
        cache.clear()

    def test_backend_reportado(self, redis_cache):
        """Stats reportam qual backend está ativo."""
        stats = redis_cache.stats()
        assert stats["backend"] in ("redis", "memory")


# ═══════════════════════════════════════════════════════════════════════════
# Testes: WorkerQueue
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkerQueue:
    """Testes da fila de workers."""

    def test_enqueue_e_status(self):
        """Enfileira task e consulta status."""
        from src.worker_queue import WorkerQueue

        q = WorkerQueue(num_workers=2)
        q.register_handler("test", lambda p, up: {"ok": True})
        q.start()

        try:
            task_id = q.enqueue("test", {"x": 1})
            assert task_id

            status = q.status(task_id)
            assert status is not None
            assert status["tipo"] == "test"
            assert status["status"] in ("queued", "running", "completed")

            # Aguarda processamento
            deadline = time.time() + 30
            while time.time() < deadline:
                q.process_results()
                s = q.status(task_id)
                if s and s["status"] in ("completed", "failed"):
                    break
                time.sleep(0.1)

            final = q.status(task_id)
            assert final["status"] == "completed"
            assert final["resultado"] == {"ok": True}
            assert final["progresso"] == 100.0
        finally:
            q.shutdown()

    def test_handler_nao_registrado(self):
        """Task sem handler falha."""
        from src.worker_queue import WorkerQueue

        q = WorkerQueue(num_workers=1)
        q.start()

        try:
            task_id = q.enqueue("inexistente", {})

            deadline = time.time() + 30
            while time.time() < deadline:
                q.process_results()
                s = q.status(task_id)
                if s and s["status"] == "failed":
                    break
                time.sleep(0.1)

            final = q.status(task_id)
            assert final["status"] == "failed"
            assert "Handler não registrado" in final["erro"]
        finally:
            q.shutdown()

    def test_retry_com_backoff(self):
        """Task com falha temporária faz retry."""
        from src.worker_queue import WorkerQueue

        tentativas = []

        def handler_falha_temporaria(payload, update_progress):
            tentativas.append(1)
            if len(tentativas) < 3:
                raise ValueError("Erro temporário")
            return {"ok": True, "tentativas": len(tentativas)}

        q = WorkerQueue(num_workers=1)
        q.register_handler("retry_test", handler_falha_temporaria)
        q.start()

        try:
            task_id = q.enqueue("retry_test", {})

            deadline = time.time() + 30
            while time.time() < deadline:
                q.process_results()
                s = q.status(task_id)
                if s and s["status"] in ("completed", "failed"):
                    break
                time.sleep(0.2)

            final = q.status(task_id)
            assert final["status"] == "completed"
            assert final["tentativas"] == 3
            assert final["resultado"]["tentativas"] == 3
        finally:
            q.shutdown()

    def test_retry_esgota(self):
        """Task que sempre falha esgota tentativas."""
        from src.worker_queue import WorkerQueue

        def handler_sempre_falha(payload, update_progress):
            raise RuntimeError("Erro permanente")

        q = WorkerQueue(num_workers=1)
        q.register_handler("fail_test", handler_sempre_falha)
        q.start()

        try:
            task_id = q.enqueue("fail_test", {})

            deadline = time.time() + 30
            while time.time() < deadline:
                q.process_results()
                s = q.status(task_id)
                if s and s["status"] == "failed":
                    break
                time.sleep(0.2)

            final = q.status(task_id)
            assert final["status"] == "failed"
            assert final["tentativas"] == 3
            assert "Erro permanente" in final["erro"]
        finally:
            q.shutdown()

    def test_list_tasks(self):
        """Lista tasks com filtro."""
        from src.worker_queue import WorkerQueue

        q = WorkerQueue(num_workers=2)
        q.register_handler("test", lambda p, up: {"ok": True})
        q.start()

        try:
            ids = [q.enqueue("test", {}) for _ in range(3)]

            # Aguarda todas concluírem
            deadline = time.time() + 30
            while time.time() < deadline:
                q.process_results()
                all_done = all(q.status(tid)["status"] in ("completed", "failed") for tid in ids)
                if all_done:
                    break
                time.sleep(0.1)

            all_tasks = q.list_tasks()
            assert len(all_tasks) == 3

            completed = q.list_tasks(status="completed")
            assert len(completed) == 3
        finally:
            q.shutdown()

    def test_progresso(self):
        """Handler pode reportar progresso."""
        from src.worker_queue import WorkerQueue

        def handler_com_progresso(payload, update_progress):
            update_progress(50.0, "Metade")
            update_progress(100.0, "Pronto")
            return {"ok": True}

        q = WorkerQueue(num_workers=1)
        q.register_handler("progress_test", handler_com_progresso)
        q.start()

        try:
            task_id = q.enqueue("progress_test", {})

            deadline = time.time() + 30
            while time.time() < deadline:
                q.process_results()
                s = q.status(task_id)
                if s and s["status"] == "completed":
                    break
                time.sleep(0.1)

            final = q.status(task_id)
            assert final["status"] == "completed"
            assert final["progresso"] == 100.0
        finally:
            q.shutdown()

    def test_shutdown_graceful(self):
        """Shutdown não quebra tasks em andamento."""
        from src.worker_queue import WorkerQueue

        q = WorkerQueue(num_workers=1)
        q.register_handler("test", lambda p, up: {"ok": True})
        q.start()
        q.enqueue("test", {})

        # Aguarda um pouco e desliga
        time.sleep(0.5)
        q.shutdown(wait=True, timeout=5)

        # Não deve lançar exceção
        assert True

    def test_enqueue_sem_start(self):
        """Enqueue sem start lança erro."""
        from src.worker_queue import WorkerQueue

        q = WorkerQueue(num_workers=1)
        with pytest.raises(RuntimeError, match="não está rodando"):
            q.enqueue("test", {})


# ═══════════════════════════════════════════════════════════════════════════
# Testes: EmailService
# ═══════════════════════════════════════════════════════════════════════════


class TestEmailService:
    """Testes do serviço de email."""

    def test_enviar_modo_log(self, email_svc):
        """Envia email em modo log."""
        msg = email_svc.enviar(
            para="teste@exemplo.com",
            assunto="Teste",
            corpo="Corpo do email de teste",
            async_mode=False,
        )
        assert msg.status == "enviado"
        assert msg.para == "teste@exemplo.com"
        assert msg.assunto == "Teste"

    def test_enviar_com_html(self, email_svc):
        """Envia email com corpo HTML."""
        msg = email_svc.enviar(
            para="teste@exemplo.com",
            assunto="Teste HTML",
            corpo="Versão texto",
            corpo_html="<h1>Versão HTML</h1>",
            async_mode=False,
        )
        assert msg.status == "enviado"
        assert msg.corpo_html == "<h1>Versão HTML</h1>"

    def test_enviar_com_cc(self, email_svc):
        """Envia email com CC."""
        msg = email_svc.enviar(
            para="principal@exemplo.com",
            assunto="Com CC",
            corpo="Teste",
            cc=["cc1@exemplo.com", "cc2@exemplo.com"],
            async_mode=False,
        )
        assert msg.status == "enviado"
        assert len(msg.cc) == 2

    def test_alerta_job_concluido(self, email_svc):
        """Template de alerta de job concluído."""
        msg = email_svc.enviar_alerta_job_concluido(
            para="admin@exemplo.com",
            job_tipo="ecd_import",
            job_id=42,
            resultado={"contas": 100},
            async_mode=False,
        )
        assert msg.status == "enviado"
        assert "concluído" in msg.assunto.lower()
        assert "42" in msg.corpo

    def test_alerta_job_falhou(self, email_svc):
        """Template de alerta de job com falha."""
        msg = email_svc.enviar_alerta_job_falhou(
            para="admin@exemplo.com",
            job_tipo="ecd_import",
            job_id=99,
            erro="Arquivo corrompido",
            async_mode=False,
        )
        assert msg.status == "enviado"
        assert "falhou" in msg.assunto.lower()
        assert "Arquivo corrompido" in msg.corpo

    def test_relatorio_agendado(self, email_svc):
        """Template de relatório agendado."""
        msg = email_svc.enviar_relatorio_agendado(
            para="cliente@exemplo.com",
            empresa="Empresa Teste",
            periodo="01/2024 a 12/2024",
            async_mode=False,
        )
        assert msg.status == "enviado"
        assert "Empresa Teste" in msg.corpo
        assert "01/2024" in msg.corpo

    def test_historico(self, email_svc):
        """Histórico de envios."""
        email_svc.enviar("a@x.com", "Teste 1", "Corpo 1", async_mode=False)
        email_svc.enviar("b@x.com", "Teste 2", "Corpo 2", async_mode=False)

        hist = email_svc.historico()
        assert len(hist) == 2
        assert hist[0]["assunto"] == "Teste 1"
        assert hist[1]["assunto"] == "Teste 2"

    def test_stats(self, email_svc):
        """Estatísticas de envio."""
        email_svc.enviar("a@x.com", "Teste", "Corpo", async_mode=False)

        stats = email_svc.stats()
        assert stats["total"] >= 1
        assert stats["enviados"] >= 1
        assert stats["modo"] == "log"

    def test_envio_assincrono(self, email_svc):
        """Envio assíncrono não bloqueia."""
        msg = email_svc.enviar(
            para="teste@exemplo.com",
            assunto="Async",
            corpo="Teste assíncrono",
            async_mode=True,
        )
        # Imediatamente após chamada, pode ainda estar pendente
        assert msg.status in ("pendente", "enviado")

        # Aguarda conclusão
        time.sleep(0.5)
        assert msg.status == "enviado"


# ═══════════════════════════════════════════════════════════════════════════
# Testes: Integração — Worker Queue + Email
# ═══════════════════════════════════════════════════════════════════════════


class TestIntegracaoFase15:
    """Testes de integração entre os novos componentes."""

    def test_worker_com_email_callback(self, email_svc):
        """Worker dispara email ao concluir task."""
        from src.worker_queue import WorkerQueue

        alertas = []

        def on_complete(task):
            email_svc.enviar_alerta_job_concluido(
                para="admin@exemplo.com",
                job_tipo=task.tipo,
                job_id=0,
                resultado=task.resultado,
            )
            alertas.append("enviado")

        q = WorkerQueue(num_workers=1)
        q.register_handler("test", lambda p, up: {"ok": True})
        q.on_complete(on_complete)
        q.start()

        try:
            q.enqueue("test", {})

            deadline = time.time() + 60
            while time.time() < deadline:
                q.process_results()
                if alertas:
                    break
                time.sleep(0.05)

            # A mensagem precisa distinguir "worker não subiu" de "callback não
            # disparou": em runner de CI de 2 núcleos, spawnar o processo e
            # reimportar a aplicação leva bem mais que numa máquina ociosa.
            assert len(alertas) == 1, (
                f"nenhum alerta em 60s — workers vivos: " f"{[w.is_alive() for w in q._workers]}"
            )
            hist = email_svc.historico()
            assert len(hist) >= 1
        finally:
            q.shutdown()

    def test_cache_com_worker(self):
        """Worker pode usar cache internamente (cache local ao worker).

        Como workers rodam em processos separados, o cache em memória
        é local a cada worker. Verificamos que ambas as tasks completam.
        Cache entre processos requer Redis (testado separadamente).
        """
        from src.cache.redis_cache import RedisCacheService
        from src.worker_queue import WorkerQueue

        # Cache local ao handler (cada worker tem o seu)
        def handler_com_cache(payload, update_progress):
            cache = RedisCacheService(max_entries=100, prefix="worker:")
            key = f"result:{payload.get('id')}"
            cached = cache.get(key)
            if cached:
                return {**cached, "from_cache": True}
            result = {"processado": True, "id": payload.get("id")}
            cache.set(key, result, ttl=60)
            return {**result, "from_cache": False}

        q = WorkerQueue(num_workers=1)
        q.register_handler("cache_test", handler_com_cache)
        q.start()

        try:
            # Primeira chamada — processa (from_cache=False)
            id1 = q.enqueue("cache_test", {"id": "abc"})
            deadline = time.time() + 30
            while time.time() < deadline:
                q.process_results()
                s = q.status(id1)
                if s and s["status"] == "completed":
                    break
                time.sleep(0.1)
            r1 = q.status(id1)
            assert r1["status"] == "completed"
            assert r1["resultado"]["from_cache"] is False

            # Segunda chamada no mesmo worker — usa cache
            id2 = q.enqueue("cache_test", {"id": "abc"})
            deadline = time.time() + 30
            while time.time() < deadline:
                q.process_results()
                s = q.status(id2)
                if s and s["status"] == "completed":
                    break
                time.sleep(0.1)
            r2 = q.status(id2)
            assert r2["status"] == "completed"
            # Cache é local ao worker — pode ou não ser hit dependendo
            # se o mesmo worker pegou a task
            assert r2["resultado"]["processado"] is True
        finally:
            q.shutdown()
