"""A terceira camada: o arquivo que efetivamente saiu.

O que estes testes protegem:

  * **o arquivado não muda quando o efetivo muda** — é a diferença entre
    responder "o que eu enviaria hoje" e "o que você enviou", e só a segunda
    resposta serve numa intimação;
  * **arquivar não sobrescreve** — duas gerações do mesmo período são dois
    fatos, e apagar o primeiro apagaria a única cópia do que saiu;
  * **o hash é do texto como saiu**, com CRLF, para conferir contra o arquivo
    que o contribuinte tem em mãos.
"""

from __future__ import annotations

import datetime
import json

import pytest
from sqlalchemy import select

from src.db.models import (
    DocumentoFiscal,
    Empresa,
    Escritorio,
    Escrituracao,
    EscrituracaoDocumento,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos import ORIGEM_USUARIO, ImportadorDeDocumentos, aplicar_ajuste
from src.escrituracoes import (
    TIPOS,
    GeradorEFDContribuicoes,
    GeradorEFDICMS,
    TipoDesconhecido,
    arquivar,
    avisos_de,
    comparar,
    escrituracoes_do_documento,
    hash_do_conteudo,
)
from tests.fixtures_nfe import nfe_xml

INICIO = datetime.date(2026, 7, 1)
FIM = datetime.date(2026, 7, 31)


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'arquivadas.db'}")
    init_db(engine)
    with get_session(engine) as s:
        yield s


@pytest.fixture
def escritorio(sessao):
    e = Escritorio(nome="Teste", slug="teste")
    sessao.add(e)
    sessao.commit()
    return e


@pytest.fixture
def empresa(sessao, escritorio):
    e = Empresa(
        cnpj="98765432000198",
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
    sessao.add(e)
    sessao.commit()
    return e


@pytest.fixture
def com_documentos(sessao, escritorio, empresa):
    importador = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id)
    for n in (1, 2):
        importador.importar(
            nfe_xml(
                chave=f"3526071234567800019555001000000{n:04d}1000000017",
                numero=str(n),
                itens=n,
            )
        )
    sessao.commit()
    return empresa


def _gerar(sessao, empresa, gerador=GeradorEFDICMS):
    return gerador(sessao, empresa=empresa, data_inicio=INICIO, data_fim=FIM).gerar()


def _arquivar(sessao, empresa, *, tipo="efd_icms", gerador=GeradorEFDICMS, **kwargs):
    return arquivar(
        sessao,
        resultado=_gerar(sessao, empresa, gerador),
        empresa=empresa,
        tipo=tipo,
        data_inicio=INICIO,
        data_fim=FIM,
        **kwargs,
    )


