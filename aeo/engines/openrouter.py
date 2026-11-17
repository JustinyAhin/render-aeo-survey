"""Answer engines called through OpenRouter, with one API key.

Each engine is one model on OpenRouter's Chat Completions API with the `web`
plugin set to the provider's native search (OpenAI, Google, Anthropic and
Perplexity all have one). OpenRouter returns cited URLs as `url_citation`
annotations in the same shape for every provider, and the exact cost of the
call in `usage.cost`.

Docs (read 2026-11-17):
- https://openrouter.ai/docs/guides/features/web-search
- https://openrouter.ai/docs/guides/administration/usage-accounting

These engines measure the API models, not the consumer apps. ChatGPT, Gemini
and Perplexity web UIs can answer differently from their APIs, so do not mix
these engines with the treg UI-scrape engines in one trend line.

To change a model, edit MODELS below. A model change breaks the trend line,
so it belongs in a commit, not in an environment variable.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .base import Engine, EngineError, RawResult, dedupe, status_error

URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_S = 180

# Engine name -> OpenRouter model id (checked on openrouter.ai/api/v1/models, 2026-11-17).
MODELS = {
    "or_openai": "openai/gpt-6.1-terra",
    "or_gemini": "google/gemini-3.5-flash",
    "or_perplexity": "perplexity/sonar",
}
# Perplexity searches on every call and rejects the web plugin, so it gets none.
BUILT_IN_SEARCH = {"or_perplexity"}


class OpenRouterEngine(Engine):

    def __init__(self, name: str, model: str):
        self.name = name
        self.model = model
        self.required_env = ("OPENROUTER_API_KEY",)
        self.description = f"OpenRouter, {self.model} + native web search (API model)"

    def ask(self, prompt: str, *, idempotency_key: str) -> RawResult:
        # OpenRouter has no idempotency key, so a task retry after a timeout
        # can pay for a second answer. Retries are rare and calls are cents.
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.name not in BUILT_IN_SEARCH:
            body["plugins"] = [{"id": "web", "engine": "native"}]
        headers = {
            "Authorization": f"Bearer {os.environ["OPENROUTER_API_KEY"]}",
            "X-Title": "render-aeo-survey",
        }
        try:
            r = httpx.post(URL, headers=headers, json=body, timeout=TIMEOUT_S)
        except httpx.TransportError as e:
            raise EngineError(f"openrouter transport error: {e!r}", retryable=True) from e
        if r.status_code != 200:
            raise status_error("openrouter", r.status_code, r.text)
        raw = r.json()
        # A 200 can still carry a provider error in the body.
        if raw.get("error"):
            err = raw["error"]
            code = err.get("code") if isinstance(err, dict) else None
            retryable = isinstance(code, int) and (code == 429 or code >= 500)
            raise EngineError(f"openrouter error: {str(err)[:300]}", retryable=retryable)
        cost = (raw.get("usage") or {}).get("cost")
        return RawResult(raw=raw, cost_usd=cost, meta={"model": raw.get("model", self.model)})

    def parse(self, raw: Any) -> tuple[str, list[str]]:
        choices = raw.get("choices") or []
        if not choices:
            return "", []
        msg = choices[0].get("message") or {}
        text = msg.get("content") or ""
        urls = [
            (a.get("url_citation") or {}).get("url")
            for a in msg.get("annotations") or []
            if a.get("type") == "url_citation"
        ]
        return text, dedupe(urls)


OPENROUTER_ENGINES = [
    OpenRouterEngine(name, model) for name, model in MODELS.items()
]
