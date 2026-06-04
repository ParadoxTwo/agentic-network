"""GitHub REST client (PAT auth).

Sync, dependency-injectable: pass your own httpx.Client (or a MockTransport in
tests) so every method is testable without hitting api.github.com. Covers the
capabilities the network needs: read issues, manage branches, commit files,
open/merge PRs, and create repos.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
from pydantic import BaseModel

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"


class Issue(BaseModel):
    number: int
    title: str
    body: str = ""
    state: str
    labels: list[str] = []


class PullRequest(BaseModel):
    number: int
    html_url: str
    head: str
    base: str
    merged: bool = False


class GitHubError(RuntimeError):
    """A non-2xx response from the GitHub API, with status + parsed body."""

    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"GitHub API {status}: {body}")
        self.status = status
        self.body = body


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        client: httpx.Client | None = None,
        base_url: str = API_BASE,
    ) -> None:
        self._owns_client = client is None
        self._http = client or httpx.Client(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
            },
            timeout=30.0,
        )

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- low level --------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._http.request(method, path, **kwargs)
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:  # noqa: BLE001
                body = resp.text
            raise GitHubError(resp.status_code, body)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # --- issues -----------------------------------------------------------

    def get_issue(self, owner: str, repo: str, number: int) -> Issue:
        data = self._request("GET", f"/repos/{owner}/{repo}/issues/{number}")
        return _to_issue(data)

    def list_open_issues(
        self, owner: str, repo: str, *, labels: str | None = None
    ) -> list[Issue]:
        params: dict[str, Any] = {"state": "open", "per_page": 100}
        if labels:
            params["labels"] = labels
        data = self._request("GET", f"/repos/{owner}/{repo}/issues", params=params)
        # The issues endpoint also returns PRs; filter them out.
        return [_to_issue(d) for d in data if "pull_request" not in d]

    # --- branches ---------------------------------------------------------

    def get_branch_sha(self, owner: str, repo: str, branch: str) -> str:
        data = self._request(
            "GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}"
        )
        return str(data["object"]["sha"])

    def create_branch(
        self, owner: str, repo: str, branch: str, from_branch: str
    ) -> str:
        """Create `branch` pointing at the tip of `from_branch`. Returns sha."""
        base_sha = self.get_branch_sha(owner, repo, from_branch)
        self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        return base_sha

    # --- files / commits --------------------------------------------------

    def get_file(
        self, owner: str, repo: str, path: str, *, ref: str
    ) -> str | None:
        """Return decoded file text, or None if it doesn't exist."""
        try:
            data = self._request(
                "GET",
                f"/repos/{owner}/{repo}/contents/{path}",
                params={"ref": ref},
            )
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise
        return base64.b64decode(data["content"]).decode()

    def commit_files(
        self,
        owner: str,
        repo: str,
        branch: str,
        files: dict[str, str],
        message: str,
    ) -> str:
        """Commit multiple files as ONE commit via the git data API.

        Returns the new commit sha. Creates a tree off the branch tip, a commit,
        then moves the ref — atomic from the caller's perspective.
        """
        base_commit = self.get_branch_sha(owner, repo, branch)
        commit = self._request(
            "GET", f"/repos/{owner}/{repo}/git/commits/{base_commit}"
        )
        base_tree = commit["tree"]["sha"]
        tree = self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/trees",
            json={
                "base_tree": base_tree,
                "tree": [
                    {"path": p, "mode": "100644", "type": "blob", "content": c}
                    for p, c in files.items()
                ],
            },
        )
        new_commit = self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/commits",
            json={
                "message": message,
                "tree": tree["sha"],
                "parents": [base_commit],
            },
        )
        new_sha = str(new_commit["sha"])
        self._request(
            "PATCH",
            f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
            json={"sha": new_sha},
        )
        return new_sha

    # --- pull requests ----------------------------------------------------

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        head: str,
        base: str,
        body: str = "",
    ) -> PullRequest:
        data = self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": body},
        )
        return _to_pr(data)

    def merge_pull_request(
        self, owner: str, repo: str, number: int, *, method: str = "squash"
    ) -> bool:
        data = self._request(
            "PUT",
            f"/repos/{owner}/{repo}/pulls/{number}/merge",
            json={"merge_method": method},
        )
        return bool(data and data.get("merged"))

    # --- repos ------------------------------------------------------------

    def create_repo(
        self, name: str, *, private: bool = True, org: str | None = None
    ) -> str:
        """Create a repo; returns its full_name. Org repo if `org` is given."""
        path = f"/orgs/{org}/repos" if org else "/user/repos"
        data = self._request(
            "POST", path, json={"name": name, "private": private, "auto_init": True}
        )
        return str(data["full_name"])


def _to_issue(data: dict[str, Any]) -> Issue:
    return Issue(
        number=data["number"],
        title=data["title"],
        body=data.get("body") or "",
        state=data["state"],
        labels=[lbl["name"] for lbl in data.get("labels", [])],
    )


def _to_pr(data: dict[str, Any]) -> PullRequest:
    return PullRequest(
        number=data["number"],
        html_url=data["html_url"],
        head=data["head"]["ref"],
        base=data["base"]["ref"],
        merged=bool(data.get("merged", False)),
    )
