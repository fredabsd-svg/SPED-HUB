Estado: implementado
Verificado contra o código em: 2026-07-29
Fase correspondente: 17

# Importação de ECD — do upload ao banco

Este é o caminho crítico do produto: um arquivo de ECD entra e vira
escrituração consultável. Ele atravessa cinco módulos e existe em três
variantes, com garantias diferentes.

## Os três caminhos

| Caminho | Entrada | Quem executa | Bloqueia o usuário? |
|---|---|---|---|
| CLI | `sped-hub importar-ecd <arquivo>` | processo do próprio comando | sim |
| Dashboard síncrono | `POST /api/upload` | thread da requisição | sim |
| Dashboard assíncrono | `POST /api/upload-async` | thread de fundo, com job e polling | não |

Há ainda o `watchdog`, que observa um diretório e usa o mesmo serviço do CLI,
e o `worker_runner`, que consome a fila (`worker_queue`) com o handler
`handler_ecd_import`.

Os três caminhos convergem em `ECDImportService.importar(...)`. Nenhum deles
tem lógica de importação própria — divergência aí produziria bancos
diferentes conforme a porta de entrada.

## Sequência (caminho assíncrono, o mais completo)

```text
navegador
   │  multipart
   ▼
dashboard.app: api_upload_async
   │
   ├─► uploads.save_upload
   │      · sanitiza o nome (sem componente de caminho)
   │      · grava em chunks de 1 MB, cortando no limite
   │      · confere `|0000|` nos primeiros 512 bytes
   │      · calcula SHA-256 na mesma passada
   │      └─► SavedUpload(path, original_name, size_bytes, sha256)
   │
   ├─► async_jobs.criar(tipo="ecd_import")   → job.id, devolvido ao navegador
   ├─► CancelToken registrado no job
   │
   └─► thread de fundo
          └─► ECDImportService.importar(path, hash_arquivo=..., cancel_token=...)
                 │
                 ├─► parsers.ecd.ECDParser  (layouts/ecd_v9.yml, 30 registros)
                 └─► db.models              (uma transação, do início ao fim)
```

O navegador recebe `job_id` e `poll_url` imediatamente e consulta
`GET /api/jobs/{id}` até `concluido`, `falhou` ou `cancelado`.

## Invariantes

1. **Transação única (§6.1).** A importação termina inteira ou não acontece.
   Não há commit parcial, e por isso não há retomada a partir de offset: uma
   ECD pela metade não fecha balanço e nada no banco indicaria que faltam
   lançamentos.
2. **O arquivo nunca é carregado inteiro.** Upload, hash e parse são todos em
   chunks. O consumo de memória não acompanha o tamanho do arquivo.
3. **O hash atravessa a fronteira.** `save_upload` já leu o arquivo; o
   `sha256` viaja em `SavedUpload` e o importador não faz segunda passada.
4. **Deduplicação é por hash**, não por nome nem por período. Reenvio levanta
   `DuplicateECDImportError` com o `ecd_id` anterior.
5. **Cancelamento é cooperativo e não deixa resíduo.** `ECDImportCancelled`
   não é falha: a transação some junto.
6. **O temporário é sempre removido**, inclusive nos caminhos de erro
   (`finally`).

## Limites conhecidos

- O caminho assíncrono do dashboard usa `threading.Thread`, não a fila de
  workers. Reiniciar o processo web durante uma importação perde o job em
  andamento — a transação reverte, então o banco fica consistente, mas o
  usuário só descobre pelo polling.
- `SPED_HUB_UPLOAD_DIR` precisa apontar para volume compartilhado quando web e
  worker rodam em containers separados.
- Só ECD passa por aqui. EFD e ECF têm rotas de upload próprias
  (`/api/upload-efd`, `/api/upload-ecf`) que fazem parse e validação, sem
  persistir escrituração.

## Evidência

`tests/test_ecd_grande.py` (volume, ausência de flush por registro),
`tests/test_integracao.py` (fluxo completo), `tests/test_hardening.py`
(limite e assinatura no upload).

## Documentos de módulo

[`uploads`](../modules/uploads.md) · [`ecd_importer`](../modules/ecd_importer.md) ·
[`db`](../modules/db.md) · [`settings`](../modules/settings.md)
