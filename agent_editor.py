"""
agent_editor.py — עורך ומגיה
שני מצבים:

  mode="article"  (Agent 2.5)
    נקרא אחרי Agent 2, לפני Agent 3
    בודק: מבנה אקדמי, קוהרנטיות, ציטוטים, זרימה בין סעיפים
    מחזיר: גרסה מתוקנת + דוח שינויים

  mode="content"  (Agent 3.6)
    נקרא אחרי Agent 3, לפני Agent 3.5 (Human Review)
    בודק לכל פלטפורמה: שפה, קול של פז, ביטויים בוטיים, פלואו
    מחזיר: גרסה מתוקנת + רשימת שינויים

שני המצבים כותבים את השינויים בקובץ המקורי (in-place)
ויוצרים קובץ גיבוי .bak לפני כל עריכה.
"""

import shutil
from pathlib import Path
from datetime import datetime

from config import ARTICLES_DIR, LINKEDIN_DIR, BLOG_DIR, PODCAST_DIR
from claude_cli import ask_claude
from voice_profile import VOICE_PROFILE

try:
    from obsidian_memory import format_for_prompt as _obsidian_memory_for_prompt
except Exception:
    def _obsidian_memory_for_prompt(_names: list[str], **_kw) -> str:
        return ""


# ─────────────────────────────────────────────
# System prompts
# ─────────────────────────────────────────────

ARTICLE_EDITOR_SYSTEM = """אתה עורך אקדמי מקצועי המתמחה במאמרי חינוך.

תפקידך: לשפר את המאמר מבלי לשנות את הטענות המרכזיות.

בדוק ותקן:

מבנה:
  - האם יש מבוא, סקירת ספרות, מסגרת תיאורטית, דיון, מסקנות, ביבליוגרפיה?
  - האם כל סעיף מסתיים במשפט מעבר לסעיף הבא?
  - האם הכותרת מייצגת את תוכן המאמר?

ציטוטים:
  - פורמט APA עקבי: (Author, Year) לכל ציטוט
  - ציטוטים ישירים מופיעים במירכאות
  - כל מקור בטקסט מופיע בביבליוגרפיה

שפה ואקדמיות:
  - מחק משפטים שמתחילים ב"חשוב לציין", "מעניין לראות", "נראה כי"
  - החלף פסיביות מיותרת בפעילות
  - ודא עקביות מינוח לאורך המאמר
  - מינימום 2,000 מילים — אם קצר מדי, הרחב את הדיון

נגד דפוסי AI (Wikipedia: Signs of AI writing):
  - הימנע מהמילים: מהווה, ממלא תפקיד מרכזי, משמעותי, עדות לחשיבות,
    בעידן הנוכחי, בנוף המשתנה, ניווט מטאפורי, עשיר, תוסס, חיוני, מכריע
  - הימנע ממבנים: "לא רק X אלא Y", "מ-X עד Y" (כשלא ציר), שלשות,
    "במילים אחרות", "לסיכום", שאלות רטוריות פתיחה, רשימות `Bold: הסבר`
  - העדף is/has: "המכון הוא X" — לא "המכון משמש בתור X"
  - קצב מעורב: שילוב משפטים קצרים וארוכים, לא אורך אחיד
  - קונקרטי: "20% מהמורים" — לא "הרבה מורים"; שם + שנה — לא "מחקרים מראים"
  - דעה ועמדה: לא רק עובדות — תגובה אמיתית
  - קו מפריד (—): מקסימום 2 במסמך
  - emoji בכותרות וב-bullets: אסור

קוהרנטיות:
  - האם הארגומנט המרכזי ברור מהמבוא עד המסקנות?
  - האם הסינתזה בין הנושאים השונים ברורה?

החזר את המאמר המתוקן במלואו ב-Markdown.
אל תוסיף הסברים — רק את הטקסט המתוקן.
"""

