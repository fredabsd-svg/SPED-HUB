"""O total do documento pela regra W16-10 do MOC 7.0.

O `vNF` é o único total do cabeçalho que **não é soma de parcela**. Ele tem
fórmula própria, com doze termos e três exceções, e por muito tempo o sistema
preferiu deixá-lo desatualizado a calculá-lo com metade dos termos — porque um
total errado apresentado como certo é pior que um desatualizado, que ao menos
é o número que o emitente declarou.

Agora a fórmula está inteira, e o que estes testes protegem são as exceções,
que é onde mora o risco de errar com aparência de acerto:

  * **exceção 1** — faturamento direto de veículo novo (`tpOp = 2`) não soma
    ST, FCP-ST nem IPI devolvido;
  * **exceção 2** — em operação de importação (CFOP 3xxx) a regra não vale, e
    o modelo não tem com o que substituí-la: não se recompõe, avisa-se;
  * **exceção 3** — o Fisco **não rejeita** quando o emitente deixou de
    subtrair o ICMS desonerado, de modo que dois totais diferentes são
    igualmente válidos para a mesma nota. Não há como escolher um por fora,
    mas há como descobrir qual o emitente usou: a primeira camada guardou o
    documento como ele veio, e a convenção é lida de lá.

Fonte: MOC 7.0, Anexo I — Leiaute e Regras de Validação da NF-e e da NFC-e,
regra W16-10, obtido do portal DF-e da SVRS.
"""

from __future__ import annotations

import pytest

from src.db.models import (
    DocumentoFiscal,
    Empresa,
    Escritorio,
    ItemDocumentoFiscal,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos import Alteracao, Filtro, Selecao, simular


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'vnf.db'}")
    init_db(engine)
    with get_session(engine) as s:
        yield s


@pytest.fixture
def montar(sessao):
    """Monta um documento com os totais que se quiser, e devolve o recorte.

    Montar na mão, e não pela fixture de XML, é o que deixa cada teste dizer
    exatamente qual termo está exercitando — com o XML, mexer no ICMS
    desonerado obrigaria a mexer no vNF junto, e o teste passaria a afirmar
    duas coisas ao mesmo tempo.
    """

    # Os termos do vNF que TAMBÉM são soma de parcela (desconto, ST, frete,
    # seguro, outras despesas, IPI) precisam existir no item: o recálculo os
    # refaz a partir dos itens antes de fechar o total, e um documento que
    # declarasse 300,00 de ST com itens zerados não é um documento válido —
    # seria a mesma armadilha da nota de teste que não fechava pela regra.
    DOS_ITENS = (
        "valor_desconto",
        "valor_frete",
        "valor_seguro",
        "valor_outras",
        "valor_icms_st",
        "valor_ipi",
    )

    def _montar(*, itens=2, valor_item=1000.0, cfop="6102", tipo_veiculo=None, **totais):
        por_item = {campo: totais[campo] / itens for campo in DOS_ITENS if campo in totais}
        escritorio = Escritorio(nome="T", slug="t")
        sessao.add(escritorio)
        sessao.flush()
        empresa = Empresa(cnpj="98765432000198", nome="C", escritorio_id=escritorio.id)
        sessao.add(empresa)
        sessao.flush()

        documento = DocumentoFiscal(
            escritorio_id=escritorio.id,
            empresa_id=empresa.id,
            chave="3" * 44,
            modelo="55",
            especie="nfe",
            numero="1",
            sentido="entrada",
            situacao="autorizado",
            hash_original="h",
            adaptador="teste",
            valor_produtos=valor_item * itens,
            **totais,
        )
        sessao.add(documento)
        sessao.flush()
        for n in range(1, itens + 1):
            sessao.add(
                ItemDocumentoFiscal(
                    documento_id=documento.id,
                    numero_item=n,
                    codigo=f"P{n}",
                    cfop=cfop,
                    valor_total=valor_item,
                    tipo_operacao_veiculo=tipo_veiculo,
                    **por_item,
                )
            )
        sessao.commit()
        return documento, Selecao(
            escritorio_id=escritorio.id, filtros=[Filtro("codigo", "comeca_com", "P")]
        )

    return _montar


def _vnf(simulacao):
    """A mudança do vNF, ou `None` — filtrando o cabeçalho.

    `valor_total` existe no documento **e** no item; sem o filtro, a busca
    encontra a alteração do item e o teste passa a falar de outra coisa.
    """
    return next(
        (m for m in simulacao.mudancas if m.campo == "valor_total" and m.item_id is None), None
    )


