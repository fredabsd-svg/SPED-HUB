# REGRAS DO PROJETO — SPED-HUB

Estas regras valem para **toda** IA ou pessoa que trabalhar neste repositório.
Não são sugestões: violação de regra é defeito, igual a bug de código. Em
conflito entre uma regra e a conveniência do momento, a regra vence.

Cada regra abaixo existe porque o projeto pagou por ela. Onde o motivo é um
defeito real que chegou a produção, ele está citado — regra sem motivo
declarado vira burocracia, e burocracia é a primeira coisa que se abandona
sob pressão.

## Como ler este documento

| Marca | Significado |
|---|---|
| **[CI]** | Verificado automaticamente. Quebrar derruba o pipeline. |
| **[REVISÃO]** | Cobrado na revisão de PR. A máquina não sabe verificar. |
| **[ADOÇÃO]** | Vale para o que mudar de agora em diante; o passivo existente está em `docs/status.md`. |

Uma regra sem marca não existe. Regra que a máquina não consegue cumprir e
que ninguém cobra é promessa vazia — e promessa vazia em documentação é
exatamente o defeito que a REGRA 1 existe para evitar.

Cada marca `[CI]` deste documento aparece no `REGISTRO_CI` de
`tests/test_regras_projeto.py`, apontando o teste que a cobra. Marcar uma
regra nova como `[CI]` sem implementar a verificação **derruba o pipeline** —
é o que impede este arquivo de prometer o que não cumpre. O mapa vive só lá,
para não existir a mesma informação em dois lugares (§1.9).

---

## REGRA 1 — DOCUMENTAÇÃO

### 1.1 Princípio único **[REVISÃO]**

**A documentação descreve o que o código FAZ hoje — nunca o que se pretende
que ele faça.**

Documento que descreve intenção, funcionalidade futura ou comportamento que
não existe mais é defeito da mesma gravidade de um bug em produção. Se algo
ainda não foi implementado, o lugar dele é `docs/roadmap.md` — jamais a
documentação técnica.

> Este projeto já viveu isso. Até a versão 0.15.0, o README afirmava que
> "toda a aplicação lê de `src/settings.py`". Na prática só um módulo lia, e
> das 20 variáveis documentadas apenas duas tinham consumidor. Quem seguia a
> documentação e configurava `DATABASE_URL` para PostgreSQL subia em SQLite
> sem nenhum aviso.

### 1.2 Estrutura da documentação **[CI]**

Os caminhos abaixo existem, e todo link interno da documentação resolve para
arquivo real.

```text
README.md                    ← porta de entrada honesta (§1.5)
CHANGELOG.md                 ← histórico por versão (§1.7)
REGRAS-DO-PROJETO.md         ← este arquivo
docs/
├── status.md                ← estado real por fase (§1.8)
├── roadmap.md               ← o que ainda não existe
├── decisions/               ← ADRs numerados (§1.6)
├── modules/                 ← um documento por módulo (§1.4)
├── architecture/            ← arquitetura real por área
├── deploy.md                ← checklist de produção
└── migrations.md            ← política de schema
```

### 1.3 Documentação acompanha o código — sempre no mesmo commit **[REVISÃO]**

- Toda mudança de comportamento, contrato, schema, rota, permissão ou fluxo
  atualiza a documentação afetada **no mesmo commit**. Não existe "documento
  depois".
- PR que muda comportamento e não toca em `docs/` declara na descrição:
  "Sem impacto documental — motivo". Se o motivo não convencer, o PR não
  entra.
- Renomeou, moveu ou removeu algo? Procure o nome antigo no repositório
  inteiro e corrija cada ocorrência. Referência a coisa que não existe mais
  é defeito.

> O `nginx.conf` apontava `/static/` para `/app/static/`, diretório que nunca
> existiu em nenhuma versão. Toda requisição a `/static/` respondia 404 e
> ninguém notou, porque nada verificava se o caminho era real.

### 1.4 Documento por módulo **[ADOÇÃO]** **[CI]**

Cada pacote ou módulo de topo em `src/` tem um arquivo em
`docs/modules/<nome>.md` respondendo, em uma página:

1. o que este módulo faz (2–4 frases);
2. o que ele expõe (API pública);
3. do que depende e quem depende dele;
4. decisões não óbvias e armadilhas conhecidas;
5. como testá-lo isoladamente;
6. o que ele **não** faz (limites explícitos).

