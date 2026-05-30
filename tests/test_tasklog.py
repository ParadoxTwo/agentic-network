"""Integration tests for the Postgres task log."""

from __future__ import annotations

import pytest

from schemas.message import AgentRole, RunStatus, TaskStatus
from store.tasklog import TaskLog

pytestmark = pytest.mark.integration


def test_create_run_starts_pending(pg) -> None:
    log = TaskLog(pg)
    run_id = log.create_run(issue_id=42, created_by="owner")

    run = log.get_run(run_id)
    assert run is not None
    assert run["issue_id"] == 42
    assert run["status"] == RunStatus.PENDING.value
    assert run["total_cost_tokens"] == 0


def test_task_lifecycle_waiting_to_done(pg) -> None:
    log = TaskLog(pg)
    run_id = log.create_run(issue_id=1, created_by="owner")
    task_id = log.create_task(
        run_id,
        agent=AgentRole.DESIGNER,
        sender=AgentRole.ORCHESTRATOR,
        inputs={"issue_title": "add login"},
        expected_output={"architecture": "str"},
        max_steps=2,
    )

    task = log.get_task(task_id)
    assert task is not None
    assert task["status"] == TaskStatus.WAITING.value
    assert task["inputs"] == {"issue_title": "add login"}
    assert task["attempt_count"] == 0

    log.start_task(task_id)
    assert log.get_task(task_id)["status"] == TaskStatus.IN_PROGRESS.value
    assert log.get_task(task_id)["attempt_count"] == 1
    assert log.get_task(task_id)["started_at"] is not None

    log.complete_task(task_id, output={"architecture": "MVC"}, cost_tokens=1500)
    done = log.get_task(task_id)
    assert done["status"] == TaskStatus.DONE.value
    assert done["output"] == {"architecture": "MVC"}
    assert done["completed_at"] is not None


def test_cost_rolls_up_to_run(pg) -> None:
    log = TaskLog(pg)
    run_id = log.create_run(issue_id=1, created_by="owner")
    t1 = log.create_task(run_id, AgentRole.DESIGNER, AgentRole.ORCHESTRATOR)
    t2 = log.create_task(run_id, AgentRole.ENGINEER, AgentRole.ORCHESTRATOR)

    log.complete_task(t1, output={}, cost_tokens=1000)
    log.complete_task(t2, output={}, cost_tokens=2500)

    assert log.get_run(run_id)["total_cost_tokens"] == 3500


def test_fail_task_records_error(pg) -> None:
    log = TaskLog(pg)
    run_id = log.create_run(issue_id=1, created_by="owner")
    task_id = log.create_task(run_id, AgentRole.ENGINEER, AgentRole.ORCHESTRATOR)

    log.start_task(task_id)
    log.fail_task(task_id, error="tests did not pass")

    task = log.get_task(task_id)
    assert task["status"] == TaskStatus.FAILED.value
    assert task["error"] == "tests did not pass"


def test_list_tasks_in_graph_order(pg) -> None:
    log = TaskLog(pg)
    run_id = log.create_run(issue_id=1, created_by="owner")
    design = log.create_task(
        run_id, AgentRole.DESIGNER, AgentRole.ORCHESTRATOR, seq=0
    )
    log.create_task(
        run_id,
        AgentRole.ENGINEER,
        AgentRole.ORCHESTRATOR,
        seq=1,
        parent_task_id=design,
    )
    log.create_task(
        run_id, AgentRole.REVIEWER, AgentRole.ORCHESTRATOR, seq=2
    )

    tasks = log.list_tasks(run_id)
    assert [t["agent"] for t in tasks] == ["designer", "engineer", "reviewer"]


def test_get_missing_returns_none(pg) -> None:
    from uuid import uuid4

    log = TaskLog(pg)
    assert log.get_run(uuid4()) is None
    assert log.get_task(uuid4()) is None
