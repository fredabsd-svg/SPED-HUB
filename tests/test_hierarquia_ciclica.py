"""Plano de contas com hierarquia cíclica não pode travar o dashboard.

`ServicoDashboard.get_composicao_ativo` sobe a hierarquia do plano de contas
por `COD_CTA_SUP` para agrupar as contas analíticas no grupo de nível 2. O
laço não tinha trava de ciclo.

Como a hierarquia vem do arquivo do cliente, bastava uma ECD em que uma conta
fosse a própria sintética (ou A→B→A) para o laço rodar para sempre. O uvicorn
atende num único event loop: o dashboard parava de responder para **todos** os
usuários do escritório, não só para quem importou. Nada matava a requisição —
o processo ficava girando até alguém reiniciar.

Foi assim que este defeito foi encontrado: os testes de navegador falhavam com
"servidor para de responder no meio da suíte", sintoma registrado no ADR 0004
como não diagnosticado. A causa não era o harness.
"""

from __future__ import annotations

import datetime
import threading

import pytest
from sqlalchemy import select

from src.dashboard.services import DashboardService
from src.db.models import ECD, Empresa, PlanoConta, criar_engine, get_session, init_db

TEMPO_LIMITE = 20.0


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(url=f"sqlite:///{tmp_path / 'ciclo.db'}")
    init_db(engine)
    with get_session(engine) as s:
        yield s


def _montar_ecd_com_ciclo(sessao, ciclo: str) -> int:
    """Cria uma ECD cujo plano de contas tem ciclo na hierarquia.

    ``ciclo='auto'``  → a conta 1 é a própria sintética (COD_CTA_SUP = 1).
    ``ciclo='mutuo'`` → 1 aponta para 2 e 2 aponta para 1.
    """
    empresa = Empresa(cnpj="00123456000199", nome="EMPRESA CICLO LTDA", uf="SP")
    sessao.add(empresa)
    sessao.flush()

    ecd = ECD(
        empresa_id=empresa.id,
        leiaute="009",
        dt_ini=datetime.date(2024, 1, 1),
        dt_fin=datetime.date(2024, 12, 31),
        nome_arquivo="ciclo.txt",
        hash_arquivo=f"hash-{ciclo}",
    )
    sessao.add(ecd)
    sessao.flush()

    if ciclo == "auto":
        contas = [("1", "1", 3, "CONTA UM")]
    else:
        contas = [("1", "2", 3, "CONTA UM"), ("2", "1", 3, "CONTA DOIS")]

    for cod, sup, nivel, nome in contas:
        sessao.add(
            PlanoConta(
                ecd_id=ecd.id,
                cod_cta=cod,
                cod_cta_sup=sup,
                nivel=nivel,
                nome_cta=nome,
                ind_cta="A",
                cod_nat="01",
            )
        )
    sessao.commit()
    return ecd.id


def _chamar_com_limite(funcao, limite: float = TEMPO_LIMITE):
    """Executa `funcao` numa thread e falha se ela não terminar no prazo.

    Um laço infinito não levanta exceção nem consome memória — ele só nunca
    volta. Sem limite de tempo este teste travaria a suíte inteira, que é
    exatamente o que o defeito fazia com o servidor.
    """
    resultado: dict = {}

    def alvo():
        try:
            resultado["valor"] = funcao()
        except BaseException as exc:  # noqa: BLE001 - repassado abaixo
            resultado["erro"] = exc

    t = threading.Thread(target=alvo, daemon=True)
    t.start()
    t.join(limite)
    if t.is_alive():
        pytest.fail(
            f"get_composicao_ativo não retornou em {limite:.0f}s: a subida pela "
            "hierarquia entrou em laço infinito. Em produção isso trava o event "
            "loop do uvicorn e o dashboard para de responder para todo mundo."
        )
    if "erro" in resultado:
        raise resultado["erro"]
    return resultado["valor"]


@pytest.mark.parametrize("ciclo", ["auto", "mutuo"])
def test_composicao_do_ativo_termina_com_hierarquia_ciclica(sessao, ciclo):
    ecd_id = _montar_ecd_com_ciclo(sessao, ciclo)
    servico = DashboardService(sessao, ecd_id)

    dados = _chamar_com_limite(servico.get_composicao_ativo)

    assert set(dados) == {"labels", "valores"}
    assert len(dados["labels"]) == len(dados["valores"])


