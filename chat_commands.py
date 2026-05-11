"""
chat_commands.py — Extracted chat command handlers from agent5_project_manager.

handle_chat_command(user_input, session, auto) -> str | None
  Returns response string if a command was matched, or None to fall through
  to the general chat LLM response.
"""

import json
import re
import sys
import subprocess
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR, LINKEDIN_DIR, BLOG_DIR, PODCAST_DIR
from memory import load_memory, get_summary
from agent3_content_creator import CONTENT_TYPES
from qa_checker import run_qa, QAResult
from checkpoint import Checkpoint
from analytics import tracker


# ─────────────────────────────────────────────
# Helpers imported from agent5 (avoids circular imports)
# ─────────────────────────────────────────────

def _read_system_state() -> dict:
    """Collect current files and memory state."""
    mem = load_memory()

    papers_dir   = OUTPUT_DIR / "papers"
    articles_dir = OUTPUT_DIR / "articles"
    posts_dir    = OUTPUT_DIR / "posts"
    designs_dir  = OUTPUT_DIR / "designs"

    papers   = sorted(papers_dir.glob("*.json"),   key=lambda p: p.stat().st_mtime, reverse=True) if papers_dir.exists()   else []
    articles = sorted(articles_dir.glob("*.md"),   key=lambda p: p.stat().st_mtime, reverse=True) if articles_dir.exists() else []
    posts    = sorted(posts_dir.glob("*.txt"),     key=lambda p: p.stat().st_mtime, reverse=True) if posts_dir.exists()    else []
    designs  = sorted(designs_dir.glob("*.svg"),   key=lambda p: p.stat().st_mtime, reverse=True) if designs_dir.exists()  else []

    return {
        "memory_summary": get_summary(),
        "researched_topics": mem.get("researched_topics", [])[-5:],
        "topic_queue": mem.get("topic_queue", [])[:3],
        "gaps": mem.get("gaps", [])[:3],
        "existing_files": {
            "papers":   [p.name for p in papers[:3]],
            "articles": [p.name for p in articles[:3]],
            "posts":    [p.name for p in posts[:5]],
            "designs":  [p.name for p in designs[:5]],
        },
        "latest": {
            "papers_files":  [str(p) for p in papers[:3]],
            "article_md":    str(articles[0]) if articles else None,
            "post_ready":    next((str(p) for p in posts if "ready" in p.name), None),
        },
    }


def _explain_last_run() -> str:
    f = OUTPUT_DIR / "analytics.json"
    if not f.exists():
        return "אין ריצות קודמות."
    runs = json.loads(f.read_text(encoding="utf-8")).get("runs", [])
    if not runs:
        return "אין ריצות קודמות."
    r = runs[-1]
    lines = [
        f"ריצה אחרונה — {r.get('started_at','')[:16].replace('T',' ')}",
        f"  נושא:  {r.get('topic','?')}",
        f"  סטטוס: {'✅' if r.get('success') else '❌'}",
        f"  זמן:   {r.get('duration_s',0)/60:.1f} דקות",
    ]
    if r.get("avg_qa"):
        lines.append(f"  QA:    {r['avg_qa']}/100")
    if r.get("est_cost"):
        lines.append(f"  עלות:  ${r['est_cost']}")
    for s in r.get("steps", []):
        icon = "✅" if s.get("status") == "ok" else "⚠️"
        qa = f" QA:{s['qa_score']}" if s.get("qa_score") else ""
        lines.append(f"    {icon} {s['agent']}: {s.get('duration_s',0):.0f}s{qa}")
    for e in r.get("errors", [])[:3]:
        lines.append(f"    ❌ {e.get('agent','?')}: {e.get('error','')[:60]}")
    return "\n".join(lines)


QUEUE_FILE = OUTPUT_DIR / "priority_queue.json"

def _load_queue() -> list:
    if QUEUE_FILE.exists():
        try:
            return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []

def _save_queue(q: list):
    QUEUE_FILE.write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")

def queue_add(request: str, priority: int = 5) -> str:
    q = _load_queue()
    q.append({"request": request, "priority": priority,
              "added_at": datetime.now().isoformat(), "status": "waiting"})
    q.sort(key=lambda x: x["priority"])
    _save_queue(q)
    return f"נוסף לתור (עדיפות {priority}): {request[:60]}"

def queue_status() -> str:
    q = _load_queue()
    if not q:
        return "התור ריק."
    icons = {"waiting": "⏳", "running": "▶️", "done": "✅"}
    lines = [f"תור עדיפויות ({len(q)} פריטים):"]
    for item in q[-10:]:
        lines.append(f"  {icons.get(item['status'],'?')} [{item['priority']}] {item['request'][:55]}")
    return "\n".join(lines)


