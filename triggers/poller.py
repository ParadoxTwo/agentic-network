"""Polling trigger: turn new GitHub issues into runs.

The fallback path when webhooks aren't configured. Lists open issues (optionally
by label), skips any that already have a run, and creates runs for the rest.
Idempotent: re-polling never double-creates a run for the same issue.
"""

from __future__ import annotations

import logging
from uuid import UUID

from github.client import GitHubClient
from store.tasklog import TaskLog

log = logging.getLogger("poller")


class IssuePoller:
    def __init__(
        self,
        github: GitHubClient,
        tasklog: TaskLog,
        *,
        owner: str,
        repo: str,
        base_branch: str = "main",
        label: str | None = None,
        auto_merge: bool = False,
        created_by: str = "poller",
    ) -> None:
        self._gh = github
        self._log = tasklog
        self._owner = owner
        self._repo = repo
        self._base = base_branch
        self._label = label
        self._auto_merge = auto_merge
        self._created_by = created_by

    def poll_once(self) -> list[UUID]:
        """Create runs for issues that don't have one yet. Returns new run_ids."""
        seen = self._log.issue_ids_with_runs()
        created: list[UUID] = []
        for issue in self._gh.list_open_issues(self._owner, self._repo, labels=self._label):
            if issue.number in seen:
                continue
            spec = {
                "owner": self._owner,
                "repo": self._repo,
                "base_branch": self._base,
                "issue_number": issue.number,
                "title": issue.title,
                "body": issue.body,
                "auto_merge": self._auto_merge,
            }
            run_id = self._log.create_run(issue.number, self._created_by, spec)
            created.append(run_id)
            log.info("created run %s for issue #%s", run_id, issue.number)
        return created
