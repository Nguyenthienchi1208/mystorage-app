# MyStorage App

A Streamlit + PostgreSQL demo application for MyStorage analytics and AI assistant.

## Prerequisites
- Git
- Docker & Docker Compose (optional for DB)
- Python 3.10+ (project uses a virtualenv at `.venv`)

## Quickstart (Local, recommended)
# 1. Clone the repo

```bash
git clone <repo-url> mystorage-app
cd mystorage-app
```

# 2. Create & activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

3. Install Python dependencies

```bash
pip install -r requirements.txt
```

# 4. Configure environment variables

Set DB connection info (defaults are shown):

```bash
export DB_HOST=localhost
export DB_PORT=5433
export DB_NAME=mystorage_db
export DB_USER=myuser
export DB_PASSWORD=mypassword
```

# 5. Run PostgreSQL

Option A — using Docker Compose (recommended for reproducible local DB):

```bash
docker compose up -d
```

# 6. Initialize schema

```bash
PGPASSWORD=mypassword psql -h localhost -p 5433 -U myuser -d mystorage_db -f schema.sql
```

# 7. Seed the database

The repository includes `seed_data.py` with a CLI entry. Run it with the same env vars to populate initial data:

```bash
.venv/bin/python3 seed_data.py
```

If the DB is already seeded the script will detect and skip.

# 8. Run the Streamlit app

Run using the included `uv` wrapper (used in this workspace) or directly via streamlit:

```bash
# Using uv wrapper (if available in your environment)
uv run streamlit run app.py
```
Open the local URL printed in the terminal (`http://localhost:8501`).



