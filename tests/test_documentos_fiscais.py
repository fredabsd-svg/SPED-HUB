"""Central de Documentos Fiscais: adaptador de NF-e e importação em lote.

O que estes testes protegem, além do óbvio:

  * **o XML original chega intacto ao banco** — é a prova do que o emitente
    declarou, e a camada 1 das três que a suíte separa;
  * **nada duplica em silêncio** — reimportar a mesma pasta é rotina;
  * **os tributos da reforma são lidos**, inclusive a separação do IBS em
    parcela estadual e municipal, que a apuração precisa;
  * **XML hostil não derruba o servidor** — documento fiscal vem de terceiro.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.db.models import (
    DocumentoFiscal,
    Empresa,
    Escritorio,
    ItemDocumentoFiscal,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos import (
    AdaptadorNFe,
    Desfecho,
    ImportadorDeDocumentos,
    OrigemNaoReconhecida,
    PoliticaDeDuplicidade,
    XMLPerigoso,
    adaptador_para,
    carregar_xml,
)
from tests.fixtures_nfe import CHAVE_PADRAO, nfe_xml


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'documentos.db'}")
    init_db(engine)
    with get_session(engine) as s:
        yield s


@pytest.fixture
def escritorio(sessao):
    e = Escritorio(nome="Escritório Teste", slug="teste")
    sessao.add(e)
    sessao.commit()
    return e


class TestAdaptadorNFe:
    def test_reconhece_nfe(self):
        assert adaptador_para(nfe_xml()).nome == "nfe"

    def test_nao_reconhece_qualquer_xml(self):
        with pytest.raises(OrigemNaoReconhecida):
            adaptador_para(b"<?xml version='1.0'?><pedido><item/></pedido>")

    def test_identidade_do_documento(self):
        d = AdaptadorNFe().normalizar(nfe_xml())
        assert d.chave == CHAVE_PADRAO
        assert d.modelo == "55"
        assert d.especie == "nfe"
        assert d.numero == "1"
        assert d.serie == "1"

    def test_nfce_e_reconhecida_pela_especie(self):
        d = AdaptadorNFe().normalizar(nfe_xml(modelo="65"))
        assert d.especie == "nfce"

    def test_partes_e_datas(self):
        d = AdaptadorNFe().normalizar(nfe_xml())
        assert d.emitente_cnpj == "12345678000195"
        assert d.emitente_uf == "SP"
        assert d.destinatario_cnpj == "98765432000198"
        assert d.destinatario_uf == "TO"
        assert d.data_emissao.isoformat() == "2026-07-30"
        assert d.data_entrada_saida.isoformat() == "2026-07-31"

    def test_totais_do_documento(self):
        d = AdaptadorNFe().normalizar(nfe_xml())
        # vProd 1.000,00 + IPI 50,00, como manda a regra W16-10.
        assert d.valor_total == 1050.00
        assert d.valor_icms == 180.00
        assert d.valor_ipi == 50.00
        assert d.valor_pis == 16.50
        assert d.valor_cofins == 76.00

    def test_item_com_tributos_do_regime_atual(self):
        item = AdaptadorNFe().normalizar(nfe_xml()).itens[0]
        assert item.codigo == "PROD-001"
        assert item.ncm == "22030000"
        assert item.cfop == "6102"
        # O ICMS vem embrulhado em ICMS00/ICMS60/ICMSSN102… conforme o caso.
        assert item.cst_icms == "00"
        assert item.aliquota_icms == 18.0
        assert item.valor_icms == 180.00
        assert item.cst_pis == "01"
        assert item.valor_pis == 16.50
        assert item.cst_cofins == "01"
        assert item.valor_cofins == 76.00
        assert item.cst_ipi == "50"
        assert item.valor_ipi == 50.00

    def test_varios_itens(self):
        d = AdaptadorNFe().normalizar(nfe_xml(itens=3))
        assert [i.numero_item for i in d.itens] == [1, 2, 3]

    def test_documento_cancelado_pelo_protocolo(self):
        """cStat 101 é cancelamento — escriturar como autorizado seria erro."""
        assert AdaptadorNFe().normalizar(nfe_xml(c_stat="101")).situacao == "cancelado"

    def test_documento_denegado(self):
        assert AdaptadorNFe().normalizar(nfe_xml(c_stat="110")).situacao == "denegado"

    def test_sem_chave_e_recusado(self):
        """Sem chave não há identidade, e sem identidade não há deduplicação."""
        quebrado = nfe_xml().replace(b'Id="NFe' + CHAVE_PADRAO.encode() + b'"', b'Id=""')
        with pytest.raises(ValueError, match="chave"):
            AdaptadorNFe().normalizar(quebrado)

    def test_xml_original_preservado_byte_a_byte(self):
        """Camada 1 das três: é a prova do que o emitente declarou."""
        bruto = nfe_xml()
        d = AdaptadorNFe().normalizar(bruto)
        assert d.xml_original.encode() == bruto


class TestReformaTributaria:
    """CBS, IBS e Imposto Seletivo — NT 2025.002, exigidos desde 03/08/2026."""

    def test_le_cst_e_classificacao(self):
        item = AdaptadorNFe().normalizar(nfe_xml()).itens[0]
        assert item.cst_ibscbs == "000"
        assert item.class_trib_ibscbs == "000001"
        assert item.base_ibscbs == 1000.00

    def test_ibs_preserva_as_duas_parcelas(self):
        """Somar estado e município destruiria o cerne do imposto.

        O IBS é um tributo com duas destinações, e a partilha entre os entes é
        o que a apuração precisa saber.
        """
        item = AdaptadorNFe().normalizar(nfe_xml()).itens[0]
        assert (item.aliquota_ibs_uf, item.valor_ibs_uf) == (0.07, 0.70)
        assert (item.aliquota_ibs_mun, item.valor_ibs_mun) == (0.03, 0.30)

    def test_municipio_do_fato_gerador_do_ibs_vem_do_ide(self):
        """`cMunFGIBS` é campo do documento (B12a), não do imposto do item.

        E é diferente do `cMunFG` do ICMS de propósito: são o município da
        operação e o município de consumo, e podem não ser o mesmo.
        """
        documento = AdaptadorNFe().normalizar(nfe_xml())
        assert documento.municipio_fg_ibs == "3106200"
        assert documento.municipio_codigo == "3550308"

    def test_cbs(self):
        item = AdaptadorNFe().normalizar(nfe_xml()).itens[0]
        assert item.aliquota_cbs == 0.90
        assert item.valor_cbs == 9.00

    def test_imposto_seletivo_ad_valorem(self):
        item = AdaptadorNFe().normalizar(nfe_xml()).itens[0]
        assert item.cst_is == "000"
        assert item.aliquota_is == 1.0
        assert item.valor_is == 10.00

    def test_imposto_seletivo_com_aliquota_especifica(self):
        """Bebidas e cigarros são tributados por unidade, não por percentual.

        Sem a unidade e a quantidade tributável, o valor não se explica nem se
        confere.
        """
        item = AdaptadorNFe().normalizar(nfe_xml(is_especifico=True)).itens[0]
        assert item.aliquota_is == 0.0
        assert item.aliquota_is_especifica == 2.5
        assert item.unidade_tributavel_is == "LT"
        assert item.quantidade_tributavel_is == 200.0
        assert item.valor_is == 500.00

    def test_totais_somam_as_parcelas_do_ibs(self):
        d = AdaptadorNFe().normalizar(nfe_xml(itens=2))
        assert d.valor_ibs == pytest.approx(2.00)  # (0,70 + 0,30) × 2
        assert d.valor_cbs == pytest.approx(18.00)

    def test_nota_anterior_a_reforma_nao_quebra(self):
        """Importar histórico é rotina, e ele não tem os grupos novos."""
        d = AdaptadorNFe().normalizar(nfe_xml(com_reforma=False))
        assert d.valor_ibs == 0.0
        assert d.valor_cbs == 0.0
        item = d.itens[0]
        assert item.cst_ibscbs is None
        assert item.valor_icms == 180.00, "o regime atual segue lido"


class TestXMLHostil:
    """Documento fiscal vem de terceiro; o parser é superfície de ataque."""

    def test_doctype_e_recusado(self):
        """`ElementTree` expande entidade interna — 4 níveis já dão 3.000 chars.

        NF-e legítima não declara DOCTYPE: o leiaute é XSD, não DTD. Recusar a
        declaração elimina a classe inteira sem custo.
        """
        bomba = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
]>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><NFe>&lol2;</NFe></nfeProc>"""
        with pytest.raises(XMLPerigoso, match="DOCTYPE"):
            carregar_xml(bomba)

    def test_entidade_externa_nao_le_arquivo(self):
        externa = b"""<?xml version="1.0"?>
<!DOCTYPE r [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<r>&xxe;</r>"""
        with pytest.raises((XMLPerigoso, ValueError)):
            carregar_xml(externa)

    def test_xml_malformado_vira_erro_legivel(self):
        with pytest.raises(ValueError, match="XML inválido"):
            carregar_xml(b"<nfeProc><NFe>")

    def test_lote_sobrevive_a_arquivo_hostil(self, sessao, escritorio):
        """Mil XML não podem se perder por causa de um."""
        importador = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id)
        resultado = importador.importar_lote(
            [
                ("boa.xml", nfe_xml()),
                ("bomba.xml", b'<?xml version="1.0"?><!DOCTYPE x []><x/>'),
                ("outra.xml", nfe_xml(chave="35260712345678000195550010000000021000000028")),
            ]
        )
        assert resultado.importados == 2
        assert resultado.rejeitados == 1

    def test_lote_sobrevive_a_nfe_reconhecida_mas_invalida(self, sessao, escritorio):
        """O caso difícil: o adaptador aceita ler, e a leitura é que falha.

        Um arquivo que nenhum adaptador reconhece é fácil de tratar. O que
        derruba um lote é a NF-e legítima na aparência e quebrada por dentro —
        chave ausente, XML truncado pela transferência. Aqui o erro nasce
        dentro do adaptador, não na escolha dele.
        """
        sem_chave = nfe_xml().replace(b'Id="NFe' + CHAVE_PADRAO.encode() + b'"', b'Id=""')
        truncada = nfe_xml()[: len(nfe_xml()) // 2]
        importador = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id)

        resultado = importador.importar_lote(
            [
                ("sem_chave.xml", sem_chave),
                ("truncada.xml", truncada),
                ("boa.xml", nfe_xml()),
            ]
        )

        assert resultado.rejeitados == 2
        assert resultado.importados == 1, "o arquivo bom depois dos ruins se perdeu"
        motivos = [o.motivo for o in resultado.ocorrencias if o.desfecho is Desfecho.REJEITADO]
        assert any("chave" in (m or "") for m in motivos)
        assert any("XML inválido" in (m or "") for m in motivos)


