# webhooks

## O que faz

Registra endpoints HTTP de terceiros e entrega eventos (`ecd.importada`,
`ecd.validada`, `relatorio.gerado`) via POST JSON, com retry por backoff
exponencial e histórico de entregas (`WebhookDelivery`) consumido pelo
dashboard. Valida a URL contra SSRF no registro e novamente no envio, assina
o payload com HMAC-SHA256 quando há secret, e oferece reenvio manual de
entregas falhas.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `EVENTOS_DISPONIVEIS` | Os 3 tipos de evento aceitos. |
| `validate_webhook_url(url, *, resolve=False)` | Valida esquema/host e bloqueia alvo local/privado; `resolve=True` resolve DNS. |
| `WebhookEvent` | Dataclass `tipo`, `dados`, `timestamp` (UTC ISO). |
| `WebhookService.registrar / listar / atualizar / remover` | CRUD de `WebhookRegistration`. |
| `WebhookService.dispatch(evento)` | Envia para os webhooks ativos inscritos, com retry; retorna `{"sucessos", "falhas"}`. |
| `WebhookService.get_deliveries / get_dashboard_stats` | Histórico e agregados. |
| `WebhookService.retry_failed(webhook_id=None)` | Reenvia um lote de entregas `failed` **e abandonadas**; devolve `restantes`. |
| `WebhookService.deliveries_abandonadas(webhook_id=None)` | Entregas sem desfecho porque o processo morreu no meio. |
| `STATUS_DESFECHO` / `STATUS_TERMINAIS` | Vocabulário de `WebhookDelivery.status`. |
| `LOTE_DE_REENVIO` | Quantas entregas um clique em "Reenviar falhas" processa. |
| `emitir(tipo, dados, *, db_path=None, aguardar=False)` | Entrada síncrona que dispara um evento. Não bloqueia e não propaga falha. |
| `BACKOFF_BASE`, `BACKOFF_MAX` | Constantes do backoff (2 s .. cap 60 s). |

## Depende de / quem depende

Depende de `db.models`, `settings` (`webhook_allow_http`); externas: httpx,
SQLAlchemy, stdlib (`ipaddress`, `socket`, `hmac`).

Consumido por `api/routes.py` (rotas `/api/v1/webhooks*`). O dashboard
consome via essas rotas, não importa o módulo diretamente.

## Decisões não óbvias e armadilhas

- **Proteção SSRF em duas etapas.** No registro, recusa IP literal não
  global (privado, loopback, link-local) e `localhost`; no envio, revalida
  com `resolve=True`, resolvendo o hostname e exigindo que **todos** os
  endereços retornados sejam públicos — cobre DNS que passa a apontar para a
  rede interna depois do registro.
- **HTTPS é obrigatório por padrão.** `http://` só com
  `SPED_HUB_WEBHOOK_ALLOW_HTTP=true` (só para desenvolvimento). URL com
  userinfo ou fragmento também é recusada.
- **A assinatura HMAC é calculada sobre uma serialização própria.**
  `X-SPED-HUB-Signature` assina `json.dumps(payload)` local, mas o corpo é
  serializado de novo pelo httpx (`json=payload`). Receptores que validam
  byte a byte contra o corpo recebido precisam saber disso.
- **Cada tentativa cria uma linha de `WebhookDelivery`** — linha é tentativa,
  não evento. Toda tentativa termina em estado terminal: `superseded` quando
  outra a seguiu, `failed` quando era a última. Antes toda falha era marcada
  `retrying` e nunca mais tocada: só a última virava `failed`, e as anteriores
  ficavam presas para sempre — não eram desfecho, não eram reenviáveis e
  ninguém as resolvia.
- **A taxa de sucesso conta desfechos, não linhas.** O denominador é
  `STATUS_DESFECHO` (`success` + `failed` + `retried`), excluindo `superseded`
  e `pending`. Contando linhas, uma entrega que só funcionou na 3ª tentativa
  valia 1 sucesso em 3 e o painel anunciava 33% para uma integração que estava
  entregando.
- **Entrega abandonada é recuperável.** A entrega roda em thread com
  `asyncio.sleep` entre as tentativas; restart, deploy ou crash no meio deixa
  a linha em `pending` (morreu no POST) ou `superseded` (morreu no backoff, e a
  próxima tentativa nunca veio). Nos dois casos o evento não chegou e não
  existe linha `failed` — o reenvio manual não via nada, e **nem manualmente**
  dava para recuperar. `deliveries_abandonadas` acha esses casos agrupando por
  `(webhook_id, request_body)`: conjunto sem desfecho cuja tentativa mais
  recente passou do limite de abandono. O limite sai do pior caso legítimo
  daquele webhook (`max_retries × timeout` + backoffs, com margem de 3× e piso
  de 5 min) — apertado demais, o reenvio dispararia sobre entrega em voo e o
  assinante receberia o evento duas vezes.
