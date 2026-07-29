# version

## O que faz

Guarda a versão única da aplicação: uma constante, `APP_VERSION`, sem nenhuma
lógica. Existe para que dashboard, API, monitoring e settings relatem o mesmo
número sem import circular — é o único módulo abaixo de `settings` no grafo.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `APP_VERSION` | String da versão (ex.: `"0.16.1"`). |

## Depende de / quem depende

Não importa nada — nem stdlib.

Consumido por: `settings` (campo `app_version`), `dashboard.app` (versão do
FastAPI, global do Jinja e resposta do `/health`), `api.routes` (info da API
v1) e `monitoring` (snapshot operacional).

## Decisões não óbvias e armadilhas

- **A versão existe em dois lugares e precisa de bump conjunto:**
  `src/version.py` e `pyproject.toml`. Não há teste comparando os dois entre
  si; o que existe é `test_versao_do_projeto_bate_com_o_status`, que compara o
  pyproject com `docs/status.md`. Bump de release toca três arquivos.
- **O release workflow confere a tag do Git contra `APP_VERSION`** e falha se
  divergirem — imagem publicada como `vX.Y.Z` tem de relatar `X.Y.Z` em
  `/health`. `tests/test_deploy_config.py` garante que essa verificação não
  suma do workflow.
- `settings` importa `version`, nunca o contrário — import novo aqui arrisca
  ciclo na base do grafo.
- Vários testes E2E afirmam que endpoints respondem exatamente `APP_VERSION`;
  mudar o formato da string quebra essas asserções.

## Como testar isoladamente

```bash
pytest tests/test_settings.py tests/test_deploy_config.py -q
```

Não existe `tests/test_version.py`; as asserções vivem nesses arquivos e nos
E2E de `tests/test_fase13.py` e `tests/test_fase16.py`.

## O que não faz

- Não lê versão de lugar nenhum (nem do pyproject, nem do ambiente) — é
  fixa no código.
- Não faz parse semântico nem comparação de versões.
- Não se sincroniza sozinho com `pyproject.toml` ou `docs/status.md`.