def _qa_gate(stage: str, attempt: int, execution_state: dict) -> tuple[bool, QAResult]:
    """Run QA for a stage, log result, print summary."""
    kwargs = {}
    if stage == "research":
        pf = execution_state.get("papers_file")
        kwargs["papers_file"] = Path(pf) if pf else None
    elif stage == "article":
        md = execution_state.get("article_paths", {}).get("md")
        kwargs["article_path"] = Path(md) if md else None
    elif stage in ("linkedin", "blog", "podcast"):
        dirs = {"linkedin": LINKEDIN_DIR, "blog": BLOG_DIR, "podcast": PODCAST_DIR}
        d = dirs[stage]
        files = sorted(d.glob("*"), key=lambda p: p.stat().st_mtime) if d.exists() else []
        kwargs["content_file"] = files[-1] if files else None
    else:
        return True, QAResult(True, 100, [], [], f"אין QA ל-{stage}")

    result = run_qa(stage, **kwargs)
    execution_state.setdefault("qa_log", []).append({
        "stage": stage, "attempt": attempt,
        "score": result.score, "passed": result.passed,
        "issues": result.issues, "warnings": result.warnings,
    })
    print(f"\n  {result.summary()}\n")
    return result.passed, result


LONG_RUN_THRESHOLD_MINS = 5

def _classify_intent(user_input: str) -> dict:
    """Classify user intent using claude_cli."""
    from claude_cli import ask_claude_json
    prompt = f"""המשתמש אמר: "{user_input}"

החזר JSON עם:
  action: "run_pipeline" | "content_only" | "qa_check" | "edit" | "info" | "add_paper" | "chat"
  params: dict עם פרמטרים (topic, content_types, platform, url)
  eta_mins: הערכת זמן ריצה בדקות (מספר שלם)

JSON בלבד."""
    try:
        result = ask_claude_json(prompt, max_budget=0.3)
        if "confirm_needed" not in result:
            result["confirm_needed"] = result.get("eta_mins", 0) >= LONG_RUN_THRESHOLD_MINS
        return result
    except Exception:
        return {"action": "chat", "params": {}, "eta_mins": 1, "confirm_needed": False}


# ─────────────────────────────────────────────
# Main handler
# ─────────────────────────────────────────────

