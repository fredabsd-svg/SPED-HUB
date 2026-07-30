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
import pathlib

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from src.db.models import (
    DocumentoFiscal,
    Empresa,
    Escrituracao,
    criar_engine,
)
from src.documentos import ImportadorDeDocumentos
from src.escrituracoes import (
    TIPOS,
    CampoObrigatorioAusente,
    GeradorEFDContribuicoes,
    GeradorEFDICMS,
    arquivar,
    avisos_de,
    comparar,
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


def _historico(sessao: Session, args) -> int:
    consulta = select(Escrituracao).order_by(Escrituracao.gerada_em, Escrituracao.id)
    if args.empresa:
        consulta = consulta.where(Escrituracao.empresa_id == args.empresa)
    escrituracoes = sessao.execute(consulta).scalars().all()

    if not escrituracoes:
        print("Nenhuma escrituração gerada.")
        return 0

    print(f"\n{'ID':>5}  {'Empresa':>8} {'Tipo':20} {'Período':24} {'Linhas':>7} Hash")
    for e in escrituracoes:
        periodo = f"{e.data_inicio} a {e.data_fim}"
        print(
            f"{e.id:>5}  {e.empresa_id:>8} {e.tipo:20} {periodo:24} "
            f"{e.total_linhas:>7} {e.hash_conteudo[:16]}…"
        )
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


ACOES = {
    "empresas": lambda sessao, args: _empresas(sessao),
    "importar": _importar,
    "documentos": _documentos,
    "gerar": _gerar,
    "historico": _historico,
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
    except (CampoObrigatorioAusente, LookupError, ValueError, OSError) as erro:
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
    p.add_argument("--db", default=None, help="Banco (URL ou caminho SQLite)")


# Argumentos sem os quais a ação não tem o que fazer.  Conferir aqui, e não
# dentro de cada função, é o que dá a mesma mensagem para todos os casos.
OBRIGATORIOS = {
    "importar": ("caminhos",),
    "documentos": ("empresa",),
    "gerar": ("empresa", "de", "ate"),
    "conferir": ("escrituracao",),
}


def conferir_argumentos(args) -> str | None:
    """A mensagem de erro, ou `None` se está tudo lá."""
    faltando = [nome for nome in OBRIGATORIOS.get(args.acao, ()) if not getattr(args, nome, None)]
    if not faltando:
        return None
    rotulos = ", ".join("caminhos" if n == "caminhos" else f"--{n}" for n in faltando)
    return f"ERRO: {rotulos} {'são obrigatórios' if len(faltando) > 1 else 'é obrigatório'} em `fiscal {args.acao}`."
