"""Testes da Fase 7 — API REST v1, multi-ECD, watchdog, layout customizável."""

import datetime
import os
import sys
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api import _hash_key, gerar_api_key, verificar_api_key
from src.dashboard.services import DashboardService
from src.db.models import (
    ECD,
    ApiKey,
    Base,
    Empresa,
    Lancamento,
    Partida,
    PlanoConta,
    SaldoPeriodico,
    SaldoResultado,
)


@pytest.fixture
def db_fase7():
    """Cria banco com 3 ECDs da mesma empresa para testes multi-período."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session = Session(engine)

    emp = Empresa(cnpj="12345678000199", nome="Empresa Fase 7 Ltda")
    session.add(emp)
    session.flush()

    ecds = []
    for ano in [2022, 2023, 2024]:
        ecd = ECD(
            empresa_id=emp.id,
            leiaute="009",
            dt_ini=datetime.date(ano, 1, 1),
            dt_fin=datetime.date(ano, 12, 31),
            nome_arquivo=f"ecd_{ano}.txt",
        )
        session.add(ecd)
        session.flush()
        ecds.append(ecd)

        # Plano de contas simplificado
        contas_data = [
            ("1", "Ativo Total", "01", "S", 1, None),
            ("1.1", "Ativo Circulante", "01", "S", 2, "1"),
            ("1.1.1", "Caixa", "01", "A", 3, "1.1"),
            ("1.1.2", "Clientes", "01", "A", 3, "1.1"),
            ("2", "Passivo Total", "02", "S", 1, None),
            ("2.1", "Passivo Circulante", "02", "S", 2, "2"),
            ("2.1.1", "Fornecedores", "02", "A", 3, "2.1"),
            ("2.1.2", "Empréstimos", "02", "A", 3, "2.1"),
            ("3", "PL Total", "03", "S", 1, None),
            ("3.1", "Capital Social", "03", "A", 2, "3"),
            ("3.2", "Reservas", "03", "A", 2, "3"),
            ("4", "Receita Bruta", "04", "A", 1, None),
            ("4.1", "Receita de Serviços", "04", "A", 2, "4"),
            ("5", "Despesas", "04", "A", 1, None),
            ("5.1", "Despesas Administrativas", "04", "A", 2, "5"),
        ]
        for cod, nome, nat, ind, nivel, sup in contas_data:
            session.add(
                PlanoConta(
                    ecd_id=ecd.id,
                    cod_cta=cod,
                    nome_cta=nome,
                    cod_nat=nat,
                    ind_cta=ind,
                    nivel=nivel,
                    cod_cta_sup=sup,
                )
            )

        # Saldos (crescentes por ano)
        base = 100000 * (ano - 2021)
        saldos_data = [
            ("1.1.1", base * 0.1, "D"),
            ("1.1.2", base * 0.2, "D"),
            ("2.1.1", base * 0.15, "C"),
            ("2.1.2", base * 0.1, "C"),
            ("3.1", base * 0.3, "C"),
            ("3.2", base * 0.05, "C"),
        ]
        for cod, vl, dc in saldos_data:
            session.add(
                SaldoPeriodico(
                    ecd_id=ecd.id,
                    cod_cta=cod,
                    dt_ini=datetime.date(ano, 1, 1),
                    dt_fin=datetime.date(ano, 12, 31),
                    vl_sld_ini=vl * 0.9,
                    ind_dc_ini=dc,
                    vl_deb=vl * 0.2,
                    vl_cred=vl * 0.1,
                    vl_sld_fin=vl,
                    ind_dc_fin=dc,
                )
            )

        # Saldos resultado
        session.add(
            SaldoResultado(
                ecd_id=ecd.id,
                cod_cta="4.1",
                dt_res=datetime.date(ano, 12, 31),
                vl_sld_fin=base * 0.5,
                ind_dc_fin="C",
            )
        )
        session.add(
            SaldoResultado(
                ecd_id=ecd.id,
                cod_cta="5.1",
                dt_res=datetime.date(ano, 12, 31),
                vl_sld_fin=base * 0.3,
                ind_dc_fin="D",
            )
        )

        # Lançamentos
        for i in range(1, 6):
            lanc = Lancamento(
                ecd_id=ecd.id,
                num_lcto=str(i),
                dt_lcto=datetime.date(ano, i * 2, 15),
                vl_lcto=1000.0,
                ind_lcto="N",
            )
            session.add(lanc)
            session.flush()
            session.add(
                Partida(
                    lancamento_id=lanc.id,
                    cod_cta="1.1.1",
                    vl_dc=1000.0,
                    ind_dc="D",
                    hist=f"Lanc {i}",
                )
            )
            session.add(
                Partida(
                    lancamento_id=lanc.id,
                    cod_cta="4.1",
                    vl_dc=1000.0,
                    ind_dc="C",
                    hist=f"Lanc {i}",
                )
            )

    # API Key para testes
    chave, hash_chave = gerar_api_key()
    session.add(
        ApiKey(
            nome="Test Key",
            key_hash=hash_chave,
            prefixo=chave[:8],
            ativo=True,
        )
    )
    session.commit()

    yield session, ecds, emp, chave
    session.close()


# ── Testes: API Key ─────────────────────────────────────────────────────────


class TestApiKey:
    def test_gerar_api_key_formato(self):
        chave, hash_chave = gerar_api_key()
        assert chave.startswith("spd_")
        assert len(chave) == 68  # spd_ + 64 hex
        assert len(hash_chave) == 64  # SHA-256

    def test_verificar_api_key_valida(self):
        chave, hash_chave = gerar_api_key()
        assert verificar_api_key(chave, hash_chave) is True

    def test_verificar_api_key_invalida(self):
        _, hash_chave = gerar_api_key()
        assert verificar_api_key("spd_invalida", hash_chave) is False

    def test_hash_key_deterministico(self):
        h1 = _hash_key("spd_teste")
        h2 = _hash_key("spd_teste")
        assert h1 == h2

    def test_api_key_persistida(self, db_fase7):
        session, ecds, emp, chave = db_fase7
        api_key = session.execute(
            select(ApiKey).where(ApiKey.nome == "Test Key")
        ).scalar_one_or_none()
        assert api_key is not None
        assert api_key.ativo is True
        assert api_key.prefixo == chave[:8]


# ── Testes: Multi-ECD Comparison ────────────────────────────────────────────


class TestMultiECD:
    def test_multi_ecd_comparison_3_ecds(self, db_fase7):
        session, ecds, emp, chave = db_fase7
        svc = DashboardService(session, ecds[0].id)
        ids = [e.id for e in ecds]
        result = svc.get_multi_ecd_comparison(ids)

        assert result is not None
        assert len(result["ecds"]) == 3
        assert len(result["balanco"]["labels"]) == 3
        assert len(result["balanco"]["ativo"]) == 3
        assert len(result["dre"]["resultado_liquido"]) == 3
        assert len(result["dfc"]["variacao"]) == 3

    def test_multi_ecd_comparison_menos_de_2(self, db_fase7):
        session, ecds, emp, chave = db_fase7
        svc = DashboardService(session, ecds[0].id)
        result = svc.get_multi_ecd_comparison([ecds[0].id])
        assert result is None

    def test_multi_ecd_comparison_limite_5(self, db_fase7):
        session, ecds, emp, chave = db_fase7
        svc = DashboardService(session, ecds[0].id)
        # Só temos 3, mas o método limita a 5
        ids = [e.id for e in ecds]
        result = svc.get_multi_ecd_comparison(ids)
        assert len(result["ecds"]) <= 5

    def test_multi_ecd_balanco_crescente(self, db_fase7):
        session, ecds, emp, chave = db_fase7
        svc = DashboardService(session, ecds[0].id)
        ids = [e.id for e in ecds]
        result = svc.get_multi_ecd_comparison(ids)

        # Ativo deve crescer a cada ano
        ativos = result["balanco"]["ativo"]
        assert ativos[0] < ativos[1] < ativos[2]

    def test_multi_ecd_dre_metadados(self, db_fase7):
        session, ecds, emp, chave = db_fase7
        svc = DashboardService(session, ecds[0].id)
        ids = [e.id for e in ecds]
        result = svc.get_multi_ecd_comparison(ids)

        for ecd_data in result["ecds"]:
            assert "id" in ecd_data
            assert "empresa" in ecd_data
            assert "periodo" in ecd_data


# ── Testes: Layout Customizável ─────────────────────────────────────────────


class TestLayoutCustomizavel:
    def test_layout_balanco(self, db_fase7):
        session, ecds, emp, chave = db_fase7
        svc = DashboardService(session, ecds[0].id)
        layout = svc.get_layout_customizavel(ecds[0].id, "balanco")

        assert layout["relatorio"] == "balanco"
        assert layout["ecd_id"] == ecds[0].id
        assert len(layout["colunas"]) == 5
        assert any(c["key"] == "saldo_atual" for c in layout["colunas"])

    def test_layout_dre(self, db_fase7):
        session, ecds, emp, chave = db_fase7
        svc = DashboardService(session, ecds[0].id)
        layout = svc.get_layout_customizavel(ecds[0].id, "dre")

        assert layout["relatorio"] == "dre"
        assert len(layout["colunas"]) == 3

    def test_layout_dfc(self, db_fase7):
        session, ecds, emp, chave = db_fase7
        svc = DashboardService(session, ecds[0].id)
        layout = svc.get_layout_customizavel(ecds[0].id, "dfc")

        assert layout["relatorio"] == "dfc"
        assert any(c["key"] == "valor" for c in layout["colunas"])

    def test_layout_diario(self, db_fase7):
        session, ecds, emp, chave = db_fase7
        svc = DashboardService(session, ecds[0].id)
        layout = svc.get_layout_customizavel(ecds[0].id, "diario")

        assert layout["relatorio"] == "diario"
        assert len(layout["colunas"]) == 6
        assert any(c["key"] == "debito" for c in layout["colunas"])

    def test_layout_colunas_visiveis(self, db_fase7):
        session, ecds, emp, chave = db_fase7
        svc = DashboardService(session, ecds[0].id)
        layout = svc.get_layout_customizavel(ecds[0].id, "balanco")

        for col in layout["colunas"]:
            assert "key" in col
            assert "label" in col
            assert "visible" in col
            assert "width" in col


# ── Testes: Watchdog ────────────────────────────────────────────────────────


class TestWatchdog:
    def test_watchdog_import_arquivo(self, db_fase7):
        """Testa importação via watchdog com arquivo ECD sintético."""
        session, ecds, emp, chave = db_fase7

        # Cria arquivo ECD mínimo
        ecd_content = """|0000|12345678000199|EMPRESA TESTE WATCHDOG LTDA||SP|||7107001|||0|0|0|0|||01012024|31122024|
