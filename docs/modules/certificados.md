# certificados

## O que faz

Guarda o certificado digital A1 de uma empresa — o PFX e a senha — cifrados, e
lê os metadados públicos do certificado sem expor nada.

É o dado mais sensível que este sistema chega a tocar: com o PFX e a senha,
quem os tiver assina documento fiscal como a empresa.

## O que expõe

| Símbolo | Para quê |
|---|---|
| `guardar(segredo)` | Cifra com a chave mestra atual e devolve um `Envelope`. |
| `abrir(envelope)` | Decifra — só para o momento da operação que precisa do segredo. |
| `Envelope` | Versão, nonce e texto cifrado; `serializar()` cabe numa coluna de texto. |
| `ler(pfx, senha)` | Metadados públicos do PKCS#12, sem guardar nada. |
| `DadosDoCertificado` | Titular, emissor, validade, fingerprint e CNPJ. |
| `DadosDoCertificado.aviso_de_vencimento()` | A frase a mostrar, ou `None` quando há folga. |
| `CofreIndisponivel`, `EnvelopeInvalido`, `CertificadoIlegivel` | Os três modos de falha, separados. |

## Decisões não óbvias e armadilhas

**A chave mestra fica fora do banco** (`SPED_HUB_CERTIFICATE_MASTER_KEY`).
Cifrar o PFX com uma chave guardada ao lado dele protege contra quase nada:
quem lê o banco lê os dois.

**Sem a chave, o cofre recusa operar** — não cai numa chave padrão. Cifra com
chave previsível dá a aparência de proteção sem a proteção, e aparência é pior
do que nada, porque ninguém vai atrás. A mensagem de erro diz como gerar a
chave, e isso é testado: erro que não diz o que fazer vira chamado de suporte.

**PFX e senha vão em envelopes separados.** São dois segredos com vidas
diferentes — a senha muda sem o arquivo mudar — e um vazamento parcial não
entrega o par.

**O envelope carrega a versão da chave.** Sem isso, rotação de chave mestra é
migrar tudo de uma vez, com janela de indisponibilidade.

**O nonce é sorteado por operação.** Dois nonces iguais no AES-GCM quebram a
cifra; há um teste que guarda o mesmo segredo duas vezes e exige envelopes
diferentes.

**`ler` não exige o cofre.** Conferir um PFX que o usuário acabou de mandar é
leitura, e travar isso por falta de chave mestra impediria até de dizer "essa
senha está errada". Testado com a variável vazia.

**A biblioteca `cryptography` mudou o nome dos campos de validade na versão
42** (`not_valid_before` → `not_valid_before_utc`). O `pyproject` não fixa a
versão, então `_data_de` aceita as duas em vez de escolher uma e quebrar na
outra.

## Como testar isoladamente

```
python -m pytest tests/test_certificados.py
```

O PFX vem de `tests/fixtures_certificado.py`, gerado na hora — **não há
certificado real neste repositório e não deve haver**. O caminho de ponta a
ponta está em `tests/test_portas_de_entrada.py::TestCofreDeCertificadosPelaCLI`,
e a asserção que dá sentido às outras é `test_o_pfx_e_a_senha_nao_ficam_
legiveis_no_banco`: lê o arquivo do SQLite em bytes e exige que nem o PFX nem
a senha estejam lá.

## Depende de / quem depende

Depende de `cryptography` (AES-GCM e PKCS#12) e de `src.settings`.

Usado por `cli` (`sped-hub certificado guardar|listar`). O modelo
`CertificadoDigital` vive em `src.db.models`.

## O que não faz

**Não decifra sozinho em lugar nenhum do fluxo normal.** `abrir` existe para o
momento da operação, e quem chama descarta o resultado.

**Não escreve o PFX em disco.** Quando a consulta a webservice existir, o
arquivo temporário e a sua remoção segura são responsabilidade de quem
consulta — e ainda não há esse caminho.

**Não rotaciona a chave mestra.** O envelope está preparado (carrega a versão),
mas o comando que relê tudo com a chave nova não existe.

**Não conecta a webservice nenhum.** Este módulo é a metade guardada do
problema; a consulta automática depende de biblioteca comercial e certificado
autorizado, e está no roadmap como bloqueio externo.
