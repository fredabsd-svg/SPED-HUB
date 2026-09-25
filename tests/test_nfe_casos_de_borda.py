"""Três formas legítimas de NF-e que o leitor não entendia.

  * **IPI não tributado** vem em `IPINT`, não em `IPITrib`. O adaptador só
    procurava o CST no `IPITrib`, e o `CST 53` (saída não tributada) virava
    `None` — o C170 saía sem CST_IPI;
  * **ICMS do Simples Nacional** vem em `ICMSSN102`, `ICMSSN500`… com `CSOSN`
    no lugar de `CST`. O gerador escrevia só a origem no CST_ICMS — "0", um
    caractere num campo de três (N 003*), que o validador recusa;
  * **XML com namespace prefixado** (`<ns0:nfeProc xmlns:ns0="…/nfe">`) — o
    que sai de qualquer programa que reserializa o XML com ElementTree — era
    recusado como origem desconhecida, porque o reconhecimento procurava o
    texto `<NFe` e não o namespace.

O CSOSN no CST_ICMS segue o Guia Prático da EFD ICMS/IPI 3.2.2 (C170, campo
10): o CSOSN "é utilizado somente na emissão da NF-e, não é utilizado no
registro das mercadorias nas entradas", e a entrada leva o CST do Convênio
SN/70 "sob o enfoque do declarante" — que só quem escritura sabe. O gerador
escreve o código que menos afirma e avisa, como faz com o IND_PGTO e o
IND_FRT: `60` quando o CSOSN diz que o ICMS já foi cobrado por substituição
(201, 202, 203 e 500 — o exemplo 2 do mesmo campo) e `90` (outros) no resto.
"""

from __future__ import annotations

import datetime
import xml.etree.ElementTree as ET

import pytest

from src.cli import main
from src.db.models import (
    DocumentoFiscal,
    Empresa,
    Escritorio,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos import Desfecho, ImportadorDeDocumentos
from src.documentos.adaptadores import AdaptadorNFe, OrigemNaoReconhecida, adaptador_para
from src.escrituracoes import GeradorEFDContribuicoes, GeradorEFDICMS
from src.escrituracoes.leiaute import EFD_CONTRIBUICOES, EFD_ICMS
from tests.fixtures_nfe import CHAVE_PADRAO, nfe_xml

CNPJ = "98765432000198"
CLIENTE = "12345678000195"
INICIO = datetime.date(2026, 7, 1)
FIM = datetime.date(2026, 7, 31)

IPI_TRIBUTADO = """<IPITrib>
              <CST>50</CST>
              <vBC>1000.00</vBC>
              <pIPI>5.0000</pIPI>
              <vIPI>50.00</vIPI>
            </IPITrib>"""

ICMS_NORMAL = """<ICMS00>
              <orig>0</orig>
              <CST>00</CST>
              <modBC>3</modBC>
              <vBC>1000.00</vBC>
              <pICMS>18.0000</pICMS>
              <vICMS>180.00</vICMS>
            </ICMS00>"""


def _com_ipi_nao_tributado(conteudo: bytes) -> bytes:
    texto = conteudo.decode()
    assert IPI_TRIBUTADO in texto
    return texto.replace(IPI_TRIBUTADO, "<IPINT><CST>53</CST></IPINT>").encode()


def _do_simples(conteudo: bytes, csosn: str = "102") -> bytes:
    texto = conteudo.decode()
    assert ICMS_NORMAL in texto
    grupo = f"<ICMSSN{csosn}><orig>0</orig><CSOSN>{csosn}</CSOSN></ICMSSN{csosn}>"
    return texto.replace(ICMS_NORMAL, grupo).encode()


def _prefixado(conteudo: bytes) -> bytes:
    """O que o ElementTree devolve ao reserializar: `ns0:` em toda tag."""
    raiz = ET.fromstring(conteudo)
    return ET.tostring(raiz, encoding="utf-8", xml_declaration=True)


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'borda.db'}")
    init_db(engine)
    with get_session(engine) as s:
        escritorio = Escritorio(nome="Teste", slug="teste")
        s.add(escritorio)
        s.commit()
        s.add(
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
                escritorio_id=escritorio.id,
            )
        )
        s.commit()
        yield s


def _importar(sessao, conteudo: bytes):
    ocorrencia = ImportadorDeDocumentos(sessao, escritorio_id=1).importar(conteudo)
    sessao.commit()
    return ocorrencia


def _campos(resultado, tipo: str, leiaute) -> dict[str, str]:
    registro = next(r for r in resultado.registros if r.tipo == tipo)
    return dict(zip(leiaute[tipo], registro.campos, strict=True))


def _icms(sessao):
    empresa = sessao.get(Empresa, 1)
    return GeradorEFDICMS(sessao, empresa=empresa, data_inicio=INICIO, data_fim=FIM).gerar()