CONTENT_EDITOR_SYSTEM = f"""אתה עורך תוכן המתמחה בהנגשת ידע חינוכי לרשתות חברתיות.

הקול הנדרש:
{VOICE_PROFILE}

תפקידך: לעבור על הטקסט ולשפר — לא לכתוב מחדש.

בדוק ותקן:

ביטויים בוטיים שחייבים להיעלם:
  "חשוב לציין", "מעניין לראות", "נראה כי", "לסיכום,"
  "כפי שניתן לראות", "יש לציין", "ראוי לציין"
  "בסיכומו של דבר", "לאור האמור לעיל"

בדיקת קול (פז):
  - האם יש לפחות משפט אחד בגוף ראשון?
  - האם יש לפחות דוגמה אחת מהשטח (מחינה/כפר נוער/אקדמיית וינגייט)?
  - האם יש שאלה שפז באמת לא יודע תשובה לה?
  - האם יש מתח / ניגוד מעניין?

לפי פלטפורמה:

  LinkedIn:
    - שורה ראשונה: עד 80 תווים, חדה, מושכת
    - אין פסקה שעולה על 3 שורות
    - סיום בשאלה פתוחה (לא "מה דעתכם?" — שאלה ספציפית)
    - 8-12 hashtags בסוף בלבד

  Blog:
    - כל כותרת משנה: שאלה (לא הצהרה)
    - כל פסקה: עד 5 שורות
    - לפחות 2 דוגמאות מהשטח

  Podcast:
    - כל משפט: עד 15 מילים
    - [הפסקה] אחרי כל טענה חזקה
    - פתיחה בסיפור ספציפי (לא הגדרה, לא נתון)

החזר את הטקסט המתוקן בלבד.
לפני הטקסט, הוסף שורה: CHANGES: <רשימת השינויים הקצרה>
"""


# ─────────────────────────────────────────────
# Core edit function
# ─────────────────────────────────────────────

def _edit_text(text: str, system_prompt: str,
               extra_instruction: str = "") -> tuple[str, list[str]]:
    """
    שולח טקסט לקלוד לעריכה.
    Returns: (edited_text, changes_list)
    """
    # Editor-specific Obsidian memory: voice + learned corrections + humanization
    memory_block = _obsidian_memory_for_prompt(
        ["voice_rules", "editor_corrections", "humanize_rules"],
        max_chars_per_note=1400,
    )
    if memory_block:
        system_prompt = system_prompt + "\n\n" + memory_block

    user_msg = text
    if extra_instruction:
        user_msg = f"הנחיה נוספת: {extra_instruction}\n\n---\n\n{text}"

    result = ask_claude(user_msg, system=system_prompt, max_budget=3.0)

    # חלץ CHANGES אם קיים
    changes = []
    if result.startswith("CHANGES:"):
        lines        = result.split("\n", 2)
        changes_line = lines[0].replace("CHANGES:", "").strip()
        changes      = [c.strip() for c in changes_line.split("|") if c.strip()]
        result       = lines[2].strip() if len(lines) > 2 else lines[-1].strip()

    return result, changes


def _verify_citations(text: str) -> list[str]:
    """Cross-check in-text citations vs References list. Returns issues."""
    import re as _re
    issues = []

    # Extract in-text citations: (Author, Year) or (Author et al., Year)
    in_text = set()
    for m in _re.finditer(r'\(([A-Z][a-zà-ÿ]+(?:\s+(?:et al\.|&\s+[A-Z][a-zà-ÿ]+))*),\s*(\d{4})', text):
        in_text.add((m.group(1).strip(), m.group(2)))
    # Narrative: Author (Year)
    for m in _re.finditer(r'([A-Z][a-zà-ÿ]+(?:\s+et al\.)?)\s*\((\d{4})\)', text):
        in_text.add((m.group(1).strip(), m.group(2)))

    # Extract References entries
    ref_section = ""
    ref_match = _re.search(r'##\s*References?\s*\n([\s\S]+?)(?=\n##|\Z)', text)
    if ref_match:
        ref_section = ref_match.group(1)

    ref_entries = set()
    for line in ref_section.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _re.match(r'([A-Z][a-zà-ÿ]+).*?\((\d{4})\)', line)
        if m:
            ref_entries.add((m.group(1), m.group(2)))

    # Find orphan citations (in text but not in References)
    for author, year in in_text:
        first_author = author.split(" et al")[0].split(" & ")[0].strip()
        if not any(first_author in r[0] for r in ref_entries):
            issues.append(f"ציטוט ללא מקור: ({author}, {year})")

    # Find unused references
    for author, year in ref_entries:
        if not any(author in c[0] for c in in_text):
            issues.append(f"מקור לא מצוטט: {author} ({year})")

    return issues[:10]


