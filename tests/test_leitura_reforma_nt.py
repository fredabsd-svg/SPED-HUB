"""Os grupos da reforma são lidos do lugar em que a NT 2025.002 v1.50 os põe.

Estes testes existem porque o leitor passou meses lendo zero sem que nada
falhasse. Ele procurava redução, diferimento, devolução, crédito presumido e
monofásico como filhos diretos de `gIBSCBS`; a NT nunca os pôs ali. Procurar
uma tag no nó errado não levanta exceção — devolve `None`, que vira `0.0`. E
como a fixture era montada a partir do leitor, e não a partir da NT, ela
reproduzia o mesmo engano, e os testes concordavam com o erro.

Por isso o XML daqui é montado **a partir do documento oficial**, com os
grupos aninhados como a NT manda, e cada valor é diferente de todos os outros:

  * `gRed`, `gDif` e `gDevTrib` existem **uma vez por destinação**, dentro de
    `gIBSUF` (UB21/UB24/UB26), `gIBSMun` (UB40/UB43/UB45) e `gCBS`
    (UB59/UB62/UB64) — com as mesmas tags nos três;
  * `gCredPresOper` (UB120) é **irmão** de `gIBSCBS`, e separa IBS (UB123) de
    CBS (UB127);
  * `gIBSCBSMono` (UB84) tem quatro variantes desde a v1.50 — IBS e CBS, ad
    rem e ad valorem — e fecha o item em `vTotIBSMonoItem`/`vTotCBSMonoItem`;
  * `cMunFGIBS` (B12a) é campo do `ide`, do documento, não do item.

Valores repetidos não serviriam: um leitor que confundisse a parcela estadual
com a municipal passaria despercebido se as duas fossem 0,35.
"""

from __future__ import annotations

import pytest

from src.documentos.adaptadores import AdaptadorNFe
from tests.fixtures_nfe import nfe_xml

CABECALHO = """<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe><infNFe Id="NFe35260712345678000195550010000000011000000017" versao="4.00">
    <ide><cUF>35</cUF><natOp>VENDA</natOp><mod>55</mod><serie>1</serie><nNF>1</nNF>
      <dhEmi>2026-08-03T10:00:00-03:00</dhEmi><tpNF>1</tpNF><idDest>1</idDest>
      <cMunFG>3550308</cMunFG><tpAmb>1</tpAmb></ide>
    <emit><CNPJ>12345678000195</CNPJ><xNome>EMIT</xNome><IE>1</IE>
      <enderEmit><UF>SP</UF></enderEmit></emit>
    <dest><CNPJ>98765432000198</CNPJ><xNome>DEST</xNome>
      <enderDest><UF>TO</UF></enderDest></dest>
    <det nItem="1">
      <prod><cProd>P1</cProd><xProd>PROD</xProd><NCM>22030000</NCM><CFOP>5102</CFOP>
        <uCom>UN</uCom><qCom>1.0000</qCom><vUnCom>1000.00</vUnCom><vProd>1000.00</vProd></prod>
      <imposto><IBSCBS><CST>200</CST><cClassTrib>200001</cClassTrib>"""

RODAPE = """</IBSCBS></imposto>
    </det>
    <total><ICMSTot><vProd>1000.00</vProd><vNF>1000.00</vNF></ICMSTot></total>
  </infNFe></NFe>
  <protNFe><infProt><cStat>100</cStat><nProt>1</nProt></infProt></protNFe>
</nfeProc>"""


def _item(miolo: str):
    """O primeiro item de uma NF-e cujo `IBSCBS` é o `miolo` dado."""
    return AdaptadorNFe().normalizar((CABECALHO + miolo + RODAPE).encode()).itens[0]


