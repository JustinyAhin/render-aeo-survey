"""Prompt sets and ground truth, stored as JSON in aeo/data."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


@cache
def load_prompt_set(name: str) -> dict:
    path = DATA / "prompt_sets" / f"{name}.json"
    if not path.exists():
        known = ", ".join(p.stem for p in (DATA / "prompt_sets").glob("*.json"))
        raise ValueError(f"Unknown prompt set {name!r}. Known: {known}")
    return json.loads(path.read_text())


@cache
def load_ground_truth() -> dict:
    return json.loads((DATA / "ground_truth.json").read_text())