**Adoção:** a regra nasceu com 6 dos 24 módulos documentados — exigir tudo
de uma vez faria o repositório nascer com 18 violações, e regra violada no
primeiro dia é regra morta. O passivo foi zerado na Fase 23; daqui em diante:

- **Módulo novo sem o documento não é entregue.** [REVISÃO]
- **Módulo existente alterado atualiza o documento no mesmo PR.** [REVISÃO]

O que o CI cobra aqui é o que ele sabe cobrar: todo módulo de `src/` aparece
em uma das duas listas do `docs/status.md` (módulo novo não passa
despercebido), todo módulo declarado documentado tem mesmo o arquivo, e todo
documento de módulo responde às seis perguntas acima. Se o documento
*descreve* o módulo de verdade, só a revisão sabe dizer.

### 1.5 README honesto **[REVISÃO]**

O README responde apenas: o que a aplicação é, o que **funciona hoje**, como
instalar, como desenvolver, onde está o resto da documentação.

Proibido: funcionalidade futura misturada com a existente, badge decorativa
que ninguém atualiza, promessa ("em breve"), contagem de testes desatualizada.
Funcionalidade só entra no README quando os testes dela passam.

### 1.6 Decisões viram ADR **[REVISÃO]**

Toda decisão estrutural gera um ADR em `docs/decisions/NNNN-titulo.md` com
quatro seções: **Contexto** (o problema), **Decisão** (o que foi decidido),
**Alternativas descartadas** (e por quê), **Consequências** (o que fica mais
fácil e o que fica mais difícil).

Conta como estrutural: troca de biblioteca central, formato de contrato,
mudança de arquitetura, política de dados, exceção a uma regra deste
documento.

- ADRs são numerados em sequência e **imutáveis**. Decisão revista gera ADR
  novo declarando "substitui o ADR NNNN"; o antigo recebe o carimbo de
  substituído e nada mais.
- Decisão estrutural não se toma em silêncio: primeiro o ADR, depois o
  código.

### 1.7 CHANGELOG disciplinado **[REVISÃO]**

Formato Keep a Changelog: Adicionado / Alterado / Corrigido / Removido /
Segurança, por versão, com data. Atualizado ao fim de **cada fase**, não só
em release.

