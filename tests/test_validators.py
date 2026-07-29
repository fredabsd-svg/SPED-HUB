"""Testes das validações de integridade (F17)."""

import datetime
from pathlib import Path

import pytest

from src.db.models import criar_engine, get_session, init_db
from src.db.repository import Repository
from src.parsers.ecd import ECDParser
from src.validators.integridade import ValidadorIntegridade

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


class TestValidadorIntegridade:
    def test_validar_todas_retorna_lista(self, session):
        validador = ValidadorIntegridade(session, session._ecd_id)
        inconsistencias = validador.validar_todas()
        assert isinstance(inconsistencias, list)

    def test_partidas_dobradas(self, session):
        validador = ValidadorIntegridade(session, session._ecd_id)
        inconsistencias = validador._validar_partidas_dobradas()
        assert len(inconsistencias) == 0

    def test_saldo_si_d_c_sf(self, session):
        validador = ValidadorIntegridade(session, session._ecd_id)
        inconsistencias = validador._validar_saldo_si_d_c_sf()
        assert len(inconsistencias) == 0

    def test_ativo_passivo_pl(self, session):
        validador = ValidadorIntegridade(session, session._ecd_id)
        inconsistencias = validador._validar_ativo_passivo_pl()
        assert len(inconsistencias) == 0

    def test_analiticas_orfas(self, session):
        validador = ValidadorIntegridade(session, session._ecd_id)
        inconsistencias = validador._validar_analiticas_orfas()
        assert len(inconsistencias) == 0

    def test_lancamentos_sinteticas(self, session):
        validador = ValidadorIntegridade(session, session._ecd_id)
        inconsistencias = validador._validar_lancamentos_sinteticas()
        assert len(inconsistencias) == 0

    def test_relatorio(self, session):
        validador = ValidadorIntegridade(session, session._ecd_id)
        inconsistencias = validador.validar_todas()
        relatorio = validador.relatorio(inconsistencias)
        assert "total_inconsistencias" in relatorio
        assert "status" in relatorio
        assert relatorio["status"] == "OK"

    def test_dre_vs_i355(self, session):
        validador = ValidadorIntegridade(session, session._ecd_id)
        inconsistencias = validador._validar_dre_vs_i355()
        assert isinstance(inconsistencias, list)


class TestHierarquiaCiclica:
    """(h) — a validação que faltava quando o defeito do PR #7 foi encontrado.

    Uma ECD com ciclo na hierarquia travava o dashboard para todos os
    usuários. O travamento foi corrigido lá; esta validação é o que avisa
    quem recebe o arquivo de que a escrituração veio inválida — antes dela,
    o ciclo entrava no banco em silêncio.
    """

    @pytest.fixture
    def sessao_vazia(self):
        engine = criar_engine(":memory:")
        init_db(engine)
        s = get_session(engine)
        repo = Repository(s)
        empresa = repo.upsert_empresa({"cnpj": "00999999000199", "nome": "CICLO SA"})
        ecd = repo.criar_ecd(
            empresa.id,
            {
                "leiaute": "009",
                "dt_ini": datetime.date(2024, 1, 1),
                "dt_fin": datetime.date(2024, 12, 31),
            },
        )
        s._ecd_id = ecd.id
        s._repo = repo
        return s

    def _plano(self, sessao, arestas):
        """arestas: lista de (cod_cta, cod_cta_sup, nivel)."""
        sessao._repo.inserir_plano_contas(
            sessao._ecd_id,
            [
                {
                    "cod_cta": cod,
                    "cod_cta_sup": sup,
                    "nome_cta": f"CONTA {cod}",
                    "cod_nat": "01",
                    "ind_cta": "A",
                    "nivel": nivel,
                }
                for cod, sup, nivel in arestas
            ],
        )
        sessao._repo.commit()

    def test_conta_que_e_a_propria_sintetica(self, sessao_vazia):
        """O caso real: foi esta forma que derrubou o dashboard."""
        self._plano(sessao_vazia, [("1", "1", 3)])
        achados = ValidadorIntegridade(
            sessao_vazia, sessao_vazia._ecd_id
        )._validar_hierarquia_ciclica()
        assert len(achados) == 1
        assert achados[0].tipo == "hierarquia_ciclica"
        assert achados[0].severidade == "erro", (
            "ciclo precisa ser erro: diferente das divergências de centavos, "
            "hierarquia cíclica não tem leitura correta possível"
        )
        assert achados[0].detalhes["ciclo"] == ["1"]

    def test_ciclo_mutuo_reportado_uma_unica_vez(self, sessao_vazia):
        """A→B→A é UM ciclo, não dois — reportar dobrado vira ruído."""
        self._plano(sessao_vazia, [("1", "2", 3), ("2", "1", 3)])
        achados = ValidadorIntegridade(
            sessao_vazia, sessao_vazia._ecd_id
        )._validar_hierarquia_ciclica()
        assert len(achados) == 1
        assert sorted(achados[0].detalhes["ciclo"]) == ["1", "2"]

    def test_conta_fora_do_ciclo_nao_e_acusada(self, sessao_vazia):
        """D→A→B→A: o defeito está em A e B; D só aponta para dentro dele."""
        self._plano(sessao_vazia, [("D", "A", 4), ("A", "B", 3), ("B", "A", 3)])
        achados = ValidadorIntegridade(
            sessao_vazia, sessao_vazia._ecd_id
        )._validar_hierarquia_ciclica()
        assert len(achados) == 1
        assert "D" not in achados[0].detalhes["ciclo"]

    def test_hierarquia_valida_nao_gera_falso_positivo(self, sessao_vazia):
        """Árvore correta de 3 níveis: nada a reportar."""
        self._plano(
            sessao_vazia,
            [("1", None, 1), ("1.1", "1", 2), ("1.1.01", "1.1", 3), ("1.1.02", "1.1", 3)],
        )
        achados = ValidadorIntegridade(
            sessao_vazia, sessao_vazia._ecd_id
        )._validar_hierarquia_ciclica()
        assert achados == []

    def test_orfa_nao_e_ciclo(self, sessao_vazia):
        """Sintética inexistente é a validação (f), não esta."""
        self._plano(sessao_vazia, [("1", "999", 3)])
        achados = ValidadorIntegridade(
            sessao_vazia, sessao_vazia._ecd_id
        )._validar_hierarquia_ciclica()
        assert achados == []

    def test_entra_no_validar_todas(self, sessao_vazia):
        """Validação que existe mas ninguém chama é promessa vazia (§1.1)."""
        self._plano(sessao_vazia, [("1", "1", 3)])
        todas = ValidadorIntegridade(sessao_vazia, sessao_vazia._ecd_id).validar_todas()
        tipos = {i.tipo for i in todas}
        assert "hierarquia_ciclica" in tipos

    def test_dois_ciclos_independentes(self, sessao_vazia):
        self._plano(sessao_vazia, [("1", "1", 3), ("5", "6", 3), ("6", "5", 3)])
        achados = ValidadorIntegridade(
            sessao_vazia, sessao_vazia._ecd_id
        )._validar_hierarquia_ciclica()
        assert len(achados) == 2
