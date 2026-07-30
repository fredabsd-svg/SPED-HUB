"""Contabilidade de entregas de webhook (Fase 27).

Cada linha de `WebhookDelivery` é **uma tentativa**, não um evento. Até aqui
toda tentativa que falhava era marcada `retrying` e nunca mais tocada — só a
última virava `failed`. Três consequências, todas visíveis para quem usa:

1. **A taxa de sucesso do painel estava errada.** Contava linha por tentativa,
   então uma entrega que só funcionou na 3ª valia 1 sucesso em 3: o painel
   anunciava 33% para uma integração que estava entregando.
2. **Entrega interrompida por restart/deploy/crash desaparecia.** A linha
   ficava sem desfecho, e o reenvio manual — que procura `failed` — não a via.
   O assinante perdia o evento em silêncio, sem recuperação nem manual.
3. Linhas presas acumulavam parecendo em andamento para sempre.

Cada teste aqui parte do defeito, não da implementação.
"""

from __future__ import annotations

import asyncio
import datetime
import json

import pytest
import sqlalchemy

import src.webhooks as mod
from src.db.models import WebhookDelivery, criar_engine, get_session, init_db
from src.webhooks import (
    STATUS_DESFECHO,
    STATUS_TERMINAIS,
    WebhookEvent,
    WebhookService,
)


@pytest.fixture
def servico(tmp_path, monkeypatch) -> WebhookService:
    """Serviço com banco isolado e sem validação de URL (o sandbox não tem DNS)."""
    referencia = f"sqlite:///{tmp_path / 'entregas.db'}"
    engine = criar_engine(referencia)
    try:
        init_db(engine)
    finally:
        engine.dispose()
    monkeypatch.setattr(mod, "validate_webhook_url", lambda url, resolve=False: url)
    servico = WebhookService(referencia)
    servico._referencia = referencia
    return servico


def _linhas(servico: WebhookService) -> list[WebhookDelivery]:
    engine = criar_engine(servico._referencia)
    with get_session(engine) as sessao:
        return list(
            sessao.execute(
                sqlalchemy.select(WebhookDelivery).order_by(WebhookDelivery.tentativa)
            ).scalars()
        )


def _cliente_falso(codigos: list[int]):
    """`httpx.AsyncClient` que devolve `codigos` em ordem, um por tentativa."""
    estado = {"n": 0}

    class Resposta:
        def __init__(self, codigo):
            self.status_code = codigo
            self.text = ""

    class Cliente:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            i = min(estado["n"], len(codigos) - 1)
            estado["n"] += 1
            return Resposta(codigos[i])

    return Cliente


@pytest.fixture(autouse=True)
def _sem_espera(monkeypatch):
    """O backoff real levaria 6 s por entrega; o que se testa não é o sono."""

    async def imediato(_segundos):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", imediato)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Nenhuma tentativa fica sem desfecho
# ═══════════════════════════════════════════════════════════════════════════


