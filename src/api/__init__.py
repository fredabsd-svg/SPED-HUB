"""API REST externa versionada — Fase 7 + Fase 12 + Fase 13.

Autenticação por API Key (X-API-Key header).
Rotas versionadas: /api/v1/...
OpenAPI documentada com tags e schemas.

Fase 12: +CRUD de API Keys com geração, listagem, revogação e UI.
Fase 13: +Rate limiting, +Logs de auditoria, +Configuração de rate limit por API Key.
"""

import datetime
import hashlib
import hmac
import logging
import secrets

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.audit import get_audit_service
from src.db.models import ApiKey, criar_engine, get_session, init_db
from src.ratelimit import get_limiter
from src.settings import database_reference

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
    return hmac.compare_digest(_hash_key(chave), hash_armazenado)


async def get_api_key(request: Request) -> str | None:
    """Extrai API Key do header X-API-Key."""
    return request.headers.get("X-API-Key")


async def validar_requisicao_api(request: Request, db_path: str):
    """Valida API key; sessões do dashboard também podem consumir a API."""
    chave = await get_api_key(request)
    if not chave:
        from src.auth import get_usuario_atual

        usuario = await get_usuario_atual(request)
        if usuario is not None:
            if usuario.admin:
                return usuario
            raise HTTPException(
                status_code=403,
                detail="Acesso administrativo necessário para a API externa",
            )
        raise HTTPException(status_code=401, detail="X-API-Key header obrigatório")

    engine = criar_engine(db_path)
    session = get_session(engine)
    try:
        hash_chave = _hash_key(chave)
        api_key = session.execute(
            select(ApiKey).where(
                ApiKey.key_hash == hash_chave,
                ApiKey.ativo.is_(True),
            )
        ).scalar_one_or_none()

        if not api_key:
            raise HTTPException(status_code=403, detail="API Key inválida ou inativa")

        if api_key.expira_em:
            expira_em = api_key.expira_em
            if expira_em.tzinfo is None:
                expira_em = expira_em.replace(tzinfo=datetime.UTC)
            if expira_em < datetime.datetime.now(datetime.UTC):
                raise HTTPException(status_code=403, detail="API Key expirada")

        # ── Rate Limiting (Fase 13) ──
        limiter = get_limiter(db_path)
        permitido, info = limiter.verificar(api_key.id)
        if not permitido:
            # Registra tentativa bloqueada
            audit = get_audit_service(db_path)
            audit.registrar(
                acao="api.rate_limited",
                recurso=str(request.url.path),
                api_key_id=api_key.id,
                metodo=request.method,
                ip=request.client.host if request.client else None,
                status_code=429,
                detalhes={"limite": info.limite, "janela": info.janela},
            )
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: {info.limite} requests per {info.janela}s. "
                f"Retry in {info.reset_em}s.",
                headers={
                    "X-RateLimit-Limit": str(info.limite),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(info.reset_em),
                    "Retry-After": str(info.reset_em),
                },
            )

        # Adiciona headers de rate limit no request state para o middleware
        request.state.rate_limit_info = info

        # Registra uso
        api_key.ultimo_uso = datetime.datetime.now(datetime.UTC)
        api_key.total_requisicoes = (api_key.total_requisicoes or 0) + 1
        session.commit()
        session.refresh(api_key)
        session.expunge(api_key)
        return api_key
    finally:
        session.close()


async def requer_api_key(request: Request):
    """Dependência HTTP sem parâmetros de caminho controláveis pelo cliente."""
    return await validar_requisicao_api(
        request,
        database_reference(),
    )


# ── API Key Service (Fase 12) ───────────────────────────────────────────────


class ApiKeyService:
    """Serviço de gerenciamento de API Keys."""

    def __init__(self, db_path: str = "sped_hub.db"):
        self.db_path = db_path

    def _get_session(self) -> Session:
        engine = criar_engine(self.db_path)
        init_db(engine)
        return get_session(engine)

    def criar(self, nome: str, dias_expiracao: int | None = None) -> dict:
        """Cria uma nova API Key.

        Returns:
            dict com id, nome, prefixo, chave_completa (exibida uma única vez),
            criado_em, expira_em.
        """
        chave_completa, key_hash = gerar_api_key()
        prefixo = chave_completa[:11]  # "spd_" + 7 chars

        expira_em = None
        if dias_expiracao:
            expira_em = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
                days=dias_expiracao
            )

        session = self._get_session()
        try:
            api_key = ApiKey(
                nome=nome,
                key_hash=key_hash,
                prefixo=prefixo,
                ativo=True,
                expira_em=expira_em,
            )
            session.add(api_key)
            session.commit()
            session.refresh(api_key)

            # Registra auditoria
            audit = get_audit_service(self.db_path)
            audit.registrar(
                acao="apikey.create",
                recurso=f"API Key #{api_key.id} ({nome})",
                detalhes={"nome": nome, "prefixo": prefixo},
            )

            return {
                "id": api_key.id,
                "nome": api_key.nome,
                "prefixo": api_key.prefixo,
                "chave": chave_completa,
                "criado_em": api_key.criado_em.isoformat() if api_key.criado_em else None,
                "expira_em": api_key.expira_em.isoformat() if api_key.expira_em else None,
                "ativo": api_key.ativo,
            }
        finally:
            session.close()

    def listar(self) -> list[dict]:
        """Lista todas as API Keys (sem expor a chave completa)."""
        session = self._get_session()
        try:
            keys = session.execute(select(ApiKey).order_by(ApiKey.criado_em.desc())).scalars().all()

            return [
                {
                    "id": k.id,
                    "nome": k.nome,
                    "prefixo": k.prefixo,
                    "ativo": k.ativo,
                    "criado_em": k.criado_em.isoformat() if k.criado_em else None,
                    "expira_em": k.expira_em.isoformat() if k.expira_em else None,
                    "ultimo_uso": k.ultimo_uso.isoformat() if k.ultimo_uso else None,
                    "total_requisicoes": k.total_requisicoes or 0,
                }
                for k in keys
            ]
        finally:
            session.close()

    def revogar(self, key_id: int) -> bool:
        """Revoga (desativa) uma API Key."""
        session = self._get_session()
        try:
            k = session.get(ApiKey, key_id)
            if not k:
                return False
            k.ativo = False
            session.commit()

            # Registra auditoria
            audit = get_audit_service(self.db_path)
            audit.registrar(
                acao="apikey.revoke",
                recurso=f"API Key #{key_id} ({k.nome})",
            )

            return True
        finally:
            session.close()

    def excluir(self, key_id: int) -> bool:
        """Exclui permanentemente uma API Key."""
        session = self._get_session()
        try:
            k = session.get(ApiKey, key_id)
            if not k:
                return False
            session.delete(k)
            session.commit()
            return True
        finally:
            session.close()
