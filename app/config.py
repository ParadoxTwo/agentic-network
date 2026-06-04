"""Runtime configuration, read from the environment.

Model tiers follow the plan: stronger model + review authority for the
Reviewer; faster model for the execution specialists. The orchestrator itself
runs no LLM — it's a deterministic state machine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    github_token: str
    designer_model: str = "claude-sonnet-4-6"
    engineer_model: str = "claude-sonnet-4-6"
    reviewer_model: str = "claude-opus-4-8"
    poll_interval_s: float = 5.0
    webhook_secret: str | None = None

    @classmethod
    def from_env(cls) -> Config:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise RuntimeError("GITHUB_TOKEN is required to run the service")
        return cls(
            github_token=token,
            designer_model=os.environ.get("DESIGNER_MODEL", cls.designer_model),
            engineer_model=os.environ.get("ENGINEER_MODEL", cls.engineer_model),
            reviewer_model=os.environ.get("REVIEWER_MODEL", cls.reviewer_model),
            poll_interval_s=float(os.environ.get("POLL_INTERVAL_S", "5")),
            webhook_secret=os.environ.get("WEBHOOK_SECRET"),
        )