# `gMonoPadrao`, `gMonoReten` e `gMonoRet` repetem os mesmos nomes de tag nas
# quatro variantes; o que muda é o tributo no nome do valor.
MONO_AD_REM = """
  <gIBSCBSMono>
    <gIBSMonoAdRem>
      <gMonoPadrao><qBCMono>100.0000</qBCMono><adRemIBS>0.1000</adRemIBS>
        <vIBSMono>10.00</vIBSMono></gMonoPadrao>
      <gMonoReten><qBCMonoReten>20.0000</qBCMonoReten><adRemIBSReten>0.1000</adRemIBSReten>
        <vIBSMonoReten>2.00</vIBSMonoReten></gMonoReten>
      <gMonoRet><vIBSMonoRet>3.00</vIBSMonoRet></gMonoRet>
    </gIBSMonoAdRem>
    <gCBSMonoAdRem>
      <gMonoPadrao><qBCMono>100.0000</qBCMono><adRemCBS>0.9000</adRemCBS>
        <vCBSMono>90.00</vCBSMono></gMonoPadrao>
      <gMonoReten><qBCMonoReten>20.0000</qBCMonoReten><adRemCBSReten>0.9000</adRemCBSReten>
        <vCBSMonoReten>18.00</vCBSMonoReten></gMonoReten>
      <gMonoRet><vCBSMonoRet>27.00</vCBSMonoRet></gMonoRet>
    </gCBSMonoAdRem>
    <vTotIBSMonoItem>12.00</vTotIBSMonoItem>
    <vTotCBSMonoItem>108.00</vTotCBSMonoItem>
  </gIBSCBSMono>"""

MONO_AD_VALOREM = """
  <gIBSCBSMono>
    <gIBSMonoAdValorem>
      <gMonoPadrao><vBCMono>500.00</vBCMono><pAliqMonoUF>1.0000</pAliqMonoUF>
        <vIBSMonoUF>5.00</vIBSMonoUF><pAliqMonoMun>0.4000</pAliqMonoMun>
        <vIBSMonoMun>2.00</vIBSMonoMun><vIBSMono>7.00</vIBSMono></gMonoPadrao>
      <gMonoRet><vIBSMonoRet>1.50</vIBSMonoRet></gMonoRet>
    </gIBSMonoAdValorem>
    <gCBSMonoAdValorem>
      <gMonoPadrao><vBCMono>500.00</vBCMono><pAliqMonoCBS>9.0000</pAliqMonoCBS>
        <vCBSMono>45.00</vCBSMono></gMonoPadrao>
      <gMonoReten><vBCMonoReten>100.00</vBCMonoReten><pAliqMonoReten>9.0000</pAliqMonoReten>
        <vCBSMonoReten>9.00</vCBSMonoReten></gMonoReten>
    </gCBSMonoAdValorem>
    <vTotIBSMonoItem>7.00</vTotIBSMonoItem>
    <vTotCBSMonoItem>45.00</vTotCBSMonoItem>
  </gIBSCBSMono>"""

# O IBS ad rem e a CBS ad valorem no mesmo item.  A escolha da variante é por
# tributo e por ano, então um não obriga o outro — e é aqui que o item tem as
# DUAS bases: quantidade pelo IBS, valor pela CBS.
MONO_MISTO = """
  <gIBSCBSMono>
    <gIBSMonoAdRem>
      <gMonoPadrao><qBCMono>80.0000</qBCMono><adRemIBS>0.1000</adRemIBS>
        <vIBSMono>8.00</vIBSMono></gMonoPadrao>
    </gIBSMonoAdRem>
    <gCBSMonoAdValorem>
      <gMonoPadrao><vBCMono>400.00</vBCMono><pAliqMonoCBS>9.0000</pAliqMonoCBS>
        <vCBSMono>36.00</vCBSMono></gMonoPadrao>
    </gCBSMonoAdValorem>
    <vTotIBSMonoItem>8.00</vTotIBSMonoItem>
    <vTotCBSMonoItem>36.00</vTotCBSMonoItem>
  </gIBSCBSMono>"""


