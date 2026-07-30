"""O comando `sped-hub fiscal` — a cadeia fiscal pela linha de comando.

O que estes testes protegem:

  * **gerar sempre arquiva** — um arquivo que sai do sistema sem registro é o
    buraco que a terceira camada existe para fechar;
  * **o arquivo em disco sai com CRLF** — o `open` do Python reescreveria o
    `\\r\\n` como `\\r\\r\\n` no Windows, e o validador recusaria tudo;
  * **os códigos de saída**, porque isto vai para dentro de script de
    fechamento: 0 correu bem, 1 erro, 2 divergiu;
  * **os avisos aparecem** — são o canal de "leia antes de transmitir";
  * **nada grava sem que se peça** — `classificar` e `alterar` mostram por
    padrão. Inverter isso na CLI seria desfazer, na porta de entrada, a
    proteção que os motores têm por dentro.
"""

from __future__ import annotations

import datetime
from unittest import mock

import pytest
from sqlalchemy import select

from src import cli_fiscal
from src.cli import main
from src.db.models import (
    AjusteFiscal,
    DocumentoFiscal,
    Empresa,
    Escritorio,
    Escrituracao,
    criar_engine,
    get_session,
    init_db,
)
from src.documentos import ORIGEM_USUARIO, aplicar_ajuste, valor_efetivo
from src.documentos.classificacao import criar_regra
from tests.fixtures_nfe import nfe_xml

CNPJ = "98765432000198"


@pytest.fixture
def banco(tmp_path):
    """Um banco pronto, e a URL para passar no `--db`."""
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


@pytest.fixture
def pasta_xml(tmp_path):
    pasta = tmp_path / "xml"
    pasta.mkdir()
    for n in (1, 2):
        (pasta / f"nfe{n}.xml").write_bytes(
            nfe_xml(
                chave=f"3526071234567800019555001000000{n:04d}1000000017",
                numero=str(n),
                itens=n,
            )
        )
    return pasta


@pytest.fixture
def importado(banco, pasta_xml):
    assert main(["fiscal", "importar", str(pasta_xml), "--escritorio", "1", "--db", banco]) == 0
    return banco


def _sessao(url):
    return get_session(criar_engine(url=url))


def _gerar(banco, saida, *extras):
    return main(
        [
            "fiscal",
            "gerar",
            "--empresa",
            "1",
            "--de",
            "2026-07-01",
            "--ate",
            "2026-07-31",
            "--saida",
            str(saida),
            "--db",
            banco,
            *extras,
        ]
    )


class TestGerarSempreArquiva:
    """A ausência de `--sem-arquivar` é deliberada."""

    def test_gerar_grava_a_escrituracao(self, importado, tmp_path):
        _gerar(importado, tmp_path / "efd.txt")

        with _sessao(importado) as sessao:
            escrituracoes = sessao.execute(select(Escrituracao)).scalars().all()

        assert len(escrituracoes) == 1
        assert escrituracoes[0].tipo == "efd_icms"

    def test_o_arquivado_e_o_arquivo_em_disco(self, importado, tmp_path):
        """Se divergissem, o registro não provaria o que foi entregue."""
        destino = tmp_path / "efd.txt"
        _gerar(importado, destino)

        with _sessao(importado) as sessao:
            escrituracao = sessao.execute(select(Escrituracao)).scalars().one()

        assert destino.read_bytes().decode("utf-8") == escrituracao.conteudo

    def test_nao_existe_como_gerar_sem_arquivar(self, importado, tmp_path, capsys):
        """Uma prévia que grava em disco é indistinguível de uma entrega."""
        with pytest.raises(SystemExit):
            _gerar(importado, tmp_path / "efd.txt", "--sem-arquivar")

        assert "unrecognized arguments" in capsys.readouterr().err

    def test_gerar_de_novo_cria_outra(self, importado, tmp_path):
        _gerar(importado, tmp_path / "a.txt")
        _gerar(importado, tmp_path / "b.txt")

        with _sessao(importado) as sessao:
            assert len(sessao.execute(select(Escrituracao)).scalars().all()) == 2


