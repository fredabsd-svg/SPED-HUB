"""Configuração de logging da aplicação (Fase 17, Etapa 5).

Duas coisas:

1. **Formato JSON opcional** (`SPED_HUB_LOG_JSON=true`).  Em produção os logs
   vão para um coletor; texto livre obriga a escrever parser por regex, que
   quebra assim que alguém muda uma mensagem.

2. **Saneamento de PII.**  Logs de erro carregam com frequência o que o
   usuário digitou — e-mail no login, CNPJ da escrituração, token de sessão em
   uma URL.  Isso vaza para o coletor, para o backup do coletor e para quem
   tiver acesso a qualquer um dos dois.  O filtro abaixo mascara os padrões
   que aparecem neste domínio antes de a linha ser emitida.
"""

from __future__ import annotations

import json
import logging
import re

from src.settings import get_settings

# CNPJ (14 dígitos, com ou sem pontuação) e CPF (11).  A raiz é mascarada e a
# cauda preservada: dá para casar a linha de log com o registro certo durante
# uma investigação, sem que o documento completo fique gravado.
_CNPJ = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
# Tokens de sessão (128 hex) e API keys (prefixo spd_).
_TOKEN = re.compile(r"\b[0-9a-f]{32,}\b")
_API_KEY = re.compile(r"\bspd_[0-9a-f]{8,}\b")


def _mascarar_cnpj(match: re.Match) -> str:
    """``12.345.678/0001-95`` → ``**.***.***/0001-95`` (mantém filial e DV)."""
    digitos = re.sub(r"\D", "", match.group(0))
    return f"**.***.***/{digitos[8:12]}-{digitos[12:]}"


def _mascarar_cpf(match: re.Match) -> str:
    """``123.456.789-01`` → ``***.***.789-01``."""
    digitos = re.sub(r"\D", "", match.group(0))
    return f"***.***.{digitos[6:9]}-{digitos[9:]}"


def sanitizar(texto: str) -> str:
    """Mascara identificadores pessoais e segredos em uma linha de log."""
    texto = _API_KEY.sub("spd_***", texto)
    texto = _TOKEN.sub("***", texto)
    texto = _CNPJ.sub(_mascarar_cnpj, texto)
    texto = _CPF.sub(_mascarar_cpf, texto)
    texto = _EMAIL.sub(lambda m: f"{m.group(1)}***{m.group(2)}", texto)
    return texto


class FiltroPII(logging.Filter):
    """Aplica :func:`sanitizar` à mensagem já formatada."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = sanitizar(record.getMessage())
            record.args = ()
        except Exception:  # nunca derrube a aplicação por causa do log
            pass
        return True


class FormatadorJSON(logging.Formatter):
    """Uma linha JSON por evento, para consumo por coletor."""

    def format(self, record: logging.LogRecord) -> str:
        evento = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "nivel": record.levelname,
            "logger": record.name,
            "mensagem": sanitizar(record.getMessage()),
        }
        if record.exc_info:
            evento["excecao"] = sanitizar(self.formatException(record.exc_info))
        for extra in ("request_id", "usuario_id", "rota", "status_code", "duracao_ms"):
            if (valor := getattr(record, extra, None)) is not None:
                evento[extra] = valor
        return json.dumps(evento, ensure_ascii=False)


def configurar_logging(forcar_json: bool | None = None) -> None:
    """Instala formato e filtro no logger raiz.  Idempotente."""
    cfg = get_settings()
    usar_json = cfg.log_json if forcar_json is None else forcar_json

    raiz = logging.getLogger()
    raiz.setLevel(getattr(logging, cfg.log_level.upper(), logging.INFO))

    # Remove apenas o handler instalado por esta função — chamar de novo troca
    # o formato sem efeito colateral.  Zerar `raiz.handlers` levaria junto
    # qualquer handler de terceiros: o de captura do pytest, o de um servidor
    # de aplicação que já tenha configurado logging, o de um coletor externo.
    for handler in [h for h in raiz.handlers if getattr(h, "_sped_hub", False)]:
        raiz.removeHandler(handler)

    handler = logging.StreamHandler()
    handler._sped_hub = True
    handler.setFormatter(
        FormatadorJSON()
        if usar_json
        else logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    # O filtro entra no handler, não no logger: assim vale para tudo que passa
    # por ele, inclusive os loggers de bibliotecas de terceiros.
    handler.addFilter(FiltroPII())
    raiz.addHandler(handler)
