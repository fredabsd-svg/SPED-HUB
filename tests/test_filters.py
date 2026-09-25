"""Testes do motor de filtros F7."""

import datetime
from pathlib import Path

import pytest

from src.db.models import criar_engine, get_session, init_db
from src.db.repository import Repository
from src.filters.engine import FilterCriteria, FilterEngine
from src.parsers.ecd import ECDParser

FIXTURE = Path(__file__).parent / "fixtures" / "ecd_sample.txt"


def _parse_data(valor):
    """Parse de data DDMMAAAA (formato SPED)."""
    s = str(int(valor)).zfill(8)
    return datetime.date(int(s[4:8]), int(s[2:4]), int(s[0:2]))


@pytest.fixture
def session():
    """Cria banco em memória com dados da fixture."""
    engine = criar_engine(":memory:")
    init_db(engine)
    s = get_session(engine)
    repo = Repository(s)

    parser = ECDParser()
    registros = parser.parse_todos(FIXTURE)

    from collections import defaultdict

    grupos = defaultdict(list)
    for r in registros:
        grupos[r["_reg"]].append(r)

    r0000 = grupos["0000"][0]

    empresa = repo.upsert_empresa(
        {
            "cnpj": str(int(r0000.get("CNPJ", 0))).zfill(14),
            "nome": r0000.get("NOME", ""),
            "uf": r0000.get("UF", ""),
        }
    )

    dt_ini = datetime.date(2024, 1, 1)
    dt_fin = datetime.date(2024, 12, 31)

    ecd = repo.criar_ecd(
        empresa.id,
        {
            "leiaute": "009",
            "dt_ini": dt_ini,
            "dt_fin": dt_fin,
            "hash_arquivo": "abc123",
            "nome_arquivo": "ecd_sample.txt",
        },
    )

    contas = []
    for r in grupos["I050"]:
        contas.append(
            {
                "cod_cta": r.get("COD_CTA", ""),
                "cod_cta_sup": r.get("COD_CTA_SUP", ""),
                "nome_cta": r.get("NOME_CTA", ""),
                "cod_nat": r.get("COD_NAT", "01"),
                "ind_cta": r.get("IND_CTA", "A"),
                "nivel": int(r.get("NIVEL", 0)),
            }
        )
    repo.inserir_plano_contas(ecd.id, contas)

    refs = []
    for r in grupos["I051"]:
        refs.append(
            {
                "cod_cta": r.get("COD_CTA", ""),
                "cod_cta_ref": r.get("COD_CTA_REF", ""),
            }
        )
    repo.inserir_contas_referenciais(ecd.id, refs)

    agls = []
    for r in grupos["I052"]:
        agls.append(
            {
                "cod_cta": r.get("COD_CTA", ""),
                "cod_agl": r.get("COD_AGL", ""),
            }
        )
    repo.inserir_aglutinacoes(ecd.id, agls)

    saldos = []
    for r in grupos["I155"]:
        saldos.append(
            {
                "cod_cta": r.get("COD_CTA", ""),
                "cod_ccus": r.get("COD_CCUS", ""),
                "dt_ini": dt_ini,
                "dt_fin": dt_fin,
                "vl_sld_ini": r.get("VL_SLD_INI", 0.0) or 0.0,
                "ind_dc_ini": r.get("IND_DC_INI", "D"),
                "vl_deb": r.get("VL_DEB", 0.0) or 0.0,
                "vl_cred": r.get("VL_CRED", 0.0) or 0.0,
                "vl_sld_fin": r.get("VL_SLD_FIN", 0.0) or 0.0,
                "ind_dc_fin": r.get("IND_DC_FIN", "D"),
            }
        )
    repo.inserir_saldos_periodicos(ecd.id, saldos)

    lancs = []
    for r in grupos["I200"]:
        dt_lcto = _parse_data(r.get("DT_LCTO", 1012024))
        lancs.append(
            {
                "num_lcto": r.get("NUM_LCTO", ""),
                "dt_lcto": dt_lcto,
                "vl_lcto": r.get("VL_LCTO", 0.0) or 0.0,
                "ind_lcto": r.get("IND_LCTO", "N"),
            }
        )
    repo.inserir_lancamentos(ecd.id, lancs)

    partidas = []
    for r in grupos["I250"]:
        dt_lcto = _parse_data(r.get("DT_LCTO", 1012024))
        partidas.append(
            {
                "num_lcto": r.get("NUM_LCTO", ""),
                "dt_lcto": dt_lcto.isoformat(),
                "cod_cta": r.get("COD_CTA", ""),
                "cod_ccus": r.get("COD_CCUS", ""),
                "vl_dc": r.get("VL_DC", 0.0) or 0.0,
                "ind_dc": r.get("IND_DC", "D"),
                "hist": r.get("HIST", ""),
                "cod_part": r.get("COD_PART", ""),
            }
        )
    repo.inserir_partidas(ecd.id, partidas)

    saldos_res = []
    for r in grupos["I355"]:
        saldos_res.append(
            {
                "cod_cta": r.get("COD_CTA", ""),
                "cod_ccus": r.get("COD_CCUS", ""),
                "dt_res": dt_fin,
                "vl_sld_fin": r.get("VL_SLD_FIN", 0.0) or 0.0,
                "ind_dc_fin": r.get("IND_DC_FIN", "D"),
            }
        )
    repo.inserir_saldos_resultado(ecd.id, saldos_res)

    repo.commit()
    s._ecd_id = ecd.id
    return s