class TestOArquivoEmDisco:
    def test_sai_com_crlf(self, importado, tmp_path):
        destino = tmp_path / "efd.txt"
        _gerar(importado, destino)

        bruto = destino.read_bytes()

        assert b"\r\n" in bruto
        assert b"\r\r\n" not in bruto, "o Python reescreveu a quebra de linha"
        assert bruto.replace(b"\r\n", b"").count(b"\n") == 0

    def test_grava_sem_traduzir_a_quebra_de_linha(self, tmp_path):
        """O teste que só o Windows precisaria, e que só o Linux pode rodar.

        `open` em modo texto sem `newline=""` reescreve `\\n` como `\\r\\n` no
        Windows; com o texto do leiaute já em `\\r\\n`, sai `\\r\\r\\n` e o
        validador recusa tudo. **No Linux os dois modos gravam os mesmos
        bytes**, então conferir o arquivo não distingue nada — o que dá para
        conferir é a chamada. Foi exatamente assim que o entrypoint do nginx
        quebrou para quem constrói no Windows, com a suíte inteira verde.
        """
        chamada = {}
        real = open

        def espiao(*args, **kwargs):
            chamada.update(kwargs)
            return real(*args, **kwargs)

        with mock.patch("src.cli_fiscal.open", espiao, create=True):
            cli_fiscal.gravar(tmp_path / "x.txt", "|0000|\r\n")

        assert chamada.get("newline") == "", (
            "sem newline='' o Python traduz a quebra de linha no Windows, "
            "e o arquivo sai com CRCRLF"
        )

    def test_gravar_devolve_os_bytes_que_recebeu(self, tmp_path):
        destino = tmp_path / "x.txt"

        cli_fiscal.gravar(destino, "|0000|\r\n|9999|2|\r\n")

        assert destino.read_bytes() == b"|0000|\r\n|9999|2|\r\n"

    def test_sem_saida_usa_nome_derivado(self, importado, tmp_path, monkeypatch):
        """Quem não passa `--saida` precisa achar o arquivo depois."""
        monkeypatch.chdir(tmp_path)

        assert (
            main(
                [
                    "fiscal",
                    "gerar",
                    "--empresa",
                    "1",
                    "--de",
                    "2026-07-01",
                    "--ate",
                    "2026-07-31",
                    "--db",
                    importado,
                ]
            )
            == 0
        )
        assert (tmp_path / f"efd_icms_{CNPJ}_202607.txt").exists()


class TestCodigosDeSaida:
    """Isto entra em script de fechamento; o código de saída é contrato."""

    def test_sucesso_e_zero(self, importado, tmp_path):
        assert _gerar(importado, tmp_path / "efd.txt") == 0

    def test_empresa_inexistente_e_um(self, banco, tmp_path, capsys):
        codigo = main(
            [
                "fiscal",
                "gerar",
                "--empresa",
                "999",
                "--de",
                "2026-07-01",
                "--ate",
                "2026-07-31",
                "--saida",
                str(tmp_path / "efd.txt"),
                "--db",
                banco,
            ]
        )

        assert codigo == 1
        assert "não existe empresa #999" in capsys.readouterr().out

    def test_cadastro_faltando_e_um_com_o_motivo(self, importado, tmp_path, capsys):
        """O gerador recusa sem `ind_perfil`; a CLI tem de dizer isso."""
        with _sessao(importado) as sessao:
            empresa = sessao.get(Empresa, 1)
            empresa.ind_perfil = None
            sessao.commit()

        codigo = _gerar(importado, tmp_path / "efd.txt")

        saida = capsys.readouterr().out
        assert codigo == 1
        assert "ind_perfil" in saida

    def test_conferir_igual_e_zero(self, importado, tmp_path):
        _gerar(importado, tmp_path / "efd.txt")

        assert main(["fiscal", "conferir", "--escrituracao", "1", "--db", importado]) == 0

    def test_conferir_divergente_e_dois(self, importado, tmp_path, capsys):
        """O código que permite alertar quando o entregue não bate mais."""
        _gerar(importado, tmp_path / "efd.txt")
        with _sessao(importado) as sessao:
            documento = sessao.execute(select(DocumentoFiscal)).scalars().first()
            aplicar_ajuste(
                sessao,
                documento=documento,
                item=documento.itens[0],
                campo="cfop",
                valor_novo="2102",
                origem=ORIGEM_USUARIO,
            )
            sessao.commit()

        codigo = main(["fiscal", "conferir", "--escrituracao", "1", "--db", importado])

        assert codigo == 2
        assert "DIVERGIU" in capsys.readouterr().out

    def test_banco_sem_schema_explica_o_que_fazer(self, tmp_path, capsys):
        """Um traceback de SQLAlchemy não diz ao contador o que fazer."""
        codigo = main(["fiscal", "empresas", "--db", f"sqlite:///{tmp_path / 'vazio.db'}"])

        saida = capsys.readouterr().out
        assert codigo == 1
        assert "sped-hub migrar" in saida
        assert "Traceback" not in saida

    def test_escrituracao_inexistente_e_um(self, banco, capsys):
        codigo = main(["fiscal", "conferir", "--escrituracao", "42", "--db", banco])

        assert codigo == 1
        assert "não existe escrituração #42" in capsys.readouterr().out


