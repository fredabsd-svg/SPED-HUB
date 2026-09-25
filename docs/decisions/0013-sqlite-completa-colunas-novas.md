# ADR 0013 — O SQLite completa as colunas novas ao subir

## Contexto

A política de schema (`docs/migrations.md`) é: PostgreSQL por migração,
SQLite por `create_all`. O `create_all` cria a tabela que falta e não altera
as que já existem.

A migração `b182f5a414b4`, da mesma leva da 0.20.0, criou a tabela
`signatarios` e três colunas `responsavel_*` em `empresas`. Quem atualizou o
código e subiu o painel direto sobre o SQLite de antes, sem
`sped-hub migrar aplicar`, recebeu "Internal Server Error" na página
inicial. O `create_all` criou `signatarios`, mas `empresas` ficou sem as
colunas novas, e toda consulta à empresa falhava com "no such column".

Pior: com `signatarios` já criada pelo painel, `migrar aplicar` também
falhava ("table signatarios already exists"). O banco ficava sem saída pelo
caminho documentado.

O `docker-compose` roda a migração antes do painel e não tem o problema. Tem
quem sobe o painel direto: `uvicorn` no desenvolvimento, instalação sem
Docker.

## Decisão

1. **No SQLite, `init_db` completa as colunas que faltam** em tabelas que já
   existem (`completar_colunas`), depois do `create_all`. Só entram as que o
   `ALTER TABLE ADD COLUMN` aceita numa tabela com linhas: coluna que admite
   nulo ou tem `server_default`, e que não é única nem chave primária. As
   outras ficam no log, com o pedido de `migrar aplicar`.
2. **Migração que cria tabela, índice ou coluna confere antes se ela já
   existe**, porque o painel pode ter subido antes dela. A `b182f5a414b4`
   passa a fazer isso.
3. **PostgreSQL continua só por migração.** `completar_colunas` não roda
   fora do SQLite.

## Alternativas descartadas

- **Rodar `alembic upgrade head` ao subir o painel.** Resolveria o banco sob
  o Alembic, mas não o que nasceu do `create_all` e nunca teve
  `alembic_version`: nele o upgrade tentaria criar tudo de novo. Além disso,
  poria migração de dados, que pode ser longa, no caminho de todo início do
  painel, disputando o banco com o worker.
- **Só documentar "rode `migrar aplicar`".** O aviso já estava no CHANGELOG e
  não evitou o erro. E não resolve o banco em que o painel já subiu, porque
  a migração falhava nele.
- **Tela de erro pedindo a migração.** É melhor que o 500, mas para o
  escritório por um passo que o código consegue dar sozinho.

## Consequências

- Atualizar o código e subir o painel volta a funcionar no SQLite quando a
  versão traz coluna nova anulável, que é o caso comum.
- O `alembic_version` não avança sozinho: `migrar status` mostra a revisão
  pendente até `migrar aplicar`, que agora passa.
- Coluna `NOT NULL` sem default, renomeação, mudança de tipo e migração de
  dados continuam exigindo `migrar aplicar`. O log diz qual coluna faltou.
- Migração nova que cria tabela, índice ou coluna precisa da guarda do item
  2. O teste `test_a_migracao_mais_recente_aplica_depois_que_o_painel_subiu`
  exercita sempre a revisão `head`, então a guarda esquecida quebra o CI.
