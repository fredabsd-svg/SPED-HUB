"""XML de NF-e para os testes — leiaute 4.00 com os grupos da reforma.

Sintético, mas fiel à estrutura: namespace, `nfeProc` envolvendo `NFe` e
`protNFe`, o ICMS embrulhado na variante (`ICMS00`), PIS e Cofins em
`PISAliq`/`COFINSAliq`, e os grupos `IBSCBS` e `IS` da NT 2025.002.

O `vNF` fecha pela regra W16-10 do MOC — produtos, mais frete, mais IPI. Uma
nota de teste que não fechasse pela própria regra do leiaute faria todo teste
sobre o total do documento concordar com um documento que a SEFAZ recusaria.

Não usa arquivo de cliente: XML real de NF-e traz CNPJ, endereço e produtos de
terceiros, que não podem ser versionados.
"""

from __future__ import annotations

import datetime

CHAVE_PADRAO = "35260712345678000195550010000000011000000017"


def nfe_xml(
    *,
    chave: str = CHAVE_PADRAO,
    numero: str = "1",
    serie: str = "1",
    modelo: str = "55",
    tp_nf: str = "1",
    emitente_cnpj: str = "12345678000195",
    destinatario_cnpj: str = "98765432000198",
    c_stat: str = "100",
    com_reforma: bool = True,
    is_especifico: bool = False,
    beneficios: bool = False,
    itens: int = 1,
    mod_frete: str | None = None,
    valor_frete: float = 0.0,
    data_emissao: str = "2026-07-30",
    data_saida: str | None = None,
) -> bytes:
    """Monta uma NF-e completa.

    `com_reforma=False` produz a nota como era antes de 03/08/2026 — sem os
    grupos IBS/CBS/IS —, que é o que o sistema recebe ao importar histórico.

    `dhEmi` e `dhSaiEnt` são datas **diferentes** por padrão — a saída no dia
    seguinte à emissão. Iguais, um adaptador que lesse o campo errado passaria
    despercebido.
    """
    saida = (
        data_saida
        or (datetime.date.fromisoformat(data_emissao) + datetime.timedelta(days=1)).isoformat()
    )
    corpo_itens = "".join(
        _item(n, com_reforma=com_reforma, is_especifico=is_especifico, beneficios=beneficios)
        for n in range(1, itens + 1)
    )
    total_prod = 1000.00 * itens
    reforma_tot = (
        f"""
      <IBSCBSTot>
        <vBCIBSCBS>{total_prod:.2f}</vBCIBSCBS>
        <gIBS>
          <gIBSUF><vIBSUF>{0.70 * itens:.2f}</vIBSUF></gIBSUF>
          <gIBSMun><vIBSMun>{0.30 * itens:.2f}</vIBSMun></gIBSMun>
        </gIBS>
        <gCBS><vCBS>{9.00 * itens:.2f}</vCBS></gCBS>
      </IBSCBSTot>
      <vNFTot>{total_prod + valor_frete + 50.00 * itens + (9.00 + 0.70 + 0.30 + 10.00) * itens:.2f}</vNFTot>"""
        if com_reforma
        else ""
    )
    # O grupo `transp` é opcional aqui de propósito: nota sem ele é o que se
    # recebe de emissor que não preenche o frete, e é o caso que o gerador
    # precisa tratar sem inventar quem pagou.
    transporte = f"\n      <transp><modFrete>{mod_frete}</modFrete></transp>" if mod_frete else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">
  <NFe>
    <infNFe Id="NFe{chave}" versao="4.00">
      <ide>
        <cUF>35</cUF>
        <natOp>VENDA DE MERCADORIA</natOp>
        <mod>{modelo}</mod>
        <serie>{serie}</serie>
        <nNF>{numero}</nNF>
        <dhEmi>{data_emissao}T09:15:00-03:00</dhEmi>
        <dhSaiEnt>{saida}T14:00:00-03:00</dhSaiEnt>
        <tpNF>{tp_nf}</tpNF>
        <idDest>1</idDest>
        <cMunFG>3550308</cMunFG>
        <cMunFGIBS>3106200</cMunFGIBS>
        <finNFe>1</finNFe>
      </ide>
      <emit>
        <CNPJ>{emitente_cnpj}</CNPJ>
        <xNome>INDUSTRIA EXEMPLO LTDA</xNome>
        <enderEmit><xMun>SAO PAULO</xMun><UF>SP</UF></enderEmit>
        <IE>110042490114</IE>
      </emit>
      <dest>
        <CNPJ>{destinatario_cnpj}</CNPJ>
        <xNome>COMERCIO EXEMPLO LTDA</xNome>
        <enderDest><xMun>PALMAS</xMun><UF>TO</UF></enderDest>
        <IE>293456789</IE>
      </dest>
{corpo_itens}
      <total>
        <ICMSTot>
          <vBC>{total_prod:.2f}</vBC>
          <vICMS>{180.00 * itens:.2f}</vICMS>
          <vST>0.00</vST>
          <vProd>{total_prod:.2f}</vProd>
          <vFrete>{valor_frete:.2f}</vFrete>
          <vSeg>0.00</vSeg>
          <vDesc>0.00</vDesc>
          <vOutro>0.00</vOutro>
          <vIPI>{50.00 * itens:.2f}</vIPI>
          <vPIS>{16.50 * itens:.2f}</vPIS>
          <vCOFINS>{76.00 * itens:.2f}</vCOFINS>
          <vNF>{total_prod + valor_frete + 50.00 * itens:.2f}</vNF>
        </ICMSTot>{reforma_tot}
      </total>{transporte}
    </infNFe>
  </NFe>
  <protNFe versao="4.00">
    <infProt>
      <chNFe>{chave}</chNFe>
      <cStat>{c_stat}</cStat>
      <xMotivo>Autorizado o uso da NF-e</xMotivo>
    </infProt>
  </protNFe>
