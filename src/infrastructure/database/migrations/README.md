# Migrations

Async Alembic. The DB URL is read from application settings (`src/core/config`), not from
`alembic.ini`. The full model registry (`src/infrastructure/database/models`) is the metadata,
so `--autogenerate` sees every table.

## First migration

Generate it once Postgres is reachable (autogenerate against Postgres renders JSONB and
partial-unique indexes correctly — a hand-written or SQLite-generated migration would not):

```bash
make up                       # starts postgres (and applies migrations on the web container)
# or point .env at any reachable Postgres, then:
make revision m="init"        # writes versions/<hash>_init.py
# review the generated file, then:
make migrate
```

## Subsequent migrations

```bash
make revision m="add gateway order_index"
# ALWAYS eyeball the diff — autogenerate misses enum value changes, CHECK constraints,
# and data migrations.
make migrate
```

## Tests

Tests do **not** use migrations — they build the schema with `Base.metadata.create_all`
against in-memory aiosqlite (see `tests/conftest.py`). Keep models portable (custom types
declare a SQLite variant) so this stays true.
