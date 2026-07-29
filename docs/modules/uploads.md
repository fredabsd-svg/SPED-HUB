# uploads

## O que faz

Recebe arquivo enviado pelo dashboard e grava em arquivo temporário sem
confiar em nada que veio do cliente: sanitiza o nome, impõe o limite de
tamanho durante a leitura e confere a assinatura do conteúdo antes de aceitar.
Calcula o SHA-256 na mesma passada, para o importador não precisar de outra.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `SavedUpload` | Dataclass congelada: `path`, `original_name`, `size_bytes`, `sha256`. |
| `safe_original_name(filename)` | Remove componentes de caminho; recusa vazio, `.` e `..`. |
| `upload_directory()` | Diretório de temporários, criado se faltar. |
| `max_upload_bytes()` | Limite efetivo em bytes. |

Erros saem como `HTTPException` (400 para nome ou conteúdo inválido, 413 para
excesso de tamanho).

## Depende de / quem depende

Depende de `fastapi` (`UploadFile`, `HTTPException`) e de `src.settings`.

Consumido só por `dashboard.app`. O caminho de CLI e watchdog lê do disco
direto e não passa por aqui.

## Decisões não óbvias e armadilhas

- **A assinatura é conferida nos primeiros 512 bytes**, procurando `|0000|` —
  o registro de abertura do SPED. Extensão não diz nada sobre conteúdo:
  qualquer arquivo renomeado para `.txt` passava, era gravado em disco e só
  quebrava adiante no parser, depois de já ter consumido o limite inteiro de
  upload.
- **O limite é imposto durante a leitura**, em chunks de 1 MB, não depois.
  Checar `size` no fim significa ter escrito o arquivo inteiro primeiro.
- **`SPED_HUB_MAX_UPLOAD_MB` é o valor documentado**;
  `SPED_HUB_MAX_UPLOAD_BYTES` é override legado e vence quando presente. Antes
  só o segundo era lido — a variável documentada não tinha efeito nenhum.
- **`SPED_HUB_UPLOAD_DIR` precisa ser volume compartilhado no Docker.** O
  container web grava e o worker lê; em volumes separados o worker encontra
  arquivo inexistente.
- O limite da aplicação precisa acompanhar o `client_max_body_size` do nginx —
  divergência aí devolve 413 do proxy, sem passar pela aplicação
  (`tests/test_deploy_config.py`).

## Como testar isoladamente

```bash
pytest tests/test_hardening.py -q -k upload
```

Sem `TestClient`: `safe_original_name` e `max_upload_bytes` são funções puras
e podem ser chamadas direto.

## O que não faz

- Não faz parse nem valida layout — só confirma que o começo parece SPED.
- Não remove o temporário: quem consome é responsável pela limpeza.
- Não faz antivírus, nem impõe cota por usuário ou por escritório.
- Não guarda o arquivo permanentemente: o destino final é o banco.
