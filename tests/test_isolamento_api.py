"""Isolamento entre escritórios na API externa (REST v1 e GraphQL v2).

A auditoria de segurança reproduziu, com a chave de um integrador do
escritório A (chave com `escritorio_id`), antes do conserto:

    GET  /api/v1/empresas/<id do B>       200 — cadastro e ECDs do B
    POST /api/v2/graphql { empresas }     as empresas dos dois escritórios
    POST /api/v2/graphql { ecd(id: B) }   a ECD do B; kpis/balanço idem
    GET  /api/v1/webhooks                 os webhooks de todos os escritórios
    PUT  /api/v1/webhooks/<do B>          200 — URL trocada para a do atacante
    DELETE /api/v1/webhooks/<do B>        200 — webhook do B removido
    GET  /api/v1/audit/logs               a trilha de auditoria da instância
    POST /api/v1/audit/limpar?dias=1      200 — trilha apagada

O REST já escopava a listagem e as rotas `/ecds/{id}`
(`tests/test_escopo_de_api_key.py`); o detalhe de empresa e o GraphQL inteiro
tinham ficado de fora, e webhooks e auditoria não têm dono por escritório.
Tudo aqui passa pela aplicação montada (`src.dashboard.app:app`), com a chave
no cabeçalho — é por onde o integrador chega.
"""

from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from src.api import ApiKeyService
from src.audit import init_audit_service
from src.auth import init_auth
from src.db.models import (
    ECD,
    ApiKey,
    AuditLog,
    Empresa,
    Escritorio,
    Usuario,
    WebhookRegistration,
    criar_engine,
    get_session,
    init_db,
)
from src.ratelimit import get_ip_limiter, init_limiter
from src.settings import reset_settings_cache
from src.webhooks import WebhookService


@pytest.fixture
def cenario(tmp_path, monkeypatch):
    """Escritórios A e B com empresa e ECD cada; chave de A, de B e de instância."""
    referencia = f"sqlite:///{tmp_path / 'isolamento.db'}"
    monkeypatch.setenv("DATABASE_URL", referencia)
    monkeypatch.delenv("SPED_HUB_DB", raising=False)
    reset_settings_cache()
    get_ip_limiter().reset()

    engine = criar_engine(url=referencia)
    init_db(engine)
    ids: dict = {"referencia": referencia}
    try:
        with get_session(engine) as sessao:
            a = Escritorio(nome="Escritório A", slug="a")
            b = Escritorio(nome="Escritório B", slug="b")
            sessao.add_all([a, b])
            sessao.flush()
            ids["escritorio_a"], ids["escritorio_b"] = a.id, b.id
            for rotulo, escritorio, cnpj in (
                ("a", a, "11111111000111"),
                ("b", b, "22222222000122"),
            ):
                empresa = Empresa(
                    escritorio_id=escritorio.id, cnpj=cnpj, nome=f"Cliente do {rotulo.upper()}"
                )
                sessao.add(empresa)
                sessao.flush()
                ecd = ECD(
                    empresa_id=empresa.id,
                    leiaute="9",
                    dt_ini=datetime.date(2024, 1, 1),
                    dt_fin=datetime.date(2024, 12, 31),
                    importado_em=datetime.datetime.now(datetime.UTC),
                    nome_arquivo=f"ecd_{rotulo}.txt",
                )
                sessao.add(ecd)
                sessao.flush()
                ids[f"empresa_{rotulo}"], ids[f"ecd_{rotulo}"] = empresa.id, ecd.id
            senha_hash, salt = Usuario.hash_senha("senha-admin")
            sessao.add(
                Usuario(
                    email="admin@escritorio.local",
                    nome="Admin",
                    senha_hash=senha_hash,
                    salt=salt,
                    admin=True,
                )
            )
            sessao.commit()
    finally:
        engine.dispose()

    # Antes de criar as chaves: `ApiKeyService.criar` registra `apikey.create`
    # no serviço de auditoria global, e é essa a trilha que os testes de
    # auditoria tentam ler e apagar.
    init_audit_service(referencia)
    servico = ApiKeyService(referencia)
    ids["chave_a"] = servico.criar("Integrador do A", escritorio_id=ids["escritorio_a"])["chave"]
    ids["chave_b"] = servico.criar("Integrador do B", escritorio_id=ids["escritorio_b"])["chave"]
    ids["chave_global"] = servico.criar("Chave de instância")["chave"]

    init_auth(referencia)
    init_limiter(referencia)
    from src.dashboard.app import app

    ids["cliente"] = TestClient(app)
    yield ids
    get_ip_limiter().reset()
    reset_settings_cache()


