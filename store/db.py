"""Connections to the state store + schema application.

Reads DATABASE_URL / REDIS_URL from the environment (CLAUDE.md: never
hardcode secrets). The same URLs work against docker-compose or the native
dev-services script — that's the whole point of mirroring their config.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import redis

_SCHEMA_SQL = Path(__file__).with_name("schema.sql")

DEFAULT_DATABASE_URL = "postgresql://agentic:devpass@127.0.0.1:5432/agentic"
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


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
