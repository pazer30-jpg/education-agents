"""
Agent 0 — Planner
מנתח את מה שנחקר עד כה ומחליט מה לחקור הבא.
משתמש ב-claude_cli (ללא API key).
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from config import PAPERS_DIR, ARTICLES_DIR
from claude_cli import ask_claude_json
from memory import load_memory, save_memory, add_to_queue, set_gaps

try:
    from obsidian_memory import format_for_prompt as _obsidian_memory_for_prompt
except Exception:
    def _obsidian_memory_for_prompt(_names: list[str], **_kw) -> str:
        return ""


# ─────────────────────────────────────────────
# Calendar awareness — חגים ואירועים חינוכיים
# ─────────────────────────────────────────────

# Hebrew holidays shift each year — hardcoded exact Gregorian dates per year.
# Verified against calendar.2net.co.il (April 2026).
# Format: (year, month, day, name, topic_hint).
HEBREW_CALENDAR = {
    2026: [
        (4,  2, "פסח", "חירות, יציאה, שאלת הסדר"),
        (4, 14, "יום השואה", "זיכרון, חינוך לשואה, טראומה בין-דורית"),
        (4, 21, "יום הזיכרון", "אבל, חוסן, שייכות לאומית, תקווה"),
        (4, 22, "יום העצמאות", "זהות, שייכות, חינוך ערכי"),
        (5,  5, "ל\"ג בעומר", "קבוצה, אש, שייכות"),
        (5, 22, "שבועות", "קבלה, לימוד, ברית קהילתית"),
        (9, 12, "ראש השנה", "התחדשות, רפלקציה, תחילת דרך"),
        (9, 21, "יום כיפור", "חשבון נפש, סליחה, אחריות"),
        (9, 26, "סוכות", "ארעיות, קהילה, שמחה"),
        (10, 3, "שמחת תורה", "סיום והתחלה, מעגל, חגיגה"),
        (12, 4, "חנוכה", "אור, זהות, חינוך בלתי-פורמלי בחגים"),
    ],
    2027: [
        (4, 22, "פסח", "חירות, יציאה, שאלת הסדר"),
        (5,  4, "יום השואה", "זיכרון, חינוך לשואה, טראומה בין-דורית"),
        (5, 11, "יום הזיכרון", "אבל, חוסן, שייכות לאומית, תקווה"),
        (5, 12, "יום העצמאות", "זהות, שייכות, חינוך ערכי"),
        (5, 25, "ל\"ג בעומר", "קבוצה, אש, שייכות"),
        (6, 11, "שבועות", "קבלה, לימוד, ברית קהילתית"),
        (10, 2, "ראש השנה", "התחדשות, רפלקציה, תחילת דרך"),
        (10, 11, "יום כיפור", "חשבון נפש, סליחה, אחריות"),
        (10, 16, "סוכות", "ארעיות, קהילה, שמחה"),
        (10, 23, "שמחת תורה", "סיום והתחלה, מעגל, חגיגה"),
        (12, 24, "חנוכה", "אור, זהות, חינוך בלתי-פורמלי בחגים"),
    ],
    2028: [
        (4, 11, "פסח", "חירות, יציאה, שאלת הסדר"),
        (4, 24, "יום השואה", "זיכרון, חינוך לשואה, טראומה בין-דורית"),
        (5,  1, "יום הזיכרון", "אבל, חוסן, שייכות לאומית, תקווה"),
        (5,  2, "יום העצמאות", "זהות, שייכות, חינוך ערכי"),
        (5, 14, "ל\"ג בעומר", "קבוצה, אש, שייכות"),
        (5, 24, "יום שחרור ירושלים", "שייכות, ירושלים, מורכבות הזיכרון"),
        (5, 30, "שבועות", "קבלה, לימוד, ברית קהילתית"),
    ],
}

# Fixed-date events (same every year)
FIXED_EVENTS = [
    (1, 27, "יום השואה הבינלאומי", "זיכרון, טראומה, חוסן קהילתי"),
    (3, 15, "פורים (בערך)", "זהות, מסכות, קבוצה, שייכות"),
    (5, 15, "סוף שנת לימודים", "סיכום, מעברים, פרידה מקבוצה"),
    (9, 1, "תחילת שנה\"ל", "שייכות חדשה, בניית קבוצה, מנהיגות"),
    (10, 7, "שנה ל-7/10", "חוסן, טראומה, קהילה בחירום, תקווה"),
]

# Backwards-compat alias
CALENDAR_EVENTS = FIXED_EVENTS


def _get_upcoming_events(days_ahead: int = 21) -> list[dict]:
    """מחזיר אירועים שקרובים ב-N ימים הקרובים."""
    today = datetime.now()
    upcoming = []

    # Hebrew calendar events — current year + next year
    for year in (today.year, today.year + 1):
        for month, day, name, hint in HEBREW_CALENDAR.get(year, []):
            try:
                event_date = datetime(year, month, day)
                if event_date < today:
                    continue
                delta = (event_date - today).days
                if 0 <= delta <= days_ahead:
                    upcoming.append({
                        "name": name,
                        "date": event_date.strftime("%d/%m"),
                        "days_until": delta,
                        "topic_hint": hint,
                    })
            except ValueError:
                pass

    # Fixed-date events
    for month, day, name, hint in FIXED_EVENTS:
        try:
            event_date = datetime(today.year, month, day)
            if event_date < today:
                event_date = datetime(today.year + 1, month, day)
            delta = (event_date - today).days
            if 0 <= delta <= days_ahead:
                upcoming.append({
                    "name": name,
                    "date": event_date.strftime("%d/%m"),
                    "days_until": delta,
                    "topic_hint": hint,
                })
        except ValueError:
            pass

    return sorted(upcoming, key=lambda e: e["days_until"])


def _build_context() -> dict:
    """Collect current state for the planner."""
    mem = load_memory()

    # Sample existing papers (titles only, to stay within prompt limits)
    papers_sample = []
    for f in PAPERS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            papers = data.get("papers", data) if isinstance(data, dict) else data
            topic = data.get("topic", f.stem) if isinstance(data, dict) else f.stem
            for p in papers[:5]:
                papers_sample.append({
                    "title": p.get("title", ""),
                    "year": p.get("year"),
                    "topic": topic,
                })
        except Exception:
            pass

    # Article previews
    articles_preview = []
    for f in ARTICLES_DIR.glob("*.md"):
        text = f.read_text(encoding="utf-8")[:400]
        articles_preview.append({"file": f.name, "preview": text})

    # Build coverage analysis
    coverage = mem.get("coverage_map", {})
    max_score = max(coverage.values()) if coverage else 1
    coverage_analysis = {
        "BLOCKED_do_not_pick": [t for t, s in coverage.items() if s >= 7],
        "CAUTION_low_priority": [t for t, s in coverage.items() if 4 <= s < 7],
        "AVAILABLE_preferred": [t for t, s in coverage.items() if s < 4],
    }
    # Topics that were mentioned in gaps/queue but never researched
    all_researched = set(t.lower() for t in mem.get("researched_topics", []))
    unexplored = [t for t in mem.get("topic_queue", []) if t.lower() not in all_researched]

    # Calendar events
    upcoming_events = _get_upcoming_events(21)

    # Performance patterns — what resonated with the audience
    perf_patterns = {}
    try:
        from performance_log import get_patterns_for_prompt
        perf_text = get_patterns_for_prompt()
        if perf_text:
            perf_patterns = mem.get("performance_patterns", {})
    except Exception:
        pass

    # Trending topics from public sources — gives the planner a "what's hot now" signal.
    # Fully isolated: any failure (network, parse) yields an empty list and never crashes.
    try:
        from trending import fetch_trending_topics
        trending = fetch_trending_topics(max_topics=5)
    except Exception:
        trending = []

    ctx = {
        "memory_summary": {
            "main_field": mem.get("main_field"),
            "researched_topics": mem["researched_topics"],
            "total_papers": len(mem["papers"]),
            "articles_written": [a["topic"] for a in mem["articles"]],
            "content_created": [(c["type"], c["topic"]) for c in mem["content_created"]],
            "topic_queue": mem["topic_queue"],
            "gaps": mem["gaps"],
            "iterations": mem["iterations"],
        },
        "coverage_analysis": coverage_analysis,
        "unexplored_topics": unexplored[:10],
        "papers_sample": papers_sample[:40],
        "articles_preview": articles_preview[:6],
        "upcoming_events": upcoming_events,
        "performance_patterns": perf_patterns,
        "trending": trending,
    }
    return ctx


def run_planner(main_field: str, user_hints: list[str] = None) -> dict:
    """
    Run Agent 0 — decides what to research next.
    Returns: {"topic": str, "subtopics": list, "content_types": list, "reasoning": str}
    """
    print(f"\n{'='*60}")
    print(f"🧠 Agent 0 — Planner | תחום: {main_field}")
    print(f"{'='*60}\n")

    # Update main field in memory
    mem = load_memory()
    if not mem.get("main_field"):
        mem["main_field"] = main_field
        save_memory(mem)

    ctx = _build_context()
    hints_text = f"\nהמשתמש רוצה להתמקד ב: {', '.join(user_hints)}" if user_hints else ""

    # Obsidian memory — strong/weak topics + voice + sources
    memory_block = _obsidian_memory_for_prompt([
        "strong_topics",
        "weak_topics",
        "recurring_sources",
    ], max_chars_per_note=1000)

    prompt = f"""{memory_block}