def _h(chave: str) -> dict:
    return {"X-API-Key": chave}


def _graphql(cenario, chave: str, query: str) -> dict:
    resposta = cenario["cliente"].post("/api/v2/graphql", json={"query": query}, headers=_h(chave))
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


# ═══════════════════════════════════════════════════════════════════════════
# 1. GraphQL aplica o mesmo escopo do REST
# ═══════════════════════════════════════════════════════════════════════════


class TestGraphQLEscopado:
    def test_listagem_de_empresas_so_mostra_o_proprio_escritorio(self, cenario):
        corpo = _graphql(
            cenario, cenario["chave_a"], "{ empresas { dados { nome cnpj } pageInfo { total } } }"
        )

        nomes = [e["nome"] for e in corpo["data"]["empresas"]["dados"]]
        assert nomes == ["Cliente do A"], (
            f"a chave do escritório A listou {nomes} pelo GraphQL — o cadastro dos "
            "clientes do escritório B vaza para o integrador do A"
        )
        assert (
            corpo["data"]["empresas"]["pageInfo"]["total"] == 1
        ), "o total sem escopo revela quantas empresas o vizinho tem"

    def test_listagem_de_ecds_so_mostra_o_proprio_escritorio(self, cenario):
        corpo = _graphql(
            cenario,
            cenario["chave_a"],
            "{ ecds { dados { empresaNome nomeArquivo } pageInfo { total } } }",
        )

        arquivos = [e["nomeArquivo"] for e in corpo["data"]["ecds"]["dados"]]
        assert arquivos == ["ecd_a.txt"], f"a chave do A listou as ECDs {arquivos}"
        assert corpo["data"]["ecds"]["pageInfo"]["total"] == 1

    def test_filtro_por_empresa_alheia_devolve_vazio(self, cenario):
        corpo = _graphql(
            cenario,
            cenario["chave_a"],
            f"{{ ecds(empresaId: {cenario['empresa_b']}) {{ dados {{ id }} pageInfo {{ total }} }} }}",
        )

        assert corpo["data"]["ecds"]["dados"] == []
        assert corpo["data"]["ecds"]["pageInfo"]["total"] == 0

    def test_empresa_e_ecd_do_outro_escritorio_por_id_sao_nulas(self, cenario):
        corpo = _graphql(
            cenario,
            cenario["chave_a"],
            f"{{ empresa(id: {cenario['empresa_b']}) {{ nome }} "
            f"ecd(id: {cenario['ecd_b']}) {{ empresaNome }} }}",
        )

        assert (
            corpo["data"]["empresa"] is None
        ), f"empresa do B entregue à chave do A: {corpo['data']['empresa']}"
        assert (
            corpo["data"]["ecd"] is None
        ), f"ECD do B entregue à chave do A: {corpo['data']['ecd']}"

    def test_resposta_para_id_alheio_e_igual_a_de_id_inexistente(self, cenario):
        """Responder diferente confirmaria que a ECD do vizinho existe."""
        consulta = "{ ecd(id: %d) { id } kpis(ecdId: %d) { empresa } }"
        alheia = _graphql(
            cenario, cenario["chave_a"], consulta % (cenario["ecd_b"], cenario["ecd_b"])
        )
        inexistente = _graphql(cenario, cenario["chave_a"], consulta % (999999, 999999))

        assert alheia["data"] == inexistente["data"]
        assert [e["message"] for e in alheia["errors"]] == [
            e["message"] for e in inexistente["errors"]
        ]

    @pytest.mark.parametrize(
        "campo,selecao",
        [
            ("balanco", "titulo totalAtivo"),
            ("dre", "titulo resultadoLiquido"),
            ("dfc", "titulo variacaoCaixa"),
            ("diario", "titulo totalLancamentos"),
            ("kpis", "empresa cnpj ativoTotal"),
            ("notas", "titulo texto"),
            ("validar", "status totalInconsistencias"),
            ("evolucaoMulti", "numPeriodos"),
        ],
    )
    def test_relatorio_da_ecd_alheia_e_recusado(self, cenario, campo, selecao):
        """Escopar só a listagem seria cosmético: basta pedir o id direto."""
        corpo = _graphql(
            cenario,
            cenario["chave_a"],
            f"{{ {campo}(ecdId: {cenario['ecd_b']}) {{ {selecao} }} }}",
        )

        assert (
            not corpo.get("data") or corpo["data"].get(campo) is None
        ), f"{campo}(ecdId: <do B>) entregou à chave do A: {corpo.get('data')}"
        assert [e["message"] for e in corpo.get("errors", [])] == ["ECD não encontrada"]
        assert "Cliente do B" not in str(corpo) and "22222222000122" not in str(corpo)

    @pytest.mark.parametrize(
        "campo,selecao",
        [("kpis", "empresa cnpj"), ("balanco", "titulo"), ("validar", "status")],
    )
    def test_a_propria_ecd_continua_acessivel(self, cenario, campo, selecao):
        """A proteção não pode fechar o caminho legítimo."""
        corpo = _graphql(
            cenario,
            cenario["chave_a"],
            f"{{ {campo}(ecdId: {cenario['ecd_a']}) {{ {selecao} }} }}",
        )

        assert "errors" not in corpo, corpo.get("errors")
        assert corpo["data"][campo] is not None

    def test_chave_de_instancia_continua_vendo_tudo(self, cenario):
        """Chave sem escritório é o comportamento histórico — invalidá-la quebraria produção."""
        corpo = _graphql(
            cenario,
            cenario["chave_global"],
            f"{{ empresas {{ dados {{ nome }} }} ecd(id: {cenario['ecd_b']}) {{ empresaNome }} }}",
        )

        assert sorted(e["nome"] for e in corpo["data"]["empresas"]["dados"]) == [
            "Cliente do A",
            "Cliente do B",
        ]
        assert corpo["data"]["ecd"]["empresaNome"] == "Cliente do B"

    def test_limite_tem_teto(self, cenario):
        """Sem teto, `limite: 1000000` pedia a base inteira numa requisição só."""
        corpo = _graphql(
            cenario,
            cenario["chave_global"],
            "{ empresas(limite: 1000000, pagina: -3) { pageInfo { limite pagina } } "
            "ecds(limite: 1000000) { pageInfo { limite } } }",
        )

        assert corpo["data"]["empresas"]["pageInfo"] == {"limite": 100, "pagina": 1}
        assert corpo["data"]["ecds"]["pageInfo"]["limite"] == 100

    def test_requisicao_conta_uma_vez_no_uso_da_chave(self, cenario):
        """A credencial é resolvida uma vez por requisição, não duas.

        O router exige a chave e o contexto também a pede; se o FastAPI não
        reaproveitasse o resultado, cada consulta GraphQL gastaria dois da
        cota de rate limit da chave.
        """
        _graphql(cenario, cenario["chave_a"], "{ health }")
        _graphql(cenario, cenario["chave_a"], "{ health }")

        engine = criar_engine(url=cenario["referencia"])
        try:
            with get_session(engine) as sessao:
                total = sessao.execute(
                    select(ApiKey.total_requisicoes).where(ApiKey.nome == "Integrador do A")
                ).scalar_one()
        finally:
            engine.dispose()
        assert total == 2

    def test_schema_sem_credencial_no_contexto_recusa(self, cenario):
        """Fora do router, sem credencial, o schema falha fechado em vez de ler tudo."""
        from src.api.graphql import schema

        resultado = schema.execute_sync("{ empresas { dados { nome } } }")

        assert resultado.data is None
        assert resultado.errors, "schema sem credencial devolveu dados sem escopo"