class TestTentativaSempreTermina:
    def test_todas_as_tentativas_ficam_em_estado_terminal(self, servico, monkeypatch):
        """Era o defeito-raiz: linha presa em `retrying` para sempre."""
        monkeypatch.setattr(mod.httpx, "AsyncClient", _cliente_falso([503]))
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])

        asyncio.run(servico._enviar_com_retry(wh, WebhookEvent(tipo="ecd.importada")))

        linhas = _linhas(servico)
        assert len(linhas) == 3, "uma linha por tentativa"
        assert [linha.status for linha in linhas] == ["superseded", "superseded", "failed"]
        assert all(linha.status in STATUS_TERMINAIS for linha in linhas)

    def test_nenhuma_linha_fica_sem_concluido_em(self, servico, monkeypatch):
        """Sem `concluido_em`, o histórico do painel mostra a tentativa em andamento."""
        monkeypatch.setattr(mod.httpx, "AsyncClient", _cliente_falso([503]))
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])

        asyncio.run(servico._enviar_com_retry(wh, WebhookEvent(tipo="ecd.importada")))

        assert all(linha.concluido_em is not None for linha in _linhas(servico))

    def test_status_retrying_nao_e_mais_escrito(self, servico, monkeypatch):
        """O estado foi retirado do vocabulário; linha nele hoje é resíduo."""
        monkeypatch.setattr(mod.httpx, "AsyncClient", _cliente_falso([503, 503, 200]))
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])

        asyncio.run(servico._enviar_com_retry(wh, WebhookEvent(tipo="ecd.importada")))

        assert "retrying" not in {linha.status for linha in _linhas(servico)}

    def test_apenas_a_ultima_tentativa_vira_failed(self, servico, monkeypatch):
        """`failed` é o desfecho do evento, não o resultado de cada tentativa.

        Se toda tentativa falha virasse `failed`, o reenvio manual mandaria o
        mesmo evento três vezes.
        """
        monkeypatch.setattr(mod.httpx, "AsyncClient", _cliente_falso([503]))
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])

        asyncio.run(servico._enviar_com_retry(wh, WebhookEvent(tipo="ecd.importada")))

        assert sum(1 for linha in _linhas(servico) if linha.status == "failed") == 1

    def test_erro_de_rede_tambem_termina_a_tentativa(self, servico, monkeypatch):
        """O caminho da exceção marcava `retrying` igual ao do HTTP ruim."""

        class Explode:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                raise OSError("conexão recusada")

        monkeypatch.setattr(mod.httpx, "AsyncClient", Explode)
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])

        asyncio.run(servico._enviar_com_retry(wh, WebhookEvent(tipo="ecd.importada")))

        linhas = _linhas(servico)
        assert [linha.status for linha in linhas] == ["superseded", "superseded", "failed"]
        assert all("conexão recusada" in (linha.error_message or "") for linha in linhas)


# ═══════════════════════════════════════════════════════════════════════════
# 2. A taxa de sucesso conta desfechos, não tentativas
# ═══════════════════════════════════════════════════════════════════════════


class TestTaxaDeSucesso:
    def test_entrega_que_funcionou_na_terceira_vale_100(self, servico, monkeypatch):
        """O defeito visível no painel: 33,3% para uma integração que entregou."""
        monkeypatch.setattr(mod.httpx, "AsyncClient", _cliente_falso([503, 503, 200]))
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])

        entregue = asyncio.run(servico._enviar_com_retry(wh, WebhookEvent(tipo="ecd.importada")))

        assert entregue is True
        stats = servico.get_dashboard_stats()
        assert stats["taxa_sucesso"] == 100.0, "o evento chegou; a taxa tem de dizer isso"
        assert stats["total_deliveries"] == 3, "o histórico por tentativa continua visível"

    def test_entrega_que_falhou_de_vez_vale_zero(self, servico, monkeypatch):
        monkeypatch.setattr(mod.httpx, "AsyncClient", _cliente_falso([503]))
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])

        asyncio.run(servico._enviar_com_retry(wh, WebhookEvent(tipo="ecd.importada")))

        assert servico.get_dashboard_stats()["taxa_sucesso"] == 0.0

    def test_uma_entregue_uma_perdida_da_cinquenta(self, servico, monkeypatch):
        """Dois eventos, seis linhas: a taxa é 50%, não 1/6."""
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])

        monkeypatch.setattr(mod.httpx, "AsyncClient", _cliente_falso([503, 503, 200]))
        asyncio.run(servico._enviar_com_retry(wh, WebhookEvent(tipo="ecd.importada")))
        monkeypatch.setattr(mod.httpx, "AsyncClient", _cliente_falso([503]))
        asyncio.run(servico._enviar_com_retry(wh, WebhookEvent(tipo="ecd.importada")))

        stats = servico.get_dashboard_stats()
        assert stats["total_deliveries"] == 6
        assert stats["taxa_sucesso"] == 50.0

    def test_reenvio_bem_sucedido_conta_como_sucesso(self, servico, monkeypatch):
        """`retried` é entrega que chegou — não pode pesar como falha."""
        monkeypatch.setattr(mod.httpx, "AsyncClient", _cliente_falso([503]))
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        asyncio.run(servico._enviar_com_retry(wh, WebhookEvent(tipo="ecd.importada")))
        assert servico.get_dashboard_stats()["taxa_sucesso"] == 0.0

        monkeypatch.setattr(mod.httpx, "AsyncClient", _cliente_falso([200]))
        resultado = asyncio.run(servico.retry_failed())

        assert resultado["sucessos"] == 1
        assert servico.get_dashboard_stats()["taxa_sucesso"] == 100.0

    def test_sem_entrega_nenhuma_a_taxa_e_cem(self, servico):
        assert servico.get_dashboard_stats()["taxa_sucesso"] == 100.0

    def test_entrega_em_voo_nao_entra_na_taxa(self, servico):
        """`pending` ainda não é desfecho: incluir jogaria a taxa para baixo."""
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        engine = criar_engine(servico._referencia)
        with get_session(engine) as sessao:
            sessao.add(
                WebhookDelivery(
                    webhook_id=wh.id,
                    evento="ecd.importada",
                    status="pending",
                    request_body="{}",
                    tentativa=1,
                )
            )
            sessao.commit()

        assert servico.get_dashboard_stats()["taxa_sucesso"] == 100.0


