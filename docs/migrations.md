# Política de versionamento do schema

## A regra

**PostgreSQL é versionado por migração.  SQLite continua com `create_all`.**

`Base.metadata.create_all` cria o que falta e não faz mais nada: não altera
tipo de coluna, não renomeia, não remove, não preenche valor para coluna nova
`NOT NULL`.  Num banco de desenvolvimento, que nasce vazio a cada vez, isso
nunca aparece.  Num banco de produção que já tem dados, o schema passa a
divergir dos modelos em silêncio — e o erro só aparece na primeira query que
usa a coluna que nunca foi criada.

Por isso:

| Ambiente | Como o schema é criado |
|---|---|
| Desenvolvimento e testes (SQLite) | `create_all` — mais rápido, sem histórico a manter |
| Produção (PostgreSQL) | `alembic upgrade head` |

As migrações são exercitadas **contra os dois backends** no CI mesmo assim,
para não apodrecerem sem ninguém notar.

## Comandos

```bash
sped-hub migrar status     # onde o banco está vs. o que existe de migração
sped-hub migrar aplicar    # leva o banco até a revisão mais recente
sped-hub migrar adotar     # adota um banco que já tem o schema (ver abaixo)
```

Todos aceitam `--db` (caminho de arquivo ou URL) e, sem ele, usam
`DATABASE_URL`.

## Bancos criados antes da Etapa 3

Qualquer instalação das fases 1–16 tem o schema completo, criado por
`create_all`, e nenhuma tabela `alembic_version`.  Rodar `aplicar` nesse banco
tentaria criar tabelas que já existem e falharia.

O caminho é adotar:

```bash
sped-hub migrar adotar    # grava a revisão atual sem executar nada
sped-hub migrar status    # confirma: "Schema em dia."
```

A partir daí as migrações seguintes aplicam normalmente.

## Gerando uma nova revisão

Depois de alterar `src/db/models.py`:

```bash
alembic revision --autogenerate -m "descrição curta do que mudou"
```

**Revise o arquivo gerado antes de commitar.**  A autogeração acerta bem
tabelas e colunas novas, e erra com frequência em:

- renomeações (vê como *drop* + *create*, o que **perde os dados** da coluna);
- alterações de tipo que precisam de `USING` no PostgreSQL;
- coluna nova `NOT NULL` em tabela com dados — precisa de default ou de um
  passo de preenchimento antes do `ALTER`.

O teste `test_schema_migrado_e_identico_ao_dos_modelos` compara, coluna a
coluna e índice a índice, o schema produzido pela migração com o produzido
pelos modelos.  **Esquecer de gerar a revisão quebra esse teste** — é o que
impede a divergência de chegar em produção.

## Concorrência

`web` e `worker` sobem juntos no `docker-compose`.  Duas migrações simultâneas
no mesmo banco se atropelam: as duas tentam o mesmo `CREATE TABLE` e uma
falha.

`upgrade_head` roda dentro de um *advisory lock* transacional do PostgreSQL
(`pg_advisory_xact_lock`).  O segundo processo espera o primeiro terminar e
então encontra o schema já em `head`, sem fazer nada.  O lock é liberado
automaticamente no fim da transação, inclusive se o processo morrer.

No `docker-compose` a migração ainda assim roda em um serviço `migrate`
próprio, de que `web` e `worker` dependem: o lock protege contra a corrida,
mas ter um lugar único onde a migração acontece torna o log legível quando
algo dá errado.

## Onde fica a URL

Em `src/settings.py`, como todo o resto.  `alembic.ini` tem
`sqlalchemy.url` deliberadamente vazio e `alembic/env.py` lê de
`database_reference()`.  Um segundo lugar para configurar o banco é
exatamente o problema que a Fase 17 resolveu.

Para apontar para outro banco pontualmente:

```bash
alembic -x url=postgresql+psycopg://user@host:5432/outro upgrade head
```
