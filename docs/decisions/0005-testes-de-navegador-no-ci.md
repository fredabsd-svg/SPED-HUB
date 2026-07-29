# ADR 0005 — Testes de navegador passam a bloquear o CI

Complementa o [ADR 0004](0004-testes-de-navegador-opt-in.md), que segue
válido como registro histórico. O que muda é o motivo: a causa que o 0004
declarava "não diagnosticada" foi encontrada.

## Contexto

O ADR 0004 tirou os testes de navegador da execução padrão porque falhavam
por dois motivos: locators ambíguos e "um servidor de teste que para de
responder no meio da suíte, por causa ainda não diagnosticada".

A causa não era do harness. `DashboardService.get_composicao_ativo` subia a
hierarquia do plano de contas por `COD_CTA_SUP` **sem trava de ciclo**:

```python
while pc and pc.nivel > 2 and pc.cod_cta_sup:
    pc = plano.get(pc.cod_cta_sup)
```

Com uma conta que é a própria sintética — ou `A→B→A` — o laço nunca termina.
O uvicorn atende num único event loop, então o processo inteiro parava de
responder: **o dashboard caía para todos os usuários do escritório**, não só
para quem importou, e só voltava com reinício manual. A hierarquia vem do
arquivo do cliente, o que torna isso negação de serviço por dado de entrada.

O diagnóstico só foi possível depois de consertar o harness. A saída do
servidor de teste ia para `DEVNULL`: sem log, o sintoma era "para de
responder" e nada mais. Com o log, ficou visível que o `POST /api/upload`
respondia 200 e o `GET /` seguinte nunca era registrado — e o uvicorn só
registra a requisição quando ela termina. O stack veio de um `SIGABRT` no
processo travado.

Corrigido o defeito e o harness, a suíte passa 10 de 10 em 14 s. Antes eram
4 de 10 em 122 s, com o tempo dominado por timeouts de 30 s.

## Decisão

Os testes de navegador voltam para o CI, em job próprio que **bloqueia** o
merge. O marcador `e2e` continua existindo e continua fora do `pytest`
padrão, para quem roda a suíte localmente sem Chromium.

O job falha explicitamente se os testes forem **pulados** em vez de
executados. Job que pula tudo e reporta verde é pior que job ausente: ele
afirma cobertura que não houve.

## Alternativas descartadas

**Mantê-los opt-in.** Era a decisão do ADR 0004, e a justificativa dela era
"defeitos de harness ainda não diagnosticados". A justificativa deixou de
existir. Regra que perde o motivo e continua valendo vira burocracia.

**Job não bloqueante.** Verificação que ninguém cobra é promessa vazia — o
mesmo defeito que a REGRA 1 existe para evitar. Ou bloqueia, ou não está lá.

**Rodá-los apenas no `main`.** Foi exatamente o que permitiu o build Docker
quebrado chegar ao `main` (ADR do PR #6): verificação depois do merge não
barra ninguém.

## Consequências

**Mais fácil:** regressão de interface passa a ser barrada. Os dois defeitos
de produto que este tier já encontrou — redirect de login ausente e senha na
query string — teriam sido pegos no PR que os introduziu.

**Mais difícil:** o CI passa a depender de navegador no runner. Se o
`google-chrome` sumir da imagem do GitHub, o job falha por motivo alheio ao
código — e, pela regra do parágrafo acima, falha ruidosamente em vez de
pular em silêncio. É o comportamento desejado, mas exige atenção.

**Risco assumido:** teste de navegador é mais sujeito a intermitência que
teste de unidade. Se aparecer intermitência real, a resposta certa é
diagnosticar — como foi aqui —, não voltar a esconder o tier. Se for
necessário reverter, isso exige um ADR novo dizendo por quê.