class TestImportacao:
    def test_documento_chega_ao_banco(self, sessao, escritorio):
        importador = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id)
        ocorrencia = importador.importar(nfe_xml(), nome_arquivo="nota.xml")

        assert ocorrencia.desfecho is Desfecho.IMPORTADO
        gravado = sessao.get(DocumentoFiscal, ocorrencia.documento_id)
        assert gravado.chave == CHAVE_PADRAO
        assert gravado.nome_arquivo == "nota.xml"
        assert gravado.adaptador == "nfe"
        assert len(gravado.itens) == 1

    def test_xml_original_fica_gravado(self, sessao, escritorio):
        """Sem ele não há como conferir o que o emitente declarou."""
        bruto = nfe_xml()
        importador = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id)
        ocorrencia = importador.importar(bruto)

        gravado = sessao.get(DocumentoFiscal, ocorrencia.documento_id)
        assert gravado.xml_original.encode() == bruto
        assert len(gravado.hash_original) == 64

    def test_todo_campo_normalizado_chega_ao_banco(self, sessao, escritorio):
        """Campo lido do XML e não copiado é perda silenciosa de informação."""
        importador = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id)
        ocorrencia = importador.importar(nfe_xml(is_especifico=True))

        item = sessao.get(DocumentoFiscal, ocorrencia.documento_id).itens[0]
        colunas = {c.name for c in ItemDocumentoFiscal.__table__.columns}
        ignorar = {"id", "documento_id"}
        do_adaptador = AdaptadorNFe().normalizar(nfe_xml(is_especifico=True)).itens[0]

        divergentes = [
            campo
            for campo in colunas - ignorar
            if hasattr(do_adaptador, campo) and getattr(item, campo) != getattr(do_adaptador, campo)
        ]
        assert not divergentes, f"campos lidos e não gravados: {divergentes}"

    def test_lote_conta_o_que_aconteceu(self, sessao, escritorio):
        importador = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id)
        resultado = importador.importar_lote(
            [
                ("a.xml", nfe_xml(chave="35260712345678000195550010000000011000000017")),
                ("b.xml", nfe_xml(chave="35260712345678000195550010000000021000000028")),
                ("c.txt", b"isto nao e xml"),
            ]
        )
        assert (resultado.total, resultado.importados, resultado.rejeitados) == (3, 2, 1)
        assert resultado.to_dict()["ocorrencias"][2]["desfecho"] == "rejeitado"


