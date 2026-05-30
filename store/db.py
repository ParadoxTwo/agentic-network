"""Connections to the state store + schema application.

Reads DATABASE_URL / REDIS_URL from the environment (CLAUDE.md: never
hardcode secrets). The same URLs work against docker-compose or the native
dev-services script — that's the whole point of mirroring their config.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

import psycopg
import redis

_SCHEMA_SQL = Path(__file__).with_name("schema.sql")

DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"


def database_url() -> str:
    """The Postgres connection string.

    DATABASE_URL wins if set (e.g. a managed Postgres in prod). Otherwise we
    build it from the same POSTGRES_* vars docker-compose reads, so the
    password lives in exactly ONE place (POSTGRES_PASSWORD) and the app and
    the database can't disagree.
    """
    explicit = os.environ.get("DATABASE_URL")
    if explicit:
        return explicit
    user = os.environ.get("POSTGRES_USER", "agentic")
    password = quote(os.environ.get("POSTGRES_PASSWORD", "devpass"), safe="")
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    name = os.environ.get("POSTGRES_DB", "agentic")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def redis_url() -> str:
    return os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)


def connect_pg(url: str | None = None) -> psycopg.Connection:
    """Open a Postgres connection. autocommit=False — callers own transactions."""
    return psycopg.connect(url or database_url())


def connect_redis(url: str | None = None) -> redis.Redis:
    """Open a Redis client that returns str (decode_responses)."""
    return redis.Redis.from_url(url or redis_url(), decode_responses=True)


def apply_schema(conn: psycopg.Connection) -> None:
    """Apply schema.sql. Idempotent — safe to run on every startup."""
    with conn.cursor() as cur:
        cur.execute(_SCHEMA_SQL.read_text())
    conn.commit()
