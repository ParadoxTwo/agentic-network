"""CLI trigger: start a run from the command line.

    python -m triggers.cli --owner o --repo r --title "Add login" \
        --body "We need auth" --base main [--issue 12] [--auto-merge]

Creates a pending run in the store; the orchestrator service picks it up.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from store.db import apply_schema, connect_pg
from store.tasklog import TaskLog


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="triggers.cli", description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", default="")
    parser.add_argument("--base", default="main", help="base branch")
    parser.add_argument("--issue", type=int, default=0, help="GitHub issue number")
    parser.add_argument("--auto-merge", action="store_true")
    parser.add_argument("--created-by", default="cli")
    args = parser.parse_args(argv)

    conn = connect_pg()
    apply_schema(conn)
    log = TaskLog(conn)
    spec = {
        "owner": args.owner,
        "repo": args.repo,
        "base_branch": args.base,
        "issue_number": args.issue,
        "title": args.title,
        "body": args.body,
        "auto_merge": args.auto_merge,
    }
    run_id = log.create_run(args.issue, args.created_by, spec)
    log.close()
    print(run_id)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
