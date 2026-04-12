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


# ─────────────────────────────────────────────
# Calendar awareness — חגים ואירועים חינוכיים
# ─────────────────────────────────────────────

CALENDAR_EVENTS = [
    # (month, day, name, topic_hint)
    (1,  27, "יום השואה הבינלאומי", "זיכרון, טראומה, חוסן קהילתי"),
    (4,  22, "יום השואה", "זיכרון, חינוך לשואה, טראומה בין-דורית"),
    (4,  29, "יום הזיכרון", "אבל, חוסן, שייכות לאומית, תקווה"),
    (4,  30, "יום העצמאות", "זהות, שייכות, חינוך ערכי"),
    (5,  15, "סוף שנת לימודים", "סיכום, מעברים, פרידה מקבוצה"),
    (9,   1, "תחילת שנה\"ל", "שייכות חדשה, בניית קבוצה, מנהיגות"),
    (9,  15, "ראש השנה (בערך)", "התחדשות, רפלקציה, תחילת דרך"),
    (10,  7, "שנה ל-7/10", "חוסן, טראומה, קהילה בחירום, תקווה"),
    (12, 25, "חנוכה (בערך)", "אור, זהות, חינוך בלתי-פורמלי בחגים"),
    (3,  15, "פורים (בערך)", "זהות, מסכות, קבוצה, שייכות"),
]


def _get_upcoming_events(days_ahead: int = 21) -> list[dict]:
    """מחזיר אירועים שקרובים ב-N ימים הקרובים."""
    today = datetime.now()
    upcoming = []
    for month, day, name, hint in CALENDAR_EVENTS:
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

    return {
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
    }


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

    prompt = f"""אתה פלאנר אסטרטגי של מחקר בתחום "{main_field}".

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

החזר JSON עם:
- topics: מערך של בדיוק 3 אובייקטים, כל אחד עם:
    - topic: נושא ספציפי (אנגלית לחיפוש)
    - subtopics: מערך של 3-4 נושאי משנה (אנגלית)
    - angle: הזווית הייחודית — "theoretical" / "empirical" / "practical"
- combined_title: כותרת משולבת (עברית)
- future_topics: מערך של 5-8 נושאים לעתיד (אנגלית)
- gaps_identified: מערך של פערים שזוהו (עברית)
- reasoning: הסבר קצר מדוע בחרת נושאים אלה ואיך הם מתחברים (עברית)
- content_recommendation: אובייקט עם "types" (מערך linkedin/blog/podcast) ו-"reason"

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

    combined_title = result.get("combined_title", " + ".join(t["topic"] for t in raw_topics))

    # Build subtopics_map for backward compat
    subtopics_map = {t["topic"]: t.get("subtopics", []) for t in raw_topics}
    topics_list   = [t["topic"] for t in raw_topics]

    # Save to memory
    add_to_queue(topics_list + result.get("future_topics", []))
    set_gaps(result.get("gaps_identified", []))
    mem = load_memory()
    mem["next_plan"] = {
        "topics":          raw_topics,
        "combined_title":  combined_title,
        "subtopics_map":   subtopics_map,
        "reasoning":       result.get("reasoning", ""),
        "content_recommendation": result.get("content_recommendation", {}),
    }
    save_memory(mem)

    plan = {
        "topics":       raw_topics,          # list of {topic, subtopics, angle}
        "topic":        combined_title,      # שם משולב לתצוגה
        "combined_title": combined_title,
        "subtopics_map": subtopics_map,
        "subtopics":    [],                  # backward compat
        "content_types": result.get("content_recommendation", {}).get("types", ["linkedin", "blog"]),
        "reasoning":    result.get("reasoning", ""),
    }

    print(f"  [Agent0] 3 נושאים: {' | '.join(t['topic'] for t in raw_topics)}")
    print(f"  [Agent0] כותרת משולבת: {combined_title}")
    print(f"  [Agent0] תוכן מומלץ: {plan['content_types']}")
    print(f"\n✅ Agent 0 complete\n")
    return plan