class TestFilterCriteria:
    def test_serializacao(self):
        c = FilterCriteria(
            cod_cta_exato=["1.1.1"],
            cod_nat=["01"],
            dt_ini=datetime.date(2024, 1, 1),
        )
        d = c.to_dict()
        assert "cod_cta_exato" in d
        assert d["cod_cta_exato"] == ["1.1.1"]
        assert d["dt_ini"] == "2024-01-01"

        c2 = FilterCriteria.from_dict(d)
        assert c2.cod_cta_exato == ["1.1.1"]
        assert c2.dt_ini == datetime.date(2024, 1, 1)

    def test_vazio(self):
        c = FilterCriteria()
        assert c.to_dict() == {}


class TestFilterEngine:
    def test_sem_filtros_retorna_todos_saldos(self, session):
        engine = FilterEngine(session, session._ecd_id)
        saldos = engine.aplicar_saldos(FilterCriteria())
        assert len(saldos) == 15

    def test_filtro_conta_exata(self, session):
        engine = FilterEngine(session, session._ecd_id)
        c = FilterCriteria(cod_cta_exato=["1.1.1"])
        saldos = engine.aplicar_saldos(c)
        assert len(saldos) == 1
        assert saldos[0].cod_cta == "1.1.1"

    def test_filtro_natureza(self, session):
        engine = FilterEngine(session, session._ecd_id)
        c = FilterCriteria(cod_nat=["01"])
        saldos = engine.aplicar_saldos(c)
        assert len(saldos) == 5

    def test_filtro_natureza_resultado(self, session):
        engine = FilterEngine(session, session._ecd_id)
        c = FilterCriteria(cod_nat=["04"])
        saldos = engine.aplicar_saldos(c)
        assert len(saldos) == 5

    def test_filtro_classificacao_sintetica(self, session):
        engine = FilterEngine(session, session._ecd_id)
        c = FilterCriteria(ind_cta=["S"])
        saldos = engine.aplicar_saldos(c)
        # Sintéticas não têm I155 na fixture
        assert len(saldos) == 0

    def test_filtro_nivel_ate(self, session):
        engine = FilterEngine(session, session._ecd_id)
        c = FilterCriteria(nivel_ate=1)
        saldos = engine.aplicar_saldos(c)
        # Nível 1 são sintéticas, sem I155
        assert len(saldos) == 0

    def test_filtro_subarvore(self, session):
        engine = FilterEngine(session, session._ecd_id)
        c = FilterCriteria(subarvore_de="1.1")
        saldos = engine.aplicar_saldos(c)
        # 1.1.1, 1.1.2, 1.1.3 (1.1 é sintética sem I155)
        assert len(saldos) == 3

    def test_filtro_ocultar_saldo_zero(self, session):
        engine = FilterEngine(session, session._ecd_id)
        c = FilterCriteria(ocultar_saldo_zero=True)
        saldos = engine.aplicar_saldos(c)
        assert len(saldos) == 15

    def test_filtro_combinado(self, session):
        engine = FilterEngine(session, session._ecd_id)
        c = FilterCriteria(cod_nat=["01"], nivel_ate=2)
        saldos = engine.aplicar_saldos(c)
        # Ativo nível 2: 1.1, 1.2 — ambas sintéticas sem I155
        assert len(saldos) == 0

    def test_descricao_filtros(self, session):
        engine = FilterEngine(session, session._ecd_id)
        c = FilterCriteria(cod_nat=["01"], nivel_ate=2)
        desc = engine.descricao_filtros(c)
        assert "Ativo" in desc
        assert "Até nível" in desc

    def test_filtro_lancamentos(self, session):
        engine = FilterEngine(session, session._ecd_id)
        c = FilterCriteria(cod_cta_exato=["1.1.2"])
        resultados = engine.aplicar_lancamentos(c)
        assert len(resultados) > 0

    def test_filtro_lancamentos_exclui_encerramento(self, session):
        engine = FilterEngine(session, session._ecd_id)
        c = FilterCriteria(ind_lcto=["N"])
        resultados = engine.aplicar_lancamentos(c)
        for _partida, lanc in resultados:
            assert lanc.ind_lcto == "N"

    def test_filtro_saldos_resultado(self, session):
        engine = FilterEngine(session, session._ecd_id)
        c = FilterCriteria()
        saldos = engine.aplicar_saldos_resultado(c)
        assert len(saldos) == 5


