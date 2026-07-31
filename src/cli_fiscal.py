"""O comando `sped-hub fiscal` — a cadeia fiscal pela linha de comando.

A Central de Documentos, o motor de classificação, os geradores de EFD e a
escrituração arquivada existem desde as fases 39 a 45, e até aqui nenhuma
rota nem comando os alcançava. Este módulo fecha isso: importar, listar,
gerar e conferir.

**Gerar sempre arquiva.** Não há como gerar sem registrar, e a ausência de um
`--sem-arquivar` é deliberada. A terceira camada existe para responder "o que
você enviou"; um arquivo que sai do sistema sem deixar registro é exatamente
o buraco que ela fecha, e uma prévia que grava em disco é indistinguível de
uma entrega depois que o arquivo está na mão de alguém. Gerar de novo cria
outra escrituração — já era assim, e o histórico de tentativas é informação
real, não sujeira.

Códigos de saída, porque isto vai para dentro de script de fechamento:

    0  correu bem
    1  erro — cadastro faltando, empresa inexistente, arquivo ilegível
    2  só em `conferir`: o arquivo entregue divergiu do que sairia agora
"""

from __future__ import annotations

import datetime
import json
import pathlib

from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, selectinload

from src.db.models import (
    DocumentoFiscal,
    Empresa,
    Escrituracao,
    ItemDocumentoFiscal,
    RegraFiscal,
    criar_engine,
)
from src.documentos import (
    OPERADORES,
    Alteracao,
    AlteracaoInvalida,
    Filtro,
    ImportadorDeDocumentos,
    MotorDeClassificacao,
    PlanilhaInvalida,
    RegraInvalida,
    Selecao,
    SelecaoVazia,
    confirmar,
    desfazer_lote,
    exportar,
    novo_lote,
    reimportar,
    simular,
)
from src.documentos.ajustes import desserializar
from src.documentos.classificacao import aplicar as aplicar_classificacao
from src.documentos.classificacao import criar_regra
from src.escrituracoes import (
    ATIVIDADES_CONTRIBUICOES,
    ATIVIDADES_ICMS,
    NATUREZAS_PJ,
    PERFIS,
    REGIMES,
    TIPOS,
    AjusteInvalido,
    ApuracaoIBSCBS,
    CampoObrigatorioAusente,
    GeradorEFDContribuicoes,
    GeradorEFDICMS,
    TransmissaoInvalida,
    ajustes_do_periodo,
    arquivar,
    avisos_de,
    comparar,
    criar_ajuste,
    espelho,
    marcar_transmitida,
    transmitidas_do_periodo,
    utilizacao,
)

# O mesmo formato dos relatórios — 1.234.567,89.  `f"{v:,.2f}"` daria
# `1,234,567.89`, que num sistema fiscal brasileiro se lê como outro número.
from src.reports.base import fmt_data, fmt_moeda

GERADORES = {
    "efd_icms": GeradorEFDICMS,
    "efd_contribuicoes": GeradorEFDContribuicoes,
}

# Extensões que a Central sabe ler hoje.  Varrer uma pasta inteira sem filtro
# encheria o relatório de rejeições de PDF e planilha que ninguém mandou
# importar.
EXTENSOES = {".xml"}

DIVERGENTE = 2

# O cadastro fiscal: os campos que decidem o enquadramento declarado no
# arquivo, e que o validador do Fisco **não** confere — ele não tem como saber
# qual é o certo.  Errar aqui produz arquivo aceito e intimação meses depois.
#
# A ordem é a de quem preenche: primeiro o que a EFD ICMS/IPI exige, depois o
# que a EFD-Contribuições exige.  Cada linha traz a tabela de valores, para
# que a recusa possa mostrá-la em vez de mandar procurar no Guia Prático.
CADASTRO_FISCAL = {
    "ind_perfil": ("IND_PERFIL do 0000 da EFD ICMS/IPI", PERFIS),
    "ind_ativ": ("IND_ATIV do 0000 da EFD ICMS/IPI", ATIVIDADES_ICMS),
    "ind_ativ_contribuicoes": (
        "IND_ATIV do 0000 da EFD-Contribuições — tabela DIFERENTE da de cima",
        ATIVIDADES_CONTRIBUICOES,
    ),
    "cod_inc_trib": ("COD_INC_TRIB do 0110 — decide se há crédito", REGIMES),
    "ind_nat_pj": ("IND_NAT_PJ do 0000 — natureza jurídica", NATUREZAS_PJ),
}

# O que cada obrigação exige antes de gerar.  `ind_nat_pj` fica fora: tem
# default, e por isso não impede a geração.
EXIGIDOS = {
    "efd_icms": ("ind_perfil", "ind_ativ"),
    "efd_contribuicoes": ("cod_inc_trib", "ind_ativ_contribuicoes"),
}


def _empresa(sessao: Session, empresa_id: int) -> Empresa:
    empresa = sessao.get(Empresa, empresa_id)
    if empresa is None:
        raise LookupError(
            f"não existe empresa #{empresa_id} — `sped-hub fiscal empresas` lista as cadastradas"
        )
    return empresa


def _periodo(args) -> tuple[datetime.date, datetime.date]:
    return _data(args.de), _data(args.ate)


def _data(valor: str) -> datetime.date:
    """AAAA-MM-DD ou DDMMAAAA, como no resto da CLI."""
    if len(valor) == 8 and valor.isdigit():
        return datetime.date(int(valor[4:]), int(valor[2:4]), int(valor[:2]))
    return datetime.date.fromisoformat(valor)


def _arquivos(caminhos: list[str]) -> list[tuple[str, bytes]]:
    """Arquivos e pastas, achatados numa lista de (nome, conteúdo).

    Pasta é o caso normal: quem baixa XML do portal recebe uma pasta cheia
    deles, e pedir que digite mil caminhos seria pedir para não usar.
    """
    encontrados: list[tuple[str, bytes]] = []
    for bruto in caminhos:
        caminho = pathlib.Path(bruto)
        if caminho.is_dir():
            candidatos = sorted(
                p for p in caminho.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSOES
            )
        else:
            candidatos = [caminho]
        for arquivo in candidatos:
            encontrados.append((arquivo.name, arquivo.read_bytes()))
    return encontrados


