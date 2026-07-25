"""Módulo de autenticação — login, registro, sessões e middleware."""

import datetime
import logging
from functools import wraps
from typing import Callable

from fastapi import Cookie, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Sessao, Usuario, UsuarioEmpresa, criar_engine, get_session, init_db

logger = logging.getLogger("sped-hub.auth")

# Duração da sessão: 24 horas
SESSAO_DURACAO_HORAS = 24


class AuthService:
    """Serviço de autenticação."""

    def __init__(self, db_path: str = "sped_hub.db"):
        self.db_path = db_path

    def _get_session(self) -> Session:
        engine = criar_engine(self.db_path)
        init_db(engine)
        return get_session(engine)

    def registrar(self, email: str, nome: str, senha: str) -> Usuario:
        """Registra um novo usuário."""
        session = self._get_session()
        try:
            existente = session.execute(
                select(Usuario).where(Usuario.email == email)
            ).scalar_one_or_none()
            if existente:
                raise ValueError("Email já cadastrado")

            senha_hash, salt = Usuario.hash_senha(senha)
            usuario = Usuario(
                email=email,
                nome=nome,
                senha_hash=senha_hash,
                salt=salt,
            )
            session.add(usuario)
            session.commit()
            session.refresh(usuario)
            return usuario
        finally:
            session.close()

    def login(self, email: str, senha: str, ip: str = "", user_agent: str = "") -> tuple[Usuario, str]:
        """Autentica usuário e retorna token de sessão."""
        session = self._get_session()
        try:
            usuario = session.execute(
                select(Usuario).where(Usuario.email == email)
            ).scalar_one_or_none()

            if not usuario or not usuario.verificar_senha(senha):
                raise ValueError("Email ou senha inválidos")

            if not usuario.ativo:
                raise ValueError("Usuário desativado")

            # Cria sessão
            token = Sessao.gerar_token()
            agora = datetime.datetime.now(datetime.UTC)
            sessao = Sessao(
                usuario_id=usuario.id,
                token=token,
                criado_em=agora,
                expira_em=agora + datetime.timedelta(hours=SESSAO_DURACAO_HORAS),
                ip=ip,
                user_agent=user_agent,
            )
            session.add(sessao)

            # Atualiza último login
            usuario.ultimo_login = agora
            session.commit()

            return usuario, token
        finally:
            session.close()

    def logout(self, token: str) -> None:
        """Invalida a sessão."""
        session = self._get_session()
        try:
            sessao = session.execute(
                select(Sessao).where(Sessao.token == token)
            ).scalar_one_or_none()
            if sessao:
                session.delete(sessao)
                session.commit()
        finally:
            session.close()

    def validar_token(self, token: str) -> Usuario | None:
        """Valida token e retorna usuário ou None."""
        if not token:
            return None
        session = self._get_session()
        try:
            sessao = session.execute(
                select(Sessao).where(Sessao.token == token)
            ).scalar_one_or_none()
            if not sessao or sessao.expirado:
                return None
            return sessao.usuario
        finally:
            session.close()

    def get_empresas_usuario(self, usuario_id: int) -> list[dict]:
        """Lista empresas associadas ao usuário."""
        session = self._get_session()
        try:
            result = session.execute(
                select(UsuarioEmpresa).where(UsuarioEmpresa.usuario_id == usuario_id)
            ).scalars().all()
            return [
                {
                    "empresa_id": ue.empresa_id,
                    "empresa_nome": ue.empresa.nome,
                    "empresa_cnpj": ue.empresa.cnpj,
                    "permissao": ue.permissao,
                }
                for ue in result
            ]
        finally:
            session.close()


# ── Middleware FastAPI ──────────────────────────────────────────────────────

# Instância global (inicializada no app)
_auth_service: AuthService | None = None


def init_auth(db_path: str = "sped_hub.db") -> AuthService:
    global _auth_service
    _auth_service = AuthService(db_path)
    return _auth_service


def get_auth() -> AuthService:
    if _auth_service is None:
        return init_auth()
    return _auth_service


async def get_usuario_atual(request: Request) -> Usuario | None:
    """Extrai usuário da sessão (cookie)."""
    token = request.cookies.get("sped_hub_session")
    if not token:
        # Tenta header Authorization
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        return None
    return get_auth().validar_token(token)


def requer_autenticacao(redirect: bool = True):
    """Decorator que exige autenticação.

    Se redirect=True, redireciona para /login.
    Se redirect=False, retorna 401 JSON (para APIs).
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(request: Request, *args, **kwargs):
            usuario = await get_usuario_atual(request)
            if usuario is None:
                if redirect:
                    return RedirectResponse(url="/login", status_code=302)
                raise HTTPException(status_code=401, detail="Não autenticado")
            # Injeta usuário no request
            request.state.usuario = usuario
            return await func(request, *args, **kwargs)
        return wrapper
    return decorator