# ── Critério de conta que não casa com nada ────────────────────────────────


CRITERIOS_SEM_CONTA = [
    pytest.param(FilterCriteria(cod_cta_exato=["9.9.9"]), id="conta-inexistente"),
    pytest.param(FilterCriteria(nome_cta="inexistente"), id="nome-inexistente"),
    pytest.param(FilterCriteria(cod_cta_prefixo=["8"]), id="prefixo-inexistente"),
    pytest.param(FilterCriteria(cod_cta_prefixo=["1"], cod_nat=["02"]), id="prefixo-e-natureza"),
    pytest.param(FilterCriteria(subarvore_de="9"), id="subarvore-inexistente"),
]


class TestCriterioDeContaSemCorrespondencia:
    """Critério de conta que não casa com nenhuma conta devolve nada — não tudo.

    O `if contas:` pulava a cláusula `IN` quando o conjunto ficava vazio:
    quem filtrava pela conta "9.9.9" recebia a escrituração inteira, com a
    aparência de um relatório daquela conta.
    """

    @pytest.mark.parametrize("criterios", CRITERIOS_SEM_CONTA)
    def test_saldos(self, session, criterios):
        saldos = FilterEngine(session, session._ecd_id).aplicar_saldos(criterios)
        assert saldos == [], (
            f"{len(saldos)} saldos para um critério que não casa com conta nenhuma: "
            "o relatório mostraria a escrituração inteira como se fosse o filtro"
        )

    @pytest.mark.parametrize("criterios", CRITERIOS_SEM_CONTA)
    def test_lancamentos(self, session, criterios):
        partidas = FilterEngine(session, session._ecd_id).aplicar_lancamentos(criterios)
        assert partidas == [], f"{len(partidas)} partidas para um critério sem conta"

    @pytest.mark.parametrize("criterios", CRITERIOS_SEM_CONTA)
    def test_saldos_resultado(self, session, criterios):
        saldos = FilterEngine(session, session._ecd_id).aplicar_saldos_resultado(criterios)
        assert saldos == [], f"{len(saldos)} saldos de resultado para um critério sem conta"

    def test_sem_criterio_de_conta_continua_devolvendo_tudo(self, session):
        engine = FilterEngine(session, session._ecd_id)
        assert len(engine.aplicar_saldos(FilterCriteria())) == 15
        assert len(engine.aplicar_saldos(FilterCriteria(dt_ini=datetime.date(2024, 1, 1)))) == 15