# ═══════════════════════════════════════════════════════════════════════════
# 3. Entrega abandonada por morte do processo é recuperável
# ═══════════════════════════════════════════════════════════════════════════


def _semear(servico: WebhookService, webhook_id: int, casos: list[tuple[str, int, bool]]) -> None:
    """Cria linhas `(status, ecd_id, antiga)` direto no banco.

    `antiga=True` recua `criado_em` duas horas — muito além do limite de
    abandono — para simular processo que morreu e não voltou.
    """
    antigo = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(hours=2)
    engine = criar_engine(servico._referencia)
    with get_session(engine) as sessao:
        for status, ecd_id, antiga in casos:
            corpo = json.dumps(
                {
                    "evento": "ecd.importada",
                    "dados": {"ecd_id": ecd_id},
                    "timestamp": "2026-07-30T00:00:00+00:00",
                    "webhook_id": webhook_id,
                }
            )
            sessao.add(
                WebhookDelivery(
                    webhook_id=webhook_id,
                    evento="ecd.importada",
                    status=status,
                    request_body=corpo,
                    tentativa=1,
                    **({"criado_em": antigo} if antiga else {}),
                )
            )
        sessao.commit()


class TestEntregaAbandonada:
    def test_morte_durante_o_post_e_recuperada(self, servico):
        """Processo morreu no POST: linha em `pending`, evento não chegou."""
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear(servico, wh.id, [("pending", 7, True)])

        orfas = servico.deliveries_abandonadas()

        assert [json.loads(o.request_body)["dados"]["ecd_id"] for o in orfas] == [7]

    def test_morte_no_backoff_e_recuperada(self, servico):
        """Morreu entre tentativas: `superseded` sem sucessora.

        É o caso que a mudança do item 1 poderia ter escondido: a tentativa
        está terminal e correta, mas a entrega lógica nunca terminou.
        """
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear(servico, wh.id, [("superseded", 8, True)])

        orfas = servico.deliveries_abandonadas()

        assert [json.loads(o.request_body)["dados"]["ecd_id"] for o in orfas] == [8]

    def test_entrega_concluida_nao_e_recuperada(self, servico):
        """Reenviar o que já chegou duplicaria o evento no assinante."""
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear(servico, wh.id, [("superseded", 9, True), ("success", 9, True)])

        assert servico.deliveries_abandonadas() == []

    def test_entrega_falha_nao_conta_como_abandonada(self, servico):
        """`failed` já tem desfecho e já é vista pelo reenvio; não é órfã."""
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear(servico, wh.id, [("superseded", 11, True), ("failed", 11, True)])

        assert servico.deliveries_abandonadas() == []

    def test_entrega_em_voo_agora_nao_e_recuperada(self, servico):
        """O ponto mais delicado: reenviar o que ainda está indo duplica."""
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear(servico, wh.id, [("pending", 10, False)])

        assert servico.deliveries_abandonadas() == []

    def test_limite_de_abandono_cobre_o_pior_caso_da_entrega(self, servico):
        """A folga tem de passar do pior caso legítimo, senão duplica entrega."""
        wh = servico.registrar(
            url="https://destino.exemplo/hook", eventos=["ecd.importada"], max_retries=5
        )
        from src.settings import get_settings

        cfg = get_settings()
        pior_caso = 5 * cfg.webhook_timeout_seconds + sum(
            min(mod.BACKOFF_BASE**n, mod.BACKOFF_MAX) for n in range(1, 5)
        )

        assert servico._segundos_para_abandono(wh) > pior_caso

    def test_webhook_com_mais_retries_espera_mais(self, servico):
        """Usar o default global para todos subestimaria quem tem mais tentativas."""
        poucos = servico.registrar(
            url="https://a.exemplo/hook", eventos=["ecd.importada"], max_retries=2
        )
        muitos = servico.registrar(
            url="https://b.exemplo/hook", eventos=["ecd.importada"], max_retries=9
        )

        assert servico._segundos_para_abandono(muitos) > servico._segundos_para_abandono(poucos)

    def test_filtro_por_webhook(self, servico):
        a = servico.registrar(url="https://a.exemplo/hook", eventos=["ecd.importada"])
        b = servico.registrar(url="https://b.exemplo/hook", eventos=["ecd.importada"])
        _semear(servico, a.id, [("pending", 20, True)])
        _semear(servico, b.id, [("pending", 21, True)])

        assert [o.webhook_id for o in servico.deliveries_abandonadas(webhook_id=b.id)] == [b.id]

    def test_reenvio_manual_recupera_a_abandonada(self, servico, monkeypatch):
        """O caminho completo: o operador aperta "Reenviar falhas" e o evento sai.

        Antes desta mudança não havia caminho nenhum — nem automático nem
        manual — para essa entrega.
        """
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear(servico, wh.id, [("pending", 7, True)])
        enviados = []

        class Cliente:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None, headers=None):
                enviados.append(json)

                class R:
                    status_code = 200
                    text = ""

                return R()

        monkeypatch.setattr(mod.httpx, "AsyncClient", Cliente)
        resultado = asyncio.run(servico.retry_failed())

        assert resultado["abandonadas_recuperadas"] == 1
        assert resultado["sucessos"] == 1
        assert enviados, "o payload precisa chegar ao envio, não só o status mudar"
        assert enviados[0]["dados"] == {"ecd_id": 7}
        assert enviados[0]["timestamp"] == "2026-07-30T00:00:00+00:00", "timestamp original"

    def test_abandonada_recuperada_sai_da_lista(self, servico, monkeypatch):
        """Sem isto, a mesma entrega seria reenviada em todo clique."""
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear(servico, wh.id, [("pending", 7, True)])
        monkeypatch.setattr(mod.httpx, "AsyncClient", _cliente_falso([200]))

        asyncio.run(servico.retry_failed())

        assert servico.deliveries_abandonadas() == []

    def test_reenvio_nao_poe_a_origem_em_estado_nao_terminal(self, servico, monkeypatch):
        """O próprio reenvio criava órfão: marcava a origem `retrying` antes de sair.

        Se o processo morresse durante o reenvio, a linha ficava presa nesse
        estado — o defeito reaparecendo pelo caminho do conserto.

        Olhar o estado final não vê isso: o `retried`/`failed` do fim
        sobrescreve o transitório. O teste observa a linha **durante** o envio,
        que é o instante em que a morte do processo a congelaria.
        """
        monkeypatch.setattr(mod.httpx, "AsyncClient", _cliente_falso([503]))
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        asyncio.run(servico._enviar_com_retry(wh, WebhookEvent(tipo="ecd.importada")))

        origem = next(linha for linha in _linhas(servico) if linha.status == "failed")
        vistos = []

        class Espia:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                atual = next(linha for linha in _linhas(servico) if linha.id == origem.id)
                vistos.append(atual.status)

                class R:
                    status_code = 200
                    text = ""

                return R()

        monkeypatch.setattr(mod.httpx, "AsyncClient", Espia)
        asyncio.run(servico.retry_failed())

        assert vistos, "o envio precisa ter acontecido para o teste valer"
        assert all(status in STATUS_TERMINAIS for status in vistos), (
            f"a linha de origem ficou em {vistos} durante o reenvio — morte do "
            "processo aqui a deixaria presa nesse estado"
        )
        assert all(linha.status in STATUS_TERMINAIS for linha in _linhas(servico))

    def test_lote_limita_o_reenvio_e_reporta_o_resto(self, servico, monkeypatch):
        """Truncar em silêncio leria como "processei tudo".

        O reenvio é sequencial e o endpoint o aguarda dentro da requisição: no
        pior caso cada entrega custa todas as tentativas esgotando o timeout
        mais os backoffs. Um lote grande deixa a requisição aberta por quase
        uma hora, o navegador do operador desiste e o trabalho continua no
        servidor. O lote é limitado e o retorno diz quantas ficaram.
        """
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        excedente = 7
        total = mod.LOTE_DE_REENVIO + excedente
        _semear(servico, wh.id, [("pending", i, True) for i in range(total)])
        monkeypatch.setattr(mod.httpx, "AsyncClient", _cliente_falso([200]))

        assert len(servico.deliveries_abandonadas()) == total, "a detecção enxerga todas"

        resultado = asyncio.run(servico.retry_failed())

        assert resultado["total_falhas"] == mod.LOTE_DE_REENVIO
        assert resultado["restantes"] == excedente
        assert len(servico.deliveries_abandonadas()) == excedente, "o resto segue recuperável"

    def test_restantes_zera_quando_o_lote_cobre_tudo(self, servico, monkeypatch):
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        _semear(servico, wh.id, [("pending", 1, True), ("pending", 2, True)])
        monkeypatch.setattr(mod.httpx, "AsyncClient", _cliente_falso([200]))

        assert asyncio.run(servico.retry_failed())["restantes"] == 0

    def test_restantes_conta_falhas_alem_do_lote(self, servico, monkeypatch):
        """O excedente de `failed` também precisa aparecer, não só o de órfãs."""
        wh = servico.registrar(url="https://destino.exemplo/hook", eventos=["ecd.importada"])
        excedente = 4
        _semear(
            servico,
            wh.id,
            [("failed", i, True) for i in range(mod.LOTE_DE_REENVIO + excedente)],
        )
        monkeypatch.setattr(mod.httpx, "AsyncClient", _cliente_falso([200]))

        assert asyncio.run(servico.retry_failed())["restantes"] == excedente


