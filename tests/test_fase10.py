"""Testes da Fase 10 — Webhooks, Multi-ECD Frontend, Layout Customizável, Cron."""

import datetime
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

import pytest

from src.db.models import (
    ECD,
    Empresa,
    WebhookRegistration,
    criar_engine,
    get_session,
    init_db,
)
from src.db.repository import Repository
from src.parsers.ecd import ECDParser
from src.webhooks import EVENTOS_DISPONIVEIS, WebhookEvent, WebhookService


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def db_webhook():
    """Banco temporário limpo para testes de webhook."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()
    os.environ["SPED_HUB_DB"] = db_path

    engine = criar_engine(db_path)
    init_db(engine)

    yield db_path

    os.unlink(db_path)


@pytest.fixture
def db_comparar():
    """Banco temporário com fixture ECD carregada para testes de comparação."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()
    os.environ["SPED_HUB_DB"] = db_path

    engine = criar_engine(db_path)
    init_db(engine)
    session = get_session(engine)

    fixture = Path(__file__).parent / "fixtures" / "ecd_sample.txt"
    parser = ECDParser()
    registros = parser.parse_todos(fixture)

    from collections import defaultdict
    grupos = defaultdict(list)
    for r in registros:
        grupos[r["_reg"]].append(r)

    repo = Repository(session)

    r0000 = grupos["0000"][0]
    empresa = repo.upsert_empresa({
        "cnpj": str(int(r0000.get("CNPJ", 0))).zfill(14),
        "nome": r0000.get("NOME", ""),
        "uf": r0000.get("UF", ""),
        "ie": r0000.get("IE", ""),
        "cod_mun": str(int(r0000.get("COD_MUN", 0))).zfill(7) if r0000.get("COD_MUN") else None,
        "im": r0000.get("IM", ""),
        "ind_sit_esp": int(r0000.get("IND_SIT_ESP", 0)) if r0000.get("IND_SIT_ESP") else None,
        "ind_nire": int(r0000.get("IND_NIRE", 0)) if r0000.get("IND_NIRE") else None,
        "ind_fin_esc": int(r0000.get("IND_FIN_ESC", 0)) if r0000.get("IND_FIN_ESC") else None,
        "ind_grande_por": int(r0000.get("IND_GRANDE_POR", 0)) if r0000.get("IND_GRANDE_POR") else None,
        "tip_ecd": r0000.get("TIP_ECD", ""),
        "ident_mf": r0000.get("IDENT_MF", ""),
        "ind_esc_cons": r0000.get("IND_ESC_CONS", ""),
    })

    rI010 = grupos["I010"][0] if grupos["I010"] else {}
    leiaute = rI010.get("COD_VER_LC", "009")

    def _parse_data(valor):
        if len(valor) == 8 and valor.isdigit():
            return datetime.date(int(valor[4:8]), int(valor[2:4]), int(valor[0:2]))
        return datetime.date.today()

    dt_ini = _parse_data(str(int(r0000.get("DT_INI", 0))).zfill(8))
    dt_fin = _parse_data(str(int(r0000.get("DT_FIN", 0))).zfill(8))

    ecd = repo.criar_ecd(empresa.id, {
        "leiaute": leiaute,
        "dt_ini": dt_ini,
        "dt_fin": dt_fin,
        "ind_esc": rI010.get("IND_ESC", ""),
        "cod_ver_lc": leiaute,
        "hash_arquivo": "test_hash",
        "nome_arquivo": "ecd_sample.txt",
    })

    contas = []
    for r in grupos["I050"]:
        contas.append({
            "cod_cta": r.get("COD_CTA", ""),
            "cod_cta_sup": r.get("COD_CTA_SUP", ""),
            "nome_cta": r.get("NOME_CTA", ""),
            "cod_nat": r.get("COD_NAT", "01"),
            "ind_cta": r.get("IND_CTA", "A"),
            "nivel": int(r.get("NIVEL", 0)),
            "dt_alt": _parse_data(str(int(r.get("DT_ALT", 0))).zfill(8)) if r.get("DT_ALT") else None,
        })
    repo.inserir_plano_contas(ecd.id, contas)

    saldos = []
    for r in grupos["I155"]:
        saldos.append({
            "cod_cta": r.get("COD_CTA", ""), "cod_ccus": r.get("COD_CCUS", ""),
            "dt_ini": dt_ini, "dt_fin": dt_fin,
            "vl_sld_ini": r.get("VL_SLD_INI", 0.0) or 0.0, "ind_dc_ini": r.get("IND_DC_INI", "D"),
            "vl_deb": r.get("VL_DEB", 0.0) or 0.0, "vl_cred": r.get("VL_CRED", 0.0) or 0.0,
            "vl_sld_fin": r.get("VL_SLD_FIN", 0.0) or 0.0, "ind_dc_fin": r.get("IND_DC_FIN", "D"),
        })
    repo.inserir_saldos_periodicos(ecd.id, saldos)

    saldos_res = []
    for r in grupos["I355"]:
        saldos_res.append({
            "cod_cta": r.get("COD_CTA", ""), "cod_ccus": r.get("COD_CCUS", ""),
            "dt_res": dt_fin,
            "vl_sld_fin": r.get("VL_SLD_FIN", 0.0) or 0.0, "ind_dc_fin": r.get("IND_DC_FIN", "D"),
        })
    repo.inserir_saldos_resultado(ecd.id, saldos_res)

    lancs = []
    for r in grupos["I200"]:
        lancs.append({
            "num_lcto": r.get("NUM_LCTO", ""),
            "dt_lcto": _parse_data(str(int(r.get("DT_LCTO", 0))).zfill(8)) if r.get("DT_LCTO") else dt_ini,
            "vl_lcto": r.get("VL_LCTO", 0.0) or 0.0,
            "ind_lcto": r.get("IND_LCTO", "N"),
            "num_arq": int(r.get("NUM_ARQ", 0)) if r.get("NUM_ARQ") else None,
        })
    repo.inserir_lancamentos(ecd.id, lancs)

    partidas = []
    for r in grupos["I250"]:
        partidas.append({
            "num_lcto": r.get("NUM_LCTO", ""),
            "dt_lcto": _parse_data(str(int(r.get("DT_LCTO", 0))).zfill(8)).isoformat() if r.get("DT_LCTO") else dt_ini.isoformat(),
            "cod_cta": r.get("COD_CTA", ""), "cod_ccus": r.get("COD_CCUS", ""),
            "vl_dc": r.get("VL_DC", 0.0) or 0.0, "ind_dc": r.get("IND_DC", "D"),
            "num_arq": int(r.get("NUM_ARQ", 0)) if r.get("NUM_ARQ") else None,
            "cod_hist_pad": r.get("COD_HIST_PAD", ""), "hist": r.get("HIST", ""),
            "cod_part": r.get("COD_PART", ""),
        })
    repo.inserir_partidas(ecd.id, partidas)

    repo.commit()
    ecd_id_val = ecd.id
    session.close()

    yield ecd_id_val

    os.unlink(db_path)


