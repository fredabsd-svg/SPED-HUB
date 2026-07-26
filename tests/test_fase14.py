"""Testes da Fase 14 — Importação Assíncrona, Cache e Benchmark.

Cobre:
  1. AsyncJobService — CRUD, progresso, polling, limpeza
  2. AsyncJob Model — criação, status, transições
  3. CacheService — get/set, TTL, invalidação, estatísticas
  4. Upload Assíncrono — endpoint, job tracking, resultado
  5. Benchmark — geração de ECD sintética, performance do parser
"""

import datetime
import json
import os
import tempfile
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from src.db.models import (
    AsyncJob,
    criar_engine,
    get_session,
    init_db,
)
from src.async_jobs import (
    AsyncJobService,
    JobInfo,
    JobStatus,
    get_async_job_service,
    init_async_job_service,
)
from src.cache import (
    CacheService,
    cached,
    get_cache,
    init_cache,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def db_path():
    """Cria banco temporário para cada teste."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="sped_test_fase14_")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def job_svc(db_path):
    """Serviço de jobs com banco limpo."""
    svc = AsyncJobService(db_path)
    return svc


@pytest.fixture
def cache_svc():
    """Cache limpo para cada teste."""
    svc = CacheService(max_entries=100, cleanup_interval=9999)
    return svc


def _authenticated_client(app, db_path: str, email: str):
    """Cria cliente autenticado para as APIs internas do dashboard."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    register = client.post(
        "/api/register",
        data={"email": email, "nome": "Teste Fase 14", "senha": "senha123"},
    )
    assert register.status_code == 200
    login = client.post(
        "/api/login",
        data={"email": email, "senha": "senha123"},
    )
    assert login.status_code == 200
    return client


# ═══════════════════════════════════════════════════════════════════════════
# Testes: AsyncJobService
# ═══════════════════════════════════════════════════════════════════════════


class TestAsyncJobService:
    """Testes do serviço de jobs assíncronos."""

    def test_criar_job(self, job_svc):
        """Cria um job com status pending."""
        job = job_svc.criar(tipo="ecd_import", parametros={"arquivo": "test.txt"})
        assert job.id > 0
        assert job.status == JobStatus.PENDING.value
        assert job.tipo == "ecd_import"
        assert job.progresso == 0.0

    def test_atualizar_progresso(self, job_svc):
        """Atualiza progresso e transita para processing."""
        job = job_svc.criar(tipo="test")
        job_svc.atualizar_progresso(job.id, 50.0, "Metade do caminho")

        info = job_svc.obter(job.id)
        assert info.status == JobStatus.PROCESSING.value
        assert info.progresso == 50.0
        assert info.mensagem == "Metade do caminho"

    def test_concluir_job(self, job_svc):
        """Conclui job com resultado."""
        job = job_svc.criar(tipo="test")
        job_svc.concluir(job.id, resultado={"ecd_id": 42, "contas": 100})

        info = job_svc.obter(job.id)
        assert info.status == JobStatus.COMPLETED.value
        assert info.progresso == 100.0
        assert info.resultado == {"ecd_id": 42, "contas": 100}
        assert info.concluido_em is not None

    def test_falhar_job(self, job_svc):
        """Marca job como falho com mensagem de erro."""
        job = job_svc.criar(tipo="test")
        job_svc.falhar(job.id, "Arquivo corrompido")

        info = job_svc.obter(job.id)
        assert info.status == JobStatus.FAILED.value
        assert info.erro == "Arquivo corrompido"
        assert "Falha" in info.mensagem

    def test_obter_inexistente(self, job_svc):
        """Retorna None para job inexistente."""
        assert job_svc.obter(99999) is None

    def test_listar_todos(self, job_svc):
        """Lista todos os jobs em ordem decrescente."""
        for i in range(3):
            job_svc.criar(tipo=f"test_{i}")

        jobs = job_svc.listar()
        assert len(jobs) == 3
        # Ordem decrescente por criação
        assert jobs[0].tipo == "test_2"

    def test_listar_por_status(self, job_svc):
        """Filtra jobs por status."""
        j1 = job_svc.criar(tipo="a")
        j2 = job_svc.criar(tipo="b")
        job_svc.concluir(j1.id)
        job_svc.falhar(j2.id, "erro de teste")

        pending = job_svc.listar(status=JobStatus.PENDING.value)
        completed = job_svc.listar(status=JobStatus.COMPLETED.value)
        failed = job_svc.listar(status=JobStatus.FAILED.value)

        assert len(pending) == 0
        assert len(completed) == 1
        assert len(failed) == 1

    def test_limpar_antigos(self, job_svc):
        """Remove jobs concluídos/falhos antigos."""
        j1 = job_svc.criar(tipo="old")
        j2 = job_svc.criar(tipo="new")

        job_svc.concluir(j1.id)
        job_svc.concluir(j2.id)

        # Força data antiga no j1 via SQL direto
        engine = criar_engine(job_svc.db_path)
        init_db(engine)
        sess = get_session(engine)
        try:
            db_job = sess.get(AsyncJob, j1.id)
            db_job.concluido_em = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=48)
            sess.commit()
        finally:
            sess.close()

        removidos = job_svc.limpar_antigos(horas=24)
        assert removidos == 1

        jobs = job_svc.listar()
        assert len(jobs) == 1
        assert jobs[0].id == j2.id

    def test_progresso_clamp(self, job_svc):
        """Progresso é limitado entre 0 e 100."""
        job = job_svc.criar(tipo="test")
        job_svc.atualizar_progresso(job.id, 150.0)
        assert job_svc.obter(job.id).progresso == 100.0

        job_svc.atualizar_progresso(job.id, -10.0)
        assert job_svc.obter(job.id).progresso == 0.0