</nfeProc>
""".encode()


def _item(numero: int, *, com_reforma: bool, is_especifico: bool, beneficios: bool = False) -> str:
    reforma = ""
    if com_reforma:
        seletivo = (
            """
          <IS>
            <CSTIS>000</CSTIS>
            <cClassTribIS>000001</cClassTribIS>
            <vBCIS>1000.00</vBCIS>
            <pISEspec>2.5000</pISEspec>
            <uTrib>LT</uTrib>
            <qTrib>200.0000</qTrib>
            <vIS>500.00</vIS>
          </IS>"""
            if is_especifico
            else """
          <IS>
            <CSTIS>000</CSTIS>
            <cClassTribIS>000001</cClassTribIS>
            <vBCIS>1000.00</vBCIS>
            <pIS>1.0000</pIS>
            <vIS>10.00</vIS>
          </IS>"""
        )

        # Redução, diferimento e devolução existem UMA VEZ POR DESTINAÇÃO, e
        # não uma vez por item: a NT repete os três grupos dentro de `gIBSUF`,
        # de `gIBSMun` e de `gCBS`.  Os valores são diferentes em cada um de
        # propósito — um leitor que confundisse as destinações passaria se
        # fossem iguais.
        def _beneficios(dif: str, dev: str, red: str, efet: str) -> str:
            if not beneficios:
                return ""
            return f"""
                <gDif><pDif>50.0000</pDif><vDif>{dif}</vDif></gDif>
                <gDevTrib><vDevTrib>{dev}</vDevTrib></gDevTrib>
                <gRed><pRedAliq>{red}</pRedAliq><pAliqEfet>{efet}</pAliqEfet></gRed>"""

        credito_presumido = (
            """
            <gCredPresOper>
              <cCredPres>02</cCredPres>
              <gIBSCredPres>
                <pCredPres>10.0000</pCredPres>
                <vCredPres>0.10</vCredPres>
                <vCredPresCondSus>0.04</vCredPresCondSus>
              </gIBSCredPres>
              <gCBSCredPres>
                <pCredPres>20.0000</pCredPres>
                <vCredPres>1.80</vCredPres>
                <vCredPresCondSus>0.60</vCredPresCondSus>
              </gCBSCredPres>
            </gCredPresOper>"""
            if beneficios
            else ""
        )
        reforma = f"""
          <IBSCBS>
            <CST>000</CST>
            <cClassTrib>000001</cClassTrib>
            <gIBSCBS>
              <vBC>1000.00</vBC>
              <gIBSUF>
                <pIBSUF>0.0700</pIBSUF>{_beneficios("0.35", "0.07", "10.0000", "0.0630")}
                <vIBSUF>0.70</vIBSUF>
              </gIBSUF>
              <gIBSMun>
                <pIBSMun>0.0300</pIBSMun>{_beneficios("0.15", "0.03", "20.0000", "0.0240")}
                <vIBSMun>0.30</vIBSMun>
              </gIBSMun>
              <gCBS>
                <pCBS>0.9000</pCBS>{_beneficios("4.50", "0.90", "30.0000", "0.6300")}
                <vCBS>9.00</vCBS>
              </gCBS>
            </gIBSCBS>{credito_presumido}
          </IBSCBS>{seletivo}"""

    return f"""      <det nItem="{numero}">
        <prod>
          <cProd>PROD-{numero:03d}</cProd>
          <xProd>PRODUTO DE TESTE {numero}</xProd>
          <NCM>22030000</NCM>
          <CEST>0300100</CEST>
          <CFOP>6102</CFOP>
          <uCom>UN</uCom>
          <qCom>10.0000</qCom>
          <vUnCom>100.0000000000</vUnCom>
          <vProd>1000.00</vProd>
          <vDesc>0.00</vDesc>
        </prod>
        <imposto>
          <ICMS>
            <ICMS00>
              <orig>0</orig>
              <CST>00</CST>
              <modBC>3</modBC>
              <vBC>1000.00</vBC>
              <pICMS>18.0000</pICMS>
              <vICMS>180.00</vICMS>
            </ICMS00>
          </ICMS>
          <IPI>
            <cEnq>999</cEnq>
            <IPITrib>
              <CST>50</CST>
              <vBC>1000.00</vBC>
              <pIPI>5.0000</pIPI>
              <vIPI>50.00</vIPI>
            </IPITrib>
          </IPI>
          <PIS>
            <PISAliq>
              <CST>01</CST>
              <vBC>1000.00</vBC>
              <pPIS>1.6500</pPIS>
              <vPIS>16.50</vPIS>
            </PISAliq>
          </PIS>
          <COFINS>
            <COFINSAliq>
              <CST>01</CST>
              <vBC>1000.00</vBC>
              <pCOFINS>7.6000</pCOFINS>
              <vCOFINS>76.00</vCOFINS>
            </COFINSAliq>
          </COFINS>{reforma}
        </imposto>
      </det>
"""
