"""
hook_log.py — Persistent log of winning hooks.

Each time hook_tester picks a winner (score ≥ 75), we log it.
Memory file output/_memory/hook_winners.md is regenerated nightly with
top 15 hooks per platform — feeds back into Agent 3's prompt as exemplars.
"""

import json
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR

LOG_DIR = OUTPUT_DIR / "_state"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "hook_log.json"
MEMORY_FILE = OUTPUT_DIR / "_memory" / "hook_winners.md"

WINNER_THRESHOLD = 75
MAX_PER_PLATFORM = 15


def _load() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    try:
        return json.loads(LOG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(entries: list[dict]):
    LOG_FILE.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def log_hook(platform: str, result: dict):
    """
    Called from Agent 3 after hook_tester picks a winner.
    Only logs if score >= WINNER_THRESHOLD.
    """
    score = result.get("score", 0)
    if score < WINNER_THRESHOLD:
        return
    hook = result.get("best", "")
    if not hook or len(hook) < 20:
        return
    entries = _load()
    # Dedupe — skip if same hook already logged
    if any(e.get("hook") == hook for e in entries):
        return
    entries.append({
        "platform": platform,
        "hook": hook,
        "score": score,
        "reasons": result.get("reasons", [])[:3],
        "switched": result.get("switched", False),
        "logged_at": datetime.now().isoformat(timespec="seconds"),
    })
    # Cap total entries (keep newest)
    entries = entries[-500:]
    _save(entries)


def regenerate_memory() -> str:
    """
    Rebuild output/_memory/hook_winners.md from log_file.
    Returns markdown body (also writes to file).
    """
    entries = _load()
    if not entries:
        return ""

    # Group by platform, sort by score desc, take top N
    by_platform: dict[str, list[dict]] = {}
    for e in entries:
        by_platform.setdefault(e["platform"], []).append(e)
    for plat in by_platform:
        by_platform[plat].sort(key=lambda x: (-x.get("score", 0), x.get("logged_at", "")))
        by_platform[plat] = by_platform[plat][:MAX_PER_PLATFORM]

    lines = [
        "---",
        "moki: true",
        "type: hook_winners",
        f"updated: {datetime.now().isoformat(timespec='seconds')}",
        "---",
        "",
        "# 🎣 Hooks שעבדו",
        "",
        f"> מתוך {len(entries)} hooks שנבדקו. סף ניצחון: {WINNER_THRESHOLD}/100.",
        "> Agent 3 (Content Creator) רואה את הקובץ הזה בכל יצירת תוכן — דוגמאות לחיקוי.",
        "",
    ]

    plat_label = {
        "linkedin": "💼 LinkedIn",
        "blog":     "📰 Blog",
        "podcast":  "🎙️ Podcast",
    }
    for plat, items in by_platform.items():
        lines.append(f"## {plat_label.get(plat, plat)}")
        lines.append("")
        for it in items:
            reasons = ", ".join(it.get("reasons", [])[:2]) or "—"
            hook = it["hook"].strip().replace("\n", " ")[:240]
            lines.append(f"- **{it['score']}/100** · {reasons}")
            lines.append(f"  > {hook}")
        lines.append("")

    body = "\n".join(lines)
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(body, encoding="utf-8")
    return body


if __name__ == "__main__":
    body = regenerate_memory()
    if body:
        print(f"✅ Regenerated {MEMORY_FILE} ({len(body)} chars)")
    else:
        print("⚠️ No hooks logged yet")
