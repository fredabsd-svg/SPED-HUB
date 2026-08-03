"""Testes do parser ECD."""

from pathlib import Path

from src.parsers.ecd import ECDParser, detectar_encoding

FIXTURE = Path(__file__).parent / "fixtures" / "ecd_sample.txt"


class TestDetectarEncoding:
    def test_detecta_utf8(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("|0000|LECD|01012024|...|EMPRESA EXEMPLO LTDA|...|\r\n", encoding="utf-8")
        assert detectar_encoding(f) == "UTF-8"

    def test_detecta_iso8859(self, tmp_path):
        f = tmp_path / "test.txt"
        content = "|0000|LECD|01012024|...|EMPRESA EXEMPLO LTDA|\r\n".encode("iso-8859-1")
        f.write_bytes(content)
        assert detectar_encoding(f) == "UTF-8"  # ASCII puro é UTF-8 válido

    def test_detecta_iso8859_com_acentos(self, tmp_path):
        f = tmp_path / "test.txt"
        content = b"|0000|LECD|01012024|...|EMPRESA \xe7\xe3O LTDA|\r\n"
        f.write_bytes(content)
        assert detectar_encoding(f) == "ISO-8859-1"


class TestParser:
    def test_parse_fixture(self):
        parser = ECDParser()
        registros = parser.parse_todos(FIXTURE)
        assert len(registros) > 0

    def test_registro_0000(self):
        parser = ECDParser()
        registros = parser.parse_todos(FIXTURE)
        r0000 = [r for r in registros if r["_reg"] == "0000"]
        assert len(r0000) == 1
        assert r0000[0]["NOME"] == "EMPRESA EXEMPLO LTDA"
        assert r0000[0]["CNPJ"] == 123456000199.0

    def test_registro_i010(self):
        parser = ECDParser()
        registros = parser.parse_todos(FIXTURE)
        i010 = [r for r in registros if r["_reg"] == "I010"]
        assert len(i010) == 1
        assert i010[0]["COD_VER_LC"] == "009"

    def test_registro_i050_quantidade(self):
        parser = ECDParser()
        registros = parser.parse_todos(FIXTURE)
        i050 = [r for r in registros if r["_reg"] == "I050"]
        assert len(i050) == 23

    def test_registro_i155_quantidade(self):
        parser = ECDParser()
        registros = parser.parse_todos(FIXTURE)
        i155 = [r for r in registros if r["_reg"] == "I155"]
        assert len(i155) == 15

    def test_registro_i200_quantidade(self):
        parser = ECDParser()
        registros = parser.parse_todos(FIXTURE)
        i200 = [r for r in registros if r["_reg"] == "I200"]
        assert len(i200) == 9

    def test_registro_i250_quantidade(self):
        parser = ECDParser()
        registros = parser.parse_todos(FIXTURE)
        i250 = [r for r in registros if r["_reg"] == "I250"]
        assert len(i250) == 18

    def test_registro_i355_quantidade(self):
        parser = ECDParser()
        registros = parser.parse_todos(FIXTURE)
        i355 = [r for r in registros if r["_reg"] == "I355"]
        assert len(i355) == 5

    def test_linha_numero_presente(self):
        parser = ECDParser()
        registros = parser.parse_todos(FIXTURE)
        for r in registros:
            assert "_linha" in r
            assert r["_linha"] > 0

    def test_parse_streaming(self):
        parser = ECDParser()
        count = 0
        for r in parser.parse(FIXTURE):
            count += 1
            assert "_reg" in r
        assert count > 0

    def test_parse_valor_numerico(self):
        parser = ECDParser()
        registros = parser.parse_todos(FIXTURE)
        i155 = [r for r in registros if r["_reg"] == "I155" and r["COD_CTA"] == "1.1.1"]
        assert len(i155) == 1
        assert i155[0]["VL_SLD_INI"] == 50000.0
        assert i155[0]["VL_DEB"] == 150000.0
        assert i155[0]["VL_CRED"] == 120000.0
        assert i155[0]["VL_SLD_FIN"] == 80000.0

    def test_heranca_i051(self):
        """I051 herda COD_CTA do I050 pai."""
        parser = ECDParser()
        registros = parser.parse_todos(FIXTURE)
        i051 = [r for r in registros if r["_reg"] == "I051"]
        assert len(i051) > 0
        # Todos devem ter COD_CTA herdado
        for r in i051:
            assert "COD_CTA" in r
            assert r["COD_CTA"] is not None

    def test_heranca_i250(self):
        """I250 herda NUM_LCTO e DT_LCTO do I200 pai."""
        parser = ECDParser()
        registros = parser.parse_todos(FIXTURE)
        i250 = [r for r in registros if r["_reg"] == "I250"]
        assert len(i250) > 0
        for r in i250:
            assert "NUM_LCTO" in r
            assert "DT_LCTO" in r


class TestLancamentoExtemporaneoDoManual:
    """O campo 6 do I200 é a data dos fatos, não o número do documento.

    [interno: pela porta, o defeito só aparece quando o leiaute **e** o
    importador erram juntos — cada metade sozinha grava `None`, que é o
    valor certo por acaso. `TestLeiaute9DaECDPelaCLI` pega o par; esta
    asserção pega o leiaute sozinho, que é onde o erro nasceu.]
    """

    def test_o_campo_6_do_i200_e_a_data_do_lancamento_extemporaneo(self, tmp_path):
        arquivo = tmp_path / "extemporaneo.txt"
        arquivo.write_text(
            "|0000|LECD|01012023|31122023|EMPRESA|11111111000191|MG||3106200|\n"
            "|I200|2000|02052023|1500,00|X|31122022|\n",
            encoding="utf-8",
        )

        i200 = {r["_reg"]: r for r in ECDParser().parse(arquivo)}["I200"]

        assert i200["DT_LCTO_EXT"] == 31122022
        assert "NUM_ARQ" not in i200


class TestBlocoJDoManual:
    """As linhas do bloco J que a RFB publica prontas, lidas pelo parser.

    [interno: nenhuma porta de entrada chega aqui — o `ECDImportService`
    filtra o bloco J antes de persistir, então o balanço e a DRE publicados
    são parseados e descartados. O leiaute deles, porém, é o mesmo arquivo
    que a importação usa, e era onde estava o erro mais grave: no J100 os
    valores estavam quatro colunas à esquerda do lugar certo. Quando o
    balanço publicado passar a ser importado, este teste vira porta.]

    Cada linha vem do "V - Exemplo de Preenchimento" do Manual do Leiaute 9
    da ECD (Anexo ao ADE Cofis nº 01/2026), com a explicação campo a campo
    do próprio manual transcrita na asserção.
    """

    @staticmethod
    def _ler(tmp_path, linha: str) -> dict:
        arquivo = tmp_path / "blocoj.txt"
        arquivo.write_text(
            "|0000|LECD|01012023|31122023|EMPRESA|11111111000191|MG||3106200|\n"
            "|J001|0|\n"
            "|J005|01012023|31122023|1|BALANCO|\n" + linha + "\n",
            encoding="utf-8",
        )
        registros = {r["_reg"]: r for r in ECDParser().parse(arquivo)}
        return registros[linha.split("|")[1]]

    def test_j100_traz_o_balanco_nas_colunas_8_a_11(self, tmp_path):
        j100 = self._ler(tmp_path, "|J100|1|T|1||A|ATIVO|936844,99|D|100000,00|D|231|")

        assert j100["COD_AGL"] == "1"
        assert j100["IND_COD_AGL"] == "T"  # totalizador
        assert j100["NIVEL_AGL"] == 1
        assert j100["COD_AGL_SUP"] is None
        assert j100["IND_GRP_BAL"] == "A"  # ativo
        assert j100["DESCR_COD_AGL"] == "ATIVO"
        assert j100["VL_CTA_INI"] == 936844.99
        assert j100["IND_DC_CTA_INI"] == "D"
        assert j100["VL_CTA_FIN"] == 100000.00
        assert j100["IND_DC_CTA_FIN"] == "D"

    def test_j150_traz_a_dre_com_o_numero_de_ordem_na_frente(self, tmp_path):
        j150 = self._ler(
            tmp_path,
            "|J150|20|3.3|T|2|3|DESPESAS OPERACIONAIS|10000,00|D|936844,99|D|D|233|",
        )

        assert j150["NU_ORDEM"] == 20  # o campo 2 da DRE é a ordem, não o código
        assert j150["COD_AGL"] == "3.3"
        assert j150["NIVEL_AGL"] == 2
        assert j150["COD_AGL_SUP"] == "3"
        assert j150["DESCR_COD_AGL"] == "DESPESAS OPERACIONAIS"
        assert j150["VL_CTA_FIN"] == 936844.99
        assert j150["IND_GRP_DRE"] == "D"

    def test_j210_comeca_pelo_indicador_de_dlpa_ou_dmpl(self, tmp_path):
        j210 = self._ler(tmp_path, "|J210|0|1.1|LUCROS ACUMULADOS|0,00|C|0,00|C|240|")

        assert j210["IND_TIP"] == "0"  # DLPA
        assert j210["COD_AGL"] == "1.1"
        assert j210["DESCR_COD_AGL"] == "LUCROS ACUMULADOS"
        assert j210["IND_DC_CTA_INI"] == "C"
        assert j210["IND_DC_CTA_FIN"] == "C"
