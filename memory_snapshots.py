"""
memory_snapshots.py — Daily versioned snapshots of output/_memory/.

Why: Memory files (voice_rules, humanize_rules, hook_winners, etc.) are read
by every agent on every run. A bad edit can silently degrade the entire
pipeline. With daily snapshots you can:
  1. Diff today's voice_rules.md vs last week to debug "why did the voice shift?"
  2. Revert a single file from a snapshot if needed
  3. See what evolved over time without polluting git history

Snapshots live at output/_memory/.history/YYYY-MM-DD/ — gitignored by default.
Auto-pruned after 30 days.

Usage:
  python3 memory_snapshots.py              # snapshot today + prune old
  python3 memory_snapshots.py --diff voice_rules  # diff today vs yesterday
  python3 memory_snapshots.py --list       # list available dates
"""

import argparse
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from config import OUTPUT_DIR

MEMORY_DIR = OUTPUT_DIR / "_memory"
HISTORY_DIR = MEMORY_DIR / ".history"
KEEP_DAYS = 30


def snapshot_today() -> Path:
    """Copy current state of _memory/*.md to .history/YYYY-MM-DD/. Idempotent."""
    today_dir = HISTORY_DIR / datetime.now().strftime("%Y-%m-%d")
    today_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0
    for md in MEMORY_DIR.glob("*.md"):
        # Skip our own history dir if it accidentally matches
        if ".history" in md.parts:
            continue
        target = today_dir / md.name
        # If today's snapshot already has this file with same content, skip
        if target.exists() and target.read_bytes() == md.read_bytes():
            skipped += 1
            continue
        shutil.copy2(md, target)
        copied += 1
    return today_dir


def prune_old(keep_days: int = KEEP_DAYS) -> int:
    """Delete snapshot dirs older than keep_days. Returns count pruned."""
    if not HISTORY_DIR.exists():
        return 0
    cutoff = datetime.now() - timedelta(days=keep_days)
    pruned = 0
    for d in HISTORY_DIR.iterdir():
        if not d.is_dir():
            continue
        try:
            d_date = datetime.strptime(d.name, "%Y-%m-%d")
        except ValueError:
            continue
        if d_date < cutoff:
            shutil.rmtree(d)
            pruned += 1
    return pruned


def list_snapshots() -> list[str]:
    """Available snapshot dates, newest first."""
    if not HISTORY_DIR.exists():
        return []
    dates = []
    for d in HISTORY_DIR.iterdir():
        if d.is_dir() and len(d.name) == 10 and d.name[4] == "-" and d.name[7] == "-":
            dates.append(d.name)
    return sorted(dates, reverse=True)


def diff_file(name: str, days_ago: int = 1) -> str:
    """Diff current memory file vs N days ago. Returns unified diff string."""
    import difflib
    current = MEMORY_DIR / f"{name}.md"
    if not current.exists():
        return f"❌ {name}.md not in current memory"
    target_date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    past = HISTORY_DIR / target_date / f"{name}.md"
    if not past.exists():
        # try most recent snapshot containing this file
        for d in list_snapshots():
            candidate = HISTORY_DIR / d / f"{name}.md"
            if candidate.exists() and d != datetime.now().strftime("%Y-%m-%d"):
                past = candidate
                target_date = d
                break
    if not past.exists():
        return f"❌ No historical snapshot of {name}.md found"
    diff = difflib.unified_diff(
        past.read_text(encoding="utf-8").splitlines(keepends=True),
        current.read_text(encoding="utf-8").splitlines(keepends=True),
        fromfile=f"{name}.md ({target_date})",
        tofile=f"{name}.md (now)",
    )
    return "".join(diff) or f"(no changes since {target_date})"


def main():
    ap = argparse.ArgumentParser(description="Daily snapshots of _memory/")
    ap.add_argument("--diff", metavar="NAME",
                    help="Diff memory file NAME vs N days ago (default 1)")
    ap.add_argument("--days", type=int, default=1,
                    help="With --diff: how many days back (default 1)")
    ap.add_argument("--list", action="store_true", help="List snapshot dates")
    args = ap.parse_args()

    if args.list:
        for d in list_snapshots()[:20]:
            print(d)
        return

    if args.diff:
        print(diff_file(args.diff, args.days))
        return

    # Default: snapshot + prune
    snap_dir = snapshot_today()
    n_files = len(list(snap_dir.glob("*.md")))
    pruned = prune_old()
    print(f"📸 snapshot: {snap_dir.name} ({n_files} files) · pruned {pruned} old · keeping {KEEP_DAYS}d")


if __name__ == "__main__":
    main()
