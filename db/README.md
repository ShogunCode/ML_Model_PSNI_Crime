# Database setup

This project now targets Postgres/PostGIS for the pipeline outputs; CSVs remain
as exports.

## Postgres (recommended)

Run migrations:

```bash
alembic upgrade head
```

The database URL is taken from `DATABASE_URL` (or `CRIMEMAP_DATABASE_URL`).

Load data from the pipeline:

```bash
python src/main.py --run-all --write-postgres
```

Or load from CSV exports:

```bash
python scripts/load_postgres.py --database crimemap
```

Environment variables supported:
- `DATABASE_URL`
- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`

## MySQL (legacy)

The legacy MySQL loader still uses `db/schema.sql` and `scripts/load_mysql.py`.
It is kept for reference but is no longer the primary data store.
