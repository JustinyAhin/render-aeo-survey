# render-aeo-survey

A weekly survey of how answer engines (ChatGPT, Gemini, Google AI Mode, Perplexity, Claude) answer developer hosting questions, and whether they name and cite Render. It runs on [Render Workflows](https://render.com/docs/workflows): one task per (engine, prompt), in parallel, with retries, then one task that computes metrics, compares them with the previous run and writes a Markdown report.

## What it measures

For each answer, the analysis records which brands it names (in order), the URLs it cites, and regex hits for known stale Render facts. Per run it reports:

| Metric | Definition |
|---|---|
| Mention rate | Share of answers that name Render, per engine and per prompt cluster |
| Position | Rank of Render's first mention among all brands in the answer. Brands that the prompt names do not count. |
| Share of voice | For each brand, the number of organic answers that name it |
| Citation rate | Share of answers that cite at least one render.com URL |
| Citation share | render.com URLs as a share of all cited URLs |
| Render pages cited | Which render.com pages engines cite, and how often |
| Stale-fact candidates | Regex hits for wrong claims listed in `aeo/data/ground_truth.json` (for example "free Postgres expires after 90 days") |
| Change since previous run | Rates, flips (engine, prompt) that gained or lost a mention or citation, new and dropped Render pages, share-of-voice changes |

"Organic" answers come from prompts that do not name Render (clusters `a_stack`, `b_ai`, `c_alt`, `d_infra`). The 40 prompts are in `aeo/data/prompt_sets/default.json`.

The diff uses only the (engine, prompt) pairs that both runs share, so a change of engines or prompts does not show up as a gain or a loss.

## Method limits

- **Answers are nondeterministic.** The same prompt can name Render on Monday and not on Tuesday. One run per week is one sample. Treat a change of one or two answers per cell as noise until it repeats.
- **Small sample.** 40 prompts, about 176 answers per run. Per-cluster rates rest on 5 to 9 answers per engine.
- **Brand detection is regex.** `Render` is case-sensitive and skips verb uses ("render the page"), but it can still miss or over-count. Stale-fact hits are candidates for a human to check, not confirmed errors.
- **Scraped UIs and APIs differ.** The ChatGPT, Gemini and Google AI Mode engines are scrapes of the logged-out web UI (US). The OpenRouter engines measure the API models, which can answer differently. Do not mix the two in one trend line.
- **Engines change.** Models, search backends and UI layouts change without notice. A step change in one engine can be a vendor change, not a content change.

## Engines

| Name | Source | Status |
|---|---|---|
| `chatgpt`, `gemini`, `google_ai_mode` | cloro web UI scrapes, through treg | `chatgpt` and `gemini` called live from the local task server; all three parsers tested on saved answers |
| `perplexity` | Perplexity Sonar through DataForSEO, through treg | One live call from this repo; parser tested on saved answers |
| `claude` | Claude Sonnet 4.5 with web search through DataForSEO, through treg. About $0.04 per call, so it runs on the 16 prompts marked `core`. | Parser tested on saved answers; not called live from this repo |
| `or_openai`, `or_gemini`, `or_perplexity` | API models through [OpenRouter](https://openrouter.ai/docs/guides/features/web-search), `OPENROUTER_API_KEY`, each with its provider's native web search. Cost comes from OpenRouter's `usage.cost`. Defaults: `openai/gpt-6.1-terra`, `google/gemini-3.5-flash`, `perplexity/sonar`. To change one, edit `MODELS` in `aeo/engines/openrouter.py`. | Parser tested on a doc-shaped payload; see below for live status |
| `claude_api` | `claude-opus-5-5` on the Anthropic API with the `web_search_20260209` server tool and server-side refusal fallback. Needs `ANTHROPIC_API_KEY`. Runs the 16 prompts marked `core`. Cited URLs are the `web_search_result_location` citations in the answer. | Parser tested on a doc-shaped payload |

To add an engine, subclass `aeo.engines.base.Engine` (`ask` returns the raw response and its cost, `parse` returns text and cited URLs) and add it to a list in `aeo/engines/`.

**treg from a deployed service.** Yes, it works over HTTP: `POST https://treg.to/call/<tool-id>` with header `X-Treg-Token: <team token>`. The adapter uses HTTP when `TREG_TOKEN` is set and falls back to the `treg` CLI otherwise. Mint a token for the workflow with `treg org agent-new aeo-survey --cap 500` (an admin command; `--cap` is calls per day). `treg serve` and `treg with` do not help here: they inject credentials for the team's own registered APIs on a machine with the CLI, not for catalog tools on a Render instance. Each call sends an `Idempotency-Key`, so a task retry after a timeout replays the stored answer instead of paying again.

## Run locally

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), the [Render CLI](https://render.com/docs/cli) 2.35.0 or later, and either `TREG_TOKEN` or a signed-in `treg` CLI.

```sh
uv sync
uv run pytest

# Terminal 1: the local task server (results go to ./runs unless DATABASE_URL is set)
render workflows dev -- .venv/bin/python main.py

# Terminal 2: start a cheap run (3 prompts x 2 engines, about $0.02)
RENDER_USE_LOCAL_DEV=true AEO_PROMPT_SET=smoke AEO_ENGINES=chatgpt,gemini \
  .venv/bin/python scripts/trigger.py
render workflows tasks runs list run_survey --local -o text
cat runs/<run id>/report.md
```

You can also start a run from the CLI: `render workflows tasks runs start run_survey --local --input='["smoke", ["chatgpt"]]'`.

To give the first scheduled run something to compare with, load saved answers as a baseline run:

```sh
uv run python -m aeo import-raw path/to/responses --run-id baseline-2026-11-17 --started-at 2026-11-17T05:00:00Z
```

`python -m aeo analyze <run id>` recomputes metrics and the report for a stored run, for example after you change a brand pattern.

## Deploy

`render.yaml` defines the workflow, a weekly cron job (Mondays 06:00 UTC) that starts `run_survey` through the Render SDK, and a Render Postgres database. Workflows do not have a built-in scheduler yet; the docs recommend a cron job that triggers runs.

1. In the Render Dashboard, choose **New > Blueprint** and select this repo.
2. Set `TREG_TOKEN` on the workflow and `RENDER_API_KEY` on the cron job.
3. Check that `run_survey`, `ask_engine` and `analyze` appear on the workflow's Definitions page.
4. Start a cheap first run of `run_survey` with input `["smoke", ["chatgpt", "gemini"]]`.
5. Use **Trigger Run** on the cron job for a full run, or wait for Monday.

## Cost per run

| Item | Estimate |
|---|---|
| Engine calls through treg (176 answers, from the 2026-11-17 run) | $1.18 (ChatGPT $0.11, Gemini $0.10, AI Mode $0.10, Perplexity $0.24, Claude $0.64) |
| Workflow compute (`flex`; about 176 runs of 15 to 45 s that mostly wait on the network) | Under $0.10 |
| Cron job | Seconds per week; $1/month minimum per cron job |
| Render Postgres `free` | $0. It expires 30 days after creation. Change the plan to `0.1c-256mb` ($6/month) to keep the data. |

About $1.30 per weekly run, or about $6.50 per month with the fixed costs ($12.50 on the paid Postgres plan). Dropping `claude` brings a run to about $0.55.

On the Hobby workspace plan a workflow can start new runs worth 16 CPU per minute, so the 176 chained runs queue and start over several minutes. A full run takes 10 to 20 minutes.

## Layout

```
main.py                    workflow tasks: run_survey, ask_engine, analyze
scripts/trigger.py         starts run_survey (the cron job's command)
aeo/engines/               engine adapters (treg.py, openrouter.py)
aeo/analysis.py            pure functions: brand detection, metrics, diff
aeo/report.py              Markdown report
aeo/storage.py             Postgres or local JSON store
aeo/data/                  prompt sets and ground truth
tests/fixtures/            21 real answers from the 2026-11-17 run, with expected rows
```

## License

MIT
