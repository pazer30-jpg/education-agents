"""
scratchpad.py — Cross-agent shared memory during pipeline run.
כל סוכן יכול לכתוב/לקרוא תובנות תוך כדי ריצה.

Storage: output/_scratchpad.json (transient, cleared per run)

Use cases:
  - Voice QA writes "tone too academic" → Agent 3.6 reads it
  - Devil's Advocate writes "kill_switch reason" → Designer reads
  - Researcher writes "weak corpus warning" → Writer adjusts threshold

Usage:
  from scratchpad import note, read, clear
  note("voice_qa", "linkedin", {"score": 75, "issue": "too academic"})
  notes = read("voice_qa")
"""

import json
from pathlib import Path
from datetime import datetime
from threading import Lock

from config import OUTPUT_DIR

SCRATCH_FILE = OUTPUT_DIR / "_scratchpad.json"
USAGE_FILE = OUTPUT_DIR / "_state" / "scratchpad_usage.json"
USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
_lock = Lock()


def _load_usage() -> dict:
    if not USAGE_FILE.exists():
        return {"writes": {}, "reads": {}, "first_used": datetime.now().isoformat()}
    try:
        return json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"writes": {}, "reads": {}, "first_used": datetime.now().isoformat()}


def _save_usage(data: dict):
    try:
        USAGE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _track_usage(action: str, agent: str, key: str):
    """Track scratchpad write/read counts per (agent, key)."""
    try:
        d = _load_usage()
        bucket = d.setdefault(action + "s", {})  # writes / reads
        slot = f"{agent}/{key}"
        bucket[slot] = bucket.get(slot, 0) + 1
        d["last_updated"] = datetime.now().isoformat()
        _save_usage(d)
    except Exception:
        pass


def usage_stats() -> dict:
    """Return reciprocal-channel usage stats."""
    d = _load_usage()
    writes = d.get("writes", {})
    reads = d.get("reads", {})
    pairs = sorted(set(writes.keys()) | set(reads.keys()))
    return {
        "first_used": d.get("first_used"),
        "last_updated": d.get("last_updated"),
        "channels": [
            {
                "channel": p,
                "writes": writes.get(p, 0),
                "reads": reads.get(p, 0),
                "active": (writes.get(p, 0) > 0 and reads.get(p, 0) > 0),
            }
            for p in pairs
        ],
        "total_writes": sum(writes.values()),
        "total_reads": sum(reads.values()),
    }


def _load() -> dict:
    if not SCRATCH_FILE.exists():
        return {}
    try:
        return json.loads(SCRATCH_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict):
    SCRATCH_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def note(agent: str, key: str, value) -> None:
    """Add a note from an agent (thread-safe). Tracks usage."""
    with _lock:
        data = _load()
        if agent not in data:
            data[agent] = {}
        data[agent][key] = {
            "value": value,
            "ts": datetime.now().isoformat(),
        }
        _save(data)
    _track_usage("write", agent, key)


def read(agent: str = None, key: str = None):
    """
    Read scratchpad. Pass agent (and optionally key) to filter.
    Tracks usage for specific (agent, key) reads.
    """
    data = _load()
    if agent is None:
        return data
    agent_data = data.get(agent, {})
    if key is None:
        return agent_data
    entry = agent_data.get(key, {})
    if entry:
        _track_usage("read", agent, key)
    return entry.get("value") if entry else None


def all_warnings() -> list[dict]:
    """Get all warnings/issues across agents (for Agent 3 to consume)."""
    data = _load()
    warnings = []
    for agent, entries in data.items():
        for key, entry in entries.items():
            v = entry.get("value")
            if isinstance(v, dict) and v.get("severity") in ("warning", "critical"):
                warnings.append({
                    "from": agent,
                    "key": key,
                    "issue": v.get("issue", ""),
                    "severity": v.get("severity"),
                })
    return warnings


def format_for_agent(consumer: str) -> str:
    """Format scratchpad as prompt-injection block for a consuming agent."""
    data = _load()
    if not data:
        return ""
    lines = ["", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
             "📋 הערות מסוכנים אחרים (scratchpad)",
             "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]

    has_content = False
    for agent, entries in data.items():
        if agent == consumer:
            continue  # don't show agent its own notes
        for key, entry in entries.items():
            v = entry.get("value")
            if isinstance(v, dict):
                # Highlight QA retry hints prominently
                if key == "retry_hint" and v.get("issues"):
                    lines.append(
                        f"  ⚠️ QA נכשל ב-{v.get('agent','?')} (ניסיון {v.get('attempt','?')}). "
                        f"תקן: {v['issues']}"
                    )
                else:
                    summary = v.get("issue") or v.get("summary") or str(v)[:140]
                    lines.append(f"  • [{agent}/{key}] {summary}")
            else:
                summary = str(v)[:140]
                lines.append(f"  • [{agent}/{key}] {summary}")
            has_content = True

    if not has_content:
        return ""
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def clear() -> None:
    """Clear scratchpad — call at start of new pipeline run."""
    with _lock:
        if SCRATCH_FILE.exists():
            SCRATCH_FILE.unlink()


# CLI
if __name__ == "__main__":
    import sys
    if "--clear" in sys.argv:
        clear()
        print("✅ scratchpad cleared")
    else:
        data = _load()
        print(json.dumps(data, ensure_ascii=False, indent=2))
