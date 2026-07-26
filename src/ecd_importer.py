"""Importação incremental de ECD para o banco de dados.

O serviço percorre o arquivo uma única vez para persistir os registros e mantém
em memória apenas metadados e mapas pequenos. O hash pode ser fornecido pelo
fluxo de upload ou calculado em chunks, sem carregar o arquivo inteiro.
"""

from __future__ import annotations

import datetime
import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    Aglutinacao,
    CentroCusto,
    ContaReferencial,
    ECD,
    Empresa,
    HistoricoPadrao,
    Lancamento,
    Partida,
    PlanoConta,
    SaldoPeriodico,
    SaldoResultado,
)
from src.parsers.ecd import ECDParser

ProgressCallback = Callable[[float, str], None]


class ECDImportError(ValueError):
    """Erro de validação ou consistência durante uma importação ECD."""


class DuplicateECDImportError(ECDImportError):
    """A escrituração já existe para a empresa e período informados."""

    def __init__(self, ecd_id: int):
        self.ecd_id = ecd_id
        super().__init__(f"ECD já importada (id={ecd_id})")


@dataclass(frozen=True)
class ECDImportResult:
    ecd_id: int
    empresa_id: int
    empresa: str
    periodo: str
    contas: int
    lancamentos: int
    partidas: int
    saldos_periodicos: int
    saldos_resultado: int
    total_registros: int
    hash_arquivo: str
    nome_arquivo: str

    def to_dict(self) -> dict:
        return asdict(self)


def hash_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Calcula SHA-256 em memória constante."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _date(value, fallback: datetime.date | None = None) -> datetime.date:
    if value is None or value == "":
        if fallback is not None:
            return fallback
        raise ECDImportError("Data obrigatória ausente")
    digits = str(int(value)).zfill(8) if isinstance(value, (int, float)) else str(value).strip()
    if len(digits) != 8 or not digits.isdigit():
        if fallback is not None:
            return fallback
        raise ECDImportError(f"Data ECD inválida: {value}")
    try:
        return datetime.date(int(digits[4:8]), int(digits[2:4]), int(digits[0:2]))
    except ValueError as exc:
        raise ECDImportError(f"Data ECD inválida: {value}") from exc


def _digits(value, width: int) -> str:
    if value is None or value == "":
        return "0" * width
    if isinstance(value, (int, float)):
        return str(int(value)).zfill(width)
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits.zfill(width)[-width:]


def _optional_int(value) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


