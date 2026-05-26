"""
source_health.py — Track which research sources fail repeatedly + auto-skip.

Today we saw: Reddit 403, Semantic Scholar rate-limited, DOAJ flaky. Every
pipeline run tried all 8 sources, including the consistently-dead ones.
This module:
  1. Logs success/failure for each (source, run) pair
  2. Computes 7-day success rate per source
  3. Returns a "skip list" of sources to bypass (success_rate < threshold)
  4. Auto-rehabilitates: once per day, dead sources get a "probe" attempt

Researcher calls record(source, success) after each source attempt.
Researcher calls should_skip(source) before each attempt.

Storage: output/_state/source_health.json
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from config import OUTPUT_DIR

HEALTH_FILE = OUTPUT_DIR / "_state" / "source_health.json"
HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)

WINDOW_DAYS = 7        # rolling window for success rate
MIN_ATTEMPTS = 3       # need at least N attempts before judging
SKIP_THRESHOLD = 0.30  # below 30% success → skip
PROBE_INTERVAL_H = 24  # give dead sources a daily chance


def _load() -> dict:
    if not HEALTH_FILE.exists():
        return {"sources": {}, "updated": ""}
    try:
        return json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"sources": {}, "updated": ""}


def _save(data: dict):
    data["updated"] = datetime.now().isoformat(timespec="seconds")
    HEALTH_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")


def _trim_to_window(events: list[dict]) -> list[dict]:
    cutoff = datetime.now() - timedelta(days=WINDOW_DAYS)
    out = []
    for e in events:
        try:
            if datetime.fromisoformat(e["ts"]) >= cutoff:
                out.append(e)
        except Exception:
            pass
    return out


def record(source: str, success: bool, error: str = "") -> None:
    """Log a (source, success) event. Auto-trims old events."""
    data = _load()
    sources = data.setdefault("sources", {})
    s = sources.setdefault(source, {"events": [], "last_probe_at": None})
    s["events"].append({
        "ts":      datetime.now().isoformat(timespec="seconds"),
        "success": bool(success),
        "error":   (error or "")[:120],
    })
    s["events"] = _trim_to_window(s["events"])
    _save(data)


def stats(source: str) -> dict:
    """Return {attempts, successes, success_rate} for a source."""
    data = _load()
    s = data.get("sources", {}).get(source, {})
    events = _trim_to_window(s.get("events", []))
    attempts = len(events)
    successes = sum(1 for e in events if e.get("success"))
    rate = (successes / attempts) if attempts else 1.0
    return {"attempts": attempts, "successes": successes, "success_rate": rate}


def should_skip(source: str) -> tuple[bool, str]:
    """
    Returns (skip: bool, reason: str). False means proceed.

    Skip logic:
      - if < MIN_ATTEMPTS in window → never skip (need data)
      - if success_rate >= SKIP_THRESHOLD → don't skip
      - if last_probe_at >= PROBE_INTERVAL_H ago → don't skip (probe attempt)
      - otherwise: skip
    """
    data = _load()
    s = data.get("sources", {}).get(source, {})
    events = _trim_to_window(s.get("events", []))
    attempts = len(events)
    if attempts < MIN_ATTEMPTS:
        return False, f"only {attempts} attempts — need data"

    successes = sum(1 for e in events if e.get("success"))
    rate = successes / attempts
    if rate >= SKIP_THRESHOLD:
        return False, f"healthy ({rate:.0%})"

    # Below threshold — but allow a daily probe
    last_probe = s.get("last_probe_at")
    now = datetime.now()
    if last_probe:
        try:
            hours_since = (now - datetime.fromisoformat(last_probe)).total_seconds() / 3600
            if hours_since >= PROBE_INTERVAL_H:
                # Allow this attempt as a probe — mark it now
                s["last_probe_at"] = now.isoformat(timespec="seconds")
                _save(data)
                return False, f"probe attempt (last {hours_since:.0f}h ago)"
        except Exception:
            pass
    else:
        # First probe ever for a degraded source
        s["last_probe_at"] = now.isoformat(timespec="seconds")
        _save(data)
        return False, "first probe"

    return True, f"degraded ({rate:.0%} success over {attempts} attempts) — skipping"


def report() -> str:
    """Markdown summary of all source health."""
    data = _load()
    sources = data.get("sources", {})
    if not sources:
        return "אין נתונים עדיין."
    lines = [
        "| מקור | attempts | success | rate | status |",
        "|---|---|---|---|---|",
    ]
    for source in sorted(sources.keys()):
        st = stats(source)
        skip, reason = should_skip(source)
        emoji = "🔴" if skip else ("🟢" if st["success_rate"] >= 0.7 else "🟡")
        lines.append(
            f"| {source} | {st['attempts']} | {st['successes']} | "
            f"{st['success_rate']:.0%} | {emoji} {reason} |"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
