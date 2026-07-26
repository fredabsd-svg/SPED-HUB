"""Utilitários seguros para uploads do dashboard."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile

_CHUNK_SIZE = 1024 * 1024
_DEFAULT_MAX_UPLOAD_BYTES = 512 * 1024 * 1024


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
    path = Path(os.environ.get("SPED_HUB_UPLOAD_DIR", "/workspace/uploads"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def max_upload_bytes() -> int:
    """Limite configurável de upload; valores inválidos usam o padrão seguro."""
    raw = os.environ.get("SPED_HUB_MAX_UPLOAD_BYTES")
    if raw is None:
        return _DEFAULT_MAX_UPLOAD_BYTES
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_UPLOAD_BYTES
    return value if value > 0 else _DEFAULT_MAX_UPLOAD_BYTES


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

    try:
        with os.fdopen(fd, "wb") as destination:
            while chunk := await upload.read(_CHUNK_SIZE):
                total += len(chunk)
                if total > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Arquivo excede o limite de {limit} bytes",
                    )
                digest.update(chunk)
                destination.write(chunk)
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
