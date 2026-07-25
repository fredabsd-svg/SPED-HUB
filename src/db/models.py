"""Modelos SQLAlchemy para o SPED-HUB.

Todas as tabelas de dados têm chave lógica (CNPJ, periodo_ini, periodo_fin).
"""

import datetime
import hashlib
import json
import secrets

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _hash_sensivel(valor: str) -> str:
    """Hash SHA-256 truncado para mascarar dados sensíveis em logs."""
    return hashlib.sha256(valor.encode()).hexdigest()[:12]


# ── Autenticação ────────────────────────────────────────────────────────────


class Usuario(Base):
    """Usuário do sistema com autenticação por senha."""

    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    nome: Mapped[str] = mapped_column(String(150), nullable=False)
    senha_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    salt: Mapped[str] = mapped_column(String(64), nullable=False)
    ativo: Mapped[bool] = mapped_column(default=True)
    admin: Mapped[bool] = mapped_column(default=False)
    criado_em: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC)
    )
    ultimo_login: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    # Relacionamentos
    sessoes: Mapped[list["Sessao"]] = relationship(back_populates="usuario", cascade="all, delete-orphan")
    empresas: Mapped[list["UsuarioEmpresa"]] = relationship(back_populates="usuario", cascade="all, delete-orphan")

    @staticmethod
    def hash_senha(senha: str, salt: str | None = None) -> tuple[str, str]:
        """Gera hash PBKDF2 da senha com salt."""
        if salt is None:
            salt = secrets.token_hex(32)
        hash_bytes = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt.encode(), 200_000)
        return hash_bytes.hex(), salt

    def verificar_senha(self, senha: str) -> bool:
        """Verifica se a senha confere."""
        hash_bytes, _ = self.hash_senha(senha, self.salt)
        return hash_bytes == self.senha_hash

    def __repr__(self):
        return f"<Usuario {self.email}>"


class Sessao(Base):
    """Sessão de usuário autenticado (token-based)."""

    __tablename__ = "sessoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    criado_em: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC)
    )
    expira_em: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(512))

    usuario: Mapped["Usuario"] = relationship(back_populates="sessoes")

    @staticmethod
    def gerar_token() -> str:
        return secrets.token_hex(64)

    @property
    def expirado(self) -> bool:
        return datetime.datetime.now(datetime.UTC) > self.expira_em


class UsuarioEmpresa(Base):
    """Associação usuário-empresa (multiempresa)."""

    __tablename__ = "usuarios_empresas"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), nullable=False, index=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), nullable=False, index=True)
    permissao: Mapped[str] = mapped_column(String(20), default="leitura")  # leitura, escrita, admin

    usuario: Mapped["Usuario"] = relationship(back_populates="empresas")
    empresa: Mapped["Empresa"] = relationship(back_populates="usuarios")

    __table_args__ = (
        UniqueConstraint("usuario_id", "empresa_id", name="uq_usuario_empresa"),
    )


# ── Cadastros ──────────────────────────────────────────────────────────────


