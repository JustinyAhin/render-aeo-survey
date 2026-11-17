"""Where runs, answers and reports live.

- PostgresStore when DATABASE_URL is set (Render Postgres in production).
- JsonStore otherwise: one directory per run under AEO_DATA_DIR (default ./runs).

Every task run on Render executes on its own instance, so the store is how
the per-prompt tasks hand answers to the analyze task. A deployed workflow
therefore needs DATABASE_URL. JsonStore only works where all tasks share a
disk, that is, the local task server.

A failed engine call is stored as an answer with an `error` and no text, so
the report can list it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store(Protocol):
    def create_run(self, run: dict) -> None: ...
    def save_answer(self, run_id: str, answer: dict) -> None: ...
    def load_answers(self, run_id: str, *, include_failed: bool = False) -> list[dict]: ...
    def get_run(self, run_id: str) -> dict | None: ...
    def finish_run(self, run_id: str, **fields) -> None: ...
    def previous_completed_run(self, run_id: str) -> dict | None: ...


def get_store() -> Store:
    url = os.environ.get("DATABASE_URL")
    if url:
        return PostgresStore(url)
    return JsonStore(Path(os.environ.get("AEO_DATA_DIR", "runs")))


# --- local JSON files -------------------------------------------------------

class JsonStore:
    def __init__(self, root: Path):
        self.root = root

    def _run_dir(self, run_id: str) -> Path:
        return self.root / run_id

    def create_run(self, run: dict) -> None:
        d = self._run_dir(run["run_id"])
        (d / "answers").mkdir(parents=True, exist_ok=True)
        (d / "run.json").write_text(json.dumps(run, indent=1))

    def save_answer(self, run_id: str, answer: dict) -> None:
        f = self._run_dir(run_id) / "answers" / answer["engine"] / f"{answer['prompt_id']}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(answer, indent=1))
        tmp.replace(f)

    def load_answers(self, run_id: str, *, include_failed: bool = False) -> list[dict]:
        files = sorted((self._run_dir(run_id) / "answers").glob("*/*.json"))
        answers = [json.loads(f.read_text()) for f in files]
        return answers if include_failed else [a for a in answers if not a.get("error")]

    def get_run(self, run_id: str) -> dict | None:
        f = self._run_dir(run_id) / "run.json"
        return json.loads(f.read_text()) if f.exists() else None

    def finish_run(self, run_id: str, **fields) -> None:
        run = self.get_run(run_id) or {"run_id": run_id}
        metrics = fields.pop("metrics", None)
        report = fields.pop("report", None)
        run.update(fields)
        d = self._run_dir(run_id)
        if metrics is not None:
            (d / "metrics.json").write_text(json.dumps(metrics, indent=1))
        if report is not None:
            (d / "report.md").write_text(report)
        (d / "run.json").write_text(json.dumps(run, indent=1))

    def previous_completed_run(self, run_id: str) -> dict | None:
        current = self.get_run(run_id) or {}
        started = current.get("started_at", "9999")
        best = None
        for f in self.root.glob("*/run.json"):
            run = json.loads(f.read_text())
            if (run["run_id"] != run_id and run.get("status") == "completed"
                    and run.get("started_at", "") < started
                    and (best is None or run["started_at"] > best["started_at"])):
                best = run
        if best is None:
            return None
        m = self._run_dir(best["run_id"]) / "metrics.json"
        best["metrics"] = json.loads(m.read_text()) if m.exists() else None
        return best


# --- Postgres ---------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS aeo_runs (
  run_id text PRIMARY KEY,
  started_at timestamptz NOT NULL,
  finished_at timestamptz,
  status text NOT NULL,
  prompt_set text NOT NULL,
  engines jsonb NOT NULL,
  cost_usd double precision,
  previous_run_id text,
  metrics jsonb,
  report text
);
CREATE TABLE IF NOT EXISTS aeo_answers (
  run_id text NOT NULL REFERENCES aeo_runs(run_id) ON DELETE CASCADE,
  engine text NOT NULL,
  prompt_id text NOT NULL,
  cluster text NOT NULL,
  prompt text NOT NULL,
  text text,
  urls jsonb,
  cost_usd double precision,
  fetched_at timestamptz NOT NULL,
  error text,
  raw jsonb,
  PRIMARY KEY (run_id, engine, prompt_id)
);
"""

