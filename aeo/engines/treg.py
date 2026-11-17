"""Answer engines reached through treg (https://treg.to).

treg resells scraped answer-engine results (cloro: ChatGPT, Gemini, Google AI
Mode web UIs) and DataForSEO's LLM response endpoints (Perplexity Sonar,
Claude with web search) per call, so one token covers five engines.

Two transports, same results:

- HTTP: `POST https://treg.to/call/<tool-id>` with header `X-Treg-Token`.
  Used when TREG_TOKEN is set. This is the transport for a deployed workflow,
  because Render task instances do not have the treg CLI. Mint a team token
  for it with `treg org agent-new <name> --cap <calls/day>`.
- CLI: `treg --json call <tool-id>`. Used when TREG_TOKEN is not set and the
  `treg` binary is on PATH (a signed-in laptop).

Every call sends an `Idempotency-Key` built from (run id, engine, prompt id).
If Render retries a task after a timeout, treg replays the stored answer and
does not charge again.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any, Callable

import httpx

from .base import Engine, EngineError, RawResult, dedupe

TREG_BASE_URL = os.environ.get("TREG_BASE_URL", "https://treg.to")
CALL_TIMEOUT_S = 300


def _call_http(tool: str, body: Any, idempotency_key: str) -> tuple[int, Any, int]:
    headers = {
        "X-Treg-Token": os.environ["TREG_TOKEN"],
        "Idempotency-Key": idempotency_key,
        "Content-Type": "application/json",
    }
    if os.environ.get("TREG_ORG"):  # only needed with a personal sign-in token
        headers["X-Treg-Org"] = os.environ["TREG_ORG"]
    try:
        r = httpx.post(f"{TREG_BASE_URL}/call/{tool}", json=body, headers=headers,
                       timeout=CALL_TIMEOUT_S)
    except httpx.TransportError as e:  # includes timeouts
        raise EngineError(f"treg transport error: {e!r}", retryable=True) from e
    cost = int(r.headers.get("X-Treg-Cost-Micro") or 0)
    try:
        payload = r.json()
    except ValueError:
        payload = r.text
    if r.headers.get("X-Treg-Upstream-Failed"):
        # Not retried: a retry with the same idempotency key would replay this failure.
        raise EngineError(f"{tool}: upstream reported failure in a 2xx body",
                          retryable=False, cost_usd=cost / 1e6)
    return r.status_code, payload, cost


def _call_cli(tool: str, body: Any, idempotency_key: str) -> tuple[int, Any, int]:
    cmd = ["treg", "--json", "call", tool, "--method", "POST", "--data", json.dumps(body),
           "--header", f"Idempotency-Key: {idempotency_key}"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=CALL_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise EngineError("treg CLI timed out", retryable=True) from e
    try:
        out = json.loads(p.stdout)
    except ValueError as e:
        raise EngineError(f"treg CLI output is not JSON: {p.stdout[:200]} {p.stderr[:200]}",
                          retryable=True) from e
    meta = out.get("_treg") or {}
    return int(meta.get("http_status") or 0), out.get("result"), int(meta.get("charged_micro") or 0)


def treg_call(tool: str, body: Any, idempotency_key: str) -> tuple[Any, float]:
    """Call a treg catalog tool. Returns (provider body, cost in USD)."""
    if os.environ.get("TREG_TOKEN"):
        status, payload, cost = _call_http(tool, body, idempotency_key)
    elif shutil.which("treg"):
        status, payload, cost = _call_cli(tool, body, idempotency_key)
    else:
        raise EngineError("Set TREG_TOKEN, or install and sign in to the treg CLI.",
                          retryable=False)
    if status != 200:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        retryable = status in (0, 409, 429) or status >= 500
        raise EngineError(f"treg {tool} HTTP {status}: {text[:300]}", retryable=retryable,
                          cost_usd=cost / 1e6)
    return payload, cost / 1e6


# --- response parsers -------------------------------------------------------

def parse_cloro(raw: Any) -> tuple[str, list[str]]:
    """cloro scrape of a chat UI: text plus `sources` and `citationPills`."""
    res = (raw or {}).get("result") or {}
    text = res.get("text") or res.get("markdown") or ""
    urls = [s.get("url") for s in res.get("sources") or []]
    urls += [p.get("url") for p in res.get("citationPills") or []]
    return text, dedupe(urls)


def parse_dataforseo(raw: Any, *, include_search_results: bool) -> tuple[str, list[str]]:
    """DataForSEO llm_responses: text sections with URL annotations.

    Perplexity Sonar also returns the `search_results` it grounded on; those
    count as citations, as in the original survey.
    """
    task = raw["tasks"][0]
    if task.get("status_code") != 20000:
        raise EngineError(f"DataForSEO task status {task.get('status_code')}: "
                          f"{task.get('status_message')}", retryable=False)
    result = task["result"][0]
    parts, urls = [], []
    for item in result.get("items") or []:
        for section in item.get("sections") or []:
            parts.append(section.get("text") or "")
            urls += [a.get("url") for a in section.get("annotations") or []]
    if include_search_results:
        urls += [s.get("url") for s in result.get("search_results") or []]
    return "".join(parts), dedupe(urls)


class TregEngine(Engine):
    def __init__(self, name: str, tool: str, body: Callable[[str], Any],
                 parse: Callable[[Any], tuple[str, list[str]]], description: str,
                 core_only: bool = False):
        self.name = name
        self.tool = tool
        self._body = body
        self._parse = parse
        self.description = description
        self.core_only = core_only

    def missing_env(self) -> list[str]:
        if os.environ.get("TREG_TOKEN") or shutil.which("treg"):
            return []
        return ["TREG_TOKEN"]

    def ask(self, prompt: str, *, idempotency_key: str) -> RawResult:
        payload, cost = treg_call(self.tool, self._body(prompt), idempotency_key)
        return RawResult(raw=payload, cost_usd=cost, meta={"tool": self.tool})

    def parse(self, raw: Any) -> tuple[str, list[str]]:
        return self._parse(raw)


def _cloro_body(prompt: str) -> dict:
    return {"prompt": prompt, "country": "US", "include": {"markdown": True}}


TREG_ENGINES = [
    TregEngine("chatgpt", "cloro.ai-search.chatgpt.scrape", _cloro_body, parse_cloro,
               "ChatGPT web UI (scraped by cloro, US)"),
    TregEngine("gemini", "cloro.ai-search.gemini.scrape", _cloro_body, parse_cloro,
               "Gemini web UI (scraped by cloro, US)"),
    TregEngine("google_ai_mode", "cloro.google.serp.ai_mode", _cloro_body, parse_cloro,
               "Google AI Mode (scraped by cloro, US)"),
    TregEngine("perplexity", "dataforseo.x.ai-optimization-perplexity-llm-responses-live",
               lambda p: [{"model_name": "sonar", "user_prompt": p,
                           "web_search_country_iso_code": "US"}],
               lambda raw: parse_dataforseo(raw, include_search_results=True),
               "Perplexity Sonar API (via DataForSEO)"),
    # About $0.04 per call, so it runs on the "core" prompt subset only.
    TregEngine("claude", "dataforseo.x.ai-optimization-claude-llm-responses-live",
               lambda p: [{"model_name": "claude-sonnet-4-5", "user_prompt": p,
                           "web_search": True, "max_output_tokens": 1200}],
               lambda raw: parse_dataforseo(raw, include_search_results=False),
               "Claude Sonnet 4.5 with web search (via DataForSEO)", core_only=True),
]
