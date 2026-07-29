# Roadmap

O que **não** existe hoje. Regra §1.1: funcionalidade futura vive aqui, nunca
na documentação técnica.

O estado do que já existe está em [`status.md`](status.md).

## Depende de decisão de produto

| Item | O que falta decidir |
|---|---|
| Exportação do balancete em PDF | Criar o template ou recusar a combinação com erro explícito |
| Retomada de importação interrompida | Como representar "escrituração incompleta" sem que os relatórios a tratem como completa |

## Depende de contrato ou credencial

| Item | Bloqueio |
|---|---|
| Deploy em servidor real com domínio e SSL | Credenciais e decisão operacional. O checklist está pronto em [`deploy.md`](deploy.md) |
| Integração com Domínio, Questor e Alterdata | Contrato e documentação das APIs |
| Migração de dados SQLite para PostgreSQL | Hoje o caminho é reimportar as ECDs. Um migrador de dados é trabalho próprio |
| Validação com ECDs reais de clientes | A importação foi exercitada com arquivos sintéticos de até 240 mil registros. Arquivos reais trazem variações de leiaute que só aparecem em campo |

## Dívida técnica conhecida

| Item | Situação |
|---|---|
| Testes de navegador no CI | Falham por defeitos de harness ainda não diagnosticados |
| Rate limiting distribuído | Em memória hoje; múltiplas réplicas exigiriam Redis |
| Documentos de módulo | O passivo e a contagem vivem em [`status.md`](status.md), em um lugar só |
