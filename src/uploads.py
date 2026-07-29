"""Utilitários seguros para uploads do dashboard."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile

from src.settings import get_settings

_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class SavedUpload:
    """Metadados de um upload salvo em arquivo temporário."""

    path: Path
    original_name: str
    size_bytes: int
    sha256: str


def safe_original_name(filename: str | None) -> str:
    """Remove componentes de caminho sem confiar no nome enviado pelo cliente."""
    if not filename:
        raise HTTPException(status_code=400, detail="Nome de arquivo ausente")

    normalized = filename.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1].strip()
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Nome de arquivo inválido")
    return name


def upload_directory() -> Path:
    """Retorna o diretório configurado para arquivos temporários de upload."""
    path = Path(get_settings().upload_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def max_upload_bytes() -> int:
    """Limite configurável de upload; valores inválidos usam o padrão seguro.

    Deriva de ``SPED_HUB_MAX_UPLOAD_MB`` (documentado) com
    ``SPED_HUB_MAX_UPLOAD_BYTES`` como override legado.  Antes só o segundo
    era lido, então o limite documentado não tinha efeito nenhum.
    """
    return get_settings().max_upload_bytes


# Um arquivo SPED começa com o registro 0000 delimitado por pipes.  Conferir a
# extensão não diz nada sobre o conteúdo: qualquer coisa renomeada para .txt
# passava, era gravada em disco e só quebrava lá adiante no parser — depois de
# já ter consumido o limite inteiro de upload.
_ASSINATURA_SPED = b"|0000|"
_BYTES_DE_ASSINATURA = 512


def _validar_assinatura(inicio: bytes, formatos: tuple[str, ...]) -> None:
    """Confere que o começo do arquivo parece um SPED.

    Aceita BOM UTF-8 e linhas em branco à frente, que aparecem em arquivos
    gerados por alguns sistemas contábeis.
    """
    amostra = inicio.lstrip(b"\xef\xbb\xbf").lstrip(b"\r\n \t")
    if amostra.startswith(_ASSINATURA_SPED):
        return
    formatos_txt = ", ".join(formatos)
    raise HTTPException(
        status_code=400,
        detail=(
            f"Conteúdo não parece um arquivo SPED ({formatos_txt}). "
            "O arquivo deve começar com o registro |0000|."
        ),
    )


async def save_upload(
    upload: UploadFile,
    allowed_extensions: tuple[str, ...],
) -> SavedUpload:
    """Salva um upload em streaming, com nome único e limite de tamanho.

    O nome fornecido pelo cliente nunca é usado como caminho no sistema de
    arquivos. Em caso de erro, o arquivo parcial é removido.
    """
    original_name = safe_original_name(upload.filename)
    suffix = Path(original_name).suffix.lower()
    allowed = tuple(ext.lower() for ext in allowed_extensions)
    if suffix not in allowed:
        formatos = ", ".join(allowed)
        raise HTTPException(
            status_code=400,
            detail=f"Formato inválido. Envie um arquivo: {formatos}",
        )

    fd, raw_path = tempfile.mkstemp(
        prefix="sped_upload_",
        suffix=suffix,
        dir=upload_directory(),
    )
    path = Path(raw_path)
    digest = hashlib.sha256()
    total = 0
    limit = max_upload_bytes()

    primeiro_bloco = True
    try:
        with os.fdopen(fd, "wb") as destination:
            while chunk := await upload.read(_CHUNK_SIZE):
                if primeiro_bloco:
                    # Valida antes de gravar: um arquivo que não é SPED é
                    # recusado no primeiro bloco, sem ocupar disco nem
                    # consumir o limite inteiro de upload.
                    _validar_assinatura(chunk[:_BYTES_DE_ASSINATURA], allowed)
                    primeiro_bloco = False
                total += len(chunk)
                if total > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Arquivo excede o limite de {limit} bytes",
                    )
                digest.update(chunk)
                destination.write(chunk)
        if primeiro_bloco:
            raise HTTPException(status_code=400, detail="Arquivo vazio")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    return SavedUpload(
        path=path,
        original_name=original_name,
        size_bytes=total,
        sha256=digest.hexdigest(),
    )
