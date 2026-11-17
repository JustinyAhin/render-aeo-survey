"""Render Workflows entry point.

Tasks:
  run_survey(prompt_set, engines, prompt_ids)  root task; fans out, then analyzes
  ask_engine(run_id, engine, prompt)           one engine, one prompt, own instance
  analyze(run_id)                              metrics, diff, Markdown report

Local:   render workflows dev -- python main.py
Render:  start command `python main.py` (see render.yaml)
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

from render import Retry, TaskContext, Workflows

from aeo import pipeline
from aeo.engines import DEFAULT_ENGINES
from aeo.storage import get_store, utcnow

log = logging.getLogger("aeo")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

# Calls in flight per engine. Scrapers and provider APIs rate-limit bursts.
MAX_PARALLEL_PER_ENGINE = int(os.environ.get("AEO_MAX_PARALLEL_PER_ENGINE", "8"))

app = Workflows()


@app.task(
    # Engine calls fail now and then (429s, scraper timeouts). Every retry reuses
    # the same treg Idempotency-Key, so a retried call is never billed twice.
    retry=Retry(max_retries=3, wait_duration_ms=5_000, backoff_scaling=2.0),
    timeout_seconds=420,
)
def ask_engine(ctx: TaskContext, run_id: str, engine: str, prompt: dict) -> dict:
    return pipeline.ask_and_store(run_id, engine, prompt)


@app.task(timeout_seconds=900, retry=Retry(max_retries=2, wait_duration_ms=2_000))
def analyze(ctx: TaskContext, run_id: str) -> dict:
    return pipeline.analyze_run(run_id)


@app.task(
    timeout_seconds=4 * 3600,
    # One retry is safe: the run id comes from the root run id, so a retried
    # root run writes to the same run and treg replays calls it already paid for.
    retry=Retry(max_retries=1, wait_duration_ms=10_000),
)
async def run_survey(ctx: TaskContext, prompt_set: str = "default",
                     engines: list[str] | None = None,
                     prompt_ids: list[str] | None = None) -> dict:
    engines = engines or DEFAULT_ENGINES
    jobs = pipeline.plan_jobs(prompt_set, engines, prompt_ids)
    run_id = _run_id(ctx)
    store = get_store()
    store.create_run({"run_id": run_id, "started_at": utcnow(), "status": "running",
                      "prompt_set": prompt_set, "engines": engines})
    log.info("run %s: %d calls across %s", run_id, len(jobs), ", ".join(engines))

    limits = {e: asyncio.Semaphore(MAX_PARALLEL_PER_ENGINE) for e in engines}

    async def one(engine: str, prompt: dict):
        async with limits[engine]:
            return await ctx.run(ask_engine, run_id, engine, prompt)

    # Each chained run gets its own instance and retry budget. return_exceptions
    # keeps one failed prompt from cancelling the rest of the survey.
    results = await asyncio.gather(*(one(e, p) for e, p in jobs), return_exceptions=True)
    for (engine, prompt), res in zip(jobs, results):
        if isinstance(res, BaseException):
            log.warning("%s/%s failed after retries: %r", engine, prompt["id"], res)
            pipeline.record_failure(run_id, engine, prompt, f"{type(res).__name__}: {res}", store)

    summary = await ctx.run(analyze, run_id)
    summary["calls"] = len(jobs)
    return summary


def _run_id(ctx: TaskContext) -> str:
    meta = ctx.metadata
    return meta.root_task_run_id or meta.task_run_id or f"local-{uuid.uuid4().hex[:12]}"


if __name__ == "__main__":
    app.start()
