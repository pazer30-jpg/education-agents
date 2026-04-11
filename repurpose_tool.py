"""
repurpose_tool.py — Repurposing תוכן קיים
מקבל קובץ קיים ומתאים אותו לפלטפורמה אחרת.
חוסך ~$8 לריצה מול כתיבה מחדש.

אפשרויות:
  בלוג    → LinkedIn post (hook + תובנות + שאלה)
  בלוג    → Podcast script (נרטיב מדובר)
  LinkedIn → Blog post (הרחבה + מקורות)
  פודקאסט → LinkedIn (3 takeaways)
  פודקאסט → Blog (תמלול + עיצוב)

Usage:
  python repurpose_tool.py --from blog/post.md --to linkedin
  python repurpose_tool.py --from linkedin/post.txt --to blog
  python repurpose_tool.py --latest blog --to linkedin
  python repurpose_tool.py --list
"""

import argparse
from pathlib import Path
from datetime import datetime

from config import LINKEDIN_DIR, BLOG_DIR, PODCAST_DIR, OUTPUT_DIR
from voice_profile import get_voice_prompt
from claude_cli import ask_claude

PLATFORM_DIRS = {
    "linkedin": LINKEDIN_DIR,
    "blog":     BLOG_DIR,
    "podcast":  PODCAST_DIR,
}


# ─────────────────────────────────────────────
# Conversion prompts
# ─────────────────────────────────────────────

def _repurpose_prompt(source_platform: str, target_platform: str,
                      content: str, voice: str, patterns: str) -> str:
    """בונה prompt מותאם לכיוון ההמרה."""

    base = f"""{voice}

{patterns}

מקור: {source_platform.upper()}
יעד:  {target_platform.upper()}

תוכן המקור:
───────────────────────────────────
{content[:3500]}
───────────────────────────────────

"""

    instructions = {
        ("blog", "linkedin"): """
חלץ מהבלוג:
  1. הרגע החזק ביותר — עשה ממנו Hook
  2. 2-3 תובנות מרכזיות — כל אחת שורה-שתיים
  3. שאלה פתוחה שנובעת מהנושא

כתוב LinkedIn post (1200-1800 תווים):
  - Hook עוצר בשורה ראשונה
  - מבנה: Hook → תובנה 1 → תובנה 2 → תובנה 3 → שאלה
  - שורות קצרות, רווחים
  - 📚 מקורות בסוף (2-4 מהבלוג)
  - 8-12 hashtags

אל תכתוב מחדש — תסנן ותצמצם את הטוב ביותר.
""",
        ("blog", "podcast"): """
המר את הבלוג לסקריפט פודקאסט מדובר:
  - פתיח: סיפור או שאלה רטורית (לא "היום נדבר על...")
  - חלק כל טענה למשפטים קצרים (עד 15 מילים)
  - הוסף [הפסקה] אחרי כל טענה חשובה
  - הפוך כל כותרת משנה לשאלה מדוברת
  - הוסף דוגמאות מהשטח שנובעות מהבלוג
  - אורך: 20-25 דקות (2000-2500 מילים)
  - Show notes בסוף עם מקורות
""",
        ("linkedin", "blog"): """
הרחב את הפוסט לבלוג מלא:
  - שמור את ה-Hook כמבוא
  - כל תובנה מהפוסט → סעיף שלם עם כותרת כשאלה
  - הוסף לכל סעיף: דוגמה מהשטח + הסבר
  - הוסף ציטוטים אקדמיים רלוונטיים (Author, Year)
  - אורך: 900-1400 מילים
  - ## מקורות בסוף (APA 7)
  - Markdown format
""",
        ("podcast", "linkedin"): """
חלץ מהפרק 3 takeaways חזקים:
  Hook = הרגע הכי מפתיע בפרק
  3 שורות — כל אחת תובנה אחת
  שאלה שנשארת פתוחה

LinkedIn post (800-1200 תווים):
  - קצר יותר מרגיל — תמצית
  - "האזנתם לפרק X? הנה מה שנשאר אצלי..."
  - קישור לפרק
  - 6-10 hashtags
""",
        ("podcast", "blog"): """
המר את הסקריפט לפוסט בלוג:
  - כותרת חדשה (לא "פרק X")
  - כל חלק מהפרק → סעיף עם כותרת כשאלה
  - הסר סימוני הפקה ([הפסקה] וכו')
  - הפוך שפה מדוברת לכתובה
  - שמור ציטוטים ומקורות
  - אורך: 1000-1400 מילים
""",
    }

    key = (source_platform, target_platform)
    instruction = instructions.get(key, f"""
המר את התוכן מ-{source_platform} ל-{target_platform}.
שמור על קול פז, הוסף מקורות כנדרש, התאם לפלטפורמה היעד.
""")

    return base + instruction