RUN_COLUMNS = ("run_id", "started_at", "finished_at", "status", "prompt_set", "engines",
               "cost_usd", "previous_run_id")


def _jsonb(value) -> str:
    # Postgres jsonb rejects NUL characters, which scraped pages sometimes contain.
    return json.dumps(value).replace("\\u0000", "")


class PostgresStore:
    def __init__(self, url: str):
        import psycopg  # imported here so local JSON use does not need it

        self._psycopg = psycopg
        self.url = url
        with self._conn() as c:
            c.execute(SCHEMA)

    def _conn(self):
        return self._psycopg.connect(self.url, autocommit=True)

    def create_run(self, run: dict) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO aeo_runs (run_id, started_at, status, prompt_set, engines) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (run_id) DO NOTHING",
                (run["run_id"], run["started_at"], run["status"], run["prompt_set"],
                 _jsonb(run["engines"])))

    def save_answer(self, run_id: str, a: dict) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO aeo_answers (run_id, engine, prompt_id, cluster, prompt, text, urls,"
                " cost_usd, fetched_at, error, raw) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (run_id, engine, prompt_id) DO UPDATE SET text = EXCLUDED.text,"
                " urls = EXCLUDED.urls, cost_usd = EXCLUDED.cost_usd,"
                " fetched_at = EXCLUDED.fetched_at, error = EXCLUDED.error, raw = EXCLUDED.raw",
                (run_id, a["engine"], a["prompt_id"], a["cluster"], a["prompt"], a.get("text"),
                 _jsonb(a.get("urls") or []), a.get("cost_usd"), a["fetched_at"], a.get("error"),
                 _jsonb(a.get("raw"))))

    def load_answers(self, run_id: str, *, include_failed: bool = False) -> list[dict]:
        sql = ("SELECT engine, prompt_id, cluster, prompt, text, urls, cost_usd, error, raw"
               " FROM aeo_answers WHERE run_id = %s")
        if not include_failed:
            sql += " AND error IS NULL"
        with self._conn() as c:
            rows = c.execute(sql + " ORDER BY engine, prompt_id", (run_id,)).fetchall()
        keys = ("engine", "prompt_id", "cluster", "prompt", "text", "urls", "cost_usd", "error",
                "raw")
        return [dict(zip(keys, r)) for r in rows]

    def get_run(self, run_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(f"SELECT {', '.join(RUN_COLUMNS)} FROM aeo_runs WHERE run_id = %s",
                            (run_id,)).fetchone()
        if not row:
            return None
        run = dict(zip(RUN_COLUMNS, row))
        for k in ("started_at", "finished_at"):
            if run[k] is not None:
                run[k] = run[k].strftime("%Y-%m-%dT%H:%M:%SZ")
        return run

    def finish_run(self, run_id: str, **fields) -> None:
        allowed = {"finished_at", "status", "cost_usd", "previous_run_id", "metrics", "report"}
        sets, values = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k} = %s")
            values.append(_jsonb(v) if k == "metrics" else v)
        with self._conn() as c:
            c.execute(f"UPDATE aeo_runs SET {', '.join(sets)} WHERE run_id = %s",
                      (*values, run_id))

    def previous_completed_run(self, run_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT run_id, metrics FROM aeo_runs WHERE status = 'completed'"
                " AND run_id <> %s AND started_at < (SELECT started_at FROM aeo_runs"
                " WHERE run_id = %s) ORDER BY started_at DESC LIMIT 1",
                (run_id, run_id)).fetchone()
        return {"run_id": row[0], "metrics": row[1]} if row else None
