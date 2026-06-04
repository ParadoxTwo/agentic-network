"""Designer agent: feature request -> architecture spec.

Single objective: turn a feature request (+ optional repo context) into a
concrete, machine-readable plan the Engineer can implement. Reads only the
issue and a file listing; does not write code.
"""

from __future__ import annotations

from typing import Any

from agents.base import AgentResult
from agents.llm import LLM, parse_json
from schemas.message import AgentRole

_SYSTEM = """You are a senior software architect on an autonomous delivery team.
Given a feature request and a list of files in the target repository, produce a
concrete implementation plan. Respond with ONLY a JSON object of the form:
{
  "approach": "<2-4 sentence summary of how to implement this>",
  "files": [{"path": "relative/path", "change": "create|modify", "purpose": "..."}],
  "test_plan": "<what tests should prove this works>",
  "risks": ["<risk or open question>"]
}
Be specific and minimal. Only list files you are confident must change."""


class Designer:
    role = AgentRole.DESIGNER

    def __init__(self, llm: LLM, max_tokens: int = 2048) -> None:
        self._llm = llm
        self._max_tokens = max_tokens

    def run(self, inputs: dict[str, Any]) -> AgentResult:
        title = inputs.get("issue_title", "")
        body = inputs.get("issue_body", "")
        repo_files = inputs.get("repo_files", [])
        prompt = (
            f"Feature request: {title}\n\n{body}\n\n"
            f"Files in the repo:\n" + "\n".join(repo_files[:200])
        )
        result = self._llm.complete(
            system=_SYSTEM, prompt=prompt, max_tokens=self._max_tokens
        )
        try:
            design = parse_json(result.text)
        except ValueError as exc:
            return AgentResult(
                output={"raw": result.text},
                cost_tokens=result.cost_tokens,
                ok=False,
                error=f"designer returned unparseable JSON: {exc}",
            )
        return AgentResult(output=design, cost_tokens=result.cost_tokens)
