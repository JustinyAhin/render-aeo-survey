"""Parsers for the direct API engines, on minimal payloads shaped like the
response examples in each provider's docs (2026-11-17). They check the JSON
paths, not live behavior: no direct engine has been called with a real key."""

from aeo.engines import get_engine


def test_openai_responses_web_search():
    raw = {"output": [
        {"type": "web_search_call", "id": "ws_1", "status": "completed"},
        {"type": "message", "content": [{
            "type": "output_text", "text": "Render or Railway.",
            "annotations": [{"type": "url_citation", "url": "https://render.com/docs", "title": "Docs",
                             "start_index": 0, "end_index": 6}]}]}]}
    assert get_engine("openai_api").parse(raw) == ("Render or Railway.", ["https://render.com/docs"])


def test_perplexity_agent_api():
    raw = {"output": [
        {"type": "search_results", "results": [{"id": 1, "url": "https://render.com/pricing"},
                                               {"id": 2, "url": "https://railway.com"}]},
        {"type": "message", "content": [{"type": "output_text", "text": "Render [1]."}]}]}
    assert get_engine("perplexity_api").parse(raw) == (
        "Render [1].", ["https://render.com/pricing", "https://railway.com"])


def test_gemini_grounding_uses_title_domain_not_redirect_uri():
    raw = {"candidates": [{
        "content": {"parts": [{"text": "Use "}, {"text": "Render."}]},
        "groundingMetadata": {"groundingChunks": [
            {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc",
                     "title": "render.com"}},
            {"web": {"uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/def",
                     "title": "Some page title"}}]}}]}
    text, urls = get_engine("gemini_api").parse(raw)
    assert text == "Use Render."
    assert urls == ["https://render.com/",
                    "https://vertexaisearch.cloud.google.com/grounding-api-redirect/def"]


def test_anthropic_web_search_citations():
    raw = {"content": [
        {"type": "server_tool_use", "name": "web_search", "input": {"query": "hosting"}},
        {"type": "web_search_tool_result", "content": [
            {"type": "web_search_result", "url": "https://fly.io", "title": "Fly"}]},
        {"type": "text", "text": "Render is simple. ", "citations": [
            {"type": "web_search_result_location", "url": "https://render.com/docs",
             "title": "Docs", "cited_text": "..."}]},
        {"type": "text", "text": "Fly.io is global."}]}
    assert get_engine("anthropic_api").parse(raw) == (
        "Render is simple. Fly.io is global.", ["https://render.com/docs"])
