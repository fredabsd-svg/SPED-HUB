"""Central de Documentos Fiscais — importação, normalização e tratamento.

**Estado: em construção.**  Hoje este pacote não tem código executável — o que
existe da Central é o modelo de dados, em :mod:`src.db.models`
(``DocumentoFiscal``, ``ItemDocumentoFiscal``, ``AjusteFiscal``).  O pacote
existe para documentar a arquitetura antes de ela ser preenchida, e some ou
cresce conforme o trabalho seguir.

O fluxo que ele vai abrigar:

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

Sobre CBS, IBS e Imposto Seletivo, ver [`docs/reforma-tributaria.md`].
"""
