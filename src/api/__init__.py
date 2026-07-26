"""API REST externa versionada — Fase 7.

Autenticação por API Key (X-API-Key header).
Rotas versionadas: /api/v1/...
OpenAPI documentada com tags e schemas.
"""

import datetime
import hashlib
import secrets
import logging
from functools import wraps
from typing import Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import ApiKey, criar_engine, get_session, init_db

logger = logging.getLogger("sped-hub.api")


# ── API Key Auth ────────────────────────────────────────────────────────────


def _hash_key(key: str) -> str:
    """Hash SHA-256 da chave para armazenamento seguro."""
    return hashlib.sha256(key.encode()).hexdigest()


def gerar_api_key() -> tuple[str, str]:
    """Gera uma nova API Key (prefixo + segredo).

    Returns:
        (chave_completa, hash_armazenado)
        Ex: "spd_abc123def456...", "sha256hash..."
    """
    segredo = secrets.token_hex(32)
    chave = f"spd_{segredo}"
    return chave, _hash_key(chave)


def verificar_api_key(chave: str, hash_armazenado: str) -> bool:
    """Verifica se a chave confere com o hash armazenado."""
    return _hash_key(chave) == hash_armazenado


async def get_api_key(request: Request) -> str | None:
    """Extrai API Key do header X-API-Key."""
    return request.headers.get("X-API-Key")


async def requer_api_key(request: Request, db_path: str = "sped_hub.db"):
    """Dependency que valida API Key e retorna o registro."""
    chave = await get_api_key(request)
    if not chave:
        raise HTTPException(status_code=401, detail="X-API-Key header obrigatório")

    engine = criar_engine(db_path)
    session = get_session(engine)
    try:
        hash_chave = _hash_key(chave)
        api_key = session.execute(
            select(ApiKey).where(
                ApiKey.key_hash == hash_chave,
                ApiKey.ativo == True,
            )
        ).scalar_one_or_none()

        if not api_key:
            raise HTTPException(status_code=403, detail="API Key inválida ou inativa")

        if api_key.expira_em and api_key.expira_em < datetime.datetime.now(datetime.UTC):
            raise HTTPException(status_code=403, detail="API Key expirada")

        # Registra uso
        api_key.ultimo_uso = datetime.datetime.now(datetime.UTC)
        api_key.total_requisicoes = (api_key.total_requisicoes or 0) + 1
        session.commit()

        return api_key
    finally:
        session.close()