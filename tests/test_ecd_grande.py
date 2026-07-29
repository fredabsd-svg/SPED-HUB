"""Importação de ECDs grandes (Fase 17, Etapa 4).

A fixture normal tem 138 registros.  O que importa aqui é o comportamento com
arquivos de verdade: memória constante, cancelamento limpo, e os parâmetros de
lote vindo das settings em vez de constantes no código.

Os arquivos são gerados em tempo de teste (`tests/fixtures/sintetico.py`) —
versionar ECDs de centenas de megabytes seria inviável.
"""

from __future__ import annotations

import resource
import threading

import pytest

from src.db.models import ECD, Lancamento, Partida, PlanoConta, criar_engine, get_session, init_db
from src.ecd_importer import (
    CancelToken,
    ECDImportCancelled,
    ECDImportService,
    hash_file,
)
from src.settings import reset_settings_cache
from tests.fixtures.sintetico import gerar_ecd, registros_esperados


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'grande.db'}")
    init_db(engine)
    session = get_session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


class TestImportacaoGrande:
    def test_arquivo_com_muitos_lancamentos(self, sessao, tmp_path):
        arquivo = gerar_ecd(tmp_path / "grande.txt", lancamentos=5_000)
        esperado = registros_esperados(5_000)

        resultado = ECDImportService(sessao).importar(arquivo)

        assert resultado.lancamentos == esperado["I200"]
        assert resultado.partidas == esperado["I250"]
        assert resultado.contas == esperado["I050"]
        assert sessao.query(Lancamento).count() == esperado["I200"]
        assert sessao.query(Partida).count() == esperado["I250"]

    def test_partidas_ficam_ligadas_ao_lancamento_certo(self, sessao, tmp_path):
        """A ligação passou a ser pelo relacionamento, não pelo id — precisa conferir.

        Cada I200 sintético tem exatamente duas partidas, uma a débito e uma a
        crédito, de mesmo valor.
        """
        arquivo = gerar_ecd(tmp_path / "vinculo.txt", lancamentos=2_000)
        ECDImportService(sessao).importar(arquivo)

        orfas = sessao.query(Partida).filter(Partida.lancamento_id.is_(None)).count()
        assert orfas == 0

        for lancamento in sessao.query(Lancamento).limit(50):
            partidas = lancamento.partidas
            assert len(partidas) == 2, f"lançamento {lancamento.num_lcto} com {len(partidas)}"
            assert {p.ind_dc for p in partidas} == {"D", "C"}
            assert sum(p.vl_dc for p in partidas) == pytest.approx(1000.0)

    def test_escrituracao_fecha(self, sessao, tmp_path):
        """Débitos e créditos precisam bater — o teste de sanidade contábil."""
        arquivo = gerar_ecd(tmp_path / "fecha.txt", lancamentos=3_000)
        ECDImportService(sessao).importar(arquivo)

        debitos = sum(p.vl_dc for p in sessao.query(Partida).filter(Partida.ind_dc == "D"))
        creditos = sum(p.vl_dc for p in sessao.query(Partida).filter(Partida.ind_dc == "C"))
        assert debitos == pytest.approx(creditos)

    def test_memoria_nao_cresce_com_o_tamanho_do_arquivo(self, tmp_path):
        """O custo de memória precisa ser do lote, não do arquivo.

        Importa um arquivo e depois um 4x maior no mesmo processo; o consumo
        adicional do segundo tem de ser uma fração do primeiro.  Se o serviço
        acumulasse registros, cresceria proporcionalmente.
        """
        medidas = []
        for indice, quantidade in enumerate([2_000, 8_000]):
            arquivo = gerar_ecd(tmp_path / f"mem_{quantidade}.txt", lancamentos=quantidade)
            engine = criar_engine(url=f"sqlite:///{tmp_path / f'mem_{indice}.db'}")
            init_db(engine)
            session = get_session(engine)
            try:
                antes = _rss_mb()
                ECDImportService(session).importar(arquivo)
                medidas.append(_rss_mb() - antes)
            finally:
                session.close()
                engine.dispose()

        primeiro, segundo = medidas
        # `ru_maxrss` é um máximo histórico do processo: o segundo só cresce se
        # de fato precisar de mais que o pico já atingido.
        assert segundo <= max(primeiro, 15.0), (
            f"memória cresceu com o arquivo: +{primeiro:.0f} MB para 2k lançamentos, "
            f"+{segundo:.0f} MB para 8k (4x maior)"
        )


class TestCancelamento:
    def test_cancelar_interrompe_e_nao_deixa_rastro(self, sessao, tmp_path):
        """Escrituração pela metade é pior que nenhuma: o balanço não fecharia."""
        arquivo = gerar_ecd(tmp_path / "cancelar.txt", lancamentos=5_000)
        token = CancelToken()

        def cancelar_no_meio(pct: float, _mensagem: str) -> None:
            if pct >= 30:
                token.cancelar("pedido pelo usuário")

        with pytest.raises(ECDImportCancelled) as erro:
            ECDImportService(sessao).importar(
                arquivo, progress=cancelar_no_meio, cancel_token=token
            )

        assert erro.value.registros_lidos > 0
        assert sessao.query(ECD).count() == 0, "ECD parcial ficou no banco"
        assert sessao.query(Lancamento).count() == 0
        assert sessao.query(Partida).count() == 0
        assert sessao.query(PlanoConta).count() == 0

    def test_token_nao_acionado_nao_atrapalha(self, sessao, tmp_path):
        arquivo = gerar_ecd(tmp_path / "sem_cancelar.txt", lancamentos=500)
        resultado = ECDImportService(sessao).importar(arquivo, cancel_token=CancelToken())
        assert resultado.lancamentos == 500

    def test_cancelamento_de_outra_thread(self, sessao, tmp_path):
        """O caso real: a requisição HTTP cancela, o worker está importando."""
        arquivo = gerar_ecd(tmp_path / "thread.txt", lancamentos=8_000)
        token = CancelToken()
        comecou = threading.Event()

        def marcar(pct: float, _mensagem: str) -> None:
            if pct >= 20:
                comecou.set()

        def cancelador() -> None:
            comecou.wait(timeout=30)
            token.cancelar("cancelado de fora")

        threading.Thread(target=cancelador, daemon=True).start()
        with pytest.raises(ECDImportCancelled):
            ECDImportService(sessao).importar(arquivo, progress=marcar, cancel_token=token)
        assert token.motivo == "cancelado de fora"
        assert sessao.query(ECD).count() == 0


