# Bibliotecas de front-end versionadas no repositório

Estes arquivos eram carregados do `cdn.jsdelivr.net` em tempo de execução.
Três problemas com isso:

1. **A aplicação quebrava em silêncio sem o CDN.**  Firewall corporativo
   bloqueando jsdelivr — situação real em escritório contábil — fazia o htmx
   não carregar, e aí os formulários caíam para submit nativo.  Foi assim que
   a senha de login acabava na query string (corrigido em `f2e6806`).
2. **Versões divergiam entre páginas.**  Só o `base.html` pinava; as demais
   usavam `@3`, `@4`, `@1`, que resolvem para a última versão do major a cada
   carregamento.  Na prática o dashboard rodava Alpine 3.14.1 e a página de
   webhooks, 3.15.12 — sem nenhuma alteração de código.
3. **Os testes de navegador dependiam de rede externa** e por isso ficavam
   fora do CI.

## Versões

| Arquivo | Origem |
|---|---|
| `htmx-1.9.12.min.js` | https://cdn.jsdelivr.net/npm/htmx.org@1.9.12/dist/htmx.min.js |
| `alpine-3.14.1.min.js` | https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js |
| `chart-4.4.4.umd.min.js` | https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js |
| `sortable-1.15.7.min.js` | https://cdn.jsdelivr.net/npm/sortablejs@1.15.7/Sortable.min.js |

As três primeiras são exatamente as que o `base.html` já declarava — a
mudança uniformiza as demais páginas, não altera o dashboard.  O SortableJS
nunca teve versão declarada em lugar nenhum; ficou no último 1.x.

## Atualizando

Baixe a versão nova, atualize o nome do arquivo nos templates e regenere o
`SHA256SUMS`:

```bash
cd src/dashboard/static/vendor
curl -sSfO https://cdn.jsdelivr.net/npm/htmx.org@X.Y.Z/dist/htmx.min.js
sha256sum *.js > SHA256SUMS
```

`tests/test_vendor_assets.py` confere que os arquivos batem com o
`SHA256SUMS` e que todo `<script src>` dos templates aponta para um arquivo
que existe — nome trocado sem arquivo correspondente quebra o teste, em vez
de virar 404 em produção.
