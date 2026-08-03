"""Modelos SQLAlchemy para o SPED-HUB.

Todas as tabelas de dados têm chave lógica (CNPJ, periodo_ini, periodo_fin).
Fase 11: +Escritorio (multi-tenancy), +WebhookDelivery (dashboard de webhooks).
Fase 13: +RateLimitConfig, +AuditLog (rate limiting e auditoria).
Fase 17: ``criar_engine`` aceita URL genérica (SQLite ou PostgreSQL) lendo
também de :mod:`src.settings`.
"""

import datetime
import hashlib
import hmac
import json
import os
import secrets
import threading
import weakref

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from src.settings import get_settings


class Base(DeclarativeBase):
    pass


def _hash_sensivel(valor: str) -> str:
    """Hash SHA-256 truncado para mascarar dados sensíveis em logs."""
    return hashlib.sha256(valor.encode()).hexdigest()[:12]


# ── Multi-Tenancy (Fase 11) ────────────────────────────────────────────────


class Escritorio(Base):
    """Escritório contábil — raiz do isolamento multi-tenant."""

    __tablename__ = "escritorios"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(150), nullable=False)
    slug: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    cnpj: Mapped[str | None] = mapped_column(String(14))
    ativo: Mapped[bool] = mapped_column(default=True)
    criado_em: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC)
    )

    usuarios: Mapped[list["Usuario"]] = relationship(back_populates="escritorio")
    empresas: Mapped[list["Empresa"]] = relationship(back_populates="escritorio")

    def __repr__(self):
        return f"<Escritorio {self.slug}>"


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
    escritorio_id: Mapped[int | None] = mapped_column(
        ForeignKey("escritorios.id"), nullable=True, index=True
    )
    criado_em: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC)
    )
    ultimo_login: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    # Relacionamentos
    escritorio: Mapped["Escritorio | None"] = relationship(back_populates="usuarios")
    sessoes: Mapped[list["Sessao"]] = relationship(
        back_populates="usuario", cascade="all, delete-orphan"
    )
    empresas: Mapped[list["UsuarioEmpresa"]] = relationship(
        back_populates="usuario", cascade="all, delete-orphan"
    )

    @staticmethod
    def hash_senha(senha: str, salt: str | None = None) -> tuple[str, str]:
        """Gera hash PBKDF2 da senha com salt."""
        if salt is None:
            salt = secrets.token_hex(32)
        hash_bytes = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt.encode(), 200_000)
        return hash_bytes.hex(), salt

    def verificar_senha(self, senha: str) -> bool:
        """Verifica se a senha confere, em tempo constante.

        `==` entre strings sai no primeiro byte diferente, o que vaza por
        tempo quantos caracteres do hash foram acertados.  `verificar_api_key`
        já usava `compare_digest`; aqui tinha ficado de fora.
        """
        hash_bytes, _ = self.hash_senha(senha, self.salt)
        return hmac.compare_digest(hash_bytes, self.senha_hash)

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
        agora = datetime.datetime.now(datetime.UTC)
        expira = self.expira_em
        if expira.tzinfo is None:
            expira = expira.replace(tzinfo=datetime.UTC)
        return agora > expira


class UsuarioEmpresa(Base):
    """Associação usuário-empresa (multiempresa)."""

    __tablename__ = "usuarios_empresas"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), nullable=False, index=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), nullable=False, index=True)
    permissao: Mapped[str] = mapped_column(String(20), default="leitura")  # leitura, escrita, admin

    usuario: Mapped["Usuario"] = relationship(back_populates="empresas")
    empresa: Mapped["Empresa"] = relationship(back_populates="usuarios")

    __table_args__ = (UniqueConstraint("usuario_id", "empresa_id", name="uq_usuario_empresa"),)


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
    # ── Configuração para a EFD ICMS/IPI ──────────────────────────────────
    # Os dois campos do registro 0000 que não dá para derivar de documento
    # nenhum: dependem do enquadramento da empresa junto à SEFAZ.  Sem eles o
    # arquivo sai, mas com o enquadramento errado — e o validador aceita,
    # porque não tem como saber.  Daí serem cadastro, não default.
    ind_perfil: Mapped[str | None] = mapped_column(String(1))  # A, B ou C
    ind_ativ: Mapped[str | None] = mapped_column(String(1))  # 0=industrial, 1=outros
    # ── Configuração para a EFD-Contribuições ─────────────────────────────
    # COD_INC_TRIB do registro 0110: 1=não cumulativo, 2=cumulativo, 3=ambos.
    # É o campo que decide se a empresa desconta crédito das aquisições.
    # Errar nele produz arquivo estruturalmente válido com contribuição
    # errada — o Fisco cobra a diferença com multa, e a conferência não pega.
    cod_inc_trib: Mapped[str | None] = mapped_column(String(1))
    # IND_ATIV do registro 0000 da EFD-Contribuições.  É campo separado do
    # `ind_ativ` acima de propósito: as duas escriturações fazem perguntas
    # diferentes com o mesmo nome.  Na EFD ICMS/IPI a resposta é binária
    # (0=industrial, 1=outros); aqui são 0=industrial ou equiparado,
    # 1=prestador de serviços, 2=comércio, 3=PJ dos §§ 6º, 8º e 9º do art. 3º
    # da Lei 9.718/98, 4=atividade imobiliária, 9=outros.  Reaproveitar o
    # outro campo declararia como prestador de serviços toda empresa de
    # comércio que respondeu "1 = outros" pensando na EFD ICMS/IPI.
    ind_ativ_contribuicoes: Mapped[str | None] = mapped_column(String(1))

    # IND_NAT_PJ do 0000 da EFD-Contribuições: a natureza da pessoa jurídica.
    # Ao contrário dos campos acima, este tem default — `00`, sociedade
    # empresária em geral, que é a imensa maioria — e por isso o gerador não
    # recusa quando falta; avisa. Cooperativa (01) e entidade que apura sobre a
    # folha (02) apuram por outra regra, e o validador aceita o enquadramento
    # errado porque não tem como saber.
    ind_nat_pj: Mapped[str | None] = mapped_column(String(2))

    escritorio_id: Mapped[int | None] = mapped_column(
        ForeignKey("escritorios.id"), nullable=True, index=True
    )

    # Relacionamentos
    escritorio: Mapped["Escritorio | None"] = relationship(back_populates="empresas")
    ecds: Mapped[list["ECD"]] = relationship(back_populates="empresa", cascade="all, delete-orphan")
    usuarios: Mapped[list["UsuarioEmpresa"]] = relationship(
        back_populates="empresa", cascade="all, delete-orphan"
    )

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

    __table_args__ = (UniqueConstraint("ecd_id", "cod_cta", name="uq_plano_conta_ecd"),)

    def __repr__(self):
        return f"<PlanoConta {self.cod_cta} {self.nome_cta[:30]}>"