def _nome_padrao(empresa: Empresa, tipo: str, inicio: datetime.date) -> str:
    return f"{tipo}_{empresa.cnpj}_{inicio:%Y%m}.txt"


def gravar(destino: pathlib.Path, texto: str) -> None:
    """Escreve o arquivo SPED sem deixar o Python mexer na quebra de linha.

    `newline=""` é o que impede a tradução automática: no Windows, um `open`
    em modo texto sem ele reescreve cada `\\n` como `\\r\\n`, e o texto do
    leiaute já vem com `\\r\\n` — o resultado é `\\r\\r\\n`, que faz o
    validador recusar o arquivo inteiro sem dizer por quê.

    Está numa função própria porque **a falha é invisível no Linux**, onde a
    suíte roda: lá os dois modos gravam os mesmos bytes. Testar o resultado
    não distingue nada, e testar a chamada é o que sobra. Não é preciosismo —
    foi assim que o entrypoint do nginx quebrou para quem constrói no
    Windows, com toda a verificação automática passando.
    """
    with open(destino, "w", encoding="utf-8", newline="") as saida:
        saida.write(texto)


# ── Subcomandos ────────────────────────────────────────────────────────────


def _empresas(sessao: Session) -> int:
    empresas = sessao.execute(select(Empresa).order_by(Empresa.id)).scalars().all()
    if not empresas:
        print("Nenhuma empresa cadastrada.")
        return 0
    print(f"\n{'ID':>4}  {'CNPJ':16} {'UF':3} {'Perfil':7} {'Regime':7} Nome")
    for e in empresas:
        # O cadastro fiscal decide se a empresa pode gerar; mostrá-lo aqui
        # evita descobrir que falta só na hora de fechar o mês.
        print(
            f"{e.id:>4}  {e.cnpj:16} {(e.uf or '—'):3} "
            f"{(e.ind_perfil or '—'):7} {(e.cod_inc_trib or '—'):7} {e.nome}"
        )
    print()
    return 0


def _cadastro(sessao: Session, args) -> int:
    """Mostra ou preenche o cadastro fiscal da empresa.

    Sem nenhum campo, é diagnóstico: mostra o que está preenchido e o que
    falta para cada obrigação. É o comando que responde "por que a geração
    recusou?" antes de alguém tentar gerar.

    Os campos aqui são os que o validador do Fisco **não** confere — ele não
    tem como saber qual é o certo. Por isso o valor é conferido contra a
    tabela na hora de gravar, e a recusa mostra a tabela inteira.
    """
    empresa = _empresa(sessao, args.empresa)
    informados = {
        campo: getattr(args, campo) for campo in CADASTRO_FISCAL if getattr(args, campo, None)
    }

    # Confere TUDO antes de atribuir QUALQUER coisa.  Hoje a sessão seria
    # descartada de qualquer jeito ao levantar, mas depender disso é depender
    # de quem chama não commitar — e este módulo não é o único que pode chamar.
    for campo, valor in informados.items():
        rotulo, tabela = CADASTRO_FISCAL[campo]
        if valor not in tabela:
            opcoes = "; ".join(f"{c} = {d}" for c, d in sorted(tabela.items()))
            raise ValueError(
                f"{valor!r} não é um valor válido de {campo} ({rotulo}). "
                f"Os válidos são: {opcoes}"
            )

    for campo, valor in informados.items():
        setattr(empresa, campo, valor)
    if informados:
        sessao.commit()

    print(f"\nCadastro fiscal — {empresa.nome} ({empresa.cnpj})")
    for campo, (_, tabela) in CADASTRO_FISCAL.items():
        valor = getattr(empresa, campo)
        descricao = tabela.get(valor, "—" if valor is None else "VALOR FORA DA TABELA")
        marca = "*" if campo in informados else " "
        print(f" {marca} {campo:24} {(valor or '—'):4} {descricao}")

    print()
    for tipo, campos in EXIGIDOS.items():
        faltando = [c for c in campos if getattr(empresa, c) not in CADASTRO_FISCAL[c][1]]
        estado = f"FALTA {', '.join(faltando)}" if faltando else "pronta para gerar"
        print(f"  {TIPOS[tipo]:20} {estado}")
    print()
    return 0


def _importar(sessao: Session, args) -> int:
    arquivos = _arquivos(args.caminhos)
    if not arquivos:
        print("Nenhum XML encontrado nos caminhos informados.")
        return 1

    resultado = ImportadorDeDocumentos(sessao, escritorio_id=args.escritorio).importar_lote(
        arquivos
    )
    sessao.commit()

    print(
        f"\n{resultado.total} arquivos: {resultado.importados} importados, "
        f"{resultado.duplicados} duplicados, {resultado.substituidos} substituídos, "
        f"{resultado.rejeitados} rejeitados"
    )
    # Rejeição é o que a pessoa precisa ver; sucesso em silêncio está certo.
    for ocorrencia in resultado.ocorrencias:
        if ocorrencia.motivo and ocorrencia.desfecho.name == "REJEITADO":
            print(f"  rejeitado  {ocorrencia.origem or '?'}: {ocorrencia.motivo}")
    print()
    return 0