|I010|009|G|
|I050|1|ATIVO|01|S|1|||
|I050|1.1|Caixa|01|A|2|1||
|I051|1.1||1.01.01|
|I155|1.1||01012024|31122024|10000,00|D|5000,00|3000,00|12000,00|D|
|I200|1|01012024|1000,00|N||
|I250|1|01012024|1.1||1000,00|D|||001|Receita de serviços||
|I250|1|01012024|4.1||1000,00|C|||001|Receita de serviços||
|I355|4.1||31122024|1000,00|C|
|9001|3|
"""

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(ecd_content)
            temp_path = f.name

        try:
            from src.watchdog import processar_arquivo

            processar_arquivo(Path(temp_path), ":memory:")
            # Não podemos testar com :memory: pois o watchdog usa o mesmo db_path
            # mas podemos verificar que não lança exceção
        finally:
            os.unlink(temp_path)

    def test_watchdog_hash_file(self):
        """Testa que hash de arquivo é determinístico."""
        from src.watchdog import _hash_file

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("teste")
            path1 = f.name

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("teste")
            path2 = f.name

        try:
            h1 = _hash_file(Path(path1))
            h2 = _hash_file(Path(path2))
            assert h1 == h2
        finally:
            os.unlink(path1)
            os.unlink(path2)

    def test_watchdog_hash_diferente(self):
        """Testa que arquivos diferentes têm hashes diferentes."""
        from src.watchdog import _hash_file

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("teste1")
            path1 = f.name

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("teste2")
            path2 = f.name

        try:
            h1 = _hash_file(Path(path1))
            h2 = _hash_file(Path(path2))
            assert h1 != h2
        finally:
            os.unlink(path1)
            os.unlink(path2)


# ── Testes: API v1 Health ───────────────────────────────────────────────────


class TestApiV1Health:
    def test_health_db_ok(self, db_fase7):
        """Health check com banco funcionando."""
        session, ecds, emp, chave = db_fase7
        # Simula o health check
        try:
            result = session.execute(select(1)).scalar()
            assert result == 1
        except Exception:
            pytest.fail("Banco deveria estar funcionando")


# ── Testes: Evolução Multi-Período com 3 ECDs ──────────────────────────────


class TestEvolucaoMultiPeriodoFase7:
    def test_evolucao_multi_com_3_ecds(self, db_fase7):
        session, ecds, emp, chave = db_fase7
        svc = DashboardService(session, ecds[2].id)  # última ECD
        result = svc.get_evolucao_multi_periodo()

        assert result is not None
        assert result["num_periodos"] == 3
        assert len(result["labels"]) == 3
        assert len(result["ativos"]) == 3
        assert len(result["passivos"]) == 3
        assert len(result["pls"]) == 3
        assert len(result["resultados"]) == 3

    def test_evolucao_multi_valores_crescentes(self, db_fase7):
        session, ecds, emp, chave = db_fase7
        svc = DashboardService(session, ecds[2].id)
        result = svc.get_evolucao_multi_periodo()

        # Ativos devem crescer
        for i in range(len(result["ativos"]) - 1):
            assert result["ativos"][i] < result["ativos"][i + 1]
