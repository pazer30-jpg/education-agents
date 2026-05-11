"""
agent_health.py — Per-agent health monitoring.

Two outputs:
  1. QA trend detection — alert if QA drops 10+ pts in 2 weeks
  2. Health card → output/_memory/agent_health.md (🟢🟡🔴 per agent)

Reads:
  - output/analytics.json    (per-run scores)
  - output/_state/scratchpad_usage.json (channel activity)

Writes:
  - output/_memory/agent_health.md (auto-rebuilt each pipeline run)
  - scratchpad note "trend_alert" if QA dropping

Usage:
  python3 agent_health.py            # build health card
  python3 agent_health.py --check    # trend check only (no MD write)
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

from config import OUTPUT_DIR


ANALYTICS_FILE = OUTPUT_DIR / "analytics.json"
USAGE_FILE = OUTPUT_DIR / "_state" / "scratchpad_usage.json"


# ─────────────────────────────────────────────
# Data loaders
# ─────────────────────────────────────────────

def _load_runs() -> list[dict]:
    if not ANALYTICS_FILE.exists():
        return []
    try:
        return json.loads(ANALYTICS_FILE.read_text(encoding="utf-8")).get("runs", [])
    except Exception:
        return []


def _load_usage() -> dict:
    if not USAGE_FILE.exists():
        return {}
    try:
        return json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ─────────────────────────────────────────────
# A: QA trend detection
# ─────────────────────────────────────────────

def qa_trends(window_days: int = 14, min_runs: int = 3,
              drop_threshold: int = 10) -> dict:
    """
    For each agent, compare avg QA in the last `window_days/2` vs the previous
    `window_days/2`. Return alerts for drops >= drop_threshold.
    """
    runs = _load_runs()
    if not runs:
        return {"alerts": [], "agents_tracked": 0}

    now = datetime.now()
    half = window_days // 2

    # Build per-agent QA history
    by_agent: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
    for r in runs:
        try:
            ts = datetime.fromisoformat(r.get("started_at", "").replace("Z", ""))
        except Exception:
            continue
        for agent, score in (r.get("qa_scores") or {}).items():
            if isinstance(score, (int, float)):
                by_agent[agent].append((ts, int(score)))

    alerts = []
    for agent, history in by_agent.items():
        recent = [s for ts, s in history if (now - ts).days <= half]
        prior = [s for ts, s in history
                 if half < (now - ts).days <= window_days]
        if len(recent) < min_runs or len(prior) < min_runs:
            continue
        avg_recent = sum(recent) / len(recent)
        avg_prior = sum(prior) / len(prior)
        drop = avg_prior - avg_recent
        if drop >= drop_threshold:
            alerts.append({
                "agent": agent,
                "avg_recent": round(avg_recent, 1),
                "avg_prior": round(avg_prior, 1),
                "drop": round(drop, 1),
                "n_recent": len(recent),
                "n_prior": len(prior),
                "severity": "high" if drop >= 20 else "medium",
            })

    # Push to scratchpad if any
    if alerts:
        try:
            from scratchpad import note as _scratch_note
            _scratch_note("agent_health", "trend_alert", {
                "issue": f"QA יורד אצל {len(alerts)} סוכנים",
                "alerts": alerts,
                "summary": "; ".join(
                    f"{a['agent']}: -{a['drop']:.0f} ({a['avg_prior']:.0f}→{a['avg_recent']:.0f})"
                    for a in alerts[:3]
                ),
            })
        except Exception:
            pass

    return {
        "alerts": alerts,
        "agents_tracked": len(by_agent),
        "window_days": window_days,
    }


# ─────────────────────────────────────────────
# C: Health card builder
# ─────────────────────────────────────────────

KNOWN_AGENTS = [
    ("planner",      "🧠", "Agent 0 — Planner"),
    ("researcher",   "🔍", "Agent 1 — Researcher"),
    ("writer",       "✍️", "Agent 2 — Writer"),
    ("editor",       "📝", "Agent 2.5 — Editor"),
    ("fact_checker", "✓",  "Agent 2.7 — Fact Checker"),
    ("content",      "✨", "Agent 3 — Content Creator"),
    ("designer",     "🎨", "Agent 4 — Designer"),
]


def _agent_score(agent: str, runs: list[dict]) -> dict:
    """Compute health for one agent across recent runs."""
    now = datetime.now()
    relevant = []
    for r in runs:
        try:
            ts = datetime.fromisoformat(r.get("started_at", "").replace("Z", ""))
        except Exception:
            continue
        if (now - ts).days > 30:
            continue
        # find this agent's step
        for step in r.get("steps", []):
            if step.get("agent") == agent:
                relevant.append({
                    "ts": ts,
                    "qa": (r.get("qa_scores") or {}).get(agent),
                    "duration_s": step.get("duration_s", 0),
                    "status": step.get("status", "unknown"),
                })
                break

    if not relevant:
        return {"status": "⚪", "label": "אין נתונים", "runs": 0}

    relevant.sort(key=lambda x: x["ts"], reverse=True)
    qa_vals = [r["qa"] for r in relevant if isinstance(r["qa"], (int, float))]
    avg_qa = round(sum(qa_vals) / len(qa_vals), 1) if qa_vals else None
    last_qa = qa_vals[0] if qa_vals else None
    success_rate = sum(1 for r in relevant if r["status"] == "ok") / len(relevant)
    last_run = relevant[0]["ts"]
    days_since = (now - last_run).days
    avg_dur = round(sum(r["duration_s"] for r in relevant) / len(relevant), 1)

    # Determine status
    if days_since > 14:
        status, label = "⚪", "ישן"
    elif success_rate < 0.5 or (avg_qa and avg_qa < 50):
        status, label = "🔴", "כשלים תכופים"
    elif success_rate < 0.8 or (avg_qa and avg_qa < 70):
        status, label = "🟡", "סביר"
    else:
        status, label = "🟢", "תקין"

    return {
        "status": status,
        "label": label,
        "runs": len(relevant),
        "avg_qa": avg_qa,
        "last_qa": last_qa,
        "success_rate": round(success_rate * 100),
        "avg_duration_min": round(avg_dur / 60, 1),
        "days_since_run": days_since,
        "last_run": last_run.strftime("%d/%m/%Y %H:%M"),
    }


def build_health_card() -> Path:
    """Build the health card MD and save to output/_memory/agent_health.md."""
    runs = _load_runs()
    usage = _load_usage()
    trends = qa_trends()

    parts = [
        "---",
        "moki: true",
        "type: agent_health",
        f"updated: {datetime.now().isoformat(timespec='seconds')}",
        "---",
        "",
        "# 🩺 Agent Health Card",
        "",
        f"_עודכן: {datetime.now().strftime('%d/%m/%Y %H:%M')} · אוטו' מ-`agent_health.py`_",
        f"_נסקרו {len(runs)} ריצות מהחודש האחרון_",
        "",
        "## 🚦 סטטוס לכל סוכן",
        "",
        "| סטטוס | סוכן | QA אחרון | QA ממוצע | success | משך ממוצע | ימים מהריצה האחרונה |",
        "|---|---|---|---|---|---|---|",
    ]

    for agent_id, emoji, name in KNOWN_AGENTS:
        h = _agent_score(agent_id, runs)
        if h["runs"] == 0:
            parts.append(f"| {h['status']} | {emoji} {name} | _no data_ | — | — | — | — |")
            continue
        last_qa = f"{h['last_qa']}" if h['last_qa'] is not None else "—"
        avg_qa = f"{h['avg_qa']}" if h['avg_qa'] is not None else "—"
        parts.append(
            f"| {h['status']} {h['label']} | {emoji} {name} | "
            f"{last_qa} | {avg_qa} | {h['success_rate']}% | "
            f"{h['avg_duration_min']} דק' | {h['days_since_run']} |"
        )

    # Trends section
    parts.extend(["", "## 📉 התראות trend (QA יורד)", ""])
    if trends["alerts"]:
        parts.append("| סוכן | קודם | עכשיו | ירידה | חומרה |")
        parts.append("|---|---|---|---|---|")
        for a in trends["alerts"]:
            sev_icon = "🔴" if a["severity"] == "high" else "🟡"
            parts.append(
                f"| {a['agent']} | {a['avg_prior']} | {a['avg_recent']} | "
                f"-{a['drop']:.1f} | {sev_icon} {a['severity']} |"
            )
    else:
        parts.append(f"_אין ירידות מובהקות ב-{trends['window_days']} ימים אחרונים._")

    # Reciprocal channel usage
    parts.extend(["", "## 🔁 שימוש בערוצי תקשורת בין-סוכנים (scratchpad)", ""])
    writes = usage.get("writes", {})
    reads = usage.get("reads", {})
    if writes or reads:
        all_channels = sorted(set(writes.keys()) | set(reads.keys()))
        parts.append("| ערוץ | כתיבות | קריאות | reciprocal? |")
        parts.append("|---|---|---|---|")
        for ch in all_channels:
            w = writes.get(ch, 0)
            r = reads.get(ch, 0)
            recip = "✅" if (w > 0 and r > 0) else ("📤 write only" if w > 0 else "📥 read only")
            parts.append(f"| `{ch}` | {w} | {r} | {recip} |")
        parts.append("")
        total_w = sum(writes.values())
        total_r = sum(reads.values())
        parts.append(f"_סה\"כ: {total_w} כתיבות, {total_r} קריאות._")
    else:
        parts.append("_אין נתוני שימוש עדיין — scratchpad פעיל רק במהלך ריצה._")

    # Last 5 runs summary
    parts.extend(["", "## 📋 5 הריצות האחרונות", ""])
    recent = sorted(runs, key=lambda r: r.get("started_at", ""), reverse=True)[:5]
    if recent:
        parts.append("| תאריך | בקשה | משך | success | QA ממוצע |")
        parts.append("|---|---|---|---|---|")
        for r in recent:
            ts = r.get("started_at", "")[:16].replace("T", " ")
            topic = (r.get("topic") or "")[:50]
            dur = round(r.get("duration_s", 0) / 60, 1)
            success = "✅" if r.get("success") else "❌"
            avg_qa = r.get("avg_qa", "—")
            parts.append(f"| {ts} | {topic} | {dur} דק' | {success} | {avg_qa} |")
    else:
        parts.append("_אין ריצות._")

    parts.extend([
        "",
        "---",
        "",
        "## 🎯 איך לפרש",
        "",
        "- 🟢 **תקין** — success ≥80% ו-QA ממוצע ≥70",
        "- 🟡 **סביר** — success 50-80% או QA 50-70",
        "- 🔴 **כשלים תכופים** — success <50% או QA <50",
        "- ⚪ **ישן/אין נתונים** — לא רץ ב-14 ימים אחרונים",
        "",
        "**אם רואה 🔴**: בדוק ב-`output/cron_*.log` למה הסוכן נכשל.",
        "**אם רואה 📤 write only ב-scratchpad**: סוכן כותב אבל אף אחד לא קורא — ערוץ מת.",
    ])

    out_path = OUTPUT_DIR / "_memory" / "agent_health.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    if "--check" in sys.argv:
        t = qa_trends()
        if t["alerts"]:
            print(f"⚠️  QA trends — {len(t['alerts'])} alerts:")
            for a in t["alerts"]:
                print(f"   {a['agent']}: -{a['drop']:.1f} ({a['avg_prior']:.0f}→{a['avg_recent']:.0f}) [{a['severity']}]")
        else:
            print(f"✅ QA trends OK ({t['agents_tracked']} agents tracked, {t['window_days']}-day window)")
        return

    path = build_health_card()
    runs = len(_load_runs())
    print(f"✅ Health card: {path.relative_to(OUTPUT_DIR.parent)} ({runs} runs analyzed)")


if __name__ == "__main__":
    main()
