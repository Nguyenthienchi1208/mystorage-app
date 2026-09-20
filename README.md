# MyStorage App

A Streamlit + PostgreSQL demo application for MyStorage analytics and AI assistant.

## Prerequisites
- Git
- Docker & Docker Compose (optional for DB)
- Python 3.10+ (project uses a virtualenv at `.venv`)

## Quickstart (Local, recommended)
1. Clone the repo

```bash
git clone <repo-url> mystorage-app
cd mystorage-app
```

2. Create & activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

3. Install Python dependencies

```bash
pip install -r requirements.txt
```

4. Configure environment variables

Set DB connection info (defaults are shown):

```bash
export DB_HOST=localhost
export DB_PORT=5433
export DB_NAME=mystorage_db
export DB_USER=myuser
export DB_PASSWORD=mypassword
```

> Note: `db.get_db_config()` in `db.py` defaults to port `5433`.

5. Run PostgreSQL

Option A — using Docker Compose (recommended for reproducible local DB):

```bash
# If repository provides compose.yaml
docker compose -f compose.yaml up -d
```

Option B — run Postgres manually or use an existing DB. Ensure the env vars above point to your DB.

6. Initialize schema

You can let the app create tables automatically on first run (it calls `ensure_tables()`), or run the schema manually with `psql`:

```bash
# Example: using psql
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -f schema.sql
```

7. Seed the database

The repository includes `seed_data.py` with a CLI entry. Run it with the same env vars to populate initial data:

```bash
.venv/bin/python3 seed_data.py
```

If the DB is already seeded the script will detect and skip.

8. Run the Streamlit app

Run using the included `uv` wrapper (used in this workspace) or directly via streamlit:

```bash
# Using uv wrapper (if available in your environment)
uv run streamlit run app.py

# Or directly (inside virtualenv)
.venv/bin/streamlit run app.py
```

Open the local URL printed in the terminal (usually `http://localhost:8501`).

## Docker (Build & Run)
Build the Docker image (uses `Dockerfile` in repo):

```bash
docker build -t mystorageapp:latest .
```

Run the container (example environment variables):

```bash
docker run -it --rm \
  -e DB_HOST=host.docker.internal \
  -e DB_PORT=5433 \
  -e DB_NAME=mystorage_db \
  -e DB_USER=myuser \
  -e DB_PASSWORD=mypassword \
  -p 8501:8501 \
  mystorageapp:latest
```

If you have `compose.debug.yaml`, use it for a debug run:

```bash
docker compose -f compose.debug.yaml up --build
```

## Running schema & seed manually (recap)
- To run DDL: `psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -f schema.sql`
- To seed: `.venv/bin/python3 seed_data.py`

## Useful commands
- Python syntax check: `python3 -m py_compile app.py`
- Run quick smoke tests (example):

```bash
.venv/bin/python3 - <<'PY'
from app import connect_db_or_die, run_chat_query_db
conn, ctx = connect_db_or_die()
try:
    sql, df, ans = run_chat_query_db(conn, "Kho nào còn nhiều slot trống nhất hệ thống?")
    print(ans)
finally:
    ctx.__exit__(None, None, None)
PY
```

## Troubleshooting
- `ModuleNotFoundError` (e.g., pandas): ensure you installed `requirements.txt` inside the activated virtualenv.
- `current transaction is aborted`: previous SQL failed — restart the DB transaction or the app; `db.fetch_df` now performs `conn.rollback()` on query error to recover.
- Streamlit deprecation: the code replaces `use_container_width` with `width='stretch'`.

## Next steps / Improvements
- Improve natural-language extraction (districts, customer names) for higher recall.
- Add unit tests and CI to run smoke tests for DB and app.
- Add a `docs/chat_prompts.md` to document supported prompts for Sales.

---

If you want, I can add `docs/chat_prompts.md` and link it from this README. Replace `<repo-url>` above with your repository URL when sharing.