def handle_chat_command(user_input: str, session: dict, auto: bool) -> str | None:
    """
    Process user chat commands.
    Returns response string if a command was matched, or None to fall through
    to the general chat LLM response.
    """
    low = user_input.strip().lower()

    # ── Quick commands ────────────────────────
    if low in ("עצור", "יציאה", "exit", "quit", "q"):
        return "__EXIT__"

    if any(w in low for w in ["מה קרה", "ריצה אחרונה", "last run"]):
        return _explain_last_run()

    if low in ("תור", "queue"):
        return queue_status()

    if low.startswith("תור "):
        return queue_add(user_input[4:].strip())

    if low in ("עזרה", "help", "?"):
        return """פקודות:

🎯 הרצה
  הרץ הכל [--parallel] [--bilingual]  — pipeline מלא (3 פלטפורמות)
  פוסט linkedin / blog / podcast       — רק תוכן
  המשך / resume                        — המשך מ-checkpoint אחרון

📊 מידע
  מה יש / סטטוס                        — קבצים קיימים
  מה קרה / ריצה אחרונה                — פירוט ריצה אחרונה
  analytics / דוח                       — דוח ביצועים מצטבר
  dashboard / דאשבורד                  — לוח בקרה בדפדפן
  checkpoints                           — סטטוס checkpoints
  לוגים / logs [N]                     — N שורות אחרונות מהלוג

✏️ עריכה ואיכות
  בדוק איכות / qa                      — QA על קבצים קיימים
  ערוך [article/linkedin/blog/podcast] — הגהה
  בדיקות / tests                        — הרץ tests.py --quick

📚 ידע
  ביבליוגרפיה / bib [query]            — ניהול/חיפוש מקורות
  חפש [מילה] / היסטוריה                — חפש בתוכן שנוצר
  סיכום שבועי                          — סיכום 7 ימים
  הוסף מאמר [URL]                      — הוסף מקור ידני
  קונטקסט / context                    — הצג/עדכן קונטקסט אישי
  רעיונות [N] / ideas                  — הצע N רעיונות מחקר מהקורפוס הקיים
  קשת / arc                            — מצב הקשת הפעילה + פוסט הבא מומלץ
  קשתות / detect arcs                  — מקבץ פוסטים קיימים לקשתות

📈 ביצועים ולמידה
  ביצועים / performance / מה עבד       — דוח performance log
  הוסף ביצוע                           — הזנת לייקים/תגובות
  תובנות / insights                    — Claude מנתח מגמות
  דפוסים גרועים / anti-patterns        — דפוסי פתיחה/מבנה שכשלו 2+ פעמים
  כיול / calibration                   — מתאם בין QA חזוי ל-engagement בפועל
  קול / voice drift / voice analysis   — ניתוח סחף סגנוני ב-30 פוסטים האחרונים
  התפתחות / evolution [N] [הצעות]      — השוואת חלון אחרון לקודם + הצעות לעדכון פרופיל
  רפלקציה / reflection / מה למדנו     — ניתוח עצמי + המלצות לשיפור

⚡ סתירות בקורפוס
  סתירות / conflicts                   — סתירות במאמרים האחרונים + verdict לכל אחת

♻️ Repurposing
  ממיר רשימה                           — קבצים זמינים להמרה
  ממיר [from] [to]                     — דוגמה: ממיר blog linkedin

💬 תגובות (Reply Drafter)
  תגובה <שם פוסט> "<תגובה>"           — נסח 3 טיוטות תגובה בקול של פז
  דוגמה: תגובה דיאלוג "מה עם גילאים צעירים?"

📂 ניהול קבצים
  קבצים / מצב קבצים                    — דוח: drafts/ready/published/archive
  סדר / ארגן                           — מעביר drafts שעברו QA → ready
  פרסמתי linkedin/blog/podcast        — מסמן כפורסם (ready → published)
  ארכיון                               — מעביר ישנים מ-30 יום → archive

📋 תור
  תור                                  — הצג תור עדיפויות
  תור [בקשה]                           — הוסף לתור

🚪 יציאה
  עצור / exit / q"""

    if low in ("סטטוס", "מה יש", "status", "מה קיים", "רשימה"):
        state = _read_system_state()
        files = state.get("existing_files", {})
        return (
            f"הנה מה שיש:\n"
            f"  מחקרים:  {len(files.get('papers', []))} קבצים\n"
            f"  מאמרים:  {len(files.get('articles', []))} קבצים\n"
            f"  פוסטים:  {len(files.get('posts', []))} קבצים\n"
            f"  עיצובים: {len(files.get('designs', []))} קבצים"
        )

    if low in ("analytics", "דוח"):
        try:
            tracker.print_report()
        except Exception:
            pass
        return "הדוח הוצג."

    if any(w in low for w in ("dashboard", "דאשבורד", "לוח בקרה", "לוח-בקרה")):
        from dashboard import build_dashboard
        build_dashboard(open_browser=True)
        return "דאשבורד נפתח בדפדפן."

    if low in ("checkpoints", "checkpoint"):
        Checkpoint.print_status()
        return ""

    if any(w in low for w in ["סיכום שבועי", "סיכום", "weekly", "summary"]):
        from weekly_summary import print_summary, save_summary
        print_summary()
        if "שמור" in low or "save" in low:
            save_summary()
        return ""

    if any(w in low for w in ["ביבליוגרפיה", "bib", "מקורות", "references"]):
        from bibliography import search as bib_search, stats as bib_stats, auto_update
        if "חפש" in low or "search" in low:
            words = low.replace("חפש", "").replace("search", "").replace("ביבליוגרפיה", "").strip()
            if words:
                results = bib_search(words)
                lines = [f"🔍 תוצאות עבור '{words}': {len(results)}"]
                for r in results[:8]:
                    cit = r.get("citation_count", 0)
                    pdf = " 📄" if r.get("pdf_url") else ""
                    lines.append(f"  [{r.get('year','?')}] "
                                 f"{r.get('title','')[:50]} (×{cit}){pdf}")
                return "\n".join(lines)
            return "מה לחפש? (למשל: ביבליוגרפיה חפש belonging)"
        auto_update()
        return bib_stats()

    if any(w in low for w in ["קונטקסט", "context"]):
        from context_update import show_context
        show_context()
        return ""

    if any(w in low for w in ["היסטוריה", "history", "חפש", "search", "מה כתבנו"]):
        from history import search_content, print_recent
        for prefix in ["חפש ", "search ", "היסטוריה ", "מה כתבנו על "]:
            if low.startswith(prefix):
                query = low[len(prefix):].strip()
                if query:
                    results = search_content(query)
                    if results:
                        lines = [f"🔍 {len(results)} תוצאות עבור '{query}':"]
                        for r in results[:6]:
                            icon = {"article":"📝","linkedin":"💼","blog":"📰","podcast":"🎙️","research":"📚"}.get(r["type"],"📄")
                            date = r["date"].strftime("%d/%m")
                            lines.append(f"  {icon} [{date}] {r['name']}")
                            lines.append(f"     {r['snippet'][:60]}")
                        return "\n".join(lines)
                    return f"לא נמצא תוכן עבור '{query}'"
        print_recent(8)
        return ""

    # ── Long-form Arc Tracker ──
    # Triggers: "קשת" / "arc" / "מצב קשת" — show current arc + next post recommendation.
    # Also: "קשתות" / "arcs detect" — re-cluster posts into arcs.
    _arc_status_triggers = ("קשת", "arc", "מצב קשת", "arc status")
    _arc_detect_triggers = ("קשתות", "detect arcs", "arcs detect", "זהה קשתות")
    if low in _arc_detect_triggers:
        from arc_tracker import detect_arcs
        arcs = detect_arcs()
        if not arcs:
            return "לא נמצאו קשתות (צריך לפחות 3 פוסטים בנושא קרוב)."
        lines = [f"🔍 זוהו {len(arcs)} קשתות:"]
        for a in arcs:
            done = "✅" if a.get("is_complete") else "⏳"
            lines.append(f"  {done} {a['arc_id']}  ({a['post_count']:2d} פוסטים)  "
                         f"{a['anchor_theme'][:60]}")
        return "\n".join(lines)
    if low in _arc_status_triggers:
        from arc_tracker import format_arc_status_for_chat
        return format_arc_status_for_chat()

    # ── Research ideas (corpus-grounded) ──
    if any(w in low for w in ["רעיונות", "ideas", "הצע רעיונות", "propose"]):
        from agent0_planner import propose_research_ideas
        n = 5
        parts = low.split()
        for tok in parts:
            if tok.isdigit():
                n = max(1, min(10, int(tok)))
                break
        result = propose_research_ideas(max_ideas=n)
        ideas = result.get("ideas", [])
        if not ideas:
            err = result.get("error", "לא הוחזרו רעיונות")
            return f"⚠️ {err}"
        lines = [f"💡 {len(ideas)} רעיונות מחקר (מעוגנים בקורפוס):\n"]
        for i, idea in enumerate(ideas, 1):
            if not isinstance(idea, dict):
                continue
            anchors = idea.get("anchor_sources", [])
            if isinstance(anchors, list):
                anchors_str = ", ".join(str(a) for a in anchors[:3])
            else:
                anchors_str = ""
            lines.append(f"{i}. {idea.get('research_question', '')}")
            lines.append(f"   💭 {idea.get('why_interesting', '')[:120]}")
            if anchors_str:
                lines.append(f"   📚 {anchors_str}")
            angle = idea.get("publication_angle", "")
            if angle:
                lines.append(f"   ✍️  {angle[:120]}")
            lines.append("")
        saved = result.get("saved_to")
        if saved:
            lines.append(f"📁 נשמר ב: {Path(saved).name}")
        return "\n".join(lines)

    # ── File organizer ──
    if low in ("סדר", "organize", "ארגן", "ארגן קבצים"):
        from file_organizer import organize_drafts, status
        result = organize_drafts()
        moved_lines = [f"  {p}: {n}" for p, n in result["moved"].items() if n]
        msg = "✅ הועברו ל-ready:\n" + "\n".join(moved_lines) if moved_lines else "אין קבצים חדשים להעביר."
        return msg + "\n" + status()

    if low in ("קבצים", "files", "מצב קבצים"):
        from file_organizer import status
        return status()

    if low.startswith("פרסמתי ") or low.startswith("published "):
        from file_organizer import mark_published
        parts = user_input.split()
        if len(parts) < 2:
            return "שימוש: פרסמתי linkedin / blog / podcast"
        platform = parts[1].lower()
        if platform not in ("linkedin", "blog", "podcast"):
            return f"פלטפורמה לא חוקית: {platform}"
        result = mark_published(platform)
        if result:
            return f"✅ סומן כפורסם: {result.name}"
        return f"⚠️ אין קובץ ב-ready/{platform} — הרץ 'סדר' קודם"

    if low in ("ארכיון", "archive", "נקה ישנים"):
        from file_organizer import archive_old
        result = archive_old(30)
        total = sum(result.values())
        return f"📦 הועברו לארכיון ({total} קבצים מ-30+ יום)"

    # ── Performance log ──
    if any(w in low for w in ["ביצועים", "performance", "מה עבד"]):
        from performance_log import show_report
        show_report()
        return ""

    if low.startswith("הוסף ביצוע") or low == "perf-add":
        from performance_log import add_entry_interactive
        add_entry_interactive()
        return ""

    # Quick performance add: "ביצועים linkedin <title> likes=47 comments=12 shares=3 reach=1200"
    if low.startswith("ביצועים ") and "=" in low:
        from performance_log import add_entry_quick
        try:
            parts = user_input.split(maxsplit=2)
            platform = parts[1].lower()
            rest = parts[2]
            # Parse key=val tokens
            metrics = {}
            title_parts = []
            for tok in rest.split():
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    if v.isdigit():
                        metrics[k] = int(v)
                else:
                    title_parts.append(tok)
            title = " ".join(title_parts) or "(no title)"
            add_entry_quick(
                platform=platform, title=title,
                likes=metrics.get("likes", 0),
                comments=metrics.get("comments", 0),
                shares=metrics.get("shares", 0),
            )
            # Add reach to the JSON manually since add_entry_quick doesn't take it
            from performance_log import _load, _save
            data = _load()
            if data and metrics.get("reach"):
                data[-1]["metrics"]["reach"] = metrics["reach"]
                _save(data)
            return f"✅ נוסף: [{platform}] {title} — likes={metrics.get('likes',0)}, comments={metrics.get('comments',0)}, reach={metrics.get('reach',0)}"
        except (IndexError, ValueError) as e:
            return f"שימוש: ביצועים linkedin <שם> likes=N comments=N shares=N reach=N\nשגיאה: {e}"

    if low in ("תובנות", "insights"):
        subprocess.run([sys.executable, "performance_log.py", "--insights"])
        return ""

    # ── Anti-patterns memory ──
    _anti_triggers = ("דפוסים גרועים", "anti-patterns", "anti patterns",
                      "antipatterns", "דפוסי כשל", "patterns to avoid")
    if low in _anti_triggers or any(low.startswith(t + " ") for t in _anti_triggers):
        from anti_patterns import get_anti_patterns
        # Optional min_failures override: "דפוסים גרועים 3" → min_failures=3
        min_failures = 2
        for tok in low.split()[1:]:
            if tok.isdigit():
                min_failures = max(2, min(20, int(tok)))
                break
        patterns = get_anti_patterns(min_failures=min_failures)
        if not patterns:
            return (
                f"אין דפוסים גרועים שחוזרים (≥{min_failures} כשלונות). "
                f"זה טוב — אבל ייתכן שגם פשוט אין מספיק רשומות עדיין."
            )
        lines = [f"📕 דפוסים גרועים ({len(patterns)} סה\"כ, מינימום {min_failures} כשלונות):\n"]
        for p in patterns[:10]:
            lines.append(f"  ✗ {p['pattern']}")
            lines.append(f"     כשלונות: {p['failures']}  |  {p['avoid_reason']}")
            if p.get("examples"):
                lines.append(f"     דוגמאות: {', '.join(p['examples'][:2])}")
            lines.append("")
        return "\n".join(lines)

    # ── Calibration ──
    _calib_triggers = ("כיול", "calibration", "calibrate", "כיול ביצועים")
    if low in _calib_triggers or any(low.startswith(t + " ") for t in _calib_triggers):
        from calibration import calibrate, adjustment_recommendation
        result = calibrate()
        verdict_he = {
            "well_calibrated":  "מכויל היטב ✅",
            "overconfident":    "Overconfident ⚠️",
            "underconfident":   "Underconfident ⚠️",
            "no_signal":        "אין מספיק נתונים",
        }.get(result["verdict"], result["verdict"])
        lines = [
            "📐 דוח כיול:",
            f"  זיווגים:    {result['samples']}",
            f"  מתאם:       {result['correlation']:+.2f}  (-1..+1)",
            f"  סטייה:      {result['drift']:.2%}",
            f"  סף QA לפוסטים מובילים: {result['qa_threshold_for_high_engagement']}/100",
            f"  מסקנה:      {verdict_he}",
            "",
            adjustment_recommendation(),
        ]
        return "\n".join(lines)

    # ── Voice drift analysis ──
    _voice_triggers = ("קול", "voice", "voice drift", "voice analysis",
                       "סחף", "סחף קולי", "ניתוח קול")
    if low in _voice_triggers or any(
        low.startswith(t + " ") for t in _voice_triggers
    ):
        from voice_drift import analyze_voice_drift, format_report
        # Optional integer suffix: "קול 50" → top_n=50
        n = 30
        for tok in low.split()[1:]:
            if tok.isdigit():
                n = max(3, min(200, int(tok)))
                break
        result = analyze_voice_drift(top_n=n)
        return format_report(result)

    # ── Voice evolution (recent vs older window) ──
    _evolution_triggers = ("התפתחות", "evolution", "voice evolution",
                           "התפתחות קול", "voice-evolution")
    if low in _evolution_triggers or any(
        low.startswith(t + " ") for t in _evolution_triggers
    ):
        from voice_evolution import (
            analyze_evolution,
            propose_voice_updates,
            format_evolution_report,
            format_proposals_report,
        )
        # Optional window_days suffix: "התפתחות 60" → window_days=60
        wd = 90
        want_propose = False
        for tok in low.split()[1:]:
            if tok.isdigit():
                wd = max(7, min(720, int(tok)))
            elif tok in ("propose", "proposals", "suggest", "הצעות"):
                want_propose = True
        result = analyze_evolution(window_days=wd)
        text = format_evolution_report(result)
        if want_propose and not result.get("insufficient_data"):
            proposal = propose_voice_updates(window_days=wd)
            text += "\n\n" + format_proposals_report(proposal)
        return text

    # ── Reflective loop ──
    _reflection_triggers = ("רפלקציה", "reflection", "מה למדנו",
                            "ניתוח עצמי", "self-reflection")
    if low in _reflection_triggers or any(
        low.startswith(t + " ") for t in _reflection_triggers
    ):
        from reflective_loop import (
            run_reflection,
            format_reflection_report,
            save_reflection,
        )
        # Optional min_posts override: "רפלקציה 5" → min_posts=5
        min_posts = 10
        for tok in low.split()[1:]:
            if tok.isdigit():
                min_posts = max(3, min(200, int(tok)))
                break
        report = run_reflection(min_posts=min_posts)
        text = format_reflection_report(report)
        if not report.get("skipped"):
            try:
                path = save_reflection(report)
                text += f"\n\n[נשמר ב: {path}]"
            except Exception as e:
                text += f"\n\n[שמירה נכשלה: {e}]"
        return text

    # ── Comment drafter (replies in Paz's voice) ──
    if low.startswith("תגובה ") or low.startswith("reply "):
        from comment_drafter import (
            draft_replies, save_drafts, find_post_by_name, _read_post
        )
        # Strip leading trigger word ("תגובה " or "reply ")
        rest = user_input.strip()
        for prefix in ("תגובה ", "reply "):
            if rest.lower().startswith(prefix.lower()):
                rest = rest[len(prefix):].strip()
                break

        # Parse: "<post-name-or-path> <comment>" — comment is usually quoted.
        comment_text = ""
        post_token = ""
        # Try quoted comment first (supports "..." or '...' or “...”)
        m = re.search(r'(["\'“”‘’])(.+?)\1', rest)
        if m:
            post_token = rest[:m.start()].strip()
            comment_text = m.group(2).strip()
        else:
            # Fallback: split on first whitespace — first token = post name
            parts = rest.split(None, 1)
            if len(parts) == 2:
                post_token, comment_text = parts[0], parts[1].strip()

        if not post_token or not comment_text:
            return ('שימוש: תגובה <שם פוסט> "<התגובה שהתקבלה>"\n'
                    'דוגמה: תגובה דיאלוג "אני מסכים אבל מה לגבי גילאים צעירים?"')

        # Resolve post: direct path first, then by name fragment
        post_path = Path(post_token)
        if not post_path.exists():
            match = find_post_by_name(post_token)
            if not match:
                return f"⚠️ לא נמצא פוסט לפי '{post_token}'"
            post_path = match

        post_text = _read_post(post_path)
        if not post_text.strip():
            return f"⚠️ הפוסט ריק או לא קריא: {post_path}"

        print(f"  📄 פוסט מקור: {post_path.name}")
        print(f"  💬 מנסח 3 תגובות בקול של פז...")
        drafts = draft_replies(post_text, comment_text)
        saved_to = save_drafts(post_path, drafts)

        items = drafts.get("drafts", [])
        if not items:
            err = drafts.get("error", "לא נוצרו טיוטות")
            return f"⚠️ {err}"

        tone_label = {
            "engage": "מסכים+מרחיב",
            "challenge": "חולק בכבוד",
            "question_back": "מחזיר שאלה",
        }
        out = [f"💬 תגובות מוצעות לפוסט: {post_path.name}"]
        out.append(f"   תגובה שהתקבלה: {comment_text[:100]}")
        out.append("")
        for i, d in enumerate(items, 1):
            tone = d.get("tone", "?")
            label = tone_label.get(tone, tone)
            length = d.get("length", 0)
            out.append(f"  {i}. [{label}] ({length} תווים)")
            out.append(f"     {d.get('reply','')}")
            out.append("")
        out.append(f"📁 נשמר ב: {saved_to}")
        return "\n".join(out)

    # ── Conflicts (corpus contradictions) ──
    _conflict_triggers = ("סתירות", "conflicts", "קונפליקטים", "סתירה")
    if low in _conflict_triggers or any(
        low.startswith(t + " ") for t in _conflict_triggers
    ):
        from conflict_resolver import (
            find_conflicts, weigh_evidence, format_conflict_report,
        )
        from config import PAPERS_DIR
        candidates = sorted(PAPERS_DIR.glob("*analysis*.json"),
                            key=lambda p: p.stat().st_mtime) \
            if PAPERS_DIR.exists() else []
        if not candidates:
            return ("⚠️ לא נמצא קובץ ניתוח (*_analysis_*.json). "
                    "הרץ קודם paper_analyzer (Agent 1.7).")
        latest = candidates[-1]
        try:
            data = json.loads(latest.read_text(encoding="utf-8"))
        except Exception as e:
            return f"⚠️ קריאת {latest.name} נכשלה: {e}"

        profiles = data.get("profiles") or []
        # Prefer pre-computed corpus_conflicts if present, else recompute on the fly
        rels = data.get("relationships") or {}
        cached = rels.get("corpus_conflicts") or {}
        if cached.get("conflicts"):
            count = cached.get("count", len(cached["conflicts"]))
            lines = [
                f"⚡ סתירות בקורפוס ({count}) — קובץ: {latest.name}",
                "",
            ]
            for i, c in enumerate(cached["conflicts"][:10], 1):
                lines.append(f"{i}. נושא: {c.get('topic','?')}")
                lines.append(f"   A: {c.get('paper_a_title','?')}")
                lines.append(f"   B: {c.get('paper_b_title','?')}")
                lines.append(f"   טבע: {c.get('nature_of_conflict','')[:140]}")
                if c.get("self_conflict"):
                    lines.append("   סוג: סתירה פנימית במאמר עצמו")
                else:
                    a = c.get("a_score", "?")
                    b = c.get("b_score", "?")
                    w = c.get("winner", "?")
                    verdict = {"a": "A מנצח", "b": "B מנצח",
                               "tie": "תיקו — הצג שני הצדדים"}.get(w, w)
                    lines.append(f"   ניקוד: A={a}/100  B={b}/100  →  {verdict}")
                lines.append("")
            return "\n".join(lines)

        # Fallback: compute now from profiles
        if not profiles:
            return f"⚠️ אין profiles בקובץ {latest.name}."
        conflicts = find_conflicts(profiles)
        if not conflicts:
            return (f"✅ לא נמצאו סתירות בקורפוס "
                    f"({len(profiles)} מאמרים) — {latest.name}")
        lines = [f"⚡ סתירות ({len(conflicts)}) — {latest.name}", ""]
        for i, c in enumerate(conflicts[:10], 1):
            p_a = c.get("paper_a") or {}
            p_b = c.get("paper_b") or {}
            lines.append(f"{i}. נושא: {c.get('topic','?')}")
            lines.append(f"   טבע: {c.get('nature_of_conflict','')[:140]}")
            if p_a is p_b:
                lines.append("   סוג: סתירה פנימית במאמר עצמו")
            else:
                v = weigh_evidence(p_a, p_b)
                verdict = {"a": "A מנצח", "b": "B מנצח",
                           "tie": "תיקו — הצג שני הצדדים"}.get(v["winner"], v["winner"])
                lines.append(
                    f"   ניקוד: A={v['a_score']}/100  B={v['b_score']}/100  →  {verdict}"
                )
            lines.append("")
        return "\n".join(lines)

    # ── Repurposing ──
    if low in ("ממיר רשימה", "repurpose list", "רשימת ממיר"):
        from repurpose_tool import list_available
        list_available()
        return ""

    if low.startswith("ממיר ") or low.startswith("repurpose "):
        parts = user_input.split()
        if len(parts) >= 3:
            src, tgt = parts[1], parts[2]
            from repurpose_tool import repurpose, _find_latest
            f = _find_latest(src)
            if f:
                repurpose(f, tgt)
                return f"הומר {src} → {tgt}"
            return f"לא נמצא קובץ אחרון ל-{src}"
        return "שימוש: ממיר [blog/linkedin/podcast] [יעד]"

    # ── Logs ──
    if low.startswith("לוגים") or low.startswith("logs"):
        n = 50
        parts = low.split()
        if len(parts) > 1 and parts[1].isdigit():
            n = int(parts[1])
        log_file = OUTPUT_DIR / "moki.log"
        if log_file.exists():
            lines = log_file.read_text(encoding="utf-8").splitlines()[-n:]
            return "\n".join(lines)
        return "אין לוגים."

    # ── Tests ──
    if low in ("בדיקות", "tests", "test"):
        subprocess.run([sys.executable, "tests.py", "--quick"])
        return ""

    # ── Resume ──
    if low in ("המשך", "resume", "חידוש"):
        ckpt = Checkpoint.latest()
        if ckpt:
            print(f"  ♻️ ממשיך מ: {ckpt.summary()}")
            planner_data = ckpt.get("planner") or {}
            title = planner_data.get("combined_title", "continue") if isinstance(planner_data, dict) else "continue"
            subprocess.run([sys.executable, "orchestrator.py", title, "--resume"])
            return ""
        return "אין checkpoint לחידוש."

    if any(w in low for w in ["qa", "איכות", "בדוק איכות"]):
        state = _read_system_state()
        latest = state.get("latest") or {}
        latest_papers = latest.get("papers_files", [])
        tmp_state = {
            "papers_file": latest_papers[-1] if latest_papers else None,
            "article_paths": {"md": latest["article_md"]} if latest.get("article_md") else {},
            "qa_log": [], "errors": [],
        }
        print("  בודק איכות קבצים קיימים...")
        if tmp_state["papers_file"]:
            _qa_gate("research", 1, tmp_state)
        if tmp_state["article_paths"].get("md"):
            _qa_gate("article", 1, tmp_state)
        for platform in ["linkedin", "blog", "podcast"]:
            d = {"linkedin": LINKEDIN_DIR, "blog": BLOG_DIR, "podcast": PODCAST_DIR}[platform]
            files = sorted(d.glob("*"), key=lambda p: p.stat().st_mtime) if d.exists() else []
            if files:
                _qa_gate(platform, 1, tmp_state)
        return "בדיקת QA הסתיימה."

    # ── Intent classification ─────────────────
    intent = _classify_intent(user_input)
    action = intent.get("action", "chat")
    params = intent.get("params", {})

    if action == "run_pipeline":
        from agent5_project_manager import run_project_manager
        topic = params.get("topic") or session.get("topic", "חינוך בלתי פורמלי")
        valid_types = {"linkedin", "blog", "podcast"}
        raw_types = params.get("content_types") or ["linkedin", "blog", "podcast"]
        content_types = [t for t in raw_types if t in valid_types] or ["linkedin", "blog", "podcast"]

        ct_str = " + ".join(CONTENT_TYPES.get(t, t) for t in content_types)
        eta = intent.get("eta_mins", 10)
        print(f"\n  מריץ pipeline — {topic} | {ct_str} | ~{eta} דק' משוער...")

        if eta >= LONG_RUN_THRESHOLD_MINS and not auto:
            try:
                ok = input(f"  זה ייקח ~{eta} דקות. להמשיך? (Enter=כן, n=לא): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ok = ""
            if ok in {"n", "no", "לא", "בטל", "cancel"}:
                return "בוטל."

        req = f"הרץ pipeline: נושא={topic} תוכן={' '.join(content_types)}"
        run_project_manager(req, auto_approve=True)
        return "Pipeline הסתיים."

    elif action == "content_only":
        from agent5_project_manager import run_project_manager
        valid_types = {"linkedin", "blog", "podcast"}
        raw_types = params.get("content_types") or ["linkedin", "blog", "podcast"]
        content_types = [t for t in raw_types if t in valid_types] or ["linkedin", "blog", "podcast"]
        print(f"\n  יוצר תוכן — {', '.join(content_types)}...")
        req = f"צור תוכן: {' '.join(content_types)} ממאמר קיים"
        run_project_manager(req, auto_approve=True)
        return "תוכן נוצר."

    elif action == "edit":
        platform = params.get("platform", "article")
        print(f"\n  מגיה {platform}...")
        try:
            from agent_editor import edit_article, edit_content
            from config import ARTICLES_DIR
            if platform == "article":
                mds = sorted(ARTICLES_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime)
                if mds:
                    edit_article({"md": mds[-1], "docx": mds[-1].with_suffix(".docx")})
                    return f"מאמר נערך: {mds[-1].name}"
                return "לא נמצא מאמר לעריכה."
            else:
                r = edit_content(platform)
                return f"{platform} נערך." if r.get("file") else f"לא נמצא קובץ ל-{platform}"
        except Exception as e:
            return f"שגיאה בעריכה: {e}"

    elif action == "add_paper":
        url = params.get("url", "")
        topic = params.get("topic", "חינוך בלתי פורמלי")
        if url:
            from add_paper import add_from_url
            add_from_url(url, topic)
            return f"מאמר נוסף מ-{url[:60]}"
        return "שלח URL להוספת מאמר."

    # No command matched — return None to fall through to general chat
    return None