class TestBeneficiosSaoPorDestinacao:
    """Um `gRed`, um `gDif` e um `gDevTrib` dentro de cada destinação."""

    @pytest.fixture
    def item(self):
        return AdaptadorNFe().normalizar(nfe_xml(beneficios=True)).itens[0]

    def test_o_diferimento_de_cada_destinacao_e_o_seu(self, item):
        """Somar os três daria 5,00 — e esconderia de qual ente é o benefício."""
        assert item.valor_diferido_ibs_uf == 0.35
        assert item.valor_diferido_ibs_mun == 0.15
        assert item.valor_diferido_cbs == 4.50

    def test_a_devolucao_de_cada_destinacao_e_a_sua(self, item):
        assert item.valor_devolucao_ibs_uf == 0.07
        assert item.valor_devolucao_ibs_mun == 0.03
        assert item.valor_devolucao_cbs == 0.90

    def test_a_reducao_de_cada_destinacao_e_a_sua(self, item):
        """Percentual não soma: três reduções de 10, 20 e 30 não são 60."""
        assert item.percentual_reducao_ibs_uf == 10.0
        assert item.percentual_reducao_ibs_mun == 20.0
        assert item.percentual_reducao_cbs == 30.0

    def test_a_aliquota_efetiva_de_cada_destinacao_e_a_sua(self, item):
        assert item.aliquota_efetiva_ibs_uf == 0.0630
        assert item.aliquota_efetiva_ibs_mun == 0.0240
        assert item.aliquota_efetiva_cbs == 0.6300

    def test_nota_sem_beneficio_nao_inventa_nenhum(self):
        item = AdaptadorNFe().normalizar(nfe_xml()).itens[0]

        assert item.valor_diferido_ibs_uf == 0.0
        assert item.percentual_reducao_cbs == 0.0
        assert item.valor_devolucao_ibs_mun == 0.0

    def test_beneficio_em_uma_destinacao_so_nao_vaza_para_as_outras(self):
        """O caso mais comum: diferimento só na parcela estadual."""
        item = _item("""
          <gIBSCBS>
            <vBC>1000.00</vBC>
            <gIBSUF><pIBSUF>0.1000</pIBSUF>
              <gDif><pDif>50.0000</pDif><vDif>0.50</vDif></gDif>
              <vIBSUF>1.00</vIBSUF></gIBSUF>
            <gIBSMun><pIBSMun>0.0300</pIBSMun><vIBSMun>0.30</vIBSMun></gIBSMun>
            <gCBS><pCBS>0.9000</pCBS><vCBS>9.00</vCBS></gCBS>
          </gIBSCBS>""")

        assert item.valor_diferido_ibs_uf == 0.50
        assert item.valor_diferido_ibs_mun == 0.0
        assert item.valor_diferido_cbs == 0.0

    def test_os_valores_obrigatorios_continuam_lidos(self, item):
        """A regressão a evitar: mexer no aninhado e quebrar o que funcionava."""
        assert item.base_ibscbs == 1000.00
        assert (item.valor_ibs_uf, item.valor_ibs_mun, item.valor_cbs) == (0.70, 0.30, 9.00)


class TestCreditoPresumidoEIrmaoDoGIBSCBS:
    """`gCredPresOper` é filho de `IBSCBS`, não de `gIBSCBS`."""

    @pytest.fixture
    def item(self):
        return AdaptadorNFe().normalizar(nfe_xml(beneficios=True)).itens[0]

    def test_o_codigo_e_um_so_para_a_operacao(self, item):
        assert item.codigo_credito_presumido == "02"

    def test_ibs_e_cbs_tem_valores_proprios(self, item):
        assert item.valor_credito_presumido_ibs == 0.10
        assert item.valor_credito_presumido_cbs == 1.80

    def test_o_suspenso_tambem_e_separado(self, item):
        """Condição suspensiva não é crédito ainda; misturar os dois somaria
        o que se tem com o que talvez se tenha."""
        assert item.valor_credito_presumido_ibs_susp == 0.04
        assert item.valor_credito_presumido_cbs_susp == 0.60

    def test_os_percentuais_sao_de_cada_tributo(self, item):
        assert item.percentual_credito_presumido_ibs == 10.0
        assert item.percentual_credito_presumido_cbs == 20.0

    def test_grupo_so_de_ibs_nao_preenche_a_cbs(self):
        item = _item("""
          <gCredPresOper>
            <cCredPres>03</cCredPres>
            <gIBSCredPres><pCredPres>5.0000</pCredPres><vCredPres>0.05</vCredPres>
              <vCredPresCondSus>0.00</vCredPresCondSus></gIBSCredPres>
          </gCredPresOper>""")

        assert item.valor_credito_presumido_ibs == 0.05
        assert item.valor_credito_presumido_cbs == 0.0

    def test_nota_sem_credito_presumido_nao_tem_codigo(self):
        item = AdaptadorNFe().normalizar(nfe_xml()).itens[0]

        assert item.codigo_credito_presumido is None
        assert item.valor_credito_presumido_ibs == 0.0


