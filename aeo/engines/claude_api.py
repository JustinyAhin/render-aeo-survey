"""Claude through the Anthropic API, with Anthropic's web search server tool.

Docs (read 2026-11-17): https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool

This measures the API model, not the claude.ai app. Do not mix it with the
treg `claude` engine in one trend line.

Cited URLs are the sources the answer itself cites: `web_search_result_location`
citations on text blocks, plus Markdown links in the answer text. With the
`web_search_20260209` tool (dynamic filtering), results pass through code
execution and the answer usually cites them as Markdown links instead of
structured citations. Search results the answer does not cite are not counted.
"""

from __future__ import annotations

import re
from typing import Any

import anthropic

from .base import Engine, EngineError, RawResult, dedupe

MODEL = "claude-opus-5-5"
# USD, checked 2026-11-17: $4 / $20 per million tokens, $10 per 1,000 searches.
PRICE_IN = 4e-6
PRICE_OUT = 20e-6
PRICE_SEARCH = 0.010
# A server-side search loop can stop with pause_turn. Resume at most this often.
MAX_RESUMES = 3
MD_LINK = re.compile(r"\]\((https?://[^\s)]+)\)")


class ClaudeApiEngine(Engine):
    name = "claude_api"
    core_only = True
    description = f"Anthropic API, {MODEL} + web search (API model)"
    required_env = ("ANTHROPIC_API_KEY",)

    def ask(self, prompt: str, *, idempotency_key: str) -> RawResult:
        client = anthropic.Anthropic(max_retries=0)  # Render retries the task run.
        messages: list[dict] = [{"role": "user", "content": prompt}]
        responses = []
        try:
            for _ in range(MAX_RESUMES + 1):
                resp = client.beta.messages.create(
                    model=MODEL,
                    max_tokens=16000,
                    output_config={"effort": "medium"},
                    tools=[{"type": "web_search_20260209", "name": "web_search"}],
                    messages=messages,
                    # On a policy decline, the API re-runs the request on a fallback model.
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
                responses.append(resp)
                if resp.stop_reason != "pause_turn":
                    break
                # Send the paused turn back unchanged; the server resumes it.
                messages = [*messages, {"role": "assistant", "content": resp.content}]
        except (anthropic.RateLimitError, anthropic.InternalServerError,
                anthropic.APIConnectionError) as e:
            raise EngineError(f"anthropic: {e!r}"[:300], retryable=True) from e
        except anthropic.APIStatusError as e:
            raise EngineError(f"anthropic HTTP {e.status_code}: {e.message}"[:300],
                              retryable=e.status_code >= 500) from e

        cost = 0.0
        for r in responses:
            u = r.usage
            searches = getattr(u.server_tool_use, "web_search_requests", 0) if u.server_tool_use else 0
            cost += u.input_tokens * PRICE_IN + u.output_tokens * PRICE_OUT + searches * PRICE_SEARCH
        raw = {"responses": [r.model_dump(mode="json") for r in responses]}
        return RawResult(raw=raw, cost_usd=cost,
                         meta={"model": responses[-1].model, "stop_reason": responses[-1].stop_reason})

    def parse(self, raw: Any) -> tuple[str, list[str]]:
        parts, urls = [], []
        for resp in raw.get("responses", []):
            for block in resp.get("content", []):
                if block.get("type") != "text":
                    continue
                parts.append(block.get("text", ""))
                urls += [c.get("url") for c in block.get("citations") or []
                         if c.get("type") == "web_search_result_location"]
        text = "".join(parts)
        return text, dedupe(urls + MD_LINK.findall(text))


CLAUDE_API_ENGINES = [ClaudeApiEngine()]
