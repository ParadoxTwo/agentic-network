"""Reviewer agent: the verification gate with authority to reject.

Single objective: judge the design + code + test output against a rubric and
return approve/reject with actionable feedback. This is the "senior" authority
in the workflow — a stronger model and the power to send work back.
"""

from __future__ import annotations

import json
from typing import Any

from agents.base import AgentResult
from agents.llm import LLM, parse_json
from schemas.message import AgentRole

_SYSTEM = """You are a staff engineer reviewing an autonomous teammate's work
before it can become a pull request. Judge it against this rubric:
- Does the code implement the design and the feature request?
- Are there tests that meaningfully cover the change, and did they pass?
- Is the change minimal, clear, and free of obvious bugs or security issues?
Respond with ONLY a JSON object:
{
  "status": "approved" | "rejected",
  "feedback": "<concise justification>",
  "requested_changes": ["<specific change>", ...]
}
Reject if any rubric item fails. Be strict but fair."""


class Reviewer:
    role = AgentRole.REVIEWER

    def __init__(self, llm: LLM, max_tokens: int = 2048) -> None:
        self._llm = llm
        self._max_tokens = max_tokens

    def run(self, inputs: dict[str, Any]) -> AgentResult:
        prompt = (
            f"Feature request: {inputs.get('issue_title', '')}\n\n"
            f"Design:\n{json.dumps(inputs.get('design', {}), indent=2)}\n\n"
            f"Files changed:\n{json.dumps(inputs.get('files', {}), indent=2)[:8000]}\n\n"
            f"Test output:\n{inputs.get('test_output', '')[:4000]}"
        )
        result = self._llm.complete(
            system=_SYSTEM, prompt=prompt, max_tokens=self._max_tokens
        )
        try:
            review = parse_json(result.text)
            status = review["status"]
            if status not in ("approved", "rejected"):
                raise ValueError(f"invalid status {status!r}")
        except (ValueError, KeyError) as exc:
            return AgentResult(
                output={"raw": result.text},
                cost_tokens=result.cost_tokens,
                ok=False,
                error=f"reviewer returned invalid verdict: {exc}",
            )
        return AgentResult(
            output={
                "status": status,
                "feedback": review.get("feedback", ""),
                "requested_changes": review.get("requested_changes", []),
            },
            cost_tokens=result.cost_tokens,
        )
