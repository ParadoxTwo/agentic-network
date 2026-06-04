"""Tests for the trigger paths: CLI, poller, webhook + the service wiring."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Sequence

import pytest
from fastapi.testclient import TestClient

from agents.llm import LLMResult
from app.service import build_agents
from github.client import Issue
from schemas.message import AgentRole
from store.tasklog import TaskLog
from triggers.poller import IssuePoller
from triggers.webhook import create_app


# --- service wiring (no DB) ----------------------------------------------

class _FakeLLM:
    def complete(self, *, system: str, prompt: str, max_tokens: int = 4096) -> LLMResult:
        return LLMResult(text="{}")


def test_build_agents_uses_factory_for_each_role() -> None:
    seen: list[AgentRole] = []

    def llm_for(role: AgentRole) -> _FakeLLM:
        seen.append(role)
        return _FakeLLM()

    agents = build_agents(llm_for)
    assert set(agents) == {AgentRole.DESIGNER, AgentRole.ENGINEER, AgentRole.REVIEWER}
    assert agents[AgentRole.REVIEWER].role == AgentRole.REVIEWER
    assert set(seen) == {AgentRole.DESIGNER, AgentRole.ENGINEER, AgentRole.REVIEWER}


# --- poller --------------------------------------------------------------

class _FakePollGitHub:
    def __init__(self, issues: Sequence[Issue]) -> None:
        self._issues = list(issues)

    def list_open_issues(self, owner: str, repo: str, *, labels: str | None = None) -> list[Issue]:
        return list(self._issues)


@pytest.mark.integration
def test_poller_creates_runs_then_dedups(pg) -> None:
    log = TaskLog(pg)
    gh = _FakePollGitHub(
        [
            Issue(number=1, title="Add A", body="a", state="open"),
            Issue(number=2, title="Add B", body="b", state="open"),
        ]
    )
    poller = IssuePoller(gh, log, owner="o", repo="r")  # type: ignore[arg-type]

    first = poller.poll_once()
    assert len(first) == 2
    # Re-polling the same open issues creates nothing new.
    assert poller.poll_once() == []


# --- webhook / API -------------------------------------------------------

@pytest.mark.integration
def test_post_runs_creates_pending_run(tasklog_factory, pg) -> None:
    client = TestClient(create_app(tasklog_factory))
    resp = client.post(
        "/runs",
        json={"owner": "o", "repo": "r", "title": "Add login", "body": "auth"},
    )
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    run = TaskLog(pg).get_run(uuid.UUID(run_id))
    assert run is not None
    assert run["status"] == "pending"
    assert run["spec"]["title"] == "Add login"


@pytest.mark.integration
def test_github_webhook_opened_creates_run(tasklog_factory) -> None:
    client = TestClient(create_app(tasklog_factory))
    payload = {
        "action": "opened",
        "issue": {"number": 7, "title": "Bug", "body": "broken"},
        "repository": {"name": "r", "owner": {"login": "o"}, "default_branch": "main"},
    }
    resp = client.post(
        "/webhook/github", json=payload, headers={"X-GitHub-Event": "issues"}
    )
    assert resp.status_code == 200
    assert "run_id" in resp.json()


@pytest.mark.integration
def test_github_webhook_ignores_non_opened(tasklog_factory) -> None:
    client = TestClient(create_app(tasklog_factory))
    resp = client.post(
        "/webhook/github",
        json={"action": "closed", "issue": {"number": 1}},
        headers={"X-GitHub-Event": "issues"},
    )
    assert resp.json() == {"ignored": True}


@pytest.mark.integration
def test_webhook_signature_enforced(tasklog_factory) -> None:
    secret = "s3cret"
    client = TestClient(create_app(tasklog_factory, webhook_secret=secret))
    payload = {
        "action": "opened",
        "issue": {"number": 9, "title": "X", "body": ""},
        "repository": {"name": "r", "owner": {"login": "o"}, "default_branch": "main"},
    }
    raw = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": "issues", "Content-Type": "application/json"}

    # No signature -> rejected.
    assert client.post("/webhook/github", content=raw, headers=headers).status_code == 401

    # Correct signature -> accepted.
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    ok = client.post(
        "/webhook/github", content=raw, headers={**headers, "X-Hub-Signature-256": sig}
    )
    assert ok.status_code == 200


@pytest.mark.integration
def test_get_run_endpoint_and_404(tasklog_factory) -> None:
    client = TestClient(create_app(tasklog_factory))
    run_id = client.post(
        "/runs", json={"owner": "o", "repo": "r", "title": "T"}
    ).json()["run_id"]

    got = client.get(f"/runs/{run_id}")
    assert got.status_code == 200
    assert got.json()["status"] == "pending"

    assert client.get(f"/runs/{uuid.uuid4()}").status_code == 404


# --- CLI -----------------------------------------------------------------

@pytest.mark.integration
def test_cli_creates_run(monkeypatch, capsys, pg) -> None:
    # Point the CLI's default connection at the test DB.
    monkeypatch.setenv("POSTGRES_DB", "agentic_test")
    from triggers.cli import main

    rc = main(["--owner", "o", "--repo", "r", "--title", "Add Z", "--body", "b"])
    assert rc == 0
    run_id = capsys.readouterr().out.strip()

    run = TaskLog(pg).get_run(uuid.UUID(run_id))
    assert run is not None
    assert run["spec"]["title"] == "Add Z"