class TestArgumentosObrigatorios:
    @pytest.mark.parametrize(
        ("acao", "faltando"),
        [
            ("gerar", "--empresa"),
            ("documentos", "--empresa"),
            ("conferir", "--escrituracao"),
            ("importar", "caminhos"),
        ],
    )
    def test_falta_de_argumento_e_recusada(self, banco, capsys, acao, faltando):
        codigo = main(["fiscal", acao, "--db", banco])

        saida = capsys.readouterr().out
        assert codigo == 1
        assert faltando in saida
        assert acao in saida

    def test_gerar_sem_periodo_e_recusado(self, banco, capsys):
        codigo = main(["fiscal", "gerar", "--empresa", "1", "--db", banco])

        assert codigo == 1
        assert "--de" in capsys.readouterr().out


class TestImportar:
    def test_importa_a_pasta_inteira(self, banco, pasta_xml, capsys):
        codigo = main(["fiscal", "importar", str(pasta_xml), "--escritorio", "1", "--db", banco])

        assert codigo == 0
        assert "2 importados" in capsys.readouterr().out

    def test_ignora_o_que_nao_e_xml(self, banco, pasta_xml, capsys):
        """Varrer sem filtro encheria o relatório de rejeições de PDF."""
        (pasta_xml / "boleto.pdf").write_bytes(b"%PDF-1.4 nao sou nota")

        main(["fiscal", "importar", str(pasta_xml), "--escritorio", "1", "--db", banco])

        saida = capsys.readouterr().out
        assert "2 arquivos" in saida
        assert "boleto.pdf" not in saida

    def test_xml_ilegivel_e_rejeitado_com_motivo(self, banco, pasta_xml, capsys):
        """Um arquivo ruim não pode derrubar o lote no fechamento."""
        (pasta_xml / "truncado.xml").write_bytes(b"<nfe>corta")

        codigo = main(["fiscal", "importar", str(pasta_xml), "--escritorio", "1", "--db", banco])

        saida = capsys.readouterr().out
        assert codigo == 0
        assert "2 importados" in saida
        assert "1 rejeitados" in saida
        assert "truncado.xml" in saida

    def test_arquivo_solto_tambem_serve(self, banco, pasta_xml, capsys):
        codigo = main(
            [
                "fiscal",
                "importar",
                str(pasta_xml / "nfe1.xml"),
                "--escritorio",
                "1",
                "--db",
                banco,
            ]
        )

        assert codigo == 0
        assert "1 importados" in capsys.readouterr().out

    def test_caminho_sem_xml_nenhum_avisa(self, banco, tmp_path, capsys):
        vazia = tmp_path / "vazia"
        vazia.mkdir()

        codigo = main(["fiscal", "importar", str(vazia), "--db", banco])

        assert codigo == 1
        assert "Nenhum XML" in capsys.readouterr().out


