"""
cost_forecaster.py — Forecast pipeline run cost from history.

The old pre-check used a hardcoded $18 ceiling that blocked perfectly
viable runs (median actual cost = $5.65 in 101 runs). This module reads
analytics.json to compute a more honest forecast.

Forecast logic:
  1. Use the last N=10 successful runs of the same mode (research/podcast)
  2. Report median, P75, and max
  3. Decision thresholds:
     - "go"        : remaining >= P75 (comfortable)
     - "tight"     : remaining >= median (likely fine, no headroom for retries)
     - "risky"     : remaining < median (likely to fail mid-pipeline)

Public API:
  forecast(mode="research") -> {
      "median_usd": float,
      "p75_usd":    float,
      "max_usd":    float,
      "samples":    int,
      "decision":   "go" | "tight" | "risky",
      "remaining":  float,
      "reason":     str,
  }
"""

import json
from pathlib import Path

from config import OUTPUT_DIR

ANALYTICS_FILE = OUTPUT_DIR / "analytics.json"

# Fallback when there's no history (first run or wiped state)
FALLBACK_MEDIAN = 6.0
FALLBACK_P75 = 9.0
FALLBACK_MAX = 15.0


def _load_runs() -> list[dict]:
    if not ANALYTICS_FILE.exists():
        return []
    try:
        data = json.loads(ANALYTICS_FILE.read_text(encoding="utf-8"))
        return data.get("runs", []) or []
    except Exception:
        return []


def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = (len(sorted_values) - 1) * q
    lo = int(idx)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = idx - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def _matches_mode(run: dict, mode: str) -> bool:
    """research / podcast / all — match by request topic keywords."""
    if mode in ("all", "", None):
        return True
    req = (run.get("topic") or run.get("request") or "").lower()
    if mode == "research":
        return "podcast" not in req
    if mode == "podcast":
        return "podcast" in req
    return True


def forecast(mode: str = "research", n_samples: int = 10) -> dict:
    """
    Estimate cost of the next run based on recent successful runs of the same mode.
    """
    from claude_cli import daily_budget_status
    bs = daily_budget_status()
    remaining = bs.get("remaining_usd", 0)

    runs = _load_runs()
    matching = [
        r.get("est_cost") for r in runs
        if r.get("est_cost") and r.get("success") and _matches_mode(r, mode)
    ]
    matching = [c for c in matching if isinstance(c, (int, float)) and c > 0]
    matching = matching[-n_samples * 3:]  # look at last 3× sample window
    matching.sort()

    if len(matching) >= 3:
        # Use last n_samples after sort (highest are most conservative)
        sample = matching[-n_samples:] if len(matching) > n_samples else matching
        median_v = _quantile(sample, 0.50)
        p75_v    = _quantile(sample, 0.75)
        max_v    = max(sample)
        samples  = len(sample)
        source   = "history"
    else:
        median_v = FALLBACK_MEDIAN
        p75_v    = FALLBACK_P75
        max_v    = FALLBACK_MAX
        samples  = len(matching)
        source   = "fallback (insufficient history)"

    # Decision
    if remaining >= p75_v:
        decision = "go"
        reason = (f"comfortable: ${remaining:.2f} remaining ≥ P75 forecast "
                  f"(${p75_v:.2f})")
    elif remaining >= median_v:
        decision = "tight"
        reason = (f"tight: ${remaining:.2f} ≥ median (${median_v:.2f}) "
                  f"but below P75 (${p75_v:.2f}). No room for retries.")
    else:
        decision = "risky"
        reason = (f"risky: ${remaining:.2f} < median forecast (${median_v:.2f}). "
                  f"Pipeline likely to fail mid-run.")

    return {
        "mode":          mode,
        "median_usd":    round(median_v, 2),
        "p75_usd":       round(p75_v, 2),
        "max_usd":       round(max_v, 2),
        "samples":       samples,
        "source":        source,
        "remaining_usd": remaining,
        "decision":      decision,
        "reason":        reason,
    }


def format_for_console(f: dict) -> str:
    """Pretty-print forecast for pre-flight check."""
    icon = {"go": "✅", "tight": "🟡", "risky": "⛔"}[f["decision"]]
    src = "history" if f["source"] == "history" else "fallback"
    return (
        f"{icon} Cost forecast ({src}, {f['samples']} samples): "
        f"median ${f['median_usd']} · P75 ${f['p75_usd']} · max ${f['max_usd']}\n"
        f"   {f['reason']}"
    )


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "research"
    f = forecast(mode)
    print(format_for_console(f))