def _documentos(sessao: Session, args) -> int:
    empresa = _empresa(sessao, args.empresa)
    consulta = select(DocumentoFiscal).where(DocumentoFiscal.empresa_id == empresa.id)
    if args.de:
        consulta = consulta.where(DocumentoFiscal.data_emissao >= _data(args.de))
    if args.ate:
        consulta = consulta.where(DocumentoFiscal.data_emissao <= _data(args.ate))
    documentos = (
        sessao.execute(consulta.order_by(DocumentoFiscal.data_emissao, DocumentoFiscal.id))
        .scalars()
        .all()
    )

    if not documentos:
        print("Nenhum documento no recorte.")
        return 0

    print(
        f"\n{'ID':>6}  {'Emissão':10} {'Mod':4} {'Série':6} {'Número':10} {'Sentido':8} {'Valor':>16}"
    )
    for d in documentos:
        emissao = fmt_data(d.data_emissao) if d.data_emissao else "—"
        print(
            f"{d.id:>6}  {emissao:10} {d.modelo:4} {(d.serie or '—'):6} "
            f"{d.numero:10} {d.sentido:8} {fmt_moeda(d.valor_total):>16}"
        )
    total = sum(d.valor_total for d in documentos)
    print(f"\n{len(documentos)} documentos, {fmt_moeda(total)}.\n")
    return 0


def _gerar(sessao: Session, args) -> int:
    empresa = _empresa(sessao, args.empresa)
    inicio, fim = _periodo(args)

    gerador = GERADORES[args.tipo](sessao, empresa=empresa, data_inicio=inicio, data_fim=fim)
    resultado = gerador.gerar()

    escrituracao = arquivar(
        sessao,
        resultado=resultado,
        empresa=empresa,
        tipo=args.tipo,
        data_inicio=inicio,
        data_fim=fim,
    )
    sessao.commit()

    destino = pathlib.Path(args.saida or _nome_padrao(empresa, args.tipo, inicio))
    gravar(destino, resultado.texto())

    print(f"\n{TIPOS[args.tipo]} — {inicio} a {fim}")
    print(f"  arquivo       {destino}")
    print(f"  linhas        {resultado.total_linhas}")
    print(f"  documentos    {len(resultado.documentos_ids)}")
    print(f"  escrituração  #{escrituracao.id}  ({escrituracao.hash_conteudo[:16]}…)")

    if resultado.avisos:
        print("\n  LEIA ANTES DE TRANSMITIR:")
        for aviso in resultado.avisos:
            print(f"    · {aviso}")
    print()
    return 0


def _espelho(sessao: Session, args) -> int:
    """O arquivo em forma de leitura, ANTES de gerar.

    Não grava escrituração e não escreve arquivo SPED, ao contrário de
    `gerar`. Não é exceção à regra de que gerar sempre arquiva: o espelho é
    prosa, não arquivo transmissível, e ninguém o entrega por engano.

    Sai com 2 quando alguma conferência falhou — o mesmo código de `conferir`,
    pela mesma razão: cabe em rotina de fechamento que decide se prossegue.
    """
    empresa = _empresa(sessao, args.empresa)
    inicio, fim = _periodo(args)

    gerador = GERADORES[args.tipo](sessao, empresa=empresa, data_inicio=inicio, data_fim=fim)
    visao = espelho(gerador.gerar(), tipo=args.tipo)

    texto = visao.texto()
    print()
    print(texto)

    if args.saida:
        gravar(pathlib.Path(args.saida), texto)
        print(f"  espelho gravado em {args.saida}\n")

    return DIVERGENTE if visao.divergencias() else 0


def _ajuste(sessao: Session, args) -> int:
    """Cadastra ou lista os ajustes de apuração (E111) do período.

    O código vem da tabela 5.1.1 do **seu estado**: o sistema confere a
    estrutura — UF, apuração e utilização — e não o sequencial, que muda por
    ato normativo e é diferente em cada Secretaria da Fazenda. Guardar essa
    tabela aqui seria guardar uma tabela errada para 26 dos 27 estados.
    """
    empresa = _empresa(sessao, args.empresa)
    inicio, fim = _periodo(args)

    if args.codigo:
        if args.valor is None:
            raise ValueError("`--valor` é obrigatório quando se informa `--codigo`")
        ajuste = criar_ajuste(
            sessao,
            empresa=empresa,
            data_inicio=inicio,
            data_fim=fim,
            cod_aj=args.codigo,
            valor=_numero_do_terminal(args.valor),
            descricao=args.descricao,
        )
        sessao.commit()
        rotulo, campo = utilizacao(ajuste.cod_aj)
        destino = campo or "NENHUM campo do E110 — é controle extra-apuração"
        print(f"\nAjuste #{ajuste.id} cadastrado: {ajuste.cod_aj} ({rotulo})")
        print(f"  valor    {fmt_moeda(ajuste.valor)}")
        print(f"  vai para {destino}\n")
        return 0

    ajustes = ajustes_do_periodo(sessao, empresa_id=empresa.id, data_inicio=inicio, data_fim=fim)
    if not ajustes:
        print(f"\nNenhum ajuste de apuração em {inicio} a {fim}.\n")
        return 0

    print(f"\nAjustes de apuração — {empresa.nome}, {inicio} a {fim}")
    print(f"\n{'ID':>5}  {'Código':10} {'Valor':>14}  {'Vai para':20} Utilização")
    for a in ajustes:
        rotulo, campo = utilizacao(a.cod_aj)
        print(
            f"{a.id:>5}  {a.cod_aj:10} {fmt_moeda(a.valor):>14}  "
            f"{(campo or '— fora da apuração'):20} {rotulo}"
        )
    print()
    return 0


def _numero_do_terminal(bruto: str) -> float:
    """`1.234,56` ou `1234.56` — quem digita usa o formato que conhece."""
    texto = str(bruto).strip()
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError as erro:
        raise ValueError(f"{bruto!r} não é um valor numérico") from erro


