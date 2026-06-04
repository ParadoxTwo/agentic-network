"""Task log: the durable, auditable record of every run and task.

This is the Postgres side of the store. Agents are stateless functions over
these rows (CLAUDE.md). Every state transition is explicit and timestamped so
the whole run can be traced after the fact.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from schemas.message import AgentRole, RunStatus, TaskStatus


class TaskLog:
    """Thin, typed wrapper over the runs/tasks/task_graph tables.

    Holds a connection but no other state — restart-safe. Callers commit
    through the methods; each write is its own transaction.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    # --- runs -------------------------------------------------------------

    def create_run(
        self,
        issue_id: int,
        created_by: str,
        spec: dict[str, Any] | None = None,
    ) -> UUID:
        run_id = uuid4()
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO runs (run_id, issue_id, created_by, status, spec) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    run_id,
                    issue_id,
                    created_by,
                    RunStatus.PENDING.value,
                    Jsonb(spec or {}),
                ),
            )
        self._conn.commit()
        return run_id

    def claim_pending_runs(self, limit: int = 10) -> list[UUID]:
        """Atomically move up to `limit` pending runs to 'running'.

        Uses SKIP LOCKED so multiple orchestrators never grab the same run.
        Returns the claimed run_ids.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE runs SET status = %s WHERE run_id IN ("
                "  SELECT run_id FROM runs WHERE status = %s "
                "  ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT %s"
                ") RETURNING run_id",
                (RunStatus.RUNNING.value, RunStatus.PENDING.value, limit),
            )
            ids = [r[0] for r in cur.fetchall()]
        self._conn.commit()
        return ids

    def running_runs(self) -> list[UUID]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT run_id FROM runs WHERE status = %s ORDER BY created_at",
                (RunStatus.RUNNING.value,),
            )
            return [r[0] for r in cur.fetchall()]

    def set_run_status(
        self, run_id: UUID, status: RunStatus, final_pr_url: str | None = None
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE runs SET status = %s, "
                "final_pr_url = COALESCE(%s, final_pr_url) WHERE run_id = %s",
                (status.value, final_pr_url, run_id),
            )
        self._conn.commit()

    def get_run(self, run_id: UUID) -> dict[str, Any] | None:
        return self._one("SELECT * FROM runs WHERE run_id = %s", (run_id,))

    # --- tasks ------------------------------------------------------------

    def create_task(
        self,
        run_id: UUID,
        agent: AgentRole,
        sender: AgentRole,
        *,
        inputs: dict[str, Any] | None = None,
        expected_output: dict[str, Any] | None = None,
        max_steps: int = 1,
        timeout_ms: int = 300_000,
        parent_task_id: UUID | None = None,
        seq: int = 0,
    ) -> UUID:
        """Insert a task and its edge in the task graph (one transaction)."""
        task_id = uuid4()
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tasks (task_id, run_id, agent, sender, inputs, "
                "expected_output, max_steps, timeout_ms) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    task_id,
                    run_id,
                    agent.value,
                    sender.value,
                    Jsonb(inputs or {}),
                    Jsonb(expected_output or {}),
                    max_steps,
                    timeout_ms,
                ),
            )
            cur.execute(
                "INSERT INTO task_graph (run_id, task_id, parent_task_id, seq) "
                "VALUES (%s, %s, %s, %s)",
                (run_id, task_id, parent_task_id, seq),
            )
        self._conn.commit()
        return task_id

    def start_task(self, task_id: UUID) -> None:
        """Mark a task in_progress and bump its attempt counter."""
        self._update(
            "UPDATE tasks SET status = %s, started_at = now(), "
            "attempt_count = attempt_count + 1 WHERE task_id = %s",
            (TaskStatus.IN_PROGRESS.value, task_id),
        )

    def complete_task(
        self, task_id: UUID, output: dict[str, Any], cost_tokens: int = 0
    ) -> None:
        """Record a successful result and roll its cost up to the run."""
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE tasks SET status = %s, output = %s, cost_tokens = %s, "
                "completed_at = now() WHERE task_id = %s "
                "RETURNING run_id",
                (TaskStatus.DONE.value, Jsonb(output), cost_tokens, task_id),
            )
            row = cur.fetchone()
            if row is not None and cost_tokens:
                cur.execute(
                    "UPDATE runs SET total_cost_tokens = total_cost_tokens + %s "
                    "WHERE run_id = %s",
                    (cost_tokens, row[0]),
                )
        self._conn.commit()

    def add_cost(self, task_id: UUID, cost_tokens: int) -> None:
        """Add token cost to a task and roll it up to the run (no status change)."""
        if not cost_tokens:
            return
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE tasks SET cost_tokens = cost_tokens + %s WHERE task_id = %s "
                "RETURNING run_id",
                (cost_tokens, task_id),
            )
            row = cur.fetchone()
            if row is not None:
                cur.execute(
                    "UPDATE runs SET total_cost_tokens = total_cost_tokens + %s "
                    "WHERE run_id = %s",
                    (cost_tokens, row[0]),
                )
        self._conn.commit()

    def fail_task(self, task_id: UUID, error: str) -> None:
        self._update(
            "UPDATE tasks SET status = %s, error = %s, completed_at = now() "
            "WHERE task_id = %s",
            (TaskStatus.FAILED.value, error, task_id),
        )

    def get_task(self, task_id: UUID) -> dict[str, Any] | None:
        return self._one("SELECT * FROM tasks WHERE task_id = %s", (task_id,))

    def list_tasks(self, run_id: UUID) -> list[dict[str, Any]]:
        """All tasks for a run in creation order — the basis of a trace.

        Ordered by created_at (monotonic across retries) so the last element is
        always the most recently created task. `seq` is a stage hint only; it
        repeats across retries, so it can't define 'latest'.
        """
        sql = (
            "SELECT t.* FROM tasks t "
            "JOIN task_graph g ON g.task_id = t.task_id "
            "WHERE t.run_id = %s ORDER BY t.created_at, g.seq"
        )
        with self._conn.cursor() as cur:
            cur.execute(sql, (run_id,))
            cols = [d.name for d in cur.description or []]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    # --- internals --------------------------------------------------------

    def _update(self, sql: str, params: tuple[Any, ...]) -> None:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
        self._conn.commit()

    def _one(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                return None
            cols = [d.name for d in cur.description or []]
            return dict(zip(cols, row))
