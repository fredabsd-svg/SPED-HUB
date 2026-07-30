# ratelimit

## O que faz

Limita requisições por janela deslizante em duas frentes independentes: por
API Key (limite configurável por chave, com fallback global) e por IP de
origem (protege o que não tem API Key — login, registro e páginas públicas).
Contagem em memória, com headers `X-RateLimit-*` na resposta e HTTP 429 ao
exceder.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `RateLimiter` / `get_limiter(db_path)` / `init_limiter(db_path)` | Limite por API Key. |
| `RateLimitInfo` | Limite, restante e instante de reset. |
| `limite_padrao()` | Cota de chave sem configuração própria, de `SPED_HUB_RATE_LIMIT_DEFAULT` / `_WINDOW`. |
| `RateLimitService` | CRUD da configuração por chave (tabela `RateLimitConfig`). |
| `IPRateLimiter` / `get_ip_limiter()` | Limite por IP. |
| `IPRateLimitInfo` | Mesma informação, para o limite por IP. |
| `ip_do_request(request)` | IP de origem, respeitando `SPED_HUB_TRUST_PROXY`. |
| `RateLimitMiddleware` | Middleware ASGI. |

## Depende de / quem depende

Depende de `src.db.models` (`RateLimitConfig`, sessão) e de `src.settings`
(limites, janelas, `trust_proxy`).

Consumido por `api` (rotas e middleware) e por `dashboard.app`.

## Decisões não óbvias e armadilhas

- **`X-Forwarded-For` só é lido com `SPED_HUB_TRUST_PROXY=true`.** Sem proxy
  na frente, quem escreve esse cabeçalho é o cliente: confiar nele sem
  condição transforma o limite por IP em decoração, porque basta trocar o
  cabeçalho a cada tentativa. Ligue apenas com o nginx do compose à frente.
- **Os dois limitadores se sobrepõem, e a ordem importa.** O middleware de IP
  roda por fora do de API Key; ele usa `headers.setdefault` para não
  sobrescrever o `X-RateLimit-Limit` que o interno já escreveu. Sem isso a
  resposta anunciava o limite errado.
- **Atenção ao NAT.** Um escritório contábil inteiro pode sair por um único IP
  público. A cota geral (`SPED_HUB_RATE_LIMIT_IP`, 300/min) precisa acomodar
  todos os usuários daquele endereço; a cota de login
  (`SPED_HUB_RATE_LIMIT_LOGIN`, 10/min) é apertada de propósito, porque é o
  que impede varredura de senhas.
- **A cota global é configurável e é lida a cada consulta.**
  `SPED_HUB_RATE_LIMIT_DEFAULT` / `SPED_HUB_RATE_LIMIT_WINDOW` valem para
  chave sem `RateLimitConfig` própria; cota gravada no banco vence a variável.
  Antes o fallback eram as constantes `DEFAULT_LIMITE`/`DEFAULT_JANELA` e as
  duas variáveis, documentadas, não tinham consumidor (§2.2). A leitura fica
  em `limite_padrao()`, não no import: o limiter global nasce com a aplicação.
- **`DEFAULT_LIMITE`/`DEFAULT_JANELA` sobraram como defaults do dataclass**
  `RateLimitInfo`, que todo caminho real sobrescreve. Não são mais a cota
  efetiva — não use como referência.
- **A contagem é em memória**: não persiste entre reinícios nem é
  compartilhada entre réplicas. Aceitável em instância única; múltiplas
  réplicas exigiriam Redis.
- O acesso ao contador é protegido por `threading.Lock` — uvicorn com mais de
  um worker de thread compartilha a estrutura.

## Como testar isoladamente

```bash
pytest tests/test_fase13.py -q       # limite por API Key, configuração por chave
pytest tests/test_hardening.py -q    # limite por IP, X-Forwarded-For, login
```

Para forçar estouro sem esperar a janela, construa `RateLimiter` com janela
curta em vez de dormir.

## O que não faz

- Não bloqueia IP permanentemente nem mantém lista de banidos.
- Não distingue rota dentro do limite por IP: a cota é do endereço.
- Não sobrevive a reinício nem coordena réplicas.
- Não substitui autenticação: limite é contenção, não controle de acesso.