def _apurar(sessao: Session, args) -> int:
    """CBS, IBS e Imposto Seletivo do período.

    Não gera arquivo nem grava nada: é leitura. Os tributos da Reforma ainda
    não têm obrigação acessória neste sistema, e apresentar o número como se
    fosse uma escrituração daria a entender que algo foi entregue.
    """
    empresa = _empresa(sessao, args.empresa)
    inicio, fim = _periodo(args)
    resultado = ApuracaoIBSCBS(sessao, empresa=empresa, data_inicio=inicio, data_fim=fim).apurar()

    print(f"\nApuração da Reforma — {inicio} a {fim}")
    print(f"  documentos    {resultado.documentos}\n")

    print(f"  {'Tributo':16} {'Débito':>14} {'Crédito':>14} {'Devido':>14}")
    for tributo in (resultado.cbs, resultado.ibs_uf, resultado.ibs_municipal):
        # O saldo credor sai na própria linha do tributo: numa linha à parte,
        # ele parece um quarto tributo.
        sobra = f"   saldo credor {fmt_moeda(tributo.saldo_credor)}" if tributo.saldo_credor else ""
        print(
            f"  {tributo.nome:16} {fmt_moeda(tributo.debito):>14} "
            f"{fmt_moeda(tributo.credito):>14} {fmt_moeda(tributo.devido):>14}{sobra}"
        )

    # O Seletivo leva travessão na coluna de crédito, não zero: ele não tem
    # crédito, e "0,00" faria parecer que tem e ficou zerado.
    print(
        f"  {'Seletivo':16} {fmt_moeda(resultado.seletivo):>14} {'—':>14} "
        f"{fmt_moeda(resultado.seletivo):>14}"
    )

    print(f"\n  {'TOTAL':16} {'':>29} {fmt_moeda(resultado.total_devido):>14}")

    # Fora do total de propósito: são valores que o documento traz e que a
    # apuração não sabe tratar.  Somá-los seria inventar o tratamento; omiti-los
    # da tela seria esconder que existem.
    if resultado.nao_cobertos:
        print("\n  FORA DO TOTAL — precisam de tratamento próprio:")
        for rotulo, (valor, itens) in sorted(resultado.nao_cobertos.items()):
            print(f"    {rotulo:34} {fmt_moeda(valor):>14}  em {itens} item(ns)")

    if resultado.cst_encontrados:
        codigos = ", ".join(
            f"{cst} ({itens})" for cst, itens in sorted(resultado.cst_encontrados.items())
        )
        print(f"\n  CST de IBS/CBS fora da tributação integral: {codigos}")

    print("\n  LEIA ANTES DE USAR ESTE NÚMERO:")
    for aviso in resultado.avisos:
        print(f"    · {aviso}")
    print()
    return 0


def _historico(sessao: Session, args) -> int:
    consulta = select(Escrituracao).order_by(Escrituracao.gerada_em, Escrituracao.id)
    if args.empresa:
        consulta = consulta.where(Escrituracao.empresa_id == args.empresa)
    if args.transmitidas:
        consulta = consulta.where(Escrituracao.transmitida_em.is_not(None))
    escrituracoes = sessao.execute(consulta).scalars().all()

    if not escrituracoes:
        print(
            "Nenhuma escrituração transmitida."
            if args.transmitidas
            else "Nenhuma escrituração gerada."
        )
        return 0

    print(
        f"\n{'ID':>5}  {'Empresa':>8} {'Tipo':20} {'Período':24} {'Linhas':>7} Entrega          Hash"
    )
    for e in escrituracoes:
        periodo = f"{e.data_inicio} a {e.data_fim}"
        # A coluna diz "gerada mas não entregue" com um travessão, não com
        # vazio: campo em branco se lê como coluna que não se aplica.
        entrega = f"{e.transmitida_em:%d/%m/%Y}" if e.transmitida else "—"
        if e.recibo:
            entrega += f" {e.recibo}"
        print(
            f"{e.id:>5}  {e.empresa_id:>8} {e.tipo:20} {periodo:24} "
            f"{e.total_linhas:>7} {entrega:16} {e.hash_conteudo[:16]}…"
        )
    print()
    return 0


def _transmitida(sessao: Session, args) -> int:
    """Registra qual geração foi a entregue.

    O sistema não transmite — quem transmite é o programa da Receita —, então
    a informação vem de fora e precisa ser dita. Enquanto ninguém disser,
    nenhuma escrituração é marcada.
    """
    escrituracao = sessao.get(Escrituracao, args.escrituracao)
    if escrituracao is None:
        raise LookupError(
            f"não existe escrituração #{args.escrituracao} — "
            "`sped-hub fiscal historico` lista as geradas"
        )

    marcar_transmitida(
        sessao,
        escrituracao,
        recibo=args.recibo,
        forcar=args.forcar,
    )
    sessao.commit()

    print(f"\nEscrituração #{escrituracao.id} — {TIPOS[escrituracao.tipo]}")
    print(f"  período       {escrituracao.data_inicio} a {escrituracao.data_fim}")
    print(f"  transmitida   {escrituracao.transmitida_em:%d/%m/%Y %H:%M}")
    print(f"  recibo        {escrituracao.recibo or '— não informado'}")
    print(f"  hash          {escrituracao.hash_conteudo[:16]}…")

    if outras := transmitidas_do_periodo(sessao, escrituracao):
        ids = ", ".join(f"#{e.id}" for e in outras)
        print(f"\n  o período já tinha entrega: {ids} — esta é uma retificação.")
    print()
    return 0


def _conferir(sessao: Session, args) -> int:
    """O arquivo entregue contra o que sairia agora.

    É a pergunta que aparece quando alguém mexe num documento depois do
    fechamento: "o que eu enviei ainda corresponde ao que está no sistema?".
    """
    escrituracao = sessao.get(Escrituracao, args.escrituracao)
    if escrituracao is None:
        raise LookupError(
            f"não existe escrituração #{args.escrituracao} — "
            "`sped-hub fiscal historico` lista as geradas"
        )

    empresa = _empresa(sessao, escrituracao.empresa_id)
    resultado = GERADORES[escrituracao.tipo](
        sessao,
        empresa=empresa,
        data_inicio=escrituracao.data_inicio,
        data_fim=escrituracao.data_fim,
    ).gerar()

    comparacao = comparar(escrituracao, resultado)
    print(
        f"\nEscrituração #{escrituracao.id} — {TIPOS[escrituracao.tipo]}, "
        f"{escrituracao.data_inicio} a {escrituracao.data_fim}"
    )

    if comparacao.iguais:
        print("  o arquivo entregue continua igual ao que sairia agora.\n")
        return 0

    print("  DIVERGIU do que sairia agora:")
    for linha in comparacao.resumo:
        print(f"    · {linha}")
    if args.diff:
        print()
        for linha in comparacao.diff:
            print(f"    {linha}")
    else:
        print("\n  (use --diff para ver as linhas)")

    if avisos := avisos_de(escrituracao):
        print("\n  avisos de quando foi gerada:")
        for aviso in avisos:
            print(f"    · {aviso}")
    print()
    return DIVERGENTE


