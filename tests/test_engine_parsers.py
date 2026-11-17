"""Parser for the OpenRouter engines, on a payload shaped like the
`url_citation` example in OpenRouter's web search docs (2026-11-17)."""

from aeo.engines import get_engine


def _completion(content, urls):
    return {"choices": [{"message": {
        "role": "assistant",
        "content": content,
        "annotations": [{"type": "url_citation", "url_citation": {"url": u, "title": "t"}}
                        for u in urls],
    }}], "usage": {"cost": 0.012}}


def test_openrouter_citations_deduped_in_order():
    raw = _completion("Render or Railway.",
                      ["https://render.com/docs", "https://railway.com", "https://render.com/docs"])
    assert get_engine("or_openai").parse(raw) == (
        "Render or Railway.", ["https://render.com/docs", "https://railway.com"])


def test_openrouter_same_parser_for_every_provider():
    raw = _completion("Use Render.", ["https://render.com/pricing"])
    for name in ("or_gemini", "or_perplexity"):
        assert get_engine(name).parse(raw) == ("Use Render.", ["https://render.com/pricing"])


def test_openrouter_empty_or_missing_fields():
    assert get_engine("or_openai").parse({"choices": []}) == ("", [])
    assert get_engine("or_openai").parse({"choices": [{"message": {"content": None}}]}) == ("", [])



def test_claude_api_citations_from_text_blocks_across_resumed_turns():
    raw = {"responses": [
        {"content": [
            {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "q"}},
            {"type": "web_search_tool_result", "tool_use_id": "s1",
             "content": [{"type": "web_search_result", "url": "https://uncited.example"}]},
            {"type": "text", "text": "Use Render. ", "citations": [
                {"type": "web_search_result_location", "url": "https://render.com/docs",
                 "title": "Docs", "cited_text": "x", "encrypted_index": "e"}]},
        ], "stop_reason": "pause_turn"},
        {"content": [
            {"type": "text", "text": "Or Railway.", "citations": None},
            {"type": "text", "text": "", "citations": [
                {"type": "web_search_result_location", "url": "https://render.com/docs"},
                {"type": "web_search_result_location", "url": "https://railway.com"}]},
        ], "stop_reason": "end_turn"},
    ]}
    assert get_engine("claude_api").parse(raw) == (
        "Use Render. Or Railway.", ["https://render.com/docs", "https://railway.com"])


def test_claude_api_is_core_only_and_needs_its_key():
    e = get_engine("claude_api")
    assert e.core_only
    assert e.required_env == ("ANTHROPIC_API_KEY",)


def test_claude_api_markdown_links_count_as_citations():
    raw = {"responses": [{"content": [
        {"type": "text", "citations": None,
         "text": "See [Render](https://render.com/articles/x) and [Temporal](https://temporal.io/)."},
    ]}]}
    assert get_engine("claude_api").parse(raw)[1] == [
        "https://render.com/articles/x", "https://temporal.io/"]