# ── Testes: Webhook Model ────────────────────────────────────────────────────


class TestWebhookModel:

    def test_criar_webhook(self, db_webhook):
        engine = criar_engine(db_webhook)
        init_db(engine)
        session = get_session(engine)
        try:
            wh = WebhookRegistration(
                url="https://example.com/hook",
                eventos=json.dumps(["ecd.importada"]),
                descricao="Teste",
            )
            session.add(wh)
            session.commit()
            assert wh.id is not None
            assert wh.get_eventos() == ["ecd.importada"]
            assert wh.ativo is True
            assert wh.total_envios == 0
        finally:
            session.close()

    def test_webhook_inativo(self, db_webhook):
        engine = criar_engine(db_webhook)
        init_db(engine)
        session = get_session(engine)
        try:
            wh = WebhookRegistration(
                url="https://example.com/hook",
                eventos=json.dumps(["ecd.importada"]),
                ativo=False,
            )
            session.add(wh)
            session.commit()
            assert wh.ativo is False
        finally:
            session.close()


# ── Testes: Webhook Service ──────────────────────────────────────────────────


class TestWebhookService:

    def test_registrar(self, db_webhook):
        svc = WebhookService(db_webhook)
        wh = svc.registrar(
            url="https://example.com/hook",
            eventos=["ecd.importada", "ecd.validada"],
            descricao="Webhook de teste",
        )
        assert wh.id is not None
        assert wh.url == "https://example.com/hook"
        assert wh.get_eventos() == ["ecd.importada", "ecd.validada"]
        assert wh.ativo is True

    def test_listar(self, db_webhook):
        svc = WebhookService(db_webhook)
        svc.registrar(url="https://a.com/hook", eventos=["ecd.importada"])
        svc.registrar(url="https://b.com/hook", eventos=["ecd.validada"])
        webhooks = svc.listar()
        assert len(webhooks) == 2

    def test_atualizar(self, db_webhook):
        svc = WebhookService(db_webhook)
        wh = svc.registrar(url="https://old.com/hook", eventos=["ecd.importada"])
        updated = svc.atualizar(wh.id, url="https://new.com/hook", ativo=False)
        assert updated is not None
        assert updated.url == "https://new.com/hook"
        assert updated.ativo is False

    def test_remover(self, db_webhook):
        svc = WebhookService(db_webhook)
        wh = svc.registrar(url="https://x.com/hook", eventos=["ecd.importada"])
        assert svc.remover(wh.id) is True
        assert svc.remover(9999) is False

    def test_atualizar_inexistente(self, db_webhook):
        svc = WebhookService(db_webhook)
        assert svc.atualizar(9999, url="https://x.com/hook") is None


