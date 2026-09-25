"""O arquivo SPED sai em ISO-8859-1, e texto do documento não quebra a linha.

Dois defeitos no mesmo lugar — o texto que vem da nota e vai para o campo:

  * **`|` e quebra de linha dentro de um campo** partiam o registro. A
    descrição "PARAFUSO 1/4 | ACO" com um CR/LF no meio fazia o C170 sair em
    duas linhas físicas, com campos a mais na primeira e a menos na segunda;
    o `9999` contava registros e não linhas, e o validador recusava o arquivo
    inteiro. O Guia Prático da EFD ICMS/IPI 3.2.2 (Seção 3) diz que campo
    alfanumérico não leva "|" nem os não-imprimíveis (00 a 31), nem espaço no
    início ou no fim;
  * **o arquivo era gravado em UTF-8**, e o leiaute pede "ASCII - ISO 8859-1
    (Latin-1)" (Guia da EFD-Contribuições 1.35, 2.1). "INDÚSTRIA" chegava ao
    validador como "INDÃšSTRIA". E caractere fora do Latin-1 — travessão, aspa
    curva, emoji — não pode ser gravado nele: é transliterado, sempre do mesmo
    jeito, em vez de derrubar a geração.
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select

from src.cli import main
from src.db.models import Empresa, Escritorio, Escrituracao, criar_engine, get_session, init_db
from src.escrituracoes import base
from src.escrituracoes.leiaute import EFD_CONTRIBUICOES, EFD_ICMS
from tests.fixtures_nfe import nfe_xml

CNPJ = "98765432000198"


def _xml_com_texto_hostil() -> bytes:
    """Uma entrada (C170 presente) com o texto que as notas trazem de verdade."""
    return (
        nfe_xml()
        .decode()
        .replace("PRODUTO DE TESTE 1", "PARAFUSO 1/4 | ACO&#13;&#10;INOX")
        .replace("INDUSTRIA EXEMPLO LTDA", "INDÚSTRIA “AÇO” — FILIAL ☕ LTDA")
        .encode()
    )


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
                nome="COMÉRCIO EXEMPLO LTDA",
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
    xml = tmp_path / "nfe.xml"
    xml.write_bytes(_xml_com_texto_hostil())
    assert main(["fiscal", "importar", str(xml), "--escritorio", "1", "--db", url]) == 0
    return url


def _gerar(banco, destino, tipo="efd_icms") -> bytes:
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
                "--tipo",
                tipo,
                "--saida",
                str(destino),
                "--db",
                banco,
            ]
        )
        == 0
    )
    return destino.read_bytes()


@pytest.mark.parametrize(
    ("tipo", "leiaute"),
    [("efd_icms", EFD_ICMS), ("efd_contribuicoes", EFD_CONTRIBUICOES)],
    ids=["efd_icms", "efd_contribuicoes"],
)
class TestUmRegistroPorLinha:
    def test_toda_linha_fisica_tem_os_campos_do_leiaute(self, banco, tmp_path, tipo, leiaute):
        bruto = _gerar(banco, tmp_path / "sped.txt", tipo)
        linhas = bruto.decode("latin-1").split("\r\n")[:-1]

        for linha in linhas:
            assert linha.startswith("|") and linha.endswith("|"), (
                f"linha partida no meio de um campo: {linha[:60]!r} — o validador recusa "
                "o arquivo inteiro sem dizer qual registro"
            )
            tipo_registro, *campos = linha.split("|")[1:-1]
            assert len(campos) == len(leiaute[tipo_registro]), (
                f"{tipo_registro} com {len(campos)} campos: um '|' dentro do texto "
                "deslocou todos os campos seguintes"
            )

    def test_o_9999_conta_as_linhas_que_existem(self, banco, tmp_path, tipo, leiaute):
        linhas = _gerar(banco, tmp_path / "sped.txt", tipo).split(b"\r\n")[:-1]
        declarado = int(linhas[-1].split(b"|")[2])
        assert declarado == len(linhas), (
            f"o 9999 diz {declarado} linhas e o arquivo tem {len(linhas)}: a quebra de "
            "linha dentro da descrição criou linhas que o bloco 9 não contou"
        )
        assert b"\r\r" not in b"\r\n".join(linhas) and b"\n\n" not in b"\r\n".join(linhas)


class TestLatin1:
    def test_acento_sai_em_um_byte(self, banco, tmp_path):
        bruto = _gerar(banco, tmp_path / "efd.txt")

        assert (
            "COMÉRCIO EXEMPLO LTDA".encode("latin-1") in bruto
        ), "o nome da empresa não está em ISO-8859-1: o validador lê 'COMÃ‰RCIO'"
        with pytest.raises(UnicodeDecodeError):
            bruto.decode("utf-8")  # se decodificasse, o arquivo ainda seria UTF-8

    def test_fora_do_latin1_e_transliterado_do_mesmo_jeito_sempre(self, banco, tmp_path):
        bruto = _gerar(banco, tmp_path / "efd.txt")
        (participante,) = [
            ln for ln in bruto.decode("latin-1").split("\r\n") if ln.startswith("|0150|")
        ]

        assert participante.split("|")[3] == 'INDÚSTRIA "AÇO" - FILIAL ? LTDA'

    def test_descricao_com_pipe_e_quebra_de_linha(self, banco, tmp_path):
        bruto = _gerar(banco, tmp_path / "efd.txt")
        (item,) = [ln for ln in bruto.decode("latin-1").split("\r\n") if ln.startswith("|C170|")]

        descricao = item.split("|")[2:-1][EFD_ICMS["C170"].index("DESCR_COMPL")]
        assert descricao == "PARAFUSO 1/4 ACO INOX"

    def test_o_arquivado_e_o_hash_sao_os_do_arquivo_em_disco(self, banco, tmp_path):
        """A terceira camada responde "o que você enviou" — byte a byte."""
        bruto = _gerar(banco, tmp_path / "efd.txt")

        with get_session(criar_engine(url=banco)) as sessao:
            escrituracao = sessao.execute(select(Escrituracao)).scalars().one()

        assert escrituracao.conteudo == bruto.decode("latin-1")
        assert escrituracao.hash_conteudo == hashlib.sha256(bruto).hexdigest(), (
            "o hash guardado não é o do arquivo gravado: conferir a entrega contra ele "
            "acusaria diferença num arquivo intocado"
        )

    def test_conferir_logo_depois_de_gerar_nao_diverge(self, banco, tmp_path):
        _gerar(banco, tmp_path / "efd.txt")
        assert main(["fiscal", "conferir", "--escrituracao", "1", "--db", banco]) == 0

    def test_o_espelho_continua_em_utf8(self, banco, tmp_path):
        """O espelho é prosa para gente, não arquivo para o validador."""
        destino = tmp_path / "espelho.txt"
        main(
            [
                "fiscal",
                "espelho",
                "--empresa",
                "1",
                "--de",
                "2026-07-01",
                "--ate",
                "2026-07-31",
                "--saida",
                str(destino),
                "--db",
                banco,
            ]
        )
        assert "ESPELHO — " in destino.read_text(encoding="utf-8")


class TestTexto:
    @pytest.mark.parametrize(
        ("bruto", "esperado"),
        [
            ("A|B", "A B"),
            ("A | B", "A B"),
            ("linha 1\r\nlinha 2", "linha 1 linha 2"),
            ("\ttab\t", "tab"),
            ("  sobra  ", "sobra"),
            ("AÇÚCAR", "AÇÚCAR"),
            ("A–B—C", "A-B-C"),
            ("“aspas” ‘simples’", "\"aspas\" 'simples'"),
            ("fim…", "fim..."),
            ("xícara ☕️", "xícara ?"),
            ("ŐDŹ ÓTIMO", "ODZ ÓTIMO"),
        ],
    )
    def test_campo_de_texto(self, bruto, esperado):
        assert base.texto(bruto) == esperado
        base.texto(bruto).encode("latin-1")  # não levanta

    def test_transliteracao_e_deterministica(self):
        assert base.para_latin1("— “x” ☕") == base.para_latin1("— “x” ☕") == '- "x" ?'
