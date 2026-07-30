"""Verificação automática das regras marcadas **[CI]** em `REGRAS-DO-PROJETO.md`.

O documento de regras afirma, logo no cabeçalho, que "uma regra sem marca não
existe" e que regra que ninguém cobra é promessa vazia.  Este arquivo é o que
torna essa afirmação verdadeira: cada seção marcada `[CI]` precisa aparecer no
`REGISTRO_CI` abaixo apontando para o teste que a cobra, e o primeiro teste
deste arquivo falha se alguém marcar uma regra nova como `[CI]` sem
implementar a verificação.

Sem essa trava o documento de regras seria exatamente o defeito que a REGRA
1.1 existe para evitar: documentação descrevendo o que se pretende fazer, não
o que o código faz.
"""

from __future__ import annotations

import ast
import datetime as dt
import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
REGRAS = REPO / "REGRAS-DO-PROJETO.md"
STATUS = REPO / "docs" / "status.md"
MODULES = REPO / "docs" / "modules"
ARCHITECTURE = REPO / "docs" / "architecture"
ENV_EXAMPLE = REPO / ".env.example"
PYPROJECT = REPO / "pyproject.toml"
ROADMAP = REPO / "docs" / "roadmap.md"

# Onde cada regra [CI] é efetivamente cobrada.  Chave: número da seção.
# Valor: arquivo::teste que quebra quando a regra é violada.
#
# Regra [CI] sem entrada aqui derruba `test_toda_regra_ci_tem_verificacao`.
REGISTRO_CI = {
    "1.2": "tests/test_regras_projeto.py::TestDocumentacao::test_estrutura_de_documentacao_existe",
    "1.4": "tests/test_regras_projeto.py::TestDocumentacao::test_passivo_de_modulos_cobre_todos_os_modulos",
    "1.8": "tests/test_regras_projeto.py::TestDocumentacao::test_fase_concluida_aponta_teste_existente",
    "1.9": "tests/test_regras_projeto.py::TestDocumentacao::test_arquivo_gerado_declara_fonte_existente",
    "1.12": "tests/test_regras_projeto.py::TestDocumentacao::test_cabecalho_de_arquitetura",
    "1.13": "tests/test_regras_projeto.py::TestRoadmap",
    "2.1": "tests/test_regras_projeto.py::TestConfiguracao::test_ninguem_le_ambiente_fora_de_settings",
    # A §2.2 tem duas metades: a variável chega ao `Settings` e o campo é
    # lido de fato.  A classe inteira é o alvo porque só uma delas não basta.
    "2.2": "tests/test_regras_projeto.py::TestConfiguracao",
    # A §3.4 é cobrada em dois lugares: o teste multibackend em si, e a
    # garantia de que todo teste dependente de Postgres roda no job certo.
    "3.4": "tests/test_multibackend.py",
    "3.5": "tests/test_regras_projeto.py::TestTestes::test_marcador_e2e_fora_da_execucao_padrao",
    "4.1": "tests/test_regras_projeto.py::TestBuild::test_lint_e_format_pinados",
    "4.2": "tests/test_regras_projeto.py::TestBuild::test_regras_de_lint_declaradas",
    "4.3": "tests/test_vendor_assets.py::TestSemCDN",
    "4.4": "tests/test_vendor_assets.py::TestVersaoUnica",
    "4.5": "tests/test_deploy_config.py",
    "5.4": "tests/test_hardening.py::TestLogSemPII",
    "6.2": "tests/test_migrations.py",
    "6.3": "tests/test_multibackend.py::TestRelatoriosIdenticos",
}

MARCAS = ("[CI]", "[REVISÃO]", "[ADOÇÃO]")

ESTADOS_DE_FASE = {"não iniciada", "em andamento", "concluída", "bloqueada"}
ESTADOS_DE_ARQUITETURA = {"especificado", "parcialmente implementado", "implementado"}


def variaveis_reservadas(texto: str) -> set[str]:
    """Variáveis do `.env.example` marcadas RESERVADO no comentário logo acima.

    O bloco de comentário considerado é só o **contíguo** imediatamente antes
    da linha da variável.  Uma janela de N caracteres para trás, que era a
    implementação anterior, contamina as variáveis seguintes: as três linhas
    depois de `SPED_HUB_SECRET_KEY` (`HOST`, `PORT`, `RELOAD`) ficavam
    dispensadas da §2.2 sem que ninguém tivesse dito isso.
    """
    reservadas: set[str] = set()
    linhas = texto.splitlines()
    for i, linha in enumerate(linhas):
        achado = re.match(r"^#?\s*([A-Z][A-Z0-9_]+)=", linha)
        if not achado:
            continue
        bloco: list[str] = []
        for anterior in reversed(linhas[:i]):
            if anterior.lstrip().startswith("#") and not re.match(
                r"^#\s*[A-Z][A-Z0-9_]+=", anterior.strip()
            ):
                bloco.append(anterior)
            else:
                break
        if any("RESERVADO" in linha_do_bloco for linha_do_bloco in bloco):
            reservadas.add(achado.group(1))
    return reservadas


