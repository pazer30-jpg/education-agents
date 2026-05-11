"""
failure_analyzer.py — Why pipeline runs fail. Pattern extraction from analytics.

Reads output/analytics.json runs, groups errors by:
  - agent (which agent failed most)
  - error pattern (regex match: timeout, missing_data, claude_cli, etc.)
  - duration anomalies (>2x median = runaway)

Outputs:
  - output/_memory/failure_report.md (human-readable)
  - scratchpad note "common_failures" (next pipeline reads it)

Usage:
  python3 failure_analyzer.py             # report + scratchpad note
  python3 failure_analyzer.py --window 30 # window in days
"""

import re
import sys
import json
import statistics
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter, defaultdict

from config import OUTPUT_DIR


# ─────────────────────────────────────────────
# Error pattern catalog
# ─────────────────────────────────────────────

ERROR_PATTERNS = [
    ("timeout", re.compile(r"timeout|exceeded.*min|hard timeout", re.I)),
    ("claude_cli", re.compile(r"CLIUnavailable|claude.*cli.*fail|all 3 retries", re.I)),
    ("budget", re.compile(r"DailyBudgetExceeded|budget.*cap|over budget", re.I)),
    ("missing_data", re.compile(r"אין מאמרים|אין מאמר|no papers|missing.*source", re.I)),
    ("json_parse", re.compile(r"JSONDecode|json.*parse|invalid json", re.I)),
    ("file_not_found", re.compile(r"FileNotFound|No such file|does not exist", re.I)),
    ("type_error", re.compile(r"TypeError|AttributeError|has no attribute", re.I)),
    ("rate_limit", re.compile(r"rate.?limit|429|too many requests", re.I)),
    ("loop_detected", re.compile(r"loop detected|too many.*attempts", re.I)),
    ("qa_fail", re.compile(r"QA failed|qa score.*<.*60", re.I)),
]


def _classify_error(err: str) -> str:
    """Return canonical error category."""
    for label, pattern in ERROR_PATTERNS:
        if pattern.search(err):
            return label
    return "other"


def _load_runs() -> list[dict]:
    f = OUTPUT_DIR / "analytics.json"
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("runs", [])
    except Exception:
        return []


# ─────────────────────────────────────────────
# Analysis
# ─────────────────────────────────────────────

def analyze(window_days: int = 30) -> dict:
    runs = _load_runs()
    cutoff = datetime.now() - timedelta(days=window_days)
    cutoff_ts = cutoff.timestamp()

    recent = []
    for r in runs:
        try:
            ts = datetime.fromisoformat(r.get("started_at", "").replace("Z", ""))
            if ts.timestamp() >= cutoff_ts:
                recent.append(r)
        except Exception:
            pass

    if not recent:
        return {"runs_analyzed": 0, "alerts": []}

    failed_runs = [r for r in recent if not r.get("success")]
    success_runs = [r for r in recent if r.get("success")]

    # Per-agent failure attribution
    agent_failures: Counter = Counter()
    error_categories: Counter = Counter()
    error_samples: dict[str, list[str]] = defaultdict(list)

    for r in failed_runs:
        steps = r.get("steps") or []
        for step in steps:
            if step.get("status") not in (None, "ok"):
                agent_failures[step.get("agent", "unknown")] += 1
        for err in r.get("errors") or []:
            err_text = err.get("error", "") if isinstance(err, dict) else str(err)
            cat = _classify_error(err_text)
            error_categories[cat] += 1
            if len(error_samples[cat]) < 3:
                error_samples[cat].append(err_text[:200])

    # Duration anomalies
    durations = [r.get("duration_s", 0) for r in recent if r.get("duration_s")]
    runaways = []
    if durations:
        median = statistics.median(durations)
        threshold = median * 3
        for r in recent:
            d = r.get("duration_s", 0)
            if d > threshold and d > 1800:  # at least 30 min AND 3x median
                runaways.append({
                    "started_at": r.get("started_at", "")[:16],
                    "topic": (r.get("topic") or "")[:60],
                    "duration_min": round(d / 60, 1),
                    "median_min": round(median / 60, 1),
                    "success": r.get("success", False),
                })

    # Build alerts list — for scratchpad injection
    alerts = []
    success_rate = len(success_runs) / len(recent) if recent else 0
    if success_rate < 0.7 and len(recent) >= 5:
        top_error = error_categories.most_common(1)
        top_agent = agent_failures.most_common(1)
        msg = f"success rate נמוך ({success_rate:.0%}) ב-{len(recent)} ריצות"
        if top_error:
            msg += f" — סיבה דומיננטית: {top_error[0][0]}"
        if top_agent:
            msg += f" — סוכן בעייתי: {top_agent[0][0]}"
        alerts.append({"severity": "high", "message": msg})

    if runaways:
        alerts.append({
            "severity": "medium",
            "message": f"{len(runaways)} ריצות runaway (>3x median, >30 דק')",
        })

    return {
        "runs_analyzed": len(recent),
        "window_days": window_days,
        "success_rate": round(success_rate, 2),
        "failed_count": len(failed_runs),
        "agent_failures": dict(agent_failures),
        "error_categories": dict(error_categories),
        "error_samples": {k: v for k, v in error_samples.items()},
        "runaways": runaways,
        "alerts": alerts,
    }


