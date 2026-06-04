"""LLM access for agents.

A small Protocol (`LLM`) is all the agents depend on, so tests inject a fake
and never touch the network or need an API key. `AnthropicLLM` is the real
implementation over the Anthropic SDK; it's only constructed in production.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from pydantic import BaseModel


class LLMResult(BaseModel):
    text: str
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def cost_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLM(Protocol):
    """The only LLM surface agents use."""

    def complete(self, *, system: str, prompt: str, max_tokens: int = 4096) -> LLMResult:
        ...


class AnthropicLLM:
    """Real Claude access. Reads ANTHROPIC_API_KEY from the environment."""

    def __init__(self, model: str, client: Any | None = None) -> None:
        if client is None:
            from anthropic import Anthropic

            client = Anthropic()
        self._client = client
        self._model = model

    def complete(self, *, system: str, prompt: str, max_tokens: int = 4096) -> LLMResult:
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            getattr(block, "text", "") for block in msg.content
            if getattr(block, "type", None) == "text"
        )
        return LLMResult(
            text=text,
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
        )


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from a model response.

    Tolerates ```json fences and surrounding prose by falling back to the
    first balanced-looking {...} span. Raises ValueError if none parses.
    """
    fenced = _JSON_FENCE.search(text)
    candidates = []
    if fenced:
        candidates.append(fenced.group(1))
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    raise ValueError(f"no JSON object found in response: {text[:200]!r}")