def test_dados_permanecem_intactos(sessao):
    """A trava não pode alterar o plano de contas — ela só para de subir."""
    ecd_id = _montar_ecd_com_ciclo(sessao, "auto")
    servico = DashboardService(sessao, ecd_id)
    _chamar_com_limite(servico.get_composicao_ativo)

    conta = sessao.execute(
        select(PlanoConta).where(PlanoConta.ecd_id == ecd_id, PlanoConta.cod_cta == "1")
    ).scalar_one()
    assert conta.cod_cta_sup == "1", "o serviço de leitura não pode reescrever o plano"


def test_ciclo_e_registrado_em_log(sessao, caplog):
    """Silenciar o ciclo esconderia um plano de contas inválido do cliente."""
    ecd_id = _montar_ecd_com_ciclo(sessao, "auto")
    servico = DashboardService(sessao, ecd_id)

    with caplog.at_level("WARNING", logger="sped-hub.dashboard.services"):
        _chamar_com_limite(servico.get_composicao_ativo)

    assert any("ciclo" in r.message.lower() for r in caplog.records), (
        "o ciclo foi contornado em silêncio; ninguém saberia que a ECD do "
        "cliente tem hierarquia inválida"
    )


class TestImportacaoRecusaCiclo:
    """A importação recusa arquivo com hierarquia cíclica (ADR 0006).

    Fecha a origem de verdade: até aqui o arquivo entrava no banco com a
    hierarquia inválida e só a validação (h) — que roda quando alguém pede —
    acusava. Agora a escrituração nem entra, e a transação única (§6.1)
    garante que NADA dela fica: nem empresa, nem ECD, nem plano.
    """

    @staticmethod
    def _arquivo_ecd(tmp_path, linhas_i050):
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
        arq = tmp_path / "ciclo.txt"
        arq.write_text("\n".join(linhas) + "\n", encoding="utf-8")
        return arq

    def _importar(self, tmp_path, linhas_i050):
        from src.ecd_importer import ECDImportService

        engine = criar_engine(f"sqlite:///{tmp_path / 'imp.db'}")
        init_db(engine)
        sessao = get_session(engine)
        arq = self._arquivo_ecd(tmp_path, linhas_i050)
        try:
            return sessao, ECDImportService(sessao).importar(arq)
        except Exception:
            sessao.rollback()
            raise

    def test_auto_ciclo_e_recusado_sem_deixar_rastro(self, tmp_path):
        import pytest as _pytest

        from src.db.models import ECD, Empresa
        from src.ecd_importer import ECDImportError, ECDImportService

        engine = criar_engine(f"sqlite:///{tmp_path / 'imp.db'}")
        init_db(engine)
        sessao = get_session(engine)
        arq = self._arquivo_ecd(tmp_path, ["|I050|01012024|01|A|3|1|1|CONTA UM|"])

        with _pytest.raises(ECDImportError) as exc:
            ECDImportService(sessao).importar(arq)
        assert "ciclo" in str(exc.value).lower()
        assert "nada foi importado" in str(exc.value).lower()

        sessao.rollback()
        assert (
            sessao.execute(select(ECD)).first() is None
        ), "a ECD recusada deixou rastro no banco — a transação não reverteu (§6.1)"
        assert sessao.execute(select(Empresa)).first() is None
        assert sessao.execute(select(PlanoConta)).first() is None

    def test_ciclo_mutuo_recusado_com_caminho_na_mensagem(self, tmp_path):
        import pytest as _pytest

        from src.ecd_importer import ECDImportError

        with _pytest.raises(ECDImportError) as exc:
            self._importar(
                tmp_path,
                ["|I050|01012024|01|A|3|1|2|CONTA UM|", "|I050|01012024|01|A|3|2|1|CONTA DOIS|"],
            )
        # O caminho aparece na mensagem: quem recebe o erro precisa saber
        # QUAL conta corrigir no sistema de origem.
        assert "1" in str(exc.value) and "2" in str(exc.value)

    def test_hierarquia_valida_continua_importando(self, tmp_path):
        sessao, resultado = self._importar(
            tmp_path,
            [
                "|I050|01012024|01|S|1|1||ATIVO|",
                "|I050|01012024|01|A|2|1.1|1|CAIXA|",
            ],
        )
        assert resultado.contas == 2
