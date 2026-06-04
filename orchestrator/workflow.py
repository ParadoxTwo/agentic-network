"""Feature-delivery workflow: shared constants and pure helpers.

The state machine itself lives in orchestrator.py; this module holds the
naming and formatting bits so they're easy to test and tweak.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

# Ordered stages of the workflow. The orchestrator advances through these.
STAGES = ("designer", "engineer", "reviewer")


def branch_name(title: str, run_id: UUID) -> str:
    """A readable, collision-free head branch for a run."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "feature"
    return f"agentic/{slug}-{str(run_id)[:8]}"


def pr_body(spec: dict[str, Any], design: dict[str, Any], review: dict[str, Any],
            total_cost_tokens: int) -> str:
    """A PR description carrying the trace: design summary, review, cost."""
    issue = spec.get("issue_number")
    lines = [
        f"Implements: **{spec.get('title', '(untitled)')}**",
        "",
        f"Closes #{issue}" if issue else "",
        "",
        "### Design",
        design.get("approach", "_n/a_"),
        "",
        "### Review",
        review.get("feedback", "_n/a_"),
        "",
        "---",
        f"_Autonomous run · {total_cost_tokens} tokens · "
        f"agentic-network_",
    ]
    return "\n".join(line for line in lines if line is not None)
