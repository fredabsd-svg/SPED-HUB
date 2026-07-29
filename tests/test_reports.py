"""Testes dos relatórios (Balancete e Razão)."""

import datetime
from pathlib import Path

import pytest

from src.db.models import criar_engine, get_session, init_db
from src.db.repository import Repository
from src.filters.engine import FilterCriteria
from src.parsers.ecd import ECDParser
from src.reports.balancete import Balancete
from src.reports.base import fmt_moeda, saldo_por_natureza, valor_sinalizado
from src.reports.razao import Razao

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

    repo.commit()
    s._ecd_id = ecd.id
    return s


class TestFormatacao:
    def test_fmt_moeda_positivo(self):
        assert fmt_moeda(1234567.89) == "1.234.567,89"

    def test_fmt_moeda_negativo(self):
        assert fmt_moeda(-500.00) == "(500,00)"

    def test_fmt_moeda_zero(self):
        assert fmt_moeda(0) == "0,00"

    def test_valor_sinalizado_debito(self):
        assert valor_sinalizado(100.0, "D") == 100.0

    def test_valor_sinalizado_credito(self):
        assert valor_sinalizado(100.0, "C") == -100.0

    def test_saldo_por_natureza_ativo(self):
        assert saldo_por_natureza(100.0, "01") == 100.0

    def test_saldo_por_natureza_passivo(self):
        assert saldo_por_natureza(-100.0, "02") == 100.0

    def test_saldo_por_natureza_resultado(self):
        assert saldo_por_natureza(-100.0, "04") == 100.0


class TestBalancete:
    def test_gerar_sem_filtros(self, session):
        balancete = Balancete(session, session._ecd_id)
        ctx, linhas = balancete.gerar()
        # 15 contas com I155
        assert len(linhas) == 15
        assert ctx.titulo == "Balancete de Verificação"

    def test_gerar_com_filtro_natureza(self, session):
        balancete = Balancete(session, session._ecd_id)
        ctx, linhas = balancete.gerar(FilterCriteria(cod_nat=["01"]))
        # 5 contas de ativo com I155
        assert len(linhas) == 5

    def test_gerar_nivel_max(self, session):
        balancete = Balancete(session, session._ecd_id)
        ctx, linhas = balancete.gerar(nivel_max=1)
        # Nível 1: 1, 2, 3, 4, 5 — mas só analíticas têm I155
        # Como as sintéticas não têm saldo, retorna 0
        assert len(linhas) == 0

    def test_conferir_sem_divergencias(self, session):
        balancete = Balancete(session, session._ecd_id)
        ctx, linhas = balancete.gerar()
        conf = balancete.conferir(linhas)
        assert conf["status"] == "OK"
        assert conf["contas_com_divergencia"] == 0

    def test_to_dict(self, session):
        balancete = Balancete(session, session._ecd_id)
        ctx, linhas = balancete.gerar()
        dados = balancete.to_dict(linhas)
        assert len(dados) == 15
        assert "cod_cta" in dados[0]
        assert "saldo_final" in dados[0]


class TestRazao:
    def test_gerar_razao_conta(self, session):
        razao = Razao(session, session._ecd_id)
        ctx, linhas = razao.gerar("1.1.2")
        assert len(linhas) > 0
        assert "1.1.2" in ctx.titulo

    def test_razao_saldo_corrente(self, session):
        razao = Razao(session, session._ecd_id)
        ctx, linhas = razao.gerar("1.1.2")
        for i in range(1, len(linhas)):
            assert linhas[i].saldo_corrente != 0.0

    def test_to_dict(self, session):
        razao = Razao(session, session._ecd_id)
        ctx, linhas = razao.gerar("1.1.2")
        dados = razao.to_dict(linhas)
        assert len(dados) > 0
        assert "data" in dados[0]
        assert "saldo_corrente" in dados[0]
