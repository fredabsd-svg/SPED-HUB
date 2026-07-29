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
| `WebhookService.retry_failed(webhook_id=None)` | Reenvia até 100 entregas `failed`. |
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
- **Cada tentativa cria uma linha de `WebhookDelivery`**, e o backoff roda
  com `asyncio.sleep` dentro do próprio `dispatch` — endpoint lento atrasa o
  dispatch inteiro (envio sequencial, timeout de 10 s por tentativa).
- **`retry_failed` reenvia de fato**: reconstrói o `WebhookEvent` a partir do
  `request_body` persistido (preservando o timestamp original) e passa pelo
  mesmo caminho de envio — há teste garantindo que o payload chega ao envio,
  não só muda status.
- **`SPED_HUB_WEBHOOK_TIMEOUT` e `SPED_HUB_WEBHOOK_DEFAULT_MAX_RETRIES` não
  têm efeito aqui**: o módulo usa timeout fixo de 10 s e 3 tentativas
  hardcoded. As variáveis existem em `settings` sem consumidor — violação da
  §2.2, registrada em `docs/status.md`.
- **Ninguém em `src/` chama `dispatch()` hoje.** O CRUD funciona e os
  eventos estão documentados, mas nenhum ponto do importador/relatórios
  dispara o envio; entregas reais só ocorrem via `retry_failed` (rota de
  retry) ou chamadas externas ao serviço. Também registrado em
  `docs/status.md`.

## Como testar isoladamente

```bash
pytest tests/test_fase10.py -k "Webhook or webhook" -q   # CRUD, eventos, rotas
pytest tests/test_fase11.py -k "Webhook or webhook" -q   # deliveries, stats, retry
pytest tests/test_review_regressions.py -k Webhook -q    # SSRF e retry real
```

## O que não faz

- Não entrega em background: `dispatch` é sequencial e bloqueia o chamador
  durante retries.
- Não dispara eventos sozinho — a emissão é responsabilidade de quem importa
  o serviço, e hoje ninguém emite.
- Não faz expurgo de `WebhookDelivery`: o histórico cresce sem limite.
- Não suporta mTLS nem validação de certificado customizada.
- Não deduplica eventos nem garante ordem de entrega.
