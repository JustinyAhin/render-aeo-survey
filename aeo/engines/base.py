"""Engine interface shared by all adapters.

An engine sends one prompt to one answer engine and returns the raw provider
response plus what it cost. `parse` turns that raw response into answer text
and the list of cited URLs. Analysis only ever sees (text, urls), so new
engines plug in without touching the metrics code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class EngineError(Exception):
    """A failed engine call.

    `retryable` is True for timeouts, 429s and 5xx responses. The workflow
    raises on those so Render retries the task run; for other errors
    (bad request, missing key) it records the failure and moves on.
    """

    def __init__(self, message: str, *, retryable: bool, cost_usd: float = 0.0):
        super().__init__(message)
        self.retryable = retryable
        self.cost_usd = cost_usd


@dataclass
class RawResult:
    raw: Any
    cost_usd: float | None
    meta: dict = field(default_factory=dict)


class Engine:
    """Base class. Subclasses set `name`, and implement `ask` and `parse`."""

    name: str = ""
    # Engines with a high cost per call only run prompts marked "core" in the prompt set.
    core_only: bool = False
    # Shown in reports so readers know what was measured.
    description: str = ""
    # Environment variables the engine needs. `missing_env()` checks them.
    required_env: tuple[str, ...] = ()

    def ask(self, prompt: str, *, idempotency_key: str) -> RawResult:
        raise NotImplementedError

    def parse(self, raw: Any) -> tuple[str, list[str]]:
        raise NotImplementedError

    def missing_env(self) -> list[str]:
        import os

        return [k for k in self.required_env if not os.environ.get(k)]


def dedupe(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def status_error(provider: str, status: int, body: str) -> EngineError:
    retryable = status == 429 or status >= 500
    return EngineError(f"{provider} HTTP {status}: {body[:300]}", retryable=retryable)
