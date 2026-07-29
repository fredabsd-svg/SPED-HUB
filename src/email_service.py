"""Email Notification Service — Fase 15.

Serviço de notificações por email para alertas e relatórios.
Suporta SMTP real e modo "log" para desenvolvimento/testes.

Uso:
    from src.email_service import EmailService, get_email_service

    svc = get_email_service()
    svc.enviar(
        para="cliente@exemplo.com",
        assunto="ECD Processada",
        corpo="Sua ECD foi processada com sucesso!",
    )

Configuração via variáveis de ambiente:
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM
"""

import datetime
import logging
import os
import smtplib
import threading
from dataclasses import dataclass, field
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger("sped-hub.email")


@dataclass
class EmailMessage:
    """Mensagem de email."""

    para: str
    assunto: str
    corpo: str
    corpo_html: str | None = None
    cc: list[str] = field(default_factory=list)
    anexos: list[dict] = field(default_factory=list)  # [{"nome": "x.pdf", "dados": bytes}]
    enviado_em: str | None = None
    status: str = "pendente"  # pendente, enviado, falha
    erro: str | None = None


class EmailService:
    """Serviço de envio de emails.

    Modos:
    - "smtp": envia via SMTP real
    - "log": apenas loga (default para desenvolvimento)

    Configuração:
        SMTP_HOST: servidor SMTP (default: localhost)
        SMTP_PORT: porta (default: 587)
        SMTP_USER: usuário
        SMTP_PASS: senha
        SMTP_FROM: remetente (default: sped-hub@localhost)
        SMTP_USE_TLS: usar TLS (default: true)
    """

    def __init__(self, modo: str = "auto"):
        self._modo = modo
        self._host = os.environ.get("SMTP_HOST", "localhost")
        self._port = int(os.environ.get("SMTP_PORT", "587"))
        self._user = os.environ.get("SMTP_USER", "")
        self._password = os.environ.get("SMTP_PASS", "")
        self._from = os.environ.get("SMTP_FROM", "sped-hub@localhost")
        self._use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"

        # Histórico
        self._sent: list[EmailMessage] = []
        self._max_history = 100
        self._history_lock = threading.Lock()

        # Detecta modo
        if self._modo == "auto":
            self._modo = "smtp" if self._user and self._password else "log"

        logger.info(
            "EmailService iniciado em modo '%s' (host=%s, from=%s)",
            self._modo,
            self._host,
            self._from,
        )

    def enviar(
        self,
        para: str,
        assunto: str,
        corpo: str,
        corpo_html: str | None = None,
        cc: list[str] | None = None,
        anexos: list[dict] | None = None,
        async_mode: bool = True,
    ) -> EmailMessage:
        """Envia um email.

        Args:
            para: Destinatário
            assunto: Assunto do email
            corpo: Corpo em texto plano
            corpo_html: Corpo em HTML (opcional)
            cc: Lista de CC
            anexos: Lista de anexos [{"nome": str, "dados": bytes}]
            async_mode: Se True, envia em thread separada

        Returns:
            EmailMessage com status do envio
        """
        msg = EmailMessage(
            para=para,
            assunto=assunto,
            corpo=corpo,
            corpo_html=corpo_html,
            cc=cc or [],
            anexos=anexos or [],
        )

        if async_mode:
            t = threading.Thread(target=self._do_enviar, args=(msg,), daemon=True)
            t.start()
        else:
            self._do_enviar(msg)

        return msg

    def _do_enviar(self, msg: EmailMessage):
        """Executa o envio e registra tanto sucessos quanto falhas."""
        try:
            if self._modo == "log":
                self._log_send(msg)
            else:
                self._smtp_send(msg)
            msg.status = "enviado"
            msg.enviado_em = datetime.datetime.now(datetime.UTC).isoformat()
        except Exception as exc:
            msg.status = "falha"
            msg.erro = str(exc)
            logger.error("Falha ao enviar email para %s: %s", msg.para, exc)
        finally:
            with self._history_lock:
                self._sent.append(msg)
                if len(self._sent) > self._max_history:
                    del self._sent[: -self._max_history]

    def _log_send(self, msg: EmailMessage):
        """Modo log: apenas registra o email."""
        logger.info("── Email (modo log) ──")
        logger.info("  Para: %s", msg.para)
        if msg.cc:
            logger.info("  CC: %s", ", ".join(msg.cc))
        logger.info("  Assunto: %s", msg.assunto)
        logger.info("  Corpo: %s", msg.corpo[:200])
        if msg.anexos:
            logger.info("  Anexos: %d", len(msg.anexos))
        logger.info("── Fim Email ──")

    def _smtp_send(self, msg: EmailMessage):
        """Envia via SMTP real."""
        mime = MIMEMultipart("mixed")
        mime["From"] = self._from
        mime["To"] = msg.para
        mime["Subject"] = msg.assunto
        if msg.cc:
            mime["Cc"] = ", ".join(msg.cc)

        body = MIMEMultipart("alternative")
        body.attach(MIMEText(msg.corpo, "plain", "utf-8"))
        if msg.corpo_html:
            body.attach(MIMEText(msg.corpo_html, "html", "utf-8"))
        mime.attach(body)

        for attachment in msg.anexos:
            name = Path(str(attachment.get("nome", "anexo.bin"))).name
            data = attachment.get("dados", b"")
            if not isinstance(data, bytes):
                raise TypeError(f"Dados do anexo '{name}' devem ser bytes")
            part = MIMEApplication(data)
            part.add_header("Content-Disposition", "attachment", filename=name)
            mime.attach(part)

        with smtplib.SMTP(self._host, self._port, timeout=10) as server:
            if self._use_tls:
                server.starttls()
            if self._user and self._password:
                server.login(self._user, self._password)
            server.send_message(mime)

        logger.info("Email enviado para %s: %s", msg.para, msg.assunto)

    def enviar_alerta_job_concluido(
        self, para: str, job_tipo: str, job_id: int, resultado: dict | None = None
    ):
        """Envia alerta de job concluído."""
        corpo = f"""
Job concluído com sucesso!

Tipo: {job_tipo}
ID: {job_id}
Data: {datetime.datetime.now(datetime.UTC).strftime('%d/%m/%Y %H:%M')}

Resultado: {resultado or 'N/A'}

--
SPED-HUB — Plataforma de Conformidade Fiscal
"""
        return self.enviar(
            para=para,
            assunto=f"[SPED-HUB] Job #{job_id} concluído — {job_tipo}",
            corpo=corpo,
        )

    def enviar_alerta_job_falhou(self, para: str, job_tipo: str, job_id: int, erro: str):
        """Envia alerta de job com falha."""
        corpo = f"""
Job falhou!

Tipo: {job_tipo}
ID: {job_id}
Data: {datetime.datetime.now(datetime.UTC).strftime('%d/%m/%Y %H:%M')}

Erro: {erro}

--
SPED-HUB — Plataforma de Conformidade Fiscal
"""
        return self.enviar(
            para=para,
            assunto=f"[SPED-HUB] ⚠️ Job #{job_id} falhou — {job_tipo}",
            corpo=corpo,
        )

    def enviar_relatorio_agendado(
        self,
        para: str,
        empresa: str,
        periodo: str,
        anexo_pdf: bytes | None = None,
    ):
        """Envia relatório agendado com anexo PDF opcional."""
        corpo = f"""
Relatório agendado disponível!

Empresa: {empresa}
Período: {periodo}
Data: {datetime.datetime.now(datetime.UTC).strftime('%d/%m/%Y %H:%M')}

O relatório segue em anexo.

--
SPED-HUB — Plataforma de Conformidade Fiscal
"""
        anexos = []
        if anexo_pdf:
            anexos.append({"nome": f"relatorio_{empresa}_{periodo}.pdf", "dados": anexo_pdf})

        return self.enviar(
            para=para,
            assunto=f"[SPED-HUB] Relatório — {empresa} — {periodo}",
            corpo=corpo,
            anexos=anexos,
        )

    def historico(self, limite: int = 20) -> list[dict]:
        """Retorna histórico de envios."""
        with self._history_lock:
            messages = list(self._sent[-limite:])
        return [
            {
                "para": message.para,
                "assunto": message.assunto,
                "status": message.status,
                "enviado_em": message.enviado_em,
                "erro": message.erro,
            }
            for message in messages
        ]

    def stats(self) -> dict:
        """Estatísticas de envio."""
        with self._history_lock:
            messages = list(self._sent)
        enviados = sum(1 for message in messages if message.status == "enviado")
        falhas = sum(1 for message in messages if message.status == "falha")
        return {
            "total": len(messages),
            "enviados": enviados,
            "falhas": falhas,
            "modo": self._modo,
        }


# Instância global
_email_service: EmailService | None = None


def init_email_service(modo: str = "auto") -> EmailService:
    global _email_service
    _email_service = EmailService(modo=modo)
    return _email_service


def get_email_service() -> EmailService:
    global _email_service
    if _email_service is None:
        return init_email_service()
    return _email_service