class TestDuplicidade:
    def test_mesmo_arquivo_duas_vezes_nao_duplica(self, sessao, escritorio):
        """Reimportar a mesma pasta é rotina de escritório."""
        importador = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id)
        importador.importar(nfe_xml())
        segunda = importador.importar(nfe_xml())

        assert segunda.desfecho is Desfecho.DUPLICADO
        assert segunda.motivo == "mesmo arquivo"
        assert sessao.execute(select(DocumentoFiscal)).scalars().all().__len__() == 1

    def test_mesma_chave_com_conteudo_diferente_e_apontada(self, sessao, escritorio):
        """Duas versões da mesma nota é problema, e o motivo tem de dizer isso."""
        importador = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id)
        importador.importar(nfe_xml())
        segunda = importador.importar(nfe_xml(numero="1", serie="2"))

        assert segunda.desfecho is Desfecho.DUPLICADO
        assert "conteúdo diferente" in segunda.motivo

    def test_politica_de_erro_interrompe(self, sessao, escritorio):
        importador = ImportadorDeDocumentos(
            sessao, escritorio_id=escritorio.id, politica=PoliticaDeDuplicidade.ERRO
        )
        importador.importar(nfe_xml())
        with pytest.raises(ValueError, match="já importado"):
            importador.importar(nfe_xml())

    def test_politica_de_erro_nao_derruba_o_lote(self, sessao, escritorio):
        """A duplicata vira rejeição; os outros arquivos seguem.

        `ERRO` existe para que a duplicata não entre em silêncio, e não para
        interromper o lote — com mil arquivos, uma duplicata na metade levaria
        junto os quinhentos que vinham depois.
        """
        importador = ImportadorDeDocumentos(
            sessao, escritorio_id=escritorio.id, politica=PoliticaDeDuplicidade.ERRO
        )
        importador.importar(nfe_xml())

        resultado = importador.importar_lote(
            [
                ("repetida.xml", nfe_xml()),
                ("nova.xml", nfe_xml(chave=CHAVE_PADRAO[:-3] + "999", numero="9")),
            ]
        )

        assert resultado.rejeitados == 1
        assert resultado.importados == 1, "a duplicata levou o arquivo seguinte junto"
        assert "já importado" in resultado.ocorrencias[0].motivo

    def test_politica_de_substituir_troca_o_documento(self, sessao, escritorio):
        importador = ImportadorDeDocumentos(
            sessao, escritorio_id=escritorio.id, politica=PoliticaDeDuplicidade.SUBSTITUIR
        )
        importador.importar(nfe_xml())
        segunda = importador.importar(nfe_xml(serie="9"))

        assert segunda.desfecho is Desfecho.SUBSTITUIDO
        # O id pode ser reaproveitado pelo banco; o que importa é o conteúdo.
        restantes = sessao.execute(select(DocumentoFiscal)).scalars().all()
        assert len(restantes) == 1 and restantes[0].serie == "9"

    def test_substituir_descarta_os_ajustes_do_documento_antigo(self, sessao, escritorio):
        """É por isso que substituir não é o padrão.

        O tratamento fiscal já feito sobre o documento — a camada 2 — vai
        junto. Reimportar uma pasta com a política errada apagaria horas de
        classificação sem avisar.
        """
        from src.db.models import AjusteFiscal

        importador = ImportadorDeDocumentos(
            sessao, escritorio_id=escritorio.id, politica=PoliticaDeDuplicidade.SUBSTITUIR
        )
        primeira = importador.importar(nfe_xml())
        sessao.add(
            AjusteFiscal(
                documento_id=primeira.documento_id,
                campo="cfop",
                valor_anterior="6102",
                valor_novo="6404",
                origem="usuario",
            )
        )
        sessao.commit()
        assert sessao.execute(select(AjusteFiscal)).scalars().all()

        importador.importar(nfe_xml(serie="9"))

        assert not sessao.execute(select(AjusteFiscal)).scalars().all(), (
            "o ajuste sobreviveu à substituição — o documento a que ele se "
            "referia não existe mais"
        )

    def test_escritorios_diferentes_nao_colidem(self, sessao, escritorio):
        """A mesma nota pode ser escriturada por dois escritórios."""
        outro = Escritorio(nome="Outro", slug="outro")
        sessao.add(outro)
        sessao.commit()

        ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(nfe_xml())
        segunda = ImportadorDeDocumentos(sessao, escritorio_id=outro.id).importar(nfe_xml())

        assert segunda.desfecho is Desfecho.IMPORTADO


