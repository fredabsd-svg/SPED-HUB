# Deploy em produção — checklist manual

Este documento é um procedimento, não automação.  Publicar a imagem e
colocá-la em produção são passos separados de propósito: subir uma versão
depende de janela, backup do banco e migração de schema, e nada disso pode
acontecer só porque alguém criou uma tag.

O `release.yml` publica a imagem no GHCR e para por aí.

---

## 0. Antes da primeira vez

| Item | Como confirmar |
|---|---|
| DNS: domínio apontando para o servidor | `dig +short seu-dominio.com.br` devolve o IP do host |
| Portas 80 e 443 abertas | `nc -zv seu-dominio.com.br 80 443` |
| Docker e Compose v2 | `docker compose version` |
| Disco para banco e uploads | `df -h` — ECDs grandes ocupam o dobro do arquivo durante a importação |

## 1. Configuração

```bash
cp .env.example .env
```

Ajuste no mínimo:

- `DATABASE_URL` — PostgreSQL em produção.  Ver [`migrations.md`](migrations.md).
- `SPED_HUB_ALLOWED_HOSTS` — o domínio real, **não** `*`.  Requisição com
  `Host` fora da lista recebe 400.  Aceita curinga de subdomínio
  (`*.escritorio.com.br`, que cobre o domínio nu também) e lista separada por
  vírgula.  `localhost`/`127.0.0.1` seguem aceitos: é o `Host` do
  `HEALTHCHECK` do container.
- `SPED_HUB_LOG_JSON=true` — se houver coletor de logs.
- `SMTP_*` / `EMAIL_FROM` — se e-mail transacional for usado.
- `SPED_HUB_MAX_UPLOAD_MB` — precisa ser **≤** `client_max_body_size` do
  `nginx.conf`, senão o nginx corta com 413 antes de a aplicação opinar.

`SPED_HUB_TRUST_PROXY` já vem `true` no compose, porque há nginx na frente
sobrescrevendo `X-Forwarded-For`.  **Se você tirar o nginx, desligue** — sem
proxy o cabeçalho é escrito pelo cliente e o limite por IP deixa de valer.

## 2. Banco de dados

### Instalação nova

```bash
docker compose run --rm migrate
```

### Instalação existente vinda das fases 1–16

O schema já existe, criado por `create_all`, e não há tabela
`alembic_version`.  Rodar a migração direto falharia tentando criar tabelas
que já existem:

```bash
docker compose run --rm web python -m src.cli migrar adotar
docker compose run --rm web python -m src.cli migrar status   # "Schema em dia."
```

### Migrando de SQLite para PostgreSQL

```bash
# 1. crie o schema no destino
docker compose run --rm web python -m src.cli migrar aplicar --db "postgresql+psycopg://..."

# 2. copie o conteúdo (tudo ou nada; recusa destino que já tenha dados)
docker compose run --rm web python -m src.cli migrar-dados \
    --de /app/data/sped_hub.db --para "postgresql+psycopg://..."
```

A cópia preserva os identificadores, corrige as sequências do Postgres e
confere as contagens no fim.  `--conferir` compara os dois bancos sem copiar
nada.  Reimportar as ECDs também funciona, mas perde o que não vem de arquivo:
usuários, mapeamentos de conta, visões de filtro, chaves de API e auditoria.

## 3. SSL

O nginx **sobe sem certificado**.  Na ausência de um Let's Encrypt válido ele
gera um autoassinado e serve a aplicação em HTTP, sem redirecionar — é o que
permite `docker compose up` funcionar na primeira execução, e é o caminho de
quem só quer experimentar na própria máquina.

Antes, o `nginx.conf` apontava direto para o certificado do Let's Encrypt.  Numa
instalação nova o arquivo não existia, o nginx recusava subir e o container
entrava em laço de reinício — inclusive no primeiro passo desta própria seção.

O endereço do backend também passou a ser resolvido **a cada requisição**, e não
uma única vez na subida.  Antes, recriar o `web` — qualquer `docker compose up`
depois de trocar a imagem — lhe dava um IP novo que o nginx não enxergava: ele
seguia mandando para o IP antigo e devolvia 502 em tudo, com o `web` saudável ao
lado, até alguém reiniciar o nginx na mão.  Se o nginx subir sem o `web` no ar,
agora responde 502 em vez de recusar subir.

### Para valer, com domínio

Defina o domínio (o nginx procura o certificado por esse nome):

```bash
echo "SPED_HUB_DOMINIO=seu-dominio.com.br" >> .env
docker compose up -d nginx        # sobe com autoassinado e serve o desafio ACME
docker compose run --rm certbot certonly --webroot -w /var/www/certbot \
    --cert-name seu-dominio.com.br \
    -d seu-dominio.com.br --email voce@exemplo.com --agree-tos --no-eff-email
docker compose restart nginx      # agora acha o certificado real
```

