# logging_config

## O que faz

Configura o logging da aplicação e mascara dado pessoal antes de a linha sair.
Oferece dois formatos: texto legível (padrão) e uma linha JSON por evento
(`SPED_HUB_LOG_JSON=true`), para coletor de logs. O saneamento vale nos dois.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `configurar_logging(forcar_json=None)` | Instala handler, formato e filtro. Chamado no start de cada entrypoint. |
| `sanitizar(texto)` | Mascara e-mail, CNPJ, CPF, token e API key numa string. |
| `FiltroPII` | `logging.Filter` que aplica `sanitizar` na mensagem e nos args. |
| `FormatadorJSON` | Uma linha JSON por registro. |

## Depende de / quem depende

Depende de `src.settings` (`log_level`, `log_json`).

Chamado por `cli`, `watchdog`, `worker_runner` e `dashboard.app` — os quatro
entrypoints. Módulos de biblioteca só usam `logging.getLogger`, nunca
configuram.

## Decisões não óbvias e armadilhas

- **A raiz do documento é mascarada, a cauda é preservada.**
  `12.345.678/0001-95` vira `**.***.***/0001-95`. Mascarar tudo tornaria o log
  inútil para investigação; preservar tudo é vazamento. A cauda basta para
  casar a linha com o registro certo.
- **`configurar_logging` só remove os handlers que ele mesmo instalou**,
  marcados com o atributo `_sped_hub`. A versão anterior limpava a raiz
  inteira e derrubava junto o handler de captura do pytest — dez testes
  falhavam por motivo que não tinha nada a ver com o código deles.
- **O filtro age nos `args`, não só na mensagem formatada.** `logger.info("%s
  entrou", email)` guarda o e-mail em `record.args`; filtrar só o texto final
  deixaria passar quem formata tarde.
- **Token é reconhecido por forma** (32+ hex) e API key por prefixo (`spd_`).
  Segredo em formato novo precisa de padrão novo — não há detecção genérica.
- O saneamento é regex sobre o domínio deste projeto (documento brasileiro,
  e-mail, os dois formatos de segredo). Não é DLP.

## Como testar isoladamente

```bash
pytest tests/test_hardening.py -q -k pii
```

`sanitizar` é função pura: teste dela sem tocar em `logging`. Para o filtro,
use `caplog` — ele continua funcionando justamente por causa da marcação
`_sped_hub`.

## O que não faz

- Não envia log para lugar nenhum: escreve em `stderr` e o resto é do
  ambiente (Docker, systemd, coletor).
- Não faz rotação de arquivo nem amostragem.
- Não mascara o que a aplicação grava no banco — só o que vai para o log.
- Não impede que alguém logue um segredo de formato desconhecido.
