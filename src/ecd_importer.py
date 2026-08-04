"""Importação incremental de ECD para o banco de dados.

O serviço percorre o arquivo uma única vez para persistir os registros e mantém
em memória apenas metadados e mapas pequenos. O hash pode ser fornecido pelo
fluxo de upload ou calculado em chunks, sem carregar o arquivo inteiro.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    ECD,
    Aglutinacao,
    CentroCusto,
    ContaReferencial,
    DemonstracaoContabil,
    Empresa,
    HistoricoPadrao,
    Lancamento,
    LinhaDemonstracao,
    Partida,
    PlanoConta,
    SaldoPeriodico,
    SaldoResultado,
)
from src.parsers.ecd import ECDParser
from src.settings import get_settings
from src.validators.integridade import encontrar_ciclos
from src.webhooks import emitir

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[float, str], None]


class ECDImportError(ValueError):
    """Erro de validação ou consistência durante uma importação ECD."""


class ECDImportCancelled(ECDImportError):
    """A importação foi cancelada antes de terminar.

    Nada é persistido: a transação inteira é revertida.  Uma escrituração pela
    metade é pior que nenhuma — o balanço não fecharia e ninguém teria como
    saber que faltam lançamentos.
    """

    def __init__(self, registros_lidos: int):
        self.registros_lidos = registros_lidos
        super().__init__(f"Importação cancelada após {registros_lidos} registros")


class CancelToken:
    """Sinal de cancelamento compartilhado entre quem pede e quem importa.

    Thread-safe por construção: só há uma transição possível (não cancelado →
    cancelado) e ela é feita por atribuição de bool.
    """

    __slots__ = ("_cancelado", "motivo")

    def __init__(self) -> None:
        self._cancelado = False
        self.motivo: str | None = None

    def cancelar(self, motivo: str | None = None) -> None:
        self.motivo = motivo
        self._cancelado = True

    @property
    def cancelado(self) -> bool:
        return self._cancelado


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
    linhas_demonstracao: int
    total_registros: int
    hash_arquivo: str
    nome_arquivo: str

    def to_dict(self) -> dict:
        return asdict(self)


def hash_file(path: Path, chunk_size: int | None = None) -> str:
    """Calcula SHA-256 em memória constante.

    O tamanho do bloco vem de ``SPED_HUB_ECD_CHUNK_BYTES`` — outra variável
    que existia nas settings desde a etapa 1 sem nenhum consumidor.
    """
    if chunk_size is None:
        chunk_size = get_settings().ecd_import_chunk_bytes
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

    def _referencia_do_banco(self) -> str:
        """A URL do banco desta sessão, para quem precisa abrir outra.

        Sem `try`: a chamada só falha em sessão sem bind, que não existe aqui
        — este método é chamado depois do commit. Engolir a falha devolveria
        ``None``, e ``None`` cai na configuração do processo, que é exatamente
        o defeito que este método existe para fechar.
        """
        return self.session.get_bind().url.render_as_string(hide_password=False)

    def _avisar_leiaute_diferente(self, cod_ver_lc: str) -> None:
        """O arquivo diz uma versão de leiaute; nós lemos com outra.

        O parser carrega sempre o `ecd_v9.yml`, seja qual for o `COD_VER_LC`
        do I010.  Quando as duas versões não batem, o arquivo é lido do
        mesmo jeito — e os campos podem estar em posições diferentes, o que
        põe dado errado em coluna certa sem erro nenhum.

        Avisa em vez de recusar (ADR 0009), pela razão da §8.2: uma ECD
        antiga ainda é melhor que ECD nenhuma, e travar a leitura de um
        arquivo de 2019 seria tirar uma capacidade para evitar um risco que
        o aviso já expõe.
        """
        do_arquivo, do_leiaute = str(cod_ver_lc).strip(), str(self.parser.versao).strip()
        if do_arquivo.lstrip("0") == do_leiaute.lstrip("0"):
            return
        logger.warning(
            "O arquivo declara leiaute %s (COD_VER_LC do I010) e foi lido com "
            "o leiaute %s, o único que este programa conhece. Os campos podem "
            "estar em outras posições: confira os valores importados antes de "
            "usar.",
            do_arquivo,
            do_leiaute,
        )

    # Campo do parser → coluna, por registro.  O que difere entre J100, J150 e
    # J210 é só o que classifica a linha; o miolo é o mesmo nos três.
    _CLASSIFICACAO_DA_LINHA = {
        "J100": {"IND_GRP_BAL": "ind_grp_bal"},
        "J150": {"NU_ORDEM": "nu_ordem", "IND_GRP_DRE": "ind_grp_dre"},
        "J210": {"IND_TIP": "ind_tip"},
    }

    def _guardar_demonstracao(
        self,
        record: dict,
        ecd_id: int,
        demonstracoes: dict,
        inicio: datetime.date,
        fim: datetime.date,
    ) -> None:
        """O J005 abre a demonstração; J100, J150 e J210 são linhas dela.

        As linhas herdam do J005 o período e o `ID_DEM` (ver `HERDA_DE` no
        parser), e é por essa chave que cada uma acha a demonstração a que
        pertence — não pela ordem de leitura, que um arquivo fora de ordem
        quebraria em silêncio.
        """
        chave = (
            _date(record.get("DT_INI"), inicio),
            _date(record.get("DT_FIN"), fim),
            str(record.get("ID_DEM") or "1"),
        )
        if record["_reg"] == "J005":
            if chave in demonstracoes:
                return
            demonstracao = DemonstracaoContabil(
                ecd_id=ecd_id,
                dt_ini=chave[0],
                dt_fin=chave[1],
                id_dem=chave[2],
                cab_dem=record.get("CAB_DEM") or None,
            )
            self.session.add(demonstracao)
            demonstracoes[chave] = demonstracao
            return

        demonstracao = demonstracoes.get(chave)
        if demonstracao is None:
            raise ECDImportError(
                f"{record['_reg']} sem J005 correspondente na linha {record.get('_linha')} "
                f"(período {chave[0]} a {chave[1]}, demonstração {chave[2]})"
            )

        linha = LinhaDemonstracao(
            registro=record["_reg"],
            cod_agl=str(record.get("COD_AGL") or ""),
            ind_cod_agl=record.get("IND_COD_AGL") or None,
            nivel_agl=_optional_int(record.get("NIVEL_AGL")),
            cod_agl_sup=record.get("COD_AGL_SUP") or None,
            descricao=record.get("DESCR_COD_AGL") or None,
            vl_cta_ini=record.get("VL_CTA_INI") or 0.0,
            ind_dc_cta_ini=record.get("IND_DC_CTA_INI") or None,
            vl_cta_fin=record.get("VL_CTA_FIN") or 0.0,
            ind_dc_cta_fin=record.get("IND_DC_CTA_FIN") or None,
        )
        for campo, coluna in self._CLASSIFICACAO_DA_LINHA[record["_reg"]].items():
            valor = record.get(campo)
            setattr(linha, coluna, _optional_int(valor) if coluna == "nu_ordem" else valor or None)
        demonstracao.linhas.append(linha)

    def importar(
        self,
        path: Path,
        *,
        hash_arquivo: str | None = None,
        nome_arquivo: str | None = None,
        escritorio_id: int | None = None,
        progress: ProgressCallback | None = None,
        flush_interval: int | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ECDImportResult:
        path = Path(path)
        if not path.is_file():
            raise ECDImportError(f"Arquivo não encontrado: {path}")
        # `SPED_HUB_ECD_CHUNK_ROWS` existia nas settings desde a etapa 1 sem
        # nenhum consumidor; o intervalo era um 1000 fixo no código.
        if flush_interval is None:
            flush_interval = get_settings().ecd_import_chunk_rows
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
        contas_pendentes: dict[str, PlanoConta] = {}
        demonstracoes: dict[tuple, DemonstracaoContabil] = {}
        current_lancamento: tuple[tuple[str, datetime.date], Lancamento] | None = None
        counts: dict[str, int] = {}
        since_flush = 0
        last_progress = 2.0

        def ensure_context() -> tuple[Empresa, ECD, datetime.date, datetime.date]:
            nonlocal empresa, ecd, dt_ini, dt_fin
            if (
                ecd is not None
                and empresa is not None
                and dt_ini is not None
                and dt_fin is not None
            ):
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
                "cod_mun": (
                    _digits(header_0000.get("COD_MUN"), 7) if header_0000.get("COD_MUN") else None
                ),
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
            self._avisar_leiaute_diferente(leiaute)
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

                if cancel_token is not None and cancel_token.cancelado:
                    raise ECDImportCancelled(sum(counts.values()))

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
                    "J005",
                    "J100",
                    "J150",
                    "J210",
                }:
                    continue

                company, current_ecd, start_date, end_date = ensure_context()

                if record_type == "I050":
                    account = PlanoConta(
                        ecd_id=current_ecd.id,
                        cod_cta=record.get("COD_CTA") or "",
                        cod_cta_sup=record.get("COD_CTA_SUP") or None,
                        nome_cta=record.get("CTA") or "",
                        cod_nat=record.get("COD_NAT") or "01",
                        ind_cta=record.get("IND_CTA") or "A",
                        nivel=int(record.get("NIVEL") or 0),
                        dt_alt=(
                            _date(record.get("DT_ALT"), start_date)
                            if record.get("DT_ALT")
                            else None
                        ),
                    )
                    # Sem `flush()` aqui: o objeto é guardado e o id só é
                    # necessário no flush do lote.  Um plano de contas real
                    # tem milhares de I050 — um round-trip por conta pesa.
                    self.session.add(account)
                    contas_pendentes[account.cod_cta] = account
                elif record_type in {"I051", "I052"}:
                    # Anexa pelo relacionamento: o SQLAlchemy resolve a FK na
                    # hora do flush, sem precisar do id agora.
                    account = contas_pendentes.get(record.get("COD_CTA") or "")
                    if account is None:
                        raise ECDImportError(
                            f"{record_type} sem I050 pai na linha {record.get('_linha')}"
                        )
                    if record_type == "I051":
                        account.contas_referenciais.append(
                            ContaReferencial(
                                cod_ccus=record.get("COD_CCUS") or None,
                                cod_cta_ref=record.get("COD_CTA_REF") or "",
                            )
                        )
                    else:
                        account.aglutinacoes.append(
                            Aglutinacao(
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
                            dt_alt=(
                                _date(record.get("DT_ALT"), start_date)
                                if record.get("DT_ALT")
                                else None
                            ),
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
                        # Sem `num_arq`: o I200 do leiaute 9 não tem esse
                        # campo.  Na posição 6 está o DT_LCTO_EXT (data do
                        # lançamento extemporâneo), e ler uma data como
                        # número de arquivo gravava 31122025 em `num_arq`.
                        # Quem tem NUM_ARQ é o I250, logo abaixo.
                    )
                    # Este `flush()` era o gargalo: um round-trip por
                    # lançamento.  Num arquivo com 80 mil lançamentos eram 80
                    # mil flushes, ~76% do tempo total de importação.
                    self.session.add(launch)
                    current_lancamento = ((launch.num_lcto, launch_date), launch)
                elif record_type == "I250":
                    launch_key = (
                        record.get("NUM_LCTO") or "",
                        _date(record.get("DT_LCTO"), start_date),
                    )
                    partida = Partida(
                        cod_cta=record.get("COD_CTA") or "",
                        cod_ccus=record.get("COD_CCUS") or None,
                        vl_dc=record.get("VL_DC") or 0.0,
                        ind_dc=record.get("IND_DC") or "D",
                        num_arq=_optional_int(record.get("NUM_ARQ")),
                        cod_hist_pad=record.get("COD_HIST_PAD") or None,
                        hist=record.get("HIST") or None,
                        cod_part=record.get("COD_PART") or None,
                    )
                    # Caso normal: os I250 vêm logo após o seu I200, e basta
                    # anexar pelo relacionamento — a FK é resolvida no flush.
                    if current_lancamento and current_lancamento[0] == launch_key:
                        current_lancamento[1].partidas.append(partida)
                    else:
                        # Fora de ordem (ou lançamento já persistido em lote
                        # anterior): aí sim é preciso buscar o id.
                        self.session.flush()
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
                        partida.lancamento_id = launch_id
                        self.session.add(partida)
                elif record_type in {"J005", "J100", "J150", "J210"}:
                    self._guardar_demonstracao(
                        record, current_ecd.id, demonstracoes, start_date, end_date
                    )
                elif record_type == "I355":
                    self.session.add(
                        SaldoResultado(
                            ecd_id=current_ecd.id,
                            cod_cta=record.get("COD_CTA") or "",
                            cod_ccus=record.get("COD_CCUS") or None,
                            dt_res=_date(record.get("DT_RES"), end_date),
                            # No I355 o manual chama esses campos de VL_CTA e
                            # IND_DC; a coluna do banco mantém o nome antigo.
                            vl_sld_fin=record.get("VL_CTA") or 0.0,
                            ind_dc_fin=record.get("IND_DC") or "D",
                        )
                    )

                since_flush += 1
                if since_flush >= flush_interval:
                    self.session.flush()
                    since_flush = 0

            company, current_ecd, start_date, end_date = ensure_context()

            # Hierarquia cíclica é recusada ANTES do commit (ADR 0006): uma
            # conta que é a própria sintética — ou A→B→A — não tem leitura
            # contábil possível, já travou o dashboard inteiro (PR #7) e a
            # transação única (§6.1) garante que nada desta importação fica.
            ciclos = encontrar_ciclos({c.cod_cta: c.cod_cta_sup for c in contas_pendentes.values()})
            if ciclos:
                caminho = " → ".join(ciclos[0] + [ciclos[0][0]])
                raise ECDImportError(
                    f"Hierarquia do plano de contas tem ciclo ({caminho}): "
                    f"{len(ciclos)} ciclo(s) encontrado(s). Arquivo recusado; "
                    "nada foi importado. Corrija o COD_CTA_SUP no sistema de "
                    "origem e gere a ECD novamente."
                )

            self.session.commit()
            callback(100.0, "Importação concluída")

            resultado = ECDImportResult(
                ecd_id=current_ecd.id,
                empresa_id=company.id,
                empresa=company.nome,
                periodo=f"{start_date} a {end_date}",
                contas=counts.get("I050", 0),
                lancamentos=counts.get("I200", 0),
                partidas=counts.get("I250", 0),
                saldos_periodicos=counts.get("I155", 0),
                saldos_resultado=counts.get("I355", 0),
                linhas_demonstracao=sum(counts.get(reg, 0) for reg in ("J100", "J150", "J210")),
                total_registros=sum(counts.values()),
                hash_arquivo=file_hash,
                nome_arquivo=original_name,
            )

            # Evento DEPOIS do commit, nunca antes: webhook de escrituração
            # que a transação ainda pode reverter é notificação de algo que
            # não aconteceu.  Emitir aqui — e não em cada chamador — cobre os
            # quatro caminhos de entrada de uma vez (CLI, dashboard síncrono,
            # dashboard assíncrono e watchdog), que convergem neste método.
            #
            # `emitir` não bloqueia e engole as próprias falhas: importação
            # concluída não vira erro porque o endpoint do cliente caiu.
            #
            # O banco vai explícito.  Sem ele, `emitir` cai na configuração do
            # processo, e `sped-hub importar-ecd --db outro.db` procurava os
            # assinantes no banco configurado — não naquele onde a ECD acabou
            # de entrar.  Nenhum assinante lá, evento nenhum: o webhook não
            # disparava e ninguém via erro.
            emitir("ecd.importada", resultado.to_dict(), db_path=self._referencia_do_banco())
            return resultado
        except Exception:
            self.session.rollback()
            raise