class TestSubarvorePelaHierarquia:
    """A subárvore segue o COD_CTA_SUP, não o prefixo do código.

    O código da conta é livre no leiaute: "111001" pode ser filha de "11" e
    "1101" pode pertencer a outra sintética. Pelo prefixo "11.", a subárvore
    de "11" saía só com a própria "11".
    """

    PLANO = [
        ("1", "", "S", 1, "ATIVO"),
        ("11", "1", "S", 2, "ATIVO CIRCULANTE"),
        ("111001", "11", "A", 3, "CAIXA"),
        ("111002", "11", "A", 3, "BANCOS"),
        ("12", "1", "S", 2, "ATIVO NAO CIRCULANTE"),
        ("1101", "12", "A", 3, "IMOBILIZADO"),
    ]

    @pytest.fixture
    def sessao(self, tmp_path):
        from src.ecd_importer import ECDImportService

        linhas = [
            "|0000|LECD|01012024|31122024|EMPRESA SEM PONTO LTDA|00123456000199|SP||1234567"
            "||0|0|1|0|0|E||1|0||",
            "|I001|0|",
            "|I010|G|009|",
        ]
        for cod, sup, ind, nivel, nome in self.PLANO:
            linhas.append(f"|I050|01012024|01|{ind}|{nivel}|{cod}|{sup}|{nome}|")
        linhas.append("|I150|01012024|31122024|")
        for cod, valor in (("111001", "100,00"), ("111002", "200,00"), ("1101", "400,00")):
            linhas.append(f"|I155|{cod}||{valor}|D|0,00|0,00|{valor}|D|")
        linhas.append("|I990|0|")
        arquivo = tmp_path / "sem_ponto.txt"
        arquivo.write_text("\n".join(linhas) + "\n", encoding="utf-8")

        engine = criar_engine(":memory:")
        init_db(engine)
        s = get_session(engine)
        s._ecd_id = ECDImportService(s).importar(arquivo).ecd_id
        yield s
        s.close()

    def test_codigo_sem_ponto(self, sessao):
        contas = FilterEngine(sessao, sessao._ecd_id)._filtrar_contas(
            FilterCriteria(subarvore_de="11")
        )
        assert contas == {"11", "111001", "111002"}, (
            f"subárvore de 11 = {sorted(contas)}: as analíticas 111001 e 111002 são "
            "filhas de 11 pelo COD_CTA_SUP e ficaram de fora"
        )

    def test_prefixo_igual_de_outra_sintetica_fica_fora(self, sessao):
        contas = FilterEngine(sessao, sessao._ecd_id)._filtrar_contas(
            FilterCriteria(subarvore_de="12")
        )
        assert contas == {
            "12",
            "1101",
        }, f"subárvore de 12 = {sorted(contas)}: 1101 é filha de 12, apesar do código"

    def test_descendentes_em_mais_de_um_nivel(self, sessao):
        contas = FilterEngine(sessao, sessao._ecd_id)._filtrar_contas(
            FilterCriteria(subarvore_de="1")
        )
        assert contas == {c[0] for c in self.PLANO}

    def test_saldos_da_subarvore(self, sessao):
        saldos = FilterEngine(sessao, sessao._ecd_id).aplicar_saldos(
            FilterCriteria(subarvore_de="11")
        )
        assert sorted(s.cod_cta for s in saldos) == ["111001", "111002"]


class TestFiltroSemContaPelaLinhaDeComando:
    """A mesma garantia, entrando pelo executável `sped-hub`."""

    @pytest.fixture
    def banco(self, tmp_path) -> str:
        from src.cli import main

        caminho = str(tmp_path / "filtro.db")
        main(["importar-ecd", str(FIXTURE), "--db", caminho])
        return caminho

    def test_balancete_de_conta_inexistente_sai_vazio(self, banco, capsys):
        from src.cli import main

        capsys.readouterr()
        main(["relatorio", "balancete", "--conta", "9.9.9", "--db", banco])
        saida = capsys.readouterr().out
        assert (
            "BANCOS CONTA MOVIMENTO" not in saida and "CAPITAL SOCIAL" not in saida
        ), "o balancete filtrado pela conta 9.9.9 listou as contas da escrituração inteira"
