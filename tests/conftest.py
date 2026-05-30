"""Test fixtures: isolated Postgres DB + Redis namespace.

Integration tests need the live store. If it's unreachable, every test that
asks for these fixtures is skipped (so `pytest` stays green on a machine with
no DB) rather than erroring.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator

import psycopg
import pytest
import redis

from store import db

# Isolate tests from any real data: own database, own Redis logical db.
TEST_DB = "agentic_test"
TEST_REDIS_DB = 15


def _admin_url() -> str:
    # Connect to the default 'postgres' db to create/drop the test db.
    base = db.database_url()
    return base.rsplit("/", 1)[0] + "/postgres"


def _test_pg_url() -> str:
    base = db.database_url()
    return base.rsplit("/", 1)[0] + "/" + TEST_DB


def _connect_admin_with_retry(
    attempts: int = 10, delay: float = 1.0
) -> psycopg.Connection | None:
    """Connect to the admin DB, retrying briefly.

    Closes the race where `docker compose up -d` is still starting Postgres
    when tests begin — without that, a not-yet-ready DB would *skip* (false
    green) instead of running. Returns None only if it's genuinely absent.
    """
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return psycopg.connect(_admin_url(), autocommit=True)
        except Exception as exc:  # noqa: BLE001 - retry any connection failure
            last = exc
            time.sleep(delay)
    print(f"store unreachable after {attempts} attempts: {last}")
    return None


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_db() -> Iterator[None]:
    admin = _connect_admin_with_retry()
    if admin is None:
        pytest.skip("Postgres unreachable, skipping integration tests")
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        cur.execute(f'CREATE DATABASE "{TEST_DB}"')
    admin.close()
    conn = psycopg.connect(_test_pg_url())
    db.apply_schema(conn)
    conn.close()
    yield


@pytest.fixture()
def pg() -> Iterator[psycopg.Connection]:
    """A connection to the test DB, truncated clean before each test."""
    conn = psycopg.connect(_test_pg_url())
    with conn.cursor() as cur:
        cur.execute("TRUNCATE runs, tasks, task_graph CASCADE")
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture()
def rds() -> Iterator[redis.Redis]:
    """A flushed Redis logical DB dedicated to tests."""
    base = db.redis_url().rsplit("/", 1)[0]
    url = f"{base}/{TEST_REDIS_DB}"
    try:
        client = redis.Redis.from_url(url, decode_responses=True)
        client.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Redis unreachable, skipping: {exc}")
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


# Let store.* pick up the test DB if any code reads the env directly.
os.environ.setdefault("PYTEST_RUNNING", "1")
