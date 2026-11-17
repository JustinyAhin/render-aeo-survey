"""Store, analyze_run, report and job planning, with no network calls."""

import json
from pathlib import Path

import pytest

from aeo import pipeline
from aeo.__main__ import import_raw
from aeo.engines import ENGINES, Engine, EngineError, RawResult
from aeo.storage import JsonStore

FIX = Path(__file__).parent / "fixtures" / "raw"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AEO_DATA_DIR", str(tmp_path))
    return JsonStore(tmp_path)


def test_first_run_is_baseline_second_run_diffs(store):
    import_raw(FIX, "run-1", "2026-11-10T05:00:00Z", "default")
    first = pipeline.analyze_run("run-1", store)
    assert first["previous_run_id"] is None
    assert "This run is the baseline" in first["report_markdown"]

    import_raw(FIX, "run-2", "2026-11-17T05:00:00Z", "default")
    a = json.loads((store.root / "run-2/answers/chatgpt/a01.json").read_text())
    a["text"] = "Use Railway or Fly.io."
    a["urls"] = []
    store.save_answer("run-2", a)
    second = pipeline.analyze_run("run-2", store)
    assert second["previous_run_id"] == "run-1"
    report = second["report_markdown"]
    assert "No longer names Render: chatgpt/a01" in report
    assert "No longer cites render.com: chatgpt/a01" in report
    assert store.get_run("run-2")["status"] == "completed"
    assert (store.root / "run-2" / "report.md").read_text() == report


def test_failed_answers_are_reported_not_analyzed(store):
    import_raw(FIX, "run-f", "2026-11-17T05:00:00Z", "default")
    prompt = {"id": "a02", "cluster": "a_stack", "prompt": "FastAPI?"}
    pipeline.record_failure("run-f", "chatgpt", prompt, "TaskRunError: boom", store)
    out = pipeline.analyze_run("run-f", store)
    assert out["failed"] == 1 and out["answers"] == 21
    assert "chatgpt / a02: TaskRunError: boom" in out["report_markdown"]


class FakeEngine(Engine):
    name = "fake"

    def __init__(self, error=None):
        self.error = error

    def ask(self, prompt, *, idempotency_key):
        if self.error:
            raise self.error
        return RawResult(raw={"t": f"Try Render. key={idempotency_key}"}, cost_usd=0.001)

    def parse(self, raw):
        return raw["t"], ["https://render.com/docs/free"]


def test_ask_and_store(store, monkeypatch):
    prompt = {"id": "a01", "cluster": "a_stack", "prompt": "Where?"}
    monkeypatch.setitem(ENGINES, "fake", FakeEngine())
    out = pipeline.ask_and_store("r", "fake", prompt, store)
    assert out["ok"] and out["n_urls"] == 1
    saved = store.load_answers("r")[0]
    assert saved["text"] == "Try Render. key=aeo/r/fake/a01"  # idempotency key per prompt

    monkeypatch.setitem(ENGINES, "fake", FakeEngine(EngineError("bad request", retryable=False)))
    assert pipeline.ask_and_store("r", "fake", prompt, store)["ok"] is False
    assert store.load_answers("r", include_failed=True)[0]["error"] == "bad request"

    monkeypatch.setitem(ENGINES, "fake", FakeEngine(EngineError("429", retryable=True)))
    with pytest.raises(EngineError):  # retryable errors go back to Render's retry logic
        pipeline.ask_and_store("r", "fake", prompt, store)


def test_plan_jobs_runs_costly_engines_on_core_prompts_only(monkeypatch):
    monkeypatch.setenv("TREG_TOKEN", "test")
    jobs = pipeline.plan_jobs("default", ["chatgpt", "claude"])
    assert sum(1 for e, _ in jobs if e == "chatgpt") == 40
    assert sum(1 for e, _ in jobs if e == "claude") == 16
    assert len(pipeline.plan_jobs("smoke", ["chatgpt"], ["a01"])) == 1


def test_plan_jobs_rejects_unconfigured_engines(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="or_openai needs OPENROUTER_API_KEY"):
        pipeline.plan_jobs("smoke", ["or_openai"])
    with pytest.raises(ValueError, match="Unknown engine"):
        pipeline.plan_jobs("smoke", ["nope"])
