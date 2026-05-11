"""
observability.py — SLO-based monitoring for Moki.

מוקי הוא single-machine pipeline — לא צריך Prometheus/Grafana.
במקום, מגדיר SLOs ברורים ומדידים, ובודק burn rate בזמן אמת.

Usage:
  python3 observability.py             # show current status
  python3 observability.py --slo       # SLO compliance report
  python3 observability.py --burn      # burn rate analysis
  python3 observability.py --alert     # check active alerts
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timedelta

from config import OUTPUT_DIR

ANALYTICS = OUTPUT_DIR / "analytics.json"
ALERTS_FILE = OUTPUT_DIR / "active_alerts.json"


# ─────────────────────────────────────────────
# SLO definitions — what "good" means for Moki
# ─────────────────────────────────────────────

SLOS = {
    "pipeline_duration": {
        "target": 35,           # minutes
        "warning": 45,
        "critical": 60,
        "description": "Pipeline completes in ≤35 min (warn 45, crit 60)",
    },
    "pipeline_success_rate": {
        "target": 0.85,         # 85%
        "warning": 0.70,
        "critical": 0.50,
        "description": "85%+ pipelines succeed (warn 70%, crit 50%)",
    },
    "step_duration_p95": {
        "target": 20,           # minutes
        "warning": 30,
        "critical": 40,
        "description": "95th percentile step ≤20 min (warn 30, crit 40)",
    },
    "qa_score_avg": {
        "target": 85,           # /100
        "warning": 75,
        "critical": 65,
        "description": "QA score ≥85/100 (warn 75, crit 65)",
    },
    "voice_score_avg": {
        "target": 85,
        "warning": 75,
        "critical": 60,
        "description": "Voice QA ≥85/100 (warn 75, crit 60)",
    },
}


def _load_runs() -> list[dict]:
    if not ANALYTICS.exists():
        return []
    try:
        return json.loads(ANALYTICS.read_text(encoding="utf-8")).get("runs", [])
    except Exception:
        return []


# ─────────────────────────────────────────────
# Golden Signals — RED method (Rate, Errors, Duration)
# ─────────────────────────────────────────────

def golden_signals(window_days: int = 7) -> dict:
    """RED metrics for last N days."""
    cutoff = (datetime.now() - timedelta(days=window_days)).isoformat()
    runs = [r for r in _load_runs() if (r.get("started_at") or "") > cutoff]

    if not runs:
        return {"window_days": window_days, "rate": 0, "errors": 0, "duration_p50": 0,
                "duration_p95": 0, "samples": 0}

    rate = len(runs) / window_days  # runs per day
    errors = sum(1 for r in runs if not r.get("success"))
    error_rate = errors / len(runs) if runs else 0

    durations = sorted(r.get("duration_s", 0) / 60 for r in runs if r.get("duration_s"))
    p50 = durations[len(durations) // 2] if durations else 0
    p95_idx = max(0, int(len(durations) * 0.95) - 1)
    p95 = durations[p95_idx] if durations else 0

    return {
        "window_days": window_days,
        "samples": len(runs),
        "rate": round(rate, 2),
        "errors": errors,
        "error_rate": round(error_rate, 3),
        "duration_p50_min": round(p50, 1),
        "duration_p95_min": round(p95, 1),
    }


# ─────────────────────────────────────────────
# SLO compliance check
# ─────────────────────────────────────────────

def slo_compliance(window_days: int = 7) -> dict:
    """Check each SLO against actual metrics."""
    g = golden_signals(window_days)
    runs = [r for r in _load_runs()
            if (r.get("started_at") or "") > (datetime.now() - timedelta(days=window_days)).isoformat()]

    qas = [r.get("avg_qa") for r in runs if r.get("avg_qa")]
    avg_qa = sum(qas) / len(qas) if qas else 0
    success_rate = 1 - g["error_rate"]

    results = {}

    # pipeline_duration (using p95)
    duration = g["duration_p95_min"]
    s = SLOS["pipeline_duration"]
    if duration <= s["target"]:
        status = "ok"
    elif duration <= s["warning"]:
        status = "warning"
    elif duration <= s["critical"]:
        status = "critical"
    else:
        status = "breached"
    results["pipeline_duration"] = {
        "value": duration, "target": s["target"], "status": status,
        "description": s["description"],
    }

    # success_rate
    s = SLOS["pipeline_success_rate"]
    if success_rate >= s["target"]:
        status = "ok"
    elif success_rate >= s["warning"]:
        status = "warning"
    else:
        status = "critical" if success_rate >= s["critical"] else "breached"
    results["pipeline_success_rate"] = {
        "value": round(success_rate, 3), "target": s["target"], "status": status,
        "description": s["description"],
    }

    # qa_score_avg
    s = SLOS["qa_score_avg"]
    if avg_qa >= s["target"]:
        status = "ok"
    elif avg_qa >= s["warning"]:
        status = "warning"
    else:
        status = "critical" if avg_qa >= s["critical"] else "breached"
    results["qa_score_avg"] = {
        "value": round(avg_qa, 1), "target": s["target"], "status": status,
        "description": s["description"],
    }

    # Step duration p95 — extract from steps
    step_durs = []
    for r in runs:
        for step in r.get("steps", []):
            d = step.get("duration_s")
            if d:
                step_durs.append(d / 60)
    step_durs.sort()
    if step_durs:
        p95_step = step_durs[max(0, int(len(step_durs) * 0.95) - 1)]
    else:
        p95_step = 0
    s = SLOS["step_duration_p95"]
    if p95_step <= s["target"]:
        status = "ok"
    elif p95_step <= s["warning"]:
        status = "warning"
    elif p95_step <= s["critical"]:
        status = "critical"
    else:
        status = "breached"
    results["step_duration_p95"] = {
        "value": round(p95_step, 1), "target": s["target"], "status": status,
        "description": s["description"],
    }

    return {"window_days": window_days, "samples": g["samples"], "slos": results}


# ─────────────────────────────────────────────
# Burn rate (am I trending toward SLO breach?)
# ─────────────────────────────────────────────

def burn_rate() -> dict:
    """
    Compare last 24h to last 7 days.
    burn_rate > 1.5 = you're burning error budget faster than sustainable.
    """
    short = golden_signals(window_days=1)
    long = golden_signals(window_days=7)

    short_error_rate = short.get("error_rate", 0)
    long_error_rate = long.get("error_rate", 0)

    burn = short_error_rate / long_error_rate if long_error_rate > 0 else (
        2.0 if short_error_rate > 0 else 1.0
    )

    short_dur = short.get("duration_p95_min", 0)
    long_dur = long.get("duration_p95_min", 1)
    duration_burn = short_dur / long_dur if long_dur > 0 else 1.0

    return {
        "error_burn_rate": round(burn, 2),
        "duration_burn_rate": round(duration_burn, 2),
        "verdict": "alert" if burn > 1.5 or duration_burn > 1.5 else "ok",
        "short_window": short,
        "long_window": long,
    }


# ─────────────────────────────────────────────
# Alert generation
# ─────────────────────────────────────────────

def check_alerts() -> list[dict]:
    """Active alerts based on SLO + burn rate."""
    alerts = []

    slo = slo_compliance(window_days=7)
    for slo_name, data in slo["slos"].items():
        if data["status"] in ("critical", "breached"):
            alerts.append({
                "level": "critical" if data["status"] == "critical" else "page",
                "slo": slo_name,
                "value": data["value"],
                "target": data["target"],
                "message": f"{slo_name}: {data['value']} (target {data['target']}) — {data['status']}",
                "fired_at": datetime.now().isoformat(),
            })
        elif data["status"] == "warning":
            alerts.append({
                "level": "warning",
                "slo": slo_name,
                "value": data["value"],
                "target": data["target"],
                "message": f"{slo_name}: {data['value']} approaching target {data['target']}",
                "fired_at": datetime.now().isoformat(),
            })

    burn = burn_rate()
    if burn["verdict"] == "alert":
        alerts.append({
            "level": "warning",
            "slo": "burn_rate",
            "value": burn["error_burn_rate"],
            "target": 1.5,
            "message": f"24h error rate is {burn['error_burn_rate']}× the 7-day average — investigate",
            "fired_at": datetime.now().isoformat(),
        })

    # Persist for dashboard
    try:
        ALERTS_FILE.write_text(
            json.dumps({"alerts": alerts, "updated_at": datetime.now().isoformat()},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    return alerts


# ─────────────────────────────────────────────
# Reports
# ─────────────────────────────────────────────

def format_status() -> str:
    lines = [f"\n📊 Moki Observability — {datetime.now().strftime('%d/%m/%Y %H:%M')}"]
    g = golden_signals(7)
    lines.append(f"\n🔆 Golden Signals (7 days, {g['samples']} runs):")
    lines.append(f"   Rate:           {g['rate']} runs/day")
    lines.append(f"   Error rate:     {g['error_rate']*100:.1f}%")
    lines.append(f"   Duration p50:   {g['duration_p50_min']} min")
    lines.append(f"   Duration p95:   {g['duration_p95_min']} min")

    slo = slo_compliance(7)
    icons = {"ok": "✅", "warning": "⚠️", "critical": "🚨", "breached": "🔥"}
    lines.append(f"\n🎯 SLO Compliance:")
    for name, data in slo["slos"].items():
        icon = icons.get(data["status"], "?")
        lines.append(f"   {icon} {name}: {data['value']} / target {data['target']} → {data['status']}")

    burn = burn_rate()
    icon = "✅" if burn["verdict"] == "ok" else "⚠️"
    lines.append(f"\n🔥 Burn Rate (24h vs 7d):")
    lines.append(f"   {icon} Error burn:    {burn['error_burn_rate']}×")
    lines.append(f"   {icon} Duration burn: {burn['duration_burn_rate']}×")

    alerts = check_alerts()
    if alerts:
        lines.append(f"\n🚨 Active Alerts ({len(alerts)}):")
        for a in alerts[:5]:
            lines.append(f"   [{a['level'].upper()}] {a['message']}")
    else:
        lines.append(f"\n✅ No active alerts")

    return "\n".join(lines)


def main():
    if "--slo" in sys.argv:
        s = slo_compliance(7)
        print(json.dumps(s, ensure_ascii=False, indent=2))
    elif "--burn" in sys.argv:
        print(json.dumps(burn_rate(), ensure_ascii=False, indent=2))
    elif "--alert" in sys.argv:
        alerts = check_alerts()
        if alerts:
            print(json.dumps(alerts, ensure_ascii=False, indent=2))
            sys.exit(1)
        print("No active alerts")
    else:
        print(format_status())


if __name__ == "__main__":
    main()
