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
from claude_cli import ask_claude_json, ask_claude
from voice_profile import get_voice_prompt, format_examples_for_prompt
from genre_router import detect_genre, format_genre_for_prompt
from counter_argument import suggest_counter, format_counter_report


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

    # Anti-pattern memory — patterns that failed 2+ times before
    anti_patterns_block = ""
    try:
        from anti_patterns import format_for_prompt as _ap_for_prompt
        anti_patterns_block = _ap_for_prompt()
    except Exception:
        pass

    # Active alerts — observability SLO breaches that should change behavior
    active_alerts_block = ""
    try:
        from active_response import adjustments_for_agent3
        adj = adjustments_for_agent3()
        if adj.get("prompt_inject"):
            active_alerts_block = "━━━ ACTIVE ALERTS (from observability) ━━━\n" + adj["prompt_inject"]
    except Exception:
        pass

    # Obsidian memory — voice rules, recurring sources, theoretical anchors
    obsidian_block = ""
    try:
        from obsidian_memory import format_for_prompt as _obs_for_prompt
        obsidian_block = _obs_for_prompt(
            ["voice_rules", "recurring_sources", "theoretical_anchors",
             "engagement", "performance_patterns"],
            max_chars_per_note=1000,
        )
    except Exception:
        pass

    # Scratchpad — QA retry hints + cross-agent warnings
    scratchpad_block = ""
    try:
        from scratchpad import format_for_agent as _scratch_for
        scratchpad_block = _scratch_for("content")
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
    if anti_patterns_block:
        ctx_parts.append(anti_patterns_block)
    if active_alerts_block:
        ctx_parts.append(active_alerts_block)
    if obsidian_block:
        ctx_parts.append(obsidian_block)
    if scratchpad_block:
        ctx_parts.append(scratchpad_block)

    # Calendar awareness — upcoming events
    try:
        from agent0_planner import _get_upcoming_events
        events = _get_upcoming_events(14)
        if events:
            ev_str = " | ".join(f"{e['name']} ({e['days_until']}d)" for e in events[:2])
            ctx_parts.append(f"אירועים קרובים: {ev_str} — אם רלוונטי, חבר לנושא")
    except Exception:
        pass

    # Content calendar — what was published recently per platform
    from datetime import datetime, timedelta
    recent_cutoff = (datetime.now() - timedelta(days=3)).strftime("%Y%m%d")
    for ct in content_types:
        from config import OUTPUT_DIR
        ready_dir = OUTPUT_DIR / "ready" / ct
        if ready_dir.exists():
            recent = [f for f in ready_dir.iterdir()
                      if f.stat().st_mtime > (datetime.now() - timedelta(days=3)).timestamp()]
            if recent:
                ctx_parts.append(f"⚠️ {ct}: {len(recent)} פוסטים ב-3 ימים אחרונים — גוון את הזווית")

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
- סיום: גוון בין סוגי סגירה (לא תמיד שאלה!):
  a. שאלה ספציפית שמישהו יכול לענות מניסיון
  b. הצהרה מתגרה ("ואולי הגיע הזמן לעצור ולשאול...")
  c. הזמנה לפעולה ("הפעם הבאה שאתם ב-X, נסו...")
  d. מתח פתוח ("אני עדיין לא יודע את התשובה. אבל...")

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