class TestOQueAparece:
    def test_os_avisos_da_geracao_aparecem(self, importado, tmp_path, capsys):
        """São o canal de "leia antes de transmitir"; engoli-los seria pior
        que não gerar."""
        _gerar(importado, tmp_path / "efd.txt")

        saida = capsys.readouterr().out

        assert "LEIA ANTES DE TRANSMITIR" in saida
        assert "soma direta" in saida

    def test_gerar_mostra_a_escrituracao_e_o_hash(self, importado, tmp_path, capsys):
        _gerar(importado, tmp_path / "efd.txt")

        with _sessao(importado) as sessao:
            escrituracao = sessao.execute(select(Escrituracao)).scalars().one()

        saida = capsys.readouterr().out
        assert f"#{escrituracao.id}" in saida
        assert escrituracao.hash_conteudo[:16] in saida

    def test_historico_lista_o_que_foi_gerado(self, importado, tmp_path, capsys):
        _gerar(importado, tmp_path / "efd.txt")
        capsys.readouterr()

        assert main(["fiscal", "historico", "--db", importado]) == 0
        assert "efd_icms" in capsys.readouterr().out

    def test_valores_saem_no_formato_brasileiro(self, importado, capsys):
        """`f"{v:,.2f}"` daria 1,000.00 — outro número, para quem lê aqui."""
        main(["fiscal", "documentos", "--empresa", "1", "--db", importado])

        saida = capsys.readouterr().out

        assert "1.000,00" in saida
        assert "1,000.00" not in saida

    def test_documentos_mostra_o_total(self, importado, capsys):
        main(["fiscal", "documentos", "--empresa", "1", "--db", importado])

        assert "3.000,00" in capsys.readouterr().out, "1.000 + 2.000"

    def test_empresas_mostra_o_cadastro_fiscal(self, banco, capsys):
        """Descobrir que falta cadastro só na hora de gerar é tarde."""
        main(["fiscal", "empresas", "--db", banco])

        saida = capsys.readouterr().out
        assert CNPJ in saida
        assert "Perfil" in saida and "Regime" in saida

    def test_conferir_sem_diff_nao_despeja_as_linhas(self, importado, tmp_path, capsys):
        _gerar(importado, tmp_path / "efd.txt")
        with _sessao(importado) as sessao:
            documento = sessao.execute(select(DocumentoFiscal)).scalars().first()
            aplicar_ajuste(
                sessao,
                documento=documento,
                item=documento.itens[0],
                campo="cfop",
                valor_novo="2102",
                origem=ORIGEM_USUARIO,
            )
            sessao.commit()
        capsys.readouterr()

        main(["fiscal", "conferir", "--escrituracao", "1", "--db", importado])
        sem_diff = capsys.readouterr().out

        main(["fiscal", "conferir", "--escrituracao", "1", "--diff", "--db", importado])
        com_diff = capsys.readouterr().out

        assert "2102" not in sem_diff
        assert "2102" in com_diff


class TestPeriodo:
    def test_aceita_as_duas_formas_de_data(self, importado, tmp_path):
        """A CLI já aceitava DDMMAAAA e AAAA-MM-DD; divergir confundiria."""
        assert (
            main(
                [
                    "fiscal",
                    "gerar",
                    "--empresa",
                    "1",
                    "--de",
                    "01072026",
                    "--ate",
                    "31072026",
                    "--saida",
                    str(tmp_path / "a.txt"),
                    "--db",
                    importado,
                ]
            )
            == 0
        )

        with _sessao(importado) as sessao:
            escrituracao = sessao.execute(select(Escrituracao)).scalars().one()

        assert escrituracao.data_inicio == datetime.date(2026, 7, 1)
        assert escrituracao.data_fim == datetime.date(2026, 7, 31)

    def test_data_sem_sentido_e_recusada(self, importado, tmp_path, capsys):
        codigo = main(
            [
                "fiscal",
                "gerar",
                "--empresa",
                "1",
                "--de",
                "trinta-de-julho",
                "--ate",
                "2026-07-31",
                "--saida",
                str(tmp_path / "a.txt"),
                "--db",
                importado,
            ]
        )

        assert codigo == 1
        assert "ERRO" in capsys.readouterr().out

    def test_documentos_respeita_o_recorte(self, importado, capsys):
        codigo = main(
            [
                "fiscal",
                "documentos",
                "--empresa",
                "1",
                "--de",
                "2026-08-01",
                "--ate",
                "2026-08-31",
                "--db",
                importado,
            ]
        )

        assert codigo == 0
        assert "Nenhum documento" in capsys.readouterr().out


