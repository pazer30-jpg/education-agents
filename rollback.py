"""
rollback.py — Undo last pipeline run.
מבטל את כל הקבצים שנוצרו בריצה האחרונה.
מעביר ל-output/_trash/ במקום למחוק (recoverable).

Usage:
  python3 rollback.py                    # show what would be rolled back
  python3 rollback.py --execute          # actually move to trash
  python3 rollback.py --restore <date>   # restore from trash
"""

import sys
import shutil
from pathlib import Path
from datetime import datetime, timedelta

from config import OUTPUT_DIR

TRASH_DIR = OUTPUT_DIR / "_trash"
TRASH_DIR.mkdir(parents=True, exist_ok=True)


def find_recent_files(since_hours: float = 24) -> list[dict]:
    """Find files modified in last N hours across content dirs."""
    cutoff = datetime.now() - timedelta(hours=since_hours)
    found = []

    for subdir in ["articles", "posts/linkedin", "posts/blog", "posts/podcast",
                   "designs", "ready"]:
        d = OUTPUT_DIR / subdir
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if not f.is_file() or f.name.startswith(".") or f.name.endswith(".bak"):
                continue
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime >= cutoff:
                found.append({
                    "path": f,
                    "mtime": mtime,
                    "size": f.stat().st_size,
                    "rel": str(f.relative_to(OUTPUT_DIR)),
                })

    found.sort(key=lambda x: x["mtime"], reverse=True)
    return found


def rollback(since_hours: float = 24, execute: bool = False) -> int:
    files = find_recent_files(since_hours)
    if not files:
        print(f"  ℹ️  אין קבצים שנוצרו ב-{since_hours} שעות אחרונות")
        return 0

    print(f"\n🔙 Rollback — {len(files)} קבצים מ-{since_hours} שעות אחרונות:")
    for f in files[:20]:
        print(f"   • {f['rel']}  ({f['mtime'].strftime('%H:%M')})")

    if len(files) > 20:
        print(f"   ... ועוד {len(files) - 20}")

    if not execute:
        print(f"\n  הרץ עם --execute כדי להעביר ל-{TRASH_DIR.name}/")
        return len(files)

    # Move to trash with timestamp
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bucket = TRASH_DIR / f"rollback_{stamp}"
    bucket.mkdir(parents=True, exist_ok=True)

    moved = 0
    for f in files:
        try:
            target = bucket / f["rel"].replace("/", "__")
            shutil.move(str(f["path"]), str(target))
            moved += 1
        except Exception as e:
            print(f"   ⚠️  Failed to move {f['rel']}: {e}")

    print(f"\n  ✅ הועברו {moved}/{len(files)} ל-{bucket}")
    print(f"     שחזור: python3 rollback.py --restore {stamp}")
    return moved


def restore(stamp: str) -> int:
    bucket = TRASH_DIR / f"rollback_{stamp}"
    if not bucket.exists():
        print(f"  ❌ לא נמצא: {bucket}")
        return 0

    restored = 0
    for f in bucket.iterdir():
        if not f.is_file():
            continue
        # Convert back: __  →  /
        rel = f.name.replace("__", "/")
        target = OUTPUT_DIR / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(f), str(target))
            restored += 1
        except Exception as e:
            print(f"   ⚠️  {e}")

    print(f"  ✅ שוחזרו {restored} קבצים")
    return restored


def main():
    if "--restore" in sys.argv:
        idx = sys.argv.index("--restore")
        if idx + 1 < len(sys.argv):
            restore(sys.argv[idx + 1])
        else:
            # List available rollbacks
            print("Available rollbacks:")
            for d in sorted(TRASH_DIR.iterdir(), reverse=True)[:10]:
                if d.name.startswith("rollback_"):
                    stamp = d.name.replace("rollback_", "")
                    n_files = sum(1 for _ in d.iterdir())
                    print(f"  {stamp} — {n_files} files")
        return

    hours = 24
    for i, a in enumerate(sys.argv):
        if a == "--hours" and i + 1 < len(sys.argv):
            try:
                hours = float(sys.argv[i + 1])
            except ValueError:
                pass

    rollback(since_hours=hours, execute="--execute" in sys.argv)


if __name__ == "__main__":
    main()