def _secoes_das_regras() -> list[tuple[str, str, str]]:
    """(número, título, marcas) de cada `### N.N` do documento de regras."""
    secoes = []
    for linha in REGRAS.read_text("utf-8").splitlines():
        achado = re.match(r"^### (\d+\.\d+) (.*)$", linha)
        if achado:
            secoes.append((achado.group(1), achado.group(2), linha))
    return secoes


def _modulos_reais() -> set[str]:
    """Módulos de topo em `src/`: arquivos .py e pacotes com `__init__.py`.

    `src/layouts/` não entra: é diretório de dados (YAML de layout), sem
    `__init__.py` e sem código.
    """
    src = REPO / "src"
    arquivos = {p.stem for p in src.glob("*.py") if p.stem != "__init__"}
    pacotes = {p.name for p in src.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    return arquivos | pacotes


def _linhas_de_fase() -> list[list[str]]:
    """Células das linhas da tabela de fases do `docs/status.md`."""
    linhas = []
    for linha in STATUS.read_text("utf-8").splitlines():
        celulas = [c.strip() for c in linha.strip().strip("|").split("|")]
        if len(celulas) == 5 and celulas[2] in ESTADOS_DE_FASE:
            linhas.append(celulas)
    return linhas


def _fases_do_status() -> set[str]:
    return {celulas[0] for celulas in _linhas_de_fase()}


def _listas_do_status() -> tuple[set[str], set[str]]:
    """(documentados, pendentes) declarados na seção de passivo do status.md."""
    texto = STATUS.read_text("utf-8")

    def extrair(rotulo: str) -> set[str]:
        bloco = re.search(rf"\*\*{rotulo}:\*\*(.+?)(?:\n\n|\Z)", texto, re.S)
        assert bloco, f"seção '{rotulo}' ausente de docs/status.md (§1.4)"
        return set(re.findall(r"`([^`]+)`", bloco.group(1)))

    return extrair("Documentados"), extrair("Pendentes")


class TestRegistroDeRegras:
    """A trava que impede o próprio documento de regras de mentir."""

    def test_toda_regra_ci_tem_verificacao(self):
        marcadas = {num for num, _, linha in _secoes_das_regras() if "[CI]" in linha}
        sem_verificacao = marcadas - set(REGISTRO_CI)
        assert not sem_verificacao, (
            f"§{', §'.join(sorted(sem_verificacao))} marcada [CI] sem verificação "
            "implementada — a marca promete que o pipeline quebra, e não quebra"
        )

    def test_registro_nao_tem_regra_fantasma(self):
        existentes = {num for num, _, _ in _secoes_das_regras()}
        fantasmas = set(REGISTRO_CI) - existentes
        assert not fantasmas, (
            f"REGISTRO_CI cita §{', §'.join(sorted(fantasmas))}, que não existe "
            "mais no documento de regras"
        )

    def test_verificacao_registrada_aponta_arquivo_existente(self):
        faltando = {
            num: alvo
            for num, alvo in REGISTRO_CI.items()
            if not (REPO / alvo.split("::")[0]).exists()
        }
        assert not faltando, f"REGISTRO_CI aponta para arquivo inexistente: {faltando}"

    def test_toda_regra_tem_marca(self):
        sem_marca = [
            f"§{num} {titulo}"
            for num, titulo, linha in _secoes_das_regras()
            if not any(m in linha for m in MARCAS)
        ]
        assert not sem_marca, (
            f"regra sem marca de cobrança: {sem_marca} — o próprio documento diz "
            "que regra sem marca não existe"
        )


class TestDocumentacao:
    def test_estrutura_de_documentacao_existe(self):
        """§1.2 — o que o documento de regras lista como estrutura precisa existir."""
        obrigatorios = [
            REPO / "README.md",
            REPO / "CHANGELOG.md",
            REGRAS,
            STATUS,
            REPO / "docs" / "roadmap.md",
            REPO / "docs" / "decisions",
            MODULES,
            ARCHITECTURE,
            REPO / "docs" / "deploy.md",
            REPO / "docs" / "migrations.md",
        ]
        ausentes = [str(p.relative_to(REPO)) for p in obrigatorios if not p.exists()]
        assert not ausentes, f"estrutura da §1.2 incompleta: {ausentes}"

    def test_links_internos_da_documentacao_resolvem(self):
        """§1.2/§1.3 — referência a arquivo que não existe é defeito."""
        quebrados: dict[str, list[str]] = {}
        docs = [*REPO.glob("*.md"), *(REPO / "docs").rglob("*.md")]
        for doc in docs:
            alvos = re.findall(r"\[[^\]]*\]\(([^)#]+)(?:#[^)]*)?\)", doc.read_text("utf-8"))
            ruins = [
                alvo
                for alvo in alvos
                if not alvo.startswith(("http://", "https://", "mailto:"))
                and not (doc.parent / alvo).exists()
            ]
            if ruins:
                quebrados[str(doc.relative_to(REPO))] = ruins
        assert not quebrados, f"link interno apontando para o vazio: {quebrados}"

    def test_passivo_de_modulos_cobre_todos_os_modulos(self):
        """§1.4 — a lista do status.md precisa bater com `src/`, sem sobra nem falta."""
        documentados, pendentes = _listas_do_status()
        reais = _modulos_reais()

        sobrando = (documentados | pendentes) - reais
        faltando = reais - (documentados | pendentes)
        assert not sobrando, f"status.md lista módulo que não existe em src/: {sorted(sobrando)}"
        assert not faltando, (
            f"módulo em src/ fora da contabilidade da §1.4: {sorted(faltando)} — "
            "módulo novo entra como documentado ou como pendente, nunca invisível"
        )
        assert not (documentados & pendentes), "módulo em duas listas ao mesmo tempo"

    def test_modulo_declarado_documentado_tem_documento(self):
        """§1.4 — a lista 'Documentados' não pode ser uma promessa."""
        documentados, _ = _listas_do_status()
        ausentes = sorted(m for m in documentados if not (MODULES / f"{m}.md").exists())
        assert not ausentes, f"status.md declara documentado sem docs/modules/<nome>.md: {ausentes}"

    def test_documento_de_modulo_responde_as_seis_perguntas(self):
        """§1.4 — documento incompleto passa a sensação de cobertura que não há."""
        exigidas = [
            "## O que faz",
            "## O que expõe",
            "## Depende de / quem depende",
            "## Decisões não óbvias e armadilhas",
            "## Como testar isoladamente",
            "## O que não faz",
        ]
        incompletos: dict[str, list[str]] = {}
        for doc in sorted(MODULES.glob("*.md")):
            texto = doc.read_text("utf-8")
            faltando = [s for s in exigidas if s not in texto]
            if faltando:
                incompletos[doc.name] = faltando
        assert not incompletos, f"documento de módulo sem as seções da §1.4: {incompletos}"

    def test_documento_de_modulo_descreve_modulo_real(self):
        reais = _modulos_reais()
        orfaos = sorted(d.stem for d in MODULES.glob("*.md") if d.stem not in reais)
        assert not orfaos, f"docs/modules descreve módulo que não existe: {orfaos}"

    def test_documento_existente_esta_na_lista_de_documentados(self):
        """O caminho inverso da §1.4: documento escrito e não contabilizado.

        Aconteceu de verdade: o PR #9 criou `docs/modules/reports.md`, mas a
        edição que movia `reports` para a lista de documentados não casou com
        o texto e falhou em silêncio — o documento existia e o status.md
        seguia dizendo que não. Nenhuma verificação pegava, porque ter
        documento "a mais" não violava nada.
        """
        documentados, _ = _listas_do_status()
        fora_da_lista = sorted(
            d.stem
            for d in MODULES.glob("*.md")
            if d.stem in _modulos_reais() and d.stem not in documentados
        )
        assert not fora_da_lista, (
            f"documento existe e o status.md não o lista como documentado: "
            f"{fora_da_lista} — a contabilidade da §1.4 está mentindo para menos"
        )

    def test_estado_de_fase_usa_vocabulario_declarado(self):
        """§1.8 — estado fora da lista impede qualquer verificação posterior.

        Uma linha de fase é reconhecida pelo primeiro campo (número ou faixa,
        como `1–8`); o estado dela precisa estar no vocabulário. Estado
        inventado faria as demais verificações da §1.8 pularem a linha em
        silêncio, que é pior que reprovar.
        """
        candidatas = []
        for linha in STATUS.read_text("utf-8").splitlines():
            celulas = [c.strip() for c in linha.strip().strip("|").split("|")]
            if len(celulas) == 5 and re.match(r"^\d+(\s*[–-]\s*\d+)?$", celulas[0]):
                candidatas.append(celulas)

        assert candidatas, "nenhuma linha de fase reconhecida em docs/status.md — formato mudou?"
        invalidos = [(c[0], c[2]) for c in candidatas if c[2] not in ESTADOS_DE_FASE]
        assert not invalidos, (
            f"fase com estado fora do vocabulário da §1.8: {invalidos} — "
            f"permitidos: {sorted(ESTADOS_DE_FASE)}"
        )

    def test_toda_fase_da_tabela_e_reconhecida(self):
        """Trava do reconhecedor: `_linhas_de_fase` filtra pelo estado, então
        uma linha com estado inválido sumiria da contagem sem alarde."""
        marcadas = sum(
            1
            for linha in STATUS.read_text("utf-8").splitlines()
            if re.match(r"^\|\s*\d+(\s*[–-]\s*\d+)?\s*\|", linha)
        )
        assert len(_linhas_de_fase()) == marcadas, (
            "linha de fase presente na tabela e não reconhecida pelo parser — "
            "provavelmente estado fora do vocabulário ou coluna a mais"
        )

    def test_fase_concluida_aponta_teste_existente(self):
        """§1.8 — nada é concluído sem os testes daquela fase passando.

        O CI não sabe dizer se um teste *prova* a fase; sabe dizer que o
        arquivo citado como evidência existe e roda.  Evidência apontando para
        arquivo removido é o começo da divergência que a regra evita.
        """
        problemas: dict[str, list[str]] = {}
        for celulas in _linhas_de_fase():
            fase, estado, evidencia = celulas[0], celulas[2], celulas[3]
            if estado != "concluída":
                continue
            citados = re.findall(r"`([^`]+)`", evidencia)
            assert citados, f"fase {fase} marcada concluída sem evidência (§1.8)"
            # `arquivo::Classe::teste` é evidência mais precisa que só o
            # arquivo, e é a forma que o `REGISTRO_CI` já usa.  Aqui o `::`
            # não era tratado, então apontar o teste exato derrubava a
            # verificação e empurrava todos para o caminho vago.
            ausentes = [c for c in citados if not (REPO / c.split("::")[0]).exists()]
            if ausentes:
                problemas[fase] = ausentes
        assert (
            not problemas
        ), f"evidência de fase concluída aponta para arquivo inexistente: {problemas}"

    def test_arquivo_gerado_declara_fonte_existente(self):
        """§1.9 — o aviso de arquivo gerado precisa dizer de onde ele vem."""
        problemas: dict[str, str] = {}
        for doc in [*REPO.glob("*.md"), *(REPO / "docs").rglob("*.md")]:
            texto = doc.read_text("utf-8")
            for achado in re.finditer(r"^ARQUIVO GERADO — não edite; fonte: (\S+)", texto, re.M):
                fonte = achado.group(1)
                # `<caminho do script>` é o molde citado na própria §1.9.
                if fonte.startswith("<"):
                    continue
                if not (REPO / fonte).exists():
                    problemas[str(doc.relative_to(REPO))] = fonte
        assert not problemas, f"arquivo gerado citando fonte inexistente: {problemas}"

    def test_cabecalho_de_arquitetura(self):
        """§1.12 — cabeçalho completo, estado válido e carimbo com menos de 90 dias.

        As três linhas são exigidas juntas. Sem `Fase correspondente` a trava
        do caminho inverso não teria em que se apoiar, e o documento
        escaparia dela em silêncio.
        """
        hoje = dt.date.today()
        fases_conhecidas = _fases_do_status()
        problemas: dict[str, str] = {}
        for doc in sorted(ARCHITECTURE.glob("*.md")):
            texto = doc.read_text("utf-8")
            estado = re.search(r"^Estado: (.+)$", texto, re.M)
            carimbo = re.search(
                r"^Verificado contra o código em: (\d{4}-\d{2}-\d{2})$", texto, re.M
            )
            fase = re.search(r"^Fase correspondente: (.+)$", texto, re.M)
            nome = doc.name
            if not estado or not carimbo or not fase:
                problemas[nome] = "cabeçalho ausente ou malformado"
                continue
            if fase.group(1).strip() not in fases_conhecidas:
                problemas[nome] = f"fase {fase.group(1).strip()!r} não existe em docs/status.md"
                continue
            if estado.group(1).strip() not in ESTADOS_DE_ARQUITETURA:
                problemas[nome] = f"estado fora da lista: {estado.group(1)!r}"
                continue
            if estado.group(1).strip() != "especificado":
                idade = (hoje - dt.date.fromisoformat(carimbo.group(1))).days
                if idade > 90:
                    problemas[nome] = f"verificado há {idade} dias (limite: 90)"
        assert not problemas, f"documento de arquitetura fora da §1.12: {problemas}"

    def test_trava_do_caminho_inverso(self):
        """§1.12 — fase iniciada com o documento ainda em 'especificado' é defeito."""
        fases_iniciadas = {
            celulas[0]
            for celulas in _linhas_de_fase()
            if celulas[2] in {"em andamento", "concluída"}
        }
        presos = []
        for doc in sorted(ARCHITECTURE.glob("*.md")):
            texto = doc.read_text("utf-8")
            estado = re.search(r"^Estado: (.+)$", texto, re.M)
            fase = re.search(r"^Fase correspondente: (.+)$", texto, re.M)
            if estado and fase and estado.group(1).strip() == "especificado":
                if fase.group(1).strip() in fases_iniciadas:
                    presos.append(doc.name)
        assert not presos, (
            f"documento ainda 'especificado' com a fase já iniciada: {presos} — "
            "assim ele escaparia da §1.3 para sempre"
        )


class TestConfiguracao:
    def test_ninguem_le_ambiente_fora_de_settings(self):
        """§2.1 — ponto único de configuração.

        A verificação é por AST, não por texto: `os.environ` dentro de string,
        comentário ou docstring não conta, e `from os import environ` conta.
        """
        infratores: dict[str, list[int]] = {}
        for arquivo in sorted((REPO / "src").rglob("*.py")):
            if arquivo == REPO / "src" / "settings.py":
                continue
            arvore = ast.parse(arquivo.read_text("utf-8"), filename=str(arquivo))
            linhas = []
            for no in ast.walk(arvore):
                if isinstance(no, ast.Attribute) and no.attr in {"environ", "getenv"}:
                    if isinstance(no.value, ast.Name) and no.value.id == "os":
                        linhas.append(no.lineno)
                elif isinstance(no, ast.ImportFrom) and no.module == "os":
                    if any(a.name in {"environ", "getenv"} for a in no.names):
                        linhas.append(no.lineno)
            if linhas:
                infratores[str(arquivo.relative_to(REPO))] = sorted(set(linhas))
        assert not infratores, (
            f"leitura de ambiente fora de src/settings.py: {infratores} — "
            "configuração espalhada diverge da documentação em silêncio (§2.1)"
        )

    def test_variavel_documentada_tem_consumidor(self):
        """§2.2 — variável documentada sem efeito é pior que não documentada."""
        from src import settings as mod

        conhecidas = set(mod._ENV_TO_FIELD) | set(mod._LEGACY_ALIASES) | {"SPED_HUB_DB"}
        texto = ENV_EXAMPLE.read_text("utf-8")

        reservadas = variaveis_reservadas(texto)

        documentadas = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", texto, re.M))
        sem_consumidor = sorted(documentadas - conhecidas - reservadas)
        assert not sem_consumidor, (
            f"variável em .env.example sem consumidor: {sem_consumidor} — "
            "quem configura acredita ter configurado algo (§2.2)"
        )

    def test_campo_de_settings_com_env_var_tem_consumidor_real(self):
        """§2.2 — o lado que `test_variavel_documentada_tem_consumidor` não vê.

        Aquele teste só confirma que a variável chega ao `Settings`.  Chegar ao
        `Settings` e ninguém ler o campo é o mesmo defeito visto de outro
        ângulo, e foi assim que sete variáveis documentadas passaram meses sem
        efeito — entre elas `SPED_HUB_ALLOWED_HOSTS`, que o `docs/deploy.md`
        manda configurar como passo de endurecimento.

        Um campo conta como consumido quando é lido em `src/` fora do
        `settings.py`, ou quando uma `@property` do próprio `Settings` que o
        lê é consumida lá fora (é o caso de `cors_origins` e
        `max_upload_bytes`).  Campo sem variável de ambiente fica de fora: a
        §2.2 fala de configuração documentada, não de atributo interno.
        """
        from dataclasses import fields

        from src import settings as mod

        campos = {f.name for f in fields(mod.Settings)}
        settings_py = REPO / "src" / "settings.py"

        arvore = ast.parse(settings_py.read_text("utf-8"), filename=str(settings_py))
        classe = next(
            no for no in arvore.body if isinstance(no, ast.ClassDef) and no.name == "Settings"
        )
        propriedades = {
            membro.name: {
                no.attr
                for no in ast.walk(membro)
                if isinstance(no, ast.Attribute) and no.attr in campos
            }
            for membro in classe.body
            if isinstance(membro, ast.FunctionDef)
            and any(isinstance(d, ast.Name) and d.id == "property" for d in membro.decorator_list)
        }

        lidos_fora: set[str] = set()
        for arquivo in sorted((REPO / "src").rglob("*.py")):
            if arquivo == settings_py:
                continue
            for no in ast.walk(ast.parse(arquivo.read_text("utf-8"), filename=str(arquivo))):
                if isinstance(no, ast.Attribute):
                    lidos_fora.add(no.attr)
                elif isinstance(no, ast.keyword) and no.arg:
                    lidos_fora.add(no.arg)

        def consumido(campo: str) -> bool:
            if campo in lidos_fora:
                return True
            return any(
                campo in lidos and nome in lidos_fora for nome, lidos in propriedades.items()
            )

        reservados = {
            mod._ENV_TO_FIELD[nome]
            for nome in variaveis_reservadas(ENV_EXAMPLE.read_text("utf-8"))
            if nome in mod._ENV_TO_FIELD
        }
        # `_LEGACY_ALIASES` mapeia nome-de-variável para nome-de-variável, não
        # para campo: os alvos já estão em `_ENV_TO_FIELD`.
        configuraveis = set(mod._ENV_TO_FIELD.values())

        sem_consumidor = sorted(
            campo for campo in configuraveis - reservados if not consumido(campo)
        )
        assert not sem_consumidor, (
            f"campo de Settings com variável de ambiente e sem leitor: {sem_consumidor} — "
            "a variável chega à configuração e não muda comportamento nenhum (§2.2)"
        )

    def test_variavel_consumida_esta_documentada(self):
        """§2.2, o outro lado: consumidor sem documentação é configuração oculta."""
        from src import settings as mod

        documentadas = set(
            re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", ENV_EXAMPLE.read_text("utf-8"), re.M)
        )
        # Aliases legados aparecem como comentário ao lado do nome atual.
        texto = ENV_EXAMPLE.read_text("utf-8")
        nao_documentadas = sorted(
            nome for nome in mod._ENV_TO_FIELD if nome not in documentadas and nome not in texto
        )
        assert (
            not nao_documentadas
        ), f"variável consumida por settings.py e ausente de .env.example: {nao_documentadas}"

    def test_marca_reservado_nao_vaza_para_a_variavel_seguinte(self):
        """A dispensa da §2.2 tem de ser explícita, não herdada por vizinhança.

        Com uma janela de N caracteres para trás, as variáveis logo abaixo de
        uma RESERVADO ficavam dispensadas de calado.
        """
        texto = (
            "# RESERVADO: nenhum componente consome esta chave hoje.\n"
            "# SPED_HUB_SECRET_KEY=change-me\n"
            "\n"
            "SPED_HUB_HOST=127.0.0.1\n"
            "SPED_HUB_PORT=8000\n"
        )
        assert variaveis_reservadas(texto) == {"SPED_HUB_SECRET_KEY"}

    def test_reservada_declara_que_ninguem_consome(self):
        texto = ENV_EXAMPLE.read_text("utf-8")
        if "RESERVADO" in texto:
            assert (
                "nenhum componente consome" in texto
            ), "variável marcada RESERVADO sem a frase exigida pela §2.2"


def _itens_do_roadmap() -> list[tuple[str, str]]:
    """(item, marcador) de cada linha de tabela do `docs/roadmap.md`."""
    itens = []
    for linha in ROADMAP.read_text("utf-8").splitlines():
        celulas = [c.strip() for c in linha.strip().strip("|").split("|")]
        if len(celulas) != 3 or celulas[0] in {"Item", ""} or set(celulas[0]) <= {"-", ":"}:
            continue
        itens.append((celulas[0], celulas[2]))
    return itens


def marcador_existe(alvo: str) -> bool:
    """O símbolo ou arquivo do marcador de ausência já está no repositório?

    `módulo:símbolo` é resolvido por AST, sem importar o módulo: importar
    executaria código, e um erro de import viraria "não existe" — a resposta
    errada, e na direção que passa despercebida.
    """
    if ":" not in alvo:
        return (REPO / alvo).exists()
    modulo, simbolo = alvo.split(":", 1)
    arquivo = REPO / (modulo.replace(".", "/") + ".py")
    if not arquivo.exists():
        arquivo = REPO / modulo.replace(".", "/") / "__init__.py"
    if not arquivo.exists():
        return False
    arvore = ast.parse(arquivo.read_text("utf-8"), filename=str(arquivo))
    nomes = {
        no.name
        for no in ast.walk(arvore)
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    nomes |= {
        destino.id
        for no in ast.walk(arvore)
        if isinstance(no, ast.Assign)
        for destino in no.targets
        if isinstance(destino, ast.Name)
    }
    return simbolo in nomes


class TestResolucaoDeMarcador:
    """A resolução em si, exercitada pelos dois lados.

    O `test_nenhum_marcador_de_ausencia_existe` só a exercita pelo lado
    negativo: com o roadmap correto, nenhum marcador existe, e uma resolução
    quebrada devolveria "não existe" para tudo — passando igual. Estes casos
    usam símbolos conhecidos do próprio repositório.
    """

    @pytest.mark.parametrize(
        "alvo",
        [
            "src.webhooks:retry_failed",  # função
            "src.webhooks:WebhookService",  # classe
            "src.webhooks:LOTE_DE_REENVIO",  # constante de módulo
            "src.webhooks:dispatch",  # método `async` dentro de classe
            "src.async_jobs:obter",  # método comum dentro de classe
            "src/reports/templates/balancete.html",  # caminho de arquivo
            "docs/roadmap.md",
        ],
    )
    def test_reconhece_o_que_existe(self, alvo):
        assert marcador_existe(alvo), f"{alvo} existe e a resolução não achou"

    @pytest.mark.parametrize(
        "alvo",
        [
            "src.webhooks:funcao_que_nao_existe",
            "src.modulo.inexistente:qualquer",
            "src/reports/templates/inexistente.html",
            "caminho/que/nao/existe",
        ],
    )
    def test_nao_inventa_o_que_nao_existe(self, alvo):
        assert not marcador_existe(alvo)

    def test_modulo_com_erro_de_sintaxe_nao_vira_ausencia_silenciosa(self, tmp_path):
        """Erro ao ler o módulo tem de estourar, não devolver "não existe".

        Devolver `False` aqui esconderia um item pronto: a direção do defeito
        que esta regra existe para pegar.
        """
        quebrado = REPO / "src" / "_marcador_quebrado_temp.py"
        quebrado.write_text("def isto( não é python\n", encoding="utf-8")
        try:
            with pytest.raises(SyntaxError):
                marcador_existe("src._marcador_quebrado_temp:qualquer")
        finally:
            quebrado.unlink()


class TestRoadmap:
    """§1.1 — o roadmap lista o que **não** existe.

    Documento de "o que falta" apodrece na direção mais difícil de notar: o
    item é feito e ninguém volta para tirá-lo de lá. Aconteceu duas vezes neste
    projeto — a exportação do balancete em PDF e os testes de navegador no CI
    seguiram listados como ausentes depois de existirem, com teste passando e
    job no pipeline. Nenhuma verificação olhava para lá.

    Cada item declara um marcador de ausência (`módulo:símbolo` ou caminho) que
    só passa a existir quando o item for feito. Item bloqueado por credencial,
    contrato ou dado de terceiro declara `externo` e a razão: não é código que
    falta, então não há marcador possível.
    """

    def test_roadmap_tem_itens(self):
        """Guarda contra a tabela mudar de forma e os testes virarem teste de nada."""
        assert _itens_do_roadmap(), "nenhum item lido de docs/roadmap.md — formato mudou?"

    def test_todo_item_declara_marcador(self):
        sem_marcador = [item for item, marcador in _itens_do_roadmap() if not marcador]
        assert not sem_marcador, (
            f"item do roadmap sem marcador de ausência: {sem_marcador} — sem ele "
            "ninguém percebe quando o item é feito e a lista fica mentindo"
        )

    def test_item_externo_declara_a_razao(self):
        """`externo` sem razão é o mesmo que não declarar nada."""
        sem_razao = [
            item
            for item, marcador in _itens_do_roadmap()
            if marcador.startswith("`externo`") and len(marcador) < len("`externo` — ") + 15
        ]
        assert not sem_razao, (
            f"item marcado `externo` sem dizer de que depende: {sem_razao} — "
            "a razão é o que permite reavaliar o bloqueio depois"
        )

    def test_nenhum_marcador_de_ausencia_existe(self):
        """O teste que faltava: item listado como ausente que já está pronto.

        A resolução vive em `marcador_existe`, exercitada pelos dois lados em
        `TestResolucaoDeMarcador` — aqui ela só é aplicada.
        """
        feitos = []
        for item, marcador in _itens_do_roadmap():
            alvo = marcador.strip("`").split("`")[0].strip()
            if alvo.startswith("externo"):
                continue
            if marcador_existe(alvo):
                feitos.append(f"{item} → {alvo} já existe")
        assert not feitos, (
            f"o roadmap lista como ausente o que já está pronto: {feitos} — "
            "tire do roadmap e registre em docs/status.md (§1.1)"
        )

    def test_roadmap_nao_repete_item_do_status(self):
        """O mesmo assunto nos dois documentos divergiria com o tempo."""
        concluidas = {
            celulas[1].lower() for celulas in _linhas_de_fase() if celulas[2] == "concluída"
        }
        repetidos = [item for item, _ in _itens_do_roadmap() if item.lower() in concluidas]
        assert (
            not repetidos
        ), f"item do roadmap com o mesmo título de fase concluída em status.md: {repetidos}"


def _fora_do_job_de_postgres(nomes: set[str], ci: str) -> list[str]:
    """Quais desses arquivos não aparecem no texto do workflow.

    Existe como função para poder ser exercitada pelos dois lados: com o CI
    correto nenhum arquivo fica de fora, então a comparação só seria exercitada
    negativamente e uma versão quebrada dela passaria igual.
    """
    return sorted(nome for nome in nomes if nome not in ci)


class TestComparacaoDeCobertura:
    def test_acha_o_que_esta_fora(self):
        assert _fora_do_job_de_postgres(
            {"test_a.py", "test_b.py"}, "run: pytest tests/test_a.py"
        ) == ["test_b.py"]

    def test_nao_inventa_quando_esta_tudo_dentro(self):
        assert (
            _fora_do_job_de_postgres({"test_a.py"}, "run: pytest tests/test_a.py tests/test_c.py")
            == []
        )

    def test_conjunto_vazio_nao_acusa_nada(self):
        assert _fora_do_job_de_postgres(set(), "") == []


class TestCoberturaDoPostgres:
    """§3.4 — teste que depende de Postgres precisa rodar contra Postgres.

    O job `postgres` do CI roda uma lista fixa de arquivos. Arquivo que use
    `TEST_DATABASE_URL` e não esteja na lista é teste que **nunca** roda contra
    Postgres: ele passa em todo lugar, pulando, e a diferença de backend que
    ele existe para pegar nunca é exercitada.

    Foi o que quase aconteceu com `test_migracao_de_dados.py`: a correção das
    sequências do Postgres — o defeito silencioso daquela migração — ficaria
    sem verificação em lugar nenhum.
    """

    CI = REPO / ".github" / "workflows" / "ci.yml"

    def _passo_do_postgres(self) -> str:
        texto = self.CI.read_text("utf-8")
        assert "TEST_DATABASE_URL" in texto, "o CI não define TEST_DATABASE_URL"
        # O comando do passo que roda com a variável definida.
        return texto

    def test_todo_teste_que_usa_postgres_esta_no_job(self):
        usam = {
            arquivo.name
            for arquivo in sorted((REPO / "tests").glob("test_*.py"))
            if "TEST_DATABASE_URL" in arquivo.read_text("utf-8")
        }
        assert usam, "nenhum teste usa TEST_DATABASE_URL — o padrão mudou?"
        fora = _fora_do_job_de_postgres(usam, self._passo_do_postgres())
        assert not fora, (
            f"teste que depende de Postgres e não está no job `postgres` do CI: {fora} — "
            "ele passa pulando, e a diferença de backend nunca é exercitada (§3.4)"
        )

    def test_job_nao_cita_arquivo_inexistente(self):
        import re

        ci = self._passo_do_postgres()
        citados = set(re.findall(r"(tests/test_\w+\.py)", ci))
        ausentes = sorted(c for c in citados if not (REPO / c).exists())
        assert not ausentes, f"o CI roda arquivo que não existe mais: {ausentes}"


class TestTestes:
    def test_marcador_e2e_fora_da_execucao_padrao(self):
        """§3.5 — teste que depende de serviço externo não derruba o pipeline."""
        config = tomllib.loads(PYPROJECT.read_text("utf-8"))
        pytest_cfg = config["tool"]["pytest"]["ini_options"]
        assert "not e2e" in pytest_cfg.get(
            "addopts", ""
        ), "a execução padrão do pytest voltou a incluir os testes de navegador"
        marcadores = " ".join(pytest_cfg.get("markers", []))
        assert marcadores.startswith("e2e:"), "marcador e2e não declarado em pyproject.toml"

    def test_teste_marcado_e2e_realmente_usa_navegador(self):
        """O marcador é a válvula de escape da §3.5; não pode virar depósito."""
        for arquivo in sorted((REPO / "tests").glob("*.py")):
            texto = arquivo.read_text("utf-8")
            if "pytest.mark.e2e" in texto or "pytestmark = pytest.mark.e2e" in texto:
                assert (
                    "playwright" in texto.lower()
                ), f"{arquivo.name} usa o marcador e2e sem ser teste de navegador"


class TestBuild:
    def test_lint_e_format_pinados(self):
        """§4.1 — o CI ficou vermelho em 14 execuções seguidas por falta disto."""
        config = tomllib.loads(PYPROJECT.read_text("utf-8"))
        dev = config["project"]["optional-dependencies"]["dev"]
        for ferramenta in ("ruff", "black"):
            # Separa o nome de qualquer especificador (`==`, `>=`, `~=`, `<`)
            # para distinguir "sumiu" de "está aqui, mas solto".
            declaracao = next(
                (d for d in dev if re.split(r"[=<>~!\[]", d, maxsplit=1)[0].strip() == ferramenta),
                None,
            )
            assert declaracao, f"{ferramenta} sumiu das dependências de dev"
            assert "==" in declaracao, (
                f"{ferramenta} declarado como {declaracao!r}: sem `==` o pipeline "
                "passa a falhar sozinho quando sai versão nova (§4.1)"
            )

    def test_regras_de_lint_declaradas(self):
        """§4.2 — sem `select`, valem os defaults da versão instalada."""
        config = tomllib.loads(PYPROJECT.read_text("utf-8"))
        select = config["tool"]["ruff"]["lint"].get("select")
        assert select, "[tool.ruff.lint].select ausente ou vazio (§4.2)"

    def test_versao_do_projeto_bate_com_o_status(self):
        config = tomllib.loads(PYPROJECT.read_text("utf-8"))
        versao = config["project"]["version"]
        assert f"**Versão:** {versao}" in STATUS.read_text(
            "utf-8"
        ), f"docs/status.md não acompanha a versão {versao} do pyproject.toml"


@pytest.mark.parametrize("numero", sorted(REGISTRO_CI))
def test_regra_ci_citada_no_documento(numero: str):
    """Espelho do registro: o número precisa existir como seção marcada [CI]."""
    linha = next((texto for n, _, texto in _secoes_das_regras() if n == numero), None)
    assert linha, f"§{numero} não existe em REGRAS-DO-PROJETO.md"
    assert "[CI]" in linha, f"§{numero} está no REGISTRO_CI mas perdeu a marca [CI]"
