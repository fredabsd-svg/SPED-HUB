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
