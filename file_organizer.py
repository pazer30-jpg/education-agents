"""
file_organizer.py — מסדר קבצי תוכן לפי סטטוס פרסום

מבנה:
  output/posts/{linkedin,blog,podcast}/   ← drafts (Agent 3 כותב לכאן)
  output/ready/{linkedin,blog,podcast}/   ← עבר QA, מחכה לפרסום
  output/published/{linkedin,blog,podcast}/ ← פורסם בפועל
  output/archive/{linkedin,blog,podcast}/ ← ישן (>30 יום)

פקודות:
  organize_drafts()    — מעביר drafts → ready (אם עברו QA)
  mark_published(p)    — מעביר ready → published (האחרון של פלטפורמה)
  archive_old(days=30) — מעביר published ישן → archive
  status()             — דוח כמה קבצים בכל סטטוס
"""

import shutil
from pathlib import Path
from datetime import datetime, timedelta
from config import OUTPUT_DIR, LINKEDIN_DIR, BLOG_DIR, PODCAST_DIR

PLATFORMS = ("linkedin", "blog", "podcast")

DRAFT_DIRS = {
    "linkedin": LINKEDIN_DIR,
    "blog":     BLOG_DIR,
    "podcast":  PODCAST_DIR,
}

READY_DIR     = OUTPUT_DIR / "ready"
PUBLISHED_DIR = OUTPUT_DIR / "published"
ARCHIVE_DIR   = OUTPUT_DIR / "archive"

PATTERNS = {
    "linkedin": "*_ready.txt",
    "blog":     "*.md",
    "podcast":  "*_script_*.md",
}


def _ensure_dirs():
    for stage in (READY_DIR, PUBLISHED_DIR, ARCHIVE_DIR):
        for platform in PLATFORMS:
            (stage / platform).mkdir(parents=True, exist_ok=True)


def _list_real(directory: Path, pattern: str) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        (p for p in directory.glob(pattern) if not p.name.endswith(".bak")),
        key=lambda p: p.stat().st_mtime,
    )


def _passes_qa(file_path: Path, platform: str) -> bool:
    """בדיקת איכות מינימלית — לא ריק, יש תוכן ממשי."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    if platform == "linkedin":
        return 200 <= len(text) <= 3500
    if platform == "blog":
        return len(text) >= 800
    if platform == "podcast":
        return len(text) >= 500
    return False


# ─────────────────────────────────────────────
# Operations
# ─────────────────────────────────────────────

def organize_drafts() -> dict:
    """מעביר drafts שעוברים QA → ready. מחזיר דוח."""
    _ensure_dirs()
    moved = {p: 0 for p in PLATFORMS}
    skipped = {p: 0 for p in PLATFORMS}

    for platform in PLATFORMS:
        src_dir = DRAFT_DIRS[platform]
        if not src_dir.exists():
            continue
        for file in _list_real(src_dir, PATTERNS[platform]):
            if _passes_qa(file, platform):
                target = READY_DIR / platform / file.name
                if target.exists():
                    skipped[platform] += 1
                    continue
                shutil.copy2(file, target)
                moved[platform] += 1
            else:
                skipped[platform] += 1

    return {"moved": moved, "skipped": skipped}


def mark_published(platform: str, file_name: str | None = None) -> Path | None:
    """
    מעביר קובץ מ-ready → published.
    אם file_name לא ניתן — לוקח את האחרון של הפלטפורמה.
    """
    _ensure_dirs()
    if platform not in PLATFORMS:
        raise ValueError(f"platform חייב להיות אחד מ: {PLATFORMS}")

    src_dir = READY_DIR / platform
    if file_name:
        src = src_dir / file_name
        if not src.exists():
            return None
    else:
        files = sorted(src_dir.iterdir(), key=lambda p: p.stat().st_mtime)
        if not files:
            return None
        src = files[-1]

    today = datetime.now().strftime("%Y-%m-%d")
    target = PUBLISHED_DIR / platform / f"{today}_{src.name}"
    shutil.move(str(src), str(target))
    return target


def archive_old(days: int = 30) -> dict:
    """מעביר published ישן מ-N יום → archive."""
    _ensure_dirs()
    cutoff = datetime.now() - timedelta(days=days)
    archived = {p: 0 for p in PLATFORMS}

    for platform in PLATFORMS:
        src_dir = PUBLISHED_DIR / platform
        if not src_dir.exists():
            continue
        for file in src_dir.iterdir():
            mtime = datetime.fromtimestamp(file.stat().st_mtime)
            if mtime < cutoff:
                target = ARCHIVE_DIR / platform / file.name
                shutil.move(str(file), str(target))
                archived[platform] += 1

    return archived


def status() -> str:
    """דוח כמה קבצים בכל סטטוס לכל פלטפורמה."""
    _ensure_dirs()
    icons = {"linkedin": "💼", "blog": "📰", "podcast": "🎙️"}

    lines = ["", "═" * 50, "📂 מצב קבצי תוכן", "═" * 50, ""]
    lines.append(f"{'פלטפורמה':<14} {'drafts':>8} {'ready':>8} {'published':>10} {'archive':>9}")
    lines.append("─" * 50)

    totals = {"drafts": 0, "ready": 0, "published": 0, "archive": 0}
    for platform in PLATFORMS:
        drafts = len(_list_real(DRAFT_DIRS[platform], PATTERNS[platform]))
        ready = len(list((READY_DIR / platform).glob("*"))) if (READY_DIR / platform).exists() else 0
        published = len(list((PUBLISHED_DIR / platform).glob("*"))) if (PUBLISHED_DIR / platform).exists() else 0
        archive = len(list((ARCHIVE_DIR / platform).glob("*"))) if (ARCHIVE_DIR / platform).exists() else 0
        totals["drafts"] += drafts
        totals["ready"] += ready
        totals["published"] += published
        totals["archive"] += archive
        lines.append(
            f"{icons[platform]} {platform:<11} {drafts:>8} {ready:>8} {published:>10} {archive:>9}"
        )

    lines.append("─" * 50)
    lines.append(
        f"{'סה״כ':<14} {totals['drafts']:>8} {totals['ready']:>8} "
        f"{totals['published']:>10} {totals['archive']:>9}"
    )
    lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if not args or args[0] == "status":
        print(status())
    elif args[0] == "organize":
        result = organize_drafts()
        print("\n  ✅ הועברו ל-ready:")
        for p, n in result["moved"].items():
            if n:
                print(f"    {p}: {n}")
        print(status())
    elif args[0] == "publish":
        platform = args[1] if len(args) > 1 else None
        if not platform or platform not in PLATFORMS:
            print(f"שימוש: python file_organizer.py publish [{'/'.join(PLATFORMS)}]")
            sys.exit(1)
        result = mark_published(platform)
        if result:
            print(f"  ✅ סומן כפורסם: {result.name}")
        else:
            print(f"  ⚠️  אין קובץ ב-ready/{platform}")
    elif args[0] == "archive":
        days = int(args[1]) if len(args) > 1 and args[1].isdigit() else 30
        result = archive_old(days)
        total = sum(result.values())
        print(f"  📦 הועברו לארכיון ({days}+ יום): {total} קבצים")
        for p, n in result.items():
            if n:
                print(f"    {p}: {n}")
    else:
        print("פקודות: status / organize / publish [platform] / archive [days]")
