"""Módulo de autenticação — login, registro, sessões, middleware e multi-tenancy.

Fase 12: +MultiTenantMiddleware com isolamento por escritório via contextvars.
"""

import contextvars
import datetime
import logging
from collections.abc import Callable
from functools import wraps

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import (
    Sessao,
    Usuario,
    UsuarioEmpresa,
    get_session,
    init_db_once,
    obter_engine,
)

logger = logging.getLogger("sped-hub.auth")

# Duração da sessão: 24 horas
SESSAO_DURACAO_HORAS = 24

# ── Multi-Tenancy Context (Fase 12) ────────────────────────────────────────

# ContextVar para o escritório do request atual (thread-safe e async-safe)
_tenant_context: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "sped_hub_tenant_id", default=None
)


def get_tenant_id() -> int | None:
    """Retorna o escritorio_id do contexto atual, ou None se não houver isolamento."""
    return _tenant_context.get()


def set_tenant_id(escritorio_id: int | None) -> None:
    """Define o escritorio_id no contexto atual."""
    _tenant_context.set(escritorio_id)


class TenantFilter:
    """Filtro de queries por tenant — aplica WHERE escritorio_id = ? quando ativo.

    Uso:
        tf = TenantFilter()
        query = tf.aplicar(query, ECD)  # adiciona join/filtro conforme o modelo
    """

    @staticmethod
    def aplicar(stmt, model_class, tenant_id: int | None = None):
        """Aplica filtro de tenant a uma query SQLAlchemy.

        Args:
            stmt: query SQLAlchemy (select)
            model_class: classe do modelo principal da query
            tenant_id: ID do escritório (se None, usa get_tenant_id())

        Returns:
            query com filtro de tenant aplicado (ou a original se tenant_id for None)
        """
        tid = tenant_id if tenant_id is not None else get_tenant_id()
        if tid is None:
            return stmt

        from src.db.models import ECD, Empresa, Usuario

        # Modelos com escritorio_id direto
        if model_class in (Empresa, Usuario):
            return stmt.where(model_class.escritorio_id == tid)

        # ECD: join com Empresa para filtrar por escritorio_id
        if model_class is ECD:
            return stmt.join(Empresa, ECD.empresa_id == Empresa.id).where(
                Empresa.escritorio_id == tid
            )

        # Para outros modelos que pertencem a uma ECD (PlanoConta, Lancamento, etc.)
        # o filtro é aplicado via ECD → Empresa
        if hasattr(model_class, "ecd_id"):
            return (
                stmt.join(ECD, model_class.ecd_id == ECD.id)
                .join(Empresa, ECD.empresa_id == Empresa.id)
                .where(Empresa.escritorio_id == tid)
            )

        # Para modelos que pertencem a Lancamento (Partida)
        if hasattr(model_class, "lancamento_id"):
            from src.db.models import Lancamento

            return (
                stmt.join(Lancamento, model_class.lancamento_id == Lancamento.id)
                .join(ECD, Lancamento.ecd_id == ECD.id)
                .join(Empresa, ECD.empresa_id == Empresa.id)
                .where(Empresa.escritorio_id == tid)
            )

        return stmt


class MultiTenantMiddleware:
    """Middleware ASGI que injeta o tenant_id do usuário autenticado no contexto.

    Deve ser registrado antes das rotas que precisam de isolamento.
    """

    def __init__(self, app, db_path: str = "sped_hub.db"):
        self.app = app
        self.db_path = db_path

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # Extrai token do cookie ou header
        request = Request(scope, receive)
        token = request.cookies.get("sped_hub_session")
        if not token:
            auth_header = dict(scope.get("headers", [])).get(b"authorization", b"").decode()
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]

        # Resolve tenant do usuário
        tenant_id = None
        if token:
            tenant_id = self._resolve_tenant(token)

        # Define no contexto
        token_ctx = _tenant_context.set(tenant_id)
        try:
            await self.app(scope, receive, send)
        finally:
            _tenant_context.reset(token_ctx)

    def _resolve_tenant(self, token: str) -> int | None:
        """Resolve o escritorio_id a partir do token de sessão."""
        try:
            engine = obter_engine(self.db_path)
            init_db_once(engine)
            session = get_session(engine)
            try:
                sessao = session.execute(
                    select(Sessao).where(Sessao.token == token)
                ).scalar_one_or_none()
                if sessao and not sessao.expirado:
                    usuario = sessao.usuario
                    return usuario.escritorio_id
            finally:
                session.close()
        except Exception:
            logger.exception("Erro ao resolver tenant do token")
        return None


