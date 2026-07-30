"""O worker não pode girar em vazio quando a fila de tarefas quebra.

O laço do worker pegava a espera da fila com `except Exception: continue`. A
intenção era o `Empty` de `get(timeout=1)` — a fila vazia, o caso normal. Mas
o `except` engolia também a fila **quebrada**, e aí o `timeout=1` não se
aplica: o erro é levantado na hora.

O resultado é um laço a plena velocidade — medido em 1,2 milhão de voltas por
segundo, onde o código espera uma. São quatro processos de worker no deploy
padrão: quatro núcleos a 100%, sem uma linha de log, até alguém notar a
máquina lenta e ir procurar.

O `process_results()`, no mesmo arquivo, já distinguia os dois casos. O laço
do worker é que não tinha acompanhado.
"""

from __future__ import annotations

import multiprocessing
import time
from queue import Empty

import pytest

from src.worker_queue import WorkerQueue


class FilaQuebrada:
    """Fila cujo `get` falha na hora, como a de um worker órfão.

    `multiprocessing.Queue` fechada levanta `ValueError`; com o processo pai
    morto, `EOFError` ou `OSError`. Nenhuma delas é `Empty`, e nenhuma respeita
    o `timeout`.

    O `teto` existe para o teste não pendurar a suíte contra código que gira em
    vazio: passado ele, a fila entrega a pílula de encerramento e o laço sai de
    qualquer jeito. Quem afere o defeito é a contagem de tentativas, não o
    tempo — assim o teste falha em segundos em vez de travar.
    """

    def __init__(self, erro: BaseException, teto: int = 500):
        self.erro = erro
        self.teto = teto
        self.tentativas = 0

    def get(self, timeout=None):
        self.tentativas += 1
        if self.tentativas > self.teto:
            return None  # pílula: encerra o laço para o teste não pendurar
        raise self.erro

    def put(self, item, timeout=None):  # pragma: no cover - não usado
        raise self.erro


class FilaVaziaEDepoisEncerra:
    """Fica vazia por N chamadas e então entrega a pílula de encerramento."""

    def __init__(self, vazias: int):
        self.restantes = vazias
        self.tentativas = 0

    def get(self, timeout=None):
        self.tentativas += 1
        if self.restantes > 0:
            self.restantes -= 1
            raise Empty
        return None  # poison pill

    def put(self, item, timeout=None):  # pragma: no cover - não usado
        raise AssertionError("o laço não devolve tarefa nestes testes")


def fila_em_operacao() -> WorkerQueue:
    """Uma `WorkerQueue` no estado de quem está rodando, sem subir processos.

    Sem marcar `_running`, o worker sai já na primeira volta pela via de
    desligamento — e o teste passaria sem exercitar nada.
    """
    queue = WorkerQueue(num_workers=1)
    queue._running.value = True
    return queue


@pytest.mark.parametrize(
    "erro",
    [
        ValueError("Queue is closed"),
        EOFError(),
        OSError("handle is closed"),
        BrokenPipeError(),
    ],
    ids=["fechada", "eof", "oserror", "pipe-quebrado"],
)
def test_fila_quebrada_encerra_o_worker_em_vez_de_girar(erro):
    """Sem isto, cada worker consome um núcleo inteiro indefinidamente."""
    fila = FilaQuebrada(erro)
    queue = fila_em_operacao()

    inicio = time.perf_counter()
    queue._worker_loop(0, fila, multiprocessing.Queue(), queue._running)
    decorrido = time.perf_counter() - inicio

    assert fila.tentativas == 1, (
        f"o worker tentou {fila.tentativas} vezes numa fila que falha na hora — "
        "é o laço em vazio que come o núcleo"
    )
    assert decorrido < 2, "o laço não deveria nem chegar a esperar"


def test_fila_quebrada_deixa_rastro_no_log(caplog):
    """Girar em silêncio é o que torna a falha difícil de achar."""
    queue = fila_em_operacao()
    with caplog.at_level("ERROR", logger="src.worker_queue"):
        queue._worker_loop(7, FilaQuebrada(EOFError()), multiprocessing.Queue(), queue._running)

    assert caplog.records, "o worker encerrou sem registrar por quê"
    # A mensagem formatada, não o `caplog.text` inteiro: o traceback embutido
    # tem números de linha e endereços, e um "7" solto ali dava por satisfeita
    # uma mensagem que não identificava worker nenhum.
    mensagens = [r.getMessage() for r in caplog.records]
    assert any("7" in m for m in mensagens), f"o log não diz qual worker caiu: {mensagens}"
    assert any(r.exc_info for r in caplog.records), "o log não traz a exceção"


def test_fila_vazia_continua_esperando():
    """O caso normal não pode ser confundido com o da fila quebrada."""
    fila = FilaVaziaEDepoisEncerra(vazias=3)
    queue = fila_em_operacao()

    queue._worker_loop(0, fila, multiprocessing.Queue(), queue._running)

    # 3 vazias + a pílula de encerramento.
    assert fila.tentativas == 4, "o worker desistiu com a fila apenas vazia"


def test_worker_encerra_quando_a_fila_e_desligada_sem_pilula():
    """A pílula pode não chegar; `_running` é a segunda via.

    `shutdown()` põe uma pílula por worker, mas o `put` tem `timeout=1` e a
    exceção era descartada em silêncio: com a fila cheia, o worker ficava
    esperando um encerramento que nunca vinha, até o `terminate()`.
    """
    fila = FilaVaziaEDepoisEncerra(vazias=10_000)
    queue = fila_em_operacao()
    queue._running.value = False  # foi o que o shutdown fez

    inicio = time.perf_counter()
    queue._worker_loop(0, fila, multiprocessing.Queue(), queue._running)

    assert fila.tentativas < 10, (
        f"o worker fez {fila.tentativas} tentativas depois de a fila ser "
        "desligada — não está olhando para `_running`"
    )
    assert time.perf_counter() - inicio < 2


def test_shutdown_registra_pilula_nao_entregue(caplog):
    """Descartar a exceção do `put` escondia o encerramento incompleto."""
    queue = WorkerQueue(num_workers=1)
    queue.start()
    try:
        queue._task_queue = FilaQuebrada(ValueError("cheia"))
        with caplog.at_level("WARNING", logger="src.worker_queue"):
            queue.shutdown(wait=False)
        assert caplog.records, "pílula não entregue passou em silêncio"
    finally:
        queue._running.value = False