class TestParametrosDeLote:
    """`SPED_HUB_ECD_CHUNK_ROWS` e `_BYTES` existiam sem consumidor desde a Etapa 1."""

    def test_flush_interval_vem_das_settings(self, sessao, tmp_path, monkeypatch):
        monkeypatch.setenv("SPED_HUB_ECD_CHUNK_ROWS", "250")
        reset_settings_cache()

        registrados: list[int] = []
        original = sessao.flush

        def contar(*args, **kwargs):
            registrados.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(sessao, "flush", contar)
        arquivo = gerar_ecd(tmp_path / "lote.txt", lancamentos=1_000)
        ECDImportService(sessao).importar(arquivo)
        reset_settings_cache()

        # ~3000 registros / 250 por lote ≈ 12 flushes; o valor exato depende
        # dos registros de cabeçalho, então basta a ordem de grandeza.
        assert 8 <= len(registrados) <= 30, f"{len(registrados)} flushes"

    def test_flush_interval_invalido(self, sessao, tmp_path):
        arquivo = gerar_ecd(tmp_path / "invalido.txt", lancamentos=10)
        with pytest.raises(ValueError):
            ECDImportService(sessao).importar(arquivo, flush_interval=0)

    def test_hash_usa_chunk_configuravel(self, tmp_path, monkeypatch):
        arquivo = gerar_ecd(tmp_path / "hash.txt", lancamentos=200)
        referencia = hash_file(arquivo)

        # O bloco de leitura não pode mudar o resultado do hash.
        monkeypatch.setenv("SPED_HUB_ECD_CHUNK_BYTES", "512")
        reset_settings_cache()
        assert hash_file(arquivo) == referencia
        reset_settings_cache()


class TestDesempenho:
    def test_nao_ha_um_flush_por_lancamento(self, sessao, tmp_path, monkeypatch):
        """O gargalo original: um round-trip ao banco por I200.

        Com 2.000 lançamentos eram >2.000 flushes, ~76% do tempo total.
        """
        chamadas: list[int] = []
        original = sessao.flush

        def contar(*args, **kwargs):
            chamadas.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(sessao, "flush", contar)
        arquivo = gerar_ecd(tmp_path / "flushes.txt", lancamentos=2_000)
        ECDImportService(sessao).importar(arquivo)

        assert len(chamadas) < 100, (
            f"{len(chamadas)} flushes para 2.000 lançamentos — voltou o " "round-trip por registro"
        )


class TestCancelamentoPelaAPI:
    """O cancelamento precisa ser alcançável de fora, senão é só API interna.

    Fluxo real: o upload assíncrono roda numa thread e a requisição de
    cancelamento chega por outra — o registro de tokens no serviço de jobs é
    o que liga as duas pontas.
    """

    def test_cancelar_job_em_execucao(self, tmp_path):
        from src.async_jobs import AsyncJobService
        from src.db.models import init_db

        engine = criar_engine(url=f"sqlite:///{tmp_path / 'jobs.db'}")
        init_db(engine)
        servico = AsyncJobService(str(engine.url))
        job = servico.criar(tipo="ecd_import", parametros={"arquivo": "x.txt"})

        token = CancelToken()
        servico.registrar_token(job.id, token)

        assert servico.cancelar(job.id, motivo="teste") is True
        assert token.cancelado is True
        assert token.motivo == "teste"

        servico.marcar_cancelado(job.id, "Importação cancelada após 10 registros")
        info = servico.obter(job.id, usuario_id=None, admin=True)
        assert info.status == "cancelled"
        engine.dispose()

    def test_cancelar_job_que_nao_esta_rodando(self, tmp_path):
        from src.async_jobs import AsyncJobService
        from src.db.models import init_db

        engine = criar_engine(url=f"sqlite:///{tmp_path / 'jobs2.db'}")
        init_db(engine)
        servico = AsyncJobService(str(engine.url))
        job = servico.criar(tipo="ecd_import", parametros={})
        # Sem token registrado: não há o que cancelar neste processo.
        assert servico.cancelar(job.id) is False
        engine.dispose()

    def test_esquecer_token_apos_o_fim(self, tmp_path):
        from src.async_jobs import AsyncJobService
        from src.db.models import init_db

        engine = criar_engine(url=f"sqlite:///{tmp_path / 'jobs3.db'}")
        init_db(engine)
        servico = AsyncJobService(str(engine.url))
        job = servico.criar(tipo="ecd_import", parametros={})
        servico.registrar_token(job.id, CancelToken())
        servico.esquecer_token(job.id)
        assert servico.cancelar(job.id) is False
        engine.dispose()