class TestSentido:
    """Entrada ou saída depende de QUEM escritura, não do que o emitente disse."""

    def _empresa(self, sessao, escritorio, cnpj):
        e = Empresa(cnpj=cnpj, nome="Cliente", escritorio_id=escritorio.id)
        sessao.add(e)
        sessao.commit()
        return e

    def test_nota_emitida_pela_empresa_e_saida(self, sessao, escritorio):
        self._empresa(sessao, escritorio, "12345678000195")
        oco = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(nfe_xml())
        assert sessao.get(DocumentoFiscal, oco.documento_id).sentido == "saida"

    def test_nota_recebida_pela_empresa_e_entrada(self, sessao, escritorio):
        """A MESMA nota, do ponto de vista do destinatário."""
        self._empresa(sessao, escritorio, "98765432000198")
        oco = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(nfe_xml())
        assert sessao.get(DocumentoFiscal, oco.documento_id).sentido == "entrada"

    def test_empresa_e_vinculada_ao_documento(self, sessao, escritorio):
        empresa = self._empresa(sessao, escritorio, "98765432000198")
        oco = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(nfe_xml())
        assert sessao.get(DocumentoFiscal, oco.documento_id).empresa_id == empresa.id

    def test_sem_empresa_cadastrada_o_documento_entra_assim_mesmo(self, sessao, escritorio):
        """Não é erro: é material para o cadastro, e a preparação vai cobrar."""
        oco = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(nfe_xml())
        assert oco.desfecho is Desfecho.IMPORTADO
        assert sessao.get(DocumentoFiscal, oco.documento_id).empresa_id is None