class TestAsDuasEscrituracoes:
    @pytest.mark.parametrize("tipo", ["efd_icms", "efd_contribuicoes"])
    def test_gera_os_dois_tipos(self, importado, tmp_path, tipo):
        assert _gerar(importado, tmp_path / f"{tipo}.txt", "--tipo", tipo) == 0

        with _sessao(importado) as sessao:
            assert sessao.execute(select(Escrituracao)).scalars().one().tipo == tipo

    def test_conferir_usa_o_gerador_do_tipo_arquivado(self, importado, tmp_path):
        """Conferir com o gerador errado acusaria divergência inexistente."""
        _gerar(importado, tmp_path / "c.txt", "--tipo", "efd_contribuicoes")

        assert main(["fiscal", "conferir", "--escrituracao", "1", "--db", importado]) == 0


# ── Classificar e corrigir: o meio do fluxo ────────────────────────────────


@pytest.fixture
def com_regra(importado):
    """Uma regra que casa com todos os itens do fixture (NCM 2203…)."""
    with _sessao(importado) as sessao:
        criar_regra(
            sessao,
            nome="Bebida vira 2102",
            condicoes=[{"campo": "ncm", "operador": "comeca_com", "valor": "2203"}],
            acoes=[{"campo": "cfop", "valor": "2102"}],
            escritorio_id=1,
            prioridade=10,
        )
        sessao.commit()
    return importado


def _ajustes(url) -> list[AjusteFiscal]:
    with _sessao(url) as sessao:
        return sessao.execute(select(AjusteFiscal)).scalars().all()


