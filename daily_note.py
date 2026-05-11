"""
daily_note.py — Auto-generated Obsidian-friendly daily home note.
מציג מה מוקי הפיק היום + sentinels של בריאות המערכת.

Usage:
  python3 daily_note.py              # write today's note
  python3 daily_note.py --date 2026-05-06
"""

import sys
import json
from pathlib import Path
from datetime import datetime, timedelta

from config import OUTPUT_DIR

DAILY_DIR = OUTPUT_DIR / "_daily"
DAILY_DIR.mkdir(parents=True, exist_ok=True)


def _files_modified_on(date: datetime, subdir: str, pattern: str) -> list[Path]:
    """List files modified on a specific date."""
    d = OUTPUT_DIR / subdir
    if not d.exists():
        return []
    start = datetime(date.year, date.month, date.day)
    end = start + timedelta(days=1)
    return [
        f for f in d.glob(pattern)
        if not f.name.endswith(".bak")
        and start <= datetime.fromtimestamp(f.stat().st_mtime) < end
    ]


def _todays_run_summary(date: datetime) -> dict:
    """Pull today's pipeline run from analytics."""
    f = OUTPUT_DIR / "analytics.json"
    if not f.exists():
        return {}
    try:
        runs = json.loads(f.read_text(encoding="utf-8")).get("runs", [])
    except Exception:
        return {}
    today_str = date.strftime("%Y-%m-%d")
    today_runs = [r for r in runs if (r.get("started_at", "")).startswith(today_str)]
    if not today_runs:
        return {}
    last = today_runs[-1]
    return {
        "started_at": last.get("started_at", "")[:16].replace("T", " "),
        "topic": last.get("topic", "?")[:60],
        "duration_min": round(last.get("duration_s", 0) / 60, 1),
        "success": last.get("success", False),
        "qa": last.get("avg_qa"),
        "cost": last.get("est_cost"),
        "errors": len(last.get("errors", [])),
    }


def _alerts() -> list[dict]:
    f = OUTPUT_DIR / "active_alerts.json"
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("alerts", [])
    except Exception:
        return []


def _voice_drift_status() -> dict:
    """Quick voice-drift snapshot."""
    try:
        from voice_drift import analyze_voice_drift
        return analyze_voice_drift(top_n=15)
    except Exception:
        return {}


def _latest_proposal() -> Path | None:
    proposals = list((OUTPUT_DIR / "proposals").glob("proposal_*.md"))
    if not proposals:
        return None
    return max(proposals, key=lambda p: p.stat().st_mtime)


