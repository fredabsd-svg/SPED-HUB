# email_service

## O que faz

Envia notificações por email (alertas de job, relatórios agendados,
mensagens avulsas) via SMTP real ou, em desenvolvimento, apenas logando
("modo log"). Detecta o modo automaticamente pela presença de credenciais
SMTP, mantém um histórico em memória dos últimos 100 envios (sucessos e
falhas) e expõe estatísticas consumidas pelo painel de monitoramento. O
envio padrão é assíncrono, em thread daemon.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `EmailMessage` | Dataclass com destinatário, corpos, cc, anexos, `status` (`pendente`/`enviado`/`falha`) e `erro`. |
| `EmailService(modo="auto")` | Serviço; modos `"auto"`, `"smtp"`, `"log"`. |
| `enviar(...)` | Envio genérico; `async_mode=True` por default. |
| `enviar_alerta_job_concluido` / `enviar_alerta_job_falhou` / `enviar_relatorio_agendado` | Templates prontos. |
| `historico(limite=20)` / `stats()` | Histórico e agregados. |
| `init_email_service(modo)` / `get_email_service()` | Singleton global. |

## Depende de / quem depende

Depende de `settings` (`smtp_*`, `email_from`) e stdlib (`smtplib`,
`email.mime`, `threading`).

Consumido por `dashboard.app` (rotas `/api/email/*`) e `monitoring`
(snapshot inclui `stats()`).

## Decisões não óbvias e armadilhas

- **`async_mode=True` é o default e devolve o objeto antes do envio.** O
  `EmailMessage` retornado nasce `"pendente"` e é mutado pela thread daemon
  depois. Testes e callbacks que conferem `historico()` logo em seguida
  precisam de `async_mode=False` — a assertiva corria contra a thread de
  envio e perdia sob carga.
- **Falha também entra no histórico**, com `status="falha"` e o texto do
  erro; `stats()` conta `falhas` separado.
- **Aliases legados de ambiente.** O código chegou a ler `SMTP_PASS` e
  `SMTP_FROM` direto do ambiente enquanto a documentação dizia
  `SMTP_PASSWORD`/`EMAIL_FROM` — quem seguia o doc ficava sem senha e sem
  remetente. Hoje a leitura passa por `settings`, que aceita os dois nomes
  com precedência do documentado.
- **Nome de anexo é sanitizado com `Path(...).name`** — `"../relatorio.pdf"`
  vira `"relatorio.pdf"` no MIME. `dados` que não sejam `bytes` levantam
  `TypeError` antes de montar a mensagem.
- **Modo `"auto"` só vira `"smtp"` com usuário E senha configurados**; caso
  contrário cai em `"log"`. **A flag `EMAIL_ENABLED` existe em settings e
  não é lida por este módulo** — violação da §2.2, registrada em
  `docs/status.md`.
- Histórico em memória com `threading.Lock` e teto de 100 — some no restart
  e não é compartilhado entre processos.

## Como testar isoladamente

```bash
pytest tests/test_fase15.py -k "EmailService or email" -q   # modo log, templates, async
pytest tests/test_review_regressions.py -k email -q         # falha registrada, anexos
```

## O que não faz

- Não persiste emails nem fila: sem retry, sem outbox — falhou, só fica o
  registro em memória.
- Não valida endereço de destinatário nem faz rate limiting de envio.
- Não usa SMTP_SSL (porta 465); só STARTTLS opcional.
- Não renderiza templates HTML: os "templates" são texto plano.
- Não lê `EMAIL_ENABLED`: desligar envio de verdade exige não configurar
  credenciais (modo log) ou `modo="log"`.