class TestIPINaoTributado:
    def test_o_cst_do_ipint_e_lido(self):
        documento = AdaptadorNFe().normalizar(_com_ipi_nao_tributado(nfe_xml()))
        assert (
            documento.itens[0].cst_ipi == "53"
        ), "IPI não tributado (IPINT) ficou sem CST: o C170 sai com CST_IPI vazio"

    def test_o_cst_chega_ao_c170(self, sessao):
        _importar(sessao, _com_ipi_nao_tributado(nfe_xml()))
        assert _campos(_icms(sessao), "C170", EFD_ICMS)["CST_IPI"] == "53"

    def test_o_ipi_tributado_continua_como_era(self):
        documento = AdaptadorNFe().normalizar(nfe_xml())
        assert documento.itens[0].cst_ipi == "50"
        assert documento.itens[0].valor_ipi == pytest.approx(50.0)


class TestSimplesNacional:
    def test_entrada_de_fornecedor_do_simples_sai_com_cst_de_tres_digitos(self, sessao):
        _importar(sessao, _do_simples(nfe_xml(numero="77")))
        resultado = _icms(sessao)

        c170 = _campos(resultado, "C170", EFD_ICMS)
        c190 = _campos(resultado, "C190", EFD_ICMS)
        assert c170["CST_ICMS"] == "090", (
            "item do Simples saiu com CST_ICMS de um caractere: o campo é N 003* e o "
            "validador recusa o registro"
        )
        assert c190["CST_ICMS"] == "090"
        aviso = next(a for a in resultado.avisos if "CSOSN" in a)
        assert "77" in aviso and "enfoque" in aviso

    def test_csosn_de_st_cobrada_vira_60(self, sessao):
        """Exemplo 2 do C170, campo 10: mercadoria com ICMS retido por ST → 60."""
        _importar(sessao, _do_simples(nfe_xml(), csosn="500"))
        assert _campos(_icms(sessao), "C170", EFD_ICMS)["CST_ICMS"] == "060"

    def test_a_classificacao_de_quem_escritura_vale(self, sessao):
        """O CST corrigido na camada efetiva é o que sai, e o aviso some."""
        from src.documentos import ORIGEM_USUARIO, aplicar_ajuste

        _importar(sessao, _do_simples(nfe_xml()))
        documento = sessao.query(DocumentoFiscal).one()
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0],
            campo="cst_icms",
            valor_novo="41",
            origem=ORIGEM_USUARIO,
        )
        sessao.commit()
        resultado = _icms(sessao)

        assert _campos(resultado, "C170", EFD_ICMS)["CST_ICMS"] == "041"
        assert not [a for a in resultado.avisos if "CSOSN" in a]

    def test_saida_de_optante_avisa_que_o_guia_pede_o_csosn(self, sessao):
        _importar(sessao, _do_simples(nfe_xml(emitente_cnpj=CNPJ, destinatario_cnpj=CLIENTE)))
        resultado = _icms(sessao)

        assert _campos(resultado, "C190", EFD_ICMS)["CST_ICMS"] == "090"
        assert any("CSOSN" in a and "Tabela B" in a for a in resultado.avisos)

    def test_efd_contribuicoes_tambem(self, sessao):
        _importar(sessao, _do_simples(nfe_xml()))
        empresa = sessao.get(Empresa, 1)
        resultado = GeradorEFDContribuicoes(
            sessao, empresa=empresa, data_inicio=INICIO, data_fim=FIM
        ).gerar()
        assert _campos(resultado, "C170", EFD_CONTRIBUICOES)["CST_ICMS"] == "090"


class TestNamespacePrefixado:
    def test_e_reconhecido_como_nfe(self):
        assert isinstance(
            adaptador_para(_prefixado(nfe_xml())), AdaptadorNFe
        ), "NF-e com namespace prefixado foi recusada como origem desconhecida"

    def test_e_lido_inteiro(self):
        documento = AdaptadorNFe().normalizar(_prefixado(nfe_xml(c_stat="101")))
        assert documento.chave == CHAVE_PADRAO
        assert documento.situacao == "cancelado"
        assert documento.itens[0].valor_icms == pytest.approx(180.0)

    def test_namespace_de_outro_documento_nao_e_nfe(self):
        """O prefixo não basta: o que decide é o URI a que ele aponta."""
        outro = b'<?xml version="1.0"?><ns0:nfeProc xmlns:ns0="http://exemplo.invalid/x"/>'
        with pytest.raises(OrigemNaoReconhecida):
            adaptador_para(outro)

    def test_pela_linha_de_comando(self, sessao, tmp_path, capsys):
        arquivo = tmp_path / "prefixado.xml"
        arquivo.write_bytes(_prefixado(nfe_xml()))
        url = str(sessao.get_bind().url)

        assert main(["fiscal", "importar", str(arquivo), "--escritorio", "1", "--db", url]) == 0
        assert "1 importados" in capsys.readouterr().out

    def test_importador_grava(self, sessao):
        ocorrencia = _importar(sessao, _prefixado(nfe_xml()))
        assert ocorrencia.desfecho is Desfecho.IMPORTADO