class AuthService:
    """Serviço de autenticação."""

    def __init__(self, db_path: str = "sped_hub.db"):
        self.db_path = db_path
        self._ensure_admin_for_existing_installation()

    def _get_session(self) -> Session:
        # Engine reutilizada e schema criado uma vez: este método roda em todo
        # request autenticado, e `criar_engine` + `create_all` a cada chamada
        # custavam ~3 ms por request só para validar um token.
        engine = obter_engine(self.db_path)
        init_db_once(engine)
        return get_session(engine)

    def _ensure_admin_for_existing_installation(self) -> None:
        """Migra instalações antigas promovendo o usuário mais antigo uma vez."""
        session = self._get_session()
        try:
            users = (
                session.execute(select(Usuario).order_by(Usuario.criado_em.asc(), Usuario.id.asc()))
                .scalars()
                .all()
            )
            if users and not any(user.admin for user in users):
                users[0].admin = True
                session.commit()
                logger.warning("Instalação sem administrador: usuário #%d promovido", users[0].id)
        finally:
            session.close()

    def registrar(
        self, email: str, nome: str, senha: str, escritorio_id: int | None = None
    ) -> Usuario:
        """Registra um novo usuário."""
        session = self._get_session()
        try:
            existente = session.execute(
                select(Usuario).where(Usuario.email == email)
            ).scalar_one_or_none()
            if existente:
                raise ValueError("Email já cadastrado")

            senha_hash, salt = Usuario.hash_senha(senha)
            is_first_user = session.execute(select(func.count(Usuario.id))).scalar_one() == 0
            usuario = Usuario(
                email=email,
                nome=nome,
                senha_hash=senha_hash,
                salt=salt,
                escritorio_id=escritorio_id,
                admin=is_first_user,
            )
            session.add(usuario)
            session.commit()
            session.refresh(usuario)
            return usuario
        finally:
            session.close()

    def login(
        self, email: str, senha: str, ip: str = "", user_agent: str = ""
    ) -> tuple[Usuario, str]:
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

            # Captura dados antes de fechar a sessão
            usuario_id = usuario.id
            usuario_email = usuario.email

            return usuario, token, usuario_id, usuario_email
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
            result = (
                session.execute(
                    select(UsuarioEmpresa).where(UsuarioEmpresa.usuario_id == usuario_id)
                )
                .scalars()
                .all()
            )
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


def aplicar_escopo_empresas(stmt, usuario: Usuario):
    """Restringe uma query com Empresa ao escritório do usuário."""
    if usuario.admin:
        return stmt
    from src.db.models import Empresa

    if usuario.escritorio_id is None:
        return stmt.where(Empresa.escritorio_id.is_(None))
    return stmt.where(Empresa.escritorio_id == usuario.escritorio_id)


def usuario_pode_acessar_ecd(session: Session, usuario: Usuario, ecd_id: int) -> bool:
    """Confirma acesso a uma ECD sem revelar existência entre tenants."""
    if usuario.admin:
        from src.db.models import ECD

        return session.get(ECD, ecd_id) is not None

    from src.db.models import ECD, Empresa

    stmt = select(ECD.id).join(Empresa, ECD.empresa_id == Empresa.id).where(ECD.id == ecd_id)
    stmt = aplicar_escopo_empresas(stmt, usuario)
    return session.execute(stmt).scalar_one_or_none() is not None


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
            # Define tenant no contexto (Fase 12)
            set_tenant_id(usuario.escritorio_id)
            return await func(request, *args, **kwargs)

        return wrapper

    return decorator