def _backup_and_write(file_path: Path, new_content: str) -> Path:
    """גיבוי + כתיבה."""
    bak = file_path.with_suffix(file_path.suffix + ".bak")
    shutil.copy2(file_path, bak)
    file_path.write_text(new_content, encoding="utf-8")
    return bak


# ─────────────────────────────────────────────
# Article editor (Agent 2.5)
# ─────────────────────────────────────────────

def edit_article(article_paths: dict[str, Path],
                 extra: str = "") -> dict:
    """
    עורך את המאמר האקדמי in-place.
    Returns: {"paths": article_paths, "changes": [...], "backup": Path}
    """
    md_path = article_paths.get("md")
    if not md_path or not Path(md_path).exists():
        print("  ⚠️  [Editor] קובץ מאמר לא נמצא — דולג")
        return {"paths": article_paths, "changes": [], "backup": None}

    md_path = Path(md_path)
    print(f"\n  ✏️  [Agent 2.5 — Editor] {md_path.name}")

    original = md_path.read_text(encoding="utf-8", errors="replace")
    words_before = len(original.split())

    edited, changes = _edit_text(original, ARTICLE_EDITOR_SYSTEM, extra)

    if not edited or len(edited) < 200:
        print("  ⚠️  עריכה ריקה — שומר מקור")
        return {"paths": article_paths, "changes": [], "backup": None}

    # ── Self-audit loop (anti-AI-tells, opt-in via MOKI_DEEP_HUMANIZE=1) ──
    # Pattern: Wikipedia "Signs of AI writing" — pass 1 audits, pass 2 fixes.
    # Adds 2 Claude calls but produces noticeably less AI-tells output.
    import os as _os
    if _os.environ.get("MOKI_DEEP_HUMANIZE", "0") == "1":
        try:
            from claude_cli import ask_claude
            audit_prompt = (
                "קרא את המאמר הבא ותגיד בקיצור: מה במאמר הזה עדיין נשמע AI?\n"
                "תן 3-5 bullets ספציפיים בלבד (ציטוט קצר + הסיבה). אל תתקן — רק תזהה.\n\n"
                f"---\n{edited[:8000]}\n---"
            )
            audit = ask_claude(audit_prompt, system="אתה מבקר עריכה. ענה קצר בעברית.",
                               max_budget=0.5, timeout=120)
            if audit and len(audit.strip()) > 20:
                print(f"  🔍 self-audit:")
                for ln in audit.strip().splitlines()[:8]:
                    if ln.strip():
                        print(f"     {ln.strip()[:120]}")
                fix_prompt = (
                    f"זה ה-audit שזיהית — תקן בדיוק את הפריטים האלה ותחזיר את כל המאמר המתוקן:\n\n"
                    f"{audit.strip()}\n\n"
                    f"כללי: שמור על אורך וטענות. שנה רק את הפרטים שב-audit. החזר Markdown מלא."
                )
                fixed, _ = _edit_text(edited, ARTICLE_EDITOR_SYSTEM, fix_prompt)
                if fixed and len(fixed) > len(edited) * 0.7:
                    edited = fixed
                    print(f"  ✅ self-audit fixes applied")
        except Exception as e:
            print(f"  ⚠️  self-audit skipped: {e}")
    else:
        print(f"  ⏭  self-audit skipped (opt-in: MOKI_DEEP_HUMANIZE=1)")

    words_after = len(edited.split())
    bak = _backup_and_write(md_path, edited)

    # עדכן גם DOCX אם קיים
    docx_path = article_paths.get("docx")
    if docx_path and Path(docx_path).exists():
        try:
            from agent2_writer import _markdown_to_docx
            title = edited.split("\n")[0].lstrip("# ").strip()
            _markdown_to_docx(edited, title, Path(docx_path))
        except Exception as e:
            print(f"  ⚠️  לא עדכן DOCX: {e}")

    # Citation verification
    cit_issues = _verify_citations(edited)
    if cit_issues:
        print(f"  📎 בעיות ציטוט ({len(cit_issues)}):")
        for ci in cit_issues[:5]:
            print(f"     • {ci}")
        # Auto-fix: ask Claude to resolve citation mismatches
        fix_prompt = "תקן את בעיות הציטוט הבאות:\n" + "\n".join(f"- {ci}" for ci in cit_issues)
        try:
            fixed, _ = _edit_text(edited, ARTICLE_EDITOR_SYSTEM, fix_prompt)
            if fixed and len(fixed) > len(edited) * 0.7:
                edited = fixed
                _backup_and_write(md_path, edited)
                remaining = _verify_citations(edited)
                print(f"  📎 תוקנו — {len(cit_issues) - len(remaining)} בעיות נפתרו")
        except Exception:
            pass

    # דוח
    delta = words_after - words_before
    delta_str = f"+{delta}" if delta > 0 else str(delta)
    print(f"  ✅ מאמר נערך: {words_before} → {words_after} מילים ({delta_str})")
    if changes:
        for c in changes[:5]:
            print(f"     • {c}")

    return {"paths": article_paths, "changes": changes, "backup": bak}


