"""Agent contract: the typed boundary every specialist implements."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from schemas.message import AgentRole


class AgentResult(BaseModel):
    """What an agent returns to the worker, which writes it to the task log."""

    output: dict[str, Any]
    cost_tokens: int = 0
    ok: bool = True
    error: str | None = None


class Agent(Protocol):
    """A specialist. Stateless: all it needs arrives in `inputs`."""

    role: AgentRole

    def run(self, inputs: dict[str, Any]) -> AgentResult:
        ...
