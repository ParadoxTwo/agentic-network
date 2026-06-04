"""Worker: the process that actually runs agents.

Pulls a task_id from its role's queue, loads the full task from Postgres,
runs the agent, and writes the result back. Stateless and restart-safe — a
crash leaves the task on the processing list for recovery. Workers never
decide what happens next; the orchestrator reads their results and schedules
the following step.
"""

from __future__ import annotations

import logging
from uuid import UUID

from agents.base import Agent
from schemas.message import AgentRole
from store.queue import TaskQueue
from store.tasklog import TaskLog

log = logging.getLogger("worker")


class Worker:
    def __init__(
        self, role: AgentRole, agent: Agent, tasklog: TaskLog, queue: TaskQueue
    ) -> None:
        self._role = role
        self._agent = agent
        self._log = tasklog
        self._queue = queue
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run_once(self, timeout: int = 5) -> bool:
        """Process at most one task. Returns True if one was handled."""
        task_id = self._queue.dequeue(self._role, timeout=timeout)
        if task_id is None:
            return False
        self._handle(task_id)
        return True

    def run_forever(self, timeout: int = 5) -> None:
        while not self._stop:
            self.run_once(timeout=timeout)

    def _handle(self, task_id: UUID) -> None:
        task = self._log.get_task(task_id)
        if task is None:
            self._queue.mark_done(self._role, task_id)  # nothing to do
            return
        self._log.start_task(task_id)
        try:
            result = self._agent.run(task["inputs"])
        except Exception as exc:  # noqa: BLE001 - never let a worker die on one task
            log.exception("agent %s crashed on task %s", self._role.value, task_id)
            self._log.fail_task(task_id, f"agent crashed: {exc}")
            self._queue.mark_failed(self._role, task_id, requeue=False)
            return

        if result.ok:
            self._log.complete_task(task_id, result.output, result.cost_tokens)
            self._queue.mark_done(self._role, task_id)
        else:
            # Record cost even on failure, then mark the task failed.
            self._log.add_cost(task_id, result.cost_tokens)
            self._log.fail_task(task_id, result.error or "agent returned not-ok")
            self._queue.mark_failed(self._role, task_id, requeue=False)
