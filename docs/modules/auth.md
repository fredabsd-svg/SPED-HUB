# auth

## O que faz

Autentica usuários do dashboard: registro, login com sessão de 24 horas em
cookie ou header Bearer, logout e validação de token. Mantém o contexto
multi-tenant num `contextvars.ContextVar` por request e oferece helpers para
restringir queries ao escritório do usuário. O hash de senha em si vive em
`db.models` (`Usuario.hash_senha` / `verificar_senha`); aqui fica a
orquestração.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `AuthService` | `registrar`, `login`, `logout`, `validar_token`, `get_empresas_usuario`. |
| `init_auth(db_path)` / `get_auth()` | Singleton do serviço, inicializado no app. |
| `get_usuario_atual(request)` | Extrai usuário do cookie `sped_hub_session` ou de `Authorization: Bearer`. |
| `aplicar_escopo_empresas(stmt, usuario)` | `WHERE escritorio_id = ?` (admin passa sem filtro). |
| `usuario_pode_acessar_ecd(session, usuario, ecd_id)` | Confirma acesso a uma ECD sem revelar existência entre tenants. |
| `get_tenant_id()` / `set_tenant_id(id)` | Contexto de tenant via contextvars. |
| `TenantFilter` / `MultiTenantMiddleware` / `requer_autenticacao` | Ver armadilhas: sem consumidor em produção. |
| `SESSAO_DURACAO_HORAS` | Constante: 24. |

## Depende de / quem depende

Depende de `src.db.models` (`Usuario`, `Sessao`, `UsuarioEmpresa`,
`obter_engine`, `init_db_once`, `truncar_para_coluna`), FastAPI e SQLAlchemy.

Consumido por `dashboard.app` (`aplicar_escopo_empresas`, `get_auth`,
`get_usuario_atual`, `init_auth`, `usuario_pode_acessar_ecd`) e por
`api/__init__.py` (`get_usuario_atual`, para sessões de admin consumirem a
API externa).

## Decisões não óbvias e armadilhas

- **Comparação de senha em tempo constante.** `Usuario.verificar_senha` usa
  `hmac.compare_digest` porque `==` entre strings sai no primeiro byte
  diferente e vaza por timing quantos caracteres do hash foram acertados.
  `verificar_api_key` já fazia certo; a senha tinha ficado de fora.
- **`ip` e `user_agent` são truncados no limite da coluna** antes de gravar a
  `Sessao`. Vêm crus da requisição; em Postgres, um User-Agent acima de 512
  chars abortava o login inteiro (SQLite ignora `String(n)` e mascarava o
  defeito).
- **`_get_session` usa `obter_engine` + `init_db_once`**, não
  `criar_engine` + `create_all`: o método roda em todo request autenticado e a
  versão sem cache custava ~3 ms por request só para validar um token.
- **`login` retorna 4 valores** (`usuario, token, usuario_id, usuario_email`),
  embora a anotação diga `tuple[Usuario, str]`. Os dois extras existem porque
  o `Usuario` fica detached após `session.close()`; o dashboard desempacota os
  quatro para registrar auditoria sem tocar no objeto detached.
- **O primeiro usuário registrado vira admin**, e o construtor de
  `AuthService` promove o usuário mais antigo em instalações legadas sem
  nenhum admin.
- **`TenantFilter`, `MultiTenantMiddleware` e `requer_autenticacao` não têm
  consumidor em `src/`** — só os testes de Fase 12 os exercitam. O isolamento
  em produção passa por `aplicar_escopo_empresas` e `usuario_pode_acessar_ecd`
  no dashboard. Não assuma que registrar rota nova ganha isolamento de graça.
- **`_resolve_tenant` engole exceções** (loga e retorna `None`): falha ao
  resolver tenant vira "sem isolamento", não erro 500.
- `Sessao.expirado` normaliza datetimes naive para UTC antes de comparar.

## Como testar isoladamente

```bash
pytest tests/test_fase12.py -q          # tenant context, TenantFilter, middleware
pytest tests/test_fase13.py -k E2EAuth -q   # registro/login/logout via TestClient
pytest tests/test_multibackend.py -k login -q  # user_agent gigante sobrevive
```

## O que não faz

- Não faz hash de senha nem gera token — isso é `db.models` (`Usuario`,
  `Sessao.gerar_token`).
- Não autentica API Keys — isso é `api` (`verificar_api_key`).
- Não renova sessão: o token expira em 24 h fixas, sem refresh.
- Não registra auditoria — quem chama (dashboard/api) é que registra.