class TestClassificarNaoGravaSozinho:
    """A regra propõe; ninguém aplica em silêncio."""

    def test_sem_aplicar_nao_grava(self, com_regra, capsys):
        codigo = main(["fiscal", "classificar", "--empresa", "1", "--db", com_regra])

        saida = capsys.readouterr().out
        assert codigo == 0
        assert "sugestões" in saida
        assert _ajustes(com_regra) == [], "gravou sem --aplicar"

    def test_diz_que_nao_gravou(self, com_regra, capsys):
        """Silêncio faria parecer que a classificação foi aplicada."""
        main(["fiscal", "classificar", "--empresa", "1", "--db", com_regra])

        assert "nada foi gravado" in capsys.readouterr().out

    def test_com_aplicar_grava(self, com_regra, capsys):
        codigo = main(["fiscal", "classificar", "--empresa", "1", "--aplicar", "--db", com_regra])

        assert codigo == 0
        assert len(_ajustes(com_regra)) == 3, "um item por documento no fixture"
        assert "gravados no lote" in capsys.readouterr().out

    def test_mostra_o_que_a_regra_propoe(self, com_regra, capsys):
        main(["fiscal", "classificar", "--empresa", "1", "--db", com_regra])

        saida = capsys.readouterr().out
        assert "cfop" in saida
        assert "'6102' → '2102'" in saida
        assert "Bebida vira 2102" in saida, "sem a regra não dá para revisar"

    def test_sem_regra_nenhuma_nao_inventa_sugestao(self, importado, capsys):
        codigo = main(["fiscal", "classificar", "--empresa", "1", "--db", importado])

        assert codigo == 0
        assert "0 sugestões" in capsys.readouterr().out
        assert _ajustes(importado) == []

    def test_reclassificar_depois_de_aplicar_nao_repropoe(self, com_regra, capsys):
        """O motor lê o efetivo; repropor faria o lote crescer sem fim."""
        main(["fiscal", "classificar", "--empresa", "1", "--aplicar", "--db", com_regra])
        capsys.readouterr()

        main(["fiscal", "classificar", "--empresa", "1", "--db", com_regra])

        assert "0 sugestões" in capsys.readouterr().out

    def test_o_lote_sai_na_tela_para_poder_desfazer(self, com_regra, capsys):
        main(["fiscal", "classificar", "--empresa", "1", "--aplicar", "--db", com_regra])

        saida = capsys.readouterr().out
        lote = _ajustes(com_regra)[0].lote

        assert lote in saida
        assert "fiscal desfazer --lote" in saida

    def test_nao_alcanca_documento_de_outra_empresa(self, com_regra, capsys):
        """O escritório tem vários clientes; classificar sem escopo mexeria
        na escrituração de quem não foi pedido."""
        with _sessao(com_regra) as sessao:
            outra = Empresa(
                cnpj="11111111000111",
                nome="OUTRA EMPRESA LTDA",
                uf="TO",
                ie="111",
                cod_mun="1721000",
                ind_perfil="A",
                ind_ativ="1",
                ind_ativ_contribuicoes="2",
                cod_inc_trib="1",
                escritorio_id=1,
            )
            sessao.add(outra)
            sessao.commit()
            # As notas já importadas passam a ser da outra empresa.
            for documento in sessao.execute(select(DocumentoFiscal)).scalars():
                documento.empresa_id = outra.id
            sessao.commit()

        codigo = main(["fiscal", "classificar", "--empresa", "1", "--aplicar", "--db", com_regra])

        assert codigo == 0
        assert "Nenhum documento no recorte" in capsys.readouterr().out
        assert _ajustes(com_regra) == [], "classificou documento de outra empresa"

    def test_conflito_aparece(self, importado, capsys):
        """O motor não resolve empate por sorteio; esconder faria parecer que sim."""
        with _sessao(importado) as sessao:
            for nome, valor in (("Regra A", "2102"), ("Regra B", "1102")):
                criar_regra(
                    sessao,
                    nome=nome,
                    condicoes=[{"campo": "ncm", "operador": "comeca_com", "valor": "2203"}],
                    acoes=[{"campo": "cfop", "valor": valor}],
                    escritorio_id=1,
                    prioridade=5,
                )
            sessao.commit()

        main(["fiscal", "classificar", "--empresa", "1", "--db", importado])

        assert "CONFLITOS" in capsys.readouterr().out