class ContaReferencial(Base):
    __tablename__ = "contas_referenciais"

    id: Mapped[int] = mapped_column(primary_key=True)
    plano_conta_id: Mapped[int] = mapped_column(
        ForeignKey("plano_contas.id"), nullable=False, index=True
    )
    cod_ccus: Mapped[str | None] = mapped_column(String(255))
    cod_cta_ref: Mapped[str] = mapped_column(String(255), nullable=False)

    conta: Mapped["PlanoConta"] = relationship(back_populates="contas_referenciais")


class Aglutinacao(Base):
    __tablename__ = "aglutinacoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    plano_conta_id: Mapped[int] = mapped_column(
        ForeignKey("plano_contas.id"), nullable=False, index=True
    )
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

    __table_args__ = (UniqueConstraint("ecd_id", "cod_ccus", name="uq_centro_custo_ecd"),)


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

    __table_args__ = (UniqueConstraint("ecd_id", "cod_part", name="uq_participante_ecd"),)


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
        UniqueConstraint(
            "ecd_id", "cod_cta", "cod_ccus", "dt_ini", "dt_fin", name="uq_saldo_periodico"
        ),
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
        UniqueConstraint("ecd_id", "cod_cta", "cod_ccus", "dt_res", name="uq_saldo_resultado"),
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

    __table_args__ = (UniqueConstraint("ecd_id", "num_lcto", "dt_lcto", name="uq_lancamento_ecd"),)


class Partida(Base):
    """I250 — Partida do lançamento."""

    __tablename__ = "partidas"

    id: Mapped[int] = mapped_column(primary_key=True)
    lancamento_id: Mapped[int] = mapped_column(
        ForeignKey("lancamentos.id"), nullable=False, index=True
    )
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
    tipo: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # dre/dfc/disponibilidades/depreciacao
    cod_cta: Mapped[str] = mapped_column(String(255), nullable=False)
    categoria: Mapped[str] = mapped_column(String(100), nullable=False)
    ordem: Mapped[int] = mapped_column(default=0)

    __table_args__ = (UniqueConstraint("empresa_id", "tipo", "cod_cta", name="uq_mapeamento"),)


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


# ── API Keys (Fase 7) ──────────────────────────────────────────────────────


class ApiKey(Base):
    """Chave de API para acesso à API REST externa."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(100), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    prefixo: Mapped[str] = mapped_column(String(11), nullable=False)  # "spd_xxxx" para exibição
    # Escritório dono da chave.  `None` = chave de instância, que lê tudo — é o
    # comportamento de toda chave criada antes desta coluna existir, preservado
    # para não derrubar integração em produção.  Chave COM dono só lê o que é
    # daquele escritório.
    escritorio_id: Mapped[int | None] = mapped_column(
        ForeignKey("escritorios.id"), nullable=True, index=True
    )
    ativo: Mapped[bool] = mapped_column(default=True)
    criado_em: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC)
    )
    expira_em: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    ultimo_uso: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    total_requisicoes: Mapped[int] = mapped_column(default=0)

    def __repr__(self):
        return f"<ApiKey {self.nome} ({self.prefixo}...)>"


# ── Rate Limiting (Fase 13) ────────────────────────────────────────────────


class RateLimitConfig(Base):
    """Configuração de rate limit por API Key.

    Permite definir limites personalizados por chave: número máximo de
    requisições por janela de tempo (em segundos).
    """

    __tablename__ = "rate_limit_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_key_id: Mapped[int] = mapped_column(
        ForeignKey("api_keys.id"), nullable=False, unique=True, index=True
    )
    limite: Mapped[int] = mapped_column(nullable=False, default=100)  # máx req por janela
    janela: Mapped[int] = mapped_column(nullable=False, default=60)  # janela em segundos
    criado_em: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC)
    )
    atualizado_em: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    def __repr__(self):
        return f"<RateLimitConfig key={self.api_key_id} {self.limite}/{self.janela}s>"


# ── Logs de Auditoria (Fase 13) ────────────────────────────────────────────


class AuditLog(Base):
    """Registro de auditoria — rastreia quem acessou o quê e quando.

    Captura automaticamente acessos a endpoints da API e ações sensíveis
    do dashboard (login, upload, exportação, etc.).
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int | None] = mapped_column(index=True)
    usuario_email: Mapped[str | None] = mapped_column(String(255))
    api_key_id: Mapped[int | None] = mapped_column(index=True)
    acao: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    recurso: Mapped[str] = mapped_column(String(255), nullable=False)
    metodo: Mapped[str | None] = mapped_column(String(10))
    ip: Mapped[str | None] = mapped_column(String(45))
    status_code: Mapped[int | None] = mapped_column()
    detalhes: Mapped[str | None] = mapped_column(Text)  # JSON
    criado_em: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC), index=True
    )

    def __repr__(self):
        return f"<AuditLog {self.acao} {self.recurso} {self.criado_em}>"

    def get_detalhes(self) -> dict:
        if self.detalhes:
            try:
                return json.loads(self.detalhes)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    def set_detalhes(self, detalhes: dict):
        self.detalhes = json.dumps(detalhes, ensure_ascii=False, default=str)


# ── Webhooks (Fase 10) ─────────────────────────────────────────────────────


class WebhookRegistration(Base):
    """Registro de webhook para integração com sistemas de terceiros."""

    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    eventos: Mapped[str] = mapped_column(Text, nullable=False)  # JSON array: ["ecd.importada", ...]
    secret: Mapped[str | None] = mapped_column(String(128))  # HMAC secret
    descricao: Mapped[str] = mapped_column(String(255), default="")
    ativo: Mapped[bool] = mapped_column(default=True)
    max_retries: Mapped[int] = mapped_column(default=3)
    criado_em: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC)
    )
    ultimo_envio: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    total_envios: Mapped[int] = mapped_column(default=0)
    total_falhas: Mapped[int] = mapped_column(default=0)

    deliveries: Mapped[list["WebhookDelivery"]] = relationship(
        back_populates="webhook", cascade="all, delete-orphan"
    )

    def get_eventos(self) -> list[str]:
        return json.loads(self.eventos)

    def __repr__(self):
        return f"<Webhook {self.id} → {self.url[:50]}>"


