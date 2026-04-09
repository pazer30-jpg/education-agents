"""
Agent 3.5 — Human-in-the-Loop
נקודת עצירה בין Agent 3 (Content) ל-Agent 4 (Designer).

מציג את הטיוטה בטרמינל ומאפשר:
  a — אשר ועבור לעיצוב
  e — ערוך (פותח עורך טקסט)
  r — דחה ובקש מ-Agent 3 לכתוב מחדש
  s — דלג (שמור בלי אישור)
  q — בטל לגמרי

שומר החלטות ב-memory לשיפור עתידי.
"""

import os
import json
import subprocess
from pathlib import Path
from datetime import datetime

from config import LINKEDIN_DIR, BLOG_DIR, PODCAST_DIR, OUTPUT_DIR
from memory import load_memory, save_memory

REVIEW_LOG = OUTPUT_DIR / "review_log.json"

PLATFORM_DIRS = {
    "linkedin": LINKEDIN_DIR,
    "blog":     BLOG_DIR,
    "podcast":  PODCAST_DIR,
}


# ─────────────────────────────────────────────
# Review log
# ─────────────────────────────────────────────

def _load_review_log() -> list:
    if REVIEW_LOG.exists():
        try:
            return json.loads(REVIEW_LOG.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_review_log(log: list):
    REVIEW_LOG.write_text(
        json.dumps(log[-100:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _record_review(platform: str, file_path: Path,
                   decision: str, edit_notes: str = ""):
    log = _load_review_log()
    log.append({
        "time":     datetime.now().isoformat(),
        "platform": platform,
        "file":     file_path.name,
        "decision": decision,   # approved / edited / rejected / skipped
        "notes":    edit_notes,
    })
    _save_review_log(log)

    # גם לזיכרון — Agent 0 ישתמש בזה
    mem = load_memory()
    reviews = mem.get("content_reviews", [])
    reviews.append({
        "platform": platform,
        "decision": decision,
        "notes":    edit_notes,
        "time":     datetime.now().isoformat(),
    })
    mem["content_reviews"] = reviews[-50:]
    save_memory(mem)


# ─────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────

def _print_divider(title: str = ""):
    width = 60
    if title:
        pad = (width - len(title) - 2) // 2
        print(f"\n{'─'*pad} {title} {'─'*pad}")
    else:
        print("─" * width)


def _display_content(platform: str, content: str):
    labels = {
        "linkedin": "📱 LinkedIn Post",
        "blog":     "📝 Blog Article",
        "podcast":  "🎙️  Podcast Script",
    }
    _print_divider(labels.get(platform, platform))
    print()

    lines = content.split("\n")
    if len(lines) <= 120:
        print(content)
    else:
        print("\n".join(lines[:60]))
        print(f"\n  ... [{len(lines)-120} שורות נסתרות] ...\n")
        print("\n".join(lines[-60:]))

    _print_divider()
    print(f"  📏 {len(content):,} תווים | {len(content.split()):,} מילים | "
          f"{len(lines)} שורות")


def _open_in_editor(file_path: Path) -> bool:
    editors = [
        os.environ.get("EDITOR"),
        os.environ.get("VISUAL"),
        "code",
        "notepad",
        "nano",
        "vim",
        "vi",
    ]
    for editor in editors:
        if not editor:
            continue
        try:
            subprocess.run([editor, str(file_path)])
            return True
        except (FileNotFoundError, OSError):
            continue

    print(f"\n  ⚠️  לא נמצא עורך אוטומטי.")
    print(f"  ערוך ידנית: {file_path}")
    input("  לחץ Enter לאחר שסיימת לערוך...")
    return True


# ─────────────────────────────────────────────
# Main review function
# ─────────────────────────────────────────────

def review_content(
    platform: str,
    content_file: Path,
    auto_approve: bool = False,
) -> dict:
    """
    מציג טיוטה ומחכה להחלטת המשתמש.
    Returns: {"decision": "approved"|"edited"|"rejected"|"skipped"|"cancelled", "file": Path, "notes": str}
    """
    if auto_approve:
        return {"decision": "skipped", "file": content_file, "notes": "auto"}

    if not content_file.exists():
        return {"decision": "skipped", "file": content_file, "notes": "file not found"}

    content = content_file.read_text(encoding="utf-8", errors="replace")

    print(f"\n{'='*60}")
    print(f"  👁️  Human Review — {platform.upper()}")
    print(f"  קובץ: {content_file.name}")
    print(f"{'='*60}")

    _display_content(platform, content)

    while True:
        print("""
  מה תרצה לעשות?
    [a] אשר ועבור לעיצוב
    [e] ערוך בעורך טקסט
    [r] דחה — כתוב מחדש
    [s] דלג (שמור כמו שהוא)
    [q] בטל לגמרי
""")
        choice = input("  בחירה: ").strip().lower()

        if choice == "a":
            notes = input("  הערה (אופציונלי, Enter לדלג): ").strip()
            _record_review(platform, content_file, "approved", notes)
            print("  ✅ אושר!")
            return {"decision": "approved", "file": content_file, "notes": notes}

        elif choice == "e":
            print("  📝 פותח עורך...")
            _open_in_editor(content_file)
            new_content = content_file.read_text(encoding="utf-8", errors="replace")
            changed = new_content != content
            content = new_content
            if changed:
                print("  ✏️  הקובץ עודכן — מציג גרסה חדשה:")
                _display_content(platform, content)
            else:
                print("  ℹ️  לא זוהו שינויים")
            notes = input("  הערת עריכה (אופציונלי): ").strip()
            _record_review(platform, content_file, "edited", notes)
            return {"decision": "edited", "file": content_file, "notes": notes}

        elif choice == "r":
            notes = input("  מה לשפר בגרסה הבאה? ").strip()
            _record_review(platform, content_file, "rejected", notes)
            print("  🔄 נדחה — Agent 3 יכתוב מחדש עם ההנחיה שלך")
            return {"decision": "rejected", "file": content_file, "notes": notes}

        elif choice == "s":
            _record_review(platform, content_file, "skipped")
            print("  ⏭  נשמר בלי אישור")
            return {"decision": "skipped", "file": content_file, "notes": ""}

        elif choice == "q":
            print("  ❌ בוטל")
            return {"decision": "cancelled", "file": content_file, "notes": ""}

        else:
            print("  ❓ בחר a / e / r / s / q")


# ─────────────────────────────────────────────
# Review all platforms
# ─────────────────────────────────────────────

def review_all(
    content_types: list[str],
    auto_approve: bool = False,
) -> dict[str, dict]:
    """
    עובר על כל הפלטפורמות ומגיש לביקורת.
    Returns: {platform: review_result}
    """
    results = {}

    for platform in content_types:
        d = PLATFORM_DIRS.get(platform)
        if not d:
            continue
        files = sorted(d.glob("*"), key=lambda p: p.stat().st_mtime)
        if not files:
            print(f"  ⚠️  לא נמצא קובץ ל-{platform}")
            continue
        result = review_content(platform, files[-1], auto_approve)
        results[platform] = result

    return results


# ─────────────────────────────────────────────
# Review stats
# ─────────────────────────────────────────────

def print_review_stats():
    log = _load_review_log()
    if not log:
        print("אין היסטוריית ביקורות.")
        return

    from collections import Counter
    decisions = Counter(r["decision"] for r in log)
    platforms = Counter(r["platform"] for r in log)

    print(f"\n📊 סטטיסטיקות ביקורת ({len(log)} סה״כ):")
    for d, n in decisions.most_common():
        pct = n / len(log) * 100
        bar = "█" * int(pct / 5)
        print(f"  {d:<10} {n:>3}  {bar} {pct:.0f}%")
    print(f"\n  לפי פלטפורמה: "
          + " | ".join(f"{p}: {n}" for p, n in platforms.most_common()))