# ── Classificar e corrigir: o meio do fluxo ────────────────────────────────


def _classificar(sessao: Session, args) -> int:
    """As regras propõem; ninguém aplica em silêncio.

    Sem `--aplicar` isto é só leitura — a etapa "revisar" do fluxo. O motor já
    trabalha assim por dentro (`avaliar` nunca grava), e a CLI não seria o
    lugar de inverter isso.
    """
    empresa = _empresa(sessao, args.empresa)
    documentos = _documentos_do_recorte(sessao, empresa, args)
    if not documentos:
        print("Nenhum documento no recorte.")
        return 0

    motor = MotorDeClassificacao(sessao, obrigacao=args.tipo if args.obrigacao else None)
    sugestoes_por_documento: list[tuple[DocumentoFiscal, list]] = []
    conflitos = []
    for documento in documentos:
        resultado = motor.avaliar(documento)
        if resultado.sugestoes:
            sugestoes_por_documento.append((documento, resultado.sugestoes))
        conflitos.extend((documento, c) for c in resultado.conflitos)

    total = sum(len(s) for _, s in sugestoes_por_documento)
    print(f"\n{total} sugestões em {len(sugestoes_por_documento)} documentos.")

    for documento, sugestoes in sugestoes_por_documento:
        print(f"\n  documento #{documento.id} ({documento.modelo}/{documento.numero})")
        for sugestao in sugestoes:
            onde = f"item {sugestao.numero_item}" if sugestao.numero_item else "cabeçalho"
            # A confiança só aparece quando não é total: repeti-la em toda
            # linha esconderia justamente a que merece atenção.
            duvida = "" if sugestao.confianca >= 1.0 else f"  (confiança {sugestao.confianca:.0%})"
            print(
                f"    {onde:12} {sugestao.campo:16} "
                f"{sugestao.valor_anterior!r} → {sugestao.valor_sugerido!r}"
                f"  [{sugestao.regra_nome}]{duvida}"
            )

    # Conflito é o que o motor se recusa a resolver por sorteio; escondê-lo
    # faria a classificação parecer completa quando ela parou no meio.
    if conflitos:
        print(f"\n  {len(conflitos)} CONFLITOS — regras de mesma prioridade disputando o campo:")
        for documento, conflito in conflitos:
            print(f"    documento #{documento.id}: {conflito}")

    if not args.aplicar:
        print("\n  (nada foi gravado; use --aplicar para gravar num lote reversível)\n")
        return 0

    if not total:
        print()
        return 0

    lote = novo_lote()
    for documento, sugestoes in sugestoes_por_documento:
        aplicar_classificacao(sessao, documento, sugestoes, lote=lote)
    sessao.commit()
    print(f"\n  {total} ajustes gravados no lote {lote}")
    print(f"  para desfazer: sped-hub fiscal desfazer --lote {lote}\n")
    return 0


def _filtro(bruto: str) -> Filtro:
    """`campo:valor`, `campo:operador:valor` ou `campo:operador`.

    Dois-pontos porque valor fiscal — NCM, CFOP, CST, CNPJ — não tem
    dois-pontos dentro, e o `=` apareceria em descrição de produto.
    """
    partes = bruto.split(":")
    if len(partes) == 2 and partes[1] in OPERADORES:
        return Filtro(campo=partes[0], operador=partes[1])
    if len(partes) == 2:
        return Filtro(campo=partes[0], operador="igual", valor=partes[1])
    if len(partes) == 3:
        return Filtro(campo=partes[0], operador=partes[1], valor=partes[2])
    raise ValueError(
        f"filtro {bruto!r} não tem forma reconhecida — use campo:valor, "
        f"campo:operador:valor ou campo:operador (operadores: {', '.join(sorted(OPERADORES))})"
    )


def _condicao(bruto: str) -> dict:
    """A condição de uma regra, na mesma sintaxe do `--filtro`.

    Reusa `_filtro` de propósito: as duas coisas são a mesma pergunta — "quais
    documentos casam com isto" —, e duas sintaxes para a mesma pergunta
    acabariam divergindo, com quem usa tendo de lembrar qual vale onde.
    """
    filtro = _filtro(bruto)
    condicao = {"campo": filtro.campo, "operador": filtro.operador}
    # `vazio` e `preenchido` não têm valor; gravar `"valor": None` faria a
    # regra parecer comparar com nulo.
    if filtro.valor is not None:
        condicao["valor"] = filtro.valor
    return condicao


def _acao(bruto: str) -> dict:
    """`campo:valor` — a ação não tem operador, ela atribui."""
    campo, separador, valor = bruto.partition(":")
    if not separador or not campo:
        raise ValueError(
            f"ação {bruto!r} não tem forma reconhecida — use campo:valor, " "como cfop:2102"
        )
    return {"campo": campo, "valor": valor}


def _regras(sessao: Session, args) -> int:
    """Cadastra, lista e remove as regras de classificação.

    Sem isto, `classificar` não tem o que aplicar: a única forma de criar uma
    regra era escrever Python.
    """
    if args.acao_regra == "listar":
        return _regras_listar(sessao, args)
    if args.acao_regra == "remover":
        return _regras_remover(sessao, args)
    return _regras_criar(sessao, args)


