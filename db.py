import os
import psycopg2
import psycopg2.extras
from contextlib import contextmanager


def get_db_config():
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "dbname": os.getenv("DB_NAME", "mystorage_db"),     # Sửa 'mystorage' -> 'mystorage_db'
        "user": os.getenv("DB_USER", "myuser"),            # Sửa 'postgres' -> 'myuser'
        "password": os.getenv("DB_PASSWORD", "mypassword"), # Sửa 'postgres' -> 'mypassword'
        "port": int(os.getenv("DB_PORT", 5433)),            # Sửa 5432 -> 5433
    }


@contextmanager
def get_conn():
    cfg = get_db_config()
    conn = None
    try:
        conn = psycopg2.connect(**cfg)
        yield conn
    except Exception:
        raise
    finally:
        if conn:
            conn.close()


def execute_script(conn, sql_script: str):
    """Execute a multi-statement SQL script."""
    with conn.cursor() as cur:
        cur.execute(sql_script)
    conn.commit()


def fetch_df(conn, sql: str, params=None):
    import pandas as pd

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            cur.execute(sql, params or ())
            rows = cur.fetchall()
        except Exception:
            # ensure transaction is usable for subsequent queries
            conn.rollback()
            raise
    return pd.DataFrame(rows)


def ensure_tables(conn, schema_path="schema.sql"):
    # Read schema.sql and execute
    if not os.path.exists(schema_path):
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    with open(schema_path, "r", encoding="utf-8") as f:
        sql = f.read()
    execute_script(conn, sql)