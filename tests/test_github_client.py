"""Unit tests for the GitHub client against a mocked transport (no network)."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from github.client import GitHubClient, GitHubError


def _client(handler) -> GitHubClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://api.github.com")
    return GitHubClient("test-token", client=http)


def test_get_issue_parses_labels() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/repos/o/r/issues/7"
        return httpx.Response(
            200,
            json={
                "number": 7,
                "title": "add login",
                "body": "we need auth",
                "state": "open",
                "labels": [{"name": "feature"}, {"name": "p1"}],
            },
        )

    issue = _client(handler).get_issue("o", "r", 7)
    assert issue.number == 7
    assert issue.title == "add login"
    assert issue.labels == ["feature", "p1"]


def test_list_open_issues_filters_out_prs() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"number": 1, "title": "real issue", "state": "open", "labels": []},
                {
                    "number": 2,
                    "title": "a PR",
                    "state": "open",
                    "labels": [],
                    "pull_request": {"url": "..."},
                },
            ],
        )

    issues = _client(handler).list_open_issues("o", "r")
    assert [i.number for i in issues] == [1]


def test_create_branch_uses_base_sha() -> None:
    posted = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and "git/ref/heads/main" in req.url.path:
            return httpx.Response(200, json={"object": {"sha": "base123"}})
        if req.method == "POST" and req.url.path.endswith("/git/refs"):
            posted.update(json.loads(req.content))
            return httpx.Response(201, json={})
        raise AssertionError(req.url.path)

    sha = _client(handler).create_branch("o", "r", "feature/x", "main")
    assert sha == "base123"
    assert posted == {"ref": "refs/heads/feature/x", "sha": "base123"}


def test_get_file_decodes_and_handles_404() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if "exists.py" in req.url.path:
            content = base64.b64encode(b"print('hi')").decode()
            return httpx.Response(200, json={"content": content})
        return httpx.Response(404, json={"message": "Not Found"})

    gh = _client(handler)
    assert gh.get_file("o", "r", "exists.py", ref="main") == "print('hi')"
    assert gh.get_file("o", "r", "missing.py", ref="main") is None


def test_commit_files_makes_one_commit_and_moves_ref() -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        calls.append((req.method, p))
        if req.method == "GET" and "git/ref/heads/feature" in p:
            return httpx.Response(200, json={"object": {"sha": "c0"}})
        if req.method == "GET" and "git/commits/c0" in p:
            return httpx.Response(200, json={"tree": {"sha": "t0"}})
        if req.method == "POST" and p.endswith("/git/trees"):
            body = json.loads(req.content)
            assert body["base_tree"] == "t0"
            assert {f["path"] for f in body["tree"]} == {"a.py", "b.py"}
            return httpx.Response(201, json={"sha": "t1"})
        if req.method == "POST" and p.endswith("/git/commits"):
            body = json.loads(req.content)
            assert body["parents"] == ["c0"]
            return httpx.Response(201, json={"sha": "c1"})
        if req.method == "PATCH" and "git/refs/heads/feature" in p:
            assert json.loads(req.content) == {"sha": "c1"}
            return httpx.Response(200, json={})
        raise AssertionError(p)

    sha = _client(handler).commit_files(
        "o", "r", "feature", {"a.py": "1", "b.py": "2"}, "add files"
    )
    assert sha == "c1"
    assert ("PATCH", "/repos/o/r/git/refs/heads/feature") in calls


def test_create_and_merge_pr() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path.endswith("/pulls"):
            return httpx.Response(
                201,
                json={
                    "number": 12,
                    "html_url": "https://github.com/o/r/pull/12",
                    "head": {"ref": "feature/x"},
                    "base": {"ref": "main"},
                },
            )
        if req.method == "PUT" and req.url.path.endswith("/12/merge"):
            return httpx.Response(200, json={"merged": True})
        raise AssertionError(req.url.path)

    gh = _client(handler)
    pr = gh.create_pull_request(
        "o", "r", title="Add X", head="feature/x", base="main", body="b"
    )
    assert pr.number == 12
    assert pr.html_url.endswith("/pull/12")
    assert gh.merge_pull_request("o", "r", 12) is True


def test_error_surfaces_status_and_body() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "Validation failed"})

    with pytest.raises(GitHubError) as ei:
        _client(handler).create_pull_request(
            "o", "r", title="x", head="h", base="main"
        )
    assert ei.value.status == 422
    assert ei.value.body["message"] == "Validation failed"