class TestAFormulaInteira:
    def test_todos_os_termos_entram_com_o_sinal_da_regra(self, montar, sessao):
        """Um termo por vez seria doze testes que não pegam sinal trocado."""
        documento, selecao = montar(
            valor_desconto=10.0,
            valor_icms_desonerado=20.0,
            valor_icms_st=30.0,
            valor_fcp_st=40.0,
            valor_frete=50.0,
            valor_seguro=60.0,
            valor_outras=70.0,
            valor_imposto_importacao=80.0,
            valor_ipi=90.0,
            valor_ipi_devolvido=100.0,
            valor_servicos=110.0,
            # 2000 − 10 − 20 + 30 + 40 + 50 + 60 + 70 + 80 + 90 + 100 + 110
            valor_total=2600.0,
        )

        simulacao = simular(sessao, selecao, [Alteracao("valor_total", 500.0)])

        # Os produtos caem de 2.000,00 para 1.000,00; o vNF cai o mesmo tanto.
        assert _vnf(simulacao).valor_novo == pytest.approx(1600.0)

    def test_desconto_e_desonerado_entram_negativos(self, montar, sessao):
        """Trocar o sinal de um deles daria um total maior, não menor."""
        documento, selecao = montar(
            valor_desconto=100.0, valor_icms_desonerado=50.0, valor_total=1850.0
        )

        simulacao = simular(sessao, selecao, [Alteracao("valor_total", 500.0)])

        assert _vnf(simulacao).valor_novo == pytest.approx(850.0)

    def test_sem_mudanca_no_total_nao_propoe_nada(self, montar, sessao):
        """Alterar um campo que não entra na fórmula não mexe no vNF."""
        documento, selecao = montar(valor_total=2000.0)

        simulacao = simular(sessao, selecao, [Alteracao("ncm", "22030000")])

        assert _vnf(simulacao) is None


class TestExcecao1VeiculoNovo:
    """`tpOp = 2` não soma ST, FCP-ST nem IPI devolvido."""

    def test_o_veiculo_novo_ignora_st_fcpst_e_ipi_devolvido(self, montar, sessao):
        documento, selecao = montar(
            tipo_veiculo="2",
            valor_icms_st=300.0,
            valor_fcp_st=40.0,
            valor_ipi_devolvido=100.0,
            valor_ipi=90.0,
            valor_total=2090.0,  # 2000 + 90 — sem ST, FCP-ST nem IPI devolvido
        )

        simulacao = simular(sessao, selecao, [Alteracao("valor_total", 500.0)])

        assert _vnf(simulacao).valor_novo == pytest.approx(1090.0)

    def test_a_mesma_nota_sem_veiculo_soma_os_tres(self, montar, sessao):
        """O contraste é o teste: com os mesmos números, o total é outro."""
        documento, selecao = montar(
            valor_icms_st=300.0,
            valor_fcp_st=40.0,
            valor_ipi_devolvido=100.0,
            valor_ipi=90.0,
            valor_total=2530.0,  # 2000 + 300 + 40 + 100 + 90
        )

        simulacao = simular(sessao, selecao, [Alteracao("valor_total", 500.0)])

        assert _vnf(simulacao).valor_novo == pytest.approx(1530.0)

    def test_outro_tipo_de_operacao_de_veiculo_nao_e_a_excecao(self, montar, sessao):
        """A exceção é do `tpOp = 2`, e não de veículo em geral."""
        documento, selecao = montar(
            tipo_veiculo="1",  # venda para concessionária
            valor_icms_st=300.0,
            valor_total=2300.0,
        )

        simulacao = simular(sessao, selecao, [Alteracao("valor_total", 500.0)])

        assert _vnf(simulacao).valor_novo == pytest.approx(1300.0)


class TestExcecao2Importacao:
    """CFOP 3xxx: a regra não se aplica, e não há com o que substituí-la."""

    def test_importacao_nao_recompoe_e_avisa(self, montar, sessao):
        documento, selecao = montar(cfop="3102", valor_total=2000.0)

        simulacao = simular(sessao, selecao, [Alteracao("valor_total", 500.0)])

        assert _vnf(simulacao) is None
        assert any("importação" in a.problema for a in simulacao.avisos)

    def test_basta_um_item_importado(self, montar, sessao):
        """A operação é de importação; não é o item que é."""
        documento, selecao = montar(cfop="6102", valor_total=2000.0)
        documento.itens[0].cfop = "3102"
        sessao.commit()

        simulacao = simular(sessao, selecao, [Alteracao("valor_total", 500.0)])

        assert _vnf(simulacao) is None

    def test_cfop_corrigido_para_importacao_vale_como_importacao(self, montar, sessao):
        """O CFOP conferido é o **efetivo**, não o que veio no XML.

        Uma classificação que corrigiu o CFOP para 3xxx disse que a operação é
        de importação. Ler o normalizado deixaria a exceção passar
        despercebida justamente onde alguém acabou de dizer que ela vale.
        """
        from src.documentos import ORIGEM_USUARIO, aplicar_ajuste

        documento, selecao = montar(cfop="6102", valor_total=2000.0)
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0],
            campo="cfop",
            valor_novo="3102",
            origem=ORIGEM_USUARIO,
        )
        sessao.commit()

        simulacao = simular(sessao, selecao, [Alteracao("valor_total", 500.0)])

        assert _vnf(simulacao) is None
        assert any("importação" in a.problema for a in simulacao.avisos)

    def test_cfop_parecido_nao_e_importacao(self, montar, sessao):
        """`1302` e `2300` não começam com 3; só o primeiro dígito decide."""
        documento, selecao = montar(cfop="1302", valor_total=2000.0)

        simulacao = simular(sessao, selecao, [Alteracao("valor_total", 500.0)])

        assert _vnf(simulacao) is not None