class TestAsyncJobModel:
    """Testes do modelo AsyncJob."""

    def test_criar_modelo(self, db_path):
        """Cria registro diretamente no banco."""
        engine = criar_engine(db_path)
        init_db(engine)
        sess = get_session(engine)
        try:
            job = AsyncJob(
                tipo="ecd_import",
                status="pending",
                progresso=0.0,
                mensagem="Aguardando...",
            )
            sess.add(job)
            sess.commit()

            assert job.id > 0
            assert job.tipo == "ecd_import"
            assert job.criado_em is not None
        finally:
            sess.close()

    def test_parametros_json(self, db_path):
        """Parametros são serializados como JSON."""
        engine = criar_engine(db_path)
        init_db(engine)
        sess = get_session(engine)
        try:
            job = AsyncJob(
                tipo="test",
                status="pending",
                parametros=json.dumps({"arquivo": "test.txt", "tamanho": 1024}),
            )
            sess.add(job)
            sess.commit()

            loaded = sess.get(AsyncJob, job.id)
            params = json.loads(loaded.parametros)
            assert params["arquivo"] == "test.txt"
            assert params["tamanho"] == 1024
        finally:
            sess.close()

    def test_resultado_json(self, db_path):
        """Resultado é serializado como JSON."""
        engine = criar_engine(db_path)
        init_db(engine)
        sess = get_session(engine)
        try:
            job = AsyncJob(
                tipo="test",
                status="completed",
                resultado=json.dumps({"ecd_id": 42}),
            )
            sess.add(job)
            sess.commit()

            loaded = sess.get(AsyncJob, job.id)
            result = json.loads(loaded.resultado)
            assert result["ecd_id"] == 42
        finally:
            sess.close()


# ═══════════════════════════════════════════════════════════════════════════
# Testes: CacheService
# ═══════════════════════════════════════════════════════════════════════════