# ─────────────────────────────────────────────
# Reports
# ─────────────────────────────────────────────

_ERROR_LABELS = {
    "timeout":        "⏱  Timeout (step >30 דק')",
    "claude_cli":     "🤖 Claude CLI נכשל",
    "budget":         "💰 חרגנו מ-budget",
    "missing_data":   "📂 חוסר נתונים (אין מאמרים/מקור)",
    "json_parse":     "🔧 JSON parse error",
    "file_not_found": "📁 File not found",
    "type_error":     "🐛 TypeError / AttributeError",
    "rate_limit":     "🚦 Rate limit (429)",
    "loop_detected":  "🔄 לולאה — אותו סוכן רץ פעמים רבות",
    "qa_fail":        "❌ QA נכשל",
    "other":          "❓ אחר",
}


def md_report(a: dict) -> Path:
    parts = [
        "---",
        "moki: true",
        "type: failure_report",
        f"updated: {datetime.now().isoformat(timespec='seconds')}",
        "---",
        "",
        "# 🚨 Failure Report",
        "",
        f"_עודכן: {datetime.now().strftime('%d/%m/%Y %H:%M')}_",
        f"_נסקרו {a['runs_analyzed']} ריצות ב-{a['window_days']} ימים אחרונים._",
        "",
    ]

    if a["runs_analyzed"] == 0:
        parts.append("_אין נתונים._")
    else:
        sr = a["success_rate"]
        icon = "🟢" if sr >= 0.8 else "🟡" if sr >= 0.5 else "🔴"
        parts.extend([
            f"## {icon} Success rate: **{sr:.0%}**",
            f"",
            f"- {a['runs_analyzed'] - a['failed_count']} ריצות הצליחו",
            f"- {a['failed_count']} ריצות נכשלו",
            "",
        ])

        # Alerts
        if a["alerts"]:
            parts.append("## ⚠️  התראות פעילות")
            parts.append("")
            for al in a["alerts"]:
                icon = "🔴" if al["severity"] == "high" else "🟡"
                parts.append(f"- {icon} {al['message']}")
            parts.append("")

        # Error categories
        if a["error_categories"]:
            parts.extend(["## 📊 התפלגות שגיאות", "", "| קטגוריה | כמות |", "|---|---|"])
            for cat, n in sorted(a["error_categories"].items(), key=lambda x: -x[1]):
                label = _ERROR_LABELS.get(cat, cat)
                parts.append(f"| {label} | {n} |")
            parts.append("")

        # Per-agent
        if a["agent_failures"]:
            parts.extend(["## 🎯 כשלים לפי סוכן", "", "| סוכן | כשלים |", "|---|---|"])
            for agent, n in sorted(a["agent_failures"].items(), key=lambda x: -x[1]):
                parts.append(f"| `{agent}` | {n} |")
            parts.append("")

        # Runaways
        if a["runaways"]:
            parts.extend([
                f"## 🏃 ריצות Runaway (>3x median, >30 דק')", "",
                "| תאריך | נושא | זמן (דק') | הצלחה? |", "|---|---|---|---|",
            ])
            for r in a["runaways"][:10]:
                ok = "✅" if r["success"] else "❌"
                parts.append(f"| {r['started_at']} | {r['topic']} | "
                             f"**{r['duration_min']}** (median: {r['median_min']}) | {ok} |")
            parts.append("")

        # Error samples
        if a["error_samples"]:
            parts.extend(["## 🔍 דוגמאות שגיאות (עד 3 לכל קטגוריה)", ""])
            for cat, samples in a["error_samples"].items():
                if not samples:
                    continue
                parts.append(f"### {_ERROR_LABELS.get(cat, cat)}")
                parts.append("")
                for s in samples:
                    parts.append(f"- `{s[:200]}`")
                parts.append("")

        # Action items
        parts.extend([
            "---",
            "",
            "## 🛠 פעולות מומלצות",
            "",
        ])
        top_cats = sorted(a["error_categories"].items(), key=lambda x: -x[1])[:3]
        recs = {
            "timeout": "השלב חורג מ-30 דק' — צמצם prompt או חלק לתת-שלבים.",
            "claude_cli": "בדוק `claude` ב-CLI ידני. אולי צריך עדכון או re-auth.",
            "budget": "`moki-cap 50` להעלאה זמני, או `moki-level 0` לבדיקה כל gate.",
            "missing_data": "ה-researcher לא מחזיר מאמרים — בדוק health של 7 המקורות.",
            "json_parse": "Claude החזיר JSON שבור — צמצם את הפרומפט או הוסף retry.",
            "file_not_found": "פייפליין מצפה לקובץ שלא נוצר. checkpoint corruption?",
            "type_error": "באג בקוד — בדוק את ה-stack trace ב-`output/cron_*.log`.",
            "rate_limit": "Semantic Scholar — מפסיק לבד אחרי 2 retries, ההפסד מינימלי.",
            "loop_detected": "סוכן מנסה אותה משימה שוב ושוב — בדוק qa_checker.",
            "qa_fail": "QA חוסם — הסוכן הבא יראה את הסיבה דרך scratchpad.",
            "other": "פתח את אחת הדוגמאות לעיל לבדיקה ידנית.",
        }
        for cat, n in top_cats:
            parts.append(f"- **{_ERROR_LABELS.get(cat, cat)}** ({n}×): {recs.get(cat, '?')}")

    # Save
    out_path = OUTPUT_DIR / "_memory" / "failure_report.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path


