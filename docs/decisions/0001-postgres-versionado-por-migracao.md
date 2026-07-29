# ADR 0001 — PostgreSQL versionado por migração, SQLite por create_all

## Contexto

`Base.metadata.create_all` cria o que falta e não faz mais nada: não altera
tipo de coluna, não renomeia, não remove, não preenche valor para coluna nova
`NOT NULL`. Em desenvolvimento, onde o banco nasce vazio a cada vez, isso
nunca aparece. Em produção, com dados, o schema passa a divergir dos modelos
em silêncio, e o erro só surge na primeira query que usa a coluna que nunca
foi criada.

## Decisão

PostgreSQL é versionado por migração (Alembic). SQLite, em desenvolvimento e
testes, continua com `create_all`.

As migrações são exercitadas contra os **dois** backends no CI mesmo assim,
para não apodrecerem sem ninguém notar.

O upgrade roda sob `pg_advisory_xact_lock`: `web` e `worker` sobem juntos no
compose, e duas migrações simultâneas executam o mesmo `CREATE TABLE`.

## Alternativas descartadas

**Alembic também em SQLite.** Custa tempo em cada execução de teste e obriga
a manter histórico para um banco que é sempre descartável. O ganho seria
uniformidade; o custo, atrito diário.

**`create_all` também em produção.** É o que existia, e é a origem do
problema descrito no contexto.

**Migração automática na subida da aplicação, sem serviço dedicado.** O
advisory lock já resolveria a corrida, mas a falha apareceria espalhada nos
logs de vários containers.

## Consequências

**Mais fácil:** alterar schema em produção sem perder dados; adotar o
controle de versão num banco pré-existente (`sped-hub migrar adotar`).

**Mais difícil:** toda alteração em `src/db/models.py` passa a exigir a
geração da revisão correspondente. O teste que compara o schema da migração
com o dos modelos, coluna a coluna, quebra quando alguém esquece — que é o
comportamento desejado, mas é atrito real.