class TestOsTermosVemDoLugarCERTO:
    """Dois campos do vNF não moram onde o resto do grupo de totais mora."""

    def _normalizar(self, miolo_total: str, miolo_prod: str = "") -> object:
        from src.documentos.adaptadores import AdaptadorNFe

        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
 <NFe><infNFe Id="NFe{"3" * 44}" versao="4.00">
  <ide><mod>55</mod><nNF>1</nNF><dhEmi>2026-07-30T10:00:00-03:00</dhEmi><tpNF>1</tpNF></ide>
  <emit><CNPJ>12345678000195</CNPJ><xNome>E</xNome></emit>
  <dest><CNPJ>98765432000198</CNPJ><xNome>D</xNome></dest>
  <det nItem="1">
   <prod><cProd>P</cProd><xProd>P</xProd><CFOP>5102</CFOP>
     <vProd>1000.00</vProd>{miolo_prod}</prod>
   <imposto/>
  </det>
  <total>{miolo_total}</total>
 </infNFe></NFe>
 <protNFe><infProt><cStat>100</cStat></infProt></protNFe>
</nfeProc>"""
        return AdaptadorNFe().normalizar(xml.encode())

    def test_vserv_vem_de_issqntot_e_nao_de_icmstot(self):
        """`vServ` é o único termo do vNF que mora em `ISSQNtot` (W18).

        Procurá-lo em `ICMSTot`, onde estão os outros onze, não levanta erro:
        devolve zero, e o total sai a menos sem que nada reclame.
        """
        documento = self._normalizar("""<ICMSTot><vProd>1000.00</vProd><vNF>1000.00</vNF></ICMSTot>
       <ISSQNtot><vServ>250.00</vServ></ISSQNtot>""")

        assert documento.valor_servicos == 250.00

    def test_os_outros_termos_vem_de_icmstot(self):
        documento = self._normalizar("""<ICMSTot><vProd>1000.00</vProd><vNF>1000.00</vNF>
         <vICMSDeson>10.00</vICMSDeson><vFCPST>20.00</vFCPST>
         <vII>30.00</vII><vIPIDevol>40.00</vIPIDevol></ICMSTot>""")

        assert documento.valor_icms_desonerado == 10.00
        assert documento.valor_fcp_st == 20.00
        assert documento.valor_imposto_importacao == 30.00
        assert documento.valor_ipi_devolvido == 40.00

    def test_o_tipo_de_operacao_do_veiculo_e_lido(self):
        """Sem ele, faturamento direto seria tratado como nota comum."""
        documento = self._normalizar(
            "<ICMSTot><vProd>1000.00</vProd><vNF>1000.00</vNF></ICMSTot>",
            "<veicProd><tpOp>2</tpOp><chassi>X</chassi></veicProd>",
        )

        assert documento.itens[0].tipo_operacao_veiculo == "2"

    def test_nota_sem_veiculo_nao_inventa_tipo(self):
        documento = self._normalizar("<ICMSTot><vProd>1000.00</vProd><vNF>1000.00</vNF></ICMSTot>")

        assert documento.itens[0].tipo_operacao_veiculo is None


class TestExcecao3ICMSDesonerado:
    """Duas contas válidas para a mesma nota; a do emitente é a que vale."""

    def test_segue_quem_subtraiu(self, montar, sessao):
        documento, selecao = montar(valor_icms_desonerado=200.0, valor_total=1800.0)

        simulacao = simular(sessao, selecao, [Alteracao("valor_total", 500.0)])

        assert _vnf(simulacao).valor_novo == pytest.approx(800.0)

    def test_segue_quem_nao_subtraiu(self, montar, sessao):
        """O Fisco aceita, então o sistema não pode "corrigir" para 800,00.

        Fazê-lo mudaria o total de uma nota que estava certa, e a diferença
        apareceria como erro do sistema no confronto com o arquivo original.
        """
        documento, selecao = montar(valor_icms_desonerado=200.0, valor_total=2000.0)

        simulacao = simular(sessao, selecao, [Alteracao("valor_total", 500.0)])

        assert _vnf(simulacao).valor_novo == pytest.approx(1000.0)

    def test_nota_que_nao_fecha_de_jeito_nenhum_e_avisada(self, montar, sessao):
        """Sem convenção a seguir, seguir uma seria inventar."""
        documento, selecao = montar(valor_icms_desonerado=200.0, valor_total=1234.56)

        simulacao = simular(sessao, selecao, [Alteracao("valor_total", 500.0)])

        assert _vnf(simulacao) is None
        assert any("não fecha" in a.problema for a in simulacao.avisos)

    def test_sem_desoneracao_as_duas_contas_sao_a_mesma(self, montar, sessao):
        """Subtrair zero não muda nada — e a nota comum não cai na exceção."""
        documento, selecao = montar(valor_icms_desonerado=0.0, valor_total=2000.0)

        simulacao = simular(sessao, selecao, [Alteracao("valor_total", 500.0)])

        assert _vnf(simulacao).valor_novo == pytest.approx(1000.0)
        assert not simulacao.avisos
