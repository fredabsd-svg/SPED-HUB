"""Isolamento entre testes de estado global de processo.

O limitador por IP (Etapa 5) é um singleton de processo, como precisa ser: o
contador tem de valer para todas as requisições. Numa suíte, porém, todos os
testes chegam do mesmo "IP" do TestClient, então sem zerar entre um e outro a
cota se esgota e testes começam a receber 429 por causa dos anteriores.
"""

from __future__ import annotations

import pytest

from src.ratelimit import get_ip_limiter


@pytest.fixture(autouse=True)
def _zerar_rate_limit_por_ip():
    get_ip_limiter().reset()
    yield
    get_ip_limiter().reset()


def url_com_senha(engine) -> str:
    """URL da engine **incluindo** a senha, para serviços que recebem `db_path`.

    `str(engine.url)` mascara a senha como `***`.  Isso passa despercebido em
    SQLite (não tem senha) e em Postgres com autenticação `trust`, e falha com
    autenticação por senha — que é o caso do CI e de qualquer produção.
    """
    return engine.url.render_as_string(hide_password=False)