def _ensure_sources(text: str, data: dict) -> str:
    """If text has no sources section, extract inline citations and append them."""
    source_markers = ["מקורות:", "📚", "## מקורות", "References:", "sources:"]
    if any(m in text for m in source_markers):
        return text  # already has sources

    # Extract (Author, Year) patterns from text
    import re
    # Hebrew names with geresh: פורג'ס, אימורדינו-יאנג
    citations = re.findall(r"[\u0590-\u05FF'\w-]+(?:\s+[\u0590-\u05FF'\w-]+){0,2}\s*\(\d{4}\)", text)
    # English: Author (Year) or Author et al. (Year)
    eng_cites = re.findall(r'[A-Z][a-z]+(?:\s+(?:et\s+al\.|&\s+[A-Z][a-z]+))?\s*\(\d{4}\)', text)
    all_cites = list(dict.fromkeys(citations + eng_cites))  # dedupe, preserve order

    if not all_cites:
        return text

    sources_block = "\n\n📚 מקורות:\n" + "\n".join(f"• {c.strip()}" for c in all_cites[:6])
    return text.rstrip() + sources_block


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
    ready_path = LINKEDIN_DIR / f"{base}_linkedin_ready_{timestamp}.txt"

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
    post_he_with_sources = _ensure_sources(post_he, data)
    ready_path.write_text(post_he_with_sources + "\n\n" + hashtag_str, encoding="utf-8")
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

    md = _ensure_sources(md.strip(), data)
    path.write_text(md, encoding="utf-8")
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
- post_hebrew: פוסט עברית בין 1,200 ל-1,600 תווים בלבד (חובה! ספור תווים!)
  זה בערך 12-16 שורות. אם הפוסט ארוך יותר — קצר אותו. שום פוסט מעל 1,600 תווים.
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
    data = ask_claude_json(prompt, system=system, max_budget=1.5, timeout=240)

    # ── Hook tester: pick the strongest opening ──
    try:
        from hook_tester import pick_best_hook
        post_he = data.get("post_hebrew", "")
        current_opening = post_he.split("\n", 1)[0] if post_he else ""
        hooks = data.get("hooks", [])
        result = pick_best_hook(hooks, current_opening=current_opening)
        print(f"  [Agent3] 🎣 Hook score: {result['score']}/100 ({'switched' if result['switched'] else 'kept original'})")
        for r in result.get("reasons", [])[:2]:
            print(f"     ✓ {r}")
        for w in result.get("warnings", [])[:1]:
            print(f"     ⚠ {w}")
        # If a stronger hook was found, swap it into the post
        if result["switched"] and result["best"]:
            new_post = result["best"] + "\n" + post_he.split("\n", 1)[-1]
            data["post_hebrew"] = new_post
            data["_original_opening"] = current_opening
            data["_swapped_to"] = result["best"]
    except Exception as e:
        print(f"  [Agent3] hook tester skipped ({e})")

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
    data = ask_claude_json(prompt, system=system, max_budget=2.0, timeout=280)
    return _save_blog(data, base)


