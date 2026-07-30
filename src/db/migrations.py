"""Aplicação programática das migrações Alembic (Fase 17, Etapa 3).

Política de versionamento do schema — ver também ``docs/migrations.md``:

* **PostgreSQL** é versionado por migração.  ``create_all`` nunca deve tocar
  um banco Postgres de produção: ele cria o que falta, mas não altera nem
  remove nada, então o schema silenciosamente diverge dos modelos.
* **SQLite** (desenvolvimento e testes) continua com ``create_all``, que é
  mais rápido e não exige histórico.  As migrações são exercitadas contra os
  dois backends no CI mesmo assim, para não apodrecerem.

O ponto delicado é a concorrência: ``web`` e ``worker`` sobem ao mesmo tempo
no ``docker-compose``, e duas migrações simultâneas no mesmo banco se
atropelam.  Por isso o upgrade em Postgres roda dentro de um *advisory lock*
transacional — o segundo processo espera o primeiro terminar e então encontra
o schema já em ``head``, sem fazer nada.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from alembic import command
from src.db.models import criar_engine
from src.settings import caminho_para_url_sqlite

logger = logging.getLogger("sped-hub.migrations")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
ALEMBIC_DIR = PROJECT_ROOT / "alembic"

# Chave arbitrária mas fixa do advisory lock.  Precisa ser constante entre
# processos para que eles de fato disputem o mesmo lock.
_LOCK_KEY = 8_150_117


def _normalizar(url: str | None) -> str | None:
    """Aceita URL pronta ou caminho de arquivo, como o resto da aplicação.

    A CLI recebe `--db` que pode ser qualquer um dos dois.
    """
    return caminho_para_url_sqlite(url) if url else None


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    if url:
        # `set_main_option` grava no configparser, que trata `%` como início
        # de interpolação.  Sem escapar, uma senha com `%` — ou um parâmetro
        # percent-encoded na query string — derruba o upgrade com
        # "invalid interpolation syntax".
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def revisao_head() -> str | None:
    """Revisão mais recente disponível no diretório de migrações."""
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def revisao_atual(engine) -> str | None:
    """Revisão em que o banco está, ou ``None`` se nunca foi migrado."""
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def upgrade_head(url: str | None = None) -> str | None:
    """Leva o banco até a revisão mais recente.  Idempotente.

    Em PostgreSQL, segura um advisory lock transacional durante toda a
    migração: sem ele, dois containers subindo juntos executam o mesmo
    ``CREATE TABLE`` e um dos dois quebra.
    """
    url = _normalizar(url)
    engine = criar_engine(url=url) if url else criar_engine()
    cfg = alembic_config(url)
    try:
        with engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.exec_driver_sql(f"SELECT pg_advisory_xact_lock({_LOCK_KEY})")
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
        atual = revisao_atual(engine)
        logger.info("Schema migrado para %s", atual)
        return atual
    finally:
        engine.dispose()


def stamp_head(url: str | None = None) -> None:
    """Marca o banco como estando em ``head`` sem executar as migrações.

    Serve para adotar o Alembic num banco que já tem o schema completo —
    o caso de qualquer instalação anterior à Etapa 3.  Ver
    ``docs/migrations.md``.
    """
    url = _normalizar(url)
    engine = criar_engine(url=url) if url else criar_engine()
    cfg = alembic_config(url)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.stamp(cfg, "head")
        logger.info("Schema marcado como %s sem executar migrações", revisao_head())
    finally:
        engine.dispose()


# ═══════════════════════════════════════════════════════════════════════════
# Migração de DADOS entre bancos (SQLite → PostgreSQL, e o caminho inverso)
# ═══════════════════════════════════════════════════════════════════════════
#
# O que está acima migra **schema**.  Isto migra **conteúdo**: até aqui, um
# escritório que rodava em SQLite e queria PostgreSQL só tinha o caminho de
# reimportar todas as ECDs — perdendo usuários, mapeamentos, visões de filtro,
# chaves de API e o histórico de auditoria, que não vêm de arquivo nenhum.


class ErroDeMigracaoDeDados(RuntimeError):
    """Migração de dados recusada ou interrompida.  Nada foi gravado."""


def _tabelas_em_ordem():
    """Tabelas em ordem de dependência — pai antes de filho.

    `sorted_tables` do SQLAlchemy já resolve isso a partir das chaves
    estrangeiras.  Copiar fora de ordem violaria a FK no destino.
    """
    from src.db.models import Base

    return list(Base.metadata.sorted_tables)


def _contagens(engine) -> dict[str, int]:
    """Quantas linhas cada tabela tem.  Tabela ausente conta como zero."""
    from sqlalchemy import func, inspect, select

    existentes = set(inspect(engine).get_table_names())
    contagens: dict[str, int] = {}
    with engine.connect() as conexao:
        for tabela in _tabelas_em_ordem():
            if tabela.name not in existentes:
                contagens[tabela.name] = 0
                continue
            contagens[tabela.name] = (
                conexao.execute(select(func.count()).select_from(tabela)).scalar() or 0
            )
    return contagens


def _corrigir_sequencias(conexao, tabelas) -> None:
    """Avança as sequências do Postgres além dos ids copiados.

    É o defeito clássico deste tipo de migração, e ele é **silencioso**: as
    linhas chegam com id explícito, a sequência continua em 1, e a migração
    parece ter dado certo.  O erro só aparece quando o escritório cadastra a
    próxima empresa e recebe violação de chave primária.
    """
    from sqlalchemy import text

    if conexao.dialect.name != "postgresql":
        return
    for tabela in tabelas:
        for coluna in tabela.primary_key.columns:
            if not isinstance(coluna.type.python_type, type):
                continue
            if coluna.type.python_type is not int:
                continue
            conexao.execute(
                text(
                    "SELECT setval("
                    "  pg_get_serial_sequence(:tabela, :coluna),"
                    f"  COALESCE((SELECT MAX({coluna.name}) FROM {tabela.name}), 0) + 1,"
                    "  false"
                    ")"
                ).bindparams(tabela=tabela.name, coluna=coluna.name),
            )


def migrar_dados(origem: str, destino: str, *, lote: int = 1_000) -> dict[str, int]:
    """Copia o conteúdo de `origem` para `destino`, preservando os ids.

    Preservar id é obrigatório, não conveniência: as chaves estrangeiras do
    banco inteiro apontam para eles, e renumerar exigiria reescrever cada
    referência — em `partidas`, `saldos_periodicos` e `lancamentos`, que são as
    tabelas grandes.

    Garantias:

    * **O destino precisa estar vazio.**  Migrar sobre banco com dados é
      escolha que este comando não toma sozinho: no melhor caso viola chave
      primária, no pior mistura a escrituração de dois lugares sem avisar.
    * **Tudo ou nada.**  A cópia inteira roda em uma transação no destino.
      Metade da escrituração migrada é pior que nenhuma, porque parece
      completa — e é dela que sairia um balanço errado.
    * **Sequências corrigidas.**  Ver `_corrigir_sequencias`.
    * **Memória constante.**  As linhas saem em lotes; `partidas` de uma ECD
      real não cabe em memória.

    Devolve `{tabela: linhas copiadas}`.  Levanta `ErroDeMigracaoDeDados` se o
    destino não estiver vazio ou se o schema dele não existir.
    """
    from sqlalchemy import inspect, select

    origem_url = _normalizar(origem)
    destino_url = _normalizar(destino)
    if not origem_url or not destino_url:
        raise ErroDeMigracaoDeDados("origem e destino são obrigatórios")
    if origem_url == destino_url:
        raise ErroDeMigracaoDeDados("origem e destino são o mesmo banco")

    motor_origem = criar_engine(url=origem_url)
    motor_destino = criar_engine(url=destino_url)
    try:
        tabelas = _tabelas_em_ordem()
        faltando = {t.name for t in tabelas} - set(inspect(motor_destino).get_table_names())
        if faltando:
            raise ErroDeMigracaoDeDados(
                f"o destino não tem o schema completo (faltam {len(faltando)} tabelas). "
                "Rode `sped-hub migrar aplicar --db <destino>` antes."
            )

        ocupadas = {nome: n for nome, n in _contagens(motor_destino).items() if n}
        if ocupadas:
            raise ErroDeMigracaoDeDados(
                f"o destino já tem dados ({ocupadas}) — migrar sobre banco ocupado "
                "misturaria a escrituração de dois lugares. Use um banco vazio."
            )

        copiadas: dict[str, int] = {}
        with motor_destino.begin() as saida, motor_origem.connect() as entrada:
            existentes_na_origem = set(inspect(motor_origem).get_table_names())
            for tabela in tabelas:
                if tabela.name not in existentes_na_origem:
                    copiadas[tabela.name] = 0
                    continue
                total = 0
                resultado = entrada.execution_options(stream_results=True).execute(select(tabela))
                while True:
                    linhas = resultado.fetchmany(lote)
                    if not linhas:
                        break
                    saida.execute(tabela.insert(), [dict(linha._mapping) for linha in linhas])
                    total += len(linhas)
                copiadas[tabela.name] = total
                if total:
                    logger.info("Migração de dados: %s → %d linhas", tabela.name, total)
            _corrigir_sequencias(saida, tabelas)

        logger.info(
            "Migração de dados concluída: %d linhas em %d tabelas",
            sum(copiadas.values()),
            sum(1 for n in copiadas.values() if n),
        )
        return copiadas
    finally:
        motor_origem.dispose()
        motor_destino.dispose()


def conferir_migracao_de_dados(origem: str, destino: str) -> dict[str, tuple[int, int]]:
    """Compara as contagens dos dois bancos.  Devolve só o que divergir.

    Existe porque "a migração não deu erro" não é o mesmo que "os dados
    chegaram". Quem move a escrituração de um escritório precisa de uma
    conferência que não seja a própria migração se autodeclarando correta.
    """
    motor_origem = criar_engine(url=_normalizar(origem))
    motor_destino = criar_engine(url=_normalizar(destino))
    try:
        antes, depois = _contagens(motor_origem), _contagens(motor_destino)
    finally:
        motor_origem.dispose()
        motor_destino.dispose()
    return {
        nome: (antes[nome], depois.get(nome, 0))
        for nome in antes
        if antes[nome] != depois.get(nome, 0)
    }
