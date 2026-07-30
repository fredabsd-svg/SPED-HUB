"""Importação de documentos fiscais, em lote, sem duplicar em silêncio."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import DocumentoFiscal, Empresa, ItemDocumentoFiscal
from src.documentos.adaptadores import (
    DocumentoNormalizado,
    OrigemNaoReconhecida,
    adaptador_para,
)

logger = logging.getLogger("sped-hub.documentos")


class Desfecho(StrEnum):
    """O que aconteceu com um documento oferecido à importação."""

    IMPORTADO = "importado"
    DUPLICADO = "duplicado"
    SUBSTITUIDO = "substituido"
    REJEITADO = "rejeitado"


class PoliticaDeDuplicidade(StrEnum):
    """O que fazer quando a chave já existe.

    O padrão é `IGNORAR` porque reimportar a mesma pasta é rotina — e porque
    substituir por engano apagaria os ajustes já feitos sobre o documento.
    """

    IGNORAR = "ignorar"
    SUBSTITUIR = "substituir"
    ERRO = "erro"


class Sentido(StrEnum):
    ENTRADA = "entrada"
    SAIDA = "saida"


@dataclass
class Ocorrencia:
    """O que aconteceu com um documento, e por quê."""

    desfecho: Desfecho
    origem: str | None = None
    chave: str | None = None
    documento_id: int | None = None
    motivo: str | None = None


@dataclass
class ResultadoImportacao:
    """O relatório de um lote."""

    ocorrencias: list[Ocorrencia] = field(default_factory=list)

    def _quantos(self, desfecho: Desfecho) -> int:
        return sum(1 for o in self.ocorrencias if o.desfecho is desfecho)

    @property
    def importados(self) -> int:
        return self._quantos(Desfecho.IMPORTADO)

    @property
    def duplicados(self) -> int:
        return self._quantos(Desfecho.DUPLICADO)

    @property
    def substituidos(self) -> int:
        return self._quantos(Desfecho.SUBSTITUIDO)

    @property
    def rejeitados(self) -> int:
        return self._quantos(Desfecho.REJEITADO)

    @property
    def total(self) -> int:
        return len(self.ocorrencias)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "importados": self.importados,
            "duplicados": self.duplicados,
            "substituidos": self.substituidos,
            "rejeitados": self.rejeitados,
            "ocorrencias": [
                {
                    "desfecho": str(o.desfecho),
                    "origem": o.origem,
                    "chave": o.chave,
                    "documento_id": o.documento_id,
                    "motivo": o.motivo,
                }
                for o in self.ocorrencias
            ],
        }


class ImportadorDeDocumentos:
    """Recebe conteúdo bruto e grava documentos normalizados.

    Não decide nada de tributação: só lê, identifica e guarda, preservando o
    original.  A classificação fiscal é etapa seguinte, e vive em cima dos
    ajustes.
    """

    def __init__(
        self,
        session: Session,
        *,
        escritorio_id: int | None = None,
        politica: PoliticaDeDuplicidade = PoliticaDeDuplicidade.IGNORAR,
    ):
        self.session = session
        self.escritorio_id = escritorio_id
        self.politica = politica

    def importar_lote(
        self, arquivos: Iterable[tuple[str, bytes]], *, origem: str = "arquivo"
    ) -> ResultadoImportacao:
        """Importa vários documentos.

        Um arquivo ruim não derruba o lote: vira uma ocorrência `rejeitado`
        com o motivo, e os outros seguem.  Importar mil XML e perder tudo por
        causa de um corrompido seria inaceitável na rotina de fechamento.
        """
        resultado = ResultadoImportacao()
        for nome, conteudo in arquivos:
            resultado.ocorrencias.append(self._um(nome, conteudo, origem))
        return resultado

    def importar(
        self, conteudo: bytes, *, nome_arquivo: str | None = None, origem: str = "arquivo"
    ) -> Ocorrencia:
        return self._um(nome_arquivo, conteudo, origem)

    def _um(self, nome: str | None, conteudo: bytes, origem: str) -> Ocorrencia:
        try:
            adaptador = adaptador_para(conteudo)
            documento = adaptador.normalizar(conteudo, nome_arquivo=nome)
        except (OrigemNaoReconhecida, ValueError) as erro:
            logger.warning("Documento recusado (%s): %s", nome, erro)
            return Ocorrencia(Desfecho.REJEITADO, origem=nome, motivo=str(erro))

        existente = self.session.execute(
            select(DocumentoFiscal).where(
                DocumentoFiscal.escritorio_id == self.escritorio_id,
                DocumentoFiscal.chave == documento.chave,
            )
        ).scalar_one_or_none()

        if existente is not None:
            return self._duplicado(existente, documento, nome, origem)

        gravado = self._gravar(documento, nome, origem)
        return Ocorrencia(
            Desfecho.IMPORTADO, origem=nome, chave=documento.chave, documento_id=gravado.id
        )

    def _duplicado(
        self,
        existente: DocumentoFiscal,
        novo: DocumentoNormalizado,
        nome: str | None,
        origem: str,
    ) -> Ocorrencia:
        identico = existente.hash_original == novo.hash_original
        detalhe = "mesmo arquivo" if identico else "MESMA CHAVE, conteúdo diferente"

        if self.politica is PoliticaDeDuplicidade.ERRO:
            raise ValueError(f"documento {novo.chave} já importado ({detalhe})")

        if self.politica is PoliticaDeDuplicidade.SUBSTITUIR:
            # Apagar leva junto os ajustes do documento antigo — por isso
            # substituir nunca é o padrão.
            self.session.delete(existente)
            self.session.flush()
            gravado = self._gravar(novo, nome, origem)
            return Ocorrencia(
                Desfecho.SUBSTITUIDO,
                origem=nome,
                chave=novo.chave,
                documento_id=gravado.id,
                motivo=f"substituído ({detalhe})",
            )

        return Ocorrencia(
            Desfecho.DUPLICADO,
            origem=nome,
            chave=novo.chave,
            documento_id=existente.id,
            motivo=detalhe,
        )

    def _gravar(
        self, normalizado: DocumentoNormalizado, nome: str | None, origem: str
    ) -> DocumentoFiscal:
        empresa = self._empresa_de(normalizado)
        documento = DocumentoFiscal(
            escritorio_id=self.escritorio_id,
            empresa_id=empresa.id if empresa else None,
            chave=normalizado.chave,
            modelo=normalizado.modelo,
            especie=normalizado.especie,
            numero=normalizado.numero,
            serie=normalizado.serie,
            sentido=str(self._sentido(normalizado, empresa)),
            situacao=normalizado.situacao,
            finalidade=normalizado.finalidade,
            natureza_operacao=normalizado.natureza_operacao,
            emitente_cnpj=normalizado.emitente_cnpj,
            emitente_nome=normalizado.emitente_nome,
            emitente_ie=normalizado.emitente_ie,
            emitente_uf=normalizado.emitente_uf,
            destinatario_cnpj=normalizado.destinatario_cnpj,
            destinatario_nome=normalizado.destinatario_nome,
            destinatario_ie=normalizado.destinatario_ie,
            destinatario_uf=normalizado.destinatario_uf,
            municipio_codigo=normalizado.municipio_codigo,
            data_emissao=normalizado.data_emissao,
            data_entrada_saida=normalizado.data_entrada_saida,
            valor_total=normalizado.valor_total,
            valor_produtos=normalizado.valor_produtos,
            valor_desconto=normalizado.valor_desconto,
            valor_frete=normalizado.valor_frete,
            valor_seguro=normalizado.valor_seguro,
            valor_outras=normalizado.valor_outras,
            base_icms=normalizado.base_icms,
            valor_icms=normalizado.valor_icms,
            valor_icms_st=normalizado.valor_icms_st,
            valor_ipi=normalizado.valor_ipi,
            valor_pis=normalizado.valor_pis,
            valor_cofins=normalizado.valor_cofins,
            valor_ibs=normalizado.valor_ibs,
            valor_cbs=normalizado.valor_cbs,
            valor_is=normalizado.valor_is,
            xml_original=normalizado.xml_original,
            hash_original=normalizado.hash_original,
            origem=origem,
            nome_arquivo=nome,
            adaptador=normalizado.adaptador,
        )
        for item in normalizado.itens:
            documento.itens.append(
                ItemDocumentoFiscal(
                    **{
                        campo: getattr(item, campo)
                        for campo in _CAMPOS_DE_ITEM
                        if hasattr(item, campo)
                    }
                )
            )
        self.session.add(documento)
        self.session.flush()
        return documento

    def _empresa_de(self, normalizado: DocumentoNormalizado) -> Empresa | None:
        """A empresa do escritório que aparece no documento.

        Pode ser o emitente (saída) ou o destinatário (entrada).  Documento em
        que nenhuma das pontas é cadastrada fica sem empresa — não é erro: é
        material para o cadastro, e a tela de preparação vai cobrar.
        """
        candidatos = [c for c in (normalizado.emitente_cnpj, normalizado.destinatario_cnpj) if c]
        if not candidatos:
            return None
        encontradas = (
            self.session.execute(
                select(Empresa)
                .where(
                    Empresa.cnpj.in_(candidatos),
                    Empresa.escritorio_id == self.escritorio_id,
                )
                .order_by(Empresa.id)
            )
            .scalars()
            .all()
        )
        if not encontradas:
            return None
        if len(encontradas) > 1:
            # As duas pontas são do escritório — transferência entre filiais,
            # por exemplo.  O documento deveria ser escriturado pelas duas (uma
            # como saída, outra como entrada), e o modelo só admite uma
            # `empresa_id`.  Fica com o EMITENTE, por ser quem tem a obrigação
            # de emitir, e o aviso registra o que está sendo perdido.
            por_cnpj = {e.cnpj: e for e in encontradas}
            escolhida = por_cnpj.get(normalizado.emitente_cnpj) or encontradas[0]
            logger.warning(
                "Documento %s tem as duas pontas cadastradas (%s); escriturado "
                "para %s. A contraparte precisa de escrituração própria.",
                normalizado.chave,
                ", ".join(sorted(por_cnpj)),
                escolhida.cnpj,
            )
            return escolhida
        return encontradas[0]

    @staticmethod
    def _sentido(normalizado: DocumentoNormalizado, empresa: Empresa | None) -> Sentido:
        """Entrada ou saída **para a empresa que escritura**.

        Não dá para tirar do `tpNF`: ele é a visão do emitente, e a mesma nota
        é saída para quem emitiu e entrada para quem recebeu.  Sem empresa
        identificada, sobra o `tpNF` — melhor que arbitrar.
        """
        if empresa is not None:
            if normalizado.emitente_cnpj == empresa.cnpj:
                return Sentido.SAIDA
            if normalizado.destinatario_cnpj == empresa.cnpj:
                return Sentido.ENTRADA
        return Sentido.ENTRADA if normalizado.tipo_operacao_emitente == "0" else Sentido.SAIDA


# Os campos que viajam de `ItemNormalizado` para `ItemDocumentoFiscal`.  Os
# dois têm os mesmos nomes de propósito: um nome que diverge é um campo que
# alguém esquece de copiar, e o teste `test_todo_campo_normalizado_chega_ao_banco`
# guarda isso.
_CAMPOS_DE_ITEM = (
    "numero_item",
    "codigo",
    "descricao",
    "ncm",
    "cest",
    "codigo_servico",
    "unidade",
    "quantidade",
    "valor_unitario",
    "valor_total",
    "valor_desconto",
    "valor_frete",
    "valor_seguro",
    "valor_outras",
    "cfop",
    "origem_mercadoria",
    "cst_icms",
    "csosn",
    "base_icms",
    "aliquota_icms",
    "valor_icms",
    "base_icms_st",
    "valor_icms_st",
    "valor_fcp",
    "cst_ipi",
    "valor_ipi",
    "cst_pis",
    "base_pis",
    "aliquota_pis",
    "valor_pis",
    "cst_cofins",
    "base_cofins",
    "aliquota_cofins",
    "valor_cofins",
    "valor_iss",
    "codigo_beneficio",
    "cst_ibscbs",
    "class_trib_ibscbs",
    "base_ibscbs",
    "aliquota_ibs_uf",
    "valor_ibs_uf",
    "aliquota_ibs_mun",
    "valor_ibs_mun",
    "municipio_fg_ibs",
    "aliquota_cbs",
    "valor_cbs",
    "percentual_reducao_aliquota",
    "aliquota_efetiva",
    "valor_diferido",
    "valor_devolucao_tributo",
    "codigo_credito_presumido",
    "valor_credito_presumido",
    "valor_credito_presumido_susp",
    "quantidade_bc_mono",
    "valor_ibs_mono",
    "valor_cbs_mono",
    "valor_ibs_mono_retido",
    "valor_cbs_mono_retido",
    "cst_is",
    "class_trib_is",
    "base_is",
    "aliquota_is",
    "aliquota_is_especifica",
    "unidade_tributavel_is",
    "quantidade_tributavel_is",
    "valor_is",
)