class TestAmbasAsPontasCadastradas:
    """Transferência entre filiais do mesmo escritório.

    O documento deveria ser escriturado pelas DUAS empresas — saída para uma,
    entrada para a outra — e o modelo só admite uma `empresa_id`. A escolha
    tem de ser determinística e avisada, senão a mesma nota importada duas
    vezes vincularia a empresas diferentes.
    """

    def _duas_pontas(self, sessao, escritorio):
        for cnpj, nome in (
            ("12345678000195", "MATRIZ"),
            ("98765432000198", "FILIAL"),
        ):
            sessao.add(Empresa(cnpj=cnpj, nome=nome, escritorio_id=escritorio.id))
        sessao.commit()

    def test_escolha_e_o_emitente(self, sessao, escritorio):
        self._duas_pontas(sessao, escritorio)
        oco = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(nfe_xml())

        documento = sessao.get(DocumentoFiscal, oco.documento_id)
        assert documento.emitente_cnpj == "12345678000195"
        empresa = sessao.get(Empresa, documento.empresa_id)
        assert empresa.cnpj == "12345678000195", "não ficou com o emitente"
        assert documento.sentido == "saida"

    def test_a_escolha_nao_depende_da_ordem_de_cadastro(self, sessao, escritorio):
        """Com `.limit(1)` sem ordem, isto dependia do humor do banco."""
        for cnpj, nome in (
            ("98765432000198", "FILIAL"),
            ("12345678000195", "MATRIZ"),
        ):
            sessao.add(Empresa(cnpj=cnpj, nome=nome, escritorio_id=escritorio.id))
        sessao.commit()

        oco = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(nfe_xml())

        documento = sessao.get(DocumentoFiscal, oco.documento_id)
        assert sessao.get(Empresa, documento.empresa_id).cnpj == "12345678000195"

    def test_avisa_que_a_contraparte_fica_sem_escrituracao(self, sessao, escritorio, caplog):
        self._duas_pontas(sessao, escritorio)
        with caplog.at_level("WARNING", logger="sped-hub.documentos"):
            ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(nfe_xml())

        assert any(
            "duas pontas" in r.getMessage() for r in caplog.records
        ), "a contraparte fica sem escrituração e ninguém é avisado"