class TestOArquivadoNaoMudaComOEfetivo:
    """A razão de a camada existir."""

    def test_ajuste_posterior_nao_altera_o_arquivado(self, sessao, com_documentos):
        """Regerar responderia "o que eu enviaria hoje"; a pergunta é outra."""
        escrituracao = _arquivar(sessao, com_documentos)
        conteudo_entregue = escrituracao.conteudo
        hash_entregue = escrituracao.hash_conteudo

        documento = sessao.execute(select(DocumentoFiscal)).scalars().first()
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0],
            campo="cfop",
            valor_novo="2102",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()

        sessao.refresh(escrituracao)
        assert escrituracao.conteudo == conteudo_entregue
        assert escrituracao.hash_conteudo == hash_entregue

    def test_e_a_geracao_nova_realmente_difere(self, sessao, com_documentos):
        """Sem isto o teste acima passaria mesmo com o ajuste não fazendo nada."""
        escrituracao = _arquivar(sessao, com_documentos)

        documento = sessao.execute(select(DocumentoFiscal)).scalars().first()
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0],
            campo="cfop",
            valor_novo="2102",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()

        assert _gerar(sessao, com_documentos).texto() != escrituracao.conteudo

    def test_comparar_aponta_a_divergencia(self, sessao, com_documentos):
        escrituracao = _arquivar(sessao, com_documentos)

        documento = sessao.execute(select(DocumentoFiscal)).scalars().first()
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0],
            campo="cfop",
            valor_novo="2102",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()

        comparacao = comparar(escrituracao, _gerar(sessao, com_documentos))

        assert not comparacao.iguais
        assert comparacao  # __bool__ = "há divergência"
        assert any("C170" in linha for linha in comparacao.resumo)
        assert any("2102" in linha for linha in comparacao.diff)

    def test_sem_mudanca_nao_ha_divergencia(self, sessao, com_documentos):
        escrituracao = _arquivar(sessao, com_documentos)

        comparacao = comparar(escrituracao, _gerar(sessao, com_documentos))

        assert comparacao.iguais
        assert not comparacao
        assert comparacao.resumo == []
        assert comparacao.diff == []

    def test_resumo_conta_registro_que_entrou(self, sessao, escritorio, empresa):
        """Documento importado depois da entrega vira registro a mais."""
        importador = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id)
        importador.importar(nfe_xml())
        sessao.commit()
        escrituracao = _arquivar(sessao, empresa)

        importador.importar(
            nfe_xml(chave="35260712345678000195550010000000021000000017", numero="2")
        )
        sessao.commit()

        resumo = comparar(escrituracao, _gerar(sessao, empresa)).resumo

        assert any(linha.startswith("C100: 1 → 2 registros") for linha in resumo)

    def test_resumo_explica_o_bloco_9_quando_ele_se_mexe(self, sessao, escritorio, empresa):
        """Acrescentar documento muda contagem, e a contagem move o bloco 9."""
        importador = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id)
        importador.importar(nfe_xml())
        sessao.commit()
        escrituracao = _arquivar(sessao, empresa)

        importador.importar(
            nfe_xml(chave="35260712345678000195550010000000021000000017", numero="2")
        )
        sessao.commit()

        resumo = comparar(escrituracao, _gerar(sessao, empresa)).resumo

        assert any("bloco 9" in linha and "não são causa própria" in linha for linha in resumo)

    def test_sem_mexer_no_bloco_9_o_aviso_nao_aparece(self, sessao, com_documentos):
        """Trocar o CFOP de um item não muda contagem nenhuma.

        Se o aviso saísse sempre, ele não informaria nada — e mandaria o
        operador ignorar o bloco 9 justamente quando ele merece atenção.
        """
        escrituracao = _arquivar(sessao, com_documentos)

        documento = sessao.execute(select(DocumentoFiscal)).scalars().first()
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0],
            campo="descricao",
            valor_novo="PRODUTO RENOMEADO",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()

        resumo = comparar(escrituracao, _gerar(sessao, com_documentos)).resumo

        assert resumo, "algo mudou; o resumo não pode sair vazio"
        assert not any("bloco 9" in linha for linha in resumo)

    def test_linha_repetida_nao_esconde_alteracao(self, sessao, escritorio, empresa):
        """Dois documentos com o mesmo produto geram C170 idênticos.

        Perguntar "esta linha continua no arquivo?" responderia que sim quando
        uma das duas mudou — e o resumo diria que o C170 está intacto
        justamente quando não está. A contagem tem de ser de multiconjunto.
        """
        importador = ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id)
        for n in (1, 2):
            importador.importar(
                nfe_xml(chave=f"3526071234567800019555001000000{n:04d}1000000017", numero=str(n))
            )
        sessao.commit()
        escrituracao = _arquivar(sessao, empresa)

        c170 = [
            linha
            for linha in escrituracao.conteudo.replace("\r\n", "\n").split("\n")
            if linha.startswith("|C170|")
        ]
        assert c170[0] == c170[1], "o cenário exige duas linhas idênticas"

        documento = sessao.execute(select(DocumentoFiscal)).scalars().first()
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0],
            campo="valor_desconto",
            valor_novo="12.34",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()

        resumo = comparar(escrituracao, _gerar(sessao, empresa)).resumo

        assert any(
            linha.startswith("C170: 1 de 2 registros com conteúdo diferente") for linha in resumo
        ), resumo


