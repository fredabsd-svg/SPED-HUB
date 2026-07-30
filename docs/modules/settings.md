# settings

## O que faz

Lê a configuração de ambiente uma única vez e entrega um objeto `Settings`
imutável ao resto da aplicação. É o único lugar do projeto autorizado a tocar
`os.environ` (REGRA §2.1). Faz coerção de tipo (booleano, inteiro), resolve
aliases legados e normaliza caminho de SQLite para URL SQLAlchemy.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `get_settings(environ=None)` | Ponto de entrada. Resultado cacheado por conteúdo do ambiente. |
| `Settings` | Dataclass congelada com os campos de configuração. |
| `reset_settings_cache()` | Invalida o cache. Usado por fixtures de teste. |
| `with_overrides(**kwargs)` | Cópia com campos trocados, sem mexer no ambiente. |
| `database_reference()` | URL do banco, para quem só precisa dela. |
| `caminho_para_url_sqlite(caminho)` | `/var/lib/x.db` → `sqlite:////var/lib/x.db`. |
| `PROJECT_ROOT` | Raiz do repositório. |

Propriedades derivadas em `Settings`: `max_upload_bytes` (resolve MB versus
override legado em bytes) e `redis_url_or_local`.

## Depende de / quem depende

Depende apenas de `src.version`. É o módulo mais na base do grafo — não pode
importar nada que importe configuração, sob pena de ciclo.

Consumido por: `db.models`, `db.migrations`, `logging_config`, `uploads`,
`ecd_importer`, `ratelimit`, `email_service`, `webhooks`, `async_jobs`,
`worker_queue`, `worker_runner`, `api` e `dashboard.app`.

## Decisões não óbvias e armadilhas

- **O cache tem chave de conteúdo, não de identidade.** `_cache_key` deriva do
  valor das variáveis relevantes. Mudar o ambiente e chamar `get_settings()`
  de novo devolve configuração nova sem precisar de `reset_settings_cache()`.
- **Booleano precisa entrar em `_BOOL_FIELDS`.** A string `"false"` é
  verdadeira em Python; `SPED_HUB_DB_ECHO=false` chegou a **ligar** o echo do
  SQLAlchemy. Campo booleano novo sem entrada na lista repete o defeito.
- **`DATABASE_URL` vence `SPED_HUB_DB`.** O segundo é legado (Fase 16 e
  anteriores) e só preenche o primeiro quando ele não existe.
- **Caminho absoluto de SQLite precisa de quatro barras.**
  `sqlite:///` + `/var/lib/x.db` produz `sqlite:////var/lib/x.db`. Concatenar
  com três gera um caminho relativo silenciosamente errado.
- **Aliases legados** (`SMTP_PASS` → `SMTP_PASSWORD`, `SMTP_FROM` →
  `EMAIL_FROM`) existem porque o `docker-compose.yml` sempre passou os nomes
  antigos.
- **Campo aqui não é o mesmo que configuração com efeito.** Chegar ao
  `Settings` e ninguém ler é a §2.2 vista de outro ângulo, e foi assim que
  sete variáveis documentadas passaram meses sem mudar comportamento nenhum.
  `tests/test_regras_projeto.py::TestConfiguracao` cobra as duas metades (há
  leitor? — por AST, contando `@property` como leitor indireto) e
  `tests/test_config_com_efeito.py` cobra o efeito observável.
- `SPED_HUB_SECRET_KEY` é a **única** reservada: é lida e não tem consumidor,
  e está marcada como tal em `.env.example`. Sessões e tokens usam CSPRNG; o
  webhook assina com o segredo do próprio registro. `SPED_HUB_DEBUG` era a
  outra e foi **removida** — knob que não faz nada não deveria existir.
- **Quem lê configuração num objeto de vida longa lê no uso, não no
  `__init__`.** `metrics_collector` e o limiter global nascem no import;
  resolver settings ali congela o valor para o processo inteiro (é o defeito
  que o `worker_runner` carrega). Por isso `MetricsCollector.retention_hours`
  é `@property` e `ratelimit.limite_padrao()` é função.

## Como testar isoladamente

```bash
pytest tests/test_settings.py -q
```

Não use `monkeypatch.setattr` no módulo: passe o ambiente por argumento
(`get_settings({...})`) ou use `monkeypatch.setenv` e deixe o cache trabalhar.

## O que não faz

- Não lê arquivo `.env` — quem carrega é o Docker, o systemd ou o shell.
- Não valida se o banco existe, se o SMTP responde ou se o Redis está de pé.
- Não guarda estado de runtime: `Settings` é congelada e descartável.