def push_to_scratchpad(a: dict):
    """Inject failure summary as scratchpad note for next pipeline run."""
    if not a["alerts"]:
        return
    try:
        from scratchpad import note as _scratch_note
        top_cats = sorted(a["error_categories"].items(), key=lambda x: -x[1])[:3]
        cat_summary = ", ".join(f"{_ERROR_LABELS.get(c, c)}: {n}×" for c, n in top_cats)
        _scratch_note("failure_analyzer", "common_failures", {
            "issue": f"success rate {a['success_rate']:.0%} ב-{a['window_days']} ימים",
            "top_failures": cat_summary,
            "summary": "; ".join(al["message"] for al in a["alerts"][:3]),
        })
    except Exception:
        pass


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    window = 30
    if "--window" in sys.argv:
        idx = sys.argv.index("--window")
        if idx + 1 < len(sys.argv) and sys.argv[idx + 1].isdigit():
            window = int(sys.argv[idx + 1])

    a = analyze(window_days=window)

    if a["runs_analyzed"] == 0:
        print(f"⚠️ No runs found in last {window} days.")
        return

    print(f"\n🚨 Failure Analyzer — {a['runs_analyzed']} ריצות ב-{window} ימים אחרונים\n")
    print(f"  Success rate: {a['success_rate']:.0%}")
    print(f"  Failed: {a['failed_count']}")
    print(f"  Runaways: {len(a['runaways'])}")

    if a["error_categories"]:
        print(f"\n  📊 התפלגות שגיאות:")
        for cat, n in sorted(a["error_categories"].items(), key=lambda x: -x[1]):
            print(f"     {_ERROR_LABELS.get(cat, cat)}: {n}")

    if a["alerts"]:
        print(f"\n  ⚠️ Alerts:")
        for al in a["alerts"]:
            print(f"     • {al['message']}")

    path = md_report(a)
    push_to_scratchpad(a)
    print(f"\n📝 Report: {path.relative_to(OUTPUT_DIR.parent)}")


if __name__ == "__main__":
    main()