class TestCacheService:
    """Testes do CacheService."""

    def test_set_get(self, cache_svc):
        """Armazena e recupera valor."""
        cache_svc.set("key1", "value1")
        assert cache_svc.get("key1") == "value1"

    def test_get_inexistente(self, cache_svc):
        """Retorna None para chave inexistente."""
        assert cache_svc.get("nao_existe") is None

    def test_ttl_expira(self, cache_svc):
        """Valor expira após TTL."""
        cache_svc.set("key", "value", ttl=1)
        assert cache_svc.get("key") == "value"

        time.sleep(1.2)
        assert cache_svc.get("key") is None

    def test_delete(self, cache_svc):
        """Remove entrada específica."""
        cache_svc.set("key", "value")
        assert cache_svc.delete("key") is True
        assert cache_svc.get("key") is None
        assert cache_svc.delete("key") is False

    def test_invalidate_prefix(self, cache_svc):
        """Invalida entradas por prefixo."""
        cache_svc.set("kpi:ativo", 1000)
        cache_svc.set("kpi:passivo", 500)
        cache_svc.set("balanco:ativo", 2000)

        count = cache_svc.invalidate_prefix("kpi:")
        assert count == 2
        assert cache_svc.get("kpi:ativo") is None
        assert cache_svc.get("kpi:passivo") is None
        assert cache_svc.get("balanco:ativo") == 2000

    def test_clear(self, cache_svc):
        """Limpa todo o cache."""
        cache_svc.set("a", 1)
        cache_svc.set("b", 2)
        assert cache_svc.clear() == 2
        assert cache_svc.get("a") is None
        assert cache_svc.get("b") is None

    def test_stats(self, cache_svc):
        """Estatísticas do cache."""
        cache_svc.set("a", 1)
        cache_svc.get("a")  # hit
        cache_svc.get("b")  # miss

        stats = cache_svc.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["sets"] == 1
        assert stats["entries"] == 1
        assert stats["hit_rate_pct"] == 50.0

    def test_max_entries(self):
        """Remove entrada mais antiga quando atinge limite."""
        svc = CacheService(max_entries=3, cleanup_interval=9999)
        svc.set("a", 1, ttl=100)
        svc.set("b", 2, ttl=100)
        svc.set("c", 3, ttl=100)
        svc.set("d", 4, ttl=100)  # deve remover 'a'

        assert svc.get("a") is None
        assert svc.get("d") == 4
        assert svc.stats()["entries"] == 3

    def test_thread_safety(self, cache_svc):
        """Operações concorrentes não quebram o cache."""
        import threading

        def worker(start_idx):
            for i in range(start_idx, start_idx + 50):
                cache_svc.set(f"key_{i}", i)
                _ = cache_svc.get(f"key_{i}")

        threads = [threading.Thread(target=worker, args=(i * 50,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = cache_svc.stats()
        assert stats["sets"] == 200
        assert stats["entries"] <= 200  # pode ter evictions


class TestCachedDecorator:
    """Testes do decorator @cached."""

    def test_cacheia_resultado(self, cache_svc):
        """Função decorada cacheia resultado."""
        call_count = 0

        @cached(ttl=60)
        def pesada(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        # Força uso do cache de teste
        import src.cache as cache_mod
        cache_mod._cache_service = cache_svc

        assert pesada(5) == 10
        assert call_count == 1

        # Segunda chamada usa cache
        assert pesada(5) == 10
        assert call_count == 1

        # Argumento diferente não usa cache
        assert pesada(10) == 20
        assert call_count == 2

    def test_prefixo_isola_cache(self, cache_svc):
        """Prefixos diferentes isolam entradas."""
        call_a = 0
        call_b = 0

        @cached(ttl=60, prefix="a:")
        def func_a(x):
            nonlocal call_a
            call_a += 1
            return x

        @cached(ttl=60, prefix="b:")
        def func_b(x):
            nonlocal call_b
            call_b += 1
            return x

        import src.cache as cache_mod
        cache_mod._cache_service = cache_svc

        assert func_a(1) == 1
        assert func_b(1) == 1
        assert call_a == 1
        assert call_b == 1


# ═══════════════════════════════════════════════════════════════════════════
# Testes: Upload Assíncrono (E2E)
# ═══════════════════════════════════════════════════════════════════════════


class TestUploadAsync:
    """Testes E2E do upload assíncrono."""

    def test_upload_async_cria_job(self, db_path):
        """Upload assíncrono cria job e retorna job_id."""
        import os
        os.environ["SPED_HUB_DB"] = db_path

        from src.dashboard.app import app
        from src.auth import init_auth
        from src.audit import init_audit_service
        from src.ratelimit import init_limiter
        from src.async_jobs import init_async_job_service
        init_auth(db_path)
        init_audit_service(db_path)
        init_limiter(db_path)
        init_async_job_service(db_path)

        client = _authenticated_client(app, db_path, "async@fase14.test")

        # Cria arquivo ECD de teste
        ecd_content = (
            "|0000|LECD|01012024|31122024|EMPRESA TESTE LTDA|00123456000199|SP||1234567||0|0|1|0|0|E||1|0||\n"
            "|I001|0|\n"
            "|I010|G|009|\n"
            "|I030|01012024|31122024|A|\n"
            "|I050|01012024|01|S|1|1||ATIVO|\n"
            "|I050|01012024|01|A|3|1.1|1|CAIXA|\n"
            "|I050|01012024|02|S|1|2||PASSIVO|\n"
            "|I050|01012024|02|A|3|2.1|2|FORNECEDORES|\n"
            "|I050|01012024|03|S|1|3||PL|\n"
            "|I050|01012024|03|A|2|3.1|3|CAPITAL|\n"
            "|I075|001|VENDA|\n"
            "|I150|01012024|31122024|\n"
            "|I155|1.1||50000.00|D|100000.00|80000.00|70000.00|D|\n"
            "|I155|2.1||30000.00|C|50000.00|60000.00|40000.00|C|\n"
            "|I155|3.1||20000.00|C|0.00|0.00|20000.00|C|\n"
            "|I200|1|15012024|50000.00|N||\n"
            "|I250|1.1||50000.00|D|||VENDA|001|\n"
            "|I250|4.1||50000.00|C||||\n"
            "|I350|31122024|\n"
            "|I355|4.1||50000.00|C|\n"
            "|I990|99|\n"
            "|9001|0|\n"
            "|9900|0000|1|\n"
            "|9900|I001|1|\n"
            "|9900|I010|1|\n"
            "|9900|I030|1|\n"
            "|9900|I050|6|\n"
            "|9900|I075|1|\n"
            "|9900|I150|1|\n"
            "|9900|I155|3|\n"
            "|9900|I200|1|\n"
            "|9900|I250|2|\n"
            "|9900|I350|1|\n"
            "|9900|I355|1|\n"
            "|9900|I990|1|\n"
            "|9900|9001|1|\n"
            "|9900|9900|15|\n"
            "|9999|33|\n"
        )

        resp = client.post(
            "/api/upload-async",
            files={"file": ("test_async.txt", ecd_content.encode(), "text/plain")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "job_id" in data
        assert data["job_id"] > 0

        job_id = data["job_id"]

        # Poll até concluir (timeout 10s)
        import time
        deadline = time.time() + 10
        while time.time() < deadline:
            resp = client.get(f"/api/jobs/{job_id}")
            assert resp.status_code == 200
            job_data = resp.json()
            if job_data["status"] in ("completed", "failed"):
                break
            time.sleep(0.2)

        assert job_data["status"] == "completed"
        assert job_data["progresso"] == 100.0
        assert job_data["resultado"] is not None
        assert job_data["resultado"]["ecd_id"] > 0
        assert job_data["resultado"]["contas"] == 6

        del os.environ["SPED_HUB_DB"]

    def test_job_inexistente(self, db_path):
        """GET /api/jobs/999 retorna 404."""
        import os
        os.environ["SPED_HUB_DB"] = db_path

        from src.dashboard.app import app
        from src.auth import init_auth
        from src.audit import init_audit_service
        from src.ratelimit import init_limiter
        from src.async_jobs import init_async_job_service
        init_auth(db_path)
        init_audit_service(db_path)
        init_limiter(db_path)
        init_async_job_service(db_path)

        client = _authenticated_client(app, db_path, "missing-job@fase14.test")

        resp = client.get("/api/jobs/99999")
        assert resp.status_code == 404

        del os.environ["SPED_HUB_DB"]

    def test_listar_jobs(self, db_path):
        """GET /api/jobs lista jobs."""
        import os
        os.environ["SPED_HUB_DB"] = db_path

        from src.dashboard.app import app
        from src.auth import init_auth
        from src.audit import init_audit_service
        from src.ratelimit import init_limiter
        from src.async_jobs import init_async_job_service
        init_auth(db_path)
        init_audit_service(db_path)
        init_limiter(db_path)
        init_async_job_service(db_path)

        client = _authenticated_client(app, db_path, "jobs@fase14.test")

        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        assert "dados" in resp.json()
        assert "total" in resp.json()

        del os.environ["SPED_HUB_DB"]

    def test_cache_stats_endpoint(self, db_path):
        """GET /api/cache/stats retorna estatísticas."""
        import os
        os.environ["SPED_HUB_DB"] = db_path

        from src.dashboard.app import app
        from src.auth import init_auth
        from src.audit import init_audit_service
        from src.ratelimit import init_limiter
        init_auth(db_path)
        init_audit_service(db_path)
        init_limiter(db_path)

        client = _authenticated_client(app, db_path, "cache@fase14.test")

        resp = client.get("/api/cache/stats")
        assert resp.status_code == 200
        stats = resp.json()
        assert "entries" in stats
        assert "hits" in stats
        assert "hit_rate_pct" in stats

        del os.environ["SPED_HUB_DB"]


# ═══════════════════════════════════════════════════════════════════════════
# Testes: Benchmark — ECD Sintética e Performance
# ═══════════════════════════════════════════════════════════════════════════


class TestBenchmark:
    """Testes de performance e geração de ECDs sintéticas."""

    def test_gerar_ecd_sintetica_pequena(self):
        """Gera ECD sintética de ~1000 registros."""
        from src.parsers.ecd import ECDParser

        linhas = []
        linhas.append("|0000|LECD|01012024|31122024|EMPRESA BENCH LTDA|00123456000199|SP||1234567||0|0|1|0|0|E||1|0||")
        linhas.append("|I001|0|")
        linhas.append("|I010|G|009|")
        linhas.append("|I030|01012024|31122024|A|")

        # 100 contas
        for i in range(100):
            linhas.append(f"|I050|01012024|01|A|3|{i+1}|1|CONTA_{i+1}|")

        # 5 históricos
        for i in range(5):
            linhas.append(f"|I075|{i+1:03d}|HISTORICO_{i+1}|")

        linhas.append("|I150|01012024|31122024|")

        # 100 saldos
        for i in range(100):
            linhas.append(f"|I155|{i+1}||1000.00|D|5000.00|4000.00|2000.00|D|")

        # 200 lançamentos com 2 partidas cada
        for i in range(200):
            linhas.append(f"|I200|{i+1}|15012024|1000.00|N||")
            linhas.append(f"|I250|{(i%100)+1}||500.00|D|||LANC_{i+1}|001|")
            linhas.append(f"|I250|{((i+50)%100)+1}||500.00|C||||")

        linhas.append("|I350|31122024|")
        for i in range(10):
            linhas.append(f"|I355|{i+1}||5000.00|C|")

        linhas.append("|I990|99|")
        linhas.append("|9001|0|")
        linhas.append(f"|9900|0000|1|")
        linhas.append(f"|9900|I001|1|")
        linhas.append(f"|9900|I010|1|")
        linhas.append(f"|9900|I030|1|")
        linhas.append(f"|9900|I050|100|")
        linhas.append(f"|9900|I075|5|")
        linhas.append(f"|9900|I150|1|")
        linhas.append(f"|9900|I155|100|")
        linhas.append(f"|9900|I200|200|")
        linhas.append(f"|9900|I250|400|")
        linhas.append(f"|9900|I350|1|")
        linhas.append(f"|9900|I355|10|")
        linhas.append(f"|9900|I990|1|")
        linhas.append(f"|9900|9001|1|")
        linhas.append(f"|9900|9900|15|")
        total = len(linhas)
        linhas.append(f"|9999|{total}|")

        content = "\n".join(linhas)

        # Salva em arquivo temporário
        fd, path = tempfile.mkstemp(suffix=".txt", prefix="bench_ecd_")
        os.close(fd)
        try:
            Path(path).write_text(content, encoding="utf-8")

            parser = ECDParser()

            # Conta registros
            t0 = time.time()
            contagem = parser.contar_registros(Path(path))
            t_contar = time.time() - t0
            assert contagem["I050"] == 100
            assert contagem["I200"] == 200
            assert contagem["I250"] == 400
            assert t_contar < 5.0, f"Contagem demorou {t_contar:.2f}s"

            # Parse completo
            t0 = time.time()
            registros = parser.parse_todos(Path(path))
            t_parse = time.time() - t0
            assert len(registros) > 800
            assert t_parse < 5.0, f"Parse demorou {t_parse:.2f}s"

            # Parse em lotes
            t0 = time.time()
            lotes = list(parser.parse_em_lotes(Path(path), tamanho_lote=200))
            t_lotes = time.time() - t0
            assert len(lotes) >= 4
            assert t_lotes < 5.0, f"Parse em lotes demorou {t_lotes:.2f}s"

        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_gerar_ecd_sintetica_media(self):
        """Gera ECD sintética de ~5000 registros e testa performance."""
        from src.parsers.ecd import ECDParser

        linhas = []
        linhas.append("|0000|LECD|01012024|31122024|EMPRESA MEDIA LTDA|00123456000199|SP||1234567||0|0|1|0|0|E||1|0||")
        linhas.append("|I001|0|")
        linhas.append("|I010|G|009|")
        linhas.append("|I030|01012024|31122024|A|")

        # 500 contas
        for i in range(500):
            linhas.append(f"|I050|01012024|01|A|3|{i+1}|1|CONTA_{i+1}|")

        linhas.append("|I150|01012024|31122024|")

        # 500 saldos
        for i in range(500):
            linhas.append(f"|I155|{i+1}||1000.00|D|5000.00|4000.00|2000.00|D|")

        # 1000 lançamentos com 2 partidas cada
        for i in range(1000):
            linhas.append(f"|I200|{i+1}|15012024|1000.00|N||")
            linhas.append(f"|I250|{(i%500)+1}||500.00|D|||LANC_{i+1}|001|")
            linhas.append(f"|I250|{((i+250)%500)+1}||500.00|C||||")

        linhas.append("|I350|31122024|")
        for i in range(50):
            linhas.append(f"|I355|{i+1}||5000.00|C|")

        linhas.append("|I990|99|")
        linhas.append("|9001|0|")
        linhas.append(f"|9999|{len(linhas)}|")

        content = "\n".join(linhas)

        fd, path = tempfile.mkstemp(suffix=".txt", prefix="bench_media_")
        os.close(fd)
        try:
            Path(path).write_text(content, encoding="utf-8")

            parser = ECDParser()

            # Conta registros
            t0 = time.time()
            contagem = parser.contar_registros(Path(path))
            t_contar = time.time() - t0
            assert contagem["I050"] == 500
            assert contagem["I200"] == 1000
            assert contagem["I250"] == 2000
            assert t_contar < 10.0, f"Contagem demorou {t_contar:.2f}s"

            # Parse em lotes (modo recomendado para arquivos grandes)
            t0 = time.time()
            total_regs = 0
            for lote in parser.parse_em_lotes(Path(path), tamanho_lote=500):
                total_regs += len(lote)
            t_lotes = time.time() - t0
            assert total_regs > 4000
            assert t_lotes < 10.0, f"Parse em lotes demorou {t_lotes:.2f}s"

        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_parser_streaming_memoria(self):
        """Parser streaming não deve consumir memória excessiva."""
        from src.parsers.ecd import ECDParser

        # Gera arquivo maior (~2000 registros)
        linhas = []
        linhas.append("|0000|LECD|01012024|31122024|EMPRESA STREAM LTDA|00123456000199|SP||1234567||0|0|1|0|0|E||1|0||")
        linhas.append("|I001|0|")
        linhas.append("|I010|G|009|")
        linhas.append("|I030|01012024|31122024|A|")

        for i in range(300):
            linhas.append(f"|I050|01012024|01|A|3|{i+1}|1|CONTA_{i+1}|")

        linhas.append("|I150|01012024|31122024|")

        for i in range(300):
            linhas.append(f"|I155|{i+1}||1000.00|D|5000.00|4000.00|2000.00|D|")

        for i in range(500):
            linhas.append(f"|I200|{i+1}|15012024|1000.00|N||")
            linhas.append(f"|I250|{(i%300)+1}||500.00|D|||LANC_{i+1}|001|")
            linhas.append(f"|I250|{((i+150)%300)+1}||500.00|C||||")

        linhas.append("|I350|31122024|")
        linhas.append("|I990|99|")
        linhas.append("|9001|0|")
        linhas.append(f"|9999|{len(linhas)}|")

        content = "\n".join(linhas)

        fd, path = tempfile.mkstemp(suffix=".txt", prefix="bench_stream_")
        os.close(fd)
        try:
            Path(path).write_text(content, encoding="utf-8")

            parser = ECDParser()

            # Streaming: processa sem acumular tudo
            t0 = time.time()
            count = 0
            for registro in parser.parse(Path(path)):
                count += 1
            t_stream = time.time() - t0

            assert count > 2000
            assert t_stream < 10.0, f"Streaming demorou {t_stream:.2f}s"

        finally:
            try:
                os.unlink(path)
            except OSError:
                pass