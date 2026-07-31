"""Central de Documentos Fiscais — importação, normalização e tratamento.

**Estado: em construção.**  Existem o modelo de dados (em :mod:`src.db.models`),
o adaptador de NF-e e o importador em lote.  Faltam os adaptadores de NFS-e, a
classificação fiscal, as alterações em massa e os geradores de escrituração —
ver ``docs/roadmap.md``.

O fluxo que ele abriga:

    XML → adaptador → documento normalizado → (ajustes) → escrituração

O ponto de projeto que sustenta o resto da suíte são as três camadas, mantidas
separadas no banco:

    1. ORIGINAL     o XML como chegou, byte a byte.  Nunca reescrito.
    2. NORMALIZADO  os campos extraídos numa estrutura única, igual para
                    NF-e, NFC-e e NFS-e de qualquer provedor.  Também imutável.
    3. EFETIVO      o que vai para o SPED — o normalizado mais os ajustes,
                    calculado na hora, não gravado.

A terceira camada ser calculada é o que torna a reversão trivial (apagar os
ajustes de um lote) e a auditoria exata (listar os ajustes de um campo responde
"por que este registro saiu assim?").  Gravar o valor final numa coluna faria
as três camadas divergirem no primeiro ``UPDATE`` escrito fora do fluxo.

**Adaptadores, não um parser único.**  Cada origem tem leiaute próprio — a
NF-e é nacional e estável, a NFS-e varia por município e por provedor.  Um
parser único viraria uma cascata de condicionais que ninguém altera sem
quebrar outro município.  Cada adaptador converterá a sua origem para a mesma
estrutura, e o resto do sistema só conhecerá essa.

Sobre CBS, IBS e Imposto Seletivo, ver ``docs/reforma-tributaria.md``.
"""

from src.documentos.adaptadores import (
    ADAPTADORES,
    Adaptador,
    AdaptadorNFe,
    DocumentoNormalizado,
    ItemNormalizado,
    OrigemNaoReconhecida,
    XMLPerigoso,
    adaptador_para,
    carregar_xml,
    registrar_adaptador,
)
from src.documentos.ajustes import (
    ORIGEM_REGRA,
    ORIGEM_USUARIO,
    CampoInexistente,
    OrigemInvalida,
    VisaoEfetiva,
    aplicar_ajuste,
    desfazer_lote,
    efetivo,
    historico,
    novo_lote,
    valor_efetivo,
)
from src.documentos.classificacao import (
    OPERADORES,
    Conflito,
    MotorDeClassificacao,
    RegraInvalida,
    ResultadoClassificacao,
    Sugestao,
    aplicar,
    criar_regra,
    regras_aplicaveis,
    validar_regra,
)
from src.documentos.importador import (
    Desfecho,
    ImportadorDeDocumentos,
    Ocorrencia,
    PoliticaDeDuplicidade,
    ResultadoImportacao,
    Sentido,
)
from src.documentos.massa import (
    Alteracao,
    AlteracaoInvalida,
    Aviso,
    Filtro,
    Mudanca,
    Selecao,
    SelecaoVazia,
    Simulacao,
    confirmar,
    recalcular,
    simular,
)
from src.documentos.planilha import (
    COLUNAS,
    EDITAVEIS,
    Divergencia,
    PlanilhaInvalida,
    Reimportacao,
    exportar,
    reimportar,
)

__all__ = [
    "ADAPTADORES",
    "COLUNAS",
    "EDITAVEIS",
    "OPERADORES",
    "ORIGEM_REGRA",
    "ORIGEM_USUARIO",
    "Adaptador",
    "AdaptadorNFe",
    "Alteracao",
    "AlteracaoInvalida",
    "Aviso",
    "CampoInexistente",
    "Conflito",
    "Desfecho",
    "Divergencia",
    "DocumentoNormalizado",
    "Filtro",
    "ImportadorDeDocumentos",
    "ItemNormalizado",
    "MotorDeClassificacao",
    "Mudanca",
    "Ocorrencia",
    "OrigemInvalida",
    "OrigemNaoReconhecida",
    "PlanilhaInvalida",
    "PoliticaDeDuplicidade",
    "RegraInvalida",
    "Reimportacao",
    "ResultadoClassificacao",
    "ResultadoImportacao",
    "Selecao",
    "SelecaoVazia",
    "Sentido",
    "Simulacao",
    "Sugestao",
    "VisaoEfetiva",
    "XMLPerigoso",
    "adaptador_para",
    "aplicar",
    "aplicar_ajuste",
    "carregar_xml",
    "confirmar",
    "criar_regra",
    "desfazer_lote",
    "efetivo",
    "exportar",
    "historico",
    "novo_lote",
    "recalcular",
    "registrar_adaptador",
    "regras_aplicaveis",
    "reimportar",
    "simular",
    "validar_regra",
    "valor_efetivo",
]
