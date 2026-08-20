"""O CNPJ alfanumérico, conferido contra a cartilha da Receita.

O que ancora este arquivo são duas coisas que não saíram da nossa cabeça:

  * o exemplo resolvido pela própria Receita — `12.ABC.345/01DE` com DV `35`,
    da pergunta 14 de "CNPJ Alfanumérico — Perguntas e Respostas";
  * a promessa oficial de que "os atuais números permanecerão válidos assim
    como os seus dígitos verificadores", que vira um teste: para entrada só
    de algarismos, o algoritmo novo tem de dar exatamente o resultado do
    antigo. É essa propriedade que impede uma reescrita de invalidar a base
    inteira de uma vez.
"""

from __future__ import annotations

import pytest

from src.cnpj import (
    alfanumerico,
    bem_formado,
    digitos_verificadores,
    formatar,
    normalizar,
    valido,
)
from src.logging_config import sanitizar

# Pergunta 14 da cartilha, com o cálculo mostrado passo a passo.
EXEMPLO_DA_RECEITA = ("12ABC34501DE", "35")


class TestOExemploDaReceita:
    def test_o_dv_do_exemplo_oficial(self):
        base, dv = EXEMPLO_DA_RECEITA

        assert digitos_verificadores(base) == dv

    def test_o_exemplo_completo_e_valido(self):
        assert valido("".join(EXEMPLO_DA_RECEITA))

    def test_a_pontuacao_do_formato_nao_atrapalha(self):
        assert valido("12.ABC.345/01DE-35")

    def test_um_digito_trocado_invalida(self):
        """Sem isto, um `valido` que devolvesse sempre True passaria acima."""
        assert not valido("12ABC34501DE36")

    def test_uma_letra_trocada_invalida(self):
        assert not valido("12ABD34501DE35")


class TestCompatibilidadeComOFormatoAntigo:
    """ "Os atuais números permanecerão válidos assim como os seus DV"."""

    # CNPJ numéricos com DV correto, dos próprios testes do projeto.
    NUMERICOS = ("12345678000195", "98765432000198", "11111111000191")

    @pytest.mark.parametrize("cnpj", NUMERICOS)
    def test_cnpj_numerico_continua_valido(self, cnpj):
        assert valido(cnpj)

    @pytest.mark.parametrize("cnpj", NUMERICOS)
    def test_o_calculo_novo_reproduz_o_dv_de_sempre(self, cnpj):
        assert digitos_verificadores(cnpj[:12]) == cnpj[12:]

    @pytest.mark.parametrize("cnpj", NUMERICOS)
    def test_numerico_nao_e_alfanumerico(self, cnpj):
        assert not alfanumerico(cnpj)

    # Bases cujo resto do módulo 11 é 0 ou 1 — o caso que a cartilha não
    # escreve.  Sem a regra `resto < 2 → 0`, o dígito seria "11" ou "10", com
    # duas casas onde cabe uma, e o CNPJ inteiro passaria a ser inválido.
    RESTO_ABAIXO_DE_DOIS = (
        ("000000000000", "00"),
        ("000000000006", "04"),
        ("000000000014", "06"),
        ("AB0000000004", "05"),
    )

    @pytest.mark.parametrize(("base", "dv"), RESTO_ABAIXO_DE_DOIS)
    def test_o_dv_do_resto_abaixo_de_dois_tem_uma_casa(self, base, dv):
        calculado = digitos_verificadores(base)

        assert calculado == dv
        assert len(calculado) == 2, "dois dígitos, não 10 ou 11 concatenados"
        assert valido(base + calculado)

    def test_o_caso_do_resto_abaixo_de_dois_e_mesmo_exercitado(self):
        """Guarda contra o teste acima virar teste de nada.

        Se nenhuma destas bases caísse no ramo, os casos passariam pelo
        caminho comum e a regra ficaria sem cobertura nenhuma.
        """
        from src.cnpj import _PESOS_PRIMEIRO

        restos = [
            sum((ord(c) - 48) * peso for c, peso in zip(base, _PESOS_PRIMEIRO, strict=True)) % 11
            for base, _ in self.RESTO_ABAIXO_DE_DOIS
        ]

        assert all(resto < 2 for resto in restos), restos


