"""Substituir na importação não apaga documento que já entrou em arquivo.

O defeito: com a política `SUBSTITUIR`, o importador apagava o documento
existente para gravar o novo. Se ele já estava numa escrituração arquivada, a
ligação `escrituracoes_documentos` impedia o `DELETE` e o banco levantava
`IntegrityError` — que o lote não trata (só `ValueError`): a importação
inteira abortava no meio, levando junto os arquivos que vinham depois, com a
sessão num estado inutilizável.

Apagar o documento seria, além disso, apagar a resposta à pergunta que a
terceira camada existe para responder: "esta nota entrou em qual arquivo?".
Agora o documento escriturado é recusado com o motivo, e o lote segue.
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import select

from src.db.models import (
    DocumentoFiscal,
    Empresa,
    Escritorio,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos import Desfecho, ImportadorDeDocumentos, PoliticaDeDuplicidade
from src.escrituracoes import GeradorEFDICMS, arquivar, escrituracoes_do_documento
from tests.fixtures_nfe import CHAVE_PADRAO, nfe_xml

INICIO = datetime.date(2026, 7, 1)
FIM = datetime.date(2026, 7, 31)
OUTRA = "35260712345678000195550010000000021000000010"


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'substituir.db'}")
    init_db(engine)
    with get_session(engine) as s:
        escritorio = Escritorio(nome="Teste", slug="teste")
        s.add(escritorio)
        s.commit()
        empresa = Empresa(
            cnpj="98765432000198",
            nome="COMERCIO EXEMPLO LTDA",
            uf="TO",
            ie="293456789",
            cod_mun="1721000",
            ind_perfil="A",
            ind_ativ="1",
            escritorio_id=escritorio.id,
        )
        s.add(empresa)
        s.commit()
        ImportadorDeDocumentos(s, escritorio_id=escritorio.id).importar(nfe_xml())
        s.commit()
        resultado = GeradorEFDICMS(s, empresa=empresa, data_inicio=INICIO, data_fim=FIM).gerar()
        arquivar(
            s,
            resultado=resultado,
            empresa=empresa,
            tipo="efd_icms",
            data_inicio=INICIO,
            data_fim=FIM,
        )
        s.commit()
        yield s


def _substituir(sessao):
    return ImportadorDeDocumentos(
        sessao, escritorio_id=1, politica=PoliticaDeDuplicidade.SUBSTITUIR
    ).importar_lote(
        [
            ("corrigida.xml", nfe_xml(ind_pag="0")),  # mesma chave, conteúdo diferente
            ("outra.xml", nfe_xml(chave=OUTRA, numero="2")),
        ]
    )


def test_o_lote_segue_e_o_escriturado_e_recusado(sessao):
    resultado = _substituir(sessao)  # não levanta IntegrityError

    primeira, segunda = resultado.ocorrencias
    assert primeira.desfecho is Desfecho.REJEITADO, (
        "o documento escriturado foi substituído: apagá-lo apagaria o registro de "
        "em que arquivo ele entrou"
    )
    assert "escrituração" in primeira.motivo and "#1" in primeira.motivo
    assert (
        segunda.desfecho is Desfecho.IMPORTADO
    ), "um documento escriturado derrubou o lote: os arquivos seguintes não entraram"


def test_o_documento_escriturado_fica_como_estava(sessao):
    _substituir(sessao)
    sessao.commit()  # a sessão continua utilizável

    documento = sessao.execute(
        select(DocumentoFiscal).where(DocumentoFiscal.chave == CHAVE_PADRAO)
    ).scalar_one()
    assert documento.indicador_pagamento is None, "o conteúdo antigo foi trocado"
    assert [e.id for e in escrituracoes_do_documento(sessao, documento)] == [1]


def test_documento_ainda_nao_escriturado_continua_sendo_substituido(sessao, tmp_path):
    """A recusa é só para o que já entrou em arquivo — o resto segue a política."""
    ImportadorDeDocumentos(sessao, escritorio_id=1).importar(nfe_xml(chave=OUTRA, numero="2"))
    sessao.commit()

    ocorrencia = ImportadorDeDocumentos(
        sessao, escritorio_id=1, politica=PoliticaDeDuplicidade.SUBSTITUIR
    ).importar(nfe_xml(chave=OUTRA, numero="2", ind_pag="1"))

    assert ocorrencia.desfecho is Desfecho.SUBSTITUIDO