def _regras_listar(sessao: Session, args) -> int:
    consulta = select(RegraFiscal).order_by(RegraFiscal.prioridade.desc(), RegraFiscal.id)
    if args.empresa:
        consulta = consulta.where(
            or_(RegraFiscal.empresa_id == args.empresa, RegraFiscal.empresa_id.is_(None))
        )
    regras = sessao.execute(consulta).scalars().all()
    if not regras:
        print("Nenhuma regra cadastrada.")
        return 0

    print(f"\n{'ID':>4}  {'Prio':>5} {'Ativa':6} {'Vigência':24} Nome")
    for r in regras:
        vigencia = f"{r.vigencia_inicio or '—'} a {r.vigencia_fim or '—'}"
        print(
            f"{r.id:>4}  {r.prioridade:>5} {'sim' if r.ativa else 'não':6} "
            f"{vigencia:24} {r.nome}"
        )
        for condicao in json.loads(r.condicoes):
            valor = condicao.get("valor")
            alvo = "" if valor is None else f" {valor!r}"
            print(f"        se   {condicao['campo']} {condicao.get('operador', 'igual')}{alvo}")
        for acao in json.loads(r.acoes):
            print(f"        então {acao['campo']} = {acao['valor']!r}")
    print()
    return 0


def _regras_criar(sessao: Session, args) -> int:
    regra = criar_regra(
        sessao,
        nome=args.nome,
        condicoes=[_condicao(c) for c in args.se or []],
        acoes=[_acao(a) for a in args.entao or []],
        escritorio_id=args.escritorio,
        empresa_id=args.empresa,
        descricao=args.descricao,
        prioridade=args.prioridade,
        obrigacao=args.tipo if args.obrigacao else None,
        vigencia_inicio=_data(args.de) if args.de else None,
        vigencia_fim=_data(args.ate) if args.ate else None,
    )
    sessao.commit()
    print(f"\nRegra #{regra.id} criada: {regra.nome}")
    print("  confira com `sped-hub fiscal classificar` antes de aplicar.\n")
    return 0


def _regras_remover(sessao: Session, args) -> int:
    """Desativa em vez de apagar.

    Uma regra apagada deixaria os ajustes que ela gerou sem explicação: o
    `AjusteFiscal` guarda o nome da regra, e quem for auditar o mês vai
    procurar qual era a condição.
    """
    regra = sessao.get(RegraFiscal, args.regra)
    if regra is None:
        raise LookupError(
            f"não existe regra #{args.regra} — `sped-hub fiscal regras listar` mostra as cadastradas"
        )
    regra.ativa = False
    sessao.commit()
    print(f"\nRegra #{regra.id} desativada: {regra.nome}")
    print("  os ajustes que ela já gerou continuam; desfaça o lote para revertê-los.\n")
    return 0


def _valor_tipado(campo: str, bruto: str):
    """O texto da linha de comando, no tipo que a coluna espera.

    Argumento de terminal é sempre `str`. Sem converter, alterar `base_icms`
    para `1000` mostraria **impacto R$ 0,00** na simulação — porque a
    diferença entre `0.0` e `"1000"` não é numérica —, e a simulação existe
    exatamente para mostrar o impacto financeiro antes de confirmar.

    A conversão é a mesma que a camada efetiva já usa para ler ajustes
    (`desserializar`): duas conversões diferentes para o mesmo campo acabariam
    divergindo.
    """
    for modelo in (ItemDocumentoFiscal, DocumentoFiscal):
        colunas = modelo.__table__.columns
        if campo in colunas:
            return desserializar(bruto, colunas[campo])
    raise ValueError(
        f"campo {campo!r} não existe em documento nem em item — "
        "alteração em massa com nome errado não alcançaria nada"
    )


def _alterar(sessao: Session, args) -> int:
    """Simula por padrão; gravar exige `--confirmar`.

    É a mesma separação que o módulo de massa já faz entre `simular` e
    `confirmar`, e a §16 do pedido: alteração em massa errada estraga o mês
    inteiro de uma vez, e a simulação é a única chance de perceber antes.
    """
    empresa = _empresa(sessao, args.empresa)
    selecao = Selecao(
        escritorio_id=empresa.escritorio_id,
        empresa_id=empresa.id,
        data_inicio=_data(args.de) if args.de else None,
        data_fim=_data(args.ate) if args.ate else None,
        filtros=[_filtro(f) for f in args.filtro or []],
    )
    alteracao = Alteracao(
        campo=args.campo,
        valor=_valor_tipado(args.campo, args.valor),
        apenas_vazios=args.apenas_vazios,
    )

    simulacao = simular(sessao, selecao, [alteracao])

    print(f"\n{simulacao.total_mudancas} mudanças em {simulacao.documentos_afetados} documentos")
    if simulacao.itens_afetados:
        print(f"  itens afetados     {simulacao.itens_afetados}")
    if simulacao.impacto_total:
        print(f"  impacto em reais   {fmt_moeda(simulacao.impacto_total)}")
    for campo, quantos in sorted(simulacao.por_campo().items()):
        print(f"  {campo:18} {quantos}")

    if simulacao.avisos:
        print("\n  avisos:")
        for aviso in simulacao.avisos:
            print(f"    {aviso}")

    if not args.confirmar:
        print("\n  (nada foi gravado; use --confirmar para gravar)\n")
        return 0

    if not simulacao.total_mudancas:
        print()
        return 0

    lote = confirmar(sessao, simulacao, motivo=args.motivo, forcar=args.forcar)
    sessao.commit()
    print(f"\n  gravado no lote {lote}")
    print(f"  para desfazer: sped-hub fiscal desfazer --lote {lote}\n")
    return 0


