"""
agent10_curator.py — Weekly review of the publish queue.

Looks at everything ready-but-unpublished, ranks by predicted performance,
and writes a curator report. Sends a weekly digest to Telegram (Sunday 09:00
via cron) so you start the week knowing WHAT to publish + in what order.

Ranking signal mix (no Claude calls — deterministic):
  - Hook score (from hook_tester)
  - Recency (newer posts decay slowly)
  - Platform balance (don't post 5 LinkedIn in a week with 0 blogs)
  - Topic diversity (avoid two posts on the same topic back-to-back)
  - Real engagement from past posts on same topic (if any)

Usage:
  python3 agent10_curator.py              # interactive report
  python3 agent10_curator.py --weekly     # send to Telegram + write memory
  python3 agent10_curator.py --top 5      # change ranking size (default 7)
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

from config import OUTPUT_DIR

LINKEDIN_DIR = OUTPUT_DIR / "posts" / "linkedin"
BLOG_DIR     = OUTPUT_DIR / "posts" / "blog"
PODCAST_DIR  = OUTPUT_DIR / "posts" / "podcast"

PUBLISH_QUEUE = OUTPUT_DIR / "_state" / "publish_queue.json"
CURATOR_REPORT = OUTPUT_DIR / "_memory" / "curator_report.md"

MAX_AGE_DAYS = 30


def _load_queue() -> dict:
    if not PUBLISH_QUEUE.exists():
        return {}
    try:
        return json.loads(PUBLISH_QUEUE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _topic_slug(filename: str) -> str:
    """Extract a topic slug from filename (first 4 underscored segments)."""
    base = filename.split("_he_")[0].split("_en_")[0]
    parts = base.split("_")[:4]
    return "_".join(parts).lower()


def _hook_text(file_path: Path) -> str:
    """First non-trivial line — what would be the hook on LinkedIn."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    for line in text.split("\n"):
        line = line.strip()
        if line and not line.startswith(("---", "#", "title:", "tags:", "moki",
                                          "kind:", "date:", "description:")):
            return line[:200]
    return ""


def _hook_score(text: str) -> int:
    """Reuse the hook_tester scorer if available."""
    if not text:
        return 0
    try:
        from hook_tester import score_hook
        return score_hook(text).get("score", 50)
    except Exception:
        return 50


def _engagement_lookup() -> dict[str, float]:
    """Map topic_slug → average engagement score from past posts."""
    q = _load_queue()
    by_topic: dict[str, list[float]] = defaultdict(list)
    for entry in q.values():
        eng = entry.get("engagement")
        if not eng:
            continue
        score = eng.get("likes", 0) + 2 * eng.get("comments", 0) + 3 * eng.get("shares", 0)
        slug = _topic_slug(Path(entry.get("file", "")).name)
        if slug:
            by_topic[slug].append(score)
    return {slug: sum(scores) / len(scores) for slug, scores in by_topic.items()}


def _candidates() -> list[dict]:
    """All ready posts not yet published."""
    q = _load_queue()
    published_files = {e.get("file") for e in q.values() if e.get("published_at")}
    cutoff = (datetime.now() - timedelta(days=MAX_AGE_DAYS)).timestamp()

    out = []
    sources = [
        (LINKEDIN_DIR, "*_ready*.txt", "linkedin"),
        (BLOG_DIR,     "*.md",          "blog"),
        (PODCAST_DIR,  "*_script_*.md", "podcast"),
    ]
    for directory, pattern, platform in sources:
        if not directory.exists():
            continue
        for p in directory.glob(pattern):
            if p.name.endswith(".bak"):
                continue
            if str(p) in published_files:
                continue
            if p.stat().st_mtime < cutoff:
                continue
            hook = _hook_text(p)
            out.append({
                "file":         str(p),
                "name":         p.name,
                "platform":     platform,
                "mtime":        p.stat().st_mtime,
                "hook":         hook,
                "hook_score":   _hook_score(hook),
                "topic_slug":   _topic_slug(p.name),
            })
    return out


def _rank(candidates: list[dict], top_n: int) -> list[dict]:
    """Composite ranking: hook score + recency + engagement history + diversity."""
    eng_map = _engagement_lookup()
    now = datetime.now().timestamp()

    for c in candidates:
        age_days = (now - c["mtime"]) / 86400
        recency = max(0, 30 - age_days)  # 0 at 30 days, 30 at 0 days
        eng_bonus = eng_map.get(c["topic_slug"], 0) * 0.5  # cap influence
        c["score"] = c["hook_score"] + recency + eng_bonus

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # Enforce diversity: cap each topic_slug at 2, alternate platforms
    selected = []
    topic_count: dict[str, int] = defaultdict(int)
    platform_seq: list[str] = []
    for c in candidates:
        if topic_count[c["topic_slug"]] >= 2:
            continue
        # Avoid 3 same-platform in a row
        if len(platform_seq) >= 2 and platform_seq[-1] == platform_seq[-2] == c["platform"]:
            continue
        selected.append(c)
        topic_count[c["topic_slug"]] += 1
        platform_seq.append(c["platform"])
        if len(selected) >= top_n:
            break
    return selected


