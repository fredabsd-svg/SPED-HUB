"""As capacidades atravessadas pela porta de entrada real.

Este arquivo existe por causa de um diagnóstico, não de uma funcionalidade.
Ao aplicar a REGRA 7 (§7.1) sobre a tabela de fases do `docs/status.md`,
29 fases marcadas como concluídas não tinham **nenhum** teste citado que
chegasse à linha de comando ou à tela: a evidência provava o módulo, e o
caminho que o usuário percorre nunca era percorrido por teste nenhum.

A maior parte foi resolvida citando um teste de ponta que já existia em
outro arquivo. O que sobrou está aqui — uma capacidade por classe, cada uma
entrando por onde o produto é usado de verdade:

  * `main([...])` — o executável `sped-hub`, o mesmo que o `pyproject.toml`
    instala no `PATH`;
  * `TestClient(app)` — a aplicação web inteira, com rota, sessão e escopo.

Nenhum teste daqui chama serviço ou repositório direto. Quando um chamar,
ele deixou de ser deste arquivo.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.auth import init_auth
from src.cli import main
from src.db.models import (
    DocumentoFiscal,
    Empresa,
    Escritorio,
    criar_engine,
    get_session,
    init_db,
)
from tests.fixtures_nfe import nfe_xml

FIXTURE_ECD = Path(__file__).parent / "fixtures" / "ecd_sample.txt"
CNPJ = "98765432000198"


# ── Bancos e portas ────────────────────────────────────────────────────────


@pytest.fixture
def banco_fiscal(tmp_path) -> str:
    """Banco com escritório e empresa, e a URL para passar no `--db`."""
    url = f"sqlite:///{tmp_path / 'porta.db'}"
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


def _pasta_com_nota(tmp_path, **campos) -> Path:
    pasta = tmp_path / "xml"
    pasta.mkdir(exist_ok=True)
    (pasta / "nfe.xml").write_bytes(nfe_xml(**campos))
    return pasta


def _importar(banco: str, pasta: Path) -> int:
    return main(["fiscal", "importar", str(pasta), "--escritorio", "1", "--db", banco])


def _apurar(banco: str) -> int:
    return main(
        [
            "fiscal",
            "apurar",
            "--empresa",
            "1",
            "--de",
            "2026-07-01",
            "--ate",
            "2026-07-31",
            "--db",
            banco,
        ]
    )


def _esperar(consulta, prazo: float = 5.0):
    """Repete `consulta` até devolver algo, ou desiste no prazo."""
    limite = time.monotonic() + prazo
    while True:
        resultado = consulta()
        if resultado or time.monotonic() > limite:
            return resultado
        time.sleep(0.05)


def _app(caminho: str):
    """A aplicação web, iniciada como o processo real a inicia."""
    os.environ["SPED_HUB_DB"] = caminho
    from src.dashboard.app import app

    init_auth(caminho)
    return app


def _entrar(cliente: TestClient, email: str = "porta@test.local") -> None:
    resposta = cliente.post(
        "/api/register",
        data={"email": email, "nome": "Porta", "senha": "senha123"},
    )
    assert resposta.status_code == 200, resposta.text
    resposta = cliente.post("/api/login", data={"email": email, "senha": "senha123"})
    assert resposta.status_code == 200, resposta.text


# ── Fase 21 e 25 — a ECD cíclica recusada pela linha de comando ────────────


class TestHierarquiaCiclicaPelaCLI:
    """O ciclo era recusado pelo `ECDImportService`, chamado direto.

    Entre o serviço e o usuário há o `main`, que decide o código de saída —
    e código de saída é o que um script de fechamento lê. Uma recusa que
    saísse com 0 seria uma importação aceita, do ponto de vista de quem
    chama.
    """

    @staticmethod
    def _arquivo(tmp_path, linhas_i050) -> Path:
        linhas = [
            "|0000|LECD|01012024|31122024|EMPRESA CICLO LTDA|00123456000199|SP||1234567||0|0|1|0|0|E||1|0||",
            "|I001|0|",
            "|I010|G|009|",
            "|I030|TERMO DE ABERTURA|1|Diario|500|EMPRESA TESTE|31123456789|11111111000191|01012015||BELO HORIZONTE|31122023|",
            *linhas_i050,
            "|I990|99|",
            "|9001|0|",
            "|9999|10|",
        ]
        arquivo = tmp_path / "ciclo.txt"
        arquivo.write_text("\n".join(linhas) + "\n", encoding="utf-8")
        return arquivo

    @pytest.fixture
    def banco(self, tmp_path) -> str:
        caminho = str(tmp_path / "ecd.db")
        engine = criar_engine(caminho)
        init_db(engine)
        engine.dispose()
        return caminho

    def test_importar_ecd_ciclica_sai_com_erro(self, banco, tmp_path, caplog):
        arquivo = self._arquivo(tmp_path, ["|I050|01012024|01|A|3|1|1|CONTA UM|"])

        with pytest.raises(SystemExit) as saida:
            main(["importar-ecd", str(arquivo), "--db", banco])

        assert saida.value.code != 0
        assert "ciclo" in caplog.text.lower()

    def test_nada_do_arquivo_recusado_fica_no_banco(self, banco, tmp_path):
        from src.db.models import ECD

        arquivo = self._arquivo(tmp_path, ["|I050|01012024|01|A|3|1|1|CONTA UM|"])

        with pytest.raises(SystemExit):
            main(["importar-ecd", str(arquivo), "--db", banco])

        engine = criar_engine(banco)
        with get_session(engine) as sessao:
            assert sessao.execute(select(ECD)).scalars().all() == []
        engine.dispose()

    def test_hierarquia_valida_continua_entrando(self, banco, capsys):
        main(["importar-ecd", str(FIXTURE_ECD), "--db", banco])

        assert "erro" not in capsys.readouterr().out.lower()


# ── Fase 57 e 58 — os grupos da NT 2025.002 v1.50 pela linha de comando ────


class TestGruposDaReformaPelaCLI:
    """Redução, diferimento e devolução, uma por destinação.

    O teste de leitura confere o adaptador contra o XML. Só que ele lê o
    objeto que o adaptador devolve — se o valor parasse ali e nunca chegasse
    à apuração, o teste continuaria verde. Aqui o valor sai do XML, entra por
    `fiscal importar` e aparece na tela de `fiscal apurar`.
    """

    @pytest.fixture
    def com_beneficios(self, banco_fiscal, tmp_path) -> str:
        assert _importar(banco_fiscal, _pasta_com_nota(tmp_path, beneficios=True)) == 0
        return banco_fiscal

    def test_o_diferimento_por_destinacao_chega_a_apuracao(self, com_beneficios, capsys):
        capsys.readouterr()

        assert _apurar(com_beneficios) == 0

        saida = capsys.readouterr().out
        assert "FORA DO TOTAL" in saida
        # Um valor por destinação, com o valor que o XML pôs em cada uma.
        # Lidos como filhos diretos de `gIBSCBS` — o engano que a v1.50
        # desfez — os três saíam zero e a seção sumia da tela.  Iguais entre
        # si, uma troca de destinação passaria despercebida.
        for destinacao, valor in (
            ("IBS estadual", "0,35"),
            ("IBS municipal", "0,15"),
            ("CBS", "4,50"),
        ):
            linha = next(
                (ln for ln in saida.splitlines() if "iferimento" in ln and destinacao in ln),
                None,
            )
            assert linha, f"sem linha de diferimento para {destinacao}:\n{saida}"
            assert valor in linha, linha

    def test_o_credito_presumido_chega_a_apuracao(self, com_beneficios, capsys):
        capsys.readouterr()

        _apurar(com_beneficios)

        assert "crédito presumido" in capsys.readouterr().out

    def test_sem_beneficios_a_secao_nao_aparece(self, banco_fiscal, tmp_path, capsys):
        _importar(banco_fiscal, _pasta_com_nota(tmp_path))
        capsys.readouterr()

        _apurar(banco_fiscal)

        assert "FORA DO TOTAL" not in capsys.readouterr().out


# ── Fase 64 — o total do documento recomposto pela linha de comando ────────


class TestTotalDoDocumentoPelaCLI:
    """O `vNF` recomposto chega ao registro C100 que é transmitido.

    A recomposição vive em `massa.py` e é testada lá, contra o objeto que a
    função devolve. Aqui ela é conferida no único lugar que importa para o
    fisco: o `VL_DOC` do C100 do arquivo que `fiscal gerar` escreve em disco.
    Entre uma coisa e a outra existem a camada efetiva e o gerador — e o
    total original **não** é alterado, de propósito, então um teste que
    olhasse o banco veria o número antigo e concluiria o contrário.
    """

    @pytest.fixture
    def importado(self, banco_fiscal, tmp_path) -> str:
        assert _importar(banco_fiscal, _pasta_com_nota(tmp_path)) == 0
        return banco_fiscal

    @staticmethod
    def _alterar(banco: str, *extras) -> int:
        return main(
            [
                "fiscal",
                "alterar",
                "--empresa",
                "1",
                "--campo",
                "valor_desconto",
                "--valor",
                "100.00",
                "--db",
                banco,
                *extras,
            ]
        )

    @staticmethod
    def _vl_doc_do_c100(banco: str, saida: Path) -> float:
        codigo = main(
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
            ]
        )
        assert codigo == 0
        for linha in saida.read_text("utf-8").splitlines():
            campos = linha.split("|")
            if len(campos) > 12 and campos[1] == "C100":
                # O SPED escreve decimal com vírgula.
                return float(campos[12].replace(",", "."))
        raise AssertionError("nenhum C100 no arquivo gerado")

    def test_o_desconto_novo_refaz_o_vl_doc_do_c100(self, importado, tmp_path):
        antes = self._vl_doc_do_c100(importado, tmp_path / "antes.txt")

        assert self._alterar(importado, "--confirmar") == 0

        depois = self._vl_doc_do_c100(importado, tmp_path / "depois.txt")
        assert depois == pytest.approx(antes - 100.00)

    def test_o_documento_original_nao_e_tocado(self, importado, tmp_path):
        """A primeira das três camadas. Se ela mudasse, não haveria como
        provar depois o que o emitente escreveu."""
        engine = criar_engine(url=importado)
        with get_session(engine) as sessao:
            antes = float(sessao.execute(select(DocumentoFiscal)).scalars().one().valor_total)
        engine.dispose()

        self._alterar(importado, "--confirmar")

        engine = criar_engine(url=importado)
        with get_session(engine) as sessao:
            depois = float(sessao.execute(select(DocumentoFiscal)).scalars().one().valor_total)
        engine.dispose()
        assert depois == pytest.approx(antes)

    def test_a_simulacao_nao_muda_o_arquivo(self, importado, tmp_path):
        antes = self._vl_doc_do_c100(importado, tmp_path / "antes.txt")

        assert self._alterar(importado) == 0

        assert self._vl_doc_do_c100(importado, tmp_path / "depois.txt") == pytest.approx(antes)


# ── Fase 9, 10, 11 e 12 — a API pela aplicação inteira ─────────────────────


class TestApiPelaAplicacao:
    """Webhooks e GraphQL passando por rota, autenticação e escopo.

    Os testes das fases 9 a 12 chamam a função da rota direto — `asyncio.run`
    na corrotina do handler. Isso pula o roteamento, a sessão e o middleware
    multi-tenant: o handler pode estar correto e a rota não existir, ou
    existir sem exigir credencial. Foi assim que a fase fechou.
    """

    @pytest.fixture
    def caminho(self, tmp_path) -> str:
        return str(tmp_path / "api.db")

    @staticmethod
    def _chave(caminho: str) -> dict[str, str]:
        from src.api import ApiKeyService

        return {"X-API-Key": ApiKeyService(caminho).criar("Porta de entrada")["chave"]}

    def test_webhook_registrado_e_listado_pela_rota(self, caminho):
        cliente = TestClient(_app(caminho))
        chave = self._chave(caminho)

        criado = cliente.post(
            "/api/v1/webhooks",
            json={
                "url": "https://exemplo.invalid/hook",
                "eventos": ["ecd.importada"],
                "descricao": "Porta de entrada",
            },
            headers=chave,
        )
        assert criado.status_code == 200, criado.text

        listados = cliente.get("/api/v1/webhooks", headers=chave)
        assert listados.status_code == 200
        assert listados.json()["total"] >= 1

    def test_evento_invalido_e_recusado_pela_rota(self, caminho):
        cliente = TestClient(_app(caminho))

        resposta = cliente.post(
            "/api/v1/webhooks",
            json={"url": "https://exemplo.invalid/hook", "eventos": ["nao.existe"]},
            headers=self._chave(caminho),
        )

        assert resposta.status_code == 400

    def test_webhooks_exigem_credencial(self, caminho):
        """O handler recusa por conta própria — a rota é que precisa exigir."""
        cliente = TestClient(_app(caminho))

        assert cliente.get("/api/v1/webhooks").status_code == 401

    def test_o_dashboard_de_entregas_responde_pela_rota(self, caminho):
        cliente = TestClient(_app(caminho))

        resposta = cliente.get("/api/v1/webhooks/dashboard", headers=self._chave(caminho))

        assert resposta.status_code == 200, resposta.text

    def test_a_tela_de_webhooks_responde_a_quem_entrou(self, caminho):
        cliente = TestClient(_app(caminho))
        _entrar(cliente)

        resposta = cliente.get("/webhooks")

        assert resposta.status_code == 200


# ── Fase 26 — o webhook disparado por uma importação de verdade ────────────


class TestWebhookDisparadoPelaCLI:
    """O evento sai quando a ECD entra pela linha de comando.

    Emitir o evento chamando `emitir(...)` direto prova a função. O que o
    projeto já teve — webhooks registrados que nunca disparavam — não era
    defeito da função: era a importação não a chamar. Só a importação de
    verdade prova o contrário.
    """

    @pytest.fixture
    def caminho(self, tmp_path) -> str:
        caminho = str(tmp_path / "hooks.db")
        engine = criar_engine(caminho)
        init_db(engine)
        engine.dispose()
        return caminho

    def test_importar_ecd_registra_a_entrega_do_evento(self, caminho, monkeypatch):
        import src.webhooks as webhooks
        from src.webhooks import WebhookService

        # Sem isto o registro recusaria um host que não resolve, e o teste
        # falharia por causa da rede, não do produto.
        monkeypatch.setattr(webhooks, "validate_webhook_url", lambda url, resolve=False: url)
        servico = WebhookService(caminho)
        servico.registrar(url="https://exemplo.invalid/hook", eventos=["ecd.importada"])

        main(["importar-ecd", str(FIXTURE_ECD), "--db", caminho])

        # A entrega sai em segundo plano, de propósito: importação não pode
        # esperar o endpoint do cliente.  Esperar por ela com prazo é o preço
        # de provar que ela acontece.
        entregas = _esperar(lambda: servico.get_deliveries())
        assert entregas, "a importação pela CLI não disparou `ecd.importada`"
        assert entregas[0].evento == "ecd.importada"


# ── Fase 51 — o saldo credor atravessando dois fechamentos ────────────────


class TestSaldoCredorPelaCLI:
    """Gerar julho, marcar transmitida, gerar agosto — pela CLI, em ordem.

    O saldo credor é a única coisa do sistema que atravessa dois períodos.
    Provado com objetos em memória, ele passa; o que faltava provar é que
    `fiscal gerar` de agosto encontra o arquivo que `fiscal transmitida`
    marcou em julho, lê o `VL_SLD_CREDOR_TRANSPORTAR` de lá e escreve no
    `VL_SLD_CREDOR_ANT` daqui.
    """

    @staticmethod
    def _gerar(banco: str, saida: Path, de: str, ate: str) -> int:
        return main(
            ["fiscal", "gerar", "--empresa", "1", "--de", de, "--ate", ate]
            + ["--saida", str(saida), "--db", banco]
        )

    @staticmethod
    def _campo_do_e110(saida: Path, nome: str) -> str:
        """O campo pelo NOME, com a posição resolvida pelo leiaute.

        Este teste lia por índice, e lia errado: `-2` é o `DEB_ESP`, não o
        `VL_SLD_CREDOR_TRANSPORTAR`, e `4` é o `VL_TOT_AJ_DEBITOS`, não o
        `VL_SLD_CREDOR_ANT`. Comparava dois campos vazios e passava sem provar
        nada. O que escondeu isso por tanto tempo foi o zero sair como campo
        vazio; quando o Bloco E passou a escrever `0,00`, a guarda do próprio
        teste acusou.
        """
        from src.escrituracoes.leiaute import EFD_ICMS

        posicao = EFD_ICMS["E110"].index(nome) + 2
        for linha in saida.read_text("utf-8").splitlines():
            campos = linha.split("|")
            if len(campos) > posicao and campos[1] == "E110":
                return campos[posicao]
        raise AssertionError(f"nenhum E110 em {saida.name}")

    @pytest.fixture
    def com_julho_transmitido(self, banco_fiscal, tmp_path) -> tuple[str, Path]:
        # Nota de entrada: o ICMS destacado vira crédito, e o mês fecha com
        # saldo credor a transportar.
        pasta = tmp_path / "entrada"
        pasta.mkdir()
        (pasta / "nfe.xml").write_bytes(nfe_xml(tp_nf="0"))
        assert _importar(banco_fiscal, pasta) == 0

        julho = tmp_path / "julho.txt"
        assert self._gerar(banco_fiscal, julho, "2026-07-01", "2026-07-31") == 0
        assert (
            main(
                ["fiscal", "transmitida", "--escrituracao", "1", "--recibo", "R-JUL"]
                + ["--db", banco_fiscal]
            )
            == 0
        )
        return banco_fiscal, julho

    def test_o_saldo_de_julho_chega_ao_e110_de_agosto(self, com_julho_transmitido, tmp_path):
        banco, julho = com_julho_transmitido
        transportado = self._campo_do_e110(julho, "VL_SLD_CREDOR_TRANSPORTAR")
        assert transportado not in (
            "",
            "0,00",
        ), "julho fechou sem saldo credor; o teste não prova nada"

        agosto = tmp_path / "agosto.txt"
        assert self._gerar(banco, agosto, "2026-08-01", "2026-08-31") == 0

        assert self._campo_do_e110(agosto, "VL_SLD_CREDOR_ANT") == transportado


# ── Fase 53 — o CST que descarta o valor, decidido pela CLI ───────────────


class TestCstDescartaPelaCLI:
    """Trocar o CST por `fiscal alterar` muda o que `fiscal gerar` apura.

    Toda a cadeia por onde o usuário passa: o CST vem do XML, é corrigido
    por um comando, e o efeito aparece no aviso do arquivo gerado. O valor
    descartado precisa ser **dito**: descartar em silêncio é a diferença
    entre uma apuração conservadora e uma apuração errada sem ninguém saber.
    """

    @pytest.fixture
    def importado(self, banco_fiscal, tmp_path) -> str:
        assert _importar(banco_fiscal, _pasta_com_nota(tmp_path)) == 0
        return banco_fiscal

    @staticmethod
    def _gerar_contribuicoes(banco: str, saida: Path) -> int:
        return main(
            ["fiscal", "gerar", "--empresa", "1", "--de", "2026-07-01", "--ate", "2026-07-31"]
            + ["--tipo", "efd_contribuicoes", "--saida", str(saida), "--db", banco]
        )

    def test_o_cst_trocado_faz_o_valor_ser_descartado_com_aviso(self, importado, tmp_path, capsys):
        assert (
            main(
                ["fiscal", "alterar", "--empresa", "1", "--campo", "cst_pis", "--valor", "70"]
                + ["--confirmar", "--db", importado]
            )
            == 0
        )
        capsys.readouterr()

        assert self._gerar_contribuicoes(importado, tmp_path / "contrib.txt") == 0

        saida = capsys.readouterr().out
        assert "DESCARTADO" in saida
        assert "70" in saida

    def test_sem_a_troca_o_aviso_nao_aparece(self, importado, tmp_path, capsys):
        assert self._gerar_contribuicoes(importado, tmp_path / "contrib.txt") == 0

        assert "DESCARTADO" not in capsys.readouterr().out


# ── Fase 67 — as tabelas oficiais do IBS/CBS pela linha de comando ─────────


class TestTabelasOficiaisPelaCLI:
    """A tabela e a data dela alcançáveis por quem escritura.

    Uma tabela oficial que só o código consulta responde igual quando está
    atualizada e quando está velha. `sped-hub fiscal tabelas` existe para que
    a pergunta "de quando é a tabela deste sistema?" tenha resposta sem abrir
    o repositório.
    """

    @staticmethod
    def _tabelas(banco: str, *extras) -> int:
        return main(["fiscal", "tabelas", "--db", banco, *extras])

    def test_a_procedencia_sai_na_tela(self, banco_fiscal, capsys):
        assert self._tabelas(banco_fiscal) == 0

        saida = capsys.readouterr().out
        assert "IT 2025.002" in saida
        assert "publicada em 2026-06-22" in saida
        assert "dfe-portal.svrs.rs.gov.br" in saida

    def test_um_cst_consultado_diz_o_grupo_que_exige(self, banco_fiscal, capsys):
        assert self._tabelas(banco_fiscal, "--codigo", "620") == 0

        saida = capsys.readouterr().out
        assert "Tributação monofásica" in saida
        assert "gIBSCBSMono" in saida

    def test_uma_classificacao_consultada_diz_a_reducao_e_a_vigencia(self, banco_fiscal, capsys):
        assert self._tabelas(banco_fiscal, "--codigo", "200049") == 0

        saida = capsys.readouterr().out
        assert "40.0%" in saida
        assert "2026-01-01" in saida

    def test_codigo_desconhecido_sai_com_erro(self, banco_fiscal, capsys):
        """Sair com 0 faria um script de fechamento tratar como encontrado."""
        assert self._tabelas(banco_fiscal, "--codigo", "999999") == 1

        assert "não está em nenhuma das três tabelas" in capsys.readouterr().out


class TestClassificacaoConferidaPelaCLI:
    """`fiscal apurar` aponta a classificação que a SEFAZ recusaria."""

    @pytest.fixture
    def importado(self, banco_fiscal, tmp_path) -> str:
        assert _importar(banco_fiscal, _pasta_com_nota(tmp_path)) == 0
        return banco_fiscal

    def test_a_nota_bem_classificada_nao_gera_apontamento(self, importado, capsys):
        capsys.readouterr()

        _apurar(importado)

        assert "CLASSIFICAÇÃO DIVERGENTE" not in capsys.readouterr().out

    def test_cst_trocado_por_comando_aparece_na_apuracao(self, importado, capsys):
        """Toda a cadeia: o CST é corrigido por `fiscal alterar` e a
        conferência da apuração acusa que ele não casa com o `cClassTrib`."""
        assert (
            main(
                ["fiscal", "alterar", "--empresa", "1", "--campo", "cst_ibscbs"]
                + ["--valor", "620", "--confirmar", "--db", importado]
            )
            == 0
        )
        capsys.readouterr()

        _apurar(importado)

        saida = capsys.readouterr().out
        assert "CLASSIFICAÇÃO DIVERGENTE DA TABELA OFICIAL" in saida
        assert "pertence ao CST 000" in saida


# ── Fase 69 — a regra com código inventado recusada pela linha de comando ──


class TestRegraComCodigoInventadoPelaCLI:
    """`fiscal regras criar` recusa o que `fiscal alterar` já recusava.

    São dois caminhos que escrevem o mesmo campo, e o da regra é o pior dos
    dois: ela vale para todo documento que casar com ela, inclusive os que
    ainda nem foram importados, e grava com origem `regra` — a que ninguém
    revisa item a item.
    """

    @staticmethod
    def _criar(banco: str, campo: str, valor: str) -> int:
        return main(
            ["fiscal", "regras", "--acao-regra", "criar", "--nome", "teste"]
            + ["--se", "ncm:22030000", "--entao", f"{campo}:{valor}"]
            + ["--escritorio", "1", "--db", banco]
        )

    def test_class_trib_inventado_sai_com_erro(self, banco_fiscal, capsys):
        codigo = self._criar(banco_fiscal, "class_trib_ibscbs", "999999")

        assert codigo == 1
        saida = capsys.readouterr().out
        assert "não está na tabela oficial" in saida
        assert "sped-hub fiscal tabelas" in saida

    def test_cst_inventado_sai_com_erro(self, banco_fiscal, capsys):
        assert self._criar(banco_fiscal, "cst_ibscbs", "999") == 1

        assert "não está na tabela oficial" in capsys.readouterr().out

    def test_codigo_que_existe_e_aceito(self, banco_fiscal, capsys):
        assert self._criar(banco_fiscal, "class_trib_ibscbs", "620001") == 0

        assert "criada" in capsys.readouterr().out

    def test_a_regra_recusada_nao_fica_no_banco(self, banco_fiscal):
        """Sair com erro depois de gravar seria o pior dos dois mundos."""
        from src.db.models import RegraFiscal

        self._criar(banco_fiscal, "class_trib_ibscbs", "999999")

        engine = criar_engine(url=banco_fiscal)
        with get_session(engine) as sessao:
            assert sessao.execute(select(RegraFiscal)).scalars().all() == []
        engine.dispose()


# ── Fase 70 — a idade da tabela oficial pela linha de comando ──────────────


class TestIdadeDaTabelaPelaCLI:
    """Quantos dias tem a tabela deste sistema, sem abrir o repositório.

    A data sozinha exige que quem lê faça a conta e saiba a cadência de
    publicação do órgão. Nenhuma das duas coisas é razoável esperar de quem
    está fechando o mês.
    """

    def test_a_idade_sai_junto_com_a_data(self, banco_fiscal, capsys):
        assert main(["fiscal", "tabelas", "--db", banco_fiscal]) == 0

        saida = capsys.readouterr().out
        assert "publicada em 2026-06-22" in saida
        assert "dias)" in saida

    def test_a_tabela_de_hoje_nao_traz_atencao(self, banco_fiscal, capsys):
        """Alerta que sai sempre é alerta que ninguém lê."""
        main(["fiscal", "tabelas", "--db", banco_fiscal])

        assert "ATENÇÃO" not in capsys.readouterr().out


# ── Fase 71 — a versão do leiaute no arquivo que sai pela CLI ──────────────


class TestVersaoDoLeiautePelaCLI:
    """O `COD_VER` do arquivo que `fiscal gerar` escreve em disco.

    É onde o defeito importava: o número certo na função e errado no arquivo
    seria o mesmo que nada. O validador do Fisco lê o arquivo, e a recusa
    chega depois de o fechamento estar pronto.
    """

    @pytest.fixture
    def importado(self, banco_fiscal, tmp_path) -> str:
        assert _importar(banco_fiscal, _pasta_com_nota(tmp_path)) == 0
        return banco_fiscal

    @staticmethod
    def _gerar(banco: str, saida: Path, de: str, ate: str) -> int:
        return main(
            ["fiscal", "gerar", "--empresa", "1", "--de", de, "--ate", ate]
            + ["--saida", str(saida), "--db", banco]
        )

    @staticmethod
    def _cod_ver(saida: Path) -> str:
        for linha in saida.read_text("utf-8").splitlines():
            campos = linha.split("|")
            if len(campos) > 2 and campos[1] == "0000":
                return campos[2]
        raise AssertionError("nenhum 0000 no arquivo gerado")

    def test_o_arquivo_de_2026_sai_com_o_leiaute_020(self, importado, tmp_path):
        """Fixo em 018, este arquivo voltaria recusado da transmissão."""
        saida = tmp_path / "julho.txt"

        assert self._gerar(importado, saida, "2026-07-01", "2026-07-31") == 0

        assert self._cod_ver(saida) == "020"

    def test_o_aviso_dos_tributos_da_reforma_sai_na_tela(self, importado, tmp_path, capsys):
        capsys.readouterr()

        self._gerar(importado, tmp_path / "julho.txt", "2026-07-01", "2026-07-31")

        saida = capsys.readouterr().out
        assert "NÃO entram neste arquivo" in saida
        assert "CBS" in saida


# ── Fase 73 — o 0200 da EFD-Contribuições no arquivo que sai pela CLI ──────


class TestRegistro0200DaEFDContribuicoesPelaCLI:
    """O `0200` sai com onze campos no arquivo que vai para o Fisco.

    O campo a mais era invisível daqui: o gerador confere contra a nossa
    própria tabela, e a tabela é que estava errada. Contar os campos do
    arquivo é a única conferência que não usa a fonte suspeita como
    referência.
    """

    @pytest.fixture
    def importado(self, banco_fiscal, tmp_path) -> str:
        assert _importar(banco_fiscal, _pasta_com_nota(tmp_path)) == 0
        return banco_fiscal

    @staticmethod
    def _gerar(banco: str, saida: Path, tipo: str) -> int:
        return main(
            ["fiscal", "gerar", "--empresa", "1", "--de", "2026-07-01", "--ate", "2026-07-31"]
            + ["--tipo", tipo, "--saida", str(saida), "--db", banco]
        )

    @staticmethod
    def _campos_do_0200(saida: Path) -> list[str]:
        for linha in saida.read_text("utf-8").splitlines():
            campos = linha.split("|")
            if len(campos) > 1 and campos[1] == "0200":
                # A linha é |0200|c1|...|cN| — fora o vazio inicial, o tipo e
                # o vazio final.
                return campos[2:-1]
        raise AssertionError("nenhum 0200 no arquivo gerado")

    def test_o_0200_da_contribuicoes_sai_com_onze_campos(self, importado, tmp_path):
        saida = tmp_path / "contrib.txt"

        assert self._gerar(importado, saida, "efd_contribuicoes") == 0

        assert len(self._campos_do_0200(saida)) == 11

    def test_o_0200_da_icms_sai_com_doze(self, importado, tmp_path):
        """A mesma nota, a outra obrigação: são leiautes diferentes."""
        saida = tmp_path / "icms.txt"

        assert self._gerar(importado, saida, "efd_icms") == 0

        assert len(self._campos_do_0200(saida)) == 12


# ── Fase 74 — o leiaute 9 da ECD conferido pelas linhas do próprio manual ──


class TestLeiaute9DaECDPelaCLI:
    """As linhas do "Exemplo de Preenchimento" do manual, pela linha de comando.

    Nove registros do `ecd_v9.yml` não correspondiam a leiaute nenhum. O
    pior era o J100: os valores do balanço estavam quatro colunas à
    esquerda de onde estão de verdade, e a posição 4 — que o arquivo lê
    como saldo inicial — guarda o nível de aglutinação, um inteiro pequeno.

    A suíte inteira passava. Ela tinha que passar: as fixtures são escritas
    a partir do próprio yml, então conferiam a cópia contra a cópia. Foi o
    mesmo furo do `CEST` no `0200` da EFD-Contribuições, uma fase antes.

    O que quebra o círculo são as linhas que a RFB publica prontas, cada
    uma seguida da explicação campo a campo. As constantes `*_DO_MANUAL`
    são cópias literais delas — mudar uma vírgula ali é reescrever o
    documento oficial. As outras linhas do arquivo são nossas, e estão
    marcadas.
    """

    # Manual do Leiaute 9 da ECD (Anexo ao ADE Cofis nº 01/2026), "V - Exemplo
    # de Preenchimento" de cada registro.
    I030_DO_MANUAL = (
        "|I030|TERMO DE ABERTURA|1|Balancete|500|EMPRESA TESTE"
        "|31123456789|11111111000191|01012015||BELO HORIZONTE|31122023|"
    )
    I050_DO_MANUAL = "|I050|01012015|01|S|1|1.01.01.01||Ativo Sintética 1|"
    I200_DO_MANUAL = "|I200|1000|02052023|5000,00|N||"
    I250_DO_MANUAL = "|I250|1.1||5000,00|D|123||RECEBIMENTO DE CLIENTES – DUPLICATA N. 100.2011||"
    I355_DO_MANUAL = "|I355|4.1||200000,00|C|"

    @pytest.fixture
    def banco(self, tmp_path) -> str:
        caminho = str(tmp_path / "leiaute9.db")
        engine = criar_engine(caminho)
        init_db(engine)
        engine.dispose()
        return caminho

    @pytest.fixture
    def arquivo(self, tmp_path) -> Path:
        linhas = [
            "|0000|LECD|01012023|31122023|EMPRESA TESTE|11111111000191|MG||3106200"
            "||0|0|1|0||0|G||N|0||0||",
            "|I001|0|",
            "|I010|G|009|",
            self.I030_DO_MANUAL,
            self.I050_DO_MANUAL,
            "|I050|01012015|01|A|2|4.1|1.01.01.01|Receita de Vendas|",
            self.I200_DO_MANUAL,
            self.I250_DO_MANUAL,
            # O lançamento extemporâneo é o que expõe o campo 6: ele leva a
            # data dos fatos que o lançamento registra (DT_LCTO_EXT).
            "|I200|2000|02052023|1500,00|X|31122022|",
            "|I250|1.1||1500,00|D|456||AJUSTE DE EXERCICIO ANTERIOR|",
            "|I350|31122023|",
            self.I355_DO_MANUAL,
            "|I990|11|",
            "|J001|0|",
            "|J990|2|",
            "|9001|0|",
            "|9999|16|",
        ]
        caminho = tmp_path / "leiaute9.txt"
        caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
        return caminho

    @pytest.fixture
    def importado(self, banco, arquivo) -> str:
        assert main(["importar-ecd", str(arquivo), "--db", banco]) == 0
        return banco

    @staticmethod
    def _sessao(banco: str):
        return get_session(criar_engine(banco))

    def test_o_nome_da_conta_vem_do_campo_8(self, importado):
        """ "Campo 08 – CTA – Nome da Conta Analítica/Grupo de Contas"."""
        from src.db.models import PlanoConta

        with self._sessao(importado) as sessao:
            conta = sessao.execute(
                select(PlanoConta).where(PlanoConta.cod_cta == "1.01.01.01")
            ).scalar_one()

            assert conta.nome_cta == "Ativo Sintética 1"

    def test_a_data_extemporanea_nao_vira_numero_de_arquivo(self, importado):
        """O campo 6 do I200 é DT_LCTO_EXT — o I200 não tem NUM_ARQ.

        Lida como número, `31122022` entrava no banco como se fosse a
        localização do documento arquivado.
        """
        from src.db.models import Lancamento

        with self._sessao(importado) as sessao:
            extemporaneo = sessao.execute(
                select(Lancamento).where(Lancamento.num_lcto == "2000")
            ).scalar_one()

            assert extemporaneo.num_arq is None

    def test_o_numero_do_documento_da_partida_continua_vindo(self, importado):
        """Quem tem NUM_ARQ é o I250, no campo 6 — e esse segue sendo lido."""
        from src.db.models import Lancamento, Partida

        with self._sessao(importado) as sessao:
            partida = sessao.execute(
                select(Partida).join(Lancamento).where(Lancamento.num_lcto == "1000")
            ).scalar_one()

            assert partida.num_arq == 123

    def test_o_saldo_de_resultado_vem_dos_campos_4_e_5(self, importado):
        """ "Campo 04 – VL_CTA", "Campo 05 – IND_DC" — não VL_SLD_FIN/IND_DC_FIN."""
        from src.db.models import SaldoResultado

        with self._sessao(importado) as sessao:
            saldo = sessao.execute(
                select(SaldoResultado).where(SaldoResultado.cod_cta == "4.1")
            ).scalar_one()

            assert (saldo.vl_sld_fin, saldo.ind_dc_fin) == (200000.00, "C")

    def test_o_leiaute_declarado_pelo_arquivo_e_conferido(self, banco, arquivo, caplog):
        """Ler um leiaute 8 com o leiaute 9 é o mesmo defeito, de novo.

        O parser carrega sempre o `ecd_v9.yml`. Se o arquivo diz outra
        versão, os campos podem estar em outras posições e o dado errado
        entra na coluna certa — em silêncio, que é o que este aviso quebra.
        """
        antigo = arquivo.read_text(encoding="utf-8").replace("|I010|G|009|", "|I010|G|008|")
        arquivo.write_text(antigo, encoding="utf-8")
        assert "|I010|G|008|" in antigo, "o arquivo não foi adulterado"

        assert main(["importar-ecd", str(arquivo), "--db", banco]) == 0

        assert "declara leiaute 008" in caplog.text
        assert "lido com o leiaute 9" in caplog.text

    def test_o_leiaute_que_bate_nao_avisa_nada(self, banco, arquivo, caplog):
        """009 e 9 são a mesma versão — avisar aqui seria ruído."""
        assert main(["importar-ecd", str(arquivo), "--db", banco]) == 0

        assert "declara leiaute" not in caplog.text

    def test_a_procedencia_do_leiaute_esta_declarada(self, importado):
        """§8.1: quem embute tabela de terceiro diz de onde ela veio."""
        import yaml

        leiaute = yaml.safe_load((Path("src/layouts/ecd_v9.yml")).read_text(encoding="utf-8"))

        assert "01/2026" in leiaute["conferido_contra"]
        assert leiaute["conferido_em"] == "2026-08-03"


# ── Fase 75 — o E110 conferido contra o Guia Prático, pela linha de comando ──


class TestAjustesNoE110PelaCLI:
    """Onde o ajuste do período aparece no arquivo que vai para o Fisco.

    O gerador escrevia os E111 nos campos 03 e 07 do E110 — `VL_AJ_DEBITOS` e
    `VL_AJ_CREDITOS` —, que o Guia descreve como "ajustes decorrentes do
    documento fiscal". Os do período são os campos 04 e 08, e o Guia diz isso
    no cabeçalho do próprio E111: ele "discrimina os ajustes lançados nos
    campos VL_TOT_AJ_DEBITOS, VL_ESTORNOS_CRED, VL_TOT_AJ_CREDITOS,
    VL_ESTORNOS_DEB, VL_TOT_DED e DEB_ESP".

    A conta fechava — o saldo apurado soma os dois pares —, então nada na tela
    denunciava. O que denunciaria é o validador do Fisco, com o fechamento
    pronto: campos 04 e 08 vazios sendo obrigatórios, e 03 e 07 com valor sem
    um C197 que os justifique.
    """

    @staticmethod
    def _ajuste(banco: str, codigo: str, valor: str) -> int:
        return main(
            ["fiscal", "ajuste", "--empresa", "1", "--de", "2026-07-01"]
            + ["--ate", "2026-07-31", "--codigo", codigo, "--valor", valor]
            + ["--db", banco]
        )

    @staticmethod
    def _e110(saida: Path) -> dict[str, str]:
        from src.escrituracoes.leiaute import EFD_ICMS

        for linha in saida.read_text("utf-8").splitlines():
            campos = linha.split("|")
            if len(campos) > 1 and campos[1] == "E110":
                return dict(zip(EFD_ICMS["E110"], campos[2:], strict=False))
        raise AssertionError(f"nenhum E110 em {saida.name}")

    @staticmethod
    def _c190(saida: Path) -> list[dict[str, str]]:
        from src.escrituracoes.leiaute import EFD_ICMS

        return [
            dict(zip(EFD_ICMS["C190"], campos[2:], strict=False))
            for campos in (linha.split("|") for linha in saida.read_text("utf-8").splitlines())
            if len(campos) > 1 and campos[1] == "C190"
        ]

    @pytest.fixture
    def arquivo(self, banco_fiscal, tmp_path) -> Path:
        assert _importar(banco_fiscal, _pasta_com_nota(tmp_path)) == 0
        assert self._ajuste(banco_fiscal, "TO000001", "20,00") == 0
        assert self._ajuste(banco_fiscal, "TO020001", "30,00") == 0

        saida = tmp_path / "julho.txt"
        assert (
            main(
                ["fiscal", "gerar", "--empresa", "1", "--de", "2026-07-01"]
                + ["--ate", "2026-07-31", "--saida", str(saida), "--db", banco_fiscal]
            )
            == 0
        )
        return saida

    @pytest.fixture
    def gerado(self, banco_fiscal, tmp_path) -> dict[str, str]:
        assert _importar(banco_fiscal, _pasta_com_nota(tmp_path)) == 0
        assert self._ajuste(banco_fiscal, "TO000001", "20,00") == 0
        assert self._ajuste(banco_fiscal, "TO020001", "30,00") == 0

        saida = tmp_path / "julho.txt"
        assert (
            main(
                ["fiscal", "gerar", "--empresa", "1", "--de", "2026-07-01"]
                + ["--ate", "2026-07-31", "--saida", str(saida), "--db", banco_fiscal]
            )
            == 0
        )
        return self._e110(saida)

    def test_o_ajuste_a_debito_sai_no_campo_dos_ajustes_do_periodo(self, gerado):
        assert gerado["VL_TOT_AJ_DEBITOS"] == "20,00"

    def test_o_ajuste_a_credito_sai_no_campo_dos_ajustes_do_periodo(self, gerado):
        assert gerado["VL_TOT_AJ_CREDITOS"] == "30,00"

    def test_os_campos_do_documento_saem_zerados(self, gerado):
        """Sem C197/D197 no arquivo, os campos 03 e 07 valem zero."""
        assert gerado["VL_AJ_DEBITOS"] == "0,00"
        assert gerado["VL_AJ_CREDITOS"] == "0,00"

    def test_nenhum_campo_numerico_do_e110_sai_vazio(self, gerado):
        """Bloco E: obrigatório sai com valor ou com zero, nunca em branco.

        "Nos registros analíticos dos blocos 'C' e 'D' e nos registros de
        apuração (Bloco E) todos os campos numéricos devem ser preenchidos,
        com valores ou com '0' (zero)" — Guia 3.2.2, Capítulo III.
        """
        vazios = [nome for nome, valor in gerado.items() if valor == ""]

        assert vazios == []

    def test_nenhum_valor_do_c190_sai_vazio(self, arquivo):
        """O C190 é registro analítico do bloco C — a mesma regra o alcança.

        Numa nota tributada normal o ST e a redução de base valem zero, e
        saíam em branco: sete campos "O" com três deles vazios.
        """
        analiticos = self._c190(arquivo)
        assert analiticos, "nenhum C190 no arquivo gerado"

        obrigatorios = (
            "VL_OPR",
            "VL_BC_ICMS",
            "VL_ICMS",
            "VL_BC_ICMS_ST",
            "VL_ICMS_ST",
            "VL_RED_BC",
            "VL_IPI",
        )
        vazios = [(nome, c190) for c190 in analiticos for nome in obrigatorios if c190[nome] == ""]

        assert vazios == []


# ── Fase 76 — o que o arquivo referencia, pela linha de comando ───────────


class TestReferenciasDoArquivoPelaCLI:
    """O bloco 0 só cadastra o que o arquivo cita, e o C170 só sai onde cabe.

    Quatro frases do Guia Prático 3.2.2, cada uma sobre um registro:

      * Exceção 2 do `C100`: "NF-e de emissão própria: regra geral, devem ser
        apresentados somente os registros C100 e C190 [...] somente será
        admitida a informação do registro C170 quando também houver sido
        informado o registro C176, C180, C181 ou o Registro C177";
      * validação do `0200`: "somente devem ser apresentados itens
        referenciados nos demais blocos";
      * `0190`: "somente devem constar as unidades de medidas informadas em
        qualquer outro registro";
      * campo 04 do `C100`: "quando se tratar de NFC-e (modelo 65), o campo
        não deve ser preenchido", e o `0150` diz o mesmo do outro lado.

    O gerador escrevia `C170` em toda nota, e com ele um `0200` e um `0190`
    para itens que registro nenhum citava.
    """

    @staticmethod
    def _gerar(banco: str, saida: Path) -> int:
        return main(
            ["fiscal", "gerar", "--empresa", "1", "--de", "2026-07-01"]
            + ["--ate", "2026-07-31", "--saida", str(saida), "--db", banco]
        )

    @staticmethod
    def _registros(saida: Path, tipo: str) -> list[list[str]]:
        return [
            campos[2:-1]
            for campos in (linha.split("|") for linha in saida.read_text("utf-8").splitlines())
            if len(campos) > 1 and campos[1] == tipo
        ]

    def _arquivo(self, banco: str, tmp_path: Path, pasta: Path) -> Path:
        assert _importar(banco, pasta) == 0
        saida = tmp_path / "julho.txt"
        assert self._gerar(banco, saida) == 0
        return saida

    # Quem emite é a empresa do `banco_fiscal`: é isso que faz a nota ser de
    # emissão própria, e o `IND_EMIT` do C100 sair "0".
    PROPRIA = {"emitente_cnpj": CNPJ, "destinatario_cnpj": "12345678000195"}

    @pytest.fixture
    def com_saida_propria(self, banco_fiscal, tmp_path) -> Path:
        """Uma NF-e de saída: emissão própria, modelo 55."""
        return self._arquivo(banco_fiscal, tmp_path, _pasta_com_nota(tmp_path, **self.PROPRIA))

    @pytest.fixture
    def com_entrada_de_terceiros(self, banco_fiscal, tmp_path) -> Path:
        """O default da fixture: a empresa é a destinatária."""
        return self._arquivo(banco_fiscal, tmp_path, _pasta_com_nota(tmp_path))

    @pytest.fixture
    def com_nfce(self, banco_fiscal, tmp_path) -> Path:
        return self._arquivo(
            banco_fiscal, tmp_path, _pasta_com_nota(tmp_path, modelo="65", **self.PROPRIA)
        )

    def test_a_nfe_propria_sai_sem_c170(self, com_saida_propria):
        assert self._registros(com_saida_propria, "C170") == []
        assert self._registros(com_saida_propria, "C190"), "o C190 continua obrigatório"

    def test_a_entrada_de_terceiros_continua_com_c170(self, com_entrada_de_terceiros):
        """É justamente o caso que o Guia nomeia ao exigir o registro."""
        assert self._registros(com_entrada_de_terceiros, "C170")

    def test_sem_c170_o_arquivo_nao_cadastra_item_nem_unidade(self, com_saida_propria):
        assert self._registros(com_saida_propria, "0200") == []
        assert self._registros(com_saida_propria, "0190") == []

    def test_com_c170_os_cadastros_voltam(self, com_entrada_de_terceiros):
        assert self._registros(com_entrada_de_terceiros, "0200")
        assert self._registros(com_entrada_de_terceiros, "0190")

    def test_a_nfce_nao_leva_participante(self, com_nfce):
        from src.escrituracoes.leiaute import EFD_ICMS

        c100 = self._registros(com_nfce, "C100")[0]
        cod_part = c100[EFD_ICMS["C100"].index("COD_PART")]

        assert cod_part == ""
        assert self._registros(com_nfce, "0150") == []

    def test_todo_codigo_citado_existe_no_cadastro(self, com_entrada_de_terceiros):
        """A regra de referência lida do outro lado: nada citado sem cadastro."""
        from src.escrituracoes.leiaute import EFD_ICMS

        cadastrados = {r[0] for r in self._registros(com_entrada_de_terceiros, "0200")}
        citados = {
            r[EFD_ICMS["C170"].index("COD_ITEM")]
            for r in self._registros(com_entrada_de_terceiros, "C170")
        }

        assert citados and citados <= cadastrados
