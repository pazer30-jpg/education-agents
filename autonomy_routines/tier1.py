"""
autonomy_routines/tier1.py — Deterministic daily routines ($0 in Claude cost).

Each routine reports {"status": "ok"|"warn"|"error", "message": "...",
"summary": "..."} so autonomy.py can log + alert consistently.

Routines:
  03:00  corpus_refresh             — fetch fresh papers in active themes
  06:00  topic_radar                — queue tomorrow's topics in series-aware way
  06:00  curator_dynamic_priority   — refresh curator ranking
  11:00  engagement_refresher       — watch ~/Downloads for LinkedIn export CSVs
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from autonomy import routine
from config import OUTPUT_DIR


# ─────────────────────────────────────────────
# 03:00 · Tier 1 · Corpus refresh
# ─────────────────────────────────────────────

TRENDING_TOPICS_FILE = OUTPUT_DIR / "_state" / "trending_topics.json"


def _strong_topic_keywords() -> list[str]:
    """Pull 3-5 short keyword sets from strong_topics.md (recent winners)."""
    p = OUTPUT_DIR / "_memory" / "strong_topics.md"
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8", errors="replace")
    # Each line that mentions a topic title — naive heuristic: lines starting with `|`
    topics = []
    for line in text.splitlines():
        if line.startswith("|") and "%" in line:
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if cells and len(cells[0]) > 5 and not cells[0].startswith("נושא"):
                topics.append(cells[0][:80])
    return topics[:5]


@routine(hour=3, tier=1, cost=0.0,
         description="fetch 3-5 fresh papers per strong topic")
def corpus_refresh() -> dict:
    """Lightweight daily corpus growth — no LLM, just REST APIs."""
    topics = _strong_topic_keywords()
    if not topics:
        return {"status": "skipped",
                "message": "no strong topics available (memory empty)"}
    try:
        from agent1_researcher import search_openalex, _clean_openalex_papers
    except Exception as e:
        return {"status": "error", "message": f"researcher import failed: {e}"}
    PAPERS_DIR = OUTPUT_DIR / "papers" / "_refresh"
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    fetched = 0
    for t in topics[:3]:  # cap to keep runtime under 2 min
        try:
            raw = search_openalex(t, limit=5)
            cleaned = _clean_openalex_papers(raw)
            if cleaned:
                out = PAPERS_DIR / f"{datetime.now().strftime('%Y%m%d')}_{t[:30].replace(' ', '_')}.json"
                out.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2),
                               encoding="utf-8")
                fetched += len(cleaned)
        except Exception:
            continue  # one source failing shouldn't kill the routine
    return {"status":  "ok",
            "message": f"fetched {fetched} papers across {len(topics[:3])} topics",
            "summary": f"corpus +{fetched}"}


# ─────────────────────────────────────────────
# 06:00 · Tier 1 · Topic radar
# ─────────────────────────────────────────────

TOPIC_QUEUE_FILE = OUTPUT_DIR / "_state" / "topic_queue.json"


@routine(hour=6, tier=1, cost=0.0,
         description="queue tomorrow's topics in series-aware way")
def topic_radar() -> dict:
    """
    Prepare a queue of 5 recommended topics for tomorrow's pipeline:
      - First slot: continuation of active series if one exists
      - Slots 2-5: strong topics not used in last 14 days + trending hints
    No LLM call — Planner reads this queue and just picks from it.
    """
    queue = []

    # 1) Active series gets first slot
    try:
        from series import detect_active_series
        active = detect_active_series()
        if active:
            angle = active.get("next_angle") or active["theme"]
            queue.append({
                "source":   "series",
                "title":    f"[{active['theme']}] {angle}",
                "priority": 100,
            })
    except Exception:
        pass

    # 2) Strong topics not used recently
    try:
        from memory import load_memory
        mem = load_memory()
        coverage = mem.get("coverage_map", {})
        recent = {a.get("topic", "") for a in mem.get("articles", [])[-10:]}
        # Pick high-coverage topics that haven't been written about in last 10 articles
        strong = sorted(coverage.items(), key=lambda kv: -kv[1])
        for t, score in strong[:30]:
            if t in recent or len(t) < 10:
                continue
            queue.append({"source": "strong_topics", "title": t, "priority": score})
            if len(queue) >= 5:
                break
    except Exception:
        pass

    # 3) Trending hints (from trending.py — Reddit/HN/arXiv)
    try:
        from trending import fetch_trending_topics
        for t in fetch_trending_topics(max_topics=3)[:3]:
            queue.append({
                "source":   f"trending:{t.get('source','')}",
                "title":    t.get("title", "")[:120],
                "priority": int(t.get("score", 0)),
            })
    except Exception:
        pass

    queue.sort(key=lambda x: -x["priority"])
    queue = queue[:5]
    TOPIC_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOPIC_QUEUE_FILE.write_text(
        json.dumps({"generated_at": datetime.now().isoformat(timespec="seconds"),
                    "queue": queue}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    titles = [q["title"][:60] for q in queue]
    return {"status":  "ok",
            "message": f"queued {len(queue)} topics for tomorrow",
            "summary": " · ".join(titles)[:200]}


# ─────────────────────────────────────────────
# 06:00 · Tier 1 · Curator dynamic priority
# ─────────────────────────────────────────────

@routine(hour=6, tier=1, cost=0.0,
         description="refresh curator ranking using latest engagement")
def curator_dynamic_priority() -> dict:
    """Re-rank ready posts using the latest engagement signals."""
    try:
        from agent10_curator import _candidates, _rank, write_report
        cands = _candidates()
        picks = _rank(cands, top_n=7)
        write_report(picks)
        return {"status":  "ok",
                "message": f"ranked {len(picks)} posts for the week",
                "summary": f"top: {picks[0]['platform']} score={picks[0]['score']:.0f}"
                            if picks else "no posts ready"}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}


# ─────────────────────────────────────────────
# 11:00 · Tier 1 · Engagement refresher
# ─────────────────────────────────────────────

@routine(hour=11, tier=1, cost=0.0,
         description="auto-import any new LinkedIn CSV from ~/Downloads")
def engagement_refresher() -> dict:
    """Watch ~/Downloads for fresh LinkedIn CSV exports; auto-import."""
    downloads = Path.home() / "Downloads"
    if not downloads.exists():
        return {"status": "skipped", "message": "no ~/Downloads"}
    # Filename hints LinkedIn uses
    candidates = []
    cutoff = datetime.now() - timedelta(hours=36)
    for p in downloads.glob("*.csv"):
        name = p.name.lower()
        if not any(h in name for h in ("linkedin", "analytics", "posts", "engagement")):
            continue
        if datetime.fromtimestamp(p.stat().st_mtime) < cutoff:
            continue
        candidates.append(p)
    if not candidates:
        return {"status": "ok", "message": "no fresh LinkedIn CSV in ~/Downloads"}
    candidates.sort(key=lambda p: -p.stat().st_mtime)
    target = candidates[0]
    try:
        from linkedin_analytics_import import ingest
        res = ingest(target, dry_run=False)
        return {
            "status":  "ok" if res.get("updated", 0) else "warn",
            "message": (f"imported {target.name}: "
                        f"matched {res.get('matched',0)}, "
                        f"updated {res.get('updated',0)}, "
                        f"unmatched {len(res.get('unmatched',[]))}"),
            "summary": f"LinkedIn CSV +{res.get('updated',0)} updates",
        }
    except Exception as e:
        return {"status": "error", "message": f"import failed: {e}"}
