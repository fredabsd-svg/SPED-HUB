"""Testes do parser ECD."""

from pathlib import Path

import pytest

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
        # O manual declara o CNPJ do 0000 como "C 014", e é texto mesmo: com
        # `tipo: N` o parser devolvia `123456000199.0` — sem os dois zeros à
        # esquerda que estão no arquivo, e `None` para CNPJ alfanumérico.
        # Esta asserção cravava o valor já estragado.
        assert r0000[0]["CNPJ"] == "00123456000199"

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


# ── Resumo da EFD-Contribuições e da ECF ───────────────────────────────────

# 0000 da EFD-Contribuições (Guia Prático): REG, COD_VER, TIPO_ESCRIT,
# IND_SIT_ESP, NUM_REC_ANTERIOR, DT_INI, DT_FIN, NOME, CNPJ, UF, COD_MUN,
# SUFRAMA, IND_NAT_PJ, IND_ATIV.
EFD_0000 = "|0000|006|0|||01012024|31012024|EMPRESA X LTDA|12345678000195|SP|3550308||00|1|"
# M100: …, 08 VL_CRED = 165,00 (o 03 IND_CRED_ORI é "0").
EFD_M100 = "|M100|101|0|10000,00|1,6500|||165,00|0,00|0,00|0,00|165,00|0|165,00|0,00|"
# M200: 02 contribuição não cumulativa 1.000; 03 crédito descontado 300;
# 09 cumulativa 50; 13 total a recolher 750.
EFD_M200 = "|M200|1000,00|300,00|0,00|700,00|0,00|0,00|700,00|50,00|0,00|0,00|50,00|750,00|"
EFD_M500 = "|M500|101|0|10000,00|7,6000|||760,00|0,00|0,00|0,00|760,00|0|760,00|0,00|"
EFD_M600 = "|M600|4600,00|760,00|0,00|3840,00|0,00|0,00|3840,00|200,00|0,00|0,00|200,00|4040,00|"
# 0111: 06 REC_BRU_TOTAL = 100.000 (02 a 05 somam 100.000).
EFD_0111 = "|0111|60000,00|20000,00|10000,00|10000,00|100000,00|"
# F100: 05 DT_OPER — a "receita bruta" antiga somava esta data.
EFD_F100 = "|F100|1|PART1|ITEM1|15012024|5000,00|01|5000,00|1,65|82,50|01|5000,00|7,6|380,00|||||"


class TestResumoEFDContribuicoes:
    def _resumo(self, tmp_path, *linhas):
        from src.parsers.efd import EFDParser

        arquivo = tmp_path / "efd.txt"
        arquivo.write_text("\n".join(linhas) + "\n", encoding="utf-8")
        return EFDParser().extrair_resumo(arquivo)

    def test_identificacao_pelos_campos_do_0000(self, tmp_path):
        resumo = self._resumo(tmp_path, EFD_0000)
        assert resumo["empresa"] == {
            "cnpj": "12345678000195",
            "nome": "EMPRESA X LTDA",
        }, "o CNPJ saía do campo DT_FIN (7º) em vez do CNPJ (9º)"
        assert resumo["periodo"] == {"dt_ini": "01012024", "dt_fin": "31012024"}

    def test_pis(self, tmp_path):
        resumo = self._resumo(tmp_path, EFD_0000, EFD_M100, EFD_M200)
        assert resumo["pis"]["debito"] == 1_050.0, (
            f"débito de PIS {resumo['pis']['debito']}: a contribuição do período é o campo "
            "02 (não cumulativa, 1.000) mais o 09 (cumulativa, 50); o 03 (300) é crédito "
            "descontado"
        )
        assert resumo["pis"]["credito"] == 165.0, "o crédito é o 08 VL_CRED, não o IND_CRED_ORI"
        assert resumo["pis"]["a_recolher"] == 750.0
        assert resumo["pis"]["saldo"] == 165.0 - 1_050.0

    def test_cofins(self, tmp_path):
        resumo = self._resumo(tmp_path, EFD_0000, EFD_M500, EFD_M600)
        assert (resumo["cofins"]["debito"], resumo["cofins"]["credito"]) == (4_800.0, 760.0)
        assert resumo["cofins"]["a_recolher"] == 4_040.0

    def test_receita_bruta_vem_do_0111(self, tmp_path):
        resumo = self._resumo(tmp_path, EFD_0000, EFD_0111, EFD_F100)
        assert (
            resumo["receita_bruta"] == 100_000.0
        ), "a receita bruta somava o DT_OPER do F100 (uma data) como se fosse valor"

    def test_sem_0111_a_receita_bruta_nao_e_inventada(self, tmp_path):
        resumo = self._resumo(tmp_path, EFD_0000, EFD_F100)
        assert resumo["receita_bruta"] is None


