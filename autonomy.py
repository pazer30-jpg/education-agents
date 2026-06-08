"""
autonomy.py — Daily autonomy framework for all agents.

The orchestrator pipeline runs Mon/Wed/Thu mornings. This file lets each
agent ALSO have a small daily routine that runs in the background — so
the morning cron starts from a warmer state instead of from zero.

Architecture
────────────
  - autonomy.py is the single entry point: `python3 autonomy.py <hour>`
  - Each routine is a function decorated with @routine(hour=H, tier=T, …)
  - autonomy.py dispatches based on the current hour
  - Tier 0 = off (skip everything)
        1 = deterministic only (no LLM calls, ~$0/day)
        2 = + cheap LLM routines (~$1.50/day)
        3 = + creative LLM routines (~$3.50/day)
  - Selected via MOKI_AUTONOMY_TIER in .env (default 1)
  - Each routine reports to output/_state/autonomy_log.json and the
    daily alerts memory file (active_alerts.md)
  - Budget gate: before any LLM routine, check daily_budget_status —
    skip if remaining < routine.cost_estimate * 2

Schedule (24h clock, all local time)
────────────────────────────────────
  02:00  retroactive_polishing (Tier 3)
  02:30  citation_watcher (Tier 2)
  03:00  corpus_refresh (Tier 1)
  04:00  trend_mapping (Tier 2)
  05:00  repurposer (Tier 3)
  06:00  curator_dynamic_priority + topic_radar (Tier 1)
  06:30  outline_prewarm (Tier 2)
  09:30  weekly_meta_synthesis (Tier 2, Mondays only)
  11:00  engagement_refresher (Tier 1)
  23:00  visual_backlog (Tier 3)

The 5-minute Telegram-approval polling is NOT in this file — it runs as
a separate launchd job (com.paz.moki.telegram-poll.plist) because it
needs higher frequency than autonomy's hourly dispatch.

Usage
──────
  python3 autonomy.py            # run all routines that match THIS hour
  python3 autonomy.py --hour 6   # explicit hour override (for testing)
  python3 autonomy.py --list     # show the routine schedule + tier
  python3 autonomy.py --tier 2   # raise tier ceiling for this run
  python3 autonomy.py --dry-run  # log what would run, don't execute
"""

import argparse
import importlib
import json
import os
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from config import OUTPUT_DIR

# When run as `python3 autonomy.py`, this file is __main__. Tier modules
# do `from autonomy import routine`, which would create a SECOND copy of
# this module under the name "autonomy" — and their decorators would
# register to that second copy's _REGISTRY, leaving __main__._REGISTRY
# empty. Alias them to the same instance so registrations land in one place.
sys.modules.setdefault("autonomy", sys.modules[__name__])

LOG_FILE     = OUTPUT_DIR / "_state" / "autonomy_log.json"
ALERTS_FILE  = OUTPUT_DIR / "_memory" / "active_alerts.md"


# ─────────────────────────────────────────────
# Routine registry
# ─────────────────────────────────────────────

@dataclass
class Routine:
    name:           str
    hour:           int          # 0-23, when to run
    tier:           int          # 1=safe, 2=cheap-LLM, 3=creative-LLM
    callable:       Callable[[], dict]
    cost_estimate:  float = 0.0  # est USD per run (used by budget gate)
    weekday_only:   int  = -1    # -1=any, 0=Mon..6=Sun; for weekly routines
    description:    str  = ""


_REGISTRY: list[Routine] = []


def routine(hour: int, tier: int, cost: float = 0.0,
            weekday_only: int = -1, description: str = ""):
    """Decorator to register a daily routine."""
    def deco(fn):
        _REGISTRY.append(Routine(
            name          = fn.__name__,
            hour          = hour,
            tier          = tier,
            callable      = fn,
            cost_estimate = cost,
            weekday_only  = weekday_only,
            description   = description,
        ))
        return fn
    return deco


# ─────────────────────────────────────────────
# Budget gate + tier read
# ─────────────────────────────────────────────

def _autonomy_tier() -> int:
    try:
        return int(os.environ.get("MOKI_AUTONOMY_TIER", "1"))
    except Exception:
        return 1


def _can_afford(cost: float) -> tuple[bool, str]:
    """Refuse LLM routines if today's remaining < 2× est cost."""
    if cost <= 0:
        return True, "no cost"
    try:
        from claude_cli import daily_budget_status
        bs = daily_budget_status()
        if bs["remaining_usd"] < cost * 2:
            return False, (f"budget tight: ${bs['remaining_usd']:.2f} remaining "
                           f"< 2× est ${cost:.2f}")
        return True, f"${bs['remaining_usd']:.2f} remaining"
    except Exception as e:
        return True, f"(budget check skipped: {e})"


# ─────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────

