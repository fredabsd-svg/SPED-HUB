"""As tabelas oficiais do IBS/CBS: procedência, geração e conferência.

O que estes testes protegem:

  * **o JSON é o que o script produz da planilha versionada** — trocar a
    planilha sem regerar, ou editar o JSON à mão, derruba o CI (§1.9). Sem
    isso a tabela seria "oficial" só na primeira vez;
  * **a leitura é por nome de coluna** — a planilha tem 82 colunas e dezenas
    vazias; uma coluna inserida no meio deslocaria tudo sem erro nenhum;
  * **a conferência devolve problema, nunca levanta** — nota mal classificada
    já foi autorizada e está no arquivo do cliente; recusá-la na importação
    faria o escritório perder o documento junto com o aviso;
  * **a data de publicação viaja com a tabela** — tabela velha que responde
    como se fosse a atual erra em silêncio, e o erro só aparece na rejeição.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

from src.documentos import tabelas_ibscbs
from src.documentos.tabelas_ibscbs import (
    ALIQUOTAS_PADRAO,
    ARQUIVO,
    aliquotas_padrao,
    conferir,
    tabelas,
)

REPO = Path(__file__).resolve().parent.parent
GERADOR = REPO / "scripts" / "gerar_tabelas_ibscbs.py"


def _gerador():
    spec = importlib.util.spec_from_file_location("gerador_tabelas", GERADOR)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class ItemFalso:
    """Um item com os campos que a conferência olha, e nada mais."""

    def __init__(self, **campos):
        self.cst_ibscbs = campos.pop("cst_ibscbs", "000")
        self.class_trib_ibscbs = campos.pop("class_trib_ibscbs", "000001")
        self.valor_ibs_uf = campos.pop("valor_ibs_uf", 0.70)
        self.valor_ibs_mun = campos.pop("valor_ibs_mun", 0.30)
        self.valor_cbs = campos.pop("valor_cbs", 9.00)
        for nome, valor in campos.items():
            setattr(self, nome, valor)

    def __getattr__(self, nome):
        # Campo que o documento não trouxe é zero, como no modelo.
        return 0.0


EMISSAO = dt.date(2026, 7, 30)


# ── §1.9: o arquivo é derivado, não mantido ────────────────────────────────


class TestGeracao:
    def test_o_json_versionado_e_o_que_o_script_produz(self):
        """Editar o JSON à mão, ou trocar a planilha sem regerar, cai aqui.

        É a trava que faz "tabela oficial" continuar verdadeiro depois do
        primeiro commit. Sem ela, o arquivo viraria uma cópia manual como
        qualquer outra — que é exatamente o que a §1.9 existe para impedir.
        """
        gerador = _gerador()

        assert gerador.serializar(gerador.gerar()) == ARQUIVO.read_text("utf-8")

    def test_o_json_declara_de_onde_veio(self):
        dados = json.loads(ARQUIVO.read_text("utf-8"))

        assert dados["aviso"].startswith("ARQUIVO GERADO")
        assert dados["gerado_por"] == "scripts/gerar_tabelas_ibscbs.py"
        for fonte in dados["fontes"]:
            assert (REPO / fonte["arquivo"]).is_file(), fonte

    def test_a_planilha_oficial_esta_versionada(self):
        """Sem a fonte no repositório a geração não é reproduzível."""
        planilhas = sorted((REPO / "dados" / "oficiais").glob("*.xlsx"))

        assert [p.name for p in planilhas] == [
            "cClassTrib_2026-06-22.xlsx",
            "cCredPres_2026-06-22.xlsx",
        ]

    def test_a_data_vem_do_nome_do_arquivo_nao_do_disco(self):
        """`mtime` seria a data do clone, e o sistema diria que é de hoje."""
        gerador = _gerador()

        assert gerador._publicado_em(Path("cClassTrib_2026-06-22.xlsx")) == "2026-06-22"

    def test_nome_sem_data_e_recusado(self):
        gerador = _gerador()

        with pytest.raises(SystemExit):
            gerador._publicado_em(Path("cClassTrib.xlsx"))

    def test_coluna_ausente_recusa_em_vez_de_devolver_vazio(self):
        """Ler por posição devolveria a coluna errada calada."""
        gerador = _gerador()

        with pytest.raises(SystemExit) as erro:
            gerador._coluna(["CST-IBS/CBS", "cClassTrib"], "pRedIBS")

        assert "pRedIBS" in str(erro.value)


# ── O conteúdo, conferido contra o documento oficial ───────────────────────


class TestConteudo:
    def test_os_dezoito_cst_da_tabela_oficial(self):
        assert sorted(tabelas().cst) == [
            "000", "010", "011", "200", "220", "221", "222", "400", "410",
            "510", "515", "550", "620", "800", "810", "811", "820", "830",
        ]  # fmt: skip

    def test_o_cst_do_monofasico_exige_o_grupo_monofasico(self):
        """E **não** o `gIBSCBS` — são alternativas, não complementos."""
        registro = tabelas().cst["620"]

        assert registro["exige"]["gIBSCBSMono"] is True
        assert registro["exige"]["gIBSCBS"] is False

    def test_o_diferimento_com_reducao_exige_os_dois_grupos(self):
        registro = tabelas().cst["515"]

        assert registro["exige"]["gDif"] is True
        assert registro["exige"]["gRed"] is True

    def test_a_reducao_do_codigo_vem_da_tabela(self):
        """40% para transporte coletivo — art. 285 da LC 214/2025."""
        registro = tabelas().class_trib["200049"]

        assert registro["reducao_ibs"] == 40.0
        assert registro["reducao_cbs"] == 40.0

    def test_cada_classificacao_comeca_pelo_cst_dela(self):
        """A regra do próprio IT: os três primeiros dígitos são o CST."""
        divergentes = {
            codigo: registro["cst"]
            for codigo, registro in tabelas().class_trib.items()
            if not codigo.startswith(registro["cst"])
        }

        assert not divergentes

    def test_toda_classificacao_aponta_para_um_cst_existente(self):
        tab = tabelas()
        orfas = {c: r["cst"] for c, r in tab.class_trib.items() if r["cst"] not in tab.cst}

        assert not orfas

    def test_a_publicacao_e_a_mais_antiga_das_fontes(self, tmp_path, monkeypatch):
        """Anunciar a mais nova esconderia a planilha que ficou para trás.

        As duas planilhas de hoje têm a **mesma** data, então comparar com a
        tabela real não distingue mínimo de máximo — o teste passaria com a
        escolha invertida. Por isso as datas são plantadas diferentes.
        """
        dados = json.loads(ARQUIVO.read_text("utf-8"))
        dados["fontes"] = [
            {"arquivo": "a.xlsx", "publicado_em": "2026-06-22"},
            {"arquivo": "b.xlsx", "publicado_em": "2024-01-01"},
        ]
        falso = tmp_path / "tabelas.json"
        falso.write_text(json.dumps(dados, ensure_ascii=False), "utf-8")
        monkeypatch.setattr(tabelas_ibscbs, "ARQUIVO", falso)
        tabelas.cache_clear()

        try:
            assert tabelas().publicada_em == "2024-01-01"
        finally:
            tabelas.cache_clear()


class TestAliquotasPadrao:
    def test_em_2026_a_parcela_municipal_e_zero(self):
        """Repartir "meio a meio" mandaria dinheiro para o ente errado.

        O item 05 do IT é explícito: em 2026 os 0,1% do IBS são todos
        estaduais. A repartição igual só começa em 2027.
        """
        assert aliquotas_padrao(2026) == {"ibs_uf": 0.1, "ibs_mun": 0.0, "cbs": 0.9}

    def test_a_cbs_de_2027_aguarda_legislacao(self):
        """`None` é diferente de zero: zero seria uma alíquota."""
        assert aliquotas_padrao(2027)["cbs"] is None
        assert aliquotas_padrao(2027)["ibs_uf"] == 0.05

    def test_ano_sem_aliquota_fixada_devolve_nada(self):
        assert aliquotas_padrao(2033) is None

    def test_a_tabela_cobre_de_2026_a_2028(self):
        assert sorted(ALIQUOTAS_PADRAO) == [2026, 2027, 2028]


# ── A conferência ──────────────────────────────────────────────────────────


class TestConferencia:
    def test_item_bem_classificado_nao_gera_problema(self):
        assert conferir(ItemFalso(), data_emissao=EMISSAO) == []

    def test_item_sem_classificacao_nenhuma_e_ignorado(self):
        """Nota anterior a 2026 não traz os grupos — não é defeito dela."""
        item = ItemFalso(cst_ibscbs=None, class_trib_ibscbs=None)

        assert conferir(item, data_emissao=dt.date(2025, 5, 1)) == []

    def test_cst_inexistente_e_apontado_com_a_procedencia(self):
        problemas = conferir(ItemFalso(cst_ibscbs="999"), data_emissao=EMISSAO)

        assert any("999 não está na tabela oficial" in p for p in problemas)
        assert any(tabelas().publicada_em in p for p in problemas)

    def test_classificacao_inexistente_e_apontada(self):
        item = ItemFalso(class_trib_ibscbs="999999")

        assert any("999999 não está na tabela" in p for p in conferir(item, data_emissao=EMISSAO))

    def test_par_que_nao_casa_e_apontado(self):
        """CST 200 com classificação de tributação integral: a SEFAZ recusa."""
        item = ItemFalso(cst_ibscbs="200", class_trib_ibscbs="000001")

        problemas = conferir(item, data_emissao=EMISSAO)

        assert any("pertence ao CST 000" in p and "declara CST 200" in p for p in problemas)

    def test_codigo_fora_da_vigencia_e_apontado_com_as_datas(self):
        problemas = conferir(ItemFalso(), data_emissao=dt.date(2025, 12, 31))

        achado = next(p for p in problemas if "não estava vigente" in p)
        assert "31/12/2025" in achado
        assert "2026-01-01" in achado

    def test_sem_data_de_emissao_a_vigencia_nao_e_cobrada(self):
        """Documento sem data não é prova de vigência errada."""
        assert conferir(ItemFalso(), data_emissao=None) == []

    def test_codigo_proibido_no_modelo_e_apontado(self):
        item = ItemFalso(cst_ibscbs="620", class_trib_ibscbs="620001", valor_ibs_mono=5.0)

        problemas = conferir(item, data_emissao=EMISSAO, modelo="65")

        assert any("não é permitido no modelo 65" in p for p in problemas)

    def test_o_mesmo_codigo_passa_no_modelo_em_que_ele_vale(self):
        item = ItemFalso(cst_ibscbs="620", class_trib_ibscbs="620001", valor_ibs_mono=5.0)

        assert conferir(item, data_emissao=EMISSAO, modelo="55") == []

    def test_grupo_exigido_e_ausente_e_apontado(self):
        """CST 510 exige `gDif`; sem ele o documento é recusado."""
        item = ItemFalso(cst_ibscbs="510", class_trib_ibscbs="510001")

        problemas = conferir(item, data_emissao=EMISSAO)

        assert any("exige o grupo gDif" in p for p in problemas)

    def test_grupo_exigido_e_presente_nao_gera_problema(self):
        item = ItemFalso(cst_ibscbs="510", class_trib_ibscbs="510001", valor_diferido_cbs=4.50)

        assert not [p for p in conferir(item, data_emissao=EMISSAO) if "gDif" in p]

    def test_um_item_pode_ter_mais_de_um_problema(self):
        """Devolver só o primeiro faria a correção virar um jogo de adivinhação."""
        item = ItemFalso(cst_ibscbs="620", class_trib_ibscbs="000001")

        assert len(conferir(item, data_emissao=EMISSAO)) >= 2