class TestArquivarNaoSobrescreve:
    def test_duas_geracoes_do_mesmo_periodo_sao_duas_linhas(self, sessao, com_documentos):
        """Qual delas foi transmitida é informação que o sistema não tem."""
        primeira = _arquivar(sessao, com_documentos)
        segunda = _arquivar(sessao, com_documentos)

        assert primeira.id != segunda.id
        assert sessao.execute(select(Escrituracao)).scalars().all() == [primeira, segunda]

    def test_a_primeira_continua_recuperavel(self, sessao, com_documentos):
        primeira = _arquivar(sessao, com_documentos)
        conteudo = primeira.conteudo

        documento = sessao.execute(select(DocumentoFiscal)).scalars().first()
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0],
            campo="cfop",
            valor_novo="2102",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()
        segunda = _arquivar(sessao, com_documentos)

        assert primeira.conteudo == conteudo
        assert segunda.conteudo != conteudo


class TestConteudoEHash:
    def test_o_conteudo_e_o_arquivo_inteiro(self, sessao, com_documentos):
        resultado = _gerar(sessao, com_documentos)
        escrituracao = arquivar(
            sessao,
            resultado=resultado,
            empresa=com_documentos,
            tipo="efd_icms",
            data_inicio=INICIO,
            data_fim=FIM,
        )

        assert escrituracao.conteudo == resultado.texto()
        assert escrituracao.total_linhas == resultado.total_linhas

    def test_o_hash_e_do_texto_com_crlf(self, sessao, com_documentos):
        """Normalizar antes de somar daria o mesmo hash para arquivos diferentes.

        O validador do Fisco recusa arquivo com LF; se o hash não distingue os
        dois, ele não serve para conferir o que foi entregue.
        """
        escrituracao = _arquivar(sessao, com_documentos)

        assert escrituracao.hash_conteudo == hash_do_conteudo(escrituracao.conteudo)
        com_lf = escrituracao.conteudo.replace("\r\n", "\n")
        assert hash_do_conteudo(com_lf) != escrituracao.hash_conteudo

    def test_o_hash_muda_quando_o_conteudo_muda(self, sessao, com_documentos):
        primeira = _arquivar(sessao, com_documentos)

        documento = sessao.execute(select(DocumentoFiscal)).scalars().first()
        aplicar_ajuste(
            sessao,
            documento=documento,
            item=documento.itens[0],
            campo="cfop",
            valor_novo="2102",
            origem=ORIGEM_USUARIO,
        )
        sessao.flush()

        assert _arquivar(sessao, com_documentos).hash_conteudo != primeira.hash_conteudo

    def test_avisos_ficam_como_estavam_na_geracao(self, sessao, com_documentos):
        """Fazem parte do que a pessoa viu ao decidir transmitir."""
        resultado = _gerar(sessao, com_documentos)
        escrituracao = _arquivar(sessao, com_documentos)

        assert avisos_de(escrituracao) == resultado.avisos
        assert avisos_de(escrituracao), "a geração produz avisos; guardá-los importa"

    def test_avisos_corrompidos_nao_derrubam_a_leitura(self, sessao, com_documentos):
        escrituracao = _arquivar(sessao, com_documentos)
        escrituracao.avisos = "isto não é JSON"

        assert avisos_de(escrituracao) == []

    def test_avisos_saem_legiveis_no_banco(self, sessao, com_documentos):
        """`ensure_ascii` faria "não" virar "n\\u00e3o" para quem lê a tabela."""
        escrituracao = _arquivar(sessao, com_documentos)

        assert "\\u" not in escrituracao.avisos
        assert json.loads(escrituracao.avisos) == avisos_de(escrituracao)


