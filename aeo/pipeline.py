"""Plain functions behind the workflow tasks. They also run without Render (CLI, tests)."""

from __future__ import annotations

from . import analysis
from .engines import ENGINES, EngineError, get_engine
from .prompts import load_ground_truth, load_prompt_set
from .report import build_report
from .storage import Store, get_store, utcnow


def plan_jobs(prompt_set: str, engines: list[str], prompt_ids: list[str] | None = None
              ) -> list[tuple[str, dict]]:
    """Return the (engine, prompt) pairs to ask. Checks engine names and env first."""
    ps = load_prompt_set(prompt_set)
    prompts = [p for p in ps["prompts"] if not prompt_ids or p["id"] in prompt_ids]
    problems = []
    for name in engines:
        missing = get_engine(name).missing_env()
        if missing:
            problems.append(f"{name} needs {', '.join(missing)}")
    if problems:
        raise ValueError("Engines not configured: " + "; ".join(problems))
    return [(e, p) for e in engines for p in prompts
            if not get_engine(e).core_only or p.get("core")]


def answer_base(engine: str, prompt: dict) -> dict:
    return {"engine": engine, "prompt_id": prompt["id"], "cluster": prompt["cluster"],
            "prompt": prompt["prompt"], "fetched_at": utcnow()}


def ask_and_store(run_id: str, engine_name: str, prompt: dict, store: Store | None = None
                  ) -> dict:
    """Ask one engine one prompt and store the answer.

    Raises EngineError only when the failure is worth a retry. Other failures
    are stored with an `error` and reported as ok=False.
    """
    store = store or get_store()
    engine = get_engine(engine_name)
    base = answer_base(engine_name, prompt)
    key = f"aeo/{run_id}/{engine_name}/{prompt['id']}"
    try:
        res = engine.ask(prompt["prompt"], idempotency_key=key)
        text, urls = engine.parse(res.raw)
    except EngineError as e:
        if e.retryable:
            raise
        store.save_answer(run_id, {**base, "error": str(e), "cost_usd": e.cost_usd})
        return {"engine": engine_name, "prompt_id": prompt["id"], "ok": False,
                "cost_usd": e.cost_usd, "error": str(e)}
    store.save_answer(run_id, {**base, "text": text, "urls": urls, "cost_usd": res.cost_usd,
                               "raw": res.raw})
    return {"engine": engine_name, "prompt_id": prompt["id"], "ok": True,
            "cost_usd": res.cost_usd, "chars": len(text), "n_urls": len(urls)}


def record_failure(run_id: str, engine_name: str, prompt: dict, error: str,
                   store: Store | None = None) -> None:
    (store or get_store()).save_answer(run_id, {**answer_base(engine_name, prompt),
                                                "error": error, "cost_usd": None})


def analyze_run(run_id: str, store: Store | None = None) -> dict:
    """Compute metrics for a run, diff them against the previous completed run,
    write the report and mark the run completed."""
    store = store or get_store()
    run = store.get_run(run_id)
    if run is None:
        raise ValueError(f"No run {run_id!r} in the store")
    ps = load_prompt_set(run["prompt_set"])
    patterns = analysis.compile_stale_patterns(load_ground_truth())
    all_answers = store.load_answers(run_id, include_failed=True)
    ok = [a for a in all_answers if not a.get("error")]
    # Parse stored raw responses again, so a parser fix also applies to past runs.
    for a in ok:
        if a.get("raw") is not None and a["engine"] in ENGINES:
            a["text"], a["urls"] = ENGINES[a["engine"]].parse(a["raw"])
    if not ok:
        store.finish_run(run_id, status="failed", finished_at=utcnow())
        raise ValueError(f"Run {run_id} has no successful answers "
                         f"({len(all_answers)} failed calls)")
    rows = [analysis.analyze_answer(a, patterns) for a in ok]
    metrics = analysis.aggregate(rows, ps["branded_clusters"])
    previous = store.previous_completed_run(run_id)
    delta = analysis.diff(previous and previous.get("metrics"), metrics)
    cost = sum(a.get("cost_usd") or 0 for a in all_answers)
    failures = [{"engine": a["engine"], "prompt_id": a["prompt_id"], "error": a["error"]}
                for a in all_answers if a.get("error")]
    run.update(cost_usd=cost, failures=failures,
               previous_run_id=previous["run_id"] if previous else None)
    report = build_report(run, metrics, delta,
                          {name: e.description for name, e in ENGINES.items()})
    store.finish_run(run_id, status="completed", finished_at=utcnow(), cost_usd=cost,
                     previous_run_id=run["previous_run_id"], metrics=metrics, report=report)
    return {
        "run_id": run_id,
        "answers": len(ok),
        "failed": len(failures),
        "cost_usd": round(cost, 4),
        "organic_mention_all": metrics["organic_mention_all"],
        "organic_mention_by_engine": metrics["organic_mention_by_engine"],
        "previous_run_id": run["previous_run_id"],
        "report_markdown": report,
    }
