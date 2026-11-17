"""Pure functions that turn answers into survey metrics.

Input: answer dicts with keys engine, prompt_id, cluster, prompt, text, urls.
Nothing here does I/O, so the logic is easy to test and to re-run on stored
answers when a brand pattern changes.

Method (same as the original one-off survey):
- A brand is "mentioned" when its regex matches the answer text.
- Position is the rank of Render's first mention among all brands found,
  ignoring brands that the prompt itself names (for "Heroku alternatives",
  Heroku does not take position 1).
- "Organic" answers come from prompts that do not name Render.
- A citation is any URL the engine returned as a source.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Iterable
from urllib.parse import urlparse

TARGET = "Render"

# Render is case-sensitive and excludes common verb uses ("render the page").
BRANDS: dict[str, str] = {
    "Render": r"\bRender(?:\.com)?\b(?!\s+(?:the|a|an|your|it|HTML|function|props|method)\b)|render\.com",
    "Railway": r"\bRailway\b",
    "Fly.io": r"\bFly(?:\.io)?\b(?=[\s.,)\]:*]|$)",
    "Heroku": r"\bHeroku\b",
    "Vercel": r"\bVercel\b",
    "Netlify": r"\bNetlify\b",
    "DigitalOcean": r"\bDigital\s?Ocean\b",
    "Northflank": r"\bNorthflank\b",
    "AWS": r"\bAWS\b|Amazon Web Services|\bECS\b|\bApp Runner\b|Elastic Beanstalk|\bLambda\b",
    "Google Cloud Run": r"Cloud Run",
    "Azure": r"\bAzure\b",
    "Koyeb": r"\bKoyeb\b",
    "Modal": r"\bModal\b(?!\s+(?:dialog|window))",
    "Trigger.dev": r"Trigger\.dev",
    "Temporal": r"\bTemporal\b",
    "Inngest": r"\bInngest\b",
    "Supabase": r"\bSupabase\b",
    "Neon": r"\bNeon\b",
    "Upstash": r"\bUpstash\b",
    "Cloudflare": r"\bCloudflare\b",
    "Coolify": r"\bCoolify\b",
    "Hetzner": r"\bHetzner\b",
    "LangGraph Platform/LangSmith": r"LangGraph (?:Platform|Cloud)|LangSmith Deployment|LangGraph Server",
    "Hatchet": r"\bHatchet\b",
    "Celery": r"\bCelery\b",
    "Sevalla": r"\bSevalla\b",
    "Porter": r"\bPorter\b",
    "Aiven": r"\bAiven\b",
    "Replit": r"\bReplit\b",
}
BRAND_RE = {name: re.compile(rx) for name, rx in BRANDS.items()}


# --- URLs -------------------------------------------------------------------

def domain(url: str) -> str:
    d = urlparse(url).netloc.lower()
    return d[4:] if d.startswith("www.") else d


def is_render_owned(d: str) -> bool:
    # render-www.onrender.com serves Render's own marketing site.
    return d == "render.com" or d.endswith(".render.com") or d == "render-www.onrender.com"


def clean_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.netloc.lower().removeprefix('www.')}{p.path.rstrip('/')}"


# --- per-answer extraction --------------------------------------------------

def find_brands(text: str) -> dict[str, int]:
    """Map each brand found in `text` to the offset of its first match."""
    first = {}
    for name, rx in BRAND_RE.items():
        m = rx.search(text)
        if m:
            first[name] = m.start()
    return first


def compile_stale_patterns(ground_truth: dict) -> list[tuple[str, str, re.Pattern]]:
    """Return (pattern id, fact key, compiled regex) for every stale pattern."""
    out = []
    for fact in ground_truth.get("facts", []):
        for p in fact.get("stale_patterns", []):
            out.append((p["id"], fact["key"], re.compile(p["regex"])))
    return out


def stale_hits(text: str, patterns: list[tuple[str, str, re.Pattern]]) -> list[dict]:
    hits = []
    for pid, fact_key, rx in patterns:
        for m in rx.finditer(text):
            hits.append({"pattern": pid, "fact": fact_key,
                         "snippet": text[max(0, m.start() - 160):m.end() + 120]})
    return hits


def analyze_answer(answer: dict, stale_patterns: list | None = None) -> dict:
    """Extract one row of facts from one answer."""
    text, urls, prompt = answer["text"], answer["urls"], answer["prompt"]
    first = find_brands(text)
    order = sorted(first, key=first.get)
    in_prompt = {b for b, rx in BRAND_RE.items() if rx.search(prompt)}
    ranked = [b for b in order if b not in in_prompt or b == TARGET]
    position = ranked.index(TARGET) + 1 if TARGET in ranked else None
    domains = [domain(u) for u in urls]
    render_urls = [clean_url(u) for u, d in zip(urls, domains) if is_render_owned(d)]
    onrender_urls = [clean_url(u) for u, d in zip(urls, domains) if d.endswith(".onrender.com")]
    mentioned = TARGET in first
    return {
        "engine": answer["engine"], "id": answer["prompt_id"], "cluster": answer["cluster"],
        "prompt": prompt, "chars": len(text),
        "render_mentioned": mentioned, "render_position": position,
        "n_brands": len(order), "brands_in_order": order,
        "n_citations": len(urls), "n_render_citations": len(render_urls),
        "cited_domains": sorted(set(domains)),
        "render_cited": bool(render_urls), "render_urls": render_urls,
        "onrender_urls": onrender_urls, "brands_named_in_prompt": sorted(in_prompt),
        "stale_hits": stale_hits(text, stale_patterns or []) if mentioned else [],
    }


# --- aggregation ------------------------------------------------------------

def rate(rows: list[dict], key: str = "render_mentioned") -> list[int]:
    """[hits, total] (a list so it survives a JSON round trip unchanged)."""
    return [sum(1 for r in rows if r[key]), len(rows)]


def aggregate(rows: list[dict], branded_clusters: Iterable[str]) -> dict:
    branded = set(branded_clusters)
    engines = sorted({r["engine"] for r in rows})
    clusters = sorted({r["cluster"] for r in rows})
    organic = [r for r in rows if r["cluster"] not in branded]

    def by(rs, engine=None, cluster=None):
        return [r for r in rs if (engine is None or r["engine"] == engine)
                and (cluster is None or r["cluster"] == cluster)]

    m: dict = {"n_answers": len(rows), "n_organic": len(organic), "engines": engines,
               "clusters": clusters, "branded_clusters": sorted(branded)}

    m["mention_by_engine_cluster"] = {
        e: {c: rate(by(rows, e, c)) for c in clusters if by(rows, e, c)} for e in engines}
    m["organic_mention_by_engine"] = {e: rate(by(organic, e)) for e in engines}
    m["organic_mention_all"] = rate(organic)
    m["organic_mention_by_cluster"] = {c: rate(by(organic, cluster=c))
                                       for c in clusters if c not in branded}

    positions = defaultdict(list)
    for r in organic:
        if r["render_position"]:
            positions[r["engine"]].append(r["render_position"])
    m["avg_position_organic"] = {e: round(sum(v) / len(v), 2) for e, v in sorted(positions.items())}
    m["first_position_share_organic"] = {
        e: [sum(1 for x in v if x == 1), len(v)] for e, v in sorted(positions.items())}

    # Share of voice: number of organic answers that name each brand.
    sov, top1 = Counter(), Counter()
    sov_engine = defaultdict(Counter)
    for r in organic:
        for b in r["brands_in_order"]:
            sov[b] += 1
            sov_engine[r["engine"]][b] += 1
        if r["brands_in_order"]:
            top1[r["brands_in_order"][0]] += 1
    m["sov_organic"] = {"n_answers": len(organic), "mentions": sov.most_common(25)}
    m["sov_organic_by_engine"] = {e: c.most_common(10) for e, c in sorted(sov_engine.items())}
    m["first_named_brand_organic"] = top1.most_common(12)
    m["sov_by_cluster"] = {}
    for c in clusters:
        rs = by(rows, cluster=c)
        cc = Counter(b for r in rs for b in r["brands_in_order"])
        m["sov_by_cluster"][c] = {"n": len(rs), "top": cc.most_common(8)}

    # Citations.
    m["organic_cited_by_engine"] = {e: rate(by(organic, e), "render_cited") for e in engines}
    m["render_cited_by_engine"] = {e: rate(by(rows, e), "render_cited") for e in engines}
    m["render_cited_by_engine_cluster"] = {
        e: {c: rate(by(rows, e, c), "render_cited") for c in clusters if by(rows, e, c)}
        for e in engines}
    # Citation share: render-owned URLs as a share of all cited URLs.
    m["render_citation_share_by_engine"] = {
        e: [sum(r["n_render_citations"] for r in by(rows, e)),
            sum(r["n_citations"] for r in by(rows, e))] for e in engines}
    m["answers_with_any_citation_by_engine"] = {
        e: [sum(1 for r in by(rows, e) if r["n_citations"]), len(by(rows, e))] for e in engines}
    m["top_cited_domains_by_answers"] = Counter(
        d for r in rows for d in r["cited_domains"]).most_common(30)
    m["render_pages_cited"] = Counter(u for r in rows for u in set(r["render_urls"])).most_common(40)
    m["cited_domains_compare_cluster"] = Counter(
        d for r in rows if r["cluster"] == "e_compare" for d in r["cited_domains"]).most_common(15)
    m["onrender_urls_cited"] = Counter(u for r in rows for u in set(r["onrender_urls"])).most_common(20)

    m["organic_misses"] = [[r["engine"], r["id"], r["brands_in_order"][:6]]
                           for r in organic if not r["render_mentioned"]]
    m["stale_hits"] = [{"engine": r["engine"], "id": r["id"], **h}
                       for r in rows for h in r["stale_hits"]]
    # Compact per-answer state, kept so the next run can diff prompt by prompt.
    m["answers"] = {f"{r['engine']}/{r['id']}": {
        "engine": r["engine"], "cluster": r["cluster"], "organic": r["cluster"] not in branded,
        "mentioned": r["render_mentioned"], "position": r["render_position"],
        "cited": r["render_cited"], "brands": r["brands_in_order"],
        "render_urls": sorted(set(r["render_urls"]))} for r in rows}
    return m


# --- run-to-run diff --------------------------------------------------------

def _pct(pair) -> float | None:
    hits, total = pair
    return 100 * hits / total if total else None


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, 1)


def _delta(prev: dict, cur: dict) -> dict:
    """Percentage-point change for each key of two {key: [hits, total]} maps."""
    out = {}
    for k in sorted(set(prev) | set(cur)):
        a = _pct(prev[k]) if k in prev else None
        b = _pct(cur[k]) if k in cur else None
        out[k] = {"prev": _round(a), "cur": _round(b),
                  "delta": _round(b - a) if a is not None and b is not None else None}
    return out


def _paired_rates(answers: dict, keys: list[str]) -> dict:
    organic = [answers[k] for k in keys if answers[k]["organic"]]

    def rates(group_by: str, field: str) -> dict:
        out: dict[str, list[int]] = {}
        for a in organic:
            pair = out.setdefault(a[group_by], [0, 0])
            pair[0] += bool(a[field])
            pair[1] += 1
        return dict(sorted(out.items()))

    return {
        "all": [sum(a["mentioned"] for a in organic), len(organic)],
        "by_engine": rates("engine", "mentioned"),
        "by_cluster": rates("cluster", "mentioned"),
        "cited_by_engine": rates("engine", "cited"),
        "sov": Counter(b for a in organic for b in a["brands"]),
        "pages": {u for k in keys for u in answers[k]["render_urls"]},
    }


def diff(prev: dict | None, cur: dict) -> dict | None:
    """Compare two aggregate() results on the (engine, prompt) pairs both runs share.

    Pairing keeps the comparison fair when engines or prompts change between
    runs. Returns None when there is no previous run.
    """
    if not prev:
        return None
    pa, ca = prev.get("answers", {}), cur.get("answers", {})
    common = sorted(set(pa) & set(ca))
    p, c = _paired_rates(pa, common), _paired_rates(ca, common)
    d = {
        "n_compared_answers": len(common),
        "organic_mention_all": _delta({"all": p["all"]}, {"all": c["all"]})["all"],
        "organic_mention_by_engine": _delta(p["by_engine"], c["by_engine"]),
        "organic_mention_by_cluster": _delta(p["by_cluster"], c["by_cluster"]),
        "organic_cited_by_engine": _delta(p["cited_by_engine"], c["cited_by_engine"]),
        "gained_mention": [k for k in common if ca[k]["mentioned"] and not pa[k]["mentioned"]],
        "lost_mention": [k for k in common if pa[k]["mentioned"] and not ca[k]["mentioned"]],
        "gained_citation": [k for k in common if ca[k]["cited"] and not pa[k]["cited"]],
        "lost_citation": [k for k in common if pa[k]["cited"] and not ca[k]["cited"]],
        "new_render_pages_cited": sorted(c["pages"] - p["pages"]),
        "dropped_render_pages_cited": sorted(p["pages"] - c["pages"]),
    }
    d["sov_change"] = sorted(
        ([b, p["sov"][b], c["sov"][b]] for b in set(p["sov"]) | set(c["sov"])
         if p["sov"][b] != c["sov"][b]),
        key=lambda x: (-abs(x[2] - x[1]), x[0]))[:15]
    return d
