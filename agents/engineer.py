"""Engineer agent: design spec -> code + passing tests.

Single objective: implement the design as concrete file contents and prove it
with tests. Calls the LLM to produce files, runs them through an Executor, and
on failure re-prompts with the test output — bounded by `max_attempts`. Only
succeeds (ok=True) when the tests actually pass: that's the verification gate.
"""

from __future__ import annotations

import json
from typing import Any

from agents.base import AgentResult
from agents.executor import ExecutionResult, LocalExecutor
from agents.llm import LLM, parse_json
from schemas.message import AgentRole

_SYSTEM = """You are a meticulous software engineer on an autonomous delivery
team. Implement the given design as complete file contents and include tests.
Respond with ONLY a JSON object of the form:
{
  "files": {"relative/path.py": "<full file content>", ...},
  "test_command": ["pytest", "-q"],
  "summary": "<one line of what you changed>"
}
Rules: provide FULL file contents (not diffs). Include tests that fail without
your change. Keep the change minimal and consistent with the existing code."""


class Engineer:
    role = AgentRole.ENGINEER

    def __init__(
        self,
        llm: LLM,
        executor: Any | None = None,
        *,
        max_attempts: int = 3,
        max_tokens: int = 8192,
    ) -> None:
        self._llm = llm
        self._executor = executor or LocalExecutor()
        self._max_attempts = max_attempts
        self._max_tokens = max_tokens

    def run(self, inputs: dict[str, Any]) -> AgentResult:
        design = inputs.get("design", {})
        repo_dir = inputs.get("repo_dir")
        base_prompt = (
            f"Design to implement:\n{json.dumps(design, indent=2)}\n\n"
            f"Existing files you may need (path -> content):\n"
            f"{json.dumps(inputs.get('repo_files', {}), indent=2)[:6000]}"
        )

        total_cost = 0
        feedback = ""
        last_exec: ExecutionResult | None = None
        files: dict[str, str] = {}
        command: list[str] = []
        for _ in range(self._max_attempts):
            prompt = base_prompt + (f"\n\nPrevious attempt failed:\n{feedback}" if feedback else "")
            result = self._llm.complete(
                system=_SYSTEM, prompt=prompt, max_tokens=self._max_tokens
            )
            total_cost += result.cost_tokens
            try:
                produced = parse_json(result.text)
                files = {str(k): str(v) for k, v in produced["files"].items()}
                command = list(produced.get("test_command", ["pytest", "-q"]))
            except (ValueError, KeyError, TypeError) as exc:
                feedback = f"Your response was not valid: {exc}. Return the exact JSON shape."
                continue

            last_exec = self._executor.run(files, command, repo_dir)
            if last_exec.passed:
                return AgentResult(
                    output={
                        "files": files,
                        "test_command": command,
                        "test_output": last_exec.output,
                        "summary": produced.get("summary", ""),
                    },
                    cost_tokens=total_cost,
                )
            feedback = f"Tests failed (exit {last_exec.returncode}):\n{last_exec.output}"

        return AgentResult(
            output={
                "files": files,
                "test_command": command,
                "test_output": last_exec.output if last_exec else "",
            },
            cost_tokens=total_cost,
            ok=False,
            error=f"tests did not pass after {self._max_attempts} attempts",
        )