def _planilha(sessao: Session, args) -> int:
    """Exporta os itens do recorte, ou lê de volta a planilha corrigida.

    Com `--saida`, escreve o `.xlsx`. Com `--arquivo`, lê e mostra o que
    mudaria — e **não grava**, exatamente como `alterar` sem `--confirmar`.
    Uma planilha que gravasse ao ser lida seria a única escrita do sistema sem
    ninguém ver o que muda.
    """
    if args.arquivo:
        return _planilha_de_volta(sessao, args)

    empresa = _empresa(sessao, args.empresa) if args.empresa else None
    selecao = Selecao(
        escritorio_id=empresa.escritorio_id if empresa else args.escritorio,
        empresa_id=args.empresa,
        data_inicio=_data(args.de) if args.de else None,
        data_fim=_data(args.ate) if args.ate else None,
        filtros=[_filtro(bruto) for bruto in (args.filtro or [])],
    )
    padrao = f"itens_{args.empresa}.xlsx" if args.empresa else "itens.xlsx"
    destino = pathlib.Path(args.saida or padrao)
    destino.write_bytes(exportar(sessao, selecao))

    print(f"\nPlanilha gravada em {destino}")
    print("  corrija as colunas editáveis e volte com:")
    print(f"    sped-hub fiscal planilha --arquivo {destino}\n")
    return 0


def _planilha_de_volta(sessao: Session, args) -> int:
    resultado = reimportar(sessao, pathlib.Path(args.arquivo).read_bytes())
    simulacao = resultado.simulacao

    print(f"\n{resultado.linhas_lidas} linha(s) lida(s) de {args.arquivo}")
    print(f"  documentos    {simulacao.documentos_afetados}")
    print(f"  itens         {simulacao.itens_afetados}")
    print(f"  mudanças      {simulacao.total_mudancas}")
    print(f"  impacto       {fmt_moeda(simulacao.impacto_total)}")

    for mudanca in simulacao.mudancas[:20]:
        print(
            f"    item {mudanca.numero_item}: {mudanca.campo} "
            f"{mudanca.valor_anterior!r} → {mudanca.valor_novo!r}"
        )
    if simulacao.total_mudancas > 20:
        print(f"    … e mais {simulacao.total_mudancas - 20}")

    if resultado.divergencias:
        print("\n  LINHAS RECUSADAS:")
        for divergencia in resultado.divergencias:
            print(f"    · {divergencia}")

    if not args.confirmar:
        print("\n  nada foi gravado — use --confirmar para aplicar\n")
        return 0

    lote = confirmar(sessao, simulacao, motivo=args.motivo or f"planilha {args.arquivo}")
    sessao.commit()
    print(f"\n  gravado no lote {lote}")
    print(f"  desfaça com: sped-hub fiscal desfazer --lote {lote}\n")
    return 0


def _desfazer(sessao: Session, args) -> int:
    """Apagar os ajustes do lote basta — o normalizado nunca foi tocado."""
    quantos = desfazer_lote(sessao, args.lote)
    sessao.commit()
    if not quantos:
        print(f"\nNenhum ajuste no lote {args.lote}.\n")
        return 0
    print(f"\n{quantos} ajustes desfeitos do lote {args.lote}.\n")
    return 0


def _documentos_do_recorte(sessao: Session, empresa, args) -> list[DocumentoFiscal]:
    consulta = (
        select(DocumentoFiscal)
        .options(selectinload(DocumentoFiscal.itens))
        .where(DocumentoFiscal.empresa_id == empresa.id)
    )
    if args.de:
        consulta = consulta.where(DocumentoFiscal.data_emissao >= _data(args.de))
    if args.ate:
        consulta = consulta.where(DocumentoFiscal.data_emissao <= _data(args.ate))
    return list(
        sessao.execute(consulta.order_by(DocumentoFiscal.data_emissao, DocumentoFiscal.id))
        .scalars()
        .unique()
        .all()
    )


ACOES = {
    "empresas": lambda sessao, args: _empresas(sessao),
    "cadastro": _cadastro,
    "importar": _importar,
    "documentos": _documentos,
    "classificar": _classificar,
    "alterar": _alterar,
    "desfazer": _desfazer,
    "planilha": _planilha,
    "gerar": _gerar,
    "espelho": _espelho,
    "ajuste": _ajuste,
    "apurar": _apurar,
    "regras": _regras,
    "historico": _historico,
    "transmitida": _transmitida,
    "conferir": _conferir,
}


def cmd_fiscal(args) -> int:
    """Despacha a ação e traduz as falhas em mensagem legível.

    Quem usa isto é contador, não quem escreveu o código: um traceback de
    SQLAlchemy na tela não diz o que fazer, e o caso comum — banco sem schema
    — tem resposta de uma linha.
    """
    engine = criar_engine(args.db) if args.db else criar_engine()
    try:
        with Session(engine) as sessao:
            return ACOES[args.acao](sessao, args)
    except OperationalError as erro:
        if "no such table" in str(erro) or "does not exist" in str(erro):
            print("ERRO: o banco ainda não tem as tabelas — rode `sped-hub migrar` antes.")
        else:
            print(f"ERRO: falha no banco: {erro.orig}")
        return 1
    except (
        AlteracaoInvalida,
        CampoObrigatorioAusente,
        LookupError,
        OSError,
        AjusteInvalido,
        PlanilhaInvalida,
        RegraInvalida,
        SelecaoVazia,
        TransmissaoInvalida,
        ValueError,
    ) as erro:
        # Todas menos `OSError` já são `ValueError`; estão nomeadas porque a
        # lista é o que diz quais falhas do domínio a CLI se compromete a
        # traduzir em mensagem em vez de traceback.
        print(f"ERRO: {erro}")
        return 1
    finally:
        engine.dispose()