# ─────────────────────────────────────────────
# Content editor (Agent 3.6)
# ─────────────────────────────────────────────

PLATFORM_DIRS = {
    "linkedin": LINKEDIN_DIR,
    "blog":     BLOG_DIR,
    "podcast":  PODCAST_DIR,
}

PLATFORM_SUFFIXES = {
    "linkedin": ["*_linkedin_ready.txt", "*_linkedin_*.txt"],
    "blog":     ["*_blog_*.md"],
    "podcast":  ["*_podcast_script_*.md"],
}


def _find_latest(platform: str) -> Path | None:
    d = PLATFORM_DIRS.get(platform)
    if not d:
        return None
    for pattern in PLATFORM_SUFFIXES.get(platform, [f"*_{platform}_*"]):
        files = sorted(d.glob(pattern), key=lambda p: p.stat().st_mtime)
        if files:
            return files[-1]
    return None


def edit_content(platform: str,
                 content_file: Path | None = None,
                 extra: str = "") -> dict:
    """
    עורך פיס תוכן in-place לפי פלטפורמה.
    Returns: {"file": Path, "changes": [...], "backup": Path}
    """
    file_path = content_file or _find_latest(platform)
    if not file_path or not file_path.exists():
        print(f"  ⚠️  [Editor] לא נמצא קובץ ל-{platform}")
        return {"file": None, "changes": [], "backup": None}

    print(f"\n  ✏️  [Agent 3.6 — Editor] {platform} | {file_path.name}")

    original = file_path.read_text(encoding="utf-8", errors="replace")
    chars_before = len(original)

    # הנחיית פלטפורמה ספציפית
    platform_extra = {
        "linkedin": "זכור: שורה ראשונה חדה, אין פסקאות ארוכות, שאלה ספציפית בסוף",
        "blog":     "זכור: כותרות כשאלות, דוגמאות מהשטח, פסקאות קצרות",
        "podcast":  "זכור: שפה דבורה, [הפסקה] אחרי טענות, פתיחה בסיפור",
    }.get(platform, "")

    full_extra = " | ".join(filter(None, [platform_extra, extra]))

    system = CONTENT_EDITOR_SYSTEM + f"\n\nפלטפורמה: {platform.upper()}"
    edited, changes = _edit_text(original, system, full_extra)

    if not edited or len(edited) < 50:
        print(f"  ⚠️  עריכה ריקה — שומר מקור")
        return {"file": file_path, "changes": [], "backup": None}

    bak = _backup_and_write(file_path, edited)
    chars_after = len(edited)
    delta = chars_after - chars_before
    delta_str = f"+{delta}" if delta > 0 else str(delta)

    print(f"  ✅ {platform} נערך: {chars_before:,} → {chars_after:,} תווים ({delta_str})")
    if changes:
        for c in changes[:5]:
            print(f"     • {c}")

    return {"file": file_path, "changes": changes, "backup": bak}


