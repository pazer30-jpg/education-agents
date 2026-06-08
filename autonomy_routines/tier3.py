"""
autonomy_routines/tier3.py — Creative-LLM daily routines (~$2.00/day extra).

Skipped automatically if MOKI_AUTONOMY_TIER < 3.

Routines:
  02:00  retroactive_polishing  — re-apply latest humanize rules to old drafts
  05:00  repurposer             — turn a 90+ day old strong article into fresh LinkedIn
  23:00  visual_backlog         — generate cover for one posted-but-uncovered article
"""

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from autonomy import routine
from config import OUTPUT_DIR


# ─────────────────────────────────────────────
# 02:00 · Tier 3 · Retroactive polishing
# ─────────────────────────────────────────────

@routine(hour=2, tier=3, cost=1.00,
         description="re-polish one old article when humanize rules updated")
def retroactive_polishing() -> dict:
    """If humanize_rules.md or agent_backstories.md was edited in the last
    24h, re-run editor on the most recent article so it reflects the
    new rules."""
    rules_files = [
        OUTPUT_DIR / "_memory" / "humanize_rules.md",
        OUTPUT_DIR / "_memory" / "agent_backstories.md",
        OUTPUT_DIR / "_memory" / "voice_rules.md",
    ]
    cutoff = datetime.now() - timedelta(hours=24)
    fresh_rule = False
    for p in rules_files:
        try:
            if p.exists() and datetime.fromtimestamp(p.stat().st_mtime) > cutoff:
                fresh_rule = True
                break
        except Exception:
            continue
    if not fresh_rule:
        return {"status": "skipped", "message": "no rule files changed in last 24h"}

    # Find the most recent article
    arts_dir = OUTPUT_DIR / "articles"
    if not arts_dir.exists():
        return {"status": "skipped", "message": "no articles directory"}
    arts = sorted((p for p in arts_dir.glob("*.md") if not p.name.endswith(".bak")),
                  key=lambda p: -p.stat().st_mtime)
    if not arts:
        return {"status": "skipped", "message": "no articles to polish"}
    target = arts[0]

    try:
        from agent_editor import edit_article
        edit_article({"md": target, "docx": target.with_suffix(".docx")})
        return {"status":  "ok",
                "message": f"re-polished {target.name} with updated rules",
                "summary": f"editor replay → {target.stem[:60]}"}
    except Exception as e:
        return {"status": "error", "message": f"edit_article failed: {e}"}


# ─────────────────────────────────────────────
# 05:00 · Tier 3 · Repurposer
# ─────────────────────────────────────────────

@routine(hour=5, tier=3, cost=1.00,
         description="repurpose one strong 90+ day old article into a fresh LinkedIn post")
def repurposer() -> dict:
    """Find an article older than 90 days that scored well in QA but whose
    LinkedIn variant is older or missing. Generate a NEW LinkedIn post
    in the current voice."""
    arts_dir = OUTPUT_DIR / "articles"
    if not arts_dir.exists():
        return {"status": "skipped", "message": "no articles directory"}

    cutoff_old = datetime.now() - timedelta(days=90)
    candidates = []
    for p in arts_dir.glob("*.md"):
        if p.name.endswith(".bak"):
            continue
        try:
            if datetime.fromtimestamp(p.stat().st_mtime) <= cutoff_old:
                candidates.append(p)
        except Exception:
            continue
    if not candidates:
        return {"status": "skipped", "message": "no articles older than 90d"}

    # Pick the one with no recent LinkedIn variant
    li_dir = OUTPUT_DIR / "posts" / "linkedin"
    recent_li_stems = set()
    cutoff_recent = datetime.now() - timedelta(days=30)
    if li_dir.exists():
        for p in li_dir.glob("*_ready*.txt"):
            try:
                if datetime.fromtimestamp(p.stat().st_mtime) > cutoff_recent:
                    recent_li_stems.add(p.stem[:25])
            except Exception:
                continue
    candidates = [c for c in candidates if c.stem[:25] not in recent_li_stems]
    if not candidates:
        return {"status": "skipped",
                "message": "all old articles already have recent LinkedIn variants"}
    target = max(candidates, key=lambda p: p.stat().st_size)

    try:
        from agent3_content_creator import _create_linkedin, _build_system
        article_text = target.read_text(encoding="utf-8", errors="replace")[:6000]
        system = _build_system(["linkedin"])
        base = f"repurpose_{datetime.now().strftime('%Y%m%d')}_{target.stem[:30]}"
        paths = _create_linkedin(article_text, base, system, ab_test=False)
        if paths:
            return {"status":  "ok",
                    "message": f"repurposed {target.name} → {Path(paths[0]).name}",
                    "summary": f"new LinkedIn from 90d-old article"}
        return {"status": "warn", "message": "creator returned no paths"}
    except Exception as e:
        return {"status": "error", "message": f"content creator failed: {e}"}


# ─────────────────────────────────────────────
# 23:00 · Tier 3 · Visual backlog
# ─────────────────────────────────────────────

@routine(hour=23, tier=3, cost=0.80,
         description="generate one cover for an uncovered article")
def visual_backlog() -> dict:
    """Find an article without a matching SVG cover, generate one."""
    arts_dir = OUTPUT_DIR / "articles"
    designs_dir = OUTPUT_DIR / "designs"
    if not arts_dir.exists():
        return {"status": "skipped", "message": "no articles directory"}

    existing_design_stems = set()
    if designs_dir.exists():
        for d in designs_dir.glob("*.svg"):
            existing_design_stems.add(d.stem[:25])

    arts = sorted((p for p in arts_dir.glob("*.md") if not p.name.endswith(".bak")),
                  key=lambda p: -p.stat().st_mtime)
    target = None
    for p in arts:
        if p.stem[:25] in existing_design_stems:
            continue
        target = p
        break
    if not target:
        return {"status": "skipped", "message": "all recent articles already covered"}

    try:
        from agent4_designer import run_designer
        topic_hint = target.stem.split("_x_")[0][:60] if "_x_" in target.stem else target.stem[:60]
        saved = run_designer(
            article_paths={"md": target},
            post_paths={},
            design_types=["linkedin_cover"],
            topic=topic_hint,
        )
        if saved:
            return {"status":  "ok",
                    "message": f"cover generated for {target.name}",
                    "summary": f"backlog visual: {list(saved.keys())}"}
        return {"status": "warn", "message": "designer returned nothing"}
    except Exception as e:
        return {"status": "error", "message": f"designer failed: {e}"}
