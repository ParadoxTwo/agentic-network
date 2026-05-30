"""Redis work queue: how tasks get dispatched to worker processes.

The queue carries only task_ids — the full typed task lives in Postgres
(single source of truth). Delivery is reliable: dequeue atomically moves the
id to a per-role processing list, so a worker that crashes mid-task leaves a
recoverable record instead of dropping it. mark_done acks; mark_failed either
requeues for retry or dead-letters.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

import redis

from schemas.message import AgentRole


def _queue_key(role: AgentRole) -> str:
    return f"queue:{role.value}"


def _processing_key(role: AgentRole) -> str:
    return f"processing:{role.value}"


def _dead_key(role: AgentRole) -> str:
    return f"dead:{role.value}"


class TaskQueue:
    """Per-role reliable queue. Stateless wrapper over a Redis client."""

    def __init__(self, client: redis.Redis) -> None:
        self._r = client

    def enqueue(self, role: AgentRole, task_id: UUID) -> None:
        """Hand a task to the role's worker pool (FIFO)."""
        self._r.lpush(_queue_key(role), str(task_id))

    def dequeue(self, role: AgentRole, timeout: int = 0) -> UUID | None:
        """Block up to `timeout`s for a task, atomically reserving it.

        Returns the task_id, now also recorded on the processing list until
        ack'd. timeout=0 blocks indefinitely; a positive timeout returns None
        if nothing arrives.
        """
        raw = self._r.blmove(
            _queue_key(role), _processing_key(role), timeout, "RIGHT", "LEFT"
        )
        return UUID(cast(str, raw)) if raw else None

    def mark_done(self, role: AgentRole, task_id: UUID) -> None:
        """Ack: drop the task from the processing list."""
        self._r.lrem(_processing_key(role), 1, str(task_id))

    def mark_failed(
        self, role: AgentRole, task_id: UUID, *, requeue: bool = False
    ) -> None:
        """Remove from processing; requeue for retry or send to dead-letter."""
        pipe = self._r.pipeline()
        pipe.lrem(_processing_key(role), 1, str(task_id))
        if requeue:
            pipe.lpush(_queue_key(role), str(task_id))
        else:
            pipe.lpush(_dead_key(role), str(task_id))
        pipe.execute()

    def depth(self, role: AgentRole) -> int:
        """Number of tasks waiting (not yet picked up) for a role."""
        return int(self._r.llen(_queue_key(role)))