class TestAlterarSimulaPorPadrao:
    """§16: alteração em massa errada estraga o mês inteiro de uma vez."""

    def _alterar(self, url, *extras):
        return main(
            [
                "fiscal",
                "alterar",
                "--empresa",
                "1",
                "--campo",
                "cfop",
                "--valor",
                "2102",
                "--db",
                url,
                *extras,
            ]
        )

    def test_sem_confirmar_nao_grava(self, importado, capsys):
        codigo = self._alterar(importado)

        saida = capsys.readouterr().out
        assert codigo == 0
        assert "mudanças" in saida
        assert _ajustes(importado) == [], "gravou sem --confirmar"

    def test_diz_que_nao_gravou(self, importado, capsys):
        self._alterar(importado)

        assert "nada foi gravado" in capsys.readouterr().out

    def test_com_confirmar_grava(self, importado, capsys):
        codigo = self._alterar(importado, "--confirmar")

        assert codigo == 0
        assert len(_ajustes(importado)) == 3
        assert "gravado no lote" in capsys.readouterr().out

    def test_a_simulacao_conta_o_que_mudaria(self, importado, capsys):
        self._alterar(importado)

        saida = capsys.readouterr().out
        assert "3 mudanças em 2 documentos" in saida

    def test_o_filtro_recorta(self, importado, capsys):
        """Sem recorte a alteração pegaria o que não devia."""
        self._alterar(importado, "--filtro", "ncm:99999999")

        assert "0 mudanças" in capsys.readouterr().out

    def test_apenas_vazios_nao_toca_no_preenchido(self, importado, capsys):
        """§12.3: preencher o que falta sem sobrescrever o que já tem valor."""
        self._alterar(importado, "--apenas-vazios")

        assert "0 mudanças" in capsys.readouterr().out, "o CFOP já está preenchido"

    def _numerico(self, url, campo, valor, *extras):
        return main(
            [
                "fiscal",
                "alterar",
                "--empresa",
                "1",
                "--campo",
                campo,
                "--valor",
                valor,
                "--db",
                url,
                *extras,
            ]
        )

    def test_impacto_em_reais_aparece_quando_ha(self, importado, capsys):
        """Argumento de terminal é `str`; sem converter o impacto sai zerado.

        A diferença entre `0.0` e `"10"` não é numérica, e a simulação existe
        exatamente para mostrar o impacto financeiro antes de confirmar.
        """
        self._numerico(importado, "valor_desconto", "10")

        saida = capsys.readouterr().out
        assert "impacto em reais" in saida
        assert "30,00" in saida, "3 itens × 10,00"

    def test_o_valor_gravado_e_numero_e_nao_texto(self, importado):
        """`"10" > 5` não compara, e a apuração somaria concatenando."""
        self._numerico(importado, "valor_desconto", "10", "--confirmar")

        with _sessao(importado) as sessao:
            documento = sessao.execute(select(DocumentoFiscal)).scalars().first()
            efetivo = valor_efetivo(
                documento.itens[0],
                "valor_desconto",
                sessao.execute(select(AjusteFiscal)).scalars().all(),
            )

        assert efetivo == 10.0
        assert isinstance(efetivo, float)

    def test_campo_inexistente_e_recusado(self, importado, capsys):
        """Alteração em massa com nome errado não alcançaria nada, em silêncio."""
        codigo = self._numerico(importado, "cfopp", "2102")

        saida = capsys.readouterr().out
        assert codigo == 1
        assert "cfopp" in saida


class TestImpeditivoPrecisaDeForcar:
    """§16: a decisão de passar por cima tem de ser tomada de propósito.

    O documento do fixture é de ENTRADA e chega com o 6102 do fornecedor;
    trocá-lo por 5102 — outro CFOP de saída — é uma mudança real e
    incompatível, e o validador do Fisco rejeitaria o arquivo.
    """

    def _cfop_de_saida(self, url, *extras):
        return main(
            [
                "fiscal",
                "alterar",
                "--empresa",
                "1",
                "--campo",
                "cfop",
                "--valor",
                "5102",
                "--db",
                url,
                *extras,
            ]
        )

    def test_a_simulacao_marca_como_impeditivo(self, importado, capsys):
        self._cfop_de_saida(importado)

        saida = capsys.readouterr().out
        assert "IMPEDITIVO" in saida
        assert "de saída" in saida and "entrada" in saida

    def test_confirmar_sozinho_nao_grava_o_impeditivo(self, importado, capsys):
        """Sem isto, `--confirmar` passaria por cima da proteção calado."""
        codigo = self._cfop_de_saida(importado, "--confirmar")

        saida = capsys.readouterr().out
        assert codigo == 1
        assert _ajustes(importado) == [], "gravou apesar do aviso impeditivo"
        assert "impeditiv" in saida.lower()

    def test_com_forcar_grava(self, importado):
        """A saída existe; só não pode ser o caminho de menor resistência."""
        codigo = self._cfop_de_saida(importado, "--confirmar", "--forcar")

        assert codigo == 0
        assert len(_ajustes(importado)) == 3

    def test_o_motivo_fica_registrado(self, importado):
        """Passar por cima da proteção sem deixar dito por quê seria pior."""
        self._cfop_de_saida(
            importado, "--confirmar", "--forcar", "--motivo", "orientação do cliente"
        )

        motivos = [a.motivo or "" for a in _ajustes(importado)]
        assert all("orientação do cliente" in m for m in motivos), motivos