class TestMonofasicoDaVersao150:
    """As quatro variantes, e o total que a NT fecha para o item."""

    def test_o_total_do_item_e_o_que_a_nt_soma(self):
        """Não recalculamos: `vTotIBSMonoItem` já é padrão + retenção."""
        item = _item(MONO_AD_REM)

        assert item.valor_ibs_mono == 12.00
        assert item.valor_cbs_mono == 108.00

    def test_ad_rem_traz_a_base_em_quantidade(self):
        item = _item(MONO_AD_REM)

        assert item.quantidade_bc_mono == 100.0
        assert item.valor_bc_mono == 0.0

    def test_ad_valorem_traz_a_base_em_valor(self):
        """São grandezas diferentes: litros não cabem na mesma coluna que reais."""
        item = _item(MONO_AD_VALOREM)

        assert item.valor_bc_mono == 500.00
        assert item.quantidade_bc_mono == 0.0

    def test_reten_e_retido_sao_coisas_diferentes(self):
        """`gMonoReten` soma ao que se recolhe; `gMonoRet` já foi cobrado.

        A NT os nomeia quase igual, e trocar um pelo outro erra o sinal do
        monofásico inteiro — por isso são duas colunas, e não uma.
        """
        item = _item(MONO_AD_REM)

        assert item.valor_ibs_mono_reten == 2.00
        assert item.valor_ibs_mono_retido == 3.00
        assert item.valor_cbs_mono_reten == 18.00
        assert item.valor_cbs_mono_retido == 27.00

    def test_ad_valorem_sem_retencao_nao_inventa_valor(self):
        item = _item(MONO_AD_VALOREM)

        assert item.valor_ibs_mono_reten == 0.0
        assert item.valor_ibs_mono_retido == 1.50
        assert item.valor_cbs_mono_retido == 0.0

    def test_a_retencao_do_ad_valorem_tambem_e_lida(self):
        """A variante ad valorem tem os mesmos três subgrupos da ad rem.

        Ler só a ad rem passaria por todo 2026 sem quebrar, porque é a variante
        do ano — e falharia calado no dia em que a lei mudar para ad valorem.
        """
        item = _item(MONO_AD_VALOREM)

        assert item.valor_cbs_mono_reten == 9.00

    def test_ibs_ad_rem_com_cbs_ad_valorem_guarda_as_duas_bases(self):
        """A variante é escolhida por tributo: um item pode ter as duas.

        Sem isso, visitar a CBS depois do IBS zeraria a quantidade que o IBS
        tinha acabado de gravar — e a base do monofásico sumiria.
        """
        item = _item(MONO_MISTO)

        assert item.quantidade_bc_mono == 80.0
        assert item.valor_bc_mono == 400.00
        assert item.valor_ibs_mono == 8.00
        assert item.valor_cbs_mono == 36.00

    def test_nota_sem_monofasico_fica_zerada(self):
        item = AdaptadorNFe().normalizar(nfe_xml()).itens[0]

        assert item.valor_ibs_mono == 0.0
        assert item.valor_cbs_mono == 0.0
        assert item.quantidade_bc_mono == 0.0

    def test_o_total_nao_e_confundido_com_o_valor_da_variante(self):
        """`vIBSMono` (10,00) vive dentro de `gMonoPadrao` e não é o total.

        Ler o de dentro daria 10,00 e perderia a retenção — que é justamente o
        que a NT manda somar ao imposto a recolher.
        """
        item = _item(MONO_AD_REM)

        assert item.valor_ibs_mono != 10.00
        assert item.valor_ibs_mono == 12.00


class TestMunicipioDoFatoGeradorDoIBS:
    """`cMunFGIBS` é do documento, não do item."""

    def test_vem_do_ide(self):
        documento = AdaptadorNFe().normalizar(nfe_xml())

        assert documento.municipio_fg_ibs == "3106200"

    def test_nao_e_o_municipio_do_icms(self):
        """Município da operação e município de consumo podem divergir, e é
        justamente aí que a parcela municipal do IBS muda de destino."""
        documento = AdaptadorNFe().normalizar(nfe_xml())

        assert documento.municipio_codigo == "3550308"
        assert documento.municipio_fg_ibs != documento.municipio_codigo

    def test_ausente_e_none_e_nao_o_do_icms(self):
        """O campo é opcional: só vem quando `indPres=5` sem endereço nem
        entrega. Cair no `cMunFG` faria toda nota parecer ter consumo local."""
        documento = AdaptadorNFe().normalizar((CABECALHO + RODAPE).encode())

        assert documento.municipio_fg_ibs is None
        assert documento.municipio_codigo == "3550308"
