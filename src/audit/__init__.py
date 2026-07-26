"""Logs de Auditoria — Fase 13.

Sistema de auditoria que rastreia acessos e ações sensíveis no SPED-HUB.

Funcionamento:
  - AuditService.registrar() — registra um evento de auditoria
  - AuditMiddleware — captura automaticamente todas as requisições da API
  - Dashboard de auditoria em /auditoria com filtros

Eventos capturados:
  - api.acesso — toda requisição autenticada na API REST
  - auth.login — login bem-sucedido ou falho
  - auth.logout — logout
  - auth.register — novo registro
  - ecd.upload — upload de ECD
  - ecd.upload_efd — upload de EFD
  - ecd.upload_ecf — upload de ECF
  - relatorio.export — exportação de relatório (PDF/XLSX)
  - apikey.create — criação de API Key
  - apikey.revoke — revogação de API Key
  - webhook.create — registro de webhook
  - webhook.delete — remoção de webhook
"""

import datetime
import json
import logging
from typing import Any, Optional

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from src.db.models import AuditLog, criar_engine, get_session, init_db

logger = logging.getLogger("sped-hub.audit")


class AuditService:
    """Serviço de logs de auditoria."""

    def __init__(self, db_path: str = "sped_hub.db"):
        self.db_path = db_path

    def _get_session(self) -> Session:
        engine = criar_engine(self.db_path)
        init_db(engine)
        return get_session(engine)

    def registrar(
        self,
        acao: str,
        recurso: str,
        usuario_id: int | None = None,
        usuario_email: str | None = None,
        api_key_id: int | None = None,
        metodo: str | None = None,
        ip: str | None = None,
        status_code: int | None = None,
        detalhes: dict | None = None,
    ) -> AuditLog:
        """Registra um evento de auditoria.

        Args:
            acao: Tipo da ação (ex: "auth.login", "ecd.upload")
            recurso: Recurso acessado (ex: "/api/v1/empresas", "ECD #5")
            usuario_id: ID do usuário (se autenticado por sessão)
            usuario_email: Email do usuário
            api_key_id: ID da API Key (se autenticado por API Key)
            metodo: Método HTTP (GET, POST, etc.)
            ip: Endereço IP do cliente
            status_code: Código de status da resposta
            detalhes: Dicionário com detalhes adicionais

        Returns:
            O registro de auditoria criado
        """
        session = self._get_session()
        try:
            log = AuditLog(
                usuario_id=usuario_id,
                usuario_email=usuario_email,
                api_key_id=api_key_id,
                acao=acao,
                recurso=recurso,
                metodo=metodo,
                ip=ip,
                status_code=status_code,
            )
            if detalhes:
                log.set_detalhes(detalhes)

            session.add(log)
            session.commit()
            session.refresh(log)
            return log
        except Exception:
            session.rollback()
            logger.exception("Erro ao registrar log de auditoria")
            raise
        finally:
            session.close()

    def listar(
        self,
        usuario_id: int | None = None,
        api_key_id: int | None = None,
        acao: str | None = None,
        dt_ini: datetime.datetime | None = None,
        dt_fin: datetime.datetime | None = None,
        limite: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Lista logs de auditoria com filtros opcionais.

        Args:
            usuario_id: Filtrar por usuário
            api_key_id: Filtrar por API Key
            acao: Filtrar por tipo de ação
            dt_ini: Data/hora inicial
            dt_fin: Data/hora final
            limite: Máximo de resultados
            offset: Deslocamento para paginação

        Returns:
            Lista de logs como dicionários
        """
        session = self._get_session()
        try:
            query = select(AuditLog).order_by(desc(AuditLog.criado_em))

            if usuario_id is not None:
                query = query.where(AuditLog.usuario_id == usuario_id)
            if api_key_id is not None:
                query = query.where(AuditLog.api_key_id == api_key_id)
            if acao:
                query = query.where(AuditLog.acao == acao)
            if dt_ini:
                query = query.where(AuditLog.criado_em >= dt_ini)
            if dt_fin:
                query = query.where(AuditLog.criado_em <= dt_fin)

            logs = session.execute(query.offset(offset).limit(limite)).scalars().all()

            return [
                {
                    "id": log.id,
                    "usuario_id": log.usuario_id,
                    "usuario_email": log.usuario_email,
                    "api_key_id": log.api_key_id,
                    "acao": log.acao,
                    "recurso": log.recurso,
                    "metodo": log.metodo,
                    "ip": log.ip,
                    "status_code": log.status_code,
                    "detalhes": log.get_detalhes(),
                    "criado_em": log.criado_em.isoformat() if log.criado_em else None,
                }
                for log in logs
            ]
        finally:
            session.close()

    def contar(
        self,
        usuario_id: int | None = None,
        acao: str | None = None,
        dt_ini: datetime.datetime | None = None,
        dt_fin: datetime.datetime | None = None,
    ) -> int:
        """Conta o total de logs com os filtros especificados."""
        session = self._get_session()
        try:
            query = select(func.count(AuditLog.id))
            if usuario_id is not None:
                query = query.where(AuditLog.usuario_id == usuario_id)
            if acao:
                query = query.where(AuditLog.acao == acao)
            if dt_ini:
                query = query.where(AuditLog.criado_em >= dt_ini)
            if dt_fin:
                query = query.where(AuditLog.criado_em <= dt_fin)
            return session.execute(query).scalar() or 0
        finally:
            session.close()

    def estatisticas(self, horas: int = 24) -> dict:
        """Estatísticas agregadas de auditoria.

        Args:
            horas: Janela de tempo em horas (default: últimas 24h)

        Returns:
            dict com total, por_acao, por_status, usuarios_unicos
        """
        session = self._get_session()
        try:
            agora = datetime.datetime.now(datetime.UTC)
            inicio = agora - datetime.timedelta(hours=horas)

            total = session.execute(
                select(func.count(AuditLog.id)).where(AuditLog.criado_em >= inicio)
            ).scalar() or 0

            # Por ação
            por_acao_raw = session.execute(
                select(AuditLog.acao, func.count(AuditLog.id))
                .where(AuditLog.criado_em >= inicio)
                .group_by(AuditLog.acao)
                .order_by(desc(func.count(AuditLog.id)))
            ).all()
            por_acao = {acao: count for acao, count in por_acao_raw}

            # Por status code
            por_status_raw = session.execute(
                select(AuditLog.status_code, func.count(AuditLog.id))
                .where(AuditLog.criado_em >= inicio)
                .group_by(AuditLog.status_code)
            ).all()
            por_status = {str(status or "N/A"): count for status, count in por_status_raw}

            # Usuários únicos
            usuarios_unicos = session.execute(
                select(func.count(func.distinct(AuditLog.usuario_id)))
                .where(AuditLog.criado_em >= inicio)
                .where(AuditLog.usuario_id.isnot(None))
            ).scalar() or 0

            # IPs únicos
            ips_unicos = session.execute(
                select(func.count(func.distinct(AuditLog.ip)))
                .where(AuditLog.criado_em >= inicio)
                .where(AuditLog.ip.isnot(None))
            ).scalar() or 0

            # Erros (4xx, 5xx)
            erros = session.execute(
                select(func.count(AuditLog.id))
                .where(AuditLog.criado_em >= inicio)
                .where(AuditLog.status_code >= 400)
            ).scalar() or 0

            return {
                "janela_horas": horas,
                "total_eventos": total,
                "por_acao": por_acao,
                "por_status": por_status,
                "usuarios_unicos": usuarios_unicos,
                "ips_unicos": ips_unicos,
                "erros": erros,
            }
        finally:
            session.close()

    def limpar_antigos(self, dias: int = 90) -> int:
        """Remove logs de auditoria mais antigos que o número de dias.

        Args:
            dias: Idade máxima dos logs em dias

        Returns:
            Número de logs removidos
        """
        session = self._get_session()
        try:
            limite = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=dias)
            logs_antigos = session.execute(
                select(AuditLog).where(AuditLog.criado_em < limite)
            ).scalars().all()

            count = len(logs_antigos)
            for log in logs_antigos:
                session.delete(log)

            session.commit()
            logger.info("Removidos %d logs de auditoria antigos (> %d dias)", count, dias)
            return count
        except Exception:
            session.rollback()
            logger.exception("Erro ao limpar logs antigos")
            raise
        finally:
            session.close()


# ── Instância global ───────────────────────────────────────────────────────

_audit_service: AuditService | None = None


def get_audit_service(db_path: str = "sped_hub.db") -> AuditService:
    global _audit_service
    if _audit_service is None:
        _audit_service = AuditService(db_path)
    return _audit_service


def init_audit_service(db_path: str = "sped_hub.db") -> AuditService:
    global _audit_service
    _audit_service = AuditService(db_path)
    return _audit_service