def _create_podcast(article_text: str, base: str, system: str) -> list[Path]:
    print("  [Agent3] יוצר סקריפט פודקאסט (2 phases)...")

    # Phase 1: Script (the long part — 2,500+ words)
    prompt_script = f"""בהתבסס על המאמר הזה, כתוב סקריפט מלא לפרק פודקאסט בקולו של פז שלמה.

{article_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
אורך הסקריפט: 2,500-3,500 מילים (פרק של 20-30 דקות)
זה חייב להיות סקריפט ארוך ומפורט — לא תקציר!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

צור JSON עם:
- episode_title: שם הפרק — שאלה או טענה חדה (עברית)
- duration_minutes: אורך משוער (20-30)
- hook: פתיח 30 שניות — סיפור ספציפי מהשטח, גוף ראשון (100-150 מילים)
- intro: הקדמה 1-2 דקות — הצג את הנושא בשפה דבורה (200-300 מילים)
- sections: מערך של 4-5 חלקים, כל אחד עם:
  - title: כותרת החלק
  - duration: אורך בדקות (4-6 דקות לחלק)
  - script: סקריפט מלא של החלק (400-600 מילים לחלק!)
    שפה דבורה, משפטים עד 12 מילה.
    כלול [הפסקה] אחרי טענות חשובות.
    כלול [דוגמה מהשטח] כשמתאים.
    כלול ציטוטים מחוקרים בשפה טבעית ("לפי X שחקר את...")
- outro: סיום + שאלה פתוחה למאזין (100-150 מילים)
- show_notes: תקציר 3-4 משפטים לפרק
- key_points: מערך של 3-5 נקודות מפתח
- sources: מערך של 4-6 מקורות (שם, שנה, כותרת)
- tags: מערך של 5-8 תגיות

חשוב מאד: כל section.script חייב להיות 400-600 מילים. סקריפט קצר = פרק ריק.
החזר JSON בלבד."""
    # Single call — script + show notes together (was 2 calls)
    data = ask_claude_json(prompt_script, system=system, max_budget=2.8, timeout=320)
    data.setdefault("show_notes", data.get("episode_title", ""))

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

    # Calibration check — read once at start of run, log if drift > 15%
    try:
        from calibration import calibrate as _calibrate
        _calib = _calibrate()
        if _calib.get("samples", 0) >= 5 and _calib.get("drift", 0.0) > 0.15:
            print(
                f"  [Agent3] ⚠️ Calibration drift {_calib['drift']:.0%} "
                f"(verdict={_calib['verdict']}, n={_calib['samples']}, "
                f"corr={_calib['correlation']:+.2f}) — "
                f"QA threshold for high engagement: "
                f"{_calib['qa_threshold_for_high_engagement']}/100"
            )
    except Exception as _calib_err:
        print(f"  [Agent3] calibration check skipped: {_calib_err}")

    # Prefer briefing (practitioner-facing) over full academic article.
    # Briefing has: proven vs suggested tagging, exact numbers, contradictions,
    # and publication angles — optimized for Agent 3 to write from.
    briefing = article_paths.get("briefing")
    if briefing and Path(briefing).exists():
        article_path = briefing
        print(f"  [Agent3] Using briefing (practitioner mode)")
    else:
        article_path = article_paths.get("md") or article_paths.get("docx")
    if not article_path or not Path(article_path).exists():
        raise ValueError("No article file. Run Agent 2 first.")

    article_text = _read_article(Path(article_path))

    # Inject field examples based on article themes
    theme_words = [w for w in article_text[:500].split() if len(w) > 3][:20]
    examples_block = format_examples_for_prompt(theme_words)
    if examples_block:
        article_text += f"\n\n{examples_block}"

    # Inject a curated quote suggestion (avoiding recently-used authors)
    try:
        from quote_bank import format_quote_for_prompt
        quote_block = format_quote_for_prompt(theme_words)
        if quote_block:
            article_text += f"\n\n{quote_block}"
    except Exception:
        pass

    # Inject cross-corpus pattern brief — what Paz already said about this topic
    try:
        from corpus_patterns import format_pattern_brief
        # Use first 3-5 significant words as topic
        topic_str = " ".join(theme_words[:5]) if theme_words else ""
        pattern_brief = format_pattern_brief(topic_str, theme_words)
        if pattern_brief:
            article_text += f"\n\n{pattern_brief}"
    except Exception:
        pass

    if extra_instruction:
        article_text += f"\n\n━━━ הנחיה נוספת ━━━\n{extra_instruction}"

    # ── Long-form Arc Tracker — inject "PREVIOUS POSTS IN ARC" block ──
    # If there's an unfinished arc in progress, the LLM gets context on what
    # already came in this multi-post journey — so it can reference prior posts
    # naturally ("כפי שטענתי בפוסט הקודם על X..."). No new LLM call; just text.
    try:
        from arc_tracker import current_arc_status, format_previous_posts_block
        _arc_status = current_arc_status()
        if _arc_status.get("active_arc_id") and not _arc_status.get("needs_new_arc"):
            arc_block = format_previous_posts_block(_arc_status["active_arc_id"])
            if arc_block:
                article_text += f"\n\n{arc_block}"
                print(f"  [Agent3] 🔗 Arc context injected: "
                      f"{_arc_status['active_arc_id']} "
                      f"({_arc_status['posts_in_arc']}/12)")
    except Exception as _arc_err:
        print(f"  [Agent3] arc context skipped: {_arc_err}")

    # ── Genre-aware voice routing — detect BEFORE writing ──
    # Different post types (explanation / personal_reflection / news_commentary /
    # research_summary) need slightly different emphasis. The base voice profile
    # stays the same; we just nudge which traits to lean into.
    try:
        detected_genre = detect_genre(article_text)
        genre_block = format_genre_for_prompt(detected_genre)
        article_text += f"\n\n{genre_block}"
        print(f"  [Agent3] 🎭 Genre detected: {detected_genre}")
    except Exception as genre_err:
        detected_genre = None
        print(f"  [Agent3] genre router skipped: {genre_err}")

    base   = Path(article_path).stem
    system = _build_system(content_types)
    saved  = {}

    # Cumulative-time guard — 3 platforms × Claude calls can exceed the 30-min
    # step timeout. Track elapsed; skip remaining platforms if running long.
    import time as _time
    _content_start = _time.time()
    _CONTENT_BUDGET_S = 24 * 60  # 24 min — leaves margin within 30-min step cap

    def _time_left() -> float:
        return _CONTENT_BUDGET_S - (_time.time() - _content_start)

    if "linkedin" in content_types:
        # A/B testing default-on for LinkedIn — generates 2 variants for engagement comparison
        ab_test_active = ab_test or True
        saved["linkedin"] = _create_linkedin(article_text, base, system, ab_test=ab_test_active)

    if "blog" in content_types:
        if _time_left() < 6 * 60:
            print(f"  [Agent3] ⏭  Blog skipped — only {_time_left()/60:.1f} min left in budget")
        else:
            saved["blog"] = _create_blog(article_text, base, system)

    if "podcast" in content_types:
        if _time_left() < 6 * 60:
            print(f"  [Agent3] ⏭  Podcast skipped — only {_time_left()/60:.1f} min left in budget")
        else:
            saved["podcast"] = _create_podcast(article_text, base, system)

    # ── Devil's Advocate — opt-in (MOKI_DEVIL=1) to save a Claude call ──
    # Skipped by default: keeps the pipeline lean for rate-limited CLI runs.
    _devil_review_cached = None
    import os as _os
    _devil_enabled = _os.environ.get("MOKI_DEVIL", "0") == "1"
    try:
        if not _devil_enabled:
            raise StopIteration  # skip cleanly
        from devils_advocate import review_post, save_review
        _source_for_review = article_text[:3500] if article_text else ""
        if _source_for_review:
            print(f"\n  👹 Devil's Advocate (article-level review)...")
            _devil_review_cached = review_post(
                _source_for_review, platform="article",
                topic_context=" | ".join(theme_words[:5]) if theme_words else "",
            )
            verdict = _devil_review_cached.get("verdict", "?")
            icon = {"strong": "✅", "okay": "⚠️", "weak": "❌"}.get(verdict, "❓")
            print(f"  {icon} Article verdict: {verdict}")
            if _devil_review_cached.get("kill_switch"):
                print(f"  🛑 STOP: {_devil_review_cached.get('kill_reason', '')}")
            for obj in (_devil_review_cached.get("objections") or [])[:3]:
                print(f"     ✋ {obj.get('role', '?')}: {obj.get('issue', '')[:80]}")
            # Save review file once
            try:
                save_review(Path(article_path), _devil_review_cached)
            except Exception:
                pass
    except StopIteration:
        print(f"  👹 Devil's Advocate skipped (opt-in: MOKI_DEVIL=1)")
    except Exception as devil_err:
        print(f"  👹 Devil's Advocate skipped: {devil_err}")

    # ── Voice QA — check adherence + auto-fix ───
    from voice_profile import check_voice_adherence
    voice_system = get_voice_prompt(content_types[0])
    _seen_content_hashes: set[str] = set()  # dedupe across paths within same platform
    for ct, paths in saved.items():
        for p in paths:
            try:
                content = Path(p).read_text(encoding="utf-8")
                # Skip duplicate content (same post saved twice — e.g. linkedin.txt + ready.txt)
                import hashlib
                content_hash = hashlib.md5(content[:500].encode("utf-8")).hexdigest()
                if content_hash in _seen_content_hashes:
                    continue
                _seen_content_hashes.add(content_hash)
                vqa = check_voice_adherence(content, platform=ct)
                score = vqa["score"]
                icon = "✅" if score >= 75 else "⚠️" if score >= 50 else "❌"
                print(f"  {icon} Voice QA [{ct}]: {score}/100")
                if vqa["issues"]:
                    for issue in vqa["issues"][:3]:
                        print(f"     • {issue}")
                if vqa["strengths"]:
                    for s in vqa["strengths"][:2]:
                        print(f"     ✓ {s}")

                # Auto-fix: one pass if score < 75
                if score < 75 and vqa.get("issues"):
                    old_score = score
                    issues_text = "\n".join(f"- {iss}" for iss in vqa["issues"])
                    fix_prompt = (
                        f"הפוסט הבא קיבל ציון {score}/100 בבדיקת קול. "
                        f"תקן את הבעיות הבאות:\n{issues_text}\n\n"
                        f"הפוסט:\n{content}\n\n"
                        f"החזר את הפוסט המתוקן בלבד."
                    )
                    try:
                        fixed_content = ask_claude(fix_prompt, system=voice_system, max_budget=0.5, timeout=120)
                        if fixed_content and fixed_content.strip():
                            # Re-run QA on the fixed version
                            vqa_new = check_voice_adherence(fixed_content.strip(), platform=ct)
                            new_score = vqa_new["score"]
                            if new_score > old_score:
                                Path(p).write_text(fixed_content.strip(), encoding="utf-8")
                                print(f"  🔧 Voice QA auto-fix: {old_score} → {new_score}")
                            else:
                                print(f"  🔧 Voice QA auto-fix: no improvement ({old_score} → {new_score}), keeping original")
                    except Exception as fix_err:
                        print(f"  🔧 Voice QA auto-fix failed: {fix_err}")

                # ── Anti-pattern memory: record if final score is still low ──
                try:
                    final_text = Path(p).read_text(encoding="utf-8")
                    final_vqa = check_voice_adherence(final_text, platform=ct)
                    final_score = final_vqa.get("score", score)
                    if final_score < 60:
                        from anti_patterns import record_failure
                        record_failure(
                            post_path=Path(p),
                            qa_score=final_score,
                            voice_score=final_score,
                            engagement=None,
                        )
                        print(f"  📕 Anti-pattern recorded (Voice {final_score}<60)")
                except Exception as ap_err:
                    print(f"  📕 Anti-pattern record skipped: {ap_err}")

                # ── PARALLEL heuristic checkers (Similarity + Pacing + Reading) ──
                # These are pure-Python, no LLM — run concurrently in threads.
                from concurrent.futures import ThreadPoolExecutor as _TPE
                final_content = Path(p).read_text(encoding="utf-8")

                def _run_similarity():
                    from similarity_checker import check_post_similarity
                    return ("similarity", check_post_similarity(final_content, platform=ct))

                def _run_pacing():
                    from pacing_analyzer import reading_level, analyze_pacing
                    return ("pacing", {"rl": reading_level(final_content),
                                       "pacing": analyze_pacing(final_content)})

                with _TPE(max_workers=2) as _ex:
                    futures = [_ex.submit(_run_similarity), _ex.submit(_run_pacing)]
                    for f in futures:
                        try:
                            kind, data = f.result(timeout=20)
                            if kind == "similarity" and data.get("flagged"):
                                ms = data.get("most_similar") or {}
                                print(f"  ⚠️ Post similarity: {data['max_similarity']:.0%} "
                                      f"overlap with {ms.get('file', '?')}")
                                for w in data.get("warnings", [])[:3]:
                                    print(f"     • {w}")
                            elif kind == "pacing":
                                rl = data["rl"]
                                pacing = data["pacing"]
                                print(f"  📖 Reading level [{ct}]: {rl['score']}/100 ({rl['grade']})")
                                print(f"  📊 Pacing [{ct}]: {pacing['arc']} arc, "
                                      f"{pacing['flat_zones']} flat zones")
                        except Exception as e:
                            print(f"  ⚠️ Checker failed: {e}")

                # ── Counter-argument injector — flag posts with no internal tension ──
                # Runs between Voice QA and Devil's Advocate. Surfaces opposing
                # views when the post lacks markers like "אבל" / "מצד שני" / "ובכל זאת".
                try:
                    final_content = Path(p).read_text(encoding="utf-8")
                    counter_themes = list(theme_words[:8]) if theme_words else []
                    if detected_genre:
                        counter_themes.append(detected_genre)
                    counter_result = suggest_counter(final_content, counter_themes)
                    print(format_counter_report(counter_result))
                except Exception as counter_err:
                    print(f"  🪞 Counter-arg skipped: {counter_err}")

                # ── Devil's Advocate verdict (computed ONCE per article — see below) ──
                # Reuses _devil_review_cached if available; saves $1.5 + 2min/run
                if _devil_review_cached:
                    verdict = _devil_review_cached.get("verdict", "?")
                    icon = {"strong": "✅", "okay": "⚠️", "weak": "❌"}.get(verdict, "❓")
                    print(f"  {icon} Devil's Advocate [{ct}]: {verdict} (shared)")

                # ── Causal Chain Validator — correlation vs causation ──
                try:
                    from causal_validator import validate_causal_claims
                    final_content = Path(p).read_text(encoding="utf-8")
                    causal = validate_causal_claims(final_content)
                    if causal["score"] < 80 and causal["weak_claims"]:
                        print(f"  🔗 Causal: {causal['score']}/100 ({causal['claims_found']} claims, {len(causal['weak_claims'])} weak)")
                        for c in causal["weak_claims"][:2]:
                            print(f"     ⚠ '{c['claim'][:50]}...' — {c['suggestion']}")
                except Exception as causal_err:
                    print(f"  🔗 Causal validator skipped: {causal_err}")
            except Exception:
                pass

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
