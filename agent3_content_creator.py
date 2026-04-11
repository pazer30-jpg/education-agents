"""
Agent 3 - Content Creator
יוצר תוכן ממאמר אקדמי לפי בקשה:
  - פוסט LinkedIn (עברית + אנגלית)
  - מאמר בלוג
  - פרק פודקאסט (סקריפט)
  - כל שילוב של השלושה

מופעל ידנית או אחרי Agent 2. מפעיל Agent 4 אוטומטית.
"""

import json
import sys
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR, POSTS_DIR, LINKEDIN_DIR, BLOG_DIR, PODCAST_DIR
from claude_cli import ask_claude_json
from voice_profile import get_voice_prompt


# ─────────────────────────────────────────────
# Content type definitions
# ─────────────────────────────────────────────

CONTENT_TYPES = {
    "linkedin": "פוסט LinkedIn",
    "blog":     "מאמר בלוג",
    "podcast":  "פרק פודקאסט",
}


# ─────────────────────────────────────────────
# Article reader
# ─────────────────────────────────────────────

def _read_article(article_path: Path, max_chars: int = 8000) -> str:
    if article_path.suffix == ".md":
        text = article_path.read_text(encoding="utf-8")
    elif article_path.suffix == ".docx":
        from docx import Document
        doc = Document(str(article_path))
        text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    else:
        text = article_path.read_text(encoding="utf-8", errors="replace")

    # Smart truncation: intro + middle sample + conclusion (saves ~50% tokens)
    if len(text) > max_chars:
        lines = text.split("\n")
        # Find conclusion section
        concl_idx = next(
            (i for i, l in enumerate(lines)
             if any(w in l.lower() for w in
                    ["conclusion", "מסקנות", "discussion", "דיון", "summary"])),
            int(len(lines) * 0.75)
        )
        intro = "\n".join(lines[:50])              # ~1500 chars
        mid_start = len(lines) // 2
        middle = "\n".join(lines[mid_start:mid_start+30])  # ~900 chars
        concl = "\n".join(lines[concl_idx:concl_idx+40])   # ~1200 chars
        text = (intro + "\n\n[...ניתוח מרכזי מקוצר...]\n\n"
                + middle + "\n\n[...המשך...]\n\n" + concl)
    return text


# ─────────────────────────────────────────────
# System prompt builder
# ─────────────────────────────────────────────

def _build_system(content_types: list[str]) -> str:
    joined = " + ".join(CONTENT_TYPES[t] for t in content_types)
    primary = content_types[0]
    voice = get_voice_prompt(primary)

    # Load personal context + rejection rules + performance patterns
    from memory import get_context, get_published_titles, format_rules_for_prompt
    ctx = get_context()
    published = get_published_titles(primary)
    rej_rules = format_rules_for_prompt(primary)

    perf_patterns = ""
    try:
        from performance_log import get_patterns_for_prompt
        perf_patterns = get_patterns_for_prompt()
    except Exception:
        pass

    ctx_parts = []
    if ctx.get("season"):
        ctx_parts.append(f"עונה נוכחית: {ctx['season']}")
    if ctx.get("content_purpose"):
        ctx_parts.append(f"מטרת תוכן: {ctx['content_purpose']}")
    if ctx.get("open_questions"):
        ctx_parts.append("שאלות פתוחות: " + " | ".join(ctx["open_questions"][:3]))
    if ctx.get("current_tensions"):
        ctx_parts.append("מתחים: " + " | ".join(ctx["current_tensions"][:2]))
    if published:
        ctx_parts.append(f"כבר כוסה (אל תחזור): {', '.join(published[:5])}")
    if rej_rules:
        ctx_parts.append(rej_rules)
    if perf_patterns:
        ctx_parts.append(perf_patterns)

    context_block = ("\n\nהקשר אישי של פז:\n" + "\n".join(f"  • {p}" for p in ctx_parts) + "\n") if ctx_parts else ""

    base = f"""אתה כותב תוכן בשם פז שלמה — איש חינוך בלתי פורמלי.
המשימה שלך: לכתוב בדיוק בקולו, לא כמו בוט, לא כמו מאמר, כמו בן אדם שחי את הנושא.
{context_block}
{voice}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
המשימה הספציפית: {joined}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

עקרונות כלליים:
- שפה: עברית טבעית ונגישה, לא אקדמית
- גוון: מקצועי אך חם — כמו מומחה שמסביר לעמית
- מיקוד: תובנות מעשיות, לא סיכום מחקרי
"""

    if "linkedin" in content_types:
        base += """
─── LinkedIn ───────────────────────────────────────
- פתח ב-Hook עוצר-גלילה (שאלה / עובדה מפתיעה / תובנה אישית)
- מבנה: Hook → הקשר → תובנה מרכזית → משמעות מעשית → שאלה לדיון
- שורות קצרות + רווחים נדיבים
- ללא אימוג'ים. ללא רשימות ממוספרות. ללא "לסיכום".
- אורך: 1,200–1,600 תווים
- סיום בשאלה אמיתית שמישהו יכול לענות עליה מניסיון

מקורות בסוף הפוסט — חובה:
  📚 מקורות:
  • שם, ר. (שנה). כותרת קצרה. כתב עת.
  (2-4 מקורות בלבד — הכי רלוונטיים)
"""

    if "blog" in content_types:
        base += """
─── בלוג ───────────────────────────────────────────
- פתח עם זיכרון ספציפי מהשטח — לא הגדרה
- כותרות פנימיות בצורת שאלה (## למה זה לא עובד?)
- לפחות ציטוט אחד מהוגה + חיבור לשטח
- לפחות שתי דוגמאות קונקרטיות
- כל פסקה עד 4 שורות. ללא "לסיכום". ללא רשימות ממוספרות.
- אורך: 900–1,400 מילים
- סיום פתוח — שאלה שנשארת

ציטוטים inline + סעיף ## מקורות בסוף (4-6 מקורות APA 7):
  שם, ר. (שנה). כותרת. כתב עת, כרך(גיליון), עמודים. https://doi.org/...
"""

    if "podcast" in content_types:
        base += """
─── פודקאסט ────────────────────────────────────────
- כתוב בשפה דבורה, לא כתובה — משפטים עד 12 מילה
- פתיח 30 שניות: סיפור ספציפי מהשטח — לא הגדרה
- חלק ל-3-5 חלקים עם מעברים טבעיים
- כלול [הפסקה] אחרי טענות חשובות
- אורך משוער: 20-35 דקות
- מסיים עם מחשבה פתוחה — לא מסקנה

ציטוטים מדוברים בסקריפט: "לפי X שחקר את הנושא ב-Y..."
Show notes בסוף עם 4+ מקורות APA 7.
"""
    return base


