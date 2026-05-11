"""
test_perf_regression.py — Performance regression tracker.
מתריע אם הריצה האחרונה הייתה ארוכה משמעותית מהממוצע של 7 ימים.

Usage:
  python3 test_perf_regression.py          # check + alert
  python3 test_perf_regression.py --watch  # continuous monitoring (sleep 60s loop)
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timedelta

PROJECT_DIR = Path(__file__).parent
ANALYTICS = PROJECT_DIR / "output" / "analytics.json"
ALERT_THRESHOLD = 1.5    # alert if last run > 1.5x avg
ABORT_THRESHOLD = 2.5    # abort recommendation if > 2.5x avg


def load_runs():
    if not ANALYTICS.exists():
        return []
    try:
        data = json.loads(ANALYTICS.read_text(encoding="utf-8"))
        return data.get("runs", [])
    except Exception:
        return []


def analyze():
    runs = load_runs()
    if len(runs) < 3:
        return {"status": "insufficient_data", "samples": len(runs)}

    # Last 7 days of runs
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    recent = [r for r in runs if (r.get("started_at") or "") > cutoff]
    if len(recent) < 2:
        recent = runs[-7:]  # fallback to last 7 by index

    durations = [r.get("duration_s", 0) / 60 for r in recent if r.get("duration_s")]
    if not durations:
        return {"status": "no_durations", "samples": len(recent)}

    avg = sum(durations) / len(durations)
    last = durations[-1]
    ratio = last / avg if avg > 0 else 0

    status = "ok"
    if ratio >= ABORT_THRESHOLD:
        status = "critical"
    elif ratio >= ALERT_THRESHOLD:
        status = "warning"

    # Cost regression too
    costs = [r.get("est_cost", 0) for r in recent if r.get("est_cost")]
    avg_cost = sum(costs) / len(costs) if costs else 0
    last_cost = costs[-1] if costs else 0
    cost_ratio = last_cost / avg_cost if avg_cost > 0 else 0

    # QA regression
    qas = [r.get("avg_qa") for r in recent if r.get("avg_qa")]
    avg_qa = sum(qas) / len(qas) if qas else 0
    last_qa = qas[-1] if qas else 0

    return {
        "status": status,
        "samples": len(recent),
        "avg_min": round(avg, 1),
        "last_min": round(last, 1),
        "ratio": round(ratio, 2),
        "avg_cost": round(avg_cost, 2),
        "last_cost": round(last_cost, 2),
        "cost_ratio": round(cost_ratio, 2),
        "avg_qa": round(avg_qa, 1) if avg_qa else None,
        "last_qa": last_qa,
        "last_run": (recent[-1].get("started_at") or "")[:16].replace("T", " "),
    }


def format_report(result: dict) -> str:
    if result.get("status") == "insufficient_data":
        return f"  ℹ️  אין מספיק נתונים ({result['samples']} ריצות, צריך ≥3)"
    if result.get("status") == "no_durations":
        return f"  ℹ️  אין נתוני זמן ב-{result['samples']} ריצות"

    status_icons = {"ok": "✅", "warning": "⚠️", "critical": "🚨"}
    icon = status_icons.get(result["status"], "❓")

    lines = [
        f"\n{icon} Performance Regression Report — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"  Last run:  {result['last_run']}",
        f"  Time:      {result['last_min']:.0f} דק' (ממוצע 7 ימים: {result['avg_min']:.0f} דק', ratio={result['ratio']:.1f}×)",
        f"  Cost:      ${result['last_cost']} (ממוצע: ${result['avg_cost']}, ratio={result['cost_ratio']:.1f}×)",
    ]
    if result.get("last_qa"):
        lines.append(f"  QA:        {result['last_qa']}/100 (ממוצע: {result['avg_qa']:.0f}/100)")

    if result["status"] == "critical":
        lines.append(f"\n  🚨 חריגה קריטית: ריצה אחרונה הייתה {result['ratio']:.1f}× מהממוצע")
        lines.append(f"     המלצה: עצור את הריצה הבאה ובדוק מה קרה")
    elif result["status"] == "warning":
        lines.append(f"\n  ⚠️  חריגה: ריצה אחרונה {result['ratio']:.1f}× מהממוצע — בדוק לוגים")
    else:
        lines.append(f"\n  ✅ בסדר — בטווח רגיל")
    return "\n".join(lines)


def main():
    result = analyze()
    print(format_report(result))
    # Exit code: 0 ok, 1 warning, 2 critical
    return {"ok": 0, "warning": 1, "critical": 2}.get(result.get("status", "ok"), 0)


if __name__ == "__main__":
    sys.exit(main())