class Empresa(Base):
    __tablename__ = "empresas"

    id: Mapped[int] = mapped_column(primary_key=True)
    cnpj: Mapped[str] = mapped_column(String(14), nullable=False, index=True)
    nome: Mapped[str] = mapped_column(String(150), nullable=False)
    uf: Mapped[str | None] = mapped_column(String(2))
    ie: Mapped[str | None] = mapped_column(String(14))
    cod_mun: Mapped[str | None] = mapped_column(String(7))
    im: Mapped[str | None] = mapped_column(String(14))
    ind_sit_esp: Mapped[int | None] = mapped_column()
    ind_nire: Mapped[int | None] = mapped_column()
    ind_fin_esc: Mapped[int | None] = mapped_column()
    ind_grande_por: Mapped[int | None] = mapped_column()
    tip_ecd: Mapped[str | None] = mapped_column(String(1))
    ident_mf: Mapped[str | None] = mapped_column(String(1))
    ind_esc_cons: Mapped[str | None] = mapped_column(String(1))

    # Relacionamentos
    ecds: Mapped[list["ECD"]] = relationship(back_populates="empresa", cascade="all, delete-orphan")
    usuarios: Mapped[list["UsuarioEmpresa"]] = relationship(back_populates="empresa", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Empresa {_hash_sensivel(self.cnpj)}>"


class ECD(Base):
    """Uma importação de ECD — identifica unicamente um arquivo processado."""

    __tablename__ = "ecds"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), nullable=False, index=True)
    leiaute: Mapped[str] = mapped_column(String(3), nullable=False)  # "009"
    dt_ini: Mapped[datetime.date] = mapped_column(nullable=False)
    dt_fin: Mapped[datetime.date] = mapped_column(nullable=False)
    ind_esc: Mapped[str | None] = mapped_column(String(1))  # G/R/A/B
    cod_ver_lc: Mapped[str | None] = mapped_column(String(3))
    hash_arquivo: Mapped[str | None] = mapped_column(String(64))
    nome_arquivo: Mapped[str | None] = mapped_column(String(255))
    importado_em: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC)
    )

    empresa: Mapped["Empresa"] = relationship(back_populates="ecds")
    plano_contas: Mapped[list["PlanoConta"]] = relationship(
        back_populates="ecd", cascade="all, delete-orphan"
    )
    centros_custo: Mapped[list["CentroCusto"]] = relationship(
        back_populates="ecd", cascade="all, delete-orphan"
    )
    participantes: Mapped[list["Participante"]] = relationship(
        back_populates="ecd", cascade="all, delete-orphan"
    )
    saldos_periodicos: Mapped[list["SaldoPeriodico"]] = relationship(
        back_populates="ecd", cascade="all, delete-orphan"
    )
    saldos_resultado: Mapped[list["SaldoResultado"]] = relationship(
        back_populates="ecd", cascade="all, delete-orphan"
    )
    lancamentos: Mapped[list["Lancamento"]] = relationship(
        back_populates="ecd", cascade="all, delete-orphan"
    )
    historicos_padrao: Mapped[list["HistoricoPadrao"]] = relationship(
        back_populates="ecd", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("empresa_id", "dt_ini", "dt_fin", name="uq_ecd_empresa_periodo"),
    )

    def __repr__(self):
        return f"<ECD empresa={_hash_sensivel(str(self.empresa_id))} {self.dt_ini}–{self.dt_fin}>"


# ── Plano de Contas ────────────────────────────────────────────────────────


class PlanoConta(Base):
    __tablename__ = "plano_contas"

    id: Mapped[int] = mapped_column(primary_key=True)
    ecd_id: Mapped[int] = mapped_column(ForeignKey("ecds.id"), nullable=False, index=True)
    cod_cta: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    cod_cta_sup: Mapped[str | None] = mapped_column(String(255), index=True)
    nome_cta: Mapped[str] = mapped_column(String(255), nullable=False)
    cod_nat: Mapped[str] = mapped_column(String(2), nullable=False)  # 01-05, 09
    ind_cta: Mapped[str] = mapped_column(String(1), nullable=False)  # S/A
    nivel: Mapped[int] = mapped_column(nullable=False)
    dt_alt: Mapped[datetime.date | None] = mapped_column()

    ecd: Mapped["ECD"] = relationship(back_populates="plano_contas")
    contas_referenciais: Mapped[list["ContaReferencial"]] = relationship(
        back_populates="conta", cascade="all, delete-orphan"
    )
    aglutinacoes: Mapped[list["Aglutinacao"]] = relationship(
        back_populates="conta", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("ecd_id", "cod_cta", name="uq_plano_conta_ecd"),
    )

    def __repr__(self):
        return f"<PlanoConta {self.cod_cta} {self.nome_cta[:30]}>"


class ContaReferencial(Base):
    __tablename__ = "contas_referenciais"

    id: Mapped[int] = mapped_column(primary_key=True)
    plano_conta_id: Mapped[int] = mapped_column(ForeignKey("plano_contas.id"), nullable=False, index=True)
    cod_ccus: Mapped[str | None] = mapped_column(String(255))
    cod_cta_ref: Mapped[str] = mapped_column(String(255), nullable=False)

    conta: Mapped["PlanoConta"] = relationship(back_populates="contas_referenciais")