class ECDImportService:
    """Persiste uma ECD em fluxo contínuo e transação atômica."""

    def __init__(self, session: Session, parser: ECDParser | None = None):
        self.session = session
        self.parser = parser or ECDParser()

    def importar(
        self,
        path: Path,
        *,
        hash_arquivo: str | None = None,
        nome_arquivo: str | None = None,
        escritorio_id: int | None = None,
        progress: ProgressCallback | None = None,
        flush_interval: int = 1000,
    ) -> ECDImportResult:
        path = Path(path)
        if not path.is_file():
            raise ECDImportError(f"Arquivo não encontrado: {path}")
        if flush_interval < 1:
            raise ValueError("flush_interval deve ser >= 1")

        file_hash = hash_arquivo or hash_file(path)
        original_name = Path(nome_arquivo or path.name).name
        file_size = max(path.stat().st_size, 1)
        callback = progress or (lambda _pct, _message: None)
        callback(2.0, "Validando arquivo ECD")

        header_0000: dict | None = None
        header_i010: dict | None = None
        empresa: Empresa | None = None
        ecd: ECD | None = None
        dt_ini: datetime.date | None = None
        dt_fin: datetime.date | None = None
        conta_ids: dict[str, int] = {}
        current_lancamento: tuple[tuple[str, datetime.date], int] | None = None
        counts: dict[str, int] = {}
        since_flush = 0
        last_progress = 2.0

        def ensure_context() -> tuple[Empresa, ECD, datetime.date, datetime.date]:
            nonlocal empresa, ecd, dt_ini, dt_fin
            if ecd is not None and empresa is not None and dt_ini is not None and dt_fin is not None:
                return empresa, ecd, dt_ini, dt_fin
            if header_0000 is None:
                raise ECDImportError("Arquivo não contém registro 0000")

            cnpj = _digits(header_0000.get("CNPJ"), 14)
            company_query = select(Empresa).where(Empresa.cnpj == cnpj)
            if escritorio_id is None:
                company_query = company_query.where(Empresa.escritorio_id.is_(None))
            else:
                company_query = company_query.where(Empresa.escritorio_id == escritorio_id)
            empresa = self.session.execute(company_query).scalars().first()

            company_data = {
                "cnpj": cnpj,
                "nome": header_0000.get("NOME") or "",
                "uf": header_0000.get("UF") or None,
                "ie": header_0000.get("IE") or None,
                "cod_mun": _digits(header_0000.get("COD_MUN"), 7)
                if header_0000.get("COD_MUN")
                else None,
                "im": header_0000.get("IM") or None,
                "ind_sit_esp": _optional_int(header_0000.get("IND_SIT_ESP")),
                "ind_nire": _optional_int(header_0000.get("IND_NIRE")),
                "ind_fin_esc": _optional_int(header_0000.get("IND_FIN_ESC")),
                "ind_grande_por": _optional_int(header_0000.get("IND_GRANDE_POR")),
                "tip_ecd": header_0000.get("TIP_ECD") or None,
                "ident_mf": header_0000.get("IDENT_MF") or None,
                "ind_esc_cons": header_0000.get("IND_ESC_CONS") or None,
                "escritorio_id": escritorio_id,
            }
            if empresa is None:
                empresa = Empresa(**company_data)
                self.session.add(empresa)
            else:
                for key, value in company_data.items():
                    if value is not None:
                        setattr(empresa, key, value)
            self.session.flush()

            dt_ini = _date(header_0000.get("DT_INI"))
            dt_fin = _date(header_0000.get("DT_FIN"))
            existing = self.session.execute(
                select(ECD).where(
                    ECD.empresa_id == empresa.id,
                    ECD.dt_ini == dt_ini,
                    ECD.dt_fin == dt_fin,
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise DuplicateECDImportError(existing.id)

            leiaute = (header_i010 or {}).get("COD_VER_LC") or "009"
            ecd = ECD(
                empresa_id=empresa.id,
                leiaute=str(leiaute),
                dt_ini=dt_ini,
                dt_fin=dt_fin,
                ind_esc=(header_i010 or {}).get("IND_ESC") or None,
                cod_ver_lc=str(leiaute),
                hash_arquivo=file_hash,
                nome_arquivo=original_name,
            )
            self.session.add(ecd)
            self.session.flush()
            return empresa, ecd, dt_ini, dt_fin

        try:
            for record in self.parser.parse(path):
                record_type = record["_reg"]
                counts[record_type] = counts.get(record_type, 0) + 1

                offset = int(record.get("_offset_bytes", 0))
                pct = min(90.0, 5.0 + (offset / file_size) * 85.0)
                if pct - last_progress >= 1.0:
                    callback(pct, f"Processando {sum(counts.values())} registros")
                    last_progress = pct

                if record_type == "0000":
                    if header_0000 is not None:
                        raise ECDImportError("Arquivo contém mais de um registro 0000")
                    header_0000 = record
                    continue
                if record_type == "I010":
                    header_i010 = record
                    ensure_context()
                    continue
                if record_type not in {
                    "I050",
                    "I051",
                    "I052",
                    "I075",
                    "I100",
                    "I155",
                    "I200",
                    "I250",
                    "I355",
                }:
                    continue

                company, current_ecd, start_date, end_date = ensure_context()

                if record_type == "I050":
                    account = PlanoConta(
                        ecd_id=current_ecd.id,
                        cod_cta=record.get("COD_CTA") or "",
                        cod_cta_sup=record.get("COD_CTA_SUP") or None,
                        nome_cta=record.get("NOME_CTA") or "",
                        cod_nat=record.get("COD_NAT") or "01",
                        ind_cta=record.get("IND_CTA") or "A",
                        nivel=int(record.get("NIVEL") or 0),
                        dt_alt=_date(record.get("DT_ALT"), start_date)
                        if record.get("DT_ALT")
                        else None,
                    )
                    self.session.add(account)
                    self.session.flush()
                    conta_ids[account.cod_cta] = account.id
                elif record_type in {"I051", "I052"}:
                    account_id = conta_ids.get(record.get("COD_CTA") or "")
                    if account_id is None:
                        raise ECDImportError(
                            f"{record_type} sem I050 pai na linha {record.get('_linha')}"
                        )
                    if record_type == "I051":
                        self.session.add(
                            ContaReferencial(
                                plano_conta_id=account_id,
                                cod_ccus=record.get("COD_CCUS") or None,
                                cod_cta_ref=record.get("COD_CTA_REF") or "",
                            )
                        )
                    else:
                        self.session.add(
                            Aglutinacao(
                                plano_conta_id=account_id,
                                cod_ccus=record.get("COD_CCUS") or None,
                                cod_agl=record.get("COD_AGL") or "",
                            )
                        )
                elif record_type == "I075":
                    self.session.add(
                        HistoricoPadrao(
                            ecd_id=current_ecd.id,
                            cod_hist=record.get("COD_HIST") or "",
                            descr_hist=record.get("DESCR_HIST") or "",
                        )
                    )
                elif record_type == "I100":
                    self.session.add(
                        CentroCusto(
                            ecd_id=current_ecd.id,
                            cod_ccus=record.get("COD_CCUS") or "",
                            ccus=record.get("CCUS") or "",
                            dt_alt=_date(record.get("DT_ALT"), start_date)
                            if record.get("DT_ALT")
                            else None,
                        )
                    )
                elif record_type == "I155":
                    self.session.add(
                        SaldoPeriodico(
                            ecd_id=current_ecd.id,
                            cod_cta=record.get("COD_CTA") or "",
                            cod_ccus=record.get("COD_CCUS") or None,
                            dt_ini=_date(record.get("DT_INI"), start_date),
                            dt_fin=_date(record.get("DT_FIN"), end_date),
                            vl_sld_ini=record.get("VL_SLD_INI") or 0.0,
                            ind_dc_ini=record.get("IND_DC_INI") or "D",
                            vl_deb=record.get("VL_DEB") or 0.0,
                            vl_cred=record.get("VL_CRED") or 0.0,
                            vl_sld_fin=record.get("VL_SLD_FIN") or 0.0,
                            ind_dc_fin=record.get("IND_DC_FIN") or "D",
                        )
                    )
                elif record_type == "I200":
                    launch_date = _date(record.get("DT_LCTO"), start_date)
                    launch = Lancamento(
                        ecd_id=current_ecd.id,
                        num_lcto=record.get("NUM_LCTO") or "",
                        dt_lcto=launch_date,
                        vl_lcto=record.get("VL_LCTO") or 0.0,
                        ind_lcto=record.get("IND_LCTO") or "N",
                        num_arq=_optional_int(record.get("NUM_ARQ")),
                    )
                    self.session.add(launch)
                    self.session.flush()
                    current_lancamento = ((launch.num_lcto, launch_date), launch.id)
                elif record_type == "I250":
                    launch_key = (
                        record.get("NUM_LCTO") or "",
                        _date(record.get("DT_LCTO"), start_date),
                    )
                    launch_id = None
                    if current_lancamento and current_lancamento[0] == launch_key:
                        launch_id = current_lancamento[1]
                    if launch_id is None:
                        launch_id = self.session.execute(
                            select(Lancamento.id).where(
                                Lancamento.ecd_id == current_ecd.id,
                                Lancamento.num_lcto == launch_key[0],
                                Lancamento.dt_lcto == launch_key[1],
                            )
                        ).scalar_one_or_none()
                    if launch_id is None:
                        raise ECDImportError(
                            f"I250 sem I200 pai na linha {record.get('_linha')}"
                        )
                    self.session.add(
                        Partida(
                            lancamento_id=launch_id,
                            cod_cta=record.get("COD_CTA") or "",
                            cod_ccus=record.get("COD_CCUS") or None,
                            vl_dc=record.get("VL_DC") or 0.0,
                            ind_dc=record.get("IND_DC") or "D",
                            num_arq=_optional_int(record.get("NUM_ARQ")),
                            cod_hist_pad=record.get("COD_HIST_PAD") or None,
                            hist=record.get("HIST") or None,
                            cod_part=record.get("COD_PART") or None,
                        )
                    )
                elif record_type == "I355":
                    self.session.add(
                        SaldoResultado(
                            ecd_id=current_ecd.id,
                            cod_cta=record.get("COD_CTA") or "",
                            cod_ccus=record.get("COD_CCUS") or None,
                            dt_res=_date(record.get("DT_RES"), end_date),
                            vl_sld_fin=record.get("VL_SLD_FIN") or 0.0,
                            ind_dc_fin=record.get("IND_DC_FIN") or "D",
                        )
                    )

                since_flush += 1
                if since_flush >= flush_interval:
                    self.session.flush()
                    since_flush = 0

            company, current_ecd, start_date, end_date = ensure_context()
            self.session.commit()
            callback(100.0, "Importação concluída")
            return ECDImportResult(
                ecd_id=current_ecd.id,
                empresa_id=company.id,
                empresa=company.nome,
                periodo=f"{start_date} a {end_date}",
                contas=counts.get("I050", 0),
                lancamentos=counts.get("I200", 0),
                partidas=counts.get("I250", 0),
                saldos_periodicos=counts.get("I155", 0),
                saldos_resultado=counts.get("I355", 0),
                total_registros=sum(counts.values()),
                hash_arquivo=file_hash,
                nome_arquivo=original_name,
            )
        except Exception:
            self.session.rollback()
            raise