def registrar(sub) -> None:
    """Acrescenta `fiscal` ao parser da CLI."""
    p = sub.add_parser(
        "fiscal",
        help="Central de Documentos, geração de SPED e escriturações arquivadas",
    )
    p.add_argument("acao", choices=sorted(ACOES), help=" | ".join(sorted(ACOES)))
    p.add_argument(
        "caminhos",
        nargs="*",
        help="Arquivos ou pastas de XML (em `importar`); pasta é varrida por .xml",
    )
    p.add_argument("--empresa", type=int, help="ID da empresa")
    p.add_argument("--escritorio", type=int, help="ID do escritório dono (em `importar`)")
    p.add_argument(
        "--tipo",
        choices=sorted(GERADORES),
        default="efd_icms",
        help="Escrituração a gerar (default: efd_icms)",
    )
    p.add_argument("--de", help="Início do período (AAAA-MM-DD ou DDMMAAAA)")
    p.add_argument("--ate", help="Fim do período (AAAA-MM-DD ou DDMMAAAA)")
    p.add_argument("--saida", help="Caminho do arquivo gerado")
    p.add_argument("--escrituracao", type=int, help="ID da escrituração (em `conferir`)")
    p.add_argument("--diff", action="store_true", help="Mostra as linhas divergentes")
    p.add_argument("--recibo", help="Número do recibo do Fisco (em `transmitida`)")
    p.add_argument("--codigo", help="Código da tabela 5.1.1 do seu estado (em `ajuste`)")
    p.add_argument("--arquivo", help="Planilha a reimportar (em `planilha`)")

    # ── cadastro fiscal ────────────────────────────────────────────────────
    # Sem `choices=`: o argparse recusaria com código 2, que nesta CLI quer
    # dizer "divergiu" e seria lido como divergência por um script de
    # fechamento.  E a mensagem dele lista os códigos sem as descrições, que é
    # justamente o que importa aqui — ninguém erra "2", erra o significado
    # de 2.  A conferência é em `_cadastro`, com a tabela inteira na recusa.
    for campo, (rotulo, tabela) in CADASTRO_FISCAL.items():
        p.add_argument(
            f"--{campo.replace('_', '-')}",
            dest=campo,
            metavar="CODIGO",
            help=f"{rotulo} ({', '.join(f'{c}={d}' for c, d in sorted(tabela.items()))})",
        )
    p.add_argument(
        "--transmitidas",
        action="store_true",
        help="Em `historico`: só as que foram entregues",
    )

    # ── classificar ────────────────────────────────────────────────────────
    p.add_argument(
        "--aplicar",
        action="store_true",
        help="Em `classificar`: grava as sugestões. Sem ele, só mostra",
    )
    p.add_argument(
        "--obrigacao",
        action="store_true",
        help="Em `classificar`: só as regras da obrigação de `--tipo`",
    )

    # ── alterar ────────────────────────────────────────────────────────────
    p.add_argument("--campo", help="Campo a alterar (em `alterar`)")
    p.add_argument("--valor", help="Valor novo (em `alterar`)")
    p.add_argument(
        "--filtro",
        action="append",
        metavar="CAMPO:[OPERADOR:]VALOR",
        help=f"Recorte; repetível, todos precisam casar. Operadores: {', '.join(sorted(OPERADORES))}",
    )
    p.add_argument(
        "--apenas-vazios",
        action="store_true",
        help="Preenche só o que está vazio, sem tocar no que já tem valor",
    )
    p.add_argument(
        "--confirmar",
        action="store_true",
        help="Em `alterar`: grava. Sem ele, só simula",
    )
    p.add_argument(
        "--forcar",
        action="store_true",
        help="Grava mesmo com aviso impeditivo — fica registrado no motivo",
    )
    p.add_argument("--motivo", help="Por que a alteração foi feita; vai para o histórico")
    p.add_argument("--lote", help="Lote a desfazer (em `desfazer`)")

    # ── regras ─────────────────────────────────────────────────────────────
    p.add_argument(
        "--acao-regra",
        choices=["listar", "criar", "remover"],
        default="listar",
        help="Em `regras`: listar (default) | criar | remover",
    )
    p.add_argument("--nome", help="Nome da regra (em `regras criar`)")
    p.add_argument("--descricao", help="Descrição da regra ou do ajuste de apuração")
    p.add_argument(
        "--se",
        action="append",
        metavar="CAMPO:[OPERADOR:]VALOR",
        help="Condição da regra; repetível, todas precisam casar. Mesma sintaxe do --filtro",
    )
    p.add_argument(
        "--entao",
        action="append",
        metavar="CAMPO:VALOR",
        help="O que a regra propõe; repetível",
    )
    p.add_argument(
        "--prioridade",
        type=int,
        default=0,
        help="Maior vence; empate no mesmo campo é conflito, e o motor o denuncia",
    )
    p.add_argument("--regra", type=int, help="ID da regra (em `regras remover`)")

    p.add_argument("--db", default=None, help="Banco (URL ou caminho SQLite)")


# Argumentos sem os quais a ação não tem o que fazer.  Conferir aqui, e não
# dentro de cada função, é o que dá a mesma mensagem para todos os casos.
OBRIGATORIOS = {
    "importar": ("caminhos",),
    "cadastro": ("empresa",),
    "documentos": ("empresa",),
    "classificar": ("empresa",),
    "alterar": ("empresa", "campo", "valor"),
    "desfazer": ("lote",),
    "gerar": ("empresa", "de", "ate"),
    "espelho": ("empresa", "de", "ate"),
    "ajuste": ("empresa", "de", "ate"),
    "apurar": ("empresa", "de", "ate"),
    "conferir": ("escrituracao",),
    "transmitida": ("escrituracao",),
}

# `regras` depende da ação: criar exige o que define a regra, remover exige o
# alvo, listar não exige nada.
OBRIGATORIOS_REGRAS = {
    "criar": ("nome", "se", "entao"),
    "remover": ("regra",),
}


def conferir_argumentos(args) -> str | None:
    """A mensagem de erro, ou `None` se está tudo lá."""
    if args.acao == "regras":
        exigidos = OBRIGATORIOS_REGRAS.get(args.acao_regra, ())
        onde = f"fiscal regras --acao-regra {args.acao_regra}"
    else:
        exigidos = OBRIGATORIOS.get(args.acao, ())
        onde = f"fiscal {args.acao}"

    faltando = [nome for nome in exigidos if not getattr(args, nome, None)]
    if not faltando:
        return None
    rotulos = ", ".join(
        "caminhos" if n == "caminhos" else f"--{n.replace('_', '-')}" for n in faltando
    )
    verbo = "são obrigatórios" if len(faltando) > 1 else "é obrigatório"
    return f"ERRO: {rotulos} {verbo} em `{onde}`."
