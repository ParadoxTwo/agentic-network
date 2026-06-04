"""Agent tests with a fake LLM and the real LocalExecutor."""

from __future__ import annotations

import json
from collections.abc import Sequence

from agents.designer import Designer
from agents.engineer import Engineer
from agents.executor import LocalExecutor
from agents.llm import LLMResult, parse_json
from agents.reviewer import Reviewer


class FakeLLM:
    """Returns scripted responses. With one response left, repeats it (retries)."""

    def __init__(self, responses: Sequence[str]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    def complete(self, *, system: str, prompt: str, max_tokens: int = 4096) -> LLMResult:
        self.calls.append(prompt)
        text = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        return LLMResult(text=text, input_tokens=10, output_tokens=5)


# --- parse_json ----------------------------------------------------------

def test_parse_json_tolerates_fences_and_prose() -> None:
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure!\n{"b": 2}\nHope that helps') == {"b": 2}


def test_parse_json_raises_when_absent() -> None:
    import pytest

    with pytest.raises(ValueError):
        parse_json("no json here")


# --- Designer ------------------------------------------------------------

def test_designer_returns_parsed_design() -> None:
    design = {"approach": "add a handler", "files": [], "test_plan": "x", "risks": []}
    agent = Designer(FakeLLM([json.dumps(design)]))
    res = agent.run({"issue_title": "add X", "issue_body": "...", "repo_files": ["a.py"]})
    assert res.ok
    assert res.output["approach"] == "add a handler"
    assert res.cost_tokens == 15


def test_designer_flags_unparseable_output() -> None:
    res = Designer(FakeLLM(["I cannot help with that"])).run({"issue_title": "x"})
    assert not res.ok
    assert "unparseable" in (res.error or "")


# --- Engineer (runs real subprocess via LocalExecutor) -------------------

def test_engineer_succeeds_when_tests_pass() -> None:
    response = json.dumps(
        {
            "files": {"check.py": "assert 1 + 1 == 2\nprint('ok')"},
            "test_command": ["python", "check.py"],
            "summary": "trivial",
        }
    )
    agent = Engineer(FakeLLM([response]), LocalExecutor(timeout_s=30))
    res = agent.run({"design": {"approach": "x"}})
    assert res.ok
    assert res.output["summary"] == "trivial"
    assert "check.py" in res.output["files"]


def test_engineer_retries_then_fails_when_tests_never_pass() -> None:
    response = json.dumps(
        {"files": {"check.py": "assert False"}, "test_command": ["python", "check.py"]}
    )
    llm = FakeLLM([response])
    agent = Engineer(llm, LocalExecutor(timeout_s=30), max_attempts=3)
    res = agent.run({"design": {}})
    assert not res.ok
    assert "did not pass" in (res.error or "")
    assert len(llm.calls) == 3  # exhausted all attempts


# --- Reviewer ------------------------------------------------------------

def test_reviewer_approves() -> None:
    verdict = json.dumps({"status": "approved", "feedback": "lgtm", "requested_changes": []})
    res = Reviewer(FakeLLM([verdict])).run({"design": {}, "files": {}, "test_output": "ok"})
    assert res.ok
    assert res.output["status"] == "approved"


def test_reviewer_rejects_with_changes() -> None:
    verdict = json.dumps(
        {"status": "rejected", "feedback": "missing tests", "requested_changes": ["add tests"]}
    )
    res = Reviewer(FakeLLM([verdict])).run({})
    assert res.output["status"] == "rejected"
    assert res.output["requested_changes"] == ["add tests"]


def test_reviewer_flags_invalid_status() -> None:
    res = Reviewer(FakeLLM(['{"status": "maybe"}'])).run({})
    assert not res.ok