- **O lote de reenvio é limitado por tempo de requisição, não por gosto.**
  `POST /api/v1/webhooks/retry` aguarda o reenvio dentro da requisição, e cada
  entrega custa, no pior caso, todas as tentativas esgotando o timeout mais os
  backoffs (~36 s no padrão). O lote era de 100: quase uma hora de requisição
  aberta contra endpoint morto, o navegador do operador desistindo e o
  trabalho seguindo no servidor. Com `LOTE_DE_REENVIO`, o pior caso cabe em
  minutos e o retorno traz `restantes` — clicar de novo drena o resto.
- **O backoff roda com `asyncio.sleep` dentro do próprio `dispatch`** —
  endpoint lento atrasa o dispatch inteiro (envio sequencial, timeout de 10 s
  por tentativa).
- **`retry_failed` reenvia de fato**: reconstrói o `WebhookEvent` a partir do
  `request_body` persistido (preservando o timestamp original) e passa pelo
  mesmo caminho de envio — há teste garantindo que o payload chega ao envio,
  não só muda status.
- **O reenvio não põe a linha de origem em estado não terminal.** Marcá-la
  `retrying` antes de sair era o defeito reaparecendo pelo caminho do
  conserto: morte do processo durante o reenvio a congelava ali.
- **`SPED_HUB_WEBHOOK_TIMEOUT` vale no cliente HTTP de cada tentativa.**
  `SPED_HUB_WEBHOOK_DEFAULT_MAX_RETRIES` é o default **para registro novo**:
  a coluna `max_retries` é `NOT NULL` e cada registro carrega o próprio
  valor, então mudar a variável depois não reescreve o que está no banco —
  reescrever seria surpresa, não configuração.
- **`emitir` é o que faz os eventos saírem.** Três invariantes: nunca quebra
  a operação de negócio (toda exceção é logada e engolida), nunca bloqueia
  quem chamou (entrega vai para thread; o backoff chega a 60 s por
  tentativa), e custa uma consulta indexada quando não há assinante — ela
  está no caminho de toda importação. `aguardar=True` entrega no mesmo
  thread, para teste e processo que vai encerrar.
- **Os eventos saem dos pontos de convergência**, não dos chamadores:
  `ecd.importada` de `ECDImportService.importar` (depois do commit — evento
  antes do commit notificaria algo que a transação ainda pode reverter),
  `ecd.validada` de `ValidadorIntegridade.validar_todas`, `relatorio.gerado`
  de `ExportEngine.export_pdf`/`export_xlsx`. Emitir em cada chamador seria
  o mesmo fato em três lugares, e um deles esqueceria.
- **O evento leva metadado, nunca escrituração.** `relatorio.gerado` informa
  formato, nome do arquivo, empresa e período — não os saldos. Webhook sai
  para endpoint de terceiro.
- **Um registro com `eventos` ilegível não bloqueia os demais.** O
  `json.loads` sem guarda derrubava a entrega inteira no primeiro registro
  corrompido.

## Como testar isoladamente

```bash
pytest tests/test_fase10.py -k "Webhook or webhook" -q   # CRUD, eventos, rotas
pytest tests/test_fase11.py -k "Webhook or webhook" -q   # deliveries, stats, retry
pytest tests/test_review_regressions.py -k Webhook -q    # SSRF e retry real
pytest tests/test_webhooks_entregas_orfas.py -q          # estados, taxa, órfãs
pytest tests/test_migrations.py -k Reconciliacao -q      # migração dos resíduos
```

## O que não faz

- `dispatch` em si é sequencial e bloqueia quem o chama durante os retries —
  quem não quer isso usa `emitir`, que o joga para thread.
- Não garante entrega: sem assinante o evento é descartado, e falha após
  todas as tentativas fica só no histórico de `WebhookDelivery`. Não há fila
  persistente nem retry automático em segundo plano — recuperar exige o
  reenvio manual, que hoje **enxerga** tanto as `failed` quanto as
  abandonadas.
- Não reenvia sozinho: `POST /api/v1/webhooks/retry` é acionado por gente. Um
  evento perdido por queda do processo fica esperando alguém clicar.
- Não faz expurgo de `WebhookDelivery`: o histórico cresce sem limite.
- Não suporta mTLS nem validação de certificado customizada.
- Não deduplica eventos nem garante ordem de entrega.