# ═══════════════════════════════════════════════════════════════════════════
# 2. REST: detalhe de empresa escopado
# ═══════════════════════════════════════════════════════════════════════════


class TestDetalheDeEmpresaEscopado:
    def test_chave_do_a_nao_le_empresa_do_b(self, cenario):
        resposta = cenario["cliente"].get(
            f"/api/v1/empresas/{cenario['empresa_b']}", headers=_h(cenario["chave_a"])
        )

        assert resposta.status_code == 404, (
            f"GET /empresas/<do B> com a chave do A respondeu {resposta.status_code}: "
            f"{resposta.text[:200]} — cadastro e lista de ECDs do vizinho vazam"
        )

    def test_resposta_igual_a_de_empresa_inexistente(self, cenario):
        cliente = cenario["cliente"]
        alheia = cliente.get(
            f"/api/v1/empresas/{cenario['empresa_b']}", headers=_h(cenario["chave_a"])
        )
        inexistente = cliente.get("/api/v1/empresas/999999", headers=_h(cenario["chave_a"]))

        assert (alheia.status_code, alheia.json()) == (inexistente.status_code, inexistente.json())

    def test_a_propria_empresa_continua_acessivel(self, cenario):
        resposta = cenario["cliente"].get(
            f"/api/v1/empresas/{cenario['empresa_a']}", headers=_h(cenario["chave_a"])
        )

        assert resposta.status_code == 200
        assert resposta.json()["nome"] == "Cliente do A"
        assert [e["id"] for e in resposta.json()["ecds"]] == [cenario["ecd_a"]]

    def test_chave_de_instancia_le_qualquer_empresa(self, cenario):
        resposta = cenario["cliente"].get(
            f"/api/v1/empresas/{cenario['empresa_b']}", headers=_h(cenario["chave_global"])
        )

        assert resposta.status_code == 200
        assert resposta.json()["nome"] == "Cliente do B"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Webhooks não têm dono: gerir exige credencial de instância
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def webhook_do_b(cenario):
    """Webhook cadastrado pela instância para o ERP do escritório B."""
    registro = WebhookService(cenario["referencia"]).registrar(
        url="https://erp-do-b.example.com/hook", eventos=["ecd.importada"], secret="segredo-b"
    )
    return registro.id


