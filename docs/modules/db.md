# db

## O que faz

Define o schema (SQLAlchemy 2.x, `DeclarativeBase`), cria e cacheia engines, e
aplica migrações Alembic. É a fronteira entre a aplicação e o banco: nenhum
outro módulo constrói engine ou decide entre `create_all` e `alembic upgrade`.

## O que expõe

`src/db/models.py`

| Símbolo | Para quê |
|---|---|
| `Base` e ~25 modelos | Schema. `Escritorio`, `Usuario`, `Empresa`, `ECD`, `Lancamento`, `Partida`, `ApiKey`, `AuditLog`, … |
| `criar_engine(...)` | Engine nova, sem cache. |
| `obter_engine(...)` | Engine cacheada por processo. Caminho normal. |
| `init_db(engine)` / `init_db_once(engine)` | `create_all`; a segunda só na primeira vez por engine. |
| `get_session(engine=None)` | `Session` do SQLAlchemy. |
| `truncar_para_coluna(modelo, campo, valor)` | Corta no limite real declarado no modelo. |

`src/db/migrations.py`

| Símbolo | Para quê |
|---|---|
| `upgrade_head(url=None)` | `alembic upgrade head` sob advisory lock. |
| `stamp_head(url=None)` | Carimba banco pré-existente sem reaplicar migração. |
| `revisao_head()` / `revisao_atual(engine)` | Comparação de revisão. |
| `alembic_config(url=None)` | `Config` pronto, com a URL escapada. |

### Migração de dados (`db.migrations`)

| Símbolo | Para quê |
|---|---|
| `migrar_dados(origem, destino, *, lote=1000)` | Copia o conteúdo inteiro preservando ids; recusa destino ocupado ou sem schema. |
| `conferir_migracao_de_dados(origem, destino)` | Compara as contagens; devolve só o que divergir. |
| `ErroDeMigracaoDeDados` | Recusa ou interrupção — nada foi gravado. |

- **Preservar id é obrigatório, não conveniência.** As chaves estrangeiras do
  banco inteiro apontam para eles; renumerar exigiria reescrever cada
  referência em `partidas`, `saldos_periodicos` e `lancamentos`.
- **A sequência do Postgres é o defeito silencioso desta migração.** Linhas
  chegam com id explícito, a sequência continua em 1, e a migração parece ter
  dado certo. O erro só aparece quando o escritório cadastra a próxima empresa
  e recebe violação de chave primária. `_corrigir_sequencias` roda `setval` em
  toda PK inteira; `COALESCE(..., 0)` cobre tabela vazia, onde `MAX(id)` é NULL
  e `setval(NULL)` levantaria erro.
- **Tudo ou nada.** A cópia roda em uma transação no destino. Meia escrituração
  migrada é pior que nenhuma, porque parece completa — e é dela que sairia um
  balanço errado.
- **Destino precisa estar vazio.** Migrar sobre banco com dados misturaria a
  escrituração de dois lugares; a recusa acontece antes de qualquer escrita.
- **Memória constante.** As linhas saem em lotes (`stream_results` +
  `fetchmany`): `partidas` de uma ECD real não cabe em memória. Há teste
  contando os INSERTs emitidos, porque contar linhas copiadas passaria mesmo
  sem o fatiamento.
- A ordem de cópia vem de `Base.metadata.sorted_tables` — pai antes de filho.

## Depende de / quem depende

Depende de `src.settings` (URL, echo) e do Alembic.

Quem depende: praticamente tudo — `cli`, `watchdog`, `auth`, `audit`,
`monitoring`, `ratelimit`, `async_jobs`, `webhooks`, `api`, `filters`,
`validators`, os seis relatórios em `reports/` e `dashboard`.

## Decisões não óbvias e armadilhas

- **A engine é cacheada por processo, exceto `:memory:`.** Um banco em memória
  vive dentro da conexão; cachear a engine faria dois testes compartilharem
  estado. O cache é limpo em `os.register_at_fork` — engine herdada por fork
  compartilha socket TCP com o pai e corrompe a conexão dos dois.
- **`init_db_once` usa `WeakSet` de engines**, não flag global: engines
  diferentes (bancos diferentes) precisam cada uma da sua inicialização.
- **`truncar_para_coluna` lê o limite do metadata do SQLAlchemy**, não de uma
  constante. `String(n)` é ignorado pelo SQLite e imposto pelo Postgres: um
  `User-Agent` de 1 KB gravava sem reclamar em desenvolvimento e derrubava o
  login inteiro em produção.
- **`alembic_config` escapa `%` como `%%`.** A URL vai para um `configparser`,
  onde `%` é interpolação. Senha com `%` quebrava a configuração.
- **`upgrade_head` roda sob `pg_advisory_xact_lock`.** Várias réplicas subindo
  ao mesmo tempo aplicariam a mesma migração em paralelo.
- **`create_all` não migra.** Ele cria o que falta e nada mais: não altera
  tipo, não renomeia, não remove. Em Postgres o caminho é migração (§6.2).

## Como testar isoladamente

```bash
pytest tests/test_multibackend.py tests/test_migrations.py -q
# com Postgres:
DATABASE_URL=postgresql+psycopg://... pytest tests/test_multibackend.py -q
```

## O que não faz

- Não contém regra de negócio: cálculo contábil vive em `reports/`.
- Não gera migração automaticamente — `alembic revision --autogenerate` é
  passo manual, revisado à mão.
- Não faz pooling entre processos nem retry de conexão.
