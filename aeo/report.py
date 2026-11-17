"""Render metrics (and the diff to the previous run) as a Markdown report."""

from __future__ import annotations


def _r(pair) -> str:
    hits, total = pair
    return f"{hits}/{total} ({100 * hits / total:.0f}%)" if total else "-"


def _table(header: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _signed(x) -> str:
    return "-" if x is None else f"{x:+.1f}"


def _num(x) -> str:
    return "-" if x is None else f"{x}"


def build_report(run: dict, metrics: dict, diff: dict | None, engines_info: dict[str, str]) -> str:
    m = metrics
    engines = m["engines"]
    out = [f"# AEO citation survey: {run['run_id']}", ""]
    out.append(f"- Started: {run.get('started_at', '-')}")
    out.append(f"- Prompt set: `{run.get('prompt_set')}` ({m['n_answers']} answers, "
               f"{m['n_organic']} organic)")
    cost = run.get("cost_usd")
    if cost is not None:
        out.append(f"- Engine spend: ${cost:.3f}")
    failures = run.get("failures") or []
    out.append(f"- Failed calls: {len(failures)}")
    out.append("- Organic means the prompt does not name Render "
               f"(excludes {', '.join(m['branded_clusters'])}).")
    out += ["", "## Engines", ""]
    out.append(_table(["Engine", "What it is"], [[e, engines_info.get(e, "")] for e in engines]))

    out += ["", "## Render mention rate (organic)", ""]
    rows = [[e, _r(m["organic_mention_by_engine"][e]), m["avg_position_organic"].get(e, "-"),
             _r(m["first_position_share_organic"].get(e, [0, 0])),
             _r(m["organic_cited_by_engine"][e])] for e in engines]
    rows.append(["**all**", _r(m["organic_mention_all"]), "", "", ""])
    out.append(_table(["Engine", "Mentioned", "Avg position", "Named first", "render.com cited"],
                      rows))

    out += ["", "## Mention rate by cluster and engine", ""]
    clusters = m["clusters"]
    out.append(_table(["Cluster"] + engines, [
        [c] + [_r(m["mention_by_engine_cluster"][e].get(c, [0, 0])) for e in engines]
        for c in clusters]))

    out += ["", "## Share of voice (organic answers that name each brand)", ""]
    n = m["sov_organic"]["n_answers"]
    out.append(_table(["Brand", "Answers", "Share"],
                      [[b, c, f"{100 * c / n:.0f}%"] for b, c in m["sov_organic"]["mentions"][:12]]))

    out += ["", "## Citations", ""]
    out.append(_table(["Engine", "Answers citing render.com", "render.com share of all cited URLs",
                       "Answers with any citation"], [
        [e, _r(m["render_cited_by_engine"][e]), _r(m["render_citation_share_by_engine"][e]),
         _r(m["answers_with_any_citation_by_engine"][e])] for e in engines]))
    out += ["", "Most cited domains (number of answers):", ""]
    out.append(", ".join(f"{d} ({c})" for d, c in m["top_cited_domains_by_answers"][:15]) or "none")
    out += ["", "Render pages cited:", ""]
    out += [f"- {u} ({c})" for u, c in m["render_pages_cited"][:20]] or ["- none"]
    if m["onrender_urls_cited"]:
        out += ["", "onrender.com URLs cited (possible duplicate of render.com pages):", ""]
        out += [f"- {u} ({c})" for u, c in m["onrender_urls_cited"]]

    out += ["", "## Organic answers that do not name Render", ""]
    out += [f"- {e} / {pid}: {', '.join(b) or 'no brands'}" for e, pid, b in m["organic_misses"]] \
        or ["- none"]

    out += ["", "## Stale-fact candidates (regex hits, review by hand)", ""]
    if m["stale_hits"]:
        for h in m["stale_hits"]:
            snippet = " ".join(h["snippet"].split())
            out.append(f"- **{h['pattern']}** ({h['engine']} / {h['id']}, fact `{h['fact']}`): "
                       f"\"...{snippet}...\"")
    else:
        out.append("- none")

    out += ["", "## Change since previous run", ""]
    if not diff:
        out.append("No previous completed run. This run is the baseline.")
    else:
        out.append(f"Previous run: `{run.get('previous_run_id')}`. "
                   f"{diff['n_compared_answers']} (engine, prompt) pairs appear in both runs. "
                   "Every number in this section uses only those pairs.")
        out.append("")
        a = diff["organic_mention_all"]
        out.append(f"Organic mention rate: {_num(a['prev'])}% -> {_num(a['cur'])}% "
                   f"({_signed(a['delta'])} pts)")
        out.append("")
        out.append(_table(["Engine", "Mentioned prev %", "now %", "change", "Cited change"], [
            [e, _num(v["prev"]), _num(v["cur"]), _signed(v["delta"]),
             _signed(diff["organic_cited_by_engine"].get(e, {}).get("delta"))]
            for e, v in diff["organic_mention_by_engine"].items()]))
        out.append("")
        out.append(_table(["Cluster", "prev %", "now %", "change"], [
            [c, _num(v["prev"]), _num(v["cur"]), _signed(v["delta"])]
            for c, v in diff["organic_mention_by_cluster"].items()]))
        for key, label in [("gained_mention", "Now names Render"),
                           ("lost_mention", "No longer names Render"),
                           ("gained_citation", "Now cites render.com"),
                           ("lost_citation", "No longer cites render.com"),
                           ("new_render_pages_cited", "Render pages cited for the first time"),
                           ("dropped_render_pages_cited", "Render pages no longer cited")]:
            if diff[key]:
                out += ["", f"{label}: " + ", ".join(diff[key])]
        if diff["sov_change"]:
            out += ["", "Share-of-voice changes (answers, prev -> now): " + ", ".join(
                f"{b} {p}->{c}" for b, p, c in diff["sov_change"])]
        out += ["", "Answers are nondeterministic. Treat a change of one or two answers "
                    "per cell as noise until it repeats over several runs."]

    if failures:
        out += ["", "## Failed calls", ""]
        out += [f"- {f['engine']} / {f['prompt_id']}: {f['error'][:200]}" for f in failures]
    return "\n".join(out) + "\n"