# ─────────────────────────────────────────────
# Save functions
# ─────────────────────────────────────────────

def _save_linkedin(data: dict, base: str) -> list[Path]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    hashtag_str = " ".join(
        f"#{t}" if not t.startswith("#") else t
        for t in data.get("hashtags", [])
    )
    hooks = data.get("hooks", [])
    post_he = data.get("post_hebrew", "")
    post_en = data.get("post_english", "")

    full_path  = POSTS_DIR   / f"{base}_linkedin_{timestamp}.txt"
    ready_path = LINKEDIN_DIR / f"{base}_linkedin_ready.txt"

    full_content = f"""╔══════════════════════════════════════════════════════╗
║  LinkedIn Post — {datetime.now().strftime('%d/%m/%Y %H:%M')}
╚══════════════════════════════════════════════════════╝

── פוסט עברית ({len(post_he)} תווים) ──────────────
{post_he}

{hashtag_str}

── גרסה אנגלית ({len(post_en)} תווים) ──────────
{post_en}

── פתיחות חלופיות (Hooks) ──────────────────────────
{chr(10).join(f'{i+1}. {h}' for i, h in enumerate(hooks))}

── Hashtags ─────────────────────────────────────────
{hashtag_str}"""

    full_path.write_text(full_content.strip(), encoding="utf-8")
    ready_path.write_text(post_he + "\n\n" + hashtag_str, encoding="utf-8")
    print(f"  [Agent3] LinkedIn → {full_path.name}")
    return [full_path, ready_path]


def _save_blog(data: dict, base: str) -> list[Path]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    path = BLOG_DIR / f"{base}_blog_{timestamp}.md"

    title    = data.get("title", "")
    subtitle = data.get("subtitle", "")
    content  = data.get("content", "")
    tags_str = ", ".join(data.get("tags", []))
    meta     = data.get("meta_description", "")

    md = f"""---
title: "{title}"
subtitle: "{subtitle}"
date: {datetime.now().strftime('%Y-%m-%d')}
tags: [{tags_str}]
description: "{meta}"
---

# {title}
{"*" + subtitle + "*" if subtitle else ""}

{content}"""

    path.write_text(md.strip(), encoding="utf-8")
    print(f"  [Agent3] Blog → {path.name}")
    return [path]