Entrada de changelog descreve o efeito para quem usa ("importação de ECD de
8 MB caiu de 59 s para 27 s"), não o detalhe interno ("refatorado o loop do
importador").

### 1.8 Estado real por fase **[CI]**

`docs/status.md` mantém uma tabela viva: fase → estado (não iniciada / em
andamento / concluída / bloqueada) → evidência (o teste que prova) →
pendências conhecidas.

**Nada é marcado como concluído sem os testes daquela fase passando.** É o
primeiro arquivo que qualquer sessão nova deve ler depois deste.

### 1.9 Gerado vence manual **[CI]**

Tudo que puder ser derivado do código **é derivado do código**, em script
versionado — nunca mantido à mão. O documento gerado leva no topo:

```text
ARQUIVO GERADO — não edite; fonte: <caminho do script>
```

Editar arquivo gerado à mão é defeito. Se o mesmo fato vive em dois lugares,
um deles vai mentir.

### 1.10 Idioma e estilo **[REVISÃO]**

- Documentação, ADRs, changelog, mensagens de commit e descrições de PR:
  **português do Brasil**, frases curtas, voz ativa.
- Identificadores, nomes de módulo e termos técnicos consagrados ficam em
  inglês. Não traduza `commit`, `rate limit`, `flush`, `advisory lock`.
- Sem emoji, sem badge decorativa, sem tom de marketing na documentação
  técnica.

### 1.11 Mensagens de commit e PRs **[REVISÃO]**

- Primeira linha até 72 caracteres, imperativo, dizendo o efeito
  ("corrige truncamento de coluna que derrubava o login em Postgres").
- Corpo explica **por quê**, não o quê — o diff já mostra o quê.
- Proibido: `wip`, `fix`, `ajustes`, `update` como mensagem inteira; commit
  gigante misturando fases; force-push em branch compartilhada.
- Todo PR traz: o que foi implementado, decisões tomadas (com link para os
  ADRs), **testes executados com comandos e resultados reais**, limitações e
  riscos. Nunca "testes ok".

### 1.12 Estado do documento de arquitetura **[CI]**

Todo documento em `docs/architecture/` começa com:

```text
Estado: especificado | parcialmente implementado | implementado
Verificado contra o código em: AAAA-MM-DD
Fase correspondente: <fase do docs/status.md>
```

A terceira linha é o que liga o documento ao `docs/status.md` e torna a trava
do caminho inverso verificável.

- **"Especificado"** é explicitamente um plano e está isento da §1.3.
- **"Parcialmente implementado"** e **"implementado"** obedecem à §1.3
  integralmente, e o carimbo de verificação precisa ter menos de 90 dias.
- A promoção de "especificado" acontece **no mesmo commit em que a primeira
  parte do código entra**, com link para o teste que prova.
- **Trava do caminho inverso:** se `docs/status.md` marca a fase como "em
  andamento" ou "concluída" e o documento correspondente ainda diz
  "especificado", é defeito. Sem essa trava um documento escaparia da §1.3
  para sempre.

O CI nunca declara que um documento "confere com o código": isso ele não sabe
fazer. Essa verificação é item obrigatório do checklist de revisão.

---

### 1.13 Roadmap com marcador de ausência **[CI]**

Todo item de `docs/roadmap.md` declara um **marcador de ausência**: um
`módulo:símbolo` ou caminho de arquivo que só passa a existir quando o item for
feito. O CI falha se algum marcador existir.

Item bloqueado por credencial, contrato ou dado de terceiro declara `externo`
seguido da razão. Não é código que falta, então não há marcador possível — mas a
razão precisa estar escrita, porque é ela que permite reavaliar o bloqueio
depois.

A §1.1 manda a funcionalidade futura para o roadmap, e o roadmap era o único
documento sem verificação nenhuma. Ele apodrece na direção mais difícil de
notar: o item é feito e ninguém volta para tirá-lo de lá. Aconteceu **duas
vezes** — a exportação do balancete em PDF e os testes de navegador no CI
seguiram listados como ausentes depois de existirem, com teste passando e job
no pipeline. Um roadmap que lista o que já está pronto é a §1.1 ao contrário, e
igualmente enganoso: quem lê acredita que falta trabalho que não falta.

A §1.1 continua **[REVISÃO]**. "A documentação descreve o que o código faz" não
é mecanizável por inteiro; só esta direção é.

---

## REGRA 2 — CONFIGURAÇÃO

### 2.1 Ponto único **[CI]**

**Nenhum módulo fora de `src/settings.py` lê `os.environ`.**

Configuração espalhada diverge da documentação em silêncio. Foi o que
aconteceu até a 0.15.0: `src/settings.py` existia, ninguém consumia, e 33
leituras diretas de ambiente continuavam espalhadas pelo código.

### 2.2 Variável documentada é variável consumida **[CI]**

Toda variável em `.env.example` e no README tem consumidor real no código, e
todo consumidor está documentado. Variável documentada sem efeito é pior que
variável não documentada: quem a configura acredita ter configurado algo.

Exceção permitida: variável **reservada**, marcada explicitamente como tal
na documentação, com a frase "nenhum componente consome hoje".

### 2.3 Toda variável booleana passa por coerção **[REVISÃO]**

`SPED_HUB_DB_ECHO=false` já chegou a **ligar** o echo do SQLAlchemy, porque
a string `"false"` é verdadeira em Python. Campo booleano novo entra em
`_BOOL_FIELDS`.

---

## REGRA 3 — TESTES

### 3.1 O teste afirma o efeito, não a presença do código **[REVISÃO]**

`assert "compare_digest" in fonte` é fraco. `assert senha_errada não
autentica` é forte. Teste que confere se uma linha existe passa a valer nada
no dia em que a linha muda de lugar.

### 3.2 Garantia delicada é verificada por mutação **[REVISÃO]**

Antes de afirmar que um teste protege alguma coisa, reintroduza o defeito e
confirme que ele falha — com mensagem que explique a consequência, não só
"assert False".

Vale para: isolamento entre tenants, truncamento de coluna, divergência de
schema, ausência de deploy automático, e qualquer invariante contábil.

### 3.3 Otimização vem depois da medição **[REVISÃO]**

Perfile antes de mexer, e registre o número no commit. A importação de ECD
parecia limitada por memória; a medição mostrou memória constante e 20.267
flushes desnecessários. Otimizar pelo palpite teria mexido no lugar errado.

### 3.4 Portabilidade entre bancos é testada, não presumida **[CI]**

Todo comportamento que dependa do banco roda contra SQLite **e** PostgreSQL.
As divergências são silenciosas em SQLite, que é justamente o que as torna
caras:

| Divergência | O que acontecia |
|---|---|
| `LIKE` case-insensitive no SQLite, sensível no Postgres | busca por histórico devolvia 2 resultados num banco e 0 no outro |
| `String(n)` ignorado pelo SQLite, imposto pelo Postgres | `User-Agent` de 1 KB derrubava o login inteiro |

### 3.5 Teste que precisa de rede externa não bloqueia o CI **[CI]**

Teste que depende de serviço de terceiro falha por motivo alheio ao código.
Vai para marcador próprio, fora da execução padrão, e a documentação diz por
quê.

O contrário também vale: **se a dependência externa puder ser removida,
remova-a em vez de marcar o teste**. Foi o que se fez com htmx, Alpine,
Chart.js e SortableJS.

---

## REGRA 4 — DEPENDÊNCIAS E BUILD

### 4.1 Ferramenta de lint e format tem versão exata **[CI]**

`ruff` e `black` são pinados com `==`. O conjunto de regras e o estilo mudam
entre releases menores, e sem pin o pipeline passa a falhar sozinho, sem
ninguém tocar em código.

> O CI deste projeto ficou vermelho em 14 execuções seguidas por causa
> disso. Como o lint roda antes dos testes, o `pytest` nunca chegou a
> executar — e as afirmações de "372 testes 100% passando" no README nunca
> foram verificadas por ninguém.

### 4.2 Regra de lint é declarada, não herdada **[CI]**

`[tool.ruff.lint].select` é explícito. Sem ele, valem os defaults da versão
instalada, que crescem a cada release.

### 4.3 Nada é carregado de fora em tempo de execução **[CI]**

Nenhum template referencia domínio externo. Biblioteca de front-end é
servida pela própria aplicação, versionada no repositório com checksum
registrado.

> Sem acesso ao CDN — firewall corporativo, situação concreta em escritório
> contábil — o htmx não carregava, os formulários caíam para submit nativo e
> **a senha de login ia para a query string**, de onde vazava para o
> histórico do navegador, o log de acesso e o cabeçalho `Referer`.

### 4.4 Versão declarada é versão única **[CI]**

Nada de `@3`, `@4`, `latest`. Cada biblioteca aparece em uma única versão em
todo o projeto. Páginas diferentes chegaram a rodar Alpine 3.14.1 e 3.15.12
ao mesmo tempo, e isso mudava sozinho.

### 4.5 Arquivo de deploy é verificado contra o código **[CI]**

`Dockerfile`, `docker-compose.yml` e `nginx.conf` descrevem coisas que só
existem juntas — healthcheck que bate numa rota, volume que precisa casar
com um diretório, limite de upload que precisa acompanhar o da aplicação.
Nada executa esses arquivos em conjunto, então a divergência só aparece em
produção. O CI verifica.

---

## REGRA 5 — SEGURANÇA

### 5.1 Comparação de segredo é em tempo constante **[REVISÃO]**

`hmac.compare_digest`, nunca `==`. Vale para senha, token de sessão e API
key.

### 5.2 Campo que vem do cliente não confia no banco como validador **[REVISÃO]**

Cabeçalho HTTP, campo de formulário e caminho de URL têm tamanho arbitrário.
Campo de telemetria é truncado no limite da coluna; campo de negócio erra
explicitamente. Perder a trilha de auditoria justamente na tentativa
suspeita é o pior resultado possível.

### 5.3 Cabeçalho de proxy só é lido com proxy confiável na frente **[REVISÃO]**

`X-Forwarded-For` é escrito pelo cliente quando não há proxy. Confiar nele
sem condição transforma o limite por IP em decoração: basta trocar o
cabeçalho a cada tentativa.

### 5.4 Log não carrega dado pessoal **[CI]**

E-mail, CNPJ, CPF, token e API key são mascarados antes de a linha sair. A
cauda do documento é preservada, para que ainda dê para casar a linha com o
registro certo numa investigação.

### 5.5 A interface degrada com segurança **[REVISÃO]**

Formulário que envia por JavaScript declara `method="post"` e `action`. Sem
isso, o navegador cai em GET quando o script não carrega — e credencial vai
para a URL.

---

## REGRA 6 — DADOS CONTÁBEIS

### 6.1 Escrituração parcial não existe **[REVISÃO]**

Importação interrompida reverte a transação inteira. Uma ECD pela metade é
pior que nenhuma: o balanço não fecha e nada indica que faltam lançamentos.

### 6.2 Schema de produção é versionado por migração **[CI]**

Em PostgreSQL, `alembic upgrade head`. `create_all` cria o que falta e não
faz mais nada — não altera tipo, não renomeia, não remove. Em produção o
schema diverge dos modelos em silêncio.

A migração precisa produzir schema **idêntico** ao dos modelos, coluna a
coluna e índice a índice.

### 6.3 Número contábil não depende do banco **[CI]**

Balancete, balanço, DRE, DFC e razão produzem o mesmo resultado em qualquer
backend suportado.

---

## REGRA 7 — DEFINIÇÃO DE PRONTO

### 7.1 Fase concluída é fase alcançável pela porta de entrada **[CI]**

Uma fase só pode ser marcada como `concluída` em `docs/status.md` quando pelo
menos um dos testes citados como evidência **alcançar a capacidade pela porta
de entrada real** — a linha de comando (`src.cli`), a tela (`TestClient`) ou o
navegador (`page.goto`).

Alcançar é **chamar**: o teste executa `main([...])` ou faz a requisição pela
aplicação montada. Importar o módulo da porta não conta, e chamar a função da
rota direto — `asyncio.run` na corrotina do handler — conta menos ainda: pula
o roteamento, a sessão e o escopo multi-tenant, que é justamente onde mora o
defeito que só aparece em produção.

Suíte de módulo verde é condição necessária, nunca suficiente. O defeito que
esta regra evita não é teórico: um módulo pode ter cem testes, cobertura
completa e nenhum caminho do produto que o alcance. Os testes passam, a fase
é dada por pronta, e o que o usuário instala não faz aquilo. A §1.8 já exigia
que o teste citado existisse; existir não é alcançar.

**Exceção declarada.** Nem toda fase entrega capacidade ao usuário: há fases
que entregam garantia interna — uma regra verificada, uma migração, uma trava
de configuração. Para essas, a evidência traz a marca `[interno: motivo]` na
mesma célula:

```text
| 30 | Roadmap com marcador | concluída | `tests/x.py` [interno: a garantia é do repositório, não do produto] | … |
```

O motivo é obrigatório e o CI cobra que ele exista. Uma marca vazia seria um
carimbo, e carimbo é o que se aplica sem pensar; escrever por que aquela fase
não tem porta obriga a olhar se ela realmente não tem. Fase sem teste de porta
e sem a marca derruba o pipeline — que é o que impede a exceção de virar o
caminho fácil para todas.

### 7.2 Todo módulo é alcançável a partir de uma porta de entrada **[CI]**

Nenhum módulo de `src/` fica órfão: cada um é alcançado, por importação
transitiva, a partir de uma porta de entrada.

A lista de portas é **derivada**, não mantida à mão (§1.9): são os
`[project.scripts]` do `pyproject.toml` — o que o `pip install` põe no `PATH`
— mais os módulos com `if __name__ == "__main__"`, que é como o Dockerfile
sobe o worker e o watchdog. Mantida à mão, a lista viraria o lugar onde se
acrescenta o módulo órfão para calar o teste.

Módulo que só os testes alcançam é código que o produto não executa —
mantido, revisado e documentado como se fosse parte do sistema, sem ser. Para
um módulo novo virar porta, ele ganha um `[project.scripts]` ou um
`__main__`: a declaração é uma forma de rodá-lo, não uma linha numa lista.

---

## Exceções

Exceção a qualquer regra acima exige ADR (§1.6) declarando qual regra,
por quê, e por quanto tempo. Exceção sem ADR é violação.

---

*Regras novas entram como REGRA 8, REGRA 9, … neste mesmo arquivo.*
