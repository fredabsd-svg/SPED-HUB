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
- `SPED_HUB_ALLOWED_HOSTS` — o domínio real, **não** `*`.
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

Não há migração de dados automatizada.  O caminho testado é reimportar as
ECDs no banco novo — a importação é idempotente por (empresa, período) e
recusa duplicatas, então repetir é seguro.

## 3. SSL

O `docker-compose.yml` traz nginx e certbot.  Na primeira emissão:

```bash
docker compose up -d nginx
docker compose run --rm certbot certonly --webroot -w /var/www/certbot \
    -d seu-dominio.com.br --email voce@exemplo.com --agree-tos --no-eff-email
docker compose restart nginx
```

O `nginx.conf` referencia `/etc/letsencrypt/live/sped-hub/`.  Ou nomeie o
certificado assim (`--cert-name sped-hub`), ou ajuste o caminho no arquivo.
A renovação roda sozinha no serviço `certbot`.

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