class TestQuaisDocumentosEntraram:
    def test_liga_os_documentos_escriturados(self, sessao, com_documentos):
        escrituracao = _arquivar(sessao, com_documentos)

        ligados = {e.documento_id for e in escrituracao.documentos}
        todos = {d.id for d in sessao.execute(select(DocumentoFiscal)).scalars()}

        assert ligados == todos

    def test_documento_fora_do_periodo_nao_e_ligado(self, sessao, escritorio, empresa):
        ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(nfe_xml())
        sessao.commit()

        resultado = GeradorEFDICMS(
            sessao,
            empresa=empresa,
            data_inicio=datetime.date(2026, 8, 1),
            data_fim=datetime.date(2026, 8, 31),
        ).gerar()
        escrituracao = arquivar(
            sessao,
            resultado=resultado,
            empresa=empresa,
            tipo="efd_icms",
            data_inicio=datetime.date(2026, 8, 1),
            data_fim=datetime.date(2026, 8, 31),
        )

        assert escrituracao.documentos == []

    def test_em_que_arquivos_esta_nota_entrou(self, sessao, com_documentos):
        """A pergunta que a intimação faz."""
        icms = _arquivar(sessao, com_documentos)
        contribuicoes = _arquivar(
            sessao,
            com_documentos,
            tipo="efd_contribuicoes",
            gerador=GeradorEFDContribuicoes,
        )

        documento = sessao.execute(select(DocumentoFiscal)).scalars().first()

        assert escrituracoes_do_documento(sessao, documento) == [icms, contribuicoes]

    def test_nota_nao_escriturada_nao_aparece_em_lugar_nenhum(
        self, sessao, escritorio, empresa, com_documentos
    ):
        _arquivar(sessao, com_documentos)

        ImportadorDeDocumentos(sessao, escritorio_id=escritorio.id).importar(
            nfe_xml(chave="35260812345678000195550010000000091000000017", numero="9")
        )
        sessao.commit()
        nova = (
            sessao.execute(select(DocumentoFiscal).order_by(DocumentoFiscal.id.desc()))
            .scalars()
            .first()
        )

        assert escrituracoes_do_documento(sessao, nova) == []

    def test_o_mesmo_documento_nao_e_ligado_duas_vezes(self, sessao, com_documentos):
        """A restrição única existe; o arquivamento não deve encostar nela."""
        resultado = _gerar(sessao, com_documentos)
        resultado.documentos_ids = resultado.documentos_ids * 2

        escrituracao = arquivar(
            sessao,
            resultado=resultado,
            empresa=com_documentos,
            tipo="efd_icms",
            data_inicio=INICIO,
            data_fim=FIM,
        )

        ligacoes = sessao.execute(select(EscrituracaoDocumento)).scalars().all()
        assert len(ligacoes) == len(escrituracao.documentos) == 2


class TestCadastroDaEscrituracao:
    def test_tipo_desconhecido_e_recusado(self, sessao, com_documentos):
        """Arquivar sob tipo inventado tornaria o arquivo inencontrável."""
        with pytest.raises(TipoDesconhecido, match="efd_icms"):
            _arquivar(sessao, com_documentos, tipo="efd_isso_ai")

    @pytest.mark.parametrize("tipo", sorted(TIPOS))
    def test_todo_tipo_conhecido_arquiva(self, sessao, com_documentos, tipo):
        gerador = GeradorEFDContribuicoes if tipo == "efd_contribuicoes" else GeradorEFDICMS
        assert _arquivar(sessao, com_documentos, tipo=tipo, gerador=gerador).id

    def test_guarda_periodo_empresa_e_escritorio(self, sessao, com_documentos):
        escrituracao = _arquivar(sessao, com_documentos)

        assert escrituracao.empresa_id == com_documentos.id
        assert escrituracao.escritorio_id == com_documentos.escritorio_id
        assert (escrituracao.data_inicio, escrituracao.data_fim) == (INICIO, FIM)

    def test_guarda_quem_gerou(self, sessao, com_documentos):
        assert _arquivar(sessao, com_documentos, usuario_id=None).usuario_id is None

    def test_registra_quando_foi_gerada(self, sessao, com_documentos):
        antes = datetime.datetime.now(datetime.UTC)
        escrituracao = _arquivar(sessao, com_documentos)
        depois = datetime.datetime.now(datetime.UTC)

        gerada = escrituracao.gerada_em
        if gerada.tzinfo is None:  # o SQLite devolve a coluna sem fuso
            gerada = gerada.replace(tzinfo=datetime.UTC)
        assert antes <= gerada <= depois
