"""Integration tests for the Redis work queue."""

from __future__ import annotations

from uuid import uuid4

import pytest

from schemas.message import AgentRole
from store.queue import TaskQueue, _dead_key, _processing_key

pytestmark = pytest.mark.integration


def test_enqueue_dequeue_fifo(rds) -> None:
    q = TaskQueue(rds)
    a, b = uuid4(), uuid4()
    q.enqueue(AgentRole.ENGINEER, a)
    q.enqueue(AgentRole.ENGINEER, b)

    assert q.depth(AgentRole.ENGINEER) == 2
    assert q.dequeue(AgentRole.ENGINEER, timeout=1) == a
    assert q.dequeue(AgentRole.ENGINEER, timeout=1) == b
    assert q.depth(AgentRole.ENGINEER) == 0


def test_dequeue_reserves_on_processing_list(rds) -> None:
    q = TaskQueue(rds)
    task_id = uuid4()
    q.enqueue(AgentRole.DESIGNER, task_id)

    got = q.dequeue(AgentRole.DESIGNER, timeout=1)
    assert got == task_id
    # Still held in-flight until ack'd (crash recovery).
    assert rds.lrange(_processing_key(AgentRole.DESIGNER), 0, -1) == [str(task_id)]

    q.mark_done(AgentRole.DESIGNER, task_id)
    assert rds.llen(_processing_key(AgentRole.DESIGNER)) == 0


def test_dequeue_timeout_returns_none(rds) -> None:
    q = TaskQueue(rds)
    assert q.dequeue(AgentRole.REVIEWER, timeout=1) is None


def test_mark_failed_requeues(rds) -> None:
    q = TaskQueue(rds)
    task_id = uuid4()
    q.enqueue(AgentRole.ENGINEER, task_id)
    q.dequeue(AgentRole.ENGINEER, timeout=1)

    q.mark_failed(AgentRole.ENGINEER, task_id, requeue=True)
    assert rds.llen(_processing_key(AgentRole.ENGINEER)) == 0
    # Back on the queue for another attempt.
    assert q.dequeue(AgentRole.ENGINEER, timeout=1) == task_id


def test_mark_failed_dead_letters(rds) -> None:
    q = TaskQueue(rds)
    task_id = uuid4()
    q.enqueue(AgentRole.ENGINEER, task_id)
    q.dequeue(AgentRole.ENGINEER, timeout=1)

    q.mark_failed(AgentRole.ENGINEER, task_id, requeue=False)
    assert rds.llen(_processing_key(AgentRole.ENGINEER)) == 0
    assert rds.lrange(_dead_key(AgentRole.ENGINEER), 0, -1) == [str(task_id)]


def test_queues_are_isolated_per_role(rds) -> None:
    q = TaskQueue(rds)
    q.enqueue(AgentRole.DESIGNER, uuid4())
    assert q.depth(AgentRole.DESIGNER) == 1
    assert q.depth(AgentRole.ENGINEER) == 0