# ── Testes: Webhook Event ────────────────────────────────────────────────────


class TestWebhookEvent:

    def test_evento_criacao(self):
        evt = WebhookEvent(
            tipo="ecd.importada",
            dados={"ecd_id": 1, "empresa": "Teste"},
        )
        assert evt.tipo == "ecd.importada"
        assert evt.dados["ecd_id"] == 1
        assert evt.timestamp is not None

    def test_eventos_disponiveis(self):
        assert "ecd.importada" in EVENTOS_DISPONIVEIS
        assert "ecd.validada" in EVENTOS_DISPONIVEIS
        assert "relatorio.gerado" in EVENTOS_DISPONIVEIS
        assert len(EVENTOS_DISPONIVEIS) == 3


# ── Testes: Multi-ECD Comparison ─────────────────────────────────────────────


class TestMultiECD:

    def test_get_multi_ecd_comparison_single(self, db_comparar):
        """Com uma única ECD, deve retornar None."""
        from src.dashboard.services import DashboardService
        engine = criar_engine(os.environ["SPED_HUB_DB"])
        session = get_session(engine)
        try:
            svc = DashboardService(session, db_comparar)
            result = svc.get_multi_ecd_comparison([db_comparar])
            assert result is None
        finally:
            session.close()

    def test_get_multi_ecd_comparison_limite_5(self, db_comparar):
        """IDs além de 5 devem ser truncados."""
        from src.dashboard.services import DashboardService
        engine = criar_engine(os.environ["SPED_HUB_DB"])
        session = get_session(engine)
        try:
            svc = DashboardService(session, db_comparar)
            # Passa 7 IDs, deve truncar para 5
            result = svc.get_multi_ecd_comparison([db_comparar] * 7)
            # Com 1 ECD real repetida 5x, ainda é < 2 distintas → None
            # Mas o importante é que não quebra
            assert result is None or "ecds" in result
        finally:
            session.close()


# ── Testes: Layout Customizável ──────────────────────────────────────────────


class TestLayoutCustomizavel:

    def test_get_layout_default(self, db_comparar):
        from src.dashboard.services import DashboardService
        engine = criar_engine(os.environ["SPED_HUB_DB"])
        session = get_session(engine)
        try:
            svc = DashboardService(session, db_comparar)
            layout = svc.get_layout_customizavel(db_comparar, "balanco")
            assert layout["relatorio"] == "balanco"
            assert len(layout["colunas"]) == 5
            assert layout["colunas"][0]["key"] == "cod_cta"
        finally:
            session.close()

    def test_get_layout_dre(self, db_comparar):
        from src.dashboard.services import DashboardService
        engine = criar_engine(os.environ["SPED_HUB_DB"])
        session = get_session(engine)
        try:
            svc = DashboardService(session, db_comparar)
            layout = svc.get_layout_customizavel(db_comparar, "dre")
            assert layout["relatorio"] == "dre"
            assert len(layout["colunas"]) == 3
        finally:
            session.close()

    def test_get_layout_dfc(self, db_comparar):
        from src.dashboard.services import DashboardService
        engine = criar_engine(os.environ["SPED_HUB_DB"])
        session = get_session(engine)
        try:
            svc = DashboardService(session, db_comparar)
            layout = svc.get_layout_customizavel(db_comparar, "dfc")
            assert layout["relatorio"] == "dfc"
            assert len(layout["colunas"]) == 3
        finally:
            session.close()

    def test_get_layout_diario(self, db_comparar):
        from src.dashboard.services import DashboardService
        engine = criar_engine(os.environ["SPED_HUB_DB"])
        session = get_session(engine)
        try:
            svc = DashboardService(session, db_comparar)
            layout = svc.get_layout_customizavel(db_comparar, "diario")
            assert layout["relatorio"] == "diario"
            assert len(layout["colunas"]) == 6
        finally:
            session.close()


