"""`/api/health/full` é público: não pode travar a aplicação nem vazar segredo.

A auditoria encontrou, na mesma rota anônima:

* um `RedisCacheService` construído a cada requisição, dentro de um handler
  `async def` — conexão e `ping` síncronos, com timeout de 2 s, rodando no
  event loop. Com o Redis fora do ar, cada chamada anônima congelava a
  aplicação inteira por ~2 s;
* o construtor logando `Redis conectado: <REDIS_URL>` com a senha.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
import types

import httpx
import pytest
from fastapi.testclient import TestClient

import src.cache.redis_cache as redis_cache
from src.cache.redis_cache import RedisCacheService, cache_compartilhado, url_sem_senha
from src.settings import reset_settings_cache

SENHA = "S3nhaDoRedis"
URL_COM_SENHA = f"redis://:{SENHA}@redis.interno:6379/0"


class _Coletor(logging.Handler):
    """Handler no PRÓPRIO logger do Redis: vê a mensagem antes de qualquer filtro da raiz."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.mensagens: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.mensagens.append(record.getMessage())


@pytest.fixture
def redis_no_ar(monkeypatch):
    """Um módulo `redis` falso que conecta: é o caminho que loga a URL."""

    class _Cliente:
        def ping(self):
            return True

    falso = types.SimpleNamespace(from_url=lambda url, **kw: _Cliente())
    monkeypatch.setitem(sys.modules, "redis", falso)


@pytest.fixture
def log_do_redis(monkeypatch):
    coletor = _Coletor()
    logger = logging.getLogger("sped-hub.cache.redis")
    monkeypatch.setattr(logger, "level", logging.DEBUG)
    logger.addHandler(coletor)
    yield coletor
    logger.removeHandler(coletor)


class TestSenhaDoRedisNaoVaiParaOLog:
    def test_conexao_loga_a_url_sem_a_senha(self, redis_no_ar, log_do_redis):
        RedisCacheService(redis_url=URL_COM_SENHA)

        texto = "\n".join(log_do_redis.mensagens)
        assert "Redis conectado" in texto, "o teste precisa passar pelo log de conexão"
        assert SENHA not in texto, f"a senha do Redis foi para o log: {texto!r}"
        assert "redis.interno:6379" in texto, "o destino continua no log, para diagnóstico"

    @pytest.mark.parametrize(
        "url,esperado",
        [
            (URL_COM_SENHA, "redis://:***@redis.interno:6379/0"),
            ("redis://app:segredo@redis:6379/1", "redis://app:***@redis:6379/1"),
            ("redis://redis:6379/0", "redis://redis:6379/0"),
        ],
    )
    def test_url_sem_senha(self, url, esperado):
        assert url_sem_senha(url) == esperado


@pytest.fixture
def conexoes(monkeypatch):
    """Conta as conexões ao Redis e simula um Redis fora do ar que demora a recusar."""
    monkeypatch.setattr(redis_cache, "_compartilhados", {})
    contagem = {"n": 0, "atraso": 0.0}

    def conectar(self):
        contagem["n"] += 1
        time.sleep(contagem["atraso"])
        self._redis_available = False

    monkeypatch.setattr(RedisCacheService, "_connect", conectar)
    return contagem


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'saude.db'}")
    monkeypatch.delenv("SPED_HUB_DB", raising=False)
    reset_settings_cache()
    from src.dashboard.app import app as aplicacao
    from src.ratelimit import get_ip_limiter

    get_ip_limiter().reset()
    yield aplicacao
    get_ip_limiter().reset()
    reset_settings_cache()


class TestHealthFull:
    def test_nao_reconecta_ao_redis_a_cada_chamada(self, app, conexoes):
        """Cada chamada anônima pagava uma conexão nova — 2 s com o Redis fora."""
        cliente = TestClient(app)
        respostas = [cliente.get("/api/health/full") for _ in range(5)]

        assert all(r.status_code == 200 for r in respostas)
        assert respostas[-1].json()["components"]["cache"] == "ok (memory)"
        assert conexoes["n"] == 1, (
            f"{conexoes['n']} conexões ao Redis em 5 chamadas: qualquer anônimo "
            "multiplica o custo da conexão só chamando a rota"
        )

    def test_redis_volta_a_ser_tentado_depois_do_intervalo(self, conexoes, monkeypatch):
        """Reaproveitar não pode significar nunca mais perceber o Redis de volta."""
        cache_compartilhado("redis://x:6379/0", prefix="health:")
        cache_compartilhado("redis://x:6379/0", prefix="health:")
        assert conexoes["n"] == 1

        relogio = time.monotonic() + redis_cache.RECONEXAO_SEGUNDOS + 1
        monkeypatch.setattr(redis_cache.time, "monotonic", lambda: relogio)
        cache_compartilhado("redis://x:6379/0", prefix="health:")

        assert conexoes["n"] == 2

    def test_checagem_lenta_nao_trava_as_outras_requisicoes(self, app, conexoes):
        """A checagem síncrona dentro de `async def` parava o event loop inteiro.

        Enquanto o health espera o Redis (aqui, 0,8 s), outra requisição
        precisa ser atendida normalmente.
        """
        TestClient(app).get("/api/v1/health")  # aquece banco e rotas fora da medição
        conexoes["atraso"] = 0.8

        async def cenario() -> tuple[int, float]:
            transporte = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transporte, base_url="http://localhost"
            ) as cliente:
                inicio = time.perf_counter()
                lenta = asyncio.create_task(cliente.get("/api/health/full"))
                await asyncio.sleep(0.05)
                rapida = await cliente.get("/api/v1/health")
                decorrido = time.perf_counter() - inicio
                await lenta
                return rapida.status_code, decorrido

        status, decorrido = asyncio.run(cenario())

        assert status == 200
        assert decorrido < 0.5, (
            f"/api/v1/health levou {decorrido:.2f}s esperando o /api/health/full: a "
            "checagem do Redis está parando o event loop, e um anônimo trava a "
            "aplicação para todos"
        )
