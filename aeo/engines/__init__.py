"""Engine registry. Add an engine by appending an Engine instance to a list here."""

from __future__ import annotations

from .base import Engine, EngineError, RawResult
from .openrouter import OPENROUTER_ENGINES
from .treg import TREG_ENGINES

ENGINES: dict[str, Engine] = {e.name: e for e in [*TREG_ENGINES, *OPENROUTER_ENGINES]}
DEFAULT_ENGINES = ["chatgpt", "gemini", "google_ai_mode", "perplexity", "claude"]


def get_engine(name: str) -> Engine:
    try:
        return ENGINES[name]
    except KeyError:
        raise ValueError(f"Unknown engine {name!r}. Known: {', '.join(ENGINES)}") from None


__all__ = ["ENGINES", "DEFAULT_ENGINES", "Engine", "EngineError", "RawResult", "get_engine"]
