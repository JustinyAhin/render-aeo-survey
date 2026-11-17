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
    for name in ("or_gemini", "or_perplexity", "or_claude"):
        assert get_engine(name).parse(raw) == ("Use Render.", ["https://render.com/pricing"])


def test_openrouter_empty_or_missing_fields():
    assert get_engine("or_openai").parse({"choices": []}) == ("", [])
    assert get_engine("or_openai").parse({"choices": [{"message": {"content": None}}]}) == ("", [])


def test_openrouter_claude_is_core_only():
    assert get_engine("or_claude").core_only
    assert not get_engine("or_openai").core_only