אתה פלאנר אסטרטגי של מחקר בתחום חינוך — עם דגש על חינוך בלתי-פורמלי.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
גבולות השדה — מה כן ומה לא
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ כן (בחר מכאן):
  - חינוך בלתי-פורמלי (תנועות נוער, כפרי נוער, מכינות, שנת שירות)
  - חינוך ערכי, חינוך לאזרחות, חינוך חברתי
  - פדגוגיה (הוראה, למידה, הערכה, תוכניות לימודים)
  - פסיכולוגיה חינוכית (מוטיבציה, חוסן, שייכות, זהות)
  - מנהיגות חינוכית, ניהול צוותים חינוכיים
  - חינוך בחירום, חינוך בטראומה
  - הכלה, נגישות, חינוך מיוחד
  - חינוך סביבתי, חינוך הרפתקני
  - חינוך דיגיטלי, למידה מקוונת
  - סוציולוגיה של חינוך, מדיניות חינוך

❌ לא (אל תבחר):
  - נושאים שאינם קשורים לחינוך כלל (רפואה, הנדסה, כלכלה טהורה)
  - marketing, SEO, business strategy
  - טכנולוגיה שאינה חינוכית

מצב נוכחי:
{json.dumps(ctx['memory_summary'], ensure_ascii=False, indent=2)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
חוקי כיסוי — חובה לעמוד בהם:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔴 BLOCKED — אסור לבחור (כיסוי ≥7):
{json.dumps(ctx['coverage_analysis']['BLOCKED_do_not_pick'], ensure_ascii=False)}

🟡 CAUTION — רק אם אין אלטרנטיבה (כיסוי 4-6):
{json.dumps(ctx['coverage_analysis']['CAUTION_low_priority'], ensure_ascii=False)}

🟢 AVAILABLE — עדיפות מלאה (כיסוי <4):
{json.dumps(ctx['coverage_analysis']['AVAILABLE_preferred'], ensure_ascii=False)}

נושאים שטרם נחקרו (עדיפות גבוהה מאד!):
{json.dumps(ctx['unexplored_topics'], ensure_ascii=False)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 אירועים קרובים (21 יום) — תעדוף נושאים רלוונטיים!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{json.dumps(ctx['upcoming_events'], ensure_ascii=False, indent=2) if ctx['upcoming_events'] else 'אין אירועים קרובים.'}
אם יש אירוע קרוב — נושא אחד לפחות מ-3 חייב להיות רלוונטי אליו.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 מה עבד אצל הקהל (performance patterns)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{json.dumps(ctx['performance_patterns'], ensure_ascii=False, indent=2) if ctx['performance_patterns'] else 'אין נתוני ביצועים עדיין.'}
אם יש best_topics — תעדף נושאים קרובים אליהם. הקהל הגיב.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔥 נושאי חינוך חמים כרגע (Hacker News / Reddit / arXiv)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{json.dumps(ctx['trending'], ensure_ascii=False, indent=2) if ctx['trending'] else 'אין נתוני trending זמינים.'}
זה הקשר חיצוני — מה מעניין את עולם החינוך *עכשיו*. אם נושא מהרשימה מתחבר לקורפוס שלך
או לאירוע קרוב — שקול לתעדף אותו לזווית עכשווית. אל תבחר נושא רק כי הוא חם — חייב להיות
חיבור אמיתי לחינוך בלתי-פורמלי / לקול של פז.

דגימה מהמאמרים הקיימים:
{json.dumps(ctx['papers_sample'], ensure_ascii=False, indent=1)}

תצוגה מקדימה של מאמרים שנכתבו:
{json.dumps(ctx['articles_preview'], ensure_ascii=False, indent=1)}
{hints_text}

כלל ה-3 זוויות — כל מאמר משולב חייב לכלול:
  - נושא 1: רקע תיאורטי / תיאוריה מרכזית
  - נושא 2: ממצאים אמפיריים / מחקר שטח
  - נושא 3: יישום / השלכות מעשיות

לוגיקת בחירה:
1. בחר 3 נושאים — כולם מ-AVAILABLE (ירוק)
2. אם אין מספיק AVAILABLE — מותר אחד מ-CAUTION (צהוב)
3. לעולם אל תבחר מ-BLOCKED (אדום) — גם לא כנושא משנה
4. נושאים מ-"טרם נחקרו" = עדיפות מקסימלית
5. כל נושא ספציפי מספיק לחיפוש אקדמי
6. combined_title משקף סינתזה בין 3 הנושאים (עברית, עד 80 תווים)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
שאלות מחקר (RQs) — חובה לכל נושא
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
לכל נושא — נסח שאלת מחקר חדה.
שאלה טובה:
  ✓ ספציפית (לא "איך חינוך עובד?")
  ✓ ניתנת למענה מהספרות (לא "האם חינוך טוב?")
  ✓ מכילה מתח/דיבט — "X או Y?", "תחת אילו תנאים?", "מה ההבדל בין?"
  ✓ רלוונטית לשטח של פז (חינוך בלתי-פורמלי)
  ✓ מעוגנת בנושא — לא כללית

פורמטים מקובלים:
  • "תחת אילו תנאים X מוביל ל-Y בקרב נוער?"
  • "מה ההבדל בין X ל-Y בהקשר של תנועות נוער?"
  • "איך X מוסבר בתיאוריה Y לעומת תיאוריה Z?"
  • "מה הקשר בין X ל-Y, ומה ממתן את הקשר?"

דוגמאות טובות:
  ✓ "תחת אילו תנאים שייכות קבוצתית מחזקת חוסן בנוער במצבי חירום?"
  ✓ "מה ההבדל בין מנהיגות פורמלית ובלתי-פורמלית בתנועות נוער?"
  ✗ "האם שייכות חשובה?" — בנאלית
  ✗ "מה זה חינוך בלתי-פורמלי?" — הגדרתית

החזר JSON עם:
- topics: מערך של בדיוק 3 אובייקטים, כל אחד עם:
    - topic: נושא ספציפי (אנגלית לחיפוש)
    - subtopics: מערך של 3-4 נושאי משנה (אנגלית)
    - angle: הזווית הייחודית — "theoretical" / "empirical" / "practical"
    - research_question: שאלת מחקר חדה (עברית, פורמט מלמעלה)
    - sub_questions: מערך של 2-3 שאלות משנה (עברית) שתומכות ב-RQ הראשית
- combined_research_question: שאלה כוללת שמחברת את 3 שאלות המחקר (עברית)
- combined_title: כותרת משולבת (עברית)
- future_topics: מערך של 5-8 נושאים לעתיד (אנגלית)
- gaps_identified: מערך של פערים שזוהו (עברית)
- reasoning: הסבר קצר מדוע בחרת נושאים אלה ואיך הם מתחברים (עברית)
- content_recommendation: אובייקט עם "types" (מערך linkedin/blog/podcast) ו-"reason"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
הצעת מחקר (proposal) — חובה
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- proposal: אובייקט עם השדות הבאים (כל הטקסטים בעברית):
    - title: כותרת קצרה לעבודת המחקר
    - background: רקע — למה זה חשוב עכשיו (2-3 משפטים)
    - rationale: למה דווקא הנושא הזה לפז (קשר לקהל, ל-coverage שלו, לאקטואליה)
    - hypothesis: מה אנחנו צופים שהממצאים יראו (משפט אחד — לא "אנחנו לא יודעים")
    - methodology: איך הספרות תיחקר (מקורות, קריטריונים, מדגם משוער)
    - expected_contribution: מה החידוש של המאמר (3-4 שורות)
    - audience: למי הוא מיועד (מדריכים? מנהלים? קובעי מדיניות? חוקרים?)
    - corpus_connection: קשר ל-papers שכבר נחקרו (איזה הוגים מהקורפוס יבולטו)
    - risks: 2-3 סיכונים — מה יכול להשתבש (מחסור במקורות, חפיפה לפוסט קודם...)
    - timeline_estimate: זמן הריצה הצפוי (במילים: "30-40 דקות", "כמה שעות")

החזר JSON בלבד."""

    print("  [Agent0] מחשב תוכנית...")
    try:
        result = ask_claude_json(prompt, max_budget=0.8)
    except Exception as e:
        print(f"  [Agent0] שגיאה: {e} — משתמש בנושאים ברירת מחדל")
        result = {
            "next_topics": [
                user_hints[0] if user_hints else "values education youth",
                user_hints[1] if len(user_hints) > 1 else "identity formation adolescents",
                user_hints[2] if len(user_hints) > 2 else "non-formal education belonging",
            ],
            "next_subtopics": {},
            "combined_title": "חינוך, זהות ושייכות",
            "future_topics": [],
            "gaps_identified": [],
            "reasoning": "fallback",
            "content_recommendation": {"types": ["linkedin", "blog"], "reason": ""},
        }

    # Support both new format (topics as objects) and old format (next_topics as strings)
    raw_topics = result.get("topics") or []
    if raw_topics and isinstance(raw_topics[0], str):
        # old format fallback
        raw_topics = [{"topic": t, "subtopics": [], "angle": "general"} for t in raw_topics]
    raw_topics = raw_topics[:3]

    # Pad to 3 if needed
    while len(raw_topics) < 3:
        raw_topics.append({"topic": main_field, "subtopics": [], "angle": "general"})

    # ── Long-form Arc Tracker — surface 4th option if a multi-post arc is in progress ──
    # An "arc" is a 10-15 post journey on a connected theme. If the previous N posts
    # form an unfinished arc, propose continuing it as an alternative to the 3 fresh
    # topics — keeps narrative continuity without forcing it.
    arc_option = None
    try:
        from arc_tracker import planner_arc_option
        arc_option = planner_arc_option()
    except Exception as _arc_err:
        print(f"  [Agent0] arc tracker skipped: {_arc_err}")
    if arc_option:
        raw_topics.append(arc_option)
        print(f"  [Agent0] 🎯 קשת פעילה: '{arc_option['topic']}' — "
              f"{arc_option.get('arc_position', '?')}")

    combined_title = result.get("combined_title", " + ".join(t["topic"] for t in raw_topics))

    # Build subtopics_map for backward compat
    subtopics_map = {t["topic"]: t.get("subtopics", []) for t in raw_topics}
    topics_list   = [t["topic"] for t in raw_topics]

    # Save to memory
    add_to_queue(topics_list + result.get("future_topics", []))
    set_gaps(result.get("gaps_identified", []))
    mem = load_memory()
    # Extract research questions (with fallback if model didn't include them)
    combined_rq = result.get("combined_research_question", "")
    research_questions = []
    for t in raw_topics:
        rq = t.get("research_question") or f"מה ידוע על {t.get('topic', '')}?"
        research_questions.append({
            "topic": t.get("topic", ""),
            "question": rq,
            "sub_questions": t.get("sub_questions", []),
        })

    mem["next_plan"] = {
        "topics":          raw_topics,
        "combined_title":  combined_title,
        "subtopics_map":   subtopics_map,
        "research_questions": research_questions,           # NEW
        "combined_research_question": combined_rq,          # NEW
        "reasoning":       result.get("reasoning", ""),
        "content_recommendation": result.get("content_recommendation", {}),
    }
    save_memory(mem)

    # ── Save research proposal as Markdown for review ──
    proposal = result.get("proposal", {}) or {}
    proposal_path = None
    if proposal:
        try:
            proposal_path = _save_proposal(
                proposal=proposal,
                combined_title=combined_title,
                research_questions=research_questions,
                combined_rq=combined_rq,
                topics=raw_topics,
            )
        except Exception as e:
            print(f"  [Agent0] ⚠️ Proposal save failed: {e}")

    plan = {
        "topics":       raw_topics,
        "topic":        combined_title,
        "combined_title": combined_title,
        "subtopics_map": subtopics_map,
        "subtopics":    [],
        "research_questions": research_questions,
        "combined_research_question": combined_rq,
        "proposal": proposal,                                # NEW
        "proposal_path": str(proposal_path) if proposal_path else None,  # NEW
        "content_types": result.get("content_recommendation", {}).get("types", ["linkedin", "blog"]),
        "reasoning":    result.get("reasoning", ""),
    }

    print(f"  [Agent0] 3 נושאים: {' | '.join(t['topic'] for t in raw_topics)}")
    print(f"  [Agent0] כותרת משולבת: {combined_title}")
    if combined_rq:
        print(f"  [Agent0] שאלת מחקר כוללת: {combined_rq}")
    for i, rq in enumerate(research_questions, 1):
        print(f"  [Agent0] RQ{i}: {rq['question']}")
    if proposal_path:
        print(f"  [Agent0] 📋 הצעת מחקר נשמרה: {proposal_path.name}")
    print(f"  [Agent0] תוכן מומלץ: {plan['content_types']}")
    print(f"\n✅ Agent 0 complete\n")
    return plan


def _save_proposal(proposal: dict, combined_title: str,
                   research_questions: list[dict], combined_rq: str,
                   topics: list[dict]) -> Path:
    """Save research proposal as a clean Markdown document."""
    from config import OUTPUT_DIR
    proposals_dir = OUTPUT_DIR / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = proposals_dir / f"proposal_{stamp}.md"

    lines = [
        f"# הצעת מחקר — {proposal.get('title') or combined_title}",
        f"",
        f"_Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')} · Agent 0 (Planner) · 🦊 Moki_",
        f"",
        f"---",
        f"",
        f"## 🎯 שאלת מחקר כוללת",
        f"",
        f"> {combined_rq or '(לא הוגדרה)'}",
        f"",
        f"## 📋 רקע",
        f"",
        proposal.get("background", "(לא סופק)"),
        f"",
        f"## 💡 למה דווקא זה — Rationale",
        f"",
        proposal.get("rationale", "(לא סופק)"),
        f"",
        f"## 🔬 שאלות מחקר ספציפיות",
        f"",
    ]

    for i, rq in enumerate(research_questions, 1):
        lines.append(f"### RQ{i}: {rq.get('topic', '')}")
        lines.append(f"")
        lines.append(f"**{rq.get('question', '')}**")
        lines.append(f"")
        for sq in rq.get("sub_questions", []):
            lines.append(f"- {sq}")
        lines.append(f"")

    lines.extend([
        f"## 🔮 השערה",
        f"",
        proposal.get("hypothesis", "(לא סופקה)"),
        f"",
        f"## 📚 מתודולוגיה",
        f"",
        proposal.get("methodology", "(לא סופקה)"),
        f"",
        f"## ✨ תרומה צפויה",
        f"",
        proposal.get("expected_contribution", "(לא סופקה)"),
        f"",
        f"## 👥 קהל יעד",
        f"",
        proposal.get("audience", "(לא סופק)"),
        f"",
        f"## 🔗 קשר לקורפוס קיים",
        f"",
        proposal.get("corpus_connection", "(לא סופק)"),
        f"",
        f"## ⚠️ סיכונים",
        f"",
        proposal.get("risks", "(לא סופק)"),
        f"",
        f"## ⏱ זמן ריצה צפוי",
        f"",
        proposal.get("timeline_estimate", "30-40 דקות"),
        f"",
        f"---",
        f"",
        f"## ✅ אישור",
        f"",
        f"- [ ] Approved by Moki",
        f"- [ ] Approved by Paz",
        f"",
        f"_To approve: edit checkbox to [x] or run: `moki > אשר`_",
    ])

    # Defensive flatten — Claude sometimes returns a field as a list, not str.
    # A raw "\n".join(lines) then crashes ("expected str, list found").
    safe_lines = []
    for ln in lines:
        if isinstance(ln, (list, tuple)):
            safe_lines.extend(str(x) for x in ln)
        else:
            safe_lines.append(str(ln))
    out_path.write_text("\n".join(safe_lines), encoding="utf-8")
    return out_path


# ─────────────────────────────────────────────
# Research ideas — grounded in existing corpus
# ─────────────────────────────────────────────

def _collect_corpus(per_topic: int = 3, abstract_chars: int = 350) -> list[dict]:
    """
    Read all papers JSONs, return top-N per topic_file by citation count.
    Balanced selection — avoids generic methodology papers dominating purely by citations.
    """
    out = []
    for f in sorted(PAPERS_DIR.glob("*_papers.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            papers = data.get("papers", data) if isinstance(data, dict) else data
            topic_file = data.get("topic", f.stem) if isinstance(data, dict) else f.stem
        except Exception:
            continue

        group = []
        for p in papers:
            if not isinstance(p, dict):
                continue
            abstract = (p.get("abstract") or "").strip()
            if not abstract:
                continue
            authors = p.get("authors") or []
            if isinstance(authors, list):
                authors_str = ", ".join(str(a) for a in authors[:2])[:80]
            else:
                authors_str = str(authors)[:80]
            group.append({
                "title":     (p.get("title") or "")[:140],
                "year":      p.get("year"),
                "authors":   authors_str,
                "citations": p.get("citation_count") or 0,
                "abstract":  abstract[:abstract_chars],
                "topic_file": topic_file,
            })
        group.sort(key=lambda x: (x["citations"] or 0), reverse=True)
        out.extend(group[:per_topic])
    return out


def _collect_articles_written(max_articles: int = 8, chars: int = 700) -> list[dict]:
    out = []
    for f in sorted(ARTICLES_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        if "_briefing" in f.stem:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        out.append({"file": f.name, "preview": text[:chars]})
        if len(out) >= max_articles:
            break
    return out


def propose_research_ideas(max_ideas: int = 5) -> dict:
    """
    Propose concrete research ideas grounded in the existing corpus.
    Separate from run_planner — does not start the pipeline.
    Returns: {"ideas": [...], "saved_to": path}
    """
    print(f"\n{'='*60}")
    print(f"💡 רעיונות מחקר — מבוססי קורפוס קיים")
    print(f"{'='*60}\n")

    corpus = _collect_corpus()
    articles = _collect_articles_written()
    mem = load_memory()
    covered = [t for t, v in mem.get("coverage_map", {}).items() if v >= 7]

    print(f"  [Ideas] קורפוס: {len(corpus)} מאמרים, {len(articles)} סיכומים שנכתבו")
    if not corpus:
        return {"ideas": [], "saved_to": None, "error": "אין מאמרים בקורפוס"}

    system = """אתה חוקר בכיר שמנתח קורפוס אקדמי קיים ומציע כיוונים חדשים.
חוקים ברזל:
1. כל רעיון חייב להיות מעוגן ב-2-3 מקורות ספציפיים מהרשימה (שם מחבר + שנה).
2. אתה לא ממציא ציטוטים — רק מה שברשימה.
3. אתה מחפש פערים, סתירות בין מאמרים, או חיבורים לא-מפותחים.
4. אל תציע נושאים שמופיעים ברשימת "כבר מוצה"."""

    prompt = f"""פז שלמה — איש חינוך בלתי-פורמלי. יש לו קורפוס אקדמי על הדיסק.
המשימה שלך: הצע לו {max_ideas} רעיונות מחקר חדשים על בסיס מה שיש לו כבר.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
קורפוס — {len(corpus)} מאמרים (ממוינים לפי ציטוטים)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{json.dumps(corpus, ensure_ascii=False, indent=1)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
מאמרים שפז כבר כתב
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{json.dumps(articles, ensure_ascii=False, indent=1)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
נושאים שכבר מוצו (אל תציע שוב)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{json.dumps(covered, ensure_ascii=False)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
החזר JSON בפורמט הבא (ועברית בכל השדות הטקסטואליים):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{{
  "ideas": [
    {{
      "research_question": "שאלת מחקר חדה וספציפית",
      "why_interesting": "איזה פער/סתירה/חיבור זיהית בקורפוס",
      "anchor_sources": ["Smith 2020", "Kahana 2015", "Cohen 2022"],
      "connects_to_article": "שם קובץ מהסיכומים שפז כבר כתב, או null",
      "publication_angle": "איך זה יכול להפוך לפוסט/מאמר של פז",
      "novelty_score": 1-5
    }}
  ]
}}

החזר JSON בלבד."""

    try:
        result = ask_claude_json(prompt, system=system, max_budget=1.5)
    except Exception as e:
        print(f"  [Ideas] שגיאה: {e}")
        return {"ideas": [], "saved_to": None, "error": str(e)}

    ideas = result.get("ideas", [])
    if not isinstance(ideas, list):
        ideas = []

    # Save to disk
    from config import OUTPUT_DIR
    ideas_dir = OUTPUT_DIR / "ideas"
    ideas_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = ideas_dir / f"research_ideas_{stamp}.md"

    lines = [f"# רעיונות מחקר — {stamp}", ""]
    lines.append(f"_מבוסס על {len(corpus)} מאמרים בקורפוס + {len(articles)} סיכומים._")
    lines.append("")
    for i, idea in enumerate(ideas, 1):
        if not isinstance(idea, dict):
            continue
        lines.append(f"## רעיון {i}: {idea.get('research_question', '')}")
        lines.append("")
        lines.append(f"**למה מעניין:** {idea.get('why_interesting', '')}")
        lines.append("")
        anchors = idea.get("anchor_sources", [])
        if isinstance(anchors, list) and anchors:
            lines.append(f"**מקורות מעגנים:** {', '.join(str(a) for a in anchors)}")
            lines.append("")
        connects = idea.get("connects_to_article")
        if connects:
            lines.append(f"**מתחבר למאמר קיים:** {connects}")
            lines.append("")
        lines.append(f"**זווית פרסום:** {idea.get('publication_angle', '')}")
        lines.append("")
        nov = idea.get("novelty_score")
        if nov:
            lines.append(f"**חדשנות:** {'★' * int(nov)}")
            lines.append("")
        lines.append("---")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  [Ideas] נשמרו {len(ideas)} רעיונות → {out_path.name}")

    return {"ideas": ideas, "saved_to": str(out_path)}
