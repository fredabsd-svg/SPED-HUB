"""Consulta desperdiçada em caminho quente (Fase 35).

Dois achados da revisão do aplicativo, ambos medidos antes do conserto.

**1. N+1 na validação de partidas dobradas.** Uma consulta de partidas por
lançamento:

    ANTIGA: 6 achados, 3.002 consultas, 0.57s
    NOVA:   6 achados,     1 consulta,  0.01s

Numa ECD de 240 mil lançamentos são ~240 mil viagens ao banco e ~54 s só nesta
validação, em SQLite local. Sobre PostgreSQL em rede, minutos de pura latência,
para uma das oito validações.

**2. `create_all` em toda requisição.** `RateLimiter._get_config` roda em toda
requisição autenticada e criava uma engine nova mais um `create_all` refletindo
as 24 tabelas:

    antes: 3,13 ms por requisição
    depois: 0,37 ms

O projeto já tinha diagnosticado isso: `init_db_once` existe com o comentário
"era ~2,9 ms dos ~3,1 ms gastos só para validar um token de sessão". A correção
tinha sido aplicada na autenticação e não nos outros oito pontos — e só trocar
a função não bastava, porque `init_db_once` guarda por *objeto* engine e
`criar_engine` cria uma nova a cada chamada. Precisava vir com `obter_engine`,
que é a versão em cache.
"""

from __future__ import annotations

import ast
import datetime
import pathlib

import pytest
import sqlalchemy
from sqlalchemy import select

from src.db.models import (
    ECD,
    Empresa,
    Lancamento,
    Partida,
    criar_engine,
    get_session,
    init_db,
)
from src.validators.integridade import ValidadorIntegridade

REPO = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def ecd_com_lancamentos(tmp_path):
    """ECD com lançamentos balanceados, desbalanceados e um sem partida."""
    referencia = f"sqlite:///{tmp_path / 'validacao.db'}"
    engine = criar_engine(url=referencia)
    init_db(engine)
    sessao = get_session(engine)

    empresa = Empresa(cnpj="00123456000199", nome="Cliente")
    sessao.add(empresa)
    sessao.flush()
    ecd = ECD(
        empresa_id=empresa.id,
        leiaute="9",
        dt_ini=datetime.date(2024, 1, 1),
        dt_fin=datetime.date(2024, 12, 31),
        importado_em=datetime.datetime.now(datetime.UTC),
    )
    sessao.add(ecd)
    sessao.flush()

    desbalanceados = []
    for i in range(400):
        lanc = Lancamento(
            ecd_id=ecd.id,
            num_lcto=str(i),
            dt_lcto=datetime.date(2024, 6, 1),
            vl_lcto=100.0,
            ind_lcto="N",
        )
        sessao.add(lanc)
        sessao.flush()
        if i % 100 == 7:
            sessao.add(Partida(lancamento_id=lanc.id, cod_cta="1", vl_dc=100.0, ind_dc="D"))
            sessao.add(Partida(lancamento_id=lanc.id, cod_cta="2", vl_dc=99.90, ind_dc="C"))
            desbalanceados.append(str(i))
        elif i % 137 == 3:
            pass  # lançamento sem partida nenhuma
        else:
            sessao.add(Partida(lancamento_id=lanc.id, cod_cta="1", vl_dc=100.0, ind_dc="D"))
            sessao.add(Partida(lancamento_id=lanc.id, cod_cta="2", vl_dc=100.0, ind_dc="C"))
    sessao.commit()

    yield sessao, ecd.id, sorted(desbalanceados)
    sessao.close()
    engine.dispose()


def _contando_consultas(funcao):
    """Executa `funcao` contando os statements emitidos."""
    consultas: list[str] = []

    def espiar(conn, cursor, statement, parameters, context, executemany):
        consultas.append(statement)

    sqlalchemy.event.listen(sqlalchemy.engine.Engine, "before_cursor_execute", espiar)
    try:
        resultado = funcao()
    finally:
        sqlalchemy.event.remove(sqlalchemy.engine.Engine, "before_cursor_execute", espiar)
    return resultado, consultas


