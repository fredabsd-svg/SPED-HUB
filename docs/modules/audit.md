# audit

## O que faz

Grava e consulta a trilha de auditoria (`AuditLog`): quem fez o quê, de onde,
com qual status HTTP. Oferece listagem com filtros e paginação, contagem,
estatísticas agregadas por janela de tempo e limpeza de logs antigos. Os
eventos (`auth.login`, `ecd.upload`, `api.acesso`, …) são registrados pelos
chamadores — dashboard e API — via `AuditService.registrar`.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `AuditService(db_path)` | Serviço principal. |
| `registrar(acao, recurso, …)` | Grava um evento; retorna o `AuditLog` criado. |
| `listar(...)` | Filtros por usuário, api_key, ação e período; `limite`/`offset`. Retorna dicts. |
| `contar(...)` | Total com os mesmos filtros (menos `api_key_id`). |
| `estatisticas(horas=24)` | Total, por ação, por status, usuários/IPs únicos, erros ≥ 400. |
| `limpar_antigos(dias=90)` | Remove logs antigos; retorna quantos apagou. |
| `get_audit_service(db_path)` / `init_audit_service(db_path)` | Singleton global; o segundo força reinicialização. |

## Depende de / quem depende

Depende de `src.db.models` (`AuditLog`, `criar_engine`, `init_db`,
`truncar_para_coluna`) e SQLAlchemy.

Consumido por `dashboard.app` (login/logout/uploads/exports e a página
`/auditoria`), `api/__init__.py` (eventos `api.rate_limited` e acessos) e
`api/routes.py`.

## Decisões não óbvias e armadilhas

- **Todo campo de texto é truncado no limite da coluna** antes do INSERT
  (`usuario_email`, `acao`, `recurso`, `metodo`, `ip`). `usuario_email` vem
  cru do formulário de login — inclusive em tentativas falhas — e `recurso`
  carrega URL. Em Postgres, estourar a coluna derrubaria a gravação, e perder
  a trilha justamente na tentativa suspeita é o pior resultado possível.
- **A docstring do módulo menciona um `AuditMiddleware` que não existe.** Não
  há middleware automático em lugar nenhum do código; a captura é manual, nos
  handlers do dashboard e em `validar_requisicao_api` da API. Não confie na
  docstring.
- **`registrar` faz rollback e re-levanta** em caso de falha (após logar).
  Chamador em caminho crítico (ex.: login) precisa decidir se engole ou não.
- **`_get_session` usa `criar_engine` + `init_db` a cada chamada** — o
  caminho caro que `auth` já abandonou (`obter_engine` + `init_db_once`).
  Cada evento de auditoria paga esse custo hoje.
- **`contar` não aceita `api_key_id`**, embora `listar` aceite: paginação
  filtrada por API Key não tem contagem correspondente.
- **`limpar_antigos` apaga linha a linha** via ORM (carrega tudo e deleta em
  loop), não com `DELETE ... WHERE`. Em volume grande, é lento.
- O singleton fixa o `db_path` da primeira chamada; testes usam
  `init_audit_service` para trocar de banco.
- Login falho também gera evento `auth.login`, com `status_code=401` — o
  registro não depende de sucesso.

## Como testar isoladamente

```bash
pytest tests/test_fase13.py -q                     # AuditService, modelo, E2E
pytest tests/test_multibackend.py -k auditoria -q  # campos gigantes sobrevivem
```

## O que não faz

- Não captura requisição automaticamente — não há middleware, apesar da
  docstring.
- Não aplica isolamento por tenant: `listar` devolve logs de todos os
  escritórios; o controle de acesso à página `/auditoria` é do dashboard.
- Não agenda a retenção: `limpar_antigos` só roda quando alguém chama.
- Não exporta nem assina os logs.
