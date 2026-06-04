"""ASGI entrypoint for the trigger/webhook API.

Run with:  uv run uvicorn app.api:app --port 8000

A fresh DB connection is opened per request (psycopg isn't threadsafe across
the server's worker threads).
"""

from __future__ import annotations

import os

from store.db import connect_pg
from store.tasklog import TaskLog
from triggers.webhook import create_app


def _make_tasklog() -> TaskLog:
    return TaskLog(connect_pg())


app = create_app(_make_tasklog, webhook_secret=os.environ.get("WEBHOOK_SECRET"))
