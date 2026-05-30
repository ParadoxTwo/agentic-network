"""Typed inter-agent message contract.

Per CLAUDE.md: messages between agents are typed (JSON schema) with task_id,
sender, recipient, inputs, expected_output, and a termination condition. No
agent may invent recipients or tasks off-schema. The wire field ``workflow_id``
equals ``runs.run_id`` in the store.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class AgentRole(str, Enum):
    """The only valid senders/recipients. Closed set — no ad-hoc roles."""

    ORCHESTRATOR = "orchestrator"
    DESIGNER = "designer"
    ENGINEER = "engineer"
    REVIEWER = "reviewer"


class TaskStatus(str, Enum):
    WAITING = "waiting"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TaskMessage(BaseModel):
    """The contract handed to a worker. `recipient` is the role that executes;
    the worker loads the full task row from Postgres by `task_id` (the queue
    only carries the id — Postgres is the source of truth)."""

    task_id: UUID = Field(default_factory=uuid4)
    workflow_id: UUID  # == runs.run_id
    sender: AgentRole
    recipient: AgentRole
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected_output: dict[str, Any] = Field(default_factory=dict)
    # Termination condition (CLAUDE.md safety rails):
    max_steps: int = 1
    timeout_ms: int = 300_000
    status: TaskStatus = TaskStatus.WAITING
    created_at: datetime = Field(default_factory=_now)