class TestLoteCabeNumaRequisicao:
    """O tamanho do lote tem de sair de um limite de tempo, não de gosto.

    `POST /api/v1/webhooks/retry` aguarda o reenvio **dentro da requisição**.
    Se o lote for grande e o endpoint do assinante estiver morto, a requisição
    fica aberta por quase uma hora: o navegador do operador desiste, o
    trabalho continua no servidor e ele clica de novo.
    """

    TETO_DE_REQUISICAO_SEGUNDOS = 900  # 15 min: já é muito, e é o limite aceito

    @staticmethod
    def _pior_caso_por_entrega() -> int:
        from src.settings import get_settings

        cfg = get_settings()
        tentativas = max(1, cfg.webhook_default_max_retries)
        backoff = sum(min(mod.BACKOFF_BASE**n, mod.BACKOFF_MAX) for n in range(1, tentativas))
        return tentativas * max(1, cfg.webhook_timeout_seconds) + backoff

    def test_pior_caso_do_lote_cabe_no_teto(self):
        pior = mod.LOTE_DE_REENVIO * self._pior_caso_por_entrega()
        assert pior <= self.TETO_DE_REQUISICAO_SEGUNDOS, (
            f"um clique em 'Reenviar falhas' pode levar {pior}s ({pior // 60} min) "
            "contra endpoint morto — a requisição fica aberta esse tempo todo"
        )

    def test_o_lote_ainda_vale_a_pena(self):
        """Teto pequeno demais transformaria o botão em clique-a-clique."""
        assert mod.LOTE_DE_REENVIO >= 5


class TestVocabularioDeStatus:
    def test_desfecho_nao_inclui_estado_intermediario(self):
        """`superseded` e `pending` não são desfecho — é o que conserta a taxa."""
        assert "superseded" not in STATUS_DESFECHO
        assert "pending" not in STATUS_DESFECHO

    def test_superseded_e_terminal(self):
        assert "superseded" in STATUS_TERMINAIS

    def test_retrying_saiu_do_vocabulario(self):
        assert "retrying" not in STATUS_TERMINAIS
        assert "retrying" not in STATUS_DESFECHO
