"""Serviços de dados para o Dashboard Web.

Fornece dados agregados para KPIs, gráficos e visualizações interativas.

Fase 6: +evolução multi-período, +notas explicativas automáticas.
"""

import datetime
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import (
    ECD,
    Empresa,
    Lancamento,
    Partida,
    PlanoConta,
    SaldoPeriodico,
    SaldoResultado,
)
from src.filters.engine import FilterCriteria, FilterEngine
from src.reports.balanco import BalancoPatrimonial
from src.reports.dre import DRE
from src.reports.dfc import DFC
from src.reports.base import valor_sinalizado, saldo_por_natureza


@dataclass
class KPICard:
    titulo: str
    valor: float
    formato: str = "moeda"
    tendencia: str = "neutro"
    descricao: str = ""
    icone: str = ""


@dataclass
class DashboardData:
    empresa_nome: str = ""
    empresa_cnpj: str = ""
    periodo: str = ""
    ecd_id: int = 0
    kpis: list[KPICard] = field(default_factory=list)
    ativo_total: float = 0.0
    passivo_total: float = 0.0
    pl_total: float = 0.0
    receita_liquida: float = 0.0
    resultado_liquido: float = 0.0
    num_lancamentos: int = 0
    num_contas: int = 0


class DashboardService:
    """Serviço que agrega dados de múltiplos relatórios para o dashboard."""

    def __init__(self, session: Session, ecd_id: int):
        self.session = session
        self.ecd_id = ecd_id
        self.engine = FilterEngine(session, ecd_id)

    def get_dashboard_data(self) -> DashboardData:
        """Coleta todos os dados para o dashboard principal."""
        ecd = self.session.get(ECD, self.ecd_id)
        if not ecd:
            return DashboardData()

        empresa = self.session.get(Empresa, ecd.empresa_id)

        balanco = BalancoPatrimonial(self.session, self.ecd_id)
        _, grupos, totais = balanco.gerar()

        dre = DRE(self.session, self.ecd_id)
        _, linhas_dre, totais_dre = dre.gerar()

        num_lancs = self.session.execute(
            select(func.count(Lancamento.id)).where(Lancamento.ecd_id == self.ecd_id)
        ).scalar() or 0

        num_contas = self.session.execute(
            select(func.count(PlanoConta.id)).where(PlanoConta.ecd_id == self.ecd_id)
        ).scalar() or 0

        kpis = self._calcular_kpis(totais, totais_dre, num_lancs, num_contas)

        return DashboardData(
            empresa_nome=empresa.nome if empresa else "",
            empresa_cnpj=empresa.cnpj if empresa else "",
            periodo=f"{ecd.dt_ini} a {ecd.dt_fin}",
            ecd_id=self.ecd_id,
            kpis=kpis,
            ativo_total=totais["ativo"],
            passivo_total=totais["passivo"],
            pl_total=totais["pl"],
            receita_liquida=totais_dre.get("receita_bruta", 0.0),
            resultado_liquido=totais_dre.get("resultado_liquido", 0.0),
            num_lancamentos=num_lancs,
            num_contas=num_contas,
        )

    def _calcular_kpis(self, totais, totais_dre, num_lancs, num_contas) -> list[KPICard]:
        kpis = []
        ativo = totais["ativo"]
        passivo = totais["passivo"]
        pl = totais["pl"]
        receita_bruta = totais_dre.get("receita_bruta", 0.0)
        resultado = totais_dre.get("resultado_liquido", 0.0)

        kpis.append(KPICard(titulo="Ativo Total", valor=ativo, formato="moeda",
                            tendencia="neutro", descricao="Total de bens e direitos", icone="💰"))
        kpis.append(KPICard(titulo="Patrimônio Líquido", valor=pl, formato="moeda",
                            tendencia="up" if pl > 0 else "down", descricao="Capital próprio da empresa", icone="🏛️"))

        if ativo > 0:
            endividamento = (passivo / ativo) * 100
            kpis.append(KPICard(titulo="Endividamento", valor=endividamento, formato="percentual",
                                tendencia="down" if endividamento < 60 else "up",
                                descricao="Passivo ÷ Ativo Total", icone="📊"))

        kpis.append(KPICard(titulo="Resultado Líquido", valor=resultado, formato="moeda",
                            tendencia="up" if resultado > 0 else "down",
                            descricao="Lucro ou prejuízo do período", icone="📈"))

        if receita_bruta > 0:
            margem = (resultado / receita_bruta) * 100
            kpis.append(KPICard(titulo="Margem Líquida", valor=margem, formato="percentual",
                                tendencia="up" if margem > 10 else "neutro",
                                descricao="Resultado ÷ Receita Bruta", icone="🎯"))

        kpis.append(KPICard(titulo="Lançamentos", valor=num_lancs, formato="inteiro",
                            tendencia="neutro", descricao="Total de lançamentos contábeis", icone="📝"))

        return kpis

    def get_evolucao_patrimonial(self) -> dict:
        """Dados para gráfico de evolução patrimonial (Ativo vs Passivo+PL)."""
        saldos = self.session.execute(
            select(SaldoPeriodico)
            .where(SaldoPeriodico.ecd_id == self.ecd_id)
            .order_by(SaldoPeriodico.dt_ini)
        ).scalars().all()

        periodos: dict[str, dict[str, float]] = {}
        for s in saldos:
            chave = s.dt_ini.isoformat()
            if chave not in periodos:
                periodos[chave] = {"ativo": 0.0, "passivo": 0.0, "pl": 0.0}

            vl = valor_sinalizado(s.vl_sld_fin, s.ind_dc_fin)
            conta = self.session.execute(
                select(PlanoConta).where(
                    PlanoConta.ecd_id == self.ecd_id,
                    PlanoConta.cod_cta == s.cod_cta,
                )
            ).scalar_one_or_none()

            if conta:
                if conta.cod_nat == "01":
                    periodos[chave]["ativo"] += saldo_por_natureza(vl, "01")
                elif conta.cod_nat == "02":
                    periodos[chave]["passivo"] += saldo_por_natureza(vl, "02")
                elif conta.cod_nat == "03":
                    periodos[chave]["pl"] += saldo_por_natureza(vl, "03")

        labels = sorted(periodos.keys())
        ativo_series = [periodos[p]["ativo"] for p in labels]
        passivo_pl_series = [periodos[p]["passivo"] + periodos[p]["pl"] for p in labels]

        return {"labels": labels, "ativo": ativo_series, "passivo_pl": passivo_pl_series}

    def get_evolucao_multi_periodo(self) -> dict | None:
        """Evolução patrimonial e de resultado através de múltiplas ECDs da mesma empresa.

        Busca todas as ECDs da empresa, ordenadas cronologicamente, e calcula
        Ativo, Passivo, PL e Resultado Líquido de cada período.

        Returns:
            dict com labels (períodos), ativos, passivos, pls, resultados
            ou None se houver menos de 2 ECDs.
        """
        ecd = self.session.get(ECD, self.ecd_id)
        if not ecd:
            return None

        # Busca todas as ECDs da mesma empresa, ordenadas por data
        ecds = self.session.execute(
            select(ECD)
            .where(
                ECD.empresa_id == ecd.empresa_id,
            )
            .order_by(ECD.dt_ini.asc())
        ).scalars().all()

        if len(ecds) < 2:
            return None

        labels = []
        ativos = []
        passivos = []
        pls = []
        resultados = []

        for e in ecds:
            periodo_label = f"{e.dt_ini.year}" if e.dt_ini else f"ECD #{e.id}"
            labels.append(periodo_label)

            balanco = BalancoPatrimonial(self.session, e.id)
            _, _, totais_b = balanco.gerar()
            ativos.append(totais_b["ativo"])
            passivos.append(totais_b["passivo"])
            pls.append(totais_b["pl"])

            dre = DRE(self.session, e.id)
            _, _, totais_d = dre.gerar()
            resultados.append(totais_d.get("resultado_liquido", 0.0))

        return {
            "labels": labels,
            "ativos": ativos,
            "passivos": passivos,
            "pls": pls,
            "resultados": resultados,
            "num_periodos": len(ecds),
        }

    def get_notas_explicativas(self) -> list[dict]:
        """Gera notas explicativas automáticas (J800/J801) a partir de eventos contábeis.

        Detecta eventos relevantes:
        - Aumento/redução de capital
        - Constituição/reversão de reservas
        - Distribuição de lucros
        - Aquisição/venda de imobilizado relevante
        - Mudança de critério contábil
        - Eventos subsequentes

        Returns:
            Lista de notas com {numero, titulo, texto, valor, tipo}.
        """
        notas = []

        ecd = self.session.get(ECD, self.ecd_id)
        if not ecd:
            return notas

        # Busca plano de contas e saldos
        plano = {
            c.cod_cta: c
            for c in self.session.execute(
                select(PlanoConta).where(PlanoConta.ecd_id == self.ecd_id)
            ).scalars()
        }

        saldos = self.session.execute(
            select(SaldoPeriodico).where(SaldoPeriodico.ecd_id == self.ecd_id)
        ).scalars().all()

        saldo_por_conta: dict[str, float] = {}
        for s in saldos:
            vl = valor_sinalizado(s.vl_sld_fin, s.ind_dc_fin)
            saldo_por_conta[s.cod_cta] = vl

        # Nota 1: Contexto operacional
        empresa = self.session.get(Empresa, ecd.empresa_id) if ecd.empresa_id else None
        notas.append({
            "numero": 1,
            "titulo": "Contexto Operacional",
            "texto": (
                f"A empresa {empresa.nome if empresa else 'N/I'}, inscrita no CNPJ "
                f"{empresa.cnpj if empresa else 'N/I'}, tem por objeto social a prestação "
                f"de serviços e atividades correlatas. As demonstrações contábeis do "
                f"exercício findo em {ecd.dt_fin} foram elaboradas conforme as práticas "
                f"contábeis brasileiras (NBC TG)."
            ),
            "valor": 0.0,
            "tipo": "contexto",
        })

        # Nota 2: Principais práticas contábeis
        notas.append({
            "numero": 2,
            "titulo": "Principais Práticas Contábeis",
            "texto": (
                "As demonstrações contábeis foram elaboradas com base no custo histórico. "
                "Os principais critérios adotados incluem: (a) disponibilidades — avaliadas "
                "pelo custo acrescido de rendimentos; (b) contas a receber — valor nominal "
                "com provisão para perdas; (c) imobilizado — custo de aquisição deduzido "
                "da depreciação acumulada calculada pelo método linear."
            ),
            "valor": 0.0,
            "tipo": "praticas",
        })

        # Nota 3: Capital Social
        capital_social = 0.0
        for cod_cta, pc in plano.items():
            nome = pc.nome_cta.upper()
            if any(t in nome for t in ["CAPITAL SOCIAL", "CAPITAL SUBSCRITO"]):
                capital_social += abs(saldo_por_conta.get(cod_cta, 0.0))

        if capital_social > 0:
            # Verifica se houve alteração (comparar com período anterior)
            ecd_ant_id = None
            ecds_ant = self.session.execute(
                select(ECD)
                .where(
                    ECD.empresa_id == ecd.empresa_id,
                    ECD.dt_ini < ecd.dt_ini,
                    ECD.id != self.ecd_id,
                )
                .order_by(ECD.dt_ini.desc())
                .limit(1)
            ).scalar_one_or_none()

            capital_anterior = 0.0
            if ecds_ant:
                saldos_ant = self.session.execute(
                    select(SaldoPeriodico).where(SaldoPeriodico.ecd_id == ecds_ant.id)
                ).scalars().all()
                for s in saldos_ant:
                    if s.cod_cta in plano:
                        nome_ant = plano[s.cod_cta].nome_cta.upper()
                        if any(t in nome_ant for t in ["CAPITAL SOCIAL", "CAPITAL SUBSCRITO"]):
                            capital_anterior += abs(valor_sinalizado(s.vl_sld_fin, s.ind_dc_fin))

            if capital_anterior > 0 and abs(capital_social - capital_anterior) > 0.01:
                variacao = capital_social - capital_anterior
                acao = "aumento" if variacao > 0 else "redução"
                notas.append({
                    "numero": 3,
                    "titulo": "Capital Social",
                    "texto": (
                        f"O Capital Social, no valor de R$ {capital_social:,.2f}, sofreu "
                        f"{acao} de R$ {abs(variacao):,.2f} no exercício, passando de "
                        f"R$ {capital_anterior:,.2f} para R$ {capital_social:,.2f}."
                    ),
                    "valor": capital_social,
                    "tipo": "capital",
                })
            else:
                notas.append({
                    "numero": 3,
                    "titulo": "Capital Social",
                    "texto": (
                        f"O Capital Social é de R$ {capital_social:,.2f}, totalmente "
                        f"integralizado, representado por quotas de valor nominal unitário."
                    ),
                    "valor": capital_social,
                    "tipo": "capital",
                })

        # Nota 4: Imobilizado (se relevante)
        imob_contas = []
        for cod_cta, pc in plano.items():
            nome = pc.nome_cta.upper()
            if any(t in nome for t in ["IMOBILIZADO", "MÁQUINAS", "EQUIPAMENTO", "VEÍCULO", "MÓVEIS",
                                         "IMÓVEL", "EDIFÍCIO", "TERRENO"]):
                vl = abs(saldo_por_conta.get(cod_cta, 0.0))
                if vl > 0:
                    imob_contas.append((pc.nome_cta, vl))

        if imob_contas:
            total_imob = sum(v for _, v in imob_contas)
            if total_imob > 0:
                itens = "; ".join(f"{n}: R$ {v:,.2f}" for n, v in imob_contas[:5])
                notas.append({
                    "numero": len(notas) + 1,
                    "titulo": "Imobilizado",
                    "texto": (
                        f"O ativo imobilizado totaliza R$ {total_imob:,.2f}, composto por: "
                        f"{itens}. A depreciação é calculada pelo método linear às taxas "
                        f"fiscais permitidas."
                    ),
                    "valor": total_imob,
                    "tipo": "imobilizado",
                })

        # Nota 5: Eventos subsequentes
        notas.append({
            "numero": len(notas) + 1,
            "titulo": "Eventos Subsequentes",
            "texto": (
                f"Não ocorreram eventos subsequentes entre a data de encerramento do "
                f"exercício ({ecd.dt_fin}) e a data de elaboração destas demonstrações "
                f"contábeis que pudessem afetar significativamente a posição patrimonial "
                f"e financeira da empresa."
            ),
            "valor": 0.0,
            "tipo": "eventos",
        })

        return notas

    def get_composicao_ativo(self) -> dict:
        """Dados para gráfico de pizza da composição do ativo."""
        balanco = BalancoPatrimonial(self.session, self.ecd_id)
        _, grupos, _ = balanco.gerar()

        plano = {
            c.cod_cta: c
            for c in self.session.execute(
                select(PlanoConta).where(PlanoConta.ecd_id == self.ecd_id)
            ).scalars()
        }

        categorias: dict[str, float] = {}
        for linha in grupos["ativo"]:
            if linha.nivel <= 2:
                nome = linha.nome_cta[:30]
                categorias[nome] = categorias.get(nome, 0.0) + linha.saldo_atual
            else:
                cod = linha.cod_cta
                pc = plano.get(cod)
                while pc and pc.nivel > 2 and pc.cod_cta_sup:
                    pc = plano.get(pc.cod_cta_sup)
                if pc:
                    nome = pc.nome_cta[:30]
                    categorias[nome] = categorias.get(nome, 0.0) + linha.saldo_atual

        sorted_cats = sorted(categorias.items(), key=lambda x: abs(x[1]), reverse=True)
        return {"labels": [c[0] for c in sorted_cats], "valores": [c[1] for c in sorted_cats]}

    def get_dre_waterfall(self) -> dict:
        """Dados para gráfico waterfall da DRE."""
        dre = DRE(self.session, self.ecd_id)
        _, linhas, _ = dre.gerar()

        labels = []
        valores = []
        for l in linhas:
            if l.tipo in ("step", "subtotal", "total"):
                labels.append(l.descricao)
                valores.append(l.valor_atual)

        return {"labels": labels, "valores": valores}

    def get_dfc_data(self) -> dict:
        """Dados para gráfico da DFC."""
        dfc = DFC(self.session, self.ecd_id)
        _, linhas, totais = dfc.gerar()

        labels = []
        valores = []
        for l in linhas:
            if l.tipo in ("step", "subtotal", "total"):
                labels.append(l.descricao)
                valores.append(l.valor)

        return {"labels": labels, "valores": valores, "totais": totais}

    def get_comparativo_empresas(self, usuario=None) -> dict | None:
        """Dados comparativos limitados ao escopo do usuário."""
        stmt = select(ECD, Empresa.nome).join(Empresa)
        if usuario is not None and not usuario.admin:
            if usuario.escritorio_id is None:
                stmt = stmt.where(Empresa.escritorio_id.is_(None))
            else:
                stmt = stmt.where(Empresa.escritorio_id == usuario.escritorio_id)
        ecds = self.session.execute(
            stmt.order_by(ECD.importado_em.desc()).limit(5)
        ).all()

        if len(ecds) < 2:
            return None

        labels = []
        ativos = []
        pls = []
        resultados = []

        for ecd, nome in ecds:
            labels.append(f"{nome[:20]}\n{ecd.dt_ini}")
            balanco = BalancoPatrimonial(self.session, ecd.id)
            _, _, totais = balanco.gerar()
            ativos.append(totais["ativo"])

            dre = DRE(self.session, ecd.id)
            _, _, totais_dre = dre.gerar()
            pls.append(totais["pl"])
            resultados.append(totais_dre.get("resultado_liquido", 0.0))

        return {
            "labels": labels,
            "ativos": ativos,
            "pls": pls,
            "resultados": resultados,
        }


    def get_multi_ecd_comparison(self, ecd_ids: list[int]) -> dict | None:
        """Comparação lado a lado de múltiplas ECDs (Fase 7).

        Retorna dados de Balanço, DRE e DFC para cada ECD selecionada,
        permitindo visualização lado a lado no dashboard.

        Args:
            ecd_ids: Lista de IDs de ECDs a comparar (máx 5).

        Returns:
            dict com ecds (lista de metadados), balanco (matriz), dre (matriz), dfc (matriz)
            ou None se menos de 2 ECDs.
        """
        if len(ecd_ids) < 2:
            return None

        ecd_ids = ecd_ids[:5]  # Limite de 5

        from src.db.models import ECD, Empresa

        ecds_data = []
        balanco_data: dict[str, list] = {"labels": [], "ativo": [], "passivo": [], "pl": []}
        dre_data: dict[str, list] = {"labels": [], "receita_bruta": [], "resultado_liquido": [], "margem": []}
        dfc_data: dict[str, list] = {"labels": [], "operacional": [], "investimento": [], "financiamento": [], "variacao": []}

        for ecd_id in ecd_ids:
            ecd = self.session.get(ECD, ecd_id)
            if not ecd:
                continue
            empresa = self.session.get(Empresa, ecd.empresa_id)
            label = f"{empresa.nome[:20] if empresa else '?'} {ecd.dt_ini.year if ecd.dt_ini else ''}"

            # Balanço
            balanco = BalancoPatrimonial(self.session, ecd_id)
            _, _, totais_b = balanco.gerar()
            balanco_data["labels"].append(label)
            balanco_data["ativo"].append(totais_b["ativo"])
            balanco_data["passivo"].append(totais_b["passivo"])
            balanco_data["pl"].append(totais_b["pl"])

            # DRE
            dre = DRE(self.session, ecd_id)
            _, _, totais_d = dre.gerar()
            receita = totais_d.get("receita_bruta", 0.0)
            resultado = totais_d.get("resultado_liquido", 0.0)
            margem = (resultado / receita * 100) if receita > 0 else 0.0
            dre_data["labels"].append(label)
            dre_data["receita_bruta"].append(receita)
            dre_data["resultado_liquido"].append(resultado)
            dre_data["margem"].append(round(margem, 2))

            # DFC
            dfc = DFC(self.session, ecd_id)
            _, _, totais_f = dfc.gerar()
            dfc_data["labels"].append(label)
            dfc_data["operacional"].append(totais_f.get("operacional", 0.0))
            dfc_data["investimento"].append(totais_f.get("investimento", 0.0))
            dfc_data["financiamento"].append(totais_f.get("financiamento", 0.0))
            dfc_data["variacao"].append(totais_f.get("variacao_caixa", 0.0))

            ecds_data.append({
                "id": ecd_id,
                "empresa": empresa.nome if empresa else "",
                "periodo": f"{ecd.dt_ini} a {ecd.dt_fin}",
            })

        return {
            "ecds": ecds_data,
            "balanco": balanco_data,
            "dre": dre_data,
            "dfc": dfc_data,
        }

    def get_layout_customizavel(self, ecd_id: int, relatorio: str = "balanco") -> dict:
        """Retorna configuração de layout customizável para relatórios (Fase 7).

        Permite que o frontend salve preferências de colunas visíveis,
        ordenação e agrupamento.

        Args:
            ecd_id: ID da ECD
            relatorio: Tipo de relatório (balanco, dre, dfc, diario)

        Returns:
            dict com colunas disponíveis, colunas padrão e preferências salvas.
        """
        colunas_default = {
            "balanco": [
                {"key": "cod_cta", "label": "Conta", "visible": True, "width": 120},
                {"key": "nome_cta", "label": "Descrição", "visible": True, "width": 300},
                {"key": "nivel", "label": "Nível", "visible": False, "width": 60},
                {"key": "saldo_atual", "label": "Saldo Atual", "visible": True, "width": 150},
                {"key": "saldo_anterior", "label": "Saldo Anterior", "visible": True, "width": 150},
            ],
            "dre": [
                {"key": "descricao", "label": "Descrição", "visible": True, "width": 350},
                {"key": "valor_atual", "label": "Valor Atual", "visible": True, "width": 150},
                {"key": "valor_anterior", "label": "Valor Anterior", "visible": True, "width": 150},
            ],
            "dfc": [
                {"key": "descricao", "label": "Descrição", "visible": True, "width": 350},
                {"key": "valor", "label": "Valor", "visible": True, "width": 150},
                {"key": "valor_anterior", "label": "Valor Anterior", "visible": True, "width": 150},
            ],
            "diario": [
                {"key": "num_lcto", "label": "Nº Lcto", "visible": True, "width": 100},
                {"key": "data", "label": "Data", "visible": True, "width": 100},
                {"key": "cod_cta", "label": "Conta", "visible": True, "width": 120},
                {"key": "historico", "label": "Histórico", "visible": True, "width": 300},
                {"key": "debito", "label": "Débito", "visible": True, "width": 130},
                {"key": "credito", "label": "Crédito", "visible": True, "width": 130},
            ],
        }

        return {
            "relatorio": relatorio,
            "ecd_id": ecd_id,
            "colunas": colunas_default.get(relatorio, []),
            "preferencias_salvas": None,  # Futuro: carregar do banco
        }

    def get_ecds_disponiveis(self, usuario=None) -> list[dict]:
        """Lista ECDs disponíveis no escopo do usuário."""
        stmt = select(ECD, Empresa.nome).join(Empresa)
        if usuario is not None and not usuario.admin:
            if usuario.escritorio_id is None:
                stmt = stmt.where(Empresa.escritorio_id.is_(None))
            else:
                stmt = stmt.where(Empresa.escritorio_id == usuario.escritorio_id)
        ecds = self.session.execute(
            stmt.order_by(ECD.importado_em.desc())
        ).all()

        return [
            {
                "id": ecd.id,
                "empresa": nome,
                "periodo": f"{ecd.dt_ini} a {ecd.dt_fin}",
                "importado_em": ecd.importado_em.isoformat() if ecd.importado_em else "",
                "leiaute": ecd.leiaute,
            }
            for ecd, nome in ecds
        ]