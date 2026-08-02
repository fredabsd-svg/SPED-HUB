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
            "|I030|01012024|31122024|A|",
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
    def _campo_do_e110(saida: Path, posicao: int) -> str:
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
        # VL_SLD_CREDOR_TRANSPORTAR é o último campo do E110.
        transportado = self._campo_do_e110(julho, -2)
        assert transportado != "0,00", "julho fechou sem saldo credor; o teste não prova nada"

        agosto = tmp_path / "agosto.txt"
        assert self._gerar(banco, agosto, "2026-08-01", "2026-08-31") == 0

        # VL_SLD_CREDOR_ANT é o quarto campo do E110.
        assert self._campo_do_e110(agosto, 4) == transportado


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