O log do nginx diz qual dos dois está em uso:

```
[sped-hub] certificado Let's Encrypt encontrado para 'seu-dominio.com.br'; HTTP redireciona para HTTPS.
[sped-hub] AVISO: sem certificado para 'sped-hub'. Subindo com certificado AUTOASSINADO...
```

O `--cert-name` precisa bater com `SPED_HUB_DOMINIO`: é por esse nome que o
nginx monta o caminho `/etc/letsencrypt/live/<nome>/`.

A renovação roda sozinha no serviço `certbot`.  A **emissão inicial** não —
`certbot renew` não emite nada na primeira vez, e é por isso que o comando
acima é necessário uma vez.

## 4. Subir

```bash
docker compose pull
docker compose up -d
docker compose ps          # migrate deve estar "exited (0)", o resto "healthy"
```

`web` e `worker` só sobem depois que `migrate` termina com sucesso.

## 5. Conferir antes de liberar

```bash
curl -sf https://seu-dominio.com.br/api/v1/health          # 200
curl -si https://seu-dominio.com.br/ | grep -i content-security-policy
curl -so /dev/null -w '%{http_code}\n' https://seu-dominio.com.br/api/ecds   # 401
```

O terceiro é intencional: a API interna **tem** que recusar sem sessão.  Se
responder 200, a autenticação não está ativa.

Verifique também o rate limit de login:

```bash
for i in $(seq 1 12); do
  curl -so /dev/null -w '%{http_code} ' -X POST \
    -d 'email=x@y.z&senha=errada' https://seu-dominio.com.br/api/login
done; echo
```

Deve terminar em `429`.  Se as doze responderem `401`, o limite não está
sendo aplicado — provavelmente `SPED_HUB_TRUST_PROXY` está lendo um
cabeçalho variável.

## 6. Backup

**Antes de qualquer atualização**, e por rotina:

O `docker-compose.yml` não sobe PostgreSQL: em produção ele é externo
(instância gerenciada ou host próprio), apontado por `DATABASE_URL`.

```bash
# PostgreSQL — `DATABASE_URL` no formato aceito pelo pg_dump
#   postgresql://user:senha@host:5432/sped_hub
docker run --rm postgres:16-alpine \
    pg_dump "$DATABASE_URL" | gzip > backup-$(date +%F).sql.gz

# SQLite (usa a API de backup: cópia consistente com o banco em uso)
docker compose exec -T web python -c "
import sqlite3
origem = sqlite3.connect('/app/data/sped_hub.db')
destino = sqlite3.connect('/app/data/backup.db')
origem.backup(destino)
"
```

Um `cp` do arquivo SQLite com a aplicação rodando pode copiar um banco em
estado inconsistente — o WAL não estará incluído.

Teste a restauração pelo menos uma vez.  Backup não verificado não é backup.

## 7. Atualizar versão

```bash
docker compose pull                            # imagem nova do GHCR
# BACKUP (passo 6) — antes da migração, não depois
docker compose run --rm migrate                # aplica migrações pendentes
docker compose up -d
docker compose logs -f --tail=50 web
```

Migrações rodam sob advisory lock, então `web` e `worker` subindo juntos não
se atropelam.  Ainda assim o serviço `migrate` existe para que a falha, se
houver, apareça num log só.

## 8. Rollback

```bash
docker compose down
# volte a imagem para a versão anterior no .env / compose
docker compose up -d
```

**Atenção:** migração aplicada não volta sozinha.  Se a versão nova alterou
schema de forma incompatível, o rollback exige `alembic downgrade` ou a
restauração do backup.  É por isso que o backup vem antes da migração.

## 9. Observabilidade

| O quê | Onde |
|---|---|
| Saúde | `GET /api/v1/health` (público), `GET /api/health/full` |
| Métricas operacionais | `GET /monitoring` (admin) |
| Auditoria | `GET /auditoria` (admin) |
| Logs | `docker compose logs`; com `SPED_HUB_LOG_JSON=true`, uma linha JSON por evento |

Os logs mascaram e-mail, CNPJ, CPF e tokens antes de sair — a cauda dos
documentos é preservada para que ainda dê para casar a linha com o registro
certo numa investigação.

---

## Fora do escopo deste checklist

Itens que dependem de contrato, credencial ou decisão que só o operador pode
tomar, e que por isso **não** têm automação neste repositório:

- provisionamento do servidor ou da instância gerenciada de PostgreSQL;
- retenção e destino dos backups (bucket, criptografia, expurgo);
- alertas (quem é avisado, por qual canal, com qual limiar);
- integração com Domínio, Questor ou Alterdata.