def _log_event(event: dict) -> None:
    """Append a single event to autonomy_log.json. Cap at 500 entries."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        log = json.loads(LOG_FILE.read_text(encoding="utf-8")) if LOG_FILE.exists() else []
    except Exception:
        log = []
    log.append(event)
    log = log[-500:]  # cap
    LOG_FILE.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def _refresh_alerts() -> None:
    """Rebuild active_alerts.md from the last 24h of routine outcomes."""
    if not LOG_FILE.exists():
        return
    try:
        log = json.loads(LOG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    from datetime import timedelta
    cutoff = datetime.now() - timedelta(hours=24)
    recent = [e for e in log
              if e.get("at") and datetime.fromisoformat(e["at"]) >= cutoff]
    if not recent:
        return
    lines = [
        "---", "moki: true", "type: active_alerts",
        f"updated: {datetime.now().isoformat(timespec='seconds')}",
        "---", "",
        "# 🛎 התראות פעילות (24h)",
        "",
    ]
    by_status = {"ok": [], "warn": [], "error": [], "skipped": []}
    for e in recent:
        by_status.setdefault(e.get("status", "ok"), []).append(e)
    if by_status.get("error"):
        lines.append("## ❌ שגיאות")
        for e in by_status["error"][-5:]:
            lines.append(f"- `{e['routine']}` · {e.get('message', '')[:140]}")
        lines.append("")
    if by_status.get("warn"):
        lines.append("## ⚠️ אזהרות")
        for e in by_status["warn"][-5:]:
            lines.append(f"- `{e['routine']}` · {e.get('message', '')[:140]}")
        lines.append("")
    if by_status.get("skipped"):
        lines.append("## ⏭ דולגו")
        for e in by_status["skipped"][-5:]:
            lines.append(f"- `{e['routine']}` · {e.get('message', '')[:140]}")
        lines.append("")
    lines.append("## ✅ פעולות שהצליחו")
    for e in by_status.get("ok", [])[-10:]:
        msg = e.get("message", "") or e.get("summary", "")
        lines.append(f"- `{e['routine']}` · {msg[:140]}")
    ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ALERTS_FILE.write_text("\n".join(lines), encoding="utf-8")


# ─────────────────────────────────────────────
# Dispatch
# ─────────────────────────────────────────────

def _should_run(r: Routine, hour: int, tier_ceiling: int) -> tuple[bool, str]:
    if r.tier > tier_ceiling:
        return False, f"tier {r.tier} > ceiling {tier_ceiling}"
    if r.hour != hour:
        return False, f"hour mismatch (scheduled {r.hour:02d}:00)"
    if r.weekday_only >= 0 and datetime.now().weekday() != r.weekday_only:
        return False, f"weekday mismatch (Mon=0..Sun=6, needs {r.weekday_only})"
    ok, reason = _can_afford(r.cost_estimate)
    if not ok:
        return False, reason
    return True, "ready"


def dispatch(hour: int, tier_ceiling: int, dry_run: bool = False) -> dict:
    """Run all registered routines matching the given hour + tier ceiling."""
    results = {"hour": hour, "tier_ceiling": tier_ceiling, "ran": [], "skipped": []}
    for r in _REGISTRY:
        ok, reason = _should_run(r, hour, tier_ceiling)
        if not ok:
            results["skipped"].append({"routine": r.name, "reason": reason})
            _log_event({
                "at":      datetime.now().isoformat(timespec="seconds"),
                "routine": r.name,
                "status":  "skipped",
                "message": reason,
            })
            continue
        if dry_run:
            results["ran"].append({"routine": r.name, "dry_run": True})
            continue
        try:
            out = r.callable() or {}
            results["ran"].append({"routine": r.name, "result": out})
            _log_event({
                "at":      datetime.now().isoformat(timespec="seconds"),
                "routine": r.name,
                "status":  out.get("status", "ok"),
                "message": out.get("message", ""),
                "summary": out.get("summary", ""),
            })
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:200]}"
            results["ran"].append({"routine": r.name, "error": err})
            _log_event({
                "at":      datetime.now().isoformat(timespec="seconds"),
                "routine": r.name,
                "status":  "error",
                "message": err,
                "trace":   traceback.format_exc()[:600],
            })
    _refresh_alerts()
    return results


# ─────────────────────────────────────────────
# Routine implementations — imported from agent modules below
# ─────────────────────────────────────────────

# These imports register routines via the @routine decorator at import time.
# Wrapping in try/except keeps autonomy.py runnable even if one routine has
# an import bug — the others still dispatch.
def _load_routines() -> None:
    for mod_name in (
        "autonomy_routines.tier1",
        "autonomy_routines.tier2",
        "autonomy_routines.tier3",
    ):
        try:
            importlib.import_module(mod_name)
        except Exception as e:
            _log_event({
                "at":      datetime.now().isoformat(timespec="seconds"),
                "routine": "_load",
                "status":  "error",
                "message": f"failed to import {mod_name}: {e}",
            })


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Daily autonomy dispatcher")
    ap.add_argument("--hour",    type=int, default=None,
                    help="hour to dispatch for (default: now)")
    ap.add_argument("--tier",    type=int, default=None,
                    help="tier ceiling override (default: MOKI_AUTONOMY_TIER or 1)")
    ap.add_argument("--list",    action="store_true",
                    help="show registered routines + schedule")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would run but don't execute")
    args = ap.parse_args()

    _load_routines()

    if args.list:
        print(f"📋 {len(_REGISTRY)} routines registered "
              f"(tier ceiling: {args.tier or _autonomy_tier()})")
        for r in sorted(_REGISTRY, key=lambda r: (r.hour, r.tier)):
            day = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][r.weekday_only] \
                  if r.weekday_only >= 0 else "any"
            print(f"  {r.hour:02d}:00 · T{r.tier} · ${r.cost_estimate:.2f} · "
                  f"{day:3} · {r.name}  — {r.description}")
        return

    hour = args.hour if args.hour is not None else datetime.now().hour
    tier = args.tier if args.tier is not None else _autonomy_tier()
    res = dispatch(hour, tier, dry_run=args.dry_run)
    print(f"⏰ {hour:02d}:00 · tier ≤ {tier} · "
          f"ran {len(res['ran'])} · skipped {len(res['skipped'])}")
    for r in res["ran"]:
        if "error" in r:
            print(f"  ❌ {r['routine']}: {r['error']}")
        elif r.get("dry_run"):
            print(f"  🔵 {r['routine']} (dry-run)")
        else:
            msg = r.get("result", {}).get("message", "")
            print(f"  ✅ {r['routine']}: {msg[:100]}")


if __name__ == "__main__":
    main()
