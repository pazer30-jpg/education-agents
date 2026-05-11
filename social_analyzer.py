"""
social_analyzer.py — A/B winner detection + engagement analysis.
מבוסס על social-media-analyzer skill.

Engagement Rate = (Likes + Comments + Shares) / Reach × 100

תמיכה מלאה ב-A/B testing: מזהה זוגות (A + B) לפי שם הקובץ
ומחליט מי ניצח לפי engagement rate.

Usage:
  python3 social_analyzer.py                 # full report
  python3 social_analyzer.py --ab            # A/B winners only
  python3 social_analyzer.py --winners       # top performers
"""

import json
import sys
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from config import OUTPUT_DIR

PERF_LOG = OUTPUT_DIR / "performance_log.json"


# ─────────────────────────────────────────────
# Engagement metrics — per skill's formula
# ─────────────────────────────────────────────

def engagement_rate(likes: int, comments: int, shares: int, reach: int) -> float:
    """Standard formula from social-media-analyzer skill."""
    if reach <= 0:
        return 0.0
    return (likes + comments + shares) / reach * 100


def categorize(rate: float) -> tuple[str, str]:
    """Return (category, action) per skill's table."""
    if rate > 6:
        return "excellent", "scale and replicate"
    elif rate >= 3:
        return "good", "optimize and expand"
    elif rate >= 1:
        return "average", "test improvements"
    return "poor", "analyze and pivot"


# ─────────────────────────────────────────────
# A/B variant pairing
# ─────────────────────────────────────────────

def _strip_b_marker(filename: str) -> str:
    """Convert 'topic_linkedin_B_20260504_1430.txt' → 'topic_linkedin_20260504_1430.txt' for pairing."""
    return re.sub(r"_linkedin_B_", "_linkedin_", filename)


def find_ab_pairs(entries: list[dict]) -> list[dict]:
    """
    Group entries into A/B pairs by stripped filename.
    Returns: [{
      "base": str,
      "variant_a": entry, "variant_b": entry,
      "winner": "A" | "B" | "tie",
      "rate_a": float, "rate_b": float,
      "diff_pct": float,  # how much B beat A (negative = A won)
    }, ...]
    """
    by_normalized = defaultdict(list)
    for e in entries:
        title = e.get("title") or e.get("file") or ""
        normalized = _strip_b_marker(title)
        by_normalized[normalized].append(e)

    pairs = []
    for normalized, group in by_normalized.items():
        if len(group) != 2:
            continue
        a = next((e for e in group if "_B_" not in (e.get("title") or e.get("file") or "")), None)
        b = next((e for e in group if "_B_" in (e.get("title") or e.get("file") or "")), None)
        if not a or not b:
            continue

        ma = a.get("metrics", {})
        mb = b.get("metrics", {})
        rate_a = engagement_rate(
            ma.get("likes", 0), ma.get("comments", 0),
            ma.get("shares", 0), ma.get("reach", 1) or 1,
        )
        rate_b = engagement_rate(
            mb.get("likes", 0), mb.get("comments", 0),
            mb.get("shares", 0), mb.get("reach", 1) or 1,
        )

        if rate_a > rate_b * 1.1:
            winner = "A"
        elif rate_b > rate_a * 1.1:
            winner = "B"
        else:
            winner = "tie"

        diff = ((rate_b - rate_a) / rate_a * 100) if rate_a > 0 else 0

        pairs.append({
            "base": normalized,
            "variant_a": a,
            "variant_b": b,
            "winner": winner,
            "rate_a": round(rate_a, 2),
            "rate_b": round(rate_b, 2),
            "diff_pct": round(diff, 1),
        })

    return pairs


# ─────────────────────────────────────────────
# Insights from winning patterns
# ─────────────────────────────────────────────

def winning_patterns(pairs: list[dict]) -> dict:
    """
    Aggregate which side wins more often.
    If B consistently wins → "different hook style works".
    """
    if not pairs:
        return {"samples": 0}

    wins_a = sum(1 for p in pairs if p["winner"] == "A")
    wins_b = sum(1 for p in pairs if p["winner"] == "B")
    ties = sum(1 for p in pairs if p["winner"] == "tie")

    avg_diff = sum(p["diff_pct"] for p in pairs) / len(pairs)

    insight = "no clear pattern"
    if wins_b >= wins_a * 2:
        insight = "Variant B (different hook style) wins consistently — switch default"
    elif wins_a >= wins_b * 2:
        insight = "Variant A (current default) wins — current hook style works"
    elif ties >= len(pairs) * 0.6:
        insight = "Hooks make little difference — focus on body content"

    return {
        "samples": len(pairs),
        "wins_a": wins_a,
        "wins_b": wins_b,
        "ties": ties,
        "avg_diff_pct": round(avg_diff, 1),
        "insight": insight,
    }


