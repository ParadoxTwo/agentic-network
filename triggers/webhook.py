"""Webhook + manual API trigger (FastAPI).

Two ways to start a run:
- POST /runs           — explicit request ("add feature X to repo Y").
- POST /webhook/github — GitHub 'issues opened' events (optional HMAC verify).

A fresh TaskLog is built per request via `make_tasklog`, because a psycopg
connection isn't safe to share across FastAPI's threadpool workers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from store.tasklog import TaskLog

MakeTaskLog = Callable[[], TaskLog]


class CreateRunRequest(BaseModel):
    owner: str
    repo: str
    title: str
    body: str = ""
    base_branch: str = "main"
    issue_number: int = 0
    auto_merge: bool = False
    created_by: str = "api"


def _verify(secret: str, body: bytes, signature: str | None) -> None:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="invalid signature")


def create_app(make_tasklog: MakeTaskLog, *, webhook_secret: str | None = None) -> FastAPI:
    app = FastAPI(title="agentic-network triggers")

    @app.post("/runs")
    def create_run(req: CreateRunRequest) -> dict[str, str]:
        spec = req.model_dump(exclude={"created_by"})
        log = make_tasklog()
        try:
            run_id = log.create_run(req.issue_number, req.created_by, spec)
        finally:
            log.close()
        return {"run_id": str(run_id)}

    @app.post("/webhook/github")
    async def github_webhook(
        request: Request,
        x_hub_signature_256: str | None = Header(default=None),
        x_github_event: str | None = Header(default=None),
    ) -> dict[str, object]:
        raw = await request.body()
        if webhook_secret:
            _verify(webhook_secret, raw, x_hub_signature_256)
        payload = json.loads(raw or b"{}")
        if x_github_event != "issues" or payload.get("action") != "opened":
            return {"ignored": True}

        issue = payload["issue"]
        repo = payload["repository"]
        spec = {
            "owner": repo["owner"]["login"],
            "repo": repo["name"],
            "base_branch": repo.get("default_branch", "main"),
            "issue_number": issue["number"],
            "title": issue["title"],
            "body": issue.get("body") or "",
            "auto_merge": False,
        }
        log = make_tasklog()
        try:
            run_id = log.create_run(issue["number"], "webhook", spec)
        finally:
            log.close()
        return {"run_id": str(run_id)}

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        from uuid import UUID

        log = make_tasklog()
        try:
            run = log.get_run(UUID(run_id))
        finally:
            log.close()
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return {
            "run_id": run_id,
            "status": run["status"],
            "final_pr_url": run["final_pr_url"],
            "total_cost_tokens": run["total_cost_tokens"],
        }

    return app