def _save_podcast(data: dict, base: str) -> list[Path]:
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M")
    ep_num     = data.get("episode_number", 1)
    duration   = data.get("duration_minutes", "?")

    script_path = PODCAST_DIR / f"{base}_podcast_script_{timestamp}.md"
    notes_path  = PODCAST_DIR / f"{base}_podcast_shownotes_{timestamp}.txt"

    sections_text = ""
    for i, sec in enumerate(data.get("sections", []), 1):
        dur  = f" ({sec.get('duration', '')})" if sec.get("duration") else ""
        note = f"\n> הערת הפקה: {sec['notes']}" if sec.get("notes") else ""
        sections_text += f"\n\n## חלק {i}: {sec.get('title', '')}{dur}\n{note}\n\n{sec.get('script', '')}"

    script = f"""# פרק {ep_num}: {data.get('episode_title', '')}
**אורך משוער:** {duration} דקות
**תאריך:** {datetime.now().strftime('%d/%m/%Y')}

---

## פתיח (Hook) — ~30 שניות
{data.get('hook', '')}

---

## הקדמה — ~1-2 דקות
{data.get('intro', '')}

---
{sections_text}

---

## אאוטרו + קריאה לפעולה
{data.get('outro', '')}"""

    script_path.write_text(script.strip(), encoding="utf-8")
    notes_path.write_text(
        f"פרק {ep_num}: {data.get('episode_title', '')}\n\n{data.get('show_notes', '')}",
        encoding="utf-8"
    )
    print(f"  [Agent3] Podcast → {script_path.name}")
    return [script_path, notes_path]


# ─────────────────────────────────────────────
# Per-type creators
# ─────────────────────────────────────────────

def _create_linkedin(article_text: str, base: str, system: str,
                     ab_test: bool = False) -> list[Path]:
    print("  [Agent3] יוצר פוסט LinkedIn...")
    prompt = f"""בהתבסס על המאמר הזה, כתוב פוסט LinkedIn בקולו של פז שלמה.

{article_text}

צור JSON עם:
- post_hebrew: פוסט עברית 1,200-1,600 תווים בקול האישי המוגדר
  • פתח לפי הקשר — לא נוסחה קבועה
  • ללא אימוג'ים. ללא רשימות ממוספרות. ללא "לסיכום".
  • סיים בשאלה אמיתית שמישהו יכול לענות עליה מניסיון
- post_english: גרסה אנגלית 500-800 תווים, אותו קול
- hooks: מערך של 3 פתיחות חלופיות בעברית (כל אחת בסגנון אחר)
- hashtags: מערך של 12 hashtags בעברית + אנגלית (ללא #)"""

    if ab_test:
        prompt += """
- post_hebrew_b: גרסה שנייה (A/B) של הפוסט — אותו תוכן, סגנון שונה:
  • אם גרסה A פותחת בסיפור → B פותחת בשאלה
  • אם A פותחת בציטוט → B פותחת בהודאה אישית
  • אורך דומה, hashtags זהים"""

    prompt += "\n\nהחזר JSON בלבד."
    data = ask_claude_json(prompt, system=system, max_budget=1.5)
    paths = _save_linkedin(data, base)

    # Save B variant if exists
    if ab_test and data.get("post_hebrew_b"):
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%Y%m%d_%H%M")
        b_path = LINKEDIN_DIR / f"{base}_linkedin_B_{ts}.txt"
        hashtag_str = " ".join(f"#{t}" if not t.startswith("#") else t for t in data.get("hashtags", []))
        b_path.write_text(data["post_hebrew_b"] + "\n\n" + hashtag_str, encoding="utf-8")
        print(f"  [Agent3] LinkedIn B variant → {b_path.name}")
        paths.append(b_path)

    return paths


def _create_blog(article_text: str, base: str, system: str) -> list[Path]:
    print("  [Agent3] יוצר מאמר בלוג...")
    prompt = f"""בהתבסס על המאמר הזה, כתוב מאמר בלוג בקולו של פז שלמה.

{article_text}

צור JSON עם:
- title: כותרת שמציגה טענה — שאלה או הצהרה חדה
- subtitle: תת-כותרת מסבירה
- content: גוף המאמר ב-Markdown, 900-1,400 מילים:
  • פתח עם זיכרון ספציפי מהשטח
  • כותרות פנימיות בצורת שאלה (## למה זה לא עובד?)
  • לפחות ציטוט אחד מהוגה + חיבור לשטח
  • לפחות שתי דוגמאות קונקרטיות
  • כל פסקה עד 4 שורות
  • ללא "לסיכום". ללא רשימות ממוספרות.
  • סיום פתוח — שאלה שנשארת
- meta_description: תיאור SEO קצר (עד 150 תווים)
- tags: מערך של 5-7 תגיות

החזר JSON בלבד."""
    data = ask_claude_json(prompt, system=system, max_budget=2.0)
    return _save_blog(data, base)


