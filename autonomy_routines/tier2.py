"""
autonomy_routines/tier2.py — Cheap-LLM daily routines (~$1.50/day budget).

Each routine wraps a single Claude call ≤$0.50 with a clear use case.
Skipped automatically if MOKI_AUTONOMY_TIER < 2.

Routines:
  02:30  citation_watcher        — re-verify weekly orphan citations vs corpus
  04:00  trend_mapping           — synthesize what trends in last 14d corpus
  06:30  outline_prewarm         — generate outline for tomorrow's top topic
  09:30  weekly_meta_synthesis   — Monday-only reflective journal entry
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from autonomy import routine
from config import OUTPUT_DIR


# ─────────────────────────────────────────────
# 02:30 · Tier 2 · Citation watcher
# ─────────────────────────────────────────────

@routine(hour=2, tier=2, cost=0.30,
         description="re-verify last week's orphan citations against fresh corpus")
def citation_watcher() -> dict:
    """Look at recent FactCheck reports for orphan citations; try to match
    them against the now-larger corpus (after corpus_refresh ran)."""
    try:
        from agent2_7_fact_checker import _load_papers, _match_citation
    except Exception as e:
        return {"status": "error", "message": f"import failed: {e}"}

    # Find the last 7 days of fact_check reports
    reports = list(OUTPUT_DIR.glob("fact_check_*.json"))
    cutoff = datetime.now() - timedelta(days=7)
    fresh_reports = [r for r in reports
                     if datetime.fromtimestamp(r.stat().st_mtime) >= cutoff]
    if not fresh_reports:
        return {"status": "skipped", "message": "no fact_check reports in last 7d"}

    # Collect all orphan citations
    orphans = []
    for rpt in fresh_reports[:5]:
        try:
            data = json.loads(rpt.read_text(encoding="utf-8"))
            for s in data.get("suspicious", []):
                if "orphan" in (s.get("reason") or ""):
                    orphans.append(s)
        except Exception:
            continue

    if not orphans:
        return {"status": "ok", "message": "no orphan citations in last 7d"}

    # Re-match against ALL papers across the corpus (post-refresh)
    papers = []
    for pf in (OUTPUT_DIR / "papers").glob("*_enriched.json"):
        try:
            d = json.loads(pf.read_text(encoding="utf-8"))
            papers.extend(d.get("papers", []) if isinstance(d, dict) else d)
        except Exception:
            continue

    resolved = 0
    for o in orphans:
        # Parse author and year out of the citation string
        cite_str = o.get("citation", "")
        import re
        m = re.search(r"\(([^,]+),\s*(\d{4})", cite_str) or \
            re.search(r"\[\[([^\]]+?)\s+(\d{4})", cite_str)
        if not m:
            continue
        cite = {"author_blob": m.group(1).strip(),
                "year":         m.group(2).strip()}
        if _match_citation(cite, papers):
            resolved += 1

    return {"status":  "ok" if resolved else "warn",
            "message": f"{resolved}/{len(orphans)} orphans now resolved by fresh corpus",
            "summary": f"orphans resolved: {resolved}/{len(orphans)}"}


# ─────────────────────────────────────────────
# 04:00 · Tier 2 · Trend mapping
# ─────────────────────────────────────────────

@routine(hour=4, tier=2, cost=0.30,
         description="synthesize trends visible in last 14d corpus growth")
def trend_mapping() -> dict:
    """Look at papers added by corpus_refresh in last 14d; ask Claude for
    the 3 strongest themes + which one is rising."""
    refresh_dir = OUTPUT_DIR / "papers" / "_refresh"
    if not refresh_dir.exists():
        return {"status": "skipped", "message": "no refresh corpus yet"}

    cutoff = datetime.now() - timedelta(days=14)
    fresh_titles = []
    for p in refresh_dir.glob("*.json"):
        if datetime.fromtimestamp(p.stat().st_mtime) < cutoff:
            continue
        try:
            for paper in json.loads(p.read_text(encoding="utf-8")):
                if t := paper.get("title"):
                    fresh_titles.append(t[:120])
        except Exception:
            continue

    if len(fresh_titles) < 10:
        return {"status": "skipped",
                "message": f"only {len(fresh_titles)} fresh papers — need ≥10"}

    try:
        from claude_cli import ask_claude
    except Exception as e:
        return {"status": "error", "message": f"claude_cli unavailable: {e}"}

    titles_block = "\n".join(f"  - {t}" for t in fresh_titles[:50])
    prompt = (
        "Below are titles of academic papers added to a non-formal-education "
        "corpus over the last 14 days. Identify the 3 strongest themes and "
        "which ONE is showing acceleration (mentions growing). Reply concise.\n\n"
        f"{titles_block}\n\n"
        "Format:\n"
        "  THEME 1: <name>\n  THEME 2: <name>\n  THEME 3: <name>\n"
        "  RISING: <theme name + why in one sentence>"
    )
    try:
        out = ask_claude(prompt, max_budget=0.30, timeout=120, max_retries=1)
    except Exception as e:
        return {"status": "error", "message": f"claude failed: {e}"}

    # Save the report
    p = OUTPUT_DIR / "_memory" / "trend_map.md"
    body = (f"---\nmoki: true\ntype: trend_map\n"
            f"updated: {datetime.now().isoformat(timespec='seconds')}\n---\n\n"
            f"# 📈 Trend map ({len(fresh_titles)} fresh papers · last 14d)\n\n"
            f"```\n{out.strip()}\n```\n")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return {"status":  "ok",
            "message": f"trend map updated from {len(fresh_titles)} fresh titles",
            "summary": out.strip().splitlines()[-1][:150] if out.strip() else ""}


# ─────────────────────────────────────────────
# 06:30 · Tier 2 · Outline pre-warming
# ─────────────────────────────────────────────

@routine(hour=6, tier=2, cost=0.50,
         description="generate outline for tomorrow's top queued topic")
def outline_prewarm() -> dict:
    """Read topic_queue.json; build an outline for the #1 entry so the
    morning Writer step starts with a ready scaffold."""
    queue_path = OUTPUT_DIR / "_state" / "topic_queue.json"
    if not queue_path.exists():
        return {"status": "skipped", "message": "no topic_queue.json (topic_radar didn't run)"}
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"status": "error", "message": f"queue parse failed: {e}"}
    queue = data.get("queue") or []
    if not queue:
        return {"status": "skipped", "message": "queue empty"}
    top = queue[0]
    title = top.get("title", "")
    if not title:
        return {"status": "skipped", "message": "top entry has no title"}

    try:
        from claude_cli import ask_claude_json
    except Exception as e:
        return {"status": "error", "message": f"claude_cli unavailable: {e}"}

    prompt = (
        f"Topic: {title}\n\n"
        "Build a 6-section outline for an academic-yet-readable review article "
        "(APA 7 style, 2,000-3,000 words). For each section: title + 1-line "
        "thrust + 2 research question hooks. Return JSON:\n"
        '{"sections": [{"title": "...", "thrust": "...", "rq_hooks": ["...", "..."]}]}'
    )
    try:
        out = ask_claude_json(prompt, max_budget=0.50, timeout=120)
    except Exception as e:
        return {"status": "error", "message": f"claude failed: {e}"}

    if not isinstance(out, dict) or not out.get("sections"):
        return {"status": "error", "message": "claude returned no sections"}

    p = OUTPUT_DIR / "_state" / "prewarmed_outline.json"
    p.write_text(json.dumps({
        "topic":        title,
        "outline":      out,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status":  "ok",
            "message": f"outline ready for: {title[:80]}",
            "summary": f"{len(out['sections'])} sections cached"}


# ─────────────────────────────────────────────
# 09:30 Monday only · Tier 2 · Weekly meta-synthesis
# ─────────────────────────────────────────────

@routine(hour=9, tier=2, cost=0.40, weekday_only=0,
         description="reflective journal of the week (Mondays only)")
def weekly_meta_synthesis() -> dict:
    """Read the past 7 daily journal entries + autonomy_log; ask Claude
    for the meta-narrative."""
    journal_dir = OUTPUT_DIR / "_journal"
    if not journal_dir.exists():
        return {"status": "skipped", "message": "no _journal directory"}

    cutoff = datetime.now() - timedelta(days=7)
    fresh = []
    for p in journal_dir.glob("*.md"):
        if p.name.startswith("_"):
            continue
        if datetime.fromtimestamp(p.stat().st_mtime) < cutoff:
            continue
        try:
            fresh.append(p.read_text(encoding="utf-8", errors="replace")[:2500])
        except Exception:
            continue
    if len(fresh) < 2:
        return {"status": "skipped",
                "message": f"only {len(fresh)} journal entries this week — need ≥2"}

    try:
        from claude_cli import ask_claude
    except Exception as e:
        return {"status": "error", "message": f"claude_cli unavailable: {e}"}

    blob = "\n\n---\n\n".join(fresh[:5])[:12000]
    prompt = (
        "מתחת לזה — 5 רישומים אחרונים מהיומן המחקרי השבועי של מערכת חינוכית "
        "אוטומטית. כתוב סינתזה מטא של 200-300 מילים, בעברית: מה הופתעת ממנו? "
        "אילו דפוסים חזרו על עצמם? מה חוזר ברצף הזה? אל תסכם — תפרש.\n\n"
        f"{blob}"
    )
    try:
        out = ask_claude(prompt, max_budget=0.40, timeout=180, max_retries=1)
    except Exception as e:
        return {"status": "error", "message": f"claude failed: {e}"}

    p = OUTPUT_DIR / "_journal" / f"_מטא-{datetime.now().strftime('%Y%m%d')}.md"
    body = (f"---\nmoki: true\ntype: weekly_meta\n"
            f"updated: {datetime.now().isoformat(timespec='seconds')}\n---\n\n"
            f"# 🌐 מטא־סינתזה שבועית\n\n{out.strip()}\n")
    p.write_text(body, encoding="utf-8")
    return {"status":  "ok",
            "message": f"weekly meta written: {p.name}",
            "summary": out.strip().split('\n')[0][:140] if out.strip() else ""}