class TestFiltro:
    @pytest.mark.parametrize(
        ("bruto", "campo", "operador", "valor"),
        [
            ("ncm:2203", "ncm", "igual", "2203"),
            ("ncm:comeca_com:2203", "ncm", "comeca_com", "2203"),
            ("cfop:vazio", "cfop", "vazio", None),
            ("cfop:preenchido", "cfop", "preenchido", None),
        ],
    )
    def test_as_tres_formas(self, bruto, campo, operador, valor):
        filtro = cli_fiscal._filtro(bruto)

        assert (filtro.campo, filtro.operador, filtro.valor) == (campo, operador, valor)

    def test_forma_sem_sentido_e_recusada_com_a_lista(self):
        with pytest.raises(ValueError, match="comeca_com"):
            cli_fiscal._filtro("sozinho")

    def test_operador_inexistente_e_recusado(self, importado, capsys):
        codigo = main(
            [
                "fiscal",
                "alterar",
                "--empresa",
                "1",
                "--campo",
                "cfop",
                "--valor",
                "2102",
                "--filtro",
                "ncm:parecido_com:2203",
                "--db",
                importado,
            ]
        )

        assert codigo == 1
        assert "ERRO" in capsys.readouterr().out

    def test_valor_com_dois_pontos_nao_e_o_caso_deste_dominio(self):
        """NCM, CFOP, CST e CNPJ não têm dois-pontos; o `=` teria colidido
        com descrição de produto."""
        assert cli_fiscal._filtro("emitente_cnpj:12345678000195").valor == "12345678000195"


class TestDesfazer:
    def test_desfazer_devolve_ao_que_era(self, com_regra, capsys):
        main(["fiscal", "classificar", "--empresa", "1", "--aplicar", "--db", com_regra])
        lote = _ajustes(com_regra)[0].lote
        capsys.readouterr()

        codigo = main(["fiscal", "desfazer", "--lote", lote, "--db", com_regra])

        assert codigo == 0
        assert _ajustes(com_regra) == []
        assert "3 ajustes desfeitos" in capsys.readouterr().out

    def test_lote_inexistente_nao_e_erro(self, importado, capsys):
        """Rodar duas vezes o mesmo desfazer não deve quebrar um script."""
        codigo = main(["fiscal", "desfazer", "--lote", "naoexiste", "--db", importado])

        assert codigo == 0
        assert "Nenhum ajuste" in capsys.readouterr().out

    def test_lote_vazio_e_recusado(self, importado, capsys):
        """Lote vazio apagaria os ajustes avulsos — é o que a §16 impede."""
        codigo = main(["fiscal", "desfazer", "--lote", "", "--db", importado])

        assert codigo == 1
        assert "lote" in capsys.readouterr().out

    def test_desfazer_so_o_lote_pedido(self, com_regra, capsys):
        """Desfazer demais seria pior que não desfazer."""
        main(["fiscal", "classificar", "--empresa", "1", "--aplicar", "--db", com_regra])
        lote_regra = _ajustes(com_regra)[0].lote
        with _sessao(com_regra) as sessao:
            documento = sessao.execute(select(DocumentoFiscal)).scalars().first()
            aplicar_ajuste(
                sessao,
                documento=documento,
                item=documento.itens[0],
                campo="valor_desconto",
                valor_novo="5.00",
                origem=ORIGEM_USUARIO,
                lote="outro-lote",
            )
            sessao.commit()
        capsys.readouterr()

        main(["fiscal", "desfazer", "--lote", lote_regra, "--db", com_regra])

        restantes = _ajustes(com_regra)
        assert len(restantes) == 1
        assert restantes[0].lote == "outro-lote"


class TestArgumentosDoMeioDoFluxo:
    @pytest.mark.parametrize(
        ("argv", "faltando"),
        [
            (["fiscal", "classificar"], "--empresa"),
            (["fiscal", "alterar", "--empresa", "1"], "--campo"),
            (["fiscal", "alterar", "--empresa", "1", "--campo", "cfop"], "--valor"),
            (["fiscal", "desfazer"], "--lote"),
        ],
    )
    def test_falta_de_argumento_e_recusada(self, banco, capsys, argv, faltando):
        codigo = main([*argv, "--db", banco])

        saida = capsys.readouterr().out
        assert codigo == 1
        assert faltando in saida