def _create_podcast(article_text: str, base: str, system: str) -> list[Path]:
    print("  [Agent3] יוצר סקריפט פודקאסט...")
    prompt = f"""בהתבסס על המאמר הזה, כתוב סקריפט לפרק פודקאסט בקולו של פז שלמה.

{article_text}

צור JSON עם:
- episode_title: שם הפרק — שאלה או טענה חדה
- episode_number: 1
- duration_minutes: אורך משוער (מספר שלם)
- hook: פתיח 30 שניות — סיפור ספציפי מהשטח, לא תיאוריה
- intro: הקדמה 1-2 דקות — הצג את הנושא בשפה דבורה
- sections: מערך של 3-4 חלקים, כל אחד עם:
  - title, duration, script (שפה דבורה, משפטים עד 12 מילה, [הפסקה] אחרי טענות חשובות), notes
- outro: סיום + קריאה לפעולה (30-60 שניות)
- show_notes: תקציר 3-4 משפטים + 3 נקודות מפתח

החזר JSON בלבד."""
    data = ask_claude_json(prompt, system=system, max_budget=2.5)
    return _save_podcast(data, base)


# ─────────────────────────────────────────────
# Main agent function
# ─────────────────────────────────────────────

def run_content_creator(
    article_paths: dict[str, Path],
    content_types: list[str],
    extra_instruction: str = "",
    ab_test: bool = False,
) -> dict[str, list[Path]]:
    """
    content_types: any combo of "linkedin", "blog", "podcast"
    Returns dict mapping type → list of saved file paths.
    """
    valid = {"linkedin", "blog", "podcast"}
    content_types = [t for t in content_types if t in valid]
    if not content_types:
        raise ValueError(f"content_types must include at least one of: {valid}")

    type_display = " + ".join(CONTENT_TYPES[t] for t in content_types)
    print(f"\n{'='*60}")
    print(f"✨ Agent 3 - Content Creator | יוצר: {type_display}")
    print(f"{'='*60}\n")

    article_path = article_paths.get("md") or article_paths.get("docx")
    if not article_path or not Path(article_path).exists():
        raise ValueError("No article file. Run Agent 2 first.")

    article_text = _read_article(Path(article_path))
    if extra_instruction:
        article_text += f"\n\n━━━ הנחיה נוספת ━━━\n{extra_instruction}"
    base   = Path(article_path).stem
    system = _build_system(content_types)
    saved  = {}

    if "linkedin" in content_types:
        saved["linkedin"] = _create_linkedin(article_text, base, system, ab_test=ab_test)

    if "blog" in content_types:
        saved["blog"] = _create_blog(article_text, base, system)

    if "podcast" in content_types:
        saved["podcast"] = _create_podcast(article_text, base, system)

    print(f"\n✅ Agent 3 complete — {type_display}\n")

    # ── Auto-trigger Agent 4 ──────────────────
    if saved:
        try:
            from agent4_designer import run_designer
            _agent4_available = True
        except ImportError:
            _agent4_available = False

        if not _agent4_available:
            print("🎨 Agent 4 (Designer) לא מותקן — מדלג על עיצובים")
        else:
            print("🎨 מפעיל Agent 4 — יוצר תמונות...")
            try:
                design_type_map = {
                    "linkedin": "linkedin_cover",
                    "blog":     "blog_banner",
                    "podcast":  "podcast_cover",
                }
                design_types = [design_type_map[ct] for ct in saved]
                post_paths = {ct: [str(p) for p in paths] for ct, paths in saved.items()}
                designs = run_designer(
                    article_paths={k: Path(v) for k, v in article_paths.items() if v},
                    post_paths=post_paths,
                    design_types=design_types,
                )
                for platform, img_path in designs.items():
                    print(f"   {platform}: {img_path.name}")
            except Exception as e:
                print(f"   ⚠️  Agent 4 שגיאה בעיצוב: {e}")

    return saved


# ─────────────────────────────────────────────
# Standalone CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        mds = list(Path("output/articles").glob("*.md"))
        article_path = max(mds, key=lambda p: p.stat().st_mtime) if mds else None
        types = ["linkedin", "blog", "podcast"]
    else:
        article_path = Path(args[0])
        types = args[1:] if len(args) > 1 else ["linkedin"]

    if not article_path or not Path(article_path).exists():
        print("Usage: python agent3_content_creator.py <article.md> [linkedin] [blog] [podcast]")
        sys.exit(1)

    results = run_content_creator({"md": article_path}, types)
    for ct, paths in results.items():
        print(f"{CONTENT_TYPES[ct]}: {[str(p) for p in paths]}")
