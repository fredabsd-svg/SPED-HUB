"""Importação assíncrona interrompida por reinício (Fase 29).

O executor de uma importação assíncrona é uma thread `daemon` dentro do
processo web. Thread `daemon` é morta no encerramento do interpretador **sem**
rodar `finally`, então reinício, atualização ou queda deixavam:

1. O job em aberto no banco **para sempre**. E a mensagem que sobrava era
   "Aguardando processamento..." — que diz a quem enviou a escrituração que
   ela está na fila. Não estava: o executor era aquela thread, não existe fila
   que alguém varra. O contador esperava por uma importação que ninguém mais
   ia rodar.
2. O progresso reportado com `persistir=False` só na memória, então a linha
   dizia `pending` / 0% durante a importação inteira: depois do reinício o job
   parecia nem ter começado.
3. O arquivo enviado no volume de uploads, órfão, sem nada que soubesse onde
   procurá-lo — o caminho temporário não era registrado em lugar nenhum.

Nada disso era removido pela limpeza automática, que só olhava
`completed`/`failed`.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy

from src.async_jobs import (
    STATUS_EM_ABERTO,
    STATUS_TERMINAIS,
    AsyncJobService,
    JobStatus,
)
from src.db.models import AsyncJob, criar_engine, get_session, init_db


@pytest.fixture
def referencia(tmp_path) -> str:
    alvo = f"sqlite:///{tmp_path / 'jobs.db'}"
    engine = criar_engine(alvo)
    try:
        init_db(engine)
    finally:
        engine.dispose()
    return alvo


@pytest.fixture
def servico(referencia) -> AsyncJobService:
    return AsyncJobService(referencia)


def _linha(referencia: str, job_id: int) -> AsyncJob:
    engine = criar_engine(referencia)
    with get_session(engine) as sessao:
        return sessao.get(AsyncJob, job_id)


def _reiniciar(referencia: str) -> AsyncJobService:
    """Um processo novo: o progresso em memória do anterior não existe mais."""
    return AsyncJobService(referencia)


# ═══════════════════════════════════════════════════════════════════════════
# 1. O banco passa a dizer a verdade durante a importação
# ═══════════════════════════════════════════════════════════════════════════


class TestEstadoPersistido:
    def test_job_em_execucao_nao_fica_como_pendente_no_banco(self, servico, referencia):
        """Antes a linha dizia `pending` durante a importação inteira."""
        job = servico.criar(tipo="ecd_import", parametros={"arquivo": "ecd.txt"})
        servico.atualizar_progresso(job.id, 40, "gravando contas", persistir=False)
        assert _linha(referencia, job.id).status == JobStatus.PENDING.value

        servico.marcar_em_execucao(job.id)

        assert _linha(referencia, job.id).status == JobStatus.PROCESSING.value

    def test_mensagem_persistida_nao_diz_que_esta_aguardando(self, servico, referencia):
        """ "Aguardando processamento..." num job em execução é mentira ativa."""
        job = servico.criar(tipo="ecd_import")
        servico.marcar_em_execucao(job.id)

        assert "Aguardando" not in (_linha(referencia, job.id).mensagem or "")

    def test_caminho_do_upload_fica_registrado(self, servico, referencia):
        """Sem ele, nada sabe onde procurar o arquivo órfão depois da queda."""
        job = servico.criar(tipo="ecd_import", parametros={"arquivo": "ecd.txt"})

        servico.marcar_em_execucao(job.id, arquivo_temporario="/uploads/abc123.txt")

        parametros = json.loads(_linha(referencia, job.id).parametros)
        assert parametros["arquivo_temporario"] == "/uploads/abc123.txt"
        assert parametros["arquivo"] == "ecd.txt", "os parâmetros originais sobrevivem"

    def test_marcar_em_execucao_sem_arquivo_nao_quebra(self, servico, referencia):
        """Outros tipos de job não têm upload associado."""
        job = servico.criar(tipo="export_lote")

        servico.marcar_em_execucao(job.id)

        assert _linha(referencia, job.id).status == JobStatus.PROCESSING.value

    def test_job_inexistente_nao_levanta(self, servico):
        servico.marcar_em_execucao(999_999)  # não deve explodir


# ═══════════════════════════════════════════════════════════════════════════
# 2. O reinício encerra o que ficou em aberto
# ═══════════════════════════════════════════════════════════════════════════


class TestRecuperacaoNoReinicio:
    def test_job_em_execucao_vira_interrompido(self, servico, referencia):
        job = servico.criar(tipo="ecd_import")
        servico.marcar_em_execucao(job.id)

        assert _reiniciar(referencia).recuperar_interrompidos() == 1

        assert _linha(referencia, job.id).status == JobStatus.INTERRUPTED.value

    def test_job_pendente_tambem_vira_interrompido(self, servico, referencia):
        """`pending` não é uma fila: o executor era a thread que morreu."""
        job = servico.criar(tipo="ecd_import")

        assert _reiniciar(referencia).recuperar_interrompidos() == 1

        assert _linha(referencia, job.id).status == JobStatus.INTERRUPTED.value

    def test_mensagem_diz_o_que_aconteceu_e_o_que_fazer(self, servico, referencia):
        """O contador precisa saber que deve reenviar, não que deve esperar."""
        job = servico.criar(tipo="ecd_import")
        servico.marcar_em_execucao(job.id)
        _reiniciar(referencia).recuperar_interrompidos()

        linha = _linha(referencia, job.id)
        assert "Aguardando" not in linha.mensagem
        assert "reinício" in linha.mensagem.lower()
        assert "novamente" in linha.mensagem.lower(), "tem de dizer para reenviar"
        assert "nada foi gravado" in linha.mensagem.lower()

    def test_interrompido_ganha_concluido_em(self, servico, referencia):
        """Sem isso a interface o mostra em andamento."""
        job = servico.criar(tipo="ecd_import")
        _reiniciar(referencia).recuperar_interrompidos()

        assert _linha(referencia, job.id).concluido_em is not None

    def test_job_concluido_nao_e_tocado(self, servico, referencia):
        job = servico.criar(tipo="ecd_import")
        servico.concluir(job.id, {"contas": 23})

        assert _reiniciar(referencia).recuperar_interrompidos() == 0

        linha = _linha(referencia, job.id)
        assert linha.status == JobStatus.COMPLETED.value
        assert json.loads(linha.resultado) == {"contas": 23}

    @pytest.mark.parametrize(
        "preparar,esperado",
        [
            (lambda s, i: s.falhar(i, "arquivo corrompido"), JobStatus.FAILED.value),
            (
                lambda s, i: s.marcar_cancelado(i, "cancelado pelo usuário"),
                JobStatus.CANCELLED.value,
            ),
        ],
    )
    def test_outros_estados_terminais_nao_sao_tocados(
        self, servico, referencia, preparar, esperado
    ):
        """`cancelled` ficava de fora das listas escritas à mão."""
        job = servico.criar(tipo="ecd_import")
        preparar(servico, job.id)

        assert _reiniciar(referencia).recuperar_interrompidos() == 0
        assert _linha(referencia, job.id).status == esperado

    def test_banco_sem_job_em_aberto_nao_faz_nada(self, servico):
        assert servico.recuperar_interrompidos() == 0

    def test_recuperacao_e_idempotente(self, servico, referencia):
        """Duas subidas seguidas não reprocessam nem contam duas vezes."""
        servico.criar(tipo="ecd_import")
        novo = _reiniciar(referencia)

        assert novo.recuperar_interrompidos() == 1
        assert novo.recuperar_interrompidos() == 0

    def test_conta_todos_os_jobs_em_aberto(self, servico, referencia):
        for _ in range(3):
            servico.criar(tipo="ecd_import")
        job_ok = servico.criar(tipo="ecd_import")
        servico.concluir(job_ok.id)

        assert _reiniciar(referencia).recuperar_interrompidos() == 3

    def test_recuperacao_esvazia_o_progresso_em_memoria(self, servico):
        """Entrada órfã no dicionário fica para sempre num processo longo.

        A asserção olha o dicionário, não a resposta da API: com o job já em
        estado terminal, `obter` ignora o overlay de qualquer forma, então pelo
        lado de fora os dois comportamentos são idênticos. O que se perde ao
        não limpar é memória, e é isso que precisa ser verificado.
        """
        job = servico.criar(tipo="ecd_import")
        servico.atualizar_progresso(job.id, 70, "quase lá", persistir=False)
        assert job.id in servico._live_progress

        servico.recuperar_interrompidos()

        assert servico._live_progress == {}
        info = servico.obter(job.id, admin=True)
        assert info.status == JobStatus.INTERRUPTED.value


# ═══════════════════════════════════════════════════════════════════════════
# 3. O arquivo enviado não fica órfão no volume
# ═══════════════════════════════════════════════════════════════════════════


class TestUploadOrfao:
    def test_arquivo_do_job_interrompido_e_removido(self, servico, referencia, tmp_path):
        """O `finally` que o apagava não roda: a thread é `daemon`."""
        arquivo = tmp_path / "upload-abc.txt"
        arquivo.write_text("|0000|LECD|")
        job = servico.criar(tipo="ecd_import")
        servico.marcar_em_execucao(job.id, arquivo_temporario=str(arquivo))

        _reiniciar(referencia).recuperar_interrompidos()

        assert not arquivo.exists()

    def test_arquivo_de_job_concluido_nao_e_tocado(self, servico, referencia, tmp_path):
        """A recuperação não pode sair apagando arquivo de quem terminou."""
        arquivo = tmp_path / "upload-ok.txt"
        arquivo.write_text("|0000|LECD|")
        job = servico.criar(tipo="ecd_import")
        servico.marcar_em_execucao(job.id, arquivo_temporario=str(arquivo))
        servico.concluir(job.id)

        _reiniciar(referencia).recuperar_interrompidos()

        assert arquivo.exists()

    def test_arquivo_ja_removido_nao_quebra_a_subida(self, servico, referencia, tmp_path):
        """Caminho registrado e arquivo ausente é o caso comum: o job terminou."""
        job = servico.criar(tipo="ecd_import")
        servico.marcar_em_execucao(job.id, arquivo_temporario=str(tmp_path / "nao-existe.txt"))

        assert _reiniciar(referencia).recuperar_interrompidos() == 1

    def test_parametros_ilegiveis_nao_impedem_a_recuperacao(self, servico, referencia):
        """Um job com JSON corrompido não pode travar a subida dos outros."""
        job_ruim = servico.criar(tipo="ecd_import")
        engine = criar_engine(referencia)
        with get_session(engine) as sessao:
            linha = sessao.get(AsyncJob, job_ruim.id)
            linha.parametros = "{isto não é json"
            sessao.commit()
        job_bom = servico.criar(tipo="ecd_import")

        assert _reiniciar(referencia).recuperar_interrompidos() == 2

        assert _linha(referencia, job_bom.id).status == JobStatus.INTERRUPTED.value
        assert _linha(referencia, job_ruim.id).status == JobStatus.INTERRUPTED.value


# ═══════════════════════════════════════════════════════════════════════════
# 4. A limpeza automática alcança o novo estado
# ═══════════════════════════════════════════════════════════════════════════


class TestLimpeza:
    def test_interrompido_e_removido_pela_limpeza(self, servico, referencia):
        """Antes só `completed`/`failed` saíam, então o resíduo acumulava."""
        servico.criar(tipo="ecd_import")
        novo = _reiniciar(referencia)
        novo.recuperar_interrompidos()

        assert novo.limpar_antigos(horas=0) == 1

    def test_cancelado_tambem_e_removido(self, servico):
        """Ficava de fora da lista escrita à mão."""
        job = servico.criar(tipo="ecd_import")
        servico.marcar_cancelado(job.id, "cancelado pelo usuário")

        assert servico.limpar_antigos(horas=0) == 1

    def test_job_em_aberto_nao_e_removido_pela_limpeza(self, servico):
        """Apagar job em execução tiraria da tela uma importação que está indo."""
        job = servico.criar(tipo="ecd_import")
        servico.marcar_em_execucao(job.id)

        assert servico.limpar_antigos(horas=0) == 0


class TestVocabularioDeStatus:
    def test_todo_estado_e_terminal_ou_em_aberto(self):
        """Estado fora das duas listas não é recuperado nem limpo por ninguém."""
        declarados = {membro.value for membro in JobStatus}
        cobertos = set(STATUS_TERMINAIS) | set(STATUS_EM_ABERTO)
        assert declarados == cobertos, f"estado sem classificação: {declarados ^ cobertos}"

    def test_as_duas_listas_nao_se_sobrepoem(self):
        assert not set(STATUS_TERMINAIS) & set(STATUS_EM_ABERTO)

    def test_cancelado_conta_como_terminal(self):
        """Era o que faltava nas verificações escritas à mão."""
        assert JobStatus.CANCELLED.value in STATUS_TERMINAIS


class TestSubidaDaAplicacao:
    def test_lifespan_recupera_os_jobs_em_aberto(self, referencia, monkeypatch):
        """A recuperação tem de estar ligada de fato ao ciclo de vida do app.

        Sem isto o método existiria e nunca seria chamado — que é a forma de
        defeito que este projeto já teve com os webhooks, que nunca disparavam.
        """
        from fastapi.testclient import TestClient

        monkeypatch.setenv("DATABASE_URL", referencia)
        from src.settings import reset_settings_cache

        reset_settings_cache()

        servico = AsyncJobService(referencia)
        job = servico.criar(tipo="ecd_import")
        servico.marcar_em_execucao(job.id)

        from src.dashboard.app import app

        with TestClient(app):  # o `with` é o que dispara o lifespan
            pass

        assert _linha(referencia, job.id).status == JobStatus.INTERRUPTED.value

    def test_rota_de_upload_marca_o_job_em_execucao(self, referencia, monkeypatch, tmp_path):
        """A ligação na rota real, não só o método existindo.

        Sem este teste, `marcar_em_execucao` poderia deixar de ser chamada em
        `/api/upload-async` e nada acusaria — a forma de defeito que este
        projeto já teve com os webhooks, que existiam e nunca disparavam.
        """
        from fastapi.testclient import TestClient

        monkeypatch.setenv("DATABASE_URL", referencia)
        monkeypatch.setenv("SPED_HUB_UPLOAD_DIR", str(tmp_path / "uploads"))
        from src.settings import reset_settings_cache

        reset_settings_cache()

        from src.audit import init_audit_service
        from src.auth import init_auth
        from src.dashboard.app import app
        from src.ratelimit import init_limiter

        init_auth(referencia)
        init_audit_service(referencia)
        init_limiter(referencia)

        cliente = TestClient(app)
        cliente.post(
            "/api/register",
            data={"email": "jobs@test.local", "nome": "Jobs", "senha": "senha123"},
        )
        cliente.post("/api/login", data={"email": "jobs@test.local", "senha": "senha123"})

        # Conteúdo SPED válido mas incompleto: a importação vai falhar, e é
        # justamente disso que o teste depende — o que importa é o estado
        # gravado ANTES de o processamento terminar.
        resposta = cliente.post(
            "/api/upload-async",
            files={"file": ("parcial.txt", b"|0000|LECD|01012024|31122024|X|00123456000199|\n")},
        )
        assert resposta.status_code == 200
        job_id = resposta.json()["job_id"]

        parametros = json.loads(_linha(referencia, job_id).parametros)
        assert "arquivo_temporario" in parametros, (
            "a rota não registrou o caminho do upload: depois de uma queda, "
            "nada saberia onde procurar o arquivo órfão"
        )
        assert parametros["arquivo"] == "parcial.txt"

    def test_falha_na_recuperacao_nao_impede_a_subida(self, monkeypatch):
        """Escritório sem sistema é pior que job em aberto a mais."""
        from fastapi.testclient import TestClient

        import src.async_jobs as modulo

        def explodir(*_a, **_k):
            raise RuntimeError("banco indisponível")

        monkeypatch.setattr(modulo, "init_async_job_service", explodir)

        from src.dashboard.app import app

        with TestClient(app) as cliente:
            assert cliente.get("/login").status_code == 200

    def test_um_job_em_aberto_nao_sobrevive_a_duas_subidas(self, referencia, monkeypatch):
        """Fecha o cenário completo: reinício, tela honesta, reenvio possível."""
        from fastapi.testclient import TestClient

        monkeypatch.setenv("DATABASE_URL", referencia)
        from src.settings import reset_settings_cache

        reset_settings_cache()

        servico = AsyncJobService(referencia)
        job = servico.criar(tipo="ecd_import")

        from src.dashboard.app import app

        with TestClient(app):
            pass
        with TestClient(app):
            pass

        linha = _linha(referencia, job.id)
        assert linha.status in STATUS_TERMINAIS
        assert linha.status == JobStatus.INTERRUPTED.value


def test_status_do_modelo_documenta_todos_os_estados():
    """O comentário do modelo listava 4 estados; existem 6.

    Comentário desatualizado ao lado da coluna é o que faz alguém escrever uma
    verificação de estado incompleta — foi assim que `cancelled` ficou de fora.
    """
    import inspect

    from src.db import models

    fonte = inspect.getsource(models.AsyncJob)
    for membro in JobStatus:
        assert membro.value in fonte, f"estado {membro.value} ausente do comentário do modelo"


def test_engine_nao_vaza_entre_verificacoes(referencia):
    """Guarda contra o teste virar teste de nada se a fixture mudar."""
    engine = criar_engine(referencia)
    try:
        with engine.connect() as conexao:
            tabelas = sqlalchemy.inspect(conexao).get_table_names()
        assert "async_jobs" in tabelas
    finally:
        engine.dispose()