# ─────────────────────────────────────────────
# Top/bottom performers
# ─────────────────────────────────────────────

def top_bottom(entries: list[dict], n: int = 5) -> dict:
    """Top and bottom performers by engagement rate."""
    scored = []
    for e in entries:
        m = e.get("metrics", {})
        reach = m.get("reach", 0)
        if reach <= 0:
            continue  # skip without reach data
        rate = engagement_rate(
            m.get("likes", 0), m.get("comments", 0),
            m.get("shares", 0), reach,
        )
        scored.append({
            "title": (e.get("title") or "")[:60],
            "rate": round(rate, 2),
            "category": categorize(rate)[0],
            "platform": e.get("platform", "?"),
        })
    scored.sort(key=lambda x: x["rate"], reverse=True)
    return {
        "top": scored[:n],
        "bottom": scored[-n:][::-1] if len(scored) > n else [],
        "samples": len(scored),
    }


# ─────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────

def full_report() -> str:
    if not PERF_LOG.exists():
        return "  ℹ️  אין performance_log.json — הוסף ביצועים דרך 'הוסף ביצוע'"

    entries = json.loads(PERF_LOG.read_text(encoding="utf-8"))
    if not entries:
        return "  ℹ️  performance_log ריק"

    pairs = find_ab_pairs(entries)
    patterns = winning_patterns(pairs)
    perf = top_bottom(entries)

    lines = [f"\n📊 Social Media Analyzer — {datetime.now().strftime('%d/%m/%Y %H:%M')}"]

    lines.append(f"\n🧪 A/B Testing ({patterns['samples']} זוגות):")
    if patterns["samples"] > 0:
        lines.append(f"   A wins:  {patterns['wins_a']}")
        lines.append(f"   B wins:  {patterns['wins_b']}")
        lines.append(f"   Ties:    {patterns['ties']}")
        lines.append(f"   Avg diff: {patterns['avg_diff_pct']}%")
        lines.append(f"   💡 {patterns['insight']}")

        for p in pairs[:3]:
            icon = "🅰" if p["winner"] == "A" else ("🅱" if p["winner"] == "B" else "🟰")
            lines.append(f"   {icon} {p['base'][:50]}: A={p['rate_a']}%, B={p['rate_b']}%")
    else:
        lines.append("   אין זוגות A/B עם נתוני engagement עדיין")

    if perf["samples"] > 0:
        lines.append(f"\n🏆 Top Performers (engagement rate):")
        for t in perf["top"][:3]:
            icon = {"excellent": "🌟", "good": "✅", "average": "⚠️", "poor": "❌"}.get(t["category"], "?")
            lines.append(f"   {icon} [{t['platform']}] {t['title']} — {t['rate']}% ({t['category']})")

        if perf["bottom"]:
            lines.append(f"\n🪃 Bottom Performers (לזרוק או לתקן):")
            for b in perf["bottom"][:3]:
                lines.append(f"   ❌ [{b['platform']}] {b['title']} — {b['rate']}%")

    return "\n".join(lines)


def main():
    if "--ab" in sys.argv:
        if PERF_LOG.exists():
            entries = json.loads(PERF_LOG.read_text(encoding="utf-8"))
            pairs = find_ab_pairs(entries)
            patterns = winning_patterns(pairs)
            print(json.dumps({"pairs": pairs, "patterns": patterns}, ensure_ascii=False, indent=2))
        else:
            print("no performance_log")
    elif "--winners" in sys.argv:
        if PERF_LOG.exists():
            entries = json.loads(PERF_LOG.read_text(encoding="utf-8"))
            tb = top_bottom(entries)
            print(json.dumps(tb, ensure_ascii=False, indent=2))
        else:
            print("no performance_log")
    else:
        print(full_report())


if __name__ == "__main__":
    main()
