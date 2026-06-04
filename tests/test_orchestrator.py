"""End-to-end orchestration: store + queue + workers + agents + fake GitHub.

Drives a run through the real state machine. The only fakes are the LLM (no
API key) and GitHub (no network) — everything else is the real code path.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest

from agents.designer import Designer
from agents.engineer import Engineer
from agents.executor import LocalExecutor
from agents.llm import LLMResult
from agents.reviewer import Reviewer
from github.client import PullRequest
from orchestrator.orchestrator import Orchestrator
from orchestrator.worker import Worker
from schemas.message import AgentRole, RunStatus
from store.queue import TaskQueue
from store.tasklog import TaskLog

pytestmark = pytest.mark.integration


class ScriptedLLM:
    def __init__(self, responses: Sequence[str]) -> None:
        self._responses = list(responses)

    def complete(self, *, system: str, prompt: str, max_tokens: int = 4096) -> LLMResult:
        text = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        return LLMResult(text=text, input_tokens=100, output_tokens=50)


class FakeGitHub:
    def __init__(self) -> None:
        self.branches: list[str] = []
        self.commits: list[tuple[str, dict[str, str]]] = []
        self.prs: list[PullRequest] = []
        self.merged = False

    def list_paths(self, owner: str, repo: str, ref: str) -> list[str]:
        return ["README.md", "app.py"]

    def create_branch(self, owner: str, repo: str, branch: str, from_branch: str) -> str:
        self.branches.append(branch)
        return "basesha"

    def commit_files(self, owner: str, repo: str, branch: str,
                     files: dict[str, str], message: str) -> str:
        self.commits.append((branch, files))
        return "commitsha"

    def create_pull_request(self, owner: str, repo: str, *, title: str,
                            head: str, base: str, body: str = "") -> PullRequest:
        pr = PullRequest(
            number=99, html_url="https://github.com/o/r/pull/99", head=head, base=base
        )
        self.prs.append(pr)
        return pr

    def merge_pull_request(self, owner: str, repo: str, number: int,
                           *, method: str = "squash") -> bool:
        self.merged = True
        return True


DESIGN = json.dumps(
    {"approach": "add a greet() function", "files": [{"path": "app.py", "change": "modify"}],
     "test_plan": "assert greet returns hello", "risks": []}
)
CODE_PASS = json.dumps(
    {"files": {"check.py": "assert 'hello' == 'hello'\nprint('ok')"},
     "test_command": ["python", "check.py"], "summary": "add greeting"}
)
CODE_FAIL = json.dumps(
    {"files": {"check.py": "assert False"}, "test_command": ["python", "check.py"]}
)
APPROVE = json.dumps({"status": "approved", "feedback": "looks good", "requested_changes": []})
REJECT = json.dumps(
    {"status": "rejected", "feedback": "needs a real test", "requested_changes": ["add test"]}
)


def _spec(**over: Any) -> dict[str, Any]:
    base = {"owner": "o", "repo": "r", "base_branch": "main", "issue_number": 5,
            "title": "Add greeting", "body": "We want a greeting", "auto_merge": True}
    base.update(over)
    return base


def _drive(orch: Orchestrator, workers: dict[AgentRole, Worker], queue: TaskQueue,
           log: TaskLog, run_id, max_iter: int = 40) -> dict[str, Any]:
    """Tick the orchestrator and run whichever worker has a queued task."""
    for _ in range(max_iter):
        orch.tick()
        for role, worker in workers.items():
            if queue.depth(role) > 0:
                worker.run_once(timeout=1)
        run = log.get_run(run_id)
        assert run is not None
        if run["status"] in (RunStatus.SUCCESS.value, RunStatus.FAILED.value):
            return run
    return log.get_run(run_id)  # type: ignore[return-value]


def _build(pg, rds, *, design=DESIGN, code=CODE_PASS, review=APPROVE):
    log = TaskLog(pg)
    queue = TaskQueue(rds)
    workers = {
        AgentRole.DESIGNER: Worker(AgentRole.DESIGNER, Designer(ScriptedLLM([design])), log, queue),
        AgentRole.ENGINEER: Worker(
            AgentRole.ENGINEER,
            Engineer(ScriptedLLM([code] if isinstance(code, str) else code),
                     LocalExecutor(timeout_s=30), max_attempts=2),
            log, queue,
        ),
        AgentRole.REVIEWER: Worker(
            AgentRole.REVIEWER,
            Reviewer(ScriptedLLM([review] if isinstance(review, str) else review)),
            log, queue,
        ),
    }
    return log, queue, workers


def test_full_run_to_merged_pr(pg, rds) -> None:
    log, queue, workers = _build(pg, rds)
    gh = FakeGitHub()
    orch = Orchestrator(log, queue, gh)  # type: ignore[arg-type]

    run_id = log.create_run(issue_id=5, created_by="owner", spec=_spec())
    run = _drive(orch, workers, queue, log, run_id)

    assert run["status"] == RunStatus.SUCCESS.value
    assert run["final_pr_url"] == "https://github.com/o/r/pull/99"
    assert run["total_cost_tokens"] > 0
    assert len(gh.branches) == 1 and gh.branches[0].startswith("agentic/add-greeting-")
    assert gh.commits and "check.py" in gh.commits[0][1]
    assert gh.merged is True


def test_reviewer_rejection_then_retry_succeeds(pg, rds) -> None:
    # Reviewer rejects the first submission, approves the second.
    log, queue, workers = _build(pg, rds, review=[REJECT, APPROVE])
    gh = FakeGitHub()
    orch = Orchestrator(log, queue, gh, max_engineer_attempts=3)  # type: ignore[arg-type]

    run_id = log.create_run(issue_id=5, created_by="owner", spec=_spec(auto_merge=False))
    run = _drive(orch, workers, queue, log, run_id)

    assert run["status"] == RunStatus.SUCCESS.value
    engineer_tasks = [t for t in log.list_tasks(run_id) if t["agent"] == "engineer"]
    assert len(engineer_tasks) == 2  # retried after rejection
    assert gh.merged is False  # auto_merge off -> PR left open


def test_engineer_failure_fails_run(pg, rds) -> None:
    log, queue, workers = _build(pg, rds, code=CODE_FAIL)
    gh = FakeGitHub()
    orch = Orchestrator(log, queue, gh)  # type: ignore[arg-type]

    run_id = log.create_run(issue_id=5, created_by="owner", spec=_spec())
    run = _drive(orch, workers, queue, log, run_id)

    assert run["status"] == RunStatus.FAILED.value
    assert not gh.prs  # never reached PR creation
