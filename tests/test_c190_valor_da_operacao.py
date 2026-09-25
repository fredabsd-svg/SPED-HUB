"""O `VL_OPR` do C190 é o valor da operação, não só o das mercadorias.

O defeito: o C190 somava apenas o `vProd` dos itens. Numa venda de 1.000,00
com 30,00 de frete e 50,00 de IPI, o C100 dizia `VL_DOC` 1080,00 e o C190
`VL_OPR` 1000,00 — num arquivo de 2025 (leiaute 019), em que o validador
ainda confere um contra o outro.

O Guia Prático da EFD ICMS/IPI 3.2.2, C190, campo 05: "informar neste campo o
valor das mercadorias somadas aos valores de fretes, seguros e outras
despesas acessórias e os valores de ICMS_ST, FCP_ST e IPI (somente quando o
IPI está destacado na NF), subtraídos o desconto incondicional e o abatimento
não tributado e não comercial. Não devem ser incluídos neste campo os
valores relativos a CBS, IBS e IS".
"""

from __future__ import annotations

import pytest

from src.cli import main
from src.db.models import (
    Empresa,
    Escritorio,
    ItemDocumentoFiscal,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos.adaptadores import AdaptadorNFe
from src.escrituracoes.leiaute import EFD_ICMS
from tests.fixtures_nfe import nfe_xml

CNPJ = "98765432000198"
CLIENTE = "12345678000195"


@pytest.fixture
def banco(tmp_path):
    url = f"sqlite:///{tmp_path / 'fiscal.db'}"
    engine = criar_engine(url=url)
    init_db(engine)
    with get_session(engine) as sessao:
        sessao.add(Escritorio(nome="Teste", slug="teste"))
        sessao.commit()
        sessao.add(
            Empresa(
                cnpj=CNPJ,
                nome="COMERCIO EXEMPLO LTDA",
                uf="TO",
                ie="293456789",
                cod_mun="1721000",
                ind_perfil="A",
                ind_ativ="1",
                ind_ativ_contribuicoes="2",
                cod_inc_trib="1",
                escritorio_id=1,
            )
        )
        sessao.commit()
    engine.dispose()
    return url


def _importar(banco, tmp_path, conteudo: bytes):
    xml = tmp_path / "nfe.xml"
    xml.write_bytes(conteudo)
    assert main(["fiscal", "importar", str(xml), "--escritorio", "1", "--db", banco]) == 0


def _gerar(banco, destino, de, ate) -> list[list[str]]:
    assert (
        main(
            [
                "fiscal",
                "gerar",
                "--empresa",
                "1",
                "--de",
                de,
                "--ate",
                ate,
                "--saida",
                str(destino),
                "--db",
                banco,
            ]
        )
        == 0
    )
    return [
        linha.split("|")[1:-1]
        for linha in destino.read_bytes().decode("latin-1").split("\r\n")
        if linha
    ]


def _campo(linhas, tipo, nome):
    registro = next(campos for campos in linhas if campos[0] == tipo)
    return registro[1:][EFD_ICMS[tipo].index(nome)]


def _com_todas_as_parcelas(conteudo: bytes) -> bytes:
    """Um item com frete, seguro, outras despesas, desconto, ICMS-ST e FCP-ST.

    Cada parcela tem um valor diferente das outras: trocar o sinal de uma, ou
    esquecê-la, muda o resultado.
    """
    texto = conteudo.decode()
    texto = texto.replace(
        "<vProd>1000.00</vProd>\n          <vDesc>0.00</vDesc>",
        "<vProd>1000.00</vProd><vFrete>30.00</vFrete><vSeg>10.00</vSeg>"
        "<vDesc>20.00</vDesc><vOutro>5.00</vOutro>",
    )
    texto = texto.replace(
        """<ICMS00>
              <orig>0</orig>
              <CST>00</CST>
              <modBC>3</modBC>
              <vBC>1000.00</vBC>
              <pICMS>18.0000</pICMS>
              <vICMS>180.00</vICMS>
            </ICMS00>""",
        """<ICMS10>
              <orig>0</orig>
              <CST>10</CST>
              <modBC>3</modBC>
              <vBC>1000.00</vBC>
              <pICMS>18.0000</pICMS>
              <vICMS>180.00</vICMS>
              <modBCST>4</modBCST>
              <vBCST>1200.00</vBCST>
              <pICMSST>18.0000</pICMSST>
              <vICMSST>40.00</vICMSST>
              <vBCFCPST>1200.00</vBCFCPST>
              <pFCPST>2.0000</pFCPST>
              <vFCPST>4.00</vFCPST>
            </ICMS10>""",
    )
    assert "vFCPST" in texto and "<vSeg>10.00" in texto
    return texto.encode()


class TestValorDaOperacao:
    def test_frete_e_ipi_entram_no_vl_opr(self, banco, tmp_path):
        """O caso da auditoria: VL_DOC 1080,00 e VL_OPR 1000,00 em 2025."""
        _importar(
            banco,
            tmp_path,
            nfe_xml(
                chave="35250798765432000198550010000000091000000019",
                emitente_cnpj=CNPJ,
                destinatario_cnpj=CLIENTE,
                numero="9",
                data_emissao="2025-07-10",
                valor_frete=30.0,
                mod_frete="0",
                ind_pag="0",
            ),
        )
        linhas = _gerar(banco, tmp_path / "efd.txt", "2025-07-01", "2025-07-31")

        assert _campo(linhas, "0000", "COD_VER") == "019"
        assert _campo(linhas, "C100", "VL_DOC") == "1080,00"
        assert _campo(linhas, "C190", "VL_OPR") == "1080,00", (
            "o VL_OPR do C190 é só o valor das mercadorias: frete e IPI ficaram de "
            "fora, e no leiaute 019 o validador confere VL_DOC contra a soma dos VL_OPR"
        )

    def test_todas_as_parcelas_com_o_sinal_do_guia(self, banco, tmp_path):
        _importar(banco, tmp_path, _com_todas_as_parcelas(nfe_xml()))
        linhas = _gerar(banco, tmp_path / "efd.txt", "2026-07-01", "2026-07-31")

        # 1000 + 30 frete + 10 seguro + 5 outras + 40 ICMS-ST + 4 FCP-ST
        # + 50 IPI − 20 desconto; nada de CBS, IBS ou IS.
        assert _campo(linhas, "C190", "VL_OPR") == "1119,00", (
            "o VL_OPR não segue a fórmula do Guia (C190, campo 05): alguma parcela "
            "ficou de fora ou entrou com o sinal trocado"
        )


class TestFCPSTDoItem:
    def test_o_adaptador_le_o_fcp_st_do_item(self):
        documento = AdaptadorNFe().normalizar(_com_todas_as_parcelas(nfe_xml()))
        assert documento.itens[0].valor_fcp_st == pytest.approx(4.00)

    def test_o_fcp_st_do_item_chega_ao_banco(self, banco, tmp_path):
        _importar(banco, tmp_path, _com_todas_as_parcelas(nfe_xml()))
        with get_session(criar_engine(url=banco)) as sessao:
            item = sessao.query(ItemDocumentoFiscal).one()
        assert item.valor_fcp_st == pytest.approx(4.00)

    def test_documento_antigo_sem_fcp_st_no_item_e_avisado(self, banco, tmp_path, capsys):
        """O importado antes da coluna tem o FCP-ST só no total."""
        _importar(
            banco,
            tmp_path,
            nfe_xml(numero="321")
            .decode()
            .replace("<vST>0.00</vST>", "<vST>0.00</vST><vFCPST>4.00</vFCPST>")
            .encode(),
        )
        _gerar(banco, tmp_path / "efd.txt", "2026-07-01", "2026-07-31")

        saida = capsys.readouterr().out
        assert (
            "FCP-ST" in saida and "321" in saida
        ), "o VL_OPR saiu sem o FCP-ST do documento sem que ninguém fosse avisado"