PLATFORM_ICON = {"linkedin": "💼", "blog": "📰", "podcast": "🎙️"}


def _report(picks: list[dict]) -> str:
    """Markdown report of the week's curated lineup."""
    if not picks:
        return ("---\nmoki: true\ntype: curator_report\n"
                f"updated: {datetime.now().isoformat(timespec='seconds')}\n---\n\n"
                "# 🎯 Curator — שבועי\n\nאין פוסטים מוכנים לפרסום השבוע.")
    lines = [
        "---",
        "moki: true",
        "type: curator_report",
        f"updated: {datetime.now().isoformat(timespec='seconds')}",
        "---",
        "",
        f"# 🎯 Curator — לוח שבועי ({len(picks)} פוסטים)",
        "",
        f"> דירוג: hook score + recency + engagement היסטורי + גיוון פלטפורמה/נושא.",
        f"> מוקי ממליץ לפרסם בסדר הזה.",
        "",
        "| # | פלטפורמה | score | hook (60 תווים) |",
        "|---|---|---|---|",
    ]
    for i, c in enumerate(picks, 1):
        icon = PLATFORM_ICON.get(c["platform"], "📄")
        hook_short = c["hook"][:60].replace("|", "\\|")
        lines.append(f"| {i} | {icon} {c['platform']} | {c['score']:.0f} | {hook_short} |")
    lines.extend([
        "",
        "## פירוט מלא",
        "",
    ])
    for i, c in enumerate(picks, 1):
        age_h = int((datetime.now().timestamp() - c["mtime"]) / 3600)
        lines.append(f"### {i}. {PLATFORM_ICON.get(c['platform'])} {c['platform']} — score {c['score']:.0f}")
        lines.append(f"- **hook score:** {c['hook_score']}/100")
        lines.append(f"- **age:** {age_h} שעות")
        lines.append(f"- **file:** `{c['name']}`")
        lines.append(f"- **hook preview:** {c['hook'][:200]}")
        lines.append("")
    return "\n".join(lines)


def write_report(picks: list[dict]) -> Path:
    CURATOR_REPORT.parent.mkdir(parents=True, exist_ok=True)
    body = _report(picks)
    CURATOR_REPORT.write_text(body, encoding="utf-8")
    return CURATOR_REPORT


def _telegram_summary(picks: list[dict]) -> str:
    if not picks:
        return "🎯 *Curator שבועי*\n\nאין פוסטים מוכנים השבוע."
    lines = [f"🎯 *Curator שבועי — {len(picks)} פוסטים*",
             f"_{datetime.now().strftime('%d/%m/%Y')}_", ""]
    for i, c in enumerate(picks, 1):
        icon = PLATFORM_ICON.get(c["platform"], "📄")
        lines.append(f"{i}. {icon} *{c['platform']}* · score {c['score']:.0f}")
        lines.append(f"   _{c['hook'][:80]}_")
    lines.append("")
    lines.append("דוח מלא: `output/_memory/curator_report.md`")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Weekly curator — rank what to publish")
    ap.add_argument("--top", type=int, default=7, help="How many posts to surface")
    ap.add_argument("--weekly", action="store_true",
                    help="Send Telegram digest + write memory file")
    args = ap.parse_args()

    cands = _candidates()
    picks = _rank(cands, args.top)
    path = write_report(picks)
    print(f"📝 דוח Curator: {path.relative_to(OUTPUT_DIR.parent)} ({len(picks)} פוסטים)")
    for i, c in enumerate(picks, 1):
        print(f"   {i}. {PLATFORM_ICON.get(c['platform'])} {c['platform']:8} score={c['score']:.0f}  {c['hook'][:60]}")

    if args.weekly:
        try:
            from notifications import _send, is_configured
            if is_configured():
                _send(_telegram_summary(picks), parse_mode="Markdown")
                print("📨 נשלח לטלגרם")
            else:
                print("⚠️  טלגרם לא מוגדר — דילגתי על שליחה")
        except Exception as e:
            print(f"⚠️  שליחה נכשלה: {e}")


if __name__ == "__main__":
    main()
