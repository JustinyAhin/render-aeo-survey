"""Answer engines called directly through each provider's own API.

UNTESTED: these adapters follow the official API docs as read on 2026-11-17,
but no call has been made with a real key. Run the smoke prompt set with one
engine before you schedule them.

They measure the API models, not the consumer apps. ChatGPT, Gemini and
Perplexity web UIs can answer differently from their APIs, so do not mix
these engines with the treg engines in one trend line.

Costs are estimates from the prices below (USD, checked 2026-11-17), except
Perplexity, which reports its own cost. Update them when the pricing pages change.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .base import Engine, EngineError, RawResult, dedupe, status_error

TIMEOUT_S = 180

PRICES = {
    # https://developers.openai.com/api/docs/pricing
    "openai": {"in": 0.10e-6, "out": 0.50e-6, "search": 0.010},
    # https://platform.claude.com/docs/en/about-claude/pricing
    "anthropic": {"in": 0.10e-6, "out": 0.50e-6, "search": 0.010},
    # https://ai.google.dev/gemini-api/docs/pricing (search: after 5,000 free queries/month)
    "gemini": {"in": 0.30e-6, "out": 2.50e-6, "search": 0.014},
}


def _post(provider: str, url: str, headers: dict, body: dict) -> dict:
    try:
        r = httpx.post(url, headers=headers, json=body, timeout=TIMEOUT_S)
    except httpx.TransportError as e:
        raise EngineError(f"{provider} transport error: {e!r}", retryable=True) from e
    if r.status_code != 200:
        raise status_error(provider, r.status_code, r.text)
    return r.json()


class OpenAIEngine(Engine):
    """OpenAI Responses API with the web_search tool.

    Docs: https://developers.openai.com/api/docs/guides/tools-web-search
    """

    name = "openai_api"
    description = "OpenAI Responses API, gpt-6-luna + web_search (direct, untested)"
    required_env = ("OPENAI_API_KEY",)
    model = os.environ.get("AEO_OPENAI_MODEL", "gpt-6-luna")

    def ask(self, prompt: str, *, idempotency_key: str) -> RawResult:
        raw = _post("openai", "https://api.openai.com/v1/responses",
                    {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                    {"model": self.model, "input": prompt, "tools": [{"type": "web_search"}]})
        usage = raw.get("usage") or {}
        searches = sum(1 for o in raw.get("output", []) if o.get("type") == "web_search_call")
        p = PRICES["openai"]
        cost = (usage.get("input_tokens", 0) * p["in"] + usage.get("output_tokens", 0) * p["out"]
                + searches * p["search"])
        return RawResult(raw=raw, cost_usd=cost, meta={"model": self.model})

    def parse(self, raw: Any) -> tuple[str, list[str]]:
        parts, urls = [], []
        for item in raw.get("output", []):
            if item.get("type") != "message":
                continue
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    parts.append(c.get("text", ""))
                    urls += [a.get("url") for a in c.get("annotations", [])
                             if a.get("type") == "url_citation"]
        return "".join(parts), dedupe(urls)


class PerplexityEngine(Engine):
    """Perplexity Agent API with model perplexity/sonar and the web_search tool.

    Sonar chat completions are legacy since 2026-09-27; the Agent API replaces them.
    Docs: https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/how-to
    """

    name = "perplexity_api"
    description = "Perplexity Agent API, perplexity/sonar + web_search (direct, untested)"
    required_env = ("PERPLEXITY_API_KEY",)
    model = os.environ.get("AEO_PERPLEXITY_MODEL", "perplexity/sonar")

    def ask(self, prompt: str, *, idempotency_key: str) -> RawResult:
        raw = _post("perplexity", "https://api.perplexity.ai/v1/agent",
                    {"Authorization": f"Bearer {os.environ['PERPLEXITY_API_KEY']}"},
                    {"model": self.model, "input": prompt, "tools": [{"type": "web_search"}]})
        cost = ((raw.get("usage") or {}).get("cost") or {}).get("total_cost")
        return RawResult(raw=raw, cost_usd=cost, meta={"model": self.model})

    def parse(self, raw: Any) -> tuple[str, list[str]]:
        parts, urls = [], []
        for item in raw.get("output", []):
            if item.get("type") == "message":
                parts += [c.get("text", "") for c in item.get("content", [])
                          if c.get("type") == "output_text"]
            elif item.get("type") == "search_results":
                urls += [r.get("url") for r in item.get("results", [])]
        return "".join(parts), dedupe(urls)


class GeminiEngine(Engine):
    """Gemini generateContent with Google Search grounding.

    Docs: https://ai.google.dev/gemini-api/docs/google-search
    The grounding chunk URIs are vertexaisearch.cloud.google.com links, not the
    source URLs; `web.title` holds the source domain. So this adapter returns
    https://<title>/ as the URL: domain-level citation data, no page paths.
    """

    name = "gemini_api"
    description = "Gemini API, gemini-3.5-flash-lite + Google Search grounding (direct, untested)"
    required_env = ("GEMINI_API_KEY",)
    model = os.environ.get("AEO_GEMINI_MODEL", "gemini-3.5-flash-lite")

    def ask(self, prompt: str, *, idempotency_key: str) -> RawResult:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        raw = _post("gemini", url, {"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                    {"contents": [{"parts": [{"text": prompt}]}],
                     "tools": [{"google_search": {}}]})
        usage = raw.get("usageMetadata") or {}
        cand = (raw.get("candidates") or [{}])[0]
        queries = len((cand.get("groundingMetadata") or {}).get("webSearchQueries") or [])
        p = PRICES["gemini"]
        cost = (usage.get("promptTokenCount", 0) * p["in"]
                + (usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0))
                * p["out"] + queries * p["search"])
        return RawResult(raw=raw, cost_usd=cost, meta={"model": self.model})

    def parse(self, raw: Any) -> tuple[str, list[str]]:
        cand = (raw.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
        urls = []
        for chunk in (cand.get("groundingMetadata") or {}).get("groundingChunks", []):
            web = chunk.get("web") or {}
            title = (web.get("title") or "").strip()
            urls.append(f"https://{title}/" if "." in title and " " not in title else web.get("uri"))
        return text, dedupe(urls)


class AnthropicEngine(Engine):
    """Anthropic Messages API with the server-side web search tool.

    Docs: https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool
    """

    name = "anthropic_api"
    description = "Anthropic Messages API, claude-haiku-5-5 + web_search (direct, untested)"
    required_env = ("ANTHROPIC_API_KEY",)
    core_only = True
    model = os.environ.get("AEO_ANTHROPIC_MODEL", "claude-haiku-5-5")

    def ask(self, prompt: str, *, idempotency_key: str) -> RawResult:
        raw = _post("anthropic", "https://api.anthropic.com/v1/messages",
                    {"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                     "anthropic-version": "2026-10-21"},
                    {"model": self.model, "max_tokens": 1500,
                     "messages": [{"role": "user", "content": prompt}],
                     "tools": [{"type": "web_search_20250305", "name": "web_search",
                                "max_uses": 3}]})
        usage = raw.get("usage") or {}
        searches = (usage.get("server_tool_use") or {}).get("web_search_requests", 0)
        p = PRICES["anthropic"]
        cost = (usage.get("input_tokens", 0) * p["in"] + usage.get("output_tokens", 0) * p["out"]
                + searches * p["search"])
        # A "pause_turn" stop means a long search turn was cut; the partial answer is kept.
        return RawResult(raw=raw, cost_usd=cost,
                         meta={"model": self.model, "stop_reason": raw.get("stop_reason")})

    def parse(self, raw: Any) -> tuple[str, list[str]]:
        parts, urls = [], []
        for block in raw.get("content", []):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
                urls += [c.get("url") for c in block.get("citations") or []
                         if c.get("type") == "web_search_result_location"]
        return "".join(parts), dedupe(urls)


DIRECT_ENGINES = [OpenAIEngine(), PerplexityEngine(), GeminiEngine(), AnthropicEngine()]