class WebhookDelivery(Base):
    """Histórico de entregas de webhook (Fase 11)."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    webhook_id: Mapped[int] = mapped_column(ForeignKey("webhooks.id"), nullable=False, index=True)
    evento: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending, success, failed, retrying
    status_code: Mapped[int | None] = mapped_column()
    request_body: Mapped[str | None] = mapped_column(Text)
    response_body: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    tentativa: Mapped[int] = mapped_column(default=1)
    criado_em: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC)
    )
    concluido_em: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    webhook: Mapped["WebhookRegistration"] = relationship(back_populates="deliveries")

    def __repr__(self):
        return f"<WebhookDelivery {self.id} {self.evento} {self.status}>"


# ── Jobs Assíncronos (Fase 14) ─────────────────────────────────────────────


class AsyncJob(Base):
    """Job assíncrono para processamento em background.

    Usado para importação de ECDs grandes, exportação em lote e outras
    operações demoradas. O cliente faz polling via GET /api/jobs/{id}.
    """

    __tablename__ = "async_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # ecd_import, export_lote, etc.
    # Vocabulário completo em `src.async_jobs.JobStatus`; as listas de estado
    # terminal e em aberto ficam lá (`STATUS_TERMINAIS`, `STATUS_EM_ABERTO`).
    # Em aberto:  pending, processing
    # Terminais:  completed, failed, cancelled, interrupted
    # `interrupted` é o job cujo processo morreu no meio — sem ele a linha
    # ficava em `pending` dizendo "Aguardando processamento..." para sempre.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    progresso: Mapped[float] = mapped_column(default=0.0)  # 0-100
    parametros: Mapped[str | None] = mapped_column(Text)  # JSON
    resultado: Mapped[str | None] = mapped_column(Text)  # JSON
    erro: Mapped[str | None] = mapped_column(Text)
    mensagem: Mapped[str | None] = mapped_column(String(500))
    criado_em: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC), index=True
    )
    concluido_em: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    def __repr__(self):
        return f"<AsyncJob {self.id} {self.tipo} {self.status}>"


# ── Central de Documentos Fiscais ──────────────────────────────────────────
#
# As três camadas que a suíte fiscal precisa manter separadas:
#
#   1. ORIGINAL    — o XML como veio, byte a byte, em `DocumentoFiscal.xml_original`.
#                    Nunca é reescrito.  É a prova de o que o emitente declarou.
#   2. NORMALIZADO — os campos extraídos do original para uma estrutura única,
#                    igual para NF-e, NFC-e, NFS-e de qualquer provedor.  Também
#                    imutável: representa o original, só que legível.
#   3. EFETIVO     — o que vai para o SPED.  NÃO é uma coluna: é o normalizado
#                    mais os `AjusteFiscal` aplicados em ordem.
#
# A terceira camada ser calculada, e não gravada, é o que torna a reversão
# trivial e a auditoria exata: desfazer um lote é apagar seus ajustes, e a
# pergunta "por que este registro saiu assim?" se responde listando os ajustes
# daquele campo.  Gravar o valor final numa coluna faria as três camadas
# divergirem no primeiro `UPDATE` que alguém escrevesse fora do fluxo.


class DocumentoFiscal(Base):
    """Um documento fiscal importado — NF-e, NFC-e, NFS-e.

    A chave de acesso é única por escritório, e é o que impede o mesmo XML de
    entrar duas vezes por caminhos diferentes (pasta, ZIP, API).  Documentos de
    serviço nem sempre têm chave de 44 dígitos; nesses casos o adaptador do
    provedor monta uma identidade estável a partir de CNPJ, número e série.
    """

    __tablename__ = "documentos_fiscais"
    __table_args__ = (UniqueConstraint("escritorio_id", "chave", name="uq_documento_chave"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    escritorio_id: Mapped[int | None] = mapped_column(ForeignKey("escritorios.id"), index=True)
    empresa_id: Mapped[int | None] = mapped_column(ForeignKey("empresas.id"), index=True)

    # Identidade
    chave: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    modelo: Mapped[str] = mapped_column(String(2), nullable=False)  # 55, 65, 57…
    especie: Mapped[str] = mapped_column(String(12), nullable=False)  # nfe, nfce, nfse
    numero: Mapped[str] = mapped_column(String(20), nullable=False)
    serie: Mapped[str | None] = mapped_column(String(5))

    # Sentido e situação
    sentido: Mapped[str] = mapped_column(String(7), nullable=False)  # entrada|saida
    situacao: Mapped[str] = mapped_column(String(12), nullable=False, default="autorizado")
    finalidade: Mapped[str | None] = mapped_column(String(20))
    natureza_operacao: Mapped[str | None] = mapped_column(String(120))

    # Partes
    emitente_cnpj: Mapped[str | None] = mapped_column(String(14), index=True)
    emitente_nome: Mapped[str | None] = mapped_column(String(120))
    emitente_ie: Mapped[str | None] = mapped_column(String(20))
    emitente_uf: Mapped[str | None] = mapped_column(String(2))
    destinatario_cnpj: Mapped[str | None] = mapped_column(String(14), index=True)
    destinatario_nome: Mapped[str | None] = mapped_column(String(120))
    destinatario_ie: Mapped[str | None] = mapped_column(String(20))
    destinatario_uf: Mapped[str | None] = mapped_column(String(2))
    municipio_codigo: Mapped[str | None] = mapped_column(String(7))
    # Município de consumo, fato gerador do IBS/CBS (`cMunFGIBS`, B12a).  Fica
    # no documento, não no item: a NT o põe no `ide`, e só o exige quando a
    # operação é presencial fora do estabelecimento (`indPres=5`) e não há
    # endereço de destinatário nem local de entrega.  É ele que decide para
    # qual município vai a parcela municipal do IBS.
    municipio_fg_ibs: Mapped[str | None] = mapped_column(String(7))

    # Datas
    data_emissao: Mapped[datetime.date | None] = mapped_column(index=True)
    data_entrada_saida: Mapped[datetime.date | None] = mapped_column()

    # `modFrete` do XML — o mesmo código do `IND_FRT` do C100.  Fica nulo em
    # documento importado antes de o campo existir, e o gerador avisa em vez
    # de inventar quem pagou o frete.
    modalidade_frete: Mapped[str | None] = mapped_column(String(1))

    # Totais, como declarados no documento
    valor_total: Mapped[float] = mapped_column(default=0.0)
    valor_produtos: Mapped[float] = mapped_column(default=0.0)
    valor_desconto: Mapped[float] = mapped_column(default=0.0)
    valor_frete: Mapped[float] = mapped_column(default=0.0)
    valor_seguro: Mapped[float] = mapped_column(default=0.0)
    valor_outras: Mapped[float] = mapped_column(default=0.0)
    base_icms: Mapped[float] = mapped_column(default=0.0)
    valor_icms: Mapped[float] = mapped_column(default=0.0)
    valor_icms_st: Mapped[float] = mapped_column(default=0.0)
    valor_ipi: Mapped[float] = mapped_column(default=0.0)
    valor_pis: Mapped[float] = mapped_column(default=0.0)
    valor_cofins: Mapped[float] = mapped_column(default=0.0)
    # Os termos que faltavam para fechar o vNF pela regra W16-10 do MOC 7.0.
    # Nenhum deles é soma de parcela que este sistema saiba alterar — vêm do
    # documento e ficam como vieram; existem para que o total possa ser
    # recomposto com a fórmula inteira, e não com metade dela.
    valor_icms_desonerado: Mapped[float] = mapped_column(default=0.0)
    valor_fcp_st: Mapped[float] = mapped_column(default=0.0)
    valor_imposto_importacao: Mapped[float] = mapped_column(default=0.0)
    valor_ipi_devolvido: Mapped[float] = mapped_column(default=0.0)
    # `vServ` mora em `ISSQNtot`, e não em `ICMSTot` como os outros.
    valor_servicos: Mapped[float] = mapped_column(default=0.0)
    # `vNFTot` (W60 da NT 2025.002): o total COM os novos tributos.  Campo à
    # parte do `vNF`, não uma versão nova dele — somá-los ao `vNF` daria um
    # documento que a SEFAZ recusa.  Opcional no leiaute, com as regras de
    # validação ainda marcadas "implementação futura".
    valor_total_com_reforma: Mapped[float] = mapped_column(default=0.0)
    # Reforma: totais do documento.  Convivem com os de cima durante toda a
    # transição (2026–2032).
    valor_ibs: Mapped[float] = mapped_column(default=0.0)
    valor_cbs: Mapped[float] = mapped_column(default=0.0)
    valor_is: Mapped[float] = mapped_column(default=0.0)

    # Camada 1: o documento como chegou.
    xml_original: Mapped[str | None] = mapped_column(Text)
    hash_original: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    origem: Mapped[str | None] = mapped_column(String(40))  # arquivo, zip, api…
    nome_arquivo: Mapped[str | None] = mapped_column(String(255))
    adaptador: Mapped[str] = mapped_column(String(40), nullable=False)

    importado_em: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC)
    )

    itens: Mapped[list["ItemDocumentoFiscal"]] = relationship(
        back_populates="documento", cascade="all, delete-orphan"
    )
    ajustes: Mapped[list["AjusteFiscal"]] = relationship(
        back_populates="documento", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<DocumentoFiscal {self.especie} {self.numero} {self.chave[:8]}…>"


class ItemDocumentoFiscal(Base):
    """Um item do documento, normalizado.

    Os campos ficam com o nome do domínio fiscal, não o do XML: o mesmo
    `cfop` vem de `prod/CFOP` na NF-e e não existe na NFS-e, onde o adaptador
    o deixa nulo para a classificação preencher depois.
    """

    __tablename__ = "itens_documentos_fiscais"
    __table_args__ = (UniqueConstraint("documento_id", "numero_item", name="uq_item_documento"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[int] = mapped_column(
        ForeignKey("documentos_fiscais.id"), nullable=False, index=True
    )
    numero_item: Mapped[int] = mapped_column(nullable=False)

    codigo: Mapped[str | None] = mapped_column(String(60))
    descricao: Mapped[str | None] = mapped_column(String(255))
    ncm: Mapped[str | None] = mapped_column(String(8), index=True)
    cest: Mapped[str | None] = mapped_column(String(7))
    codigo_servico: Mapped[str | None] = mapped_column(String(20))
    unidade: Mapped[str | None] = mapped_column(String(6))
    quantidade: Mapped[float] = mapped_column(default=0.0)
    valor_unitario: Mapped[float] = mapped_column(default=0.0)
    valor_total: Mapped[float] = mapped_column(default=0.0)
    valor_desconto: Mapped[float] = mapped_column(default=0.0)
    valor_frete: Mapped[float] = mapped_column(default=0.0)
    valor_seguro: Mapped[float] = mapped_column(default=0.0)
    valor_outras: Mapped[float] = mapped_column(default=0.0)

    cfop: Mapped[str | None] = mapped_column(String(4), index=True)
    origem_mercadoria: Mapped[str | None] = mapped_column(String(1))
    cst_icms: Mapped[str | None] = mapped_column(String(3), index=True)
    csosn: Mapped[str | None] = mapped_column(String(3))
    base_icms: Mapped[float] = mapped_column(default=0.0)
    aliquota_icms: Mapped[float] = mapped_column(default=0.0)
    valor_icms: Mapped[float] = mapped_column(default=0.0)
    base_icms_st: Mapped[float] = mapped_column(default=0.0)
    valor_icms_st: Mapped[float] = mapped_column(default=0.0)
    valor_fcp: Mapped[float] = mapped_column(default=0.0)
    cst_ipi: Mapped[str | None] = mapped_column(String(2))
    valor_ipi: Mapped[float] = mapped_column(default=0.0)
    cst_pis: Mapped[str | None] = mapped_column(String(2), index=True)
    base_pis: Mapped[float] = mapped_column(default=0.0)
    aliquota_pis: Mapped[float] = mapped_column(default=0.0)
    valor_pis: Mapped[float] = mapped_column(default=0.0)
    cst_cofins: Mapped[str | None] = mapped_column(String(2), index=True)
    base_cofins: Mapped[float] = mapped_column(default=0.0)
    aliquota_cofins: Mapped[float] = mapped_column(default=0.0)
    valor_cofins: Mapped[float] = mapped_column(default=0.0)
    valor_iss: Mapped[float] = mapped_column(default=0.0)
    codigo_beneficio: Mapped[str | None] = mapped_column(String(10))
    # `veicProd/tpOp` — só existe em item de veículo novo.  Está aqui por uma
    # razão só: `tpOp = 2` (faturamento direto) muda a fórmula do vNF, e sem
    # conseguir reconhecer o caso o recálculo o trataria como comum.
    tipo_operacao_veiculo: Mapped[str | None] = mapped_column(String(1))

    # ── Reforma Tributária do Consumo (EC 132/2023, LC 214/2025) ──────────
    #
    # Estes campos CONVIVEM com os de ICMS/PIS/Cofins acima, não os
    # substituem: de 2026 a 2032 o mesmo item carrega os dois regimes.  Só em
    # 2033, com a extinção de ICMS e ISS, o bloco antigo deixa de ser
    # preenchido.  Modelar como substituição obrigaria a reescrever tudo na
    # virada de cada ano da transição.
    #
    # O IBS é UM tributo com DUAS destinações — estadual e municipal —, e o
    # XML traz alíquota e valor separados para cada uma.  Somá-los numa coluna
    # só perderia a informação que a apuração precisa, porque a partilha entre
    # os entes é o cerne do imposto.
    #
    # Vigência: os grupos passam a ser exigidos na NF-e em 03/08/2026
    # (NT 2025.002).  Ver docs/reforma-tributaria.md para o cronograma e a
    # procedência de cada informação.
    cst_ibscbs: Mapped[str | None] = mapped_column(String(3), index=True)
    # Os três primeiros dígitos repetem o CST; o resto detalha o enquadramento.
    # Largura folgada de propósito: a tabela é publicada pela SVRS e cresce.
    class_trib_ibscbs: Mapped[str | None] = mapped_column(String(10))
    base_ibscbs: Mapped[float] = mapped_column(default=0.0)

    aliquota_ibs_uf: Mapped[float] = mapped_column(default=0.0)
    valor_ibs_uf: Mapped[float] = mapped_column(default=0.0)
    aliquota_ibs_mun: Mapped[float] = mapped_column(default=0.0)
    valor_ibs_mun: Mapped[float] = mapped_column(default=0.0)

    aliquota_cbs: Mapped[float] = mapped_column(default=0.0)
    valor_cbs: Mapped[float] = mapped_column(default=0.0)

    # Reduções, diferimento e devolução.
    #
    # São TRÊS de cada, e não uma: a NT põe um `gRed`, um `gDif` e um
    # `gDevTrib` dentro de `gIBSUF`, de `gIBSMun` e de `gCBS`, cada um com o
    # seu percentual.  Uma coluna só obrigaria a somar valores de tributos
    # diferentes — e, no caso dos percentuais, a somar percentuais, que não
    # somam.  Ver UB21/UB24/UB26 (UF), UB40/UB43/UB45 (município) e
    # UB59/UB62/UB64 (CBS) da NT 2025.002 v1.50.
    percentual_reducao_ibs_uf: Mapped[float] = mapped_column(default=0.0)
    aliquota_efetiva_ibs_uf: Mapped[float] = mapped_column(default=0.0)
    valor_diferido_ibs_uf: Mapped[float] = mapped_column(default=0.0)
    valor_devolucao_ibs_uf: Mapped[float] = mapped_column(default=0.0)

    percentual_reducao_ibs_mun: Mapped[float] = mapped_column(default=0.0)
    aliquota_efetiva_ibs_mun: Mapped[float] = mapped_column(default=0.0)
    valor_diferido_ibs_mun: Mapped[float] = mapped_column(default=0.0)
    valor_devolucao_ibs_mun: Mapped[float] = mapped_column(default=0.0)

    percentual_reducao_cbs: Mapped[float] = mapped_column(default=0.0)
    aliquota_efetiva_cbs: Mapped[float] = mapped_column(default=0.0)
    valor_diferido_cbs: Mapped[float] = mapped_column(default=0.0)
    valor_devolucao_cbs: Mapped[float] = mapped_column(default=0.0)

    # Crédito presumido da operação (`gCredPresOper`, UB120).  O código é um
    # só; o percentual e o valor vêm separados para IBS (UB123) e CBS (UB127).
    codigo_credito_presumido: Mapped[str | None] = mapped_column(String(10))
    base_credito_presumido: Mapped[float] = mapped_column(default=0.0)
    percentual_credito_presumido_ibs: Mapped[float] = mapped_column(default=0.0)
    valor_credito_presumido_ibs: Mapped[float] = mapped_column(default=0.0)
    valor_credito_presumido_ibs_susp: Mapped[float] = mapped_column(default=0.0)
    percentual_credito_presumido_cbs: Mapped[float] = mapped_column(default=0.0)
    valor_credito_presumido_cbs: Mapped[float] = mapped_column(default=0.0)
    valor_credito_presumido_cbs_susp: Mapped[float] = mapped_column(default=0.0)

    # Monofásico.  A v1.50 da NT separou ad rem de ad valorem em quatro grupos
    # (IBS e CBS × ad rem e ad valorem), e o que vale para a apuração é o total
    # do item, que a própria NT fecha em `vTotIBSMonoItem`/`vTotCBSMonoItem`
    # (UB105a/UB105b) — filhos diretos de `gIBSCBSMono`, únicos qualquer que
    # tenha sido o regime.  Guardar o total é ler o que a NT já somou.
    #
    # A base é `qBCMono` (quantidade) no ad rem e `vBCMono` (valor) no ad
    # valorem: são grandezas diferentes e por isso colunas diferentes.
    quantidade_bc_mono: Mapped[float] = mapped_column(default=0.0)
    valor_bc_mono: Mapped[float] = mapped_column(default=0.0)
    valor_ibs_mono: Mapped[float] = mapped_column(default=0.0)
    valor_cbs_mono: Mapped[float] = mapped_column(default=0.0)
    # `gMonoReten` e `gMonoRet` são coisas opostas, e a NT as nomeia quase
    # igual: `Reten` é o imposto sobre o biocombustível a ser misturado, que
    # **soma** ao que se recolhe; `Ret` é o que já foi cobrado antes.  Trocar
    # um pelo outro erra o sinal do monofásico inteiro.
    valor_ibs_mono_reten: Mapped[float] = mapped_column(default=0.0)
    valor_cbs_mono_reten: Mapped[float] = mapped_column(default=0.0)
    valor_ibs_mono_retido: Mapped[float] = mapped_column(default=0.0)
    valor_cbs_mono_retido: Mapped[float] = mapped_column(default=0.0)
    # Mistura de etanol anidro fora do percentual obrigatório (art. 179, II da
    # LC 214/2025).  O mesmo campo é valor a recolher ou a ressarcir conforme o
    # `cClassTrib` — o sinal está no código, e o sistema não o interpreta.
    quantidade_bio_diferenca: Mapped[float] = mapped_column(default=0.0)
    valor_ibs_bio_diferenca: Mapped[float] = mapped_column(default=0.0)
    valor_cbs_bio_diferenca: Mapped[float] = mapped_column(default=0.0)

    # Transferência de crédito, ajuste de competência e estorno.  Os dois
    # primeiros são ALTERNATIVAS a `gIBSCBS` na mesma escolha do schema: um
    # item que transfere crédito não traz grupo de tributo nenhum, e por isso
    # os valores não podem ser lidos como complemento dos outros.
    valor_transf_credito_ibs: Mapped[float] = mapped_column(default=0.0)
    valor_transf_credito_cbs: Mapped[float] = mapped_column(default=0.0)
    # `competApur` (AAAA-MM) pode ser retroativo: é o que diz a que apuração o
    # ajuste pertence.  Sem ele o valor não tem destino.
    competencia_ajuste: Mapped[str | None] = mapped_column(String(7))
    valor_ajuste_compet_ibs: Mapped[float] = mapped_column(default=0.0)
    valor_ajuste_compet_cbs: Mapped[float] = mapped_column(default=0.0)
    valor_estorno_credito_ibs: Mapped[float] = mapped_column(default=0.0)
    valor_estorno_credito_cbs: Mapped[float] = mapped_column(default=0.0)

    # Imposto Seletivo.  Tem alíquota ad valorem E específica (por unidade
    # tributável) — bebidas e cigarros usam a segunda —, por isso a unidade e
    # a quantidade viajam junto.
    cst_is: Mapped[str | None] = mapped_column(String(3))
    class_trib_is: Mapped[str | None] = mapped_column(String(10))
    base_is: Mapped[float] = mapped_column(default=0.0)
    aliquota_is: Mapped[float] = mapped_column(default=0.0)
    aliquota_is_especifica: Mapped[float] = mapped_column(default=0.0)
    unidade_tributavel_is: Mapped[str | None] = mapped_column(String(6))
    quantidade_tributavel_is: Mapped[float] = mapped_column(default=0.0)
    valor_is: Mapped[float] = mapped_column(default=0.0)

    documento: Mapped["DocumentoFiscal"] = relationship(back_populates="itens")

    def __repr__(self):
        return f"<ItemDocumentoFiscal {self.numero_item} {self.codigo}>"


class AjusteFiscal(Base):
    """A camada de tratamento: um campo, um valor novo, uma justificativa.

    É aditiva de propósito.  Cada ajuste guarda o valor que o campo tinha
    quando foi criado (`valor_anterior`), quem o fez e por quê — e o valor
    efetivo de um campo é o do ajuste mais recente que o alcança.  Desfazer um
    lote é apagar os ajustes daquele lote; nada mais precisa ser tocado, e o
    normalizado nunca foi alterado.

    `origem` distingue o que a regra sugeriu do que a pessoa decidiu, que é o
    que a §6 do pedido chama de "informação sugerida" contra "informação
    alterada pelo usuário".
    """

    __tablename__ = "ajustes_fiscais"

    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[int] = mapped_column(
        ForeignKey("documentos_fiscais.id"), nullable=False, index=True
    )
    # Nulo = ajuste no cabeçalho do documento.
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("itens_documentos_fiscais.id"), index=True
    )

    campo: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    valor_anterior: Mapped[str | None] = mapped_column(Text)
    valor_novo: Mapped[str | None] = mapped_column(Text)

    origem: Mapped[str] = mapped_column(String(12), nullable=False)  # regra|usuario
    regra: Mapped[str | None] = mapped_column(String(120))
    motivo: Mapped[str | None] = mapped_column(Text)
    lote: Mapped[str | None] = mapped_column(String(32), index=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    criado_em: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC)
    )

    documento: Mapped["DocumentoFiscal"] = relationship(back_populates="ajustes")

    def __repr__(self):
        return f"<AjusteFiscal {self.campo}={self.valor_novo!r} ({self.origem})>"


class RegraFiscal(Base):
    """Uma classificação recorrente, guardada para não ser refeita a cada mês.

    "Para este fornecedor e este NCM, use sempre este CFOP" é a forma que o
    conhecimento fiscal do escritório toma — e hoje ele mora na cabeça de
    alguém.  Aqui ele vira dado.

    **Condições e ações são estruturadas, não expressão avaliada.**  Um campo
    de texto com uma expressão que o sistema executa seria muito mais
    expressivo, e transformaria o banco em superfície de execução de código:
    quem escrevesse na tabela rodaria o que quisesse no servidor.  A troco de
    conveniência que este domínio não exige — as condições reais são
    comparações entre um campo e um valor.

    Formato de `condicoes` (todas precisam casar):

        [{"campo": "ncm", "operador": "comeca_com", "valor": "2203"},
         {"campo": "emitente_cnpj", "operador": "igual", "valor": "1234…"}]

    Formato de `acoes`:

        [{"campo": "cfop", "valor": "6404"}]
    """

    __tablename__ = "regras_fiscais"

    id: Mapped[int] = mapped_column(primary_key=True)
    escritorio_id: Mapped[int | None] = mapped_column(ForeignKey("escritorios.id"), index=True)
    # Nulo = vale para todas as empresas do escritório.
    empresa_id: Mapped[int | None] = mapped_column(ForeignKey("empresas.id"), index=True)

    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    descricao: Mapped[str | None] = mapped_column(Text)
    # Maior vence.  Empate entre regras que agem no mesmo campo é conflito, e
    # o motor o denuncia em vez de escolher por sorteio.
    prioridade: Mapped[int] = mapped_column(default=0, index=True)
    condicoes: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    acoes: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    # A que obrigação a regra serve (`efd_icms`, `efd_contribuicoes`…); nulo
    # vale para todas.
    obrigacao: Mapped[str | None] = mapped_column(String(30))
    # Regra fiscal nasce e morre com a legislação: uma que valia até dezembro
    # não pode ser aplicada a documento de janeiro.
    vigencia_inicio: Mapped[datetime.date | None] = mapped_column()
    vigencia_fim: Mapped[datetime.date | None] = mapped_column()
    confianca: Mapped[float] = mapped_column(default=1.0)
    ativa: Mapped[bool] = mapped_column(default=True, index=True)

    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    criado_em: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC)
    )
    atualizado_em: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    def __repr__(self):
        return f"<RegraFiscal {self.nome!r} p={self.prioridade}>"


class AjusteApuracao(Base):
    """Um ajuste da apuração do ICMS — o registro E111 e o que ele compõe.

    A apuração do bloco E era a soma dos documentos e mais nada.  Empresa com
    benefício fiscal, crédito outorgado, estorno ou dedução tem valores que
    **não estão em nota nenhuma**, e sem eles o imposto sai errado nos dois
    sentidos.

    O código vem da **tabela 5.1.1**, que é de cada Secretaria da Fazenda: os
    quatro últimos dígitos e o que cada um significa mudam por estado, e o
    sistema não os conhece nem tenta conhecer.  O que ele lê é a estrutura, que
    é nacional (Ato COTEPE/ICMS 09/2008) — `PRBCDDDD`:

      * `PR` — a UF, que tem de ser a da empresa;
      * `B`  — a apuração: 0 ICMS, 1 ICMS-ST, 2 DIFAL, 3 FCP;
      * `C`  — a utilização, que decide em que campo do E110 o valor entra:
        0 outros débitos, 1 estorno de créditos, 2 outros créditos, 3 estorno
        de débitos, 4 deduções, 5 débito especial, 9 controle extra-apuração;
      * `DDDD` — o sequencial da tabela do estado.

    Ou seja: **quem informa o código informa junto o tratamento**, e o sistema
    deriva o resto sem palpite.
    """

    __tablename__ = "ajustes_apuracao"

    id: Mapped[int] = mapped_column(primary_key=True)
    escritorio_id: Mapped[int | None] = mapped_column(ForeignKey("escritorios.id"), index=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), nullable=False, index=True)

    # A obrigação a que o ajuste pertence.  Hoje só `efd_icms`; o campo existe
    # para que um ajuste de outra escrituração não entre nesta por engano.
    tipo: Mapped[str] = mapped_column(String(30), nullable=False, default="efd_icms", index=True)
    data_inicio: Mapped[datetime.date] = mapped_column(nullable=False, index=True)
    data_fim: Mapped[datetime.date] = mapped_column(nullable=False)

    cod_aj: Mapped[str] = mapped_column(String(8), nullable=False)
    descricao: Mapped[str | None] = mapped_column(String(255))
    valor: Mapped[float] = mapped_column(nullable=False, default=0.0)

    criado_em: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC)
    )
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))

    def __repr__(self):
        return f"<AjusteApuracao {self.cod_aj} {self.valor}>"


class Escrituracao(Base):
    """A terceira camada: o arquivo que efetivamente saiu.

    As outras duas já existem — o documento original (`xml_original`, byte a
    byte) e o tratamento fiscal (`AjusteFiscal`, do qual sai a camada
    efetiva).  Faltava guardar o que foi **entregue ao Fisco**, que não é
    dedutível de nenhuma das duas.

    **O conteúdo é guardado, não reconstruído.**  Regerar o período depois
    produziria outro arquivo assim que qualquer ajuste mudasse — e é
    justamente quando algo muda que se precisa saber o que foi enviado antes.
    Um sistema que reconstrói responde "o que eu enviaria hoje"; a pergunta da
    intimação é "o que você enviou".

    **A linha é imutável.**  Regerar o mesmo período cria outra escrituração;
    esta nunca é alterada.  Sem isso a prova valeria o quanto vale um
    documento que a parte interessada pode reescrever.

    O `hash_conteudo` é do texto exatamente como saiu, com CRLF, e serve para
    conferir contra o arquivo que o contribuinte tem em mãos.
    """

    __tablename__ = "escrituracoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    escritorio_id: Mapped[int | None] = mapped_column(ForeignKey("escritorios.id"), index=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), nullable=False, index=True)

    # `efd_icms`, `efd_contribuicoes`.
    tipo: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    data_inicio: Mapped[datetime.date] = mapped_column(nullable=False, index=True)
    data_fim: Mapped[datetime.date] = mapped_column(nullable=False)

    conteudo: Mapped[str] = mapped_column(Text, nullable=False)
    hash_conteudo: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    total_linhas: Mapped[int] = mapped_column(nullable=False, default=0)

    # Os avisos como estavam na hora de gerar.  Fazem parte do que a pessoa viu
    # ao decidir transmitir, e o gerador de amanhã pode avisar outra coisa.
    avisos: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    gerada_em: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=lambda: datetime.datetime.now(datetime.UTC)
    )
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))

    # Qual das gerações foi de fato entregue.  Nulo em todas até que alguém
    # diga — o sistema não transmite, e adivinhar pela mais recente diria que
    # foi entregue justamente a que se acabou de gerar para conferir.
    transmitida_em: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    # O número do recibo devolvido pelo Fisco.  É o que liga o arquivo daqui
    # ao que está lá; sem ele, "transmitida" é palavra de quem marcou.
    recibo: Mapped[str | None] = mapped_column(String(60))
    transmitida_por_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))

    documentos: Mapped[list["EscrituracaoDocumento"]] = relationship(
        back_populates="escrituracao", cascade="all, delete-orphan"
    )

    @property
    def transmitida(self) -> bool:
        return self.transmitida_em is not None

    def __repr__(self):
        return f"<Escrituracao {self.tipo} {self.data_inicio}..{self.data_fim}>"


class EscrituracaoDocumento(Base):
    """Que documentos entraram em que arquivo.

    Responde as duas perguntas que a intimação faz: "esta nota foi
    escriturada?" e "em qual arquivo?".  Sem a ligação, a única resposta
    possível seria reabrir cada arquivo gerado e procurar a chave dentro.
    """

    __tablename__ = "escrituracoes_documentos"
    __table_args__ = (
        UniqueConstraint("escrituracao_id", "documento_id", name="uq_escrituracao_documento"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    escrituracao_id: Mapped[int] = mapped_column(
        ForeignKey("escrituracoes.id"), nullable=False, index=True
    )
    documento_id: Mapped[int] = mapped_column(
        ForeignKey("documentos_fiscais.id"), nullable=False, index=True
    )

    escrituracao: Mapped["Escrituracao"] = relationship(back_populates="documentos")


# ── Engine Factory (Fase 17: banco configurável) ───────────────────────────


# Casos especiais do SQLite que têm URL canônica própria.
_SQLITE_INLINE_URLS = {
    ":memory:": "sqlite:///:memory:",
    "": "sqlite:///:memory:",
}


def _caminho_para_url_sqlite(caminho: str) -> str:
    """Converte caminho de arquivo em URL SQLite preservando o contrato da Fase 16.

    Regras:
        * ``:memory:`` e vazio → ``sqlite:///:memory:`` (banco em memória,
          uma instância por engine — esta é a forma historicamente usada).
        * Caminho absoluto → ``sqlite:///{caminho}`` (3 barras + path).
        * Caminho relativo → ``sqlite:///./{caminho}`` (resolve a partir do cwd).
    """
    if caminho in _SQLITE_INLINE_URLS:
        return _SQLITE_INLINE_URLS[caminho]
    if caminho.startswith("/"):
        return f"sqlite:///{caminho}"
    return f"sqlite:///./{caminho}"


def _normalizar_database_url(caminho: str | None) -> str:
    """Converte um caminho SQLite em URL, mas mantém URLs explícitas."""
    if caminho is None:
        return get_settings().database_url
    if "://" in caminho:
        return caminho
    return _caminho_para_url_sqlite(caminho)


def criar_engine(
    caminho: str | None = None,
    *,
    url: str | None = None,
    echo: bool | None = None,
):
    """Cria engine SQLAlchemy.

    Parâmetros:
        caminho: aceita caminho de arquivo (legado, p.ex. ``":memory:"`` ou
            ``"sped_hub.db"``) ou uma URL completa (``"sqlite:///..."``,
            ``"postgresql+psycopg://..."``).  Quando contém ``://`` é tratado
            como URL diretamente.
        url: URL canônica (tem precedência sobre ``caminho``).
        echo: ativa ``echo`` do SQLAlchemy.  Quando ``None``, usa
            ``settings.database_echo``.

    Para bancos SQLite, ativa ``WAL`` e ``foreign_keys=ON`` (exceto em
    ``:memory:``, onde WAL não é suportado e é ignorado silenciosamente).
    Para outros backends, apenas retorna a engine configurada para a URL
    fornecida.
    """
    final_url = url or _normalizar_database_url(caminho) or get_settings().database_url

    if echo is None:
        echo = get_settings().database_echo

    is_sqlite_memory = final_url == "sqlite:///:memory:"
    is_sqlite = final_url.startswith("sqlite")

    engine = create_engine(final_url, echo=echo, future=True)

    if is_sqlite and not is_sqlite_memory:

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            try:
                # WAL não funciona em :memory:, mas fora dele é seguro.
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

    return engine


_ENGINES: dict[tuple[str, bool], Engine] = {}
_ENGINES_LOCK = threading.Lock()
# Engines cujo schema já foi criado.  A chave é o *objeto* engine, não a URL:
# uma engine nova (banco novo, arquivo temporário de teste) nunca é confundida
# com uma anterior que apontava para o mesmo caminho.
_SCHEMA_PRONTO: weakref.WeakSet = weakref.WeakSet()


def obter_engine(
    caminho: str | None = None,
    *,
    url: str | None = None,
    echo: bool | None = None,
) -> Engine:
    """Engine reutilizada por processo — mesma assinatura de :func:`criar_engine`.

    ``criar_engine`` devolve uma engine nova a cada chamada, o que significa um
    pool de conexões descartável por uso.  Nos caminhos quentes (validação de
    sessão, resolução de tenant, cada request do dashboard) isso custa uma
    conexão nova por request; em Postgres, um handshake de rede por request.

    Bancos ``:memory:`` nunca são reaproveitados: cada engine em memória é um
    banco distinto, e compartilhá-las mudaria o comportamento de quem espera
    isolamento (as fixtures de teste, entre outros).
    """
    final_url = url or _normalizar_database_url(caminho) or get_settings().database_url
    if final_url == "sqlite:///:memory:":
        return criar_engine(caminho, url=url, echo=echo)

    chave = (final_url, bool(get_settings().database_echo if echo is None else echo))
    engine = _ENGINES.get(chave)
    if engine is not None:
        return engine
    with _ENGINES_LOCK:
        engine = _ENGINES.get(chave)
        if engine is None:
            engine = criar_engine(caminho, url=url, echo=echo)
            _ENGINES[chave] = engine
    return engine


def _descartar_engines_apos_fork() -> None:
    """Zera o cache no processo filho após ``fork``.

    Conexões e pools do SQLAlchemy não podem ser compartilhados entre
    processos: herdar o socket/handle do pai corrompe o estado dos dois.  O
    worker de fila usa ``multiprocessing``, então este hook não é teórico.
    """
    _ENGINES.clear()
    _SCHEMA_PRONTO.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_descartar_engines_apos_fork)


def init_db(engine: Engine | None = None) -> None:
    """Cria todas as tabelas.

    Quando ``engine`` é ``None``, cria uma nova a partir das settings.  Útil
    para CLI/workers que já estão configurados para um banco específico
    através do ambiente.
    """
    if engine is None:
        engine = criar_engine()
    Base.metadata.create_all(engine)
    _SCHEMA_PRONTO.add(engine)


def init_db_once(engine: Engine) -> None:
    """``init_db`` idempotente por engine, para os caminhos quentes.

    ``create_all`` é idempotente no resultado, mas não no custo: ele reflete as
    24 tabelas no banco a cada chamada.  Rodando por request, era ~2,9 ms dos
    ~3,1 ms gastos só para validar um token de sessão.
    """
    if engine in _SCHEMA_PRONTO:
        return
    init_db(engine)


def truncar_para_coluna(modelo: type, campo: str, valor: str | None) -> str | None:
    """Corta ``valor`` no limite declarado da coluna ``modelo.campo``.

    O SQLite ignora o tamanho de ``String(n)`` e grava o que vier; o
    PostgreSQL rejeita com erro.  Sem isto, um cabeçalho ``User-Agent`` acima
    de 512 caracteres — que qualquer cliente pode enviar — faz o login
    funcionar em SQLite e falhar em Postgres.

    Aplica-se a campos de telemetria (IP, user-agent, recurso auditado), onde
    registrar uma versão truncada é melhor que perder o evento inteiro.  Campos
    de negócio não devem passar por aqui: para eles, o erro é a resposta certa.

    O limite vem da metadata do SQLAlchemy, e não de uma constante repetida,
    para não divergir do schema quando a coluna mudar.
    """
    if valor is None:
        return None
    limite = getattr(modelo.__table__.c[campo].type, "length", None)
    if limite is None or len(valor) <= limite:
        return valor
    return valor[:limite]


def get_session(engine: Engine | None = None) -> Session:
    """Retorna uma nova sessão.

    Mantido retrocompatível: aceita uma engine explícita ou usa settings.
    """
    if engine is None:
        engine = criar_engine()
    return Session(engine)
