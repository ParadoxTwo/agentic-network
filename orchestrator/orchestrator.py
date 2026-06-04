"""Orchestrator: drives runs through the feature-delivery state machine.

Long-running and stateless across ticks — all state is in Postgres, so it can
crash and resume. Each tick: claim pending runs, then advance every running
run by one step based on the latest task's result. It never runs agents
itself (workers do that); it schedules the next task and, on approval, does the
GitHub work (branch, commit, PR, optional merge).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from github.client import GitHubClient
from orchestrator.workflow import branch_name, pr_body
from schemas.message import AgentRole, RunStatus, TaskStatus
from store.queue import TaskQueue
from store.tasklog import TaskLog

log = logging.getLogger("orchestrator")

_TERMINAL = {TaskStatus.DONE.value, TaskStatus.FAILED.value}


class Orchestrator:
    def __init__(
        self,
        tasklog: TaskLog,
        queue: TaskQueue,
        github: GitHubClient,
        *,
        max_engineer_attempts: int = 2,
    ) -> None:
        self._log = tasklog
        self._queue = queue
        self._gh = github
        self._max_engineer_attempts = max_engineer_attempts

    # --- tick loop --------------------------------------------------------

    def tick(self) -> None:
        self._log.claim_pending_runs()
        for run_id in self._log.running_runs():
            try:
                self.advance_run(run_id)
            except Exception:  # noqa: BLE001 - one bad run must not stop the loop
                log.exception("advance_run failed for %s", run_id)

    # --- per-run state machine -------------------------------------------

    def advance_run(self, run_id: UUID) -> None:
        run = self._log.get_run(run_id)
        if run is None or run["status"] != RunStatus.RUNNING.value:
            return
        tasks = self._log.list_tasks(run_id)

        if not tasks:
            self._enqueue_design(run)
            return

        latest = tasks[-1]
        if latest["status"] not in _TERMINAL:
            return  # a worker is still on it

        if latest["status"] == TaskStatus.FAILED.value:
            self._fail(run_id, f"{latest['agent']} failed: {latest.get('error')}")
            return

        # latest task succeeded — advance based on which role just finished.
        role = latest["agent"]
        if role == AgentRole.DESIGNER.value:
            self._enqueue_engineer(run, design=latest["output"])
        elif role == AgentRole.ENGINEER.value:
            self._enqueue_reviewer(run, tasks)
        elif role == AgentRole.REVIEWER.value:
            self._handle_review(run, tasks, review=latest["output"])

    # --- stage transitions ------------------------------------------------

    def _enqueue_design(self, run: dict[str, Any]) -> None:
        spec = run["spec"]
        repo_files = self._safe_list_paths(spec)
        task_id = self._log.create_task(
            run["run_id"],
            AgentRole.DESIGNER,
            AgentRole.ORCHESTRATOR,
            inputs={
                "issue_title": spec.get("title", ""),
                "issue_body": spec.get("body", ""),
                "repo_files": repo_files,
            },
            seq=0,
        )
        self._queue.enqueue(AgentRole.DESIGNER, task_id)

    def _enqueue_engineer(
        self, run: dict[str, Any], design: dict[str, Any], feedback: str = ""
    ) -> None:
        inputs: dict[str, Any] = {
            "design": design,
            "issue_title": run["spec"].get("title", ""),
            "repo_files": {},
        }
        if feedback:
            inputs["feedback"] = feedback
        task_id = self._log.create_task(
            run["run_id"], AgentRole.ENGINEER, AgentRole.ORCHESTRATOR,
            inputs=inputs, max_steps=3, seq=1,
        )
        self._queue.enqueue(AgentRole.ENGINEER, task_id)

    def _enqueue_reviewer(
        self, run: dict[str, Any], tasks: list[dict[str, Any]]
    ) -> None:
        design = self._latest_output(tasks, AgentRole.DESIGNER)
        engineer = self._latest_output(tasks, AgentRole.ENGINEER)
        task_id = self._log.create_task(
            run["run_id"], AgentRole.REVIEWER, AgentRole.ORCHESTRATOR,
            inputs={
                "issue_title": run["spec"].get("title", ""),
                "design": design,
                "files": engineer.get("files", {}),
                "test_output": engineer.get("test_output", ""),
            },
            seq=2,
        )
        self._queue.enqueue(AgentRole.REVIEWER, task_id)

    def _handle_review(
        self, run: dict[str, Any], tasks: list[dict[str, Any]], review: dict[str, Any]
    ) -> None:
        if review.get("status") == "approved":
            self._finish_with_pr(run, tasks, review)
            return
        # rejected: re-engineer with feedback if we have attempts left.
        attempts = sum(1 for t in tasks if t["agent"] == AgentRole.ENGINEER.value)
        if attempts >= self._max_engineer_attempts:
            self._fail(run["run_id"], "reviewer rejected; engineer attempts exhausted")
            return
        design = self._latest_output(tasks, AgentRole.DESIGNER)
        feedback = review.get("feedback", "") + "\n" + "\n".join(
            review.get("requested_changes", [])
        )
        self._enqueue_engineer(run, design=design, feedback=feedback)

    # --- finalize via GitHub ---------------------------------------------

    def _finish_with_pr(
        self, run: dict[str, Any], tasks: list[dict[str, Any]], review: dict[str, Any]
    ) -> None:
        spec = run["spec"]
        owner, repo = spec["owner"], spec["repo"]
        base = spec.get("base_branch", "main")
        design = self._latest_output(tasks, AgentRole.DESIGNER)
        engineer = self._latest_output(tasks, AgentRole.ENGINEER)
        head = branch_name(spec.get("title", "feature"), run["run_id"])

        self._gh.create_branch(owner, repo, head, base)
        self._gh.commit_files(
            owner, repo, head, engineer["files"],
            f"feat: {spec.get('title', 'autonomous change')}",
        )
        pr = self._gh.create_pull_request(
            owner, repo,
            title=spec.get("title", "Autonomous change"),
            head=head, base=base,
            body=pr_body(spec, design, review, run["total_cost_tokens"]),
        )
        if spec.get("auto_merge"):
            self._gh.merge_pull_request(owner, repo, pr.number)

        self._log.set_run_status(
            run["run_id"], RunStatus.SUCCESS, final_pr_url=pr.html_url
        )

    # --- helpers ----------------------------------------------------------

    def _fail(self, run_id: UUID, reason: str) -> None:
        log.warning("run %s failed: %s", run_id, reason)
        self._log.set_run_status(run_id, RunStatus.FAILED)

    @staticmethod
    def _latest_output(
        tasks: list[dict[str, Any]], role: AgentRole
    ) -> dict[str, Any]:
        for task in reversed(tasks):
            if task["agent"] == role.value and task["status"] == TaskStatus.DONE.value:
                return task["output"] or {}
        return {}

    def _safe_list_paths(self, spec: dict[str, Any]) -> list[str]:
        try:
            return self._gh.list_paths(
                spec["owner"], spec["repo"], spec.get("base_branch", "main")
            )
        except Exception:  # noqa: BLE001 - design can proceed without the listing
            log.warning("could not list repo paths; proceeding without")
            return []