# ─────────────────────────────────────────────
# Detect source platform from file
# ─────────────────────────────────────────────

def _detect_platform(file_path: Path) -> str:
    path_str = str(file_path).lower()
    if "linkedin" in path_str: return "linkedin"
    if "blog"     in path_str: return "blog"
    if "podcast"  in path_str: return "podcast"
    if file_path.suffix == ".txt": return "linkedin"
    return "blog"


def _find_latest(platform: str) -> Path | None:
    d = PLATFORM_DIRS.get(platform)
    if not d:
        return None
    files = sorted(d.glob("*"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


# ─────────────────────────────────────────────
# Core repurpose function
# ─────────────────────────────────────────────

def repurpose(
    source_path: Path,
    target_platform: str,
    extra_note: str = "",
) -> Path:
    """מתאים קובץ קיים לפלטפורמה יעד. מחזיר Path לקובץ החדש."""
    if not source_path.exists():
        raise FileNotFoundError(f"קובץ מקור לא נמצא: {source_path}")

    source_platform = _detect_platform(source_path)
    content         = source_path.read_text(encoding="utf-8", errors="replace")

    print(f"\n  🔄 Repurposing: {source_platform} → {target_platform}")
    print(f"     מקור: {source_path.name} ({len(content):,} תווים)")

    voice = get_voice_prompt(target_platform)

    try:
        from performance_log import get_patterns_for_prompt
        patterns = get_patterns_for_prompt()
    except Exception:
        patterns = ""

    prompt = _repurpose_prompt(
        source_platform, target_platform, content, voice, patterns
    )

    if extra_note:
        prompt += f"\n\nהנחיה נוספת: {extra_note}"

    result = ask_claude(prompt, max_budget=1.2).strip()

    target_dir = PLATFORM_DIRS.get(target_platform, OUTPUT_DIR / "posts")
    target_dir.mkdir(parents=True, exist_ok=True)
    ts       = datetime.now().strftime("%Y%m%d_%H%M")
    stem     = source_path.stem[:30]
    ext      = ".md" if target_platform in ("blog", "podcast") else ".txt"
    out_path = target_dir / f"{stem}_repurposed_{ts}{ext}"
    out_path.write_text(result, encoding="utf-8")

    print(f"  ✅ שמור: {out_path.name} ({len(result):,} תווים)")
    return out_path


def repurpose_all(source_path: Path, targets: list[str]) -> dict[str, Path]:
    """ממיר קובץ אחד לכמה פלטפורמות."""
    source_platform = _detect_platform(source_path)
    results = {}
    for t in targets:
        if t == source_platform:
            continue
        try:
            results[t] = repurpose(source_path, t)
        except Exception as e:
            print(f"  ⚠️  {t}: {e}")
    return results


# ─────────────────────────────────────────────
# List available content
# ─────────────────────────────────────────────

def list_available():
    print("\n  📁 תוכן זמין לrepurposing:\n")
    for platform, d in PLATFORM_DIRS.items():
        if not d.exists():
            continue
        files = sorted(d.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
        if files:
            print(f"  {platform}:")
            for f in files:
                print(f"    {f.name}")
    print()


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Repurposing תוכן קיים",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
דוגמאות:
  python repurpose_tool.py --from blog/post.md --to linkedin
  python repurpose_tool.py --from linkedin/post.txt --to blog podcast
  python repurpose_tool.py --latest blog --to linkedin
  python repurpose_tool.py --list
        """,
    )
    parser.add_argument("--from",   dest="source", help="קובץ מקור")
    parser.add_argument("--to",     nargs="+",
                        choices=["linkedin","blog","podcast"],
                        help="פלטפורמת יעד (אפשר כמה)")
    parser.add_argument("--latest", choices=["linkedin","blog","podcast"],
                        help="השתמש בקובץ האחרון מהפלטפורמה")
    parser.add_argument("--note",   default="", help="הנחיה נוספת")
    parser.add_argument("--list",   action="store_true", help="הצג קבצים זמינים")
    args = parser.parse_args()

    if args.list:
        list_available()

    elif args.to:
        if args.latest:
            source = _find_latest(args.latest)
            if not source:
                print(f"  לא נמצאו קבצים ב-{args.latest}")
                import sys; sys.exit(1)
        elif args.source:
            source = Path(args.source)
            if not source.exists():
                for d in PLATFORM_DIRS.values():
                    candidate = d / args.source
                    if candidate.exists():
                        source = candidate
                        break
        else:
            print("  דרוש --from או --latest")
            import sys; sys.exit(1)

        targets = args.to
        if len(targets) == 1:
            repurpose(source, targets[0], args.note)
        else:
            repurpose_all(source, targets)
    else:
        parser.print_help()