def _implementacao_antiga(sessao, ecd_id):
    """A versão N+1, mantida aqui como referência de comportamento.

    O que importa não é ser rápida — é o resultado da versão nova ser
    **idêntico** ao dela. Otimização que muda o que o contador vê não é
    otimização.
    """
    achados = []
    for lanc in sessao.execute(select(Lancamento).where(Lancamento.ecd_id == ecd_id)).scalars():
        partidas = list(
            sessao.execute(select(Partida).where(Partida.lancamento_id == lanc.id)).scalars()
        )
        debitos = sum(p.vl_dc for p in partidas if p.ind_dc == "D")
        creditos = sum(p.vl_dc for p in partidas if p.ind_dc == "C")
        if abs(debitos - creditos) > 0.005:
            achados.append(
                (lanc.num_lcto, round(debitos, 2), round(creditos, 2), round(debitos - creditos, 2))
            )
    return sorted(achados)


def _resumo(inconsistencias):
    return sorted(
        (
            i.detalhes["num_lcto"],
            i.detalhes["total_debitos"],
            i.detalhes["total_creditos"],
            i.detalhes["diferenca"],
        )
        for i in inconsistencias
    )


class TestValidacaoDePartidasDobradas:
    def test_resultado_identico_ao_da_versao_n_mais_um(self, ecd_com_lancamentos):
        """A âncora do PR: mesmo resultado, sem o N+1."""
        sessao, ecd_id, _ = ecd_com_lancamentos
        validador = ValidadorIntegridade(sessao, ecd_id)

        assert _resumo(validador._validar_partidas_dobradas()) == _implementacao_antiga(
            sessao, ecd_id
        )

    def test_acha_exatamente_os_desbalanceados(self, ecd_com_lancamentos):
        sessao, ecd_id, esperados = ecd_com_lancamentos

        achados = ValidadorIntegridade(sessao, ecd_id)._validar_partidas_dobradas()

        assert sorted(i.detalhes["num_lcto"] for i in achados) == esperados

    def test_uma_consulta_so(self, ecd_com_lancamentos):
        """400 lançamentos não podem custar 400 viagens ao banco."""
        sessao, ecd_id, _ = ecd_com_lancamentos
        validador = ValidadorIntegridade(sessao, ecd_id)

        _, consultas = _contando_consultas(validador._validar_partidas_dobradas)

        assert len(consultas) == 1, (
            f"a validação emitiu {len(consultas)} consultas para 400 lançamentos — "
            "sobre PostgreSQL em rede isso é latência multiplicada pelo tamanho da ECD"
        )

    def test_o_custo_nao_cresce_com_o_tamanho(self, ecd_com_lancamentos, tmp_path):
        """Contar 1 consulta num tamanho só não prova que não cresce.

        Uma implementação que fizesse `ceil(n/400)` consultas passaria no teste
        acima. Este compara dois tamanhos.
        """
        sessao_grande, ecd_grande, _ = ecd_com_lancamentos

        referencia = f"sqlite:///{tmp_path / 'pequena.db'}"
        engine = criar_engine(url=referencia)
        init_db(engine)
        sessao_pequena = get_session(engine)
        try:
            empresa = Empresa(cnpj="00123456000188", nome="Pequeno")
            sessao_pequena.add(empresa)
            sessao_pequena.flush()
            ecd = ECD(
                empresa_id=empresa.id,
                leiaute="9",
                dt_ini=datetime.date(2024, 1, 1),
                dt_fin=datetime.date(2024, 12, 31),
                importado_em=datetime.datetime.now(datetime.UTC),
            )
            sessao_pequena.add(ecd)
            sessao_pequena.flush()
            lanc = Lancamento(
                ecd_id=ecd.id,
                num_lcto="1",
                dt_lcto=datetime.date(2024, 6, 1),
                vl_lcto=10.0,
                ind_lcto="N",
            )
            sessao_pequena.add(lanc)
            sessao_pequena.flush()
            sessao_pequena.add(Partida(lancamento_id=lanc.id, cod_cta="1", vl_dc=10.0, ind_dc="D"))
            sessao_pequena.add(Partida(lancamento_id=lanc.id, cod_cta="2", vl_dc=10.0, ind_dc="C"))
            sessao_pequena.commit()

            _, poucas = _contando_consultas(
                ValidadorIntegridade(sessao_pequena, ecd.id)._validar_partidas_dobradas
            )
        finally:
            sessao_pequena.close()
            engine.dispose()

        _, muitas = _contando_consultas(
            ValidadorIntegridade(sessao_grande, ecd_grande)._validar_partidas_dobradas
        )

        assert len(poucas) == len(muitas), (
            f"1 lançamento custou {len(poucas)} consulta(s) e 400 custaram "
            f"{len(muitas)} — o custo cresce com o tamanho da escrituração"
        )

    def test_lancamento_sem_partida_nao_e_reportado(self, ecd_com_lancamentos):
        """Soma zero dos dois lados: não há desequilíbrio a reportar."""
        sessao, ecd_id, esperados = ecd_com_lancamentos

        achados = ValidadorIntegridade(sessao, ecd_id)._validar_partidas_dobradas()

        assert "3" not in [i.detalhes["num_lcto"] for i in achados]
        assert sorted(i.detalhes["num_lcto"] for i in achados) == esperados

    def test_lancamento_com_partida_de_um_lado_so_e_reportado(self, ecd_com_lancamentos):
        """Débito sem contrapartida nenhuma é o desequilíbrio mais grave.

        O cenário não era construído em teste nenhum antes deste. Ele não
        distingue `LEFT` de `INNER JOIN` nem exige `COALESCE` — cheguei a
        afirmar isso e está errado: o `CASE` tem `else_=0.0`, então o lado vazio
        soma zero, não NULL, sempre que existe alguma partida. O que ele cobre é
        o desequilíbrio máximo, que precisa aparecer no relatório.
        """
        sessao, ecd_id, _ = ecd_com_lancamentos
        lanc = Lancamento(
            ecd_id=ecd_id,
            num_lcto="so-debito",
            dt_lcto=datetime.date(2024, 7, 1),
            vl_lcto=500.0,
            ind_lcto="N",
        )
        sessao.add(lanc)
        sessao.flush()
        sessao.add(Partida(lancamento_id=lanc.id, cod_cta="1", vl_dc=500.0, ind_dc="D"))
        sessao.commit()

        achados = ValidadorIntegridade(sessao, ecd_id)._validar_partidas_dobradas()

        alvo = [i for i in achados if i.detalhes["num_lcto"] == "so-debito"]
        assert alvo, "lançamento sem contrapartida nenhuma não foi reportado"
        assert alvo[0].detalhes["total_debitos"] == 500.0
        assert (
            alvo[0].detalhes["total_creditos"] == 0.0
        ), "o lado vazio precisa somar 0, não NULL — senão a comparação some"

    def test_mensagem_ao_usuario_nao_mudou(self, ecd_com_lancamentos):
        """A descrição é o que o contador lê; otimizar não muda o texto."""
        sessao, ecd_id, _ = ecd_com_lancamentos

        achados = ValidadorIntegridade(sessao, ecd_id)._validar_partidas_dobradas()

        assert achados
        descricao = achados[0].descricao
        assert descricao.startswith("Lançamento ")
        assert "Débitos R$" in descricao and "≠ Créditos R$" in descricao
        assert achados[0].tipo == "partidas_dobradas"
        assert achados[0].severidade == "erro"


