"""
weekly_digest.py — Monday morning briefing of last 7 days.

Aggregates data from across the system into a single human-readable
digest. Designed to be the FIRST thing the user reads on Monday morning,
so they don't need to open the dashboard to know where things stand.

Sources mined:
  - output/analytics.json          — runs, successes, duration, cost
  - output/_state/hook_log.json    — winning hooks of the week
  - output/_state/publish_queue.json — ready posts + engagement (if any)
  - output/_memory/failure_report.md — recurring failure patterns
  - output/_state/series.json      — active series + episode progress
  - output/_state/source_health.json — flaky sources to watch

Outputs:
  - output/_memory/weekly_digest.md  (markdown — for Obsidian)
  - Telegram message (if TELEGRAM_BOT_TOKEN configured)

CLI:
  python3 weekly_digest.py            # write file + send to Telegram if configured
  python3 weekly_digest.py --print    # print to stdout only
  python3 weekly_digest.py --no-send  # write file, don't send
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from config import OUTPUT_DIR

ANALYTICS  = OUTPUT_DIR / "analytics.json"
HOOK_LOG   = OUTPUT_DIR / "_state" / "hook_log.json"
PUB_QUEUE  = OUTPUT_DIR / "_state" / "publish_queue.json"
SERIES     = OUTPUT_DIR / "_state" / "series.json"
SRC_HEALTH = OUTPUT_DIR / "_state" / "source_health.json"
FAILURE_RPT = OUTPUT_DIR / "_memory" / "failure_report.md"

OUT_MEMORY = OUTPUT_DIR / "_memory" / "weekly_digest.md"


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _within_week(ts_str: str, cutoff: datetime) -> bool:
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "")) >= cutoff
    except Exception:
        return False


# ─────────────────────────────────────────────
# Section builders
# ─────────────────────────────────────────────

def _runs_summary(cutoff: datetime) -> dict:
    data = _load_json(ANALYTICS, {"runs": []})
    runs = [r for r in data.get("runs", [])
            if r.get("started_at") and _within_week(r["started_at"], cutoff)]
    successes = sum(1 for r in runs if r.get("success"))
    durations = [r.get("duration_s", 0) / 60 for r in runs if r.get("duration_s")]
    costs     = [r.get("est_cost", 0)        for r in runs if r.get("est_cost")]
    return {
        "total":       len(runs),
        "successes":   successes,
        "failures":    len(runs) - successes,
        "avg_min":     round(sum(durations) / len(durations), 1) if durations else 0,
        "total_cost":  round(sum(costs), 2),
        "avg_cost":    round(sum(costs) / len(costs), 2) if costs else 0,
    }


def _top_hooks(cutoff: datetime, n: int = 3) -> list[dict]:
    log = _load_json(HOOK_LOG, [])
    fresh = [e for e in log if _within_week(e.get("logged_at", ""), cutoff)]
    fresh.sort(key=lambda e: e.get("score", 0), reverse=True)
    return fresh[:n]


def _ready_count() -> dict:
    """Count posts ready to publish but not yet published."""
    q = _load_json(PUB_QUEUE, {})
    ready_by_platform = {"linkedin": 0, "blog": 0, "podcast": 0}
    for entry in (q.values() if isinstance(q, dict) else []):
        if entry.get("published_at"):
            continue
        plat = entry.get("platform", "")
        if plat in ready_by_platform:
            ready_by_platform[plat] += 1
    return ready_by_platform


def _engagement_winners(cutoff: datetime, n: int = 3) -> list[dict]:
    """Posts published in window with the highest engagement score."""
    q = _load_json(PUB_QUEUE, {})
    items = []
    for token, entry in (q.items() if isinstance(q, dict) else []):
        if not entry.get("published_at"):
            continue
        if not _within_week(entry["published_at"], cutoff):
            continue
        eng = entry.get("engagement") or {}
        if not eng:
            continue
        score = eng.get("likes", 0) + 2 * eng.get("comments", 0) + 3 * eng.get("shares", 0)
        items.append({
            "token":    token,
            "platform": entry.get("platform", "?"),
            "file":     entry.get("file", ""),
            "score":    score,
            "eng":      eng,
        })
    items.sort(key=lambda x: x["score"], reverse=True)
    return items[:n]


def _active_series() -> list[dict]:
    data = _load_json(SERIES, {"series": []})
    return [s for s in data.get("series", []) if s.get("status") == "active"]


def _source_health_snapshot() -> dict:
    """Summary: how many sources healthy vs degraded vs unknown."""
    data = _load_json(SRC_HEALTH, {"sources": {}})
    healthy = degraded = unknown = 0
    for s, info in data.get("sources", {}).items():
        events = info.get("events", [])
        if len(events) < 3:
            unknown += 1
            continue
        rate = sum(1 for e in events if e.get("success")) / len(events)
        if rate >= 0.7:
            healthy += 1
        else:
            degraded += 1
    return {"healthy": healthy, "degraded": degraded, "unknown": unknown}


def _failure_top_pattern() -> str:
    """Pull the dominant failure category from failure_report.md (first listed)."""
    if not FAILURE_RPT.exists():
        return ""
    try:
        text = FAILURE_RPT.read_text(encoding="utf-8")
    except Exception:
        return ""
    m = re.search(r"## .*?(?:Top|דומיננטית|התראות).*?\n(.*?)(?:\n##|\Z)", text, re.S)
    if not m:
        return ""
    # Grab first 2 lines after the header
    lines = [ln.strip("- ").strip() for ln in m.group(1).split("\n")
             if ln.strip() and "—" in ln]
    return lines[0] if lines else ""


# ─────────────────────────────────────────────
# Compose
# ─────────────────────────────────────────────

def compose_digest() -> str:
    now = datetime.now()
    cutoff = now - timedelta(days=7)
    summary = _runs_summary(cutoff)
    hooks   = _top_hooks(cutoff)
    ready   = _ready_count()
    winners = _engagement_winners(cutoff)
    series  = _active_series()
    src     = _source_health_snapshot()
    fail    = _failure_top_pattern()

    rate = (summary["successes"] / summary["total"] * 100) if summary["total"] else 0
    success_icon = "✅" if rate >= 70 else "🟡" if rate >= 40 else "🔴"

    lines = [
        "---",
        "moki: true",
        "type: weekly_digest",
        f"updated: {now.isoformat(timespec='seconds')}",
        "---",
        "",
        f"# 📬 דיגסט שבועי — {(cutoff.strftime('%d/%m'))}–{now.strftime('%d/%m/%Y')}",
        "",
        "## 📊 השבוע במספרים",
        "",
        f"- **ריצות:** {summary['total']} · {success_icon} {summary['successes']} הצלחות · "
        f"❌ {summary['failures']} כשלים · **{rate:.0f}% הצלחה**",
        f"- **זמן ממוצע:** {summary['avg_min']} דק'",
        f"- **עלות:** ${summary['total_cost']} סה\"כ · ${summary['avg_cost']} ממוצע לריצה",
        "",
        "## 🎯 פוסטים מוכנים לפרסום",
        "",
    ]

    total_ready = sum(ready.values())
    if total_ready:
        lines.append(f"**{total_ready} פוסטים מחכים לפרסום:**  "
                     f"💼 LinkedIn {ready['linkedin']}  ·  "
                     f"📰 Blog {ready['blog']}  ·  "
                     f"🎙️ Podcast {ready['podcast']}")
    else:
        lines.append("_אין פוסטים מוכנים. הקרון של היום (אם רץ) ייצור חדשים._")
    lines.append("")

    lines.append("## 🎣 Hooks החזקים השבוע")
    lines.append("")
    if hooks:
        for h in hooks:
            score = h.get("score", 0)
            text = (h.get("hook") or "")[:140].replace("\n", " ")
            plat = h.get("platform", "?")
            lines.append(f"- **{score}/100** ({plat}) · {text}")
    else:
        lines.append("_אין hooks חדשים שעברו את סף 75/100 השבוע._")
    lines.append("")

    if winners:
        lines.append("## 🏆 ביצועים אמיתיים (engagement)")
        lines.append("")
        for w in winners:
            eng = w["eng"]
            lines.append(f"- **{w['platform']}** · score {w['score']} · "
                         f"👍 {eng.get('likes', 0)} · 💬 {eng.get('comments', 0)} · "
                         f"🔁 {eng.get('shares', 0)} · "
                         f"`{Path(w['file']).name}`")
        lines.append("")

    if series:
        lines.append("## 📺 סדרות פעילות")
        lines.append("")
        for s in series:
            n_eps = len(s.get("episodes", []))
            last = s.get("last_episode_at", "")[:10]
            lines.append(f"- **{s['theme']}** — {n_eps} פרקים · אחרון {last}")
            if s.get("next_angle"):
                lines.append(f"  - 🎯 זווית הבאה: {s['next_angle']}")
        lines.append("")

    lines.append("## 🩺 בריאות מערכת")
    lines.append("")
    lines.append(f"- **מקורות מחקר:** 🟢 {src['healthy']} בריאים · "
                 f"🟡 {src['degraded']} דעוכים · ⚪ {src['unknown']} לא ידוע")
    if fail:
        lines.append(f"- **כשל דומיננטי:** {fail}")
    lines.append("")

    lines.append("## 💡 מומלץ השבוע")
    lines.append("")
    recs = []
    if rate < 50 and summary["total"] >= 3:
        recs.append("Success rate < 50% — לבדוק failure_report.md לאיתור דפוס")
    if not winners and total_ready > 5:
        recs.append(f"{total_ready} פוסטים נערמים בלי engagement data — "
                    f"להזין נתונים ב-engagement_tracker.py")
    if not hooks and summary["total"] > 0:
        recs.append("0 hooks ניצחו השבוע — בדוק את voice_rules.md / hook_winners.md")
    if src["degraded"] > 0:
        recs.append(f"{src['degraded']} מקורות מחקר בדעיכה — יקפצו אוטומטית בעוד 24h")
    if not recs:
        recs.append("הכל בכיוון. המשך כרגיל.")
    for r in recs:
        lines.append(f"- {r}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"_נוצר אוטומטית · {now.strftime('%d/%m/%Y %H:%M')}_")

    return "\n".join(lines)


def telegram_summary(digest_md: str) -> str:
    """Short Telegram-friendly version — markdown, fits in one message."""
    # Pull the headline numbers + first 3 hooks for a compact message
    now = datetime.now()
    cutoff = now - timedelta(days=7)
    s = _runs_summary(cutoff)
    h = _top_hooks(cutoff, n=2)
    ready = _ready_count()
    rate = (s["successes"] / s["total"] * 100) if s["total"] else 0
    icon = "✅" if rate >= 70 else "🟡" if rate >= 40 else "🔴"
    total_ready = sum(ready.values())

    parts = [
        f"📬 *דיגסט שבועי*  ·  {cutoff.strftime('%d/%m')}–{now.strftime('%d/%m')}",
        "",
        f"{icon} {s['total']} ריצות · {s['successes']} הצלחות · *{rate:.0f}%*",
        f"💰 ${s['total_cost']} סה\"כ · ⏱ ממוצע {s['avg_min']} דק'",
        f"🎯 {total_ready} מוכנים: 💼{ready['linkedin']} 📰{ready['blog']} 🎙{ready['podcast']}",
    ]
    if h:
        parts.append("")
        parts.append("*🎣 Top hooks:*")
        for hook in h:
            text = (hook.get("hook") or "")[:80].replace("*", "")
            parts.append(f"  • _{hook['score']}/100_ · {text}")
    parts.append("")
    parts.append("דוח מלא: `output/_memory/weekly_digest.md`")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser(description="Weekly Monday digest")
    ap.add_argument("--print",   action="store_true", help="stdout only")
    ap.add_argument("--no-send", action="store_true", help="write file but don't send to Telegram")
    args = ap.parse_args()

    body = compose_digest()

    if args.print:
        print(body)
        return

    OUT_MEMORY.parent.mkdir(parents=True, exist_ok=True)
    OUT_MEMORY.write_text(body, encoding="utf-8")
    print(f"📬 {OUT_MEMORY.relative_to(OUTPUT_DIR.parent)}")
    print(f"   Open in Obsidian to read the digest.")


if __name__ == "__main__":
    main()