class TestNormalizacao:
    """O ponto perigoso: tirar "tudo que não é dígito" troca a empresa."""

    def test_a_pontuacao_sai_e_as_letras_ficam(self):
        assert normalizar("12.ABC.345/01DE-35") == "12ABC34501DE35"

    def test_minuscula_vira_maiuscula(self):
        assert normalizar("12abc34501de35") == "12ABC34501DE35"

    def test_zero_a_esquerda_e_preservado(self):
        assert normalizar("00.123.456/0001-99") == "00123456000199"

    def test_vazio_devolve_vazio(self):
        assert normalizar(None) == "" and normalizar("") == ""

    def test_tirar_nao_digitos_daria_outra_empresa(self):
        """A demonstração do defeito, em uma linha.

        `re.sub(r"\\D", "", ...)` sobre um CNPJ alfanumérico devolve oito
        posições — que, completadas, são a inscrição de outra pessoa jurídica,
        com aparência perfeita.
        """
        import re

        assert re.sub(r"\D", "", "12ABC34501DE35") != normalizar("12ABC34501DE35")

    def test_bem_formado_recusa_letra_no_digito_verificador(self):
        assert not bem_formado("12ABC34501DEAB")

    def test_bem_formado_recusa_comprimento_errado(self):
        assert not bem_formado("12ABC34501DE3")

    def test_formatar_devolve_a_mascara_do_formato(self):
        assert formatar("12ABC34501DE35") == "12.ABC.345/01DE-35"


class TestLogNaoVazaOCnpjNovo:
    """O sanitizador olhava só para algarismos, e deixava o formato novo passar."""

    def test_o_cnpj_alfanumerico_e_mascarado(self):
        saida = sanitizar("empresa 12.ABC.345/01DE-35 importada")

        assert "12.ABC" not in saida
        assert "01DE-35" in saida, "a cauda fica, para casar a linha com o registro"

    def test_o_cnpj_alfanumerico_sem_pontuacao_tambem(self):
        assert "12ABC345" not in sanitizar("empresa 12ABC34501DE35 importada")

    def test_o_cnpj_numerico_continua_mascarado(self):
        saida = sanitizar("empresa 12.345.678/0001-95 importada")

        assert "12.345" not in saida
        assert "0001-95" in saida


class TestOsTiposDoLeiauteDaEcd:
    """O `tipo` do yml diz ao parser como ler, e nem sempre bate com o manual.

    Onde o manual diz "C" e o arquivo dizia "N", o parser perdia o valor: o
    CNPJ do `0000` virava `None` no formato alfanumérico e `123456000199.0`
    no numérico — sem os zeros à esquerda. Onde o manual diz "N" e aqui está
    "C", a divergência é deliberada e está declarada no cabeçalho do yml.

    Este teste existe para que uma divergência nova não entre calada.
    """

    # Manual do Leiaute 9 da ECD, ADE Cofis nº 01/2026.  Lidas à mão, uma a
    # uma, porque a extração automática do PDF errou o `IND_LCTO`.
    DIVERGENCIAS_DECLARADAS = {
        ("0000", "TIP_ECD"),
        ("0000", "IND_CENTRALIZADA"),
        ("J210", "IND_TIP"),
    }

    @staticmethod
    def _leiaute() -> dict:
        from pathlib import Path

        import yaml

        return yaml.safe_load(Path("src/layouts/ecd_v9.yml").read_text(encoding="utf-8"))

    def test_o_cnpj_do_0000_e_texto(self):
        """ "06 CNPJ [...] C 014" — e é o campo que cria a empresa no banco."""
        campos = {c["nome"]: c for c in self._leiaute()["registros"]["0000"]["campos"]}

        assert campos["CNPJ"]["tipo"] == "C"

    def test_o_cnpj_da_scp_tambem(self):
        campos = {c["nome"]: c for c in self._leiaute()["registros"]["0000"]["campos"]}

        assert campos["COD_SCP"]["tipo"] == "C"

    def test_o_num_arq_da_partida_e_texto(self):
        """ "Número, Código ou caminho de localização" — caminho não é número."""
        campos = {c["nome"]: c for c in self._leiaute()["registros"]["I250"]["campos"]}

        assert campos["NUM_ARQ"]["tipo"] == "C"

    def test_as_divergencias_deliberadas_estao_no_cabecalho(self):
        """Cada uma precisa estar escrita no arquivo, com a razão."""
        from pathlib import Path

        cabecalho = Path("src/layouts/ecd_v9.yml").read_text(encoding="utf-8").split("versao:")[0]

        for _registro, campo in self.DIVERGENCIAS_DECLARADAS:
            assert campo in cabecalho, f"{campo} diverge do manual sem estar declarado"