# ═══════════════════════════════════════════════════════════════════════════
# `create_all` em caminho quente
# ═══════════════════════════════════════════════════════════════════════════


MODULOS_QUENTES = [
    "src/ratelimit/__init__.py",
    "src/audit/__init__.py",
    "src/api/__init__.py",
    "src/api/graphql.py",
    "src/api/routes.py",
    "src/async_jobs/__init__.py",
    "src/webhooks/__init__.py",
    "src/monitoring.py",
]


class TestCaminhoQuenteNaoRecriaSchema:
    @pytest.mark.parametrize("modulo", MODULOS_QUENTES)
    def test_nao_chama_criar_engine(self, modulo):
        """`criar_engine` cria engine nova a cada chamada, com pool novo.

        Em caminho por-requisição, o certo é `obter_engine`, que é a versão em
        cache. Verificação por AST: `criar_engine` dentro de comentário ou
        docstring não conta.
        """
        arquivo = REPO / modulo
        chamadas = [
            no.lineno
            for no in ast.walk(ast.parse(arquivo.read_text("utf-8")))
            if isinstance(no, ast.Call)
            and isinstance(no.func, ast.Name)
            and no.func.id == "criar_engine"
        ]
        assert not chamadas, (
            f"{modulo} chama `criar_engine` nas linhas {chamadas} — em caminho "
            "quente isso é engine e pool novos a cada requisição; use `obter_engine`"
        )

    @pytest.mark.parametrize("modulo", MODULOS_QUENTES)
    def test_nao_chama_init_db(self, modulo):
        """`init_db` roda `create_all`, que reflete as 24 tabelas.

        Medido em ~2,9 ms — o comentário de `init_db_once` no próprio
        `db.models` registra isso. Em caminho quente vai `init_db_once`.
        """
        arquivo = REPO / modulo
        chamadas = [
            no.lineno
            for no in ast.walk(ast.parse(arquivo.read_text("utf-8")))
            if isinstance(no, ast.Call)
            and isinstance(no.func, ast.Name)
            and no.func.id == "init_db"
        ]
        assert not chamadas, (
            f"{modulo} chama `init_db` nas linhas {chamadas} — isso reflete as 24 "
            "tabelas a cada chamada; use `init_db_once`"
        )

    def test_schema_e_criado_uma_vez_por_engine(self, tmp_path):
        """O efeito observável: o `create_all` não se repete.

        Contar `CREATE TABLE` é o que distingue `init_db_once` de `init_db`:
        os dois deixam o schema pronto, e só um o refaz.
        """
        from src.db.models import ApiKey
        from src.ratelimit import RateLimiter

        referencia = f"sqlite:///{tmp_path / 'quente.db'}"
        engine = criar_engine(url=referencia)
        init_db(engine)
        with get_session(engine) as sessao:
            sessao.add(ApiKey(nome="k", key_hash="h" * 64, prefixo="spd_x"))
            sessao.commit()
            key_id = sessao.execute(select(ApiKey.id)).scalar()
        engine.dispose()

        limiter = RateLimiter(referencia)
        limiter.verificar(key_id)  # primeira chamada prepara o schema

        _, consultas = _contando_consultas(lambda: [limiter.verificar(key_id) for _ in range(20)])

        criacoes = [c for c in consultas if "CREATE TABLE" in c.upper()]
        assert not criacoes, (
            f"20 requisições emitiram {len(criacoes)} CREATE TABLE — o schema "
            "está sendo refeito a cada requisição"
        )

    def test_consultas_por_requisicao_sao_poucas(self, tmp_path):
        """Uma verificação de cota é uma leitura, não um inventário do schema."""
        from src.db.models import ApiKey
        from src.ratelimit import RateLimiter

        referencia = f"sqlite:///{tmp_path / 'quente2.db'}"
        engine = criar_engine(url=referencia)
        init_db(engine)
        with get_session(engine) as sessao:
            sessao.add(ApiKey(nome="k", key_hash="h" * 64, prefixo="spd_y"))
            sessao.commit()
            key_id = sessao.execute(select(ApiKey.id)).scalar()
        engine.dispose()

        limiter = RateLimiter(referencia)
        limiter.verificar(key_id)

        _, consultas = _contando_consultas(lambda: limiter.verificar(key_id))

        assert (
            len(consultas) <= 2
        ), f"uma verificação de cota emitiu {len(consultas)} consultas: {consultas[:4]}"