# 0000 da ECF (manual do leiaute): REG, NOME_ESC, COD_VER, CNPJ, NOME,
# IND_SIT_INI_PER, SIT_ESPECIAL, PAT_REMAN_CIS, DT_SIT_ESP, DT_INI, DT_FIN,
# RETIFICADORA, NUM_REC, TIP_ECF, COD_SCP.
ECF_0000 = "|0000|LECF|0010|12345678000195|EMPRESA X LTDA|0|0|||01012024|31122024|N||0||"


class TestResumoECF:
    def _resumo(self, tmp_path, *linhas):
        from src.parsers.ecf import ECFParser

        arquivo = tmp_path / "ecf.txt"
        arquivo.write_text("\n".join(linhas) + "\n", encoding="utf-8")
        return ECFParser().extrair_resumo(arquivo)

    def test_identificacao_pelos_campos_do_0000(self, tmp_path):
        resumo = self._resumo(tmp_path, ECF_0000)
        assert resumo["empresa"] == {
            "cnpj": "12345678000195",
            "nome": "EMPRESA X LTDA",
        }, "o CNPJ saía do PAT_REMAN_CIS e o nome do DT_SIT_ESP"
        assert resumo["periodo"] == {"dt_ini": "01012024", "dt_fin": "31122024"}

    def test_n670_e_csll_e_nao_vira_irpj(self, tmp_path):
        resumo = self._resumo(
            tmp_path,
            ECF_0000,
            "|N670|1|BASE DE CALCULO DA CSLL|100000,00|",
            "|N670|2|CSLL 9%|9000,00|",
            "|N670|19|CSLL A PAGAR|9000,00|",
            "|N630|1|BASE DE CALCULO DO IRPJ|100000,00|",
        )
        assert resumo["irpj"] is None, (
            f"irpj = {resumo['irpj']}: somava base, alíquota e CSLL a pagar do N670 — que é "
            "CSLL — num número sem significado"
        )
        assert [linha["valor"] for linha in resumo["apuracao_csll"]] == [100000.0, 9000.0, 9000.0]
        assert resumo["apuracao_csll"][2]["descricao"] == "CSLL A PAGAR"
        assert [linha["codigo"] for linha in resumo["apuracao_irpj"]] == ["1"]


class TestResumoPelaTela:
    """O resumo que a tela de upload devolve, pela aplicação montada."""

    @pytest.fixture
    def cliente(self, tmp_path):
        import os

        from fastapi.testclient import TestClient

        from src.auth import init_auth

        caminho = str(tmp_path / "upload.db")
        os.environ["SPED_HUB_DB"] = caminho
        from src.dashboard.app import app

        init_auth(caminho)
        cliente = TestClient(app)
        dados = {"email": "upload@test.local", "nome": "Upload", "senha": "senha123"}
        assert cliente.post("/api/register", data=dados).status_code == 200
        del dados["nome"]
        assert cliente.post("/api/login", data=dados).status_code == 200
        return cliente

    def test_upload_efd(self, cliente):
        conteudo = "\n".join([EFD_0000, EFD_M100, EFD_M200]) + "\n"
        resposta = cliente.post(
            "/api/upload-efd", files={"file": ("efd.txt", conteudo.encode(), "text/plain")}
        )
        assert resposta.status_code == 200, resposta.text
        resumo = resposta.json()["resumo"]
        assert resumo["empresa"]["cnpj"] == "12345678000195"
        assert resumo["pis"]["debito"] == 1_050.0

    def test_upload_ecf(self, cliente):
        conteudo = "\n".join([ECF_0000, "|N670|2|CSLL 9%|9000,00|"]) + "\n"
        resposta = cliente.post(
            "/api/upload-ecf", files={"file": ("ecf.txt", conteudo.encode(), "text/plain")}
        )
        assert resposta.status_code == 200, resposta.text
        resumo = resposta.json()["resumo"]
        assert resumo["empresa"] == {"cnpj": "12345678000195", "nome": "EMPRESA X LTDA"}
        assert resumo["irpj"] is None