def edit_all_content(content_types: list[str],
                     extra: str = "") -> dict[str, dict]:
    """
    עורך כל הפלטפורמות — batch בקריאה אחת כשיש 2+ פלטפורמות.
    חוסך ~66% בטוקנים.
    """
    import re

    # איסוף קבצים
    files = {}
    for platform in content_types:
        fp = _find_latest(platform)
        if fp and fp.exists():
            files[platform] = fp

    if not files:
        return {}

    if len(files) == 1:
        platform = list(files.keys())[0]
        return {platform: edit_content(platform, extra=extra)}

    # ── Skip batch for podcast — too long, causes timeouts ──
    has_podcast = "podcast" in files
    if has_podcast and len(files) > 1:
        print(f"\n  ✏️  [Agent 3.6 — Editor] sequential (podcast detected): {', '.join(files.keys())}")
        results = {}
        for platform in files:
            try:
                results[platform] = edit_content(platform, extra=extra)
            except Exception as e:
                print(f"  ⚠️  {platform} edit failed: {e}")
                results[platform] = {"file": files[platform], "changes": [], "backup": None}
        return results

    # ── Batch for shorter platforms only (linkedin + blog) ──
    print(f"\n  ✏️  [Agent 3.6 — Editor] batch: {', '.join(files.keys())}")

    sections = []
    originals = {}
    for platform, fp in files.items():
        text = fp.read_text(encoding="utf-8", errors="replace")
        originals[platform] = text
        sections.append(f"=== {platform.upper()} ===\n{text[:3000]}")

    combined = "\n\n".join(sections)
    extra_note = f"\nהנחיה נוספת: {extra}" if extra else ""

    system = CONTENT_EDITOR_SYSTEM + f"""

ערוך כל פלטפורמה בנפרד.
פורמט החזרה — חייב להיות בדיוק:
=== LINKEDIN_EDITED ===
[טקסט מתוקן]
=== BLOG_EDITED ===
[טקסט מתוקן]

כלול רק את הפלטפורמות שהתקבלו.
{extra_note}"""

    try:
        result_text = ask_claude(combined, system=system, max_budget=4.0, timeout=1200)
    except Exception as e:
        print(f"  ⚠️  Batch edit failed ({e}) — falling back to sequential")
        results = {}
        for platform in files:
            try:
                results[platform] = edit_content(platform, extra=extra)
            except Exception as e2:
                print(f"  ⚠️  {platform} edit failed: {e2}")
                results[platform] = {"file": files[platform], "changes": [], "backup": None}
        return results

    # פרסר
    results = {}
    for platform, fp in files.items():
        marker = f"{platform.upper()}_EDITED"
        pattern = rf"=== {marker} ===\n([\s\S]+?)(?====|$)"
        m = re.search(pattern, result_text)
        if m:
            edited = m.group(1).strip()
            if len(edited) > 50:
                bak = _backup_and_write(fp, edited)
                print(f"  ✅ {platform}: {len(edited):,} תווים")
                results[platform] = {"file": fp, "changes": [], "backup": bak}
            else:
                print(f"  ⚠️  {platform}: עריכה קצרה מדי — שומר מקור")
                results[platform] = {"file": fp, "changes": [], "backup": None}
        else:
            # fallback — קריאה נפרדת
            results[platform] = edit_content(platform, extra=extra)

    return results


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="עורך ומגיה")
    parser.add_argument("mode", choices=["article", "linkedin", "blog", "podcast", "all"],
                        help="מה לערוך")
    parser.add_argument("--file", help="נתיב לקובץ ספציפי")
    parser.add_argument("--note", default="", help="הנחיה נוספת לעורך")
    args = parser.parse_args()

    if args.mode == "article":
        if args.file:
            p = Path(args.file)
        else:
            mds = sorted(ARTICLES_DIR.glob("*.md"), key=lambda x: x.stat().st_mtime)
            p   = mds[-1] if mds else None
        if p:
            edit_article({"md": p, "docx": p.with_suffix(".docx")}, args.note)

    elif args.mode == "all":
        for platform in ["linkedin", "blog", "podcast"]:
            edit_content(platform, extra=args.note)

    else:
        fp = Path(args.file) if args.file else None
        edit_content(args.mode, fp, args.note)
