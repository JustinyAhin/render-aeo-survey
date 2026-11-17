"""Analysis tests. Fixtures are real answers from the 2026-11-17 survey run, and
expected_rows.jsonl holds what the original one-off analyze.py extracted from them."""

import json
from pathlib import Path

import pytest

from aeo import analysis
from aeo.engines import get_engine
from aeo.prompts import load_ground_truth

FIX = Path(__file__).parent / "fixtures"
BRANDED = ["e_compare", "f_render"]
PATTERNS = analysis.compile_stale_patterns(load_ground_truth())


def load_answers():
    answers = []
    for f in sorted((FIX / "raw").glob("*/*.json")):
        rec = json.loads(f.read_text())
        text, urls = get_engine(rec["engine"]).parse(rec["result"])
        answers.append({"engine": rec["engine"], "prompt_id": rec["prompt_id"],
                        "cluster": rec["cluster"], "prompt": rec["prompt"],
                        "text": text, "urls": urls})
    return answers


def expected_rows():
    rows = [json.loads(line) for line in (FIX / "expected_rows.jsonl").read_text().splitlines()]
    return {(r["engine"], r["id"]): r for r in rows}


@pytest.fixture(scope="module")
def rows():
    return [analysis.analyze_answer(a, PATTERNS) for a in load_answers()]


def test_rows_match_original_survey(rows):
    expected = expected_rows()
    assert len(rows) == len(expected) == 21
    for row in rows:
        exp = expected[(row["engine"], row["id"])]
        for key in ("render_mentioned", "render_position", "brands_in_order", "n_citations",
                    "cited_domains", "render_cited", "render_urls", "onrender_urls",
                    "brands_named_in_prompt", "chars"):
            assert row[key] == exp[key], (row["engine"], row["id"], key)
        assert [h["pattern"] for h in row["stale_hits"]] == \
               [h["pattern"] for h in exp["stale_hits"]], (row["engine"], row["id"])


def test_render_verb_uses_are_not_brand_mentions():
    assert "Render" not in analysis.find_brands("React will render the page, then render your props.")
    assert "Render" not in analysis.find_brands("server-side rendering on Vercel")
    assert "Render" in analysis.find_brands("Deploy it on Render. It is simple.")
    assert "Render" in analysis.find_brands("see render.com/docs")


def test_position_ignores_brands_named_in_the_prompt():
    answer = {"engine": "x", "prompt_id": "c01", "cluster": "c_alt",
              "prompt": "What are the best Heroku alternatives?",
              "text": "Heroku is fine, but try Railway or Render. Fly.io also works.", "urls": []}
    row = analysis.analyze_answer(answer)
    assert row["brands_in_order"] == ["Heroku", "Railway", "Render", "Fly.io"]
    assert row["render_position"] == 2


def test_stale_hits_only_when_render_is_named():
    text = "Free Postgres databases expire after 90 days."
    base = {"engine": "x", "prompt_id": "p", "cluster": "f_render", "prompt": "q", "urls": []}
    assert analysis.analyze_answer({**base, "text": text}, PATTERNS)["stale_hits"] == []
    hit = analysis.analyze_answer({**base, "text": "On Render, " + text}, PATTERNS)["stale_hits"]
    assert [(h["pattern"], h["fact"]) for h in hit] == [("pg_90_days", "free_pg")]


def test_render_owned_domains():
    assert analysis.is_render_owned("render.com")
    assert analysis.is_render_owned("community.render.com")
    assert analysis.is_render_owned("render-www.onrender.com")
    assert not analysis.is_render_owned("myapp.onrender.com")
    assert not analysis.is_render_owned("notrender.com")
    assert analysis.clean_url("https://www.Render.com/docs/free/") == "render.com/docs/free"


def test_aggregate_counts(rows):
    m = analysis.aggregate(rows, BRANDED)
    exp = [r for r in expected_rows().values() if r["cluster"] not in BRANDED]
    assert m["n_answers"] == 21
    assert m["n_organic"] == len(exp) == 15
    assert m["organic_mention_all"] == [sum(r["render_mentioned"] for r in exp), 15]
    # b06 ("durable workflow engine for AI tasks") is the gap from the original survey.
    assert m["organic_mention_by_cluster"]["b_ai"] == [0, 5]
    assert m["organic_mention_by_engine"]["chatgpt"] == [2, 3]
    assert m["avg_position_organic"]["google_ai_mode"] == 2.0  # positions 3 and 1
    assert m["organic_cited_by_engine"]["chatgpt"] == [2, 3]
    assert {h["pattern"] for h in m["stale_hits"]} >= {"pg_90_days"}
    assert set(m["answers"]) == {f"{r['engine']}/{r['id']}" for r in rows}
    hits, total = m["render_citation_share_by_engine"]["chatgpt"]
    assert 0 < hits <= total


def test_diff_reports_flips_and_deltas(rows):
    prev = analysis.aggregate(rows, BRANDED)
    changed = [dict(r) for r in rows]
    for r in changed:
        if (r["engine"], r["id"]) == ("chatgpt", "a01"):
            r.update(render_mentioned=False, render_position=None, render_cited=False,
                     render_urls=[], brands_in_order=[b for b in r["brands_in_order"]
                                                       if b != "Render"])
    cur = analysis.aggregate(changed, BRANDED)
    d = analysis.diff(prev, cur)
    assert d["lost_mention"] == ["chatgpt/a01"]
    assert d["lost_citation"] == ["chatgpt/a01"]
    assert d["gained_mention"] == []
    assert d["organic_mention_by_engine"]["chatgpt"]["delta"] == pytest.approx(-33.3)
    before = dict(map(tuple, prev["sov_organic"]["mentions"]))["Render"]
    assert ["Render", before, before - 1] in d["sov_change"]
    assert analysis.diff(None, cur) is None


def test_diff_survives_json_round_trip(rows):
    m = analysis.aggregate(rows, BRANDED)
    again = json.loads(json.dumps(m))
    d = analysis.diff(again, m)
    assert d["lost_mention"] == d["gained_mention"] == []
    assert all(v["delta"] == 0 for v in d["organic_mention_by_engine"].values())
