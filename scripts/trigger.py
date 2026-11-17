"""Start one survey run. The weekly Render cron job runs this script.

Env:
  RENDER_API_KEY   Render API key (not needed with RENDER_USE_LOCAL_DEV=true)
  WORKFLOW_SLUG    the workflow's slug, e.g. aeo-survey
  AEO_PROMPT_SET   default: "default"
  AEO_ENGINES      comma-separated engine names; default: the five treg engines

The script starts the root task and exits. It does not wait: the cron job is
billed while it runs, and the run's progress is on the workflow's Runs page.
The idempotency key is the ISO week, so a re-triggered cron run in the same
week returns the existing run instead of paying for a second one.
"""

from __future__ import annotations

import os
import sys
from datetime import date

from render import Render


def main() -> int:
    slug = os.environ.get("WORKFLOW_SLUG", "aeo-survey")
    engines = [e for e in os.environ.get("AEO_ENGINES", "").split(",") if e] or None
    args = {"prompt_set": os.environ.get("AEO_PROMPT_SET", "default"), "engines": engines}
    year, week, _ = date.today().isocalendar()
    key = os.environ.get("AEO_IDEMPOTENCY_KEY", f"aeo-survey-{year}-W{week:02d}")
    run = Render().workflows.start_task(f"{slug}/run_survey", args, idempotency_key=key)
    print(f"started {slug}/run_survey run {run.id} (idempotency key {key})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
