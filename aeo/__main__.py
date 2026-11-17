"""Command line helpers that work without the workflow.

  python -m aeo import-raw DIR [--run-id ID] [--started-at ISO]
      Load raw answers saved by the original one-off survey
      (DIR/<engine>/<prompt_id>.json) as a completed run. Use it to seed a
      baseline so the first scheduled run has something to diff against.
  python -m aeo analyze RUN_ID
      Recompute metrics and the report for a stored run (for example after a
      brand pattern changes). Prints the report.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engines import get_engine
from .pipeline import analyze_run
from .storage import get_store, utcnow


def import_raw(directory: Path, run_id: str, started_at: str, prompt_set: str) -> None:
    store = get_store()
    files = sorted(directory.glob("*/*.json"))
    if not files:
        raise SystemExit(f"No */*.json files in {directory}")
    engines = sorted({f.parent.name for f in files})
    store.create_run({"run_id": run_id, "started_at": started_at, "status": "running",
                      "prompt_set": prompt_set, "engines": engines})
    for f in files:
        rec = json.loads(f.read_text())
        text, urls = get_engine(rec["engine"]).parse(rec["result"])
        store.save_answer(run_id, {
            "engine": rec["engine"], "prompt_id": rec["prompt_id"], "cluster": rec["cluster"],
            "prompt": rec["prompt"], "fetched_at": rec.get("fetched_at") or started_at,
            "text": text, "urls": urls,
            "cost_usd": ((rec.get("treg") or {}).get("charged_micro") or 0) / 1e6,
            "raw": rec["result"]})
    print(f"imported {len(files)} answers from {directory} as run {run_id}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aeo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import-raw")
    imp.add_argument("directory", type=Path)
    imp.add_argument("--run-id", default=None)
    imp.add_argument("--started-at", default=None, help="ISO time, e.g. 2026-11-17T05:00:00Z")
    imp.add_argument("--prompt-set", default="default")
    an = sub.add_parser("analyze")
    an.add_argument("run_id")
    a = ap.parse_args(argv)

    if a.cmd == "import-raw":
        started = a.started_at or utcnow()
        run_id = a.run_id or f"import-{started[:10]}"
        import_raw(a.directory, run_id, started, a.prompt_set)
        summary = analyze_run(run_id)
    else:
        summary = analyze_run(a.run_id)
    print(summary.pop("report_markdown"))
    print(json.dumps(summary, indent=1), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