class Aglutinacao(Base):
    __tablename__ = "aglutinacoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    plano_conta_id: Mapped[int] = mapped_column(ForeignKey("plano_contas.id"), nullable=False, index=True)
    cod_ccus: Mapped[str | None] = mapped_column(String(255))
    cod_agl: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    conta: Mapped["PlanoConta"] = relationship(back_populates="aglutinacoes")


# ── Centros de Custo ───────────────────────────────────────────────────────


class CentroCusto(Base):
    __tablename__ = "centros_custo"

    id: Mapped[int] = mapped_column(primary_key=True)
    ecd_id: Mapped[int] = mapped_column(ForeignKey("ecds.id"), nullable=False, index=True)
    cod_ccus: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    ccus: Mapped[str] = mapped_column(String(255), nullable=False)
    dt_alt: Mapped[datetime.date | None] = mapped_column()

    ecd: Mapped["ECD"] = relationship(back_populates="centros_custo")

    __table_args__ = (
        UniqueConstraint("ecd_id", "cod_ccus", name="uq_centro_custo_ecd"),
    )


# ── Participantes ──────────────────────────────────────────────────────────


class Participante(Base):
    __tablename__ = "participantes"

    id: Mapped[int] = mapped_column(primary_key=True)
    ecd_id: Mapped[int] = mapped_column(ForeignKey("ecds.id"), nullable=False, index=True)
    cod_part: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    nome: Mapped[str] = mapped_column(String(150), nullable=False)
    cnpj: Mapped[str | None] = mapped_column(String(14))
    cpf: Mapped[str | None] = mapped_column(String(11))

    ecd: Mapped["ECD"] = relationship(back_populates="participantes")

    __table_args__ = (
        UniqueConstraint("ecd_id", "cod_part", name="uq_participante_ecd"),
    )


# ── Históricos Padronizados ────────────────────────────────────────────────


class HistoricoPadrao(Base):
    __tablename__ = "historicos_padrao"

    id: Mapped[int] = mapped_column(primary_key=True)
    ecd_id: Mapped[int] = mapped_column(ForeignKey("ecds.id"), nullable=False, index=True)
    cod_hist: Mapped[str] = mapped_column(String(255), nullable=False)
    descr_hist: Mapped[str] = mapped_column(String(255), nullable=False)

    ecd: Mapped["ECD"] = relationship(back_populates="historicos_padrao")


# ── Saldos ─────────────────────────────────────────────────────────────────


class SaldoPeriodico(Base):
    """I155 — Saldos periódicos (mensais)."""

    __tablename__ = "saldos_periodicos"

    id: Mapped[int] = mapped_column(primary_key=True)
    ecd_id: Mapped[int] = mapped_column(ForeignKey("ecds.id"), nullable=False, index=True)
    cod_cta: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    cod_ccus: Mapped[str | None] = mapped_column(String(255))
    dt_ini: Mapped[datetime.date] = mapped_column(nullable=False)
    dt_fin: Mapped[datetime.date] = mapped_column(nullable=False)
    vl_sld_ini: Mapped[float] = mapped_column(nullable=False, default=0.0)
    ind_dc_ini: Mapped[str] = mapped_column(String(1), nullable=False)  # D/C
    vl_deb: Mapped[float] = mapped_column(nullable=False, default=0.0)
    vl_cred: Mapped[float] = mapped_column(nullable=False, default=0.0)
    vl_sld_fin: Mapped[float] = mapped_column(nullable=False, default=0.0)
    ind_dc_fin: Mapped[str] = mapped_column(String(1), nullable=False)  # D/C

    ecd: Mapped["ECD"] = relationship(back_populates="saldos_periodicos")

    __table_args__ = (
        UniqueConstraint("ecd_id", "cod_cta", "cod_ccus", "dt_ini", "dt_fin",
                         name="uq_saldo_periodico"),
    )