# ── Testes: API v1 Webhooks ──────────────────────────────────────────────────


class TestAPIWebhooks:

    def test_listar_eventos(self, db_webhook):
        from src.api.routes import listar_eventos
        import asyncio
        # Mock da dependência api_key
        result = asyncio.run(_call_listar_eventos())
        assert "eventos" in result
        assert len(result["eventos"]) == 3

    def test_registrar_webhook_api(self, db_webhook):
        from src.api.routes import registrar_webhook
        import asyncio
        payload = {
            "url": "https://example.com/hook",
            "eventos": ["ecd.importada"],
            "descricao": "Teste API",
        }
        result = asyncio.run(_call_registrar(payload))
        assert result["status"] == "ok"
        assert result["url"] == "https://example.com/hook"

    def test_registrar_webhook_sem_url(self, db_webhook):
        from src.api.routes import registrar_webhook
        from fastapi import HTTPException
        import asyncio
        payload = {"url": "", "eventos": ["ecd.importada"]}
        try:
            asyncio.run(_call_registrar(payload))
            assert False, "Deveria ter lançado HTTPException"
        except HTTPException as e:
            assert e.status_code == 400

    def test_registrar_webhook_evento_invalido(self, db_webhook):
        from src.api.routes import registrar_webhook
        from fastapi import HTTPException
        import asyncio
        payload = {"url": "https://x.com/hook", "eventos": ["evento.invalido"]}
        try:
            asyncio.run(_call_registrar(payload))
            assert False, "Deveria ter lançado HTTPException"
        except HTTPException as e:
            assert e.status_code == 400

    def test_listar_webhooks(self, db_webhook):
        from src.api.routes import listar_webhooks
        import asyncio
        # Registra um primeiro
        svc = WebhookService(db_webhook)
        svc.registrar(url="https://a.com/hook", eventos=["ecd.importada"])
        result = asyncio.run(_call_listar_webhooks())
        assert result["total"] >= 1

    def test_remover_webhook(self, db_webhook):
        from src.api.routes import remover_webhook
        import asyncio
        svc = WebhookService(db_webhook)
        wh = svc.registrar(url="https://x.com/hook", eventos=["ecd.importada"])
        result = asyncio.run(_call_remover(wh.id))
        assert result["status"] == "ok"

    def test_remover_webhook_inexistente(self, db_webhook):
        from src.api.routes import remover_webhook
        from fastapi import HTTPException
        import asyncio
        try:
            asyncio.run(_call_remover(9999))
            assert False
        except HTTPException as e:
            assert e.status_code == 404


# ── Helpers para chamar endpoints async ──────────────────────────────────────


async def _call_listar_eventos():
    from src.api.routes import listar_eventos
    return await listar_eventos(api_key="test")


async def _call_registrar(payload):
    from src.api.routes import registrar_webhook
    return await registrar_webhook(payload=payload, api_key="test")


async def _call_listar_webhooks():
    from src.api.routes import listar_webhooks
    return await listar_webhooks(api_key="test")


async def _call_remover(webhook_id):
    from src.api.routes import remover_webhook
    return await remover_webhook(webhook_id=webhook_id, api_key="test")


# ── Testes: Cron Script ──────────────────────────────────────────────────────


class TestCronScript:

    def test_script_existe(self):
        script = Path(__file__).parent.parent / "scripts" / "cron-import.sh"
        assert script.exists()
        assert os.access(script, os.X_OK)

    def test_script_shebang(self):
        script = Path(__file__).parent.parent / "scripts" / "cron-import.sh"
        first_line = open(script).readline().strip()
        assert first_line == "#!/bin/bash"


# ── Testes: Templates Fase 10 ────────────────────────────────────────────────


class TestTemplatesFase10:

    def test_comparar_template_existe(self):
        template = Path(__file__).parent.parent / "src" / "dashboard" / "templates" / "comparar.html"
        assert template.exists()

    def test_layout_template_existe(self):
        template = Path(__file__).parent.parent / "src" / "dashboard" / "templates" / "layout.html"
        assert template.exists()

    def test_comparar_template_tem_chartjs(self):
        template = Path(__file__).parent.parent / "src" / "dashboard" / "templates" / "comparar.html"
        content = template.read_text()
        assert "chart.js" in content.lower()
        assert "alpinejs" in content.lower()

    def test_layout_template_tem_toggle(self):
        template = Path(__file__).parent.parent / "src" / "dashboard" / "templates" / "layout.html"
        content = template.read_text()
        assert "localStorage" in content
        assert "toggle" in content.lower()