def generate_daily(date: datetime = None) -> Path:
    if date is None:
        date = datetime.now()

    date_str = date.strftime("%Y-%m-%d")
    weekday_he = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"][date.weekday()]
    out_path = DAILY_DIR / f"{date_str}.md"

    # Gather data
    articles = _files_modified_on(date, "articles", "*.md")
    linkedin = _files_modified_on(date, "posts/linkedin", "*ready*.txt")
    blog     = _files_modified_on(date, "posts/blog", "*.md")
    podcast  = _files_modified_on(date, "posts/podcast", "*script*.md")
    papers   = _files_modified_on(date, "papers", "*.json")
    designs  = _files_modified_on(date, "designs", "*.svg")

    run = _todays_run_summary(date)
    alerts = _alerts()
    drift = _voice_drift_status()
    proposal = _latest_proposal()

    # Build markdown
    lines = [
        "---",
        f"title: \"📅 {date_str} · יום {weekday_he}\"",
        "tags: [moki/daily]",
        f"date: {date_str}",
        "---",
        "",
        f"# 🦊 מוקי · {date_str} · יום {weekday_he}",
        "",
        "## 📊 הפעילות של היום",
        "",
    ]

    if run:
        icon = "✅" if run.get("success") else "❌"
        lines.append(f"**ריצה אחרונה:** {icon} {run.get('started_at', '')}")
        lines.append(f"- נושא: _{run.get('topic', '?')}_")
        lines.append(f"- זמן: {run.get('duration_min', 0)} דק' · QA: {run.get('qa', '?')}")
        if run.get("cost"):
            lines.append(f"- עלות: ${run['cost']}")
        if run.get("errors"):
            lines.append(f"- שגיאות: {run['errors']}")
    else:
        lines.append("_לא רצה ריצה היום._")
    lines.append("")

    # ── Outputs ──
    lines.append("## 📦 תוצרים")
    lines.append("")

    sections = [
        (articles, "📝 מאמרים"),
        (linkedin, "💼 LinkedIn"),
        (blog, "📰 בלוג"),
        (podcast, "🎙️ פודקאסט"),
        (papers, "🔍 מאמרי מחקר"),
        (designs, "🎨 עיצובים"),
    ]
    for files, label in sections:
        if files:
            lines.append(f"### {label} ({len(files)})")
            for f in files[:5]:
                # Wikilink to Obsidian-style
                rel = str(f.relative_to(OUTPUT_DIR))
                lines.append(f"- [[{rel}|{f.stem[:60]}]]")
            lines.append("")

    if not any(s for s, _ in sections):
        lines.append("_אין תוצרים חדשים היום._")
        lines.append("")

    # ── Latest proposal ──
    if proposal:
        rel = str(proposal.relative_to(OUTPUT_DIR))
        prop_age = (datetime.now() - datetime.fromtimestamp(proposal.stat().st_mtime)).days
        if prop_age <= 3:
            lines.append("## 📋 הצעת מחקר אחרונה")
            lines.append("")
            lines.append(f"- [[{rel}|{proposal.stem}]]  _(לפני {prop_age} ימים)_")
            lines.append("")

    # ── Alerts ──
    if alerts:
        lines.append(f"## 🚨 התראות פעילות ({len(alerts)})")
        lines.append("")
        for a in alerts[:5]:
            level_icon = {"page": "🔥", "critical": "🚨", "warning": "⚠️"}.get(a.get("level"), "?")
            lines.append(f"- {level_icon} **{a.get('slo', '?')}**: {a.get('message', '')}")
        lines.append("")

    # ── Voice drift ──
    if drift and drift.get("samples", 0) >= 5:
        verdict = drift.get("verdict", "?")
        score = drift.get("diversity_score", 0)
        icon = {"diverse": "✅", "drifting": "⚠️", "stuck": "❌"}.get(verdict, "?")
        lines.append("## 🌊 Voice Drift")
        lines.append("")
        lines.append(f"{icon} **{verdict}** · diversity {score}/100 · בסיס: {drift['samples']} פוסטים")
        lines.append("")
        for r in drift.get("recommendations", [])[:2]:
            lines.append(f"- {r}")
        lines.append("")

    # ── Quick links ──
    lines.append("## 🔗 קישורים שימושיים")
    lines.append("")
    lines.append("- [[_מקורות|📚 אינדקס מקורות]]")
    lines.append("- [[_נושאים|🗺 אינדקס נושאים]]")
    lines.append("- `moki-dash` (דשבורד)")
    lines.append("- `moki-reflect` (תובנות)")
    lines.append("")

    # ── Footer ──
    lines.append("---")
    lines.append(f"_Generated by daily_note.py at {datetime.now().strftime('%H:%M')}_")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def main():
    date = datetime.now()
    if "--date" in sys.argv:
        idx = sys.argv.index("--date")
        if idx + 1 < len(sys.argv):
            try:
                date = datetime.strptime(sys.argv[idx + 1], "%Y-%m-%d")
            except ValueError:
                pass
    path = generate_daily(date)
    print(f"\n✅ Daily note: {path}")
    print(f"   {len(path.read_text(encoding='utf-8').splitlines())} lines")


if __name__ == "__main__":
    main()