class SaldoResultado(Base):
    """I355 — Saldos das contas de resultado antes do encerramento."""

    __tablename__ = "saldos_resultado"

    id: Mapped[int] = mapped_column(primary_key=True)
    ecd_id: Mapped[int] = mapped_column(ForeignKey("ecds.id"), nullable=False, index=True)
    cod_cta: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    cod_ccus: Mapped[str | None] = mapped_column(String(255))
    dt_res: Mapped[datetime.date] = mapped_column(nullable=False)
    vl_sld_fin: Mapped[float] = mapped_column(nullable=False, default=0.0)
    ind_dc_fin: Mapped[str] = mapped_column(String(1), nullable=False)  # D/C

    ecd: Mapped["ECD"] = relationship(back_populates="saldos_resultado")

    __table_args__ = (
        UniqueConstraint("ecd_id", "cod_cta", "cod_ccus", "dt_res",
                         name="uq_saldo_resultado"),
    )


# ── Lançamentos ────────────────────────────────────────────────────────────


class Lancamento(Base):
    """I200 — Lançamento contábil."""

    __tablename__ = "lancamentos"

    id: Mapped[int] = mapped_column(primary_key=True)
    ecd_id: Mapped[int] = mapped_column(ForeignKey("ecds.id"), nullable=False, index=True)
    num_lcto: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    dt_lcto: Mapped[datetime.date] = mapped_column(nullable=False, index=True)
    vl_lcto: Mapped[float] = mapped_column(nullable=False, default=0.0)
    ind_lcto: Mapped[str] = mapped_column(String(1), nullable=False)  # N/E/X
    num_arq: Mapped[int | None] = mapped_column()

    ecd: Mapped["ECD"] = relationship(back_populates="lancamentos")
    partidas: Mapped[list["Partida"]] = relationship(
        back_populates="lancamento", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("ecd_id", "num_lcto", "dt_lcto", name="uq_lancamento_ecd"),
    )


class Partida(Base):
    """I250 — Partida do lançamento."""

    __tablename__ = "partidas"

    id: Mapped[int] = mapped_column(primary_key=True)
    lancamento_id: Mapped[int] = mapped_column(ForeignKey("lancamentos.id"), nullable=False, index=True)
    cod_cta: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    cod_ccus: Mapped[str | None] = mapped_column(String(255))
    vl_dc: Mapped[float] = mapped_column(nullable=False, default=0.0)
    ind_dc: Mapped[str] = mapped_column(String(1), nullable=False)  # D/C
    num_arq: Mapped[int | None] = mapped_column()
    cod_hist_pad: Mapped[str | None] = mapped_column(String(255))
    hist: Mapped[str | None] = mapped_column(Text)
    cod_part: Mapped[str | None] = mapped_column(String(255))

    lancamento: Mapped["Lancamento"] = relationship(back_populates="partidas")


# ── Mapeamentos Configuráveis ──────────────────────────────────────────────


class Mapeamento(Base):
    """Mapeamentos configuráveis por empresa (DRE, DFC, disponibilidades)."""

    __tablename__ = "mapeamentos"

    id: Mapped[int] = mapped_column(primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), nullable=False, index=True)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)  # dre/dfc/disponibilidades/depreciacao
    cod_cta: Mapped[str] = mapped_column(String(255), nullable=False)
    categoria: Mapped[str] = mapped_column(String(100), nullable=False)
    ordem: Mapped[int] = mapped_column(default=0)

    __table_args__ = (
        UniqueConstraint("empresa_id", "tipo", "cod_cta", name="uq_mapeamento"),
    )


# ── Visões de Filtro ───────────────────────────────────────────────────────


class FilterView(Base):
    """Visão salva de filtros (F7)."""

    __tablename__ = "filter_views"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(100), nullable=False)
    criterios_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    criado_em: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC)
    )

    def get_criterios(self) -> dict:
        return json.loads(self.criterios_json)

    def set_criterios(self, criterios: dict):
        self.criterios_json = json.dumps(criterios, ensure_ascii=False)


# ── Engine Factory ─────────────────────────────────────────────────────────


def criar_engine(caminho: str = "sped_hub.db", echo: bool = False):
    """Cria engine SQLite com WAL mode e foreign keys ativadas."""
    engine = create_engine(f"sqlite:///{caminho}", echo=echo)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def init_db(engine) -> None:
    """Cria todas as tabelas."""
    Base.metadata.create_all(engine)


def get_session(engine):
    """Retorna uma nova sessão."""
    return Session(engine)