def _webhook(cenario, webhook_id):
    engine = criar_engine(url=cenario["referencia"])
    try:
        with get_session(engine) as sessao:
            registro = sessao.get(WebhookRegistration, webhook_id)
            return None if registro is None else (registro.url, registro.ativo)
    finally:
        engine.dispose()


ROTAS_DE_WEBHOOK = [
    ("get", "/api/v1/webhooks", None),
    ("post", "/api/v1/webhooks", {"url": "https://a.example.com/h", "eventos": ["ecd.importada"]}),
    ("put", "/api/v1/webhooks/{id}", {"url": "https://atacante.example.net/roubo"}),
    ("delete", "/api/v1/webhooks/{id}", None),
    ("get", "/api/v1/webhooks/dashboard", None),
    ("get", "/api/v1/webhooks/deliveries", None),
    ("post", "/api/v1/webhooks/retry", None),
]


class TestWebhooksExigemCredencialDeInstancia:
    @pytest.mark.parametrize("metodo,rota,corpo", ROTAS_DE_WEBHOOK)
    def test_chave_de_escritorio_recebe_403(self, cenario, webhook_do_b, metodo, rota, corpo):
        kwargs = {"headers": _h(cenario["chave_a"])}
        if corpo is not None:
            kwargs["json"] = corpo

        resposta = getattr(cenario["cliente"], metodo)(rota.format(id=webhook_do_b), **kwargs)

        assert resposta.status_code == 403, (
            f"{metodo.upper()} {rota} aceitou a chave do escritório A "
            f"({resposta.status_code}) — o registro de webhook não tem dono, então "
            "ela alcança os webhooks de todos os escritórios"
        )

    def test_webhook_do_b_sai_intacto_das_tentativas_do_a(self, cenario, webhook_do_b):
        cliente, cabecalho = cenario["cliente"], _h(cenario["chave_a"])
        cliente.put(
            f"/api/v1/webhooks/{webhook_do_b}",
            json={"url": "https://atacante.example.net/roubo"},
            headers=cabecalho,
        )
        cliente.delete(f"/api/v1/webhooks/{webhook_do_b}", headers=cabecalho)

        assert _webhook(cenario, webhook_do_b) == ("https://erp-do-b.example.com/hook", True), (
            "a chave do A redirecionou ou apagou o webhook do B: os eventos das "
            "importações do B passariam a ir para o endereço do atacante"
        )

    def test_listagem_nao_vaza_para_chave_de_escritorio(self, cenario, webhook_do_b):
        resposta = cenario["cliente"].get("/api/v1/webhooks", headers=_h(cenario["chave_a"]))

        assert "erp-do-b" not in resposta.text

    def test_catalogo_de_eventos_segue_aberto(self, cenario):
        """Informação estática, sem dado de ninguém — não há por que fechar."""
        resposta = cenario["cliente"].get(
            "/api/v1/webhooks/eventos", headers=_h(cenario["chave_a"])
        )

        assert resposta.status_code == 200

    def test_chave_de_instancia_gerencia(self, cenario, webhook_do_b):
        cliente, cabecalho = cenario["cliente"], _h(cenario["chave_global"])

        assert cliente.get("/api/v1/webhooks", headers=cabecalho).json()["total"] == 1
        resposta = cliente.put(
            f"/api/v1/webhooks/{webhook_do_b}", json={"ativo": False}, headers=cabecalho
        )
        assert resposta.status_code == 200
        assert _webhook(cenario, webhook_do_b) == ("https://erp-do-b.example.com/hook", False)

    def test_admin_de_sessao_continua_gerenciando(self, cenario, webhook_do_b):
        """A tela /webhooks do dashboard consome estas rotas com a sessão do admin."""
        cliente = cenario["cliente"]
        cliente.post("/api/login", data={"email": "admin@escritorio.local", "senha": "senha-admin"})

        assert cliente.get("/api/v1/webhooks").status_code == 200
        assert cliente.get("/api/v1/webhooks/dashboard").status_code == 200
        assert cliente.get("/api/v1/webhooks/deliveries").status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# 4. Auditoria é da instância
# ═══════════════════════════════════════════════════════════════════════════


def _linhas_de_auditoria(cenario) -> int:
    engine = criar_engine(url=cenario["referencia"])
    try:
        with get_session(engine) as sessao:
            return sessao.execute(select(func.count(AuditLog.id))).scalar_one()
    finally:
        engine.dispose()


def _envelhecer_auditoria(cenario) -> None:
    engine = criar_engine(url=cenario["referencia"])
    try:
        with get_session(engine) as sessao:
            for registro in sessao.execute(select(AuditLog)).scalars():
                registro.criado_em = datetime.datetime(2020, 1, 1)
            sessao.commit()
    finally:
        engine.dispose()


class TestAuditoriaExigeCredencialDeInstancia:
    @pytest.mark.parametrize(
        "metodo,rota",
        [("get", "/api/v1/audit/logs"), ("get", "/api/v1/audit/stats")],
    )
    def test_chave_de_escritorio_nao_le(self, cenario, metodo, rota):
        resposta = getattr(cenario["cliente"], metodo)(rota, headers=_h(cenario["chave_a"]))

        assert resposta.status_code == 403, (
            f"{rota} entregou à chave do escritório A a auditoria da instância "
            f"({resposta.status_code}): ações e e-mails dos usuários do B"
        )
        assert "Integrador do B" not in resposta.text

    def test_chave_de_escritorio_nao_apaga_a_trilha(self, cenario):
        _envelhecer_auditoria(cenario)
        antes = _linhas_de_auditoria(cenario)
        assert antes > 0, "o cenário precisa ter trilha para o teste significar algo"

        resposta = cenario["cliente"].post(
            "/api/v1/audit/limpar?dias=1", headers=_h(cenario["chave_a"])
        )

        assert resposta.status_code == 403
        assert _linhas_de_auditoria(cenario) == antes, (
            "a chave de um escritório apagou a trilha de auditoria da instância — "
            "inclusive o registro do que ela mesma fez"
        )

    def test_chave_de_instancia_le_e_limpa(self, cenario):
        cliente, cabecalho = cenario["cliente"], _h(cenario["chave_global"])

        assert cliente.get("/api/v1/audit/logs", headers=cabecalho).status_code == 200
        assert cliente.get("/api/v1/audit/stats", headers=cabecalho).status_code == 200
        _envelhecer_auditoria(cenario)
        resposta = cliente.post("/api/v1/audit/limpar?dias=1", headers=cabecalho)
        assert resposta.status_code == 200
        assert resposta.json()["removidos"] >= 1
