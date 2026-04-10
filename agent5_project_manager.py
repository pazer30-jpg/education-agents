"""
Agent 5 — Project Manager + QA + Loop Detector
  - מקבל בקשה בשפה חופשית
  - בונה תוכנית ושואל אישור
  - מריץ כל שלב + בדיקת QA אחריו
  - מזהה תקיעות ולולאות
  - מנסה לתקן אוטומטית עד MAX_RETRIES
  - מדווח סטטוס מלא בסוף

Usage:
  python agent5_project_manager.py "כתוב פוסט LinkedIn על שייכות"
  python agent5_project_manager.py "הרץ הכל על נושא חוסן" --auto
  python agent5_project_manager.py "מה יש לי כרגע?" --auto
  python agent5_project_manager.py --chat
"""

import json
import sys
import time
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR, LINKEDIN_DIR, BLOG_DIR, PODCAST_DIR
from claude_cli import ask_claude, ask_claude_json
from memory import load_memory, get_summary, record_research, record_article, record_content
from agent3_content_creator import CONTENT_TYPES
from qa_checker import run_qa, LoopDetector, QAResult
from checkpoint import Checkpoint
from analytics import tracker
from logger import log

MAX_RETRIES = 2   # מספר ניסיונות חוזרים לפני עצירה


# ─────────────────────────────────────────────
# Agents registry
# ─────────────────────────────────────────────

_loop = LoopDetector()

AGENTS = {
    "planner": {
        "id": "0", "name": "Agent 0 — Planner",
        "desc": "בוחר נושא הבא לחקור לפי זיכרון המערכת",
        "emoji": "🧠",
    },
    "researcher": {
        "id": "1", "name": "Agent 1 — Researcher",
        "desc": "שולף מאמרים אקדמיים מ-Semantic Scholar",
        "emoji": "🔍",
    },
    "writer": {
        "id": "2", "name": "Agent 2 — Writer",
        "desc": "כותב מאמר אקדמי מלא (.md + .docx)",
        "emoji": "✍️",
    },
    "content": {
        "id": "3", "name": "Agent 3 — Content Creator",
        "desc": "יוצר LinkedIn / בלוג / פודקאסט בקולו של פז",
        "emoji": "✨",
    },
    "designer": {
        "id": "4", "name": "Agent 4 — Designer",
        "desc": "מעצב ויזואלים: cover / banner / podcast cover / quote card",
        "emoji": "🎨",
    },
}


# ─────────────────────────────────────────────
# Memory sync — retroactively fill memory from disk
# ─────────────────────────────────────────────

def sync_memory_from_disk():
    """סנכרן זיכרון מהקבצים הקיימים בדיסק (פעם אחת, אם הזיכרון ריק)."""
    import json as _json
    from memory import load_memory, save_memory

    mem = load_memory()
    papers_dir   = OUTPUT_DIR / "papers"
    articles_dir = OUTPUT_DIR / "articles"

    changed = False

    # Scan paper JSONs
    if papers_dir.exists():
        for pf in sorted(papers_dir.glob("*.json"), key=lambda p: p.stat().st_mtime):
            try:
                with open(pf, encoding="utf-8") as f:
                    data = _json.load(f)
                topic = data.get("topic", pf.stem) if isinstance(data, dict) else pf.stem
                papers = data.get("papers", data) if isinstance(data, dict) else data
                if topic not in mem["researched_topics"]:
                    mem["researched_topics"].append(topic)
                    changed = True
                for p in papers:
                    pid = p.get("paperId") or p.get("title", "")[:60]
                    if pid and pid not in mem["papers"]:
                        mem["papers"][pid] = {
                            "title": p.get("title"),
                            "year": p.get("year"),
                            "topic": topic,
                        }
                        changed = True
            except Exception:
                pass

    # Scan article MDs
    if articles_dir.exists():
        for mf in sorted(articles_dir.glob("*.md"), key=lambda p: p.stat().st_mtime):
            entry = {"topic": mf.stem, "paths": {"md": str(mf)}, "created_at": ""}
            if not any(a.get("paths", {}).get("md") == str(mf) for a in mem["articles"]):
                mem["articles"].append(entry)
                changed = True

    if changed:
        save_memory(mem)
        print(f"  [Memory] סנכרון: {len(mem['researched_topics'])} נושאים, "
              f"{len(mem['papers'])} מאמרים, {len(mem['articles'])} כתבות")


# ─────────────────────────────────────────────
# System state reader
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
            "papers_files":  [str(p) for p in papers[:3]],   # עד 3 קבצים אחרונים
            "article_md":    str(articles[0]) if articles else None,
            "post_ready":    next((str(p) for p in posts if "ready" in p.name), None),
        },
    }


# ─────────────────────────────────────────────
# Planner: ask Claude to create execution plan
# ─────────────────────────────────────────────

def _create_plan(request: str, state: dict) -> dict:
    """Ask Claude to decide what to run."""
    agents_desc = "\n".join(
        f'  "{k}": {v["desc"]}' for k, v in AGENTS.items()
    )

    prompt = f"""אתה מנהל פרויקט של מערכת בוטים לחינוך. שמך מוקי.

בקשת המשתמש: "{request}"

מצב המערכת הנוכחי:
{json.dumps(state, ensure_ascii=False, indent=2)}

הבוטים הזמינים:
{agents_desc}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ארכיטקטורת הפייפליין — חובה להבין לפני תכנון:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

פייפליין מלא (כשביקשו "הכל" / "אוטונומי"):
  1. planner  — בוחר 3 נושאים בבת אחת
  2. researcher — חוקר את כל 3 הנושאים (צעד אחד, רץ פנימית 3 פעמים)
  3. writer  — כותב מאמר אחד משולב מ-3 המחקרים
  4. content  — יוצר LinkedIn + בלוג + פודקאסט מהמאמר המשולב
  (Agent 4 Designer מופעל אוטומטית בסוף Agent 3 — לא צריך לתכנן)

חשוב מאד:
- researcher ו-writer הם צעד אחד כל אחד (לא לפצל לפי נושא)
- researcher מקבל 3 נושאים מה-planner ורץ פנימית 3 פעמים
- writer כותב מאמר אחד קוהרנטי מכל המחקרים

פייפליין חלקי (כשביקשו רק תוכן / יש קבצים קיימים):
  - אם יש מאמר קיים → ישירות content
  - אם יש מחקר קיים → writer → content
  - designer רק אם ביקשו במפורש

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
כללי:
- use_existing=true אם יש קבצים רלוונטיים קיימים
- content_types לפי הבקשה (ברירת מחדל: linkedin + blog)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

החזר JSON עם:
- summary: תיאור קצר (עברית)
- estimated_time: זמן משוער
- steps: מערך צעדים, כל אחד עם:
  - agent: "planner" / "researcher" / "writer" / "content" / "designer"
  - action: מה עושים (עברית)
  - topics: מערך נושאים (לresearcher/writer — מ-plan של planner)
  - content_types: מערך מתוך ["linkedin", "blog", "podcast"] בלבד! אין ערכים אחרים!
  - design_types: ["linkedin_cover", "blog_banner", "podcast_cover"] (לdesigner)
  - use_existing: true/false
  - optional: true/false

החזר JSON בלבד."""

    return ask_claude_json(prompt, max_budget=0.5)


# ─────────────────────────────────────────────
# Plan display
# ─────────────────────────────────────────────

def _show_plan(plan: dict):
    print(f"\n📋 תוכנית: {plan.get('summary', '')}")
    print(f"⏱  זמן משוער: {plan.get('estimated_time', '?')}\n")
    for i, step in enumerate(plan.get("steps", []), 1):
        agent_info = AGENTS.get(step.get("agent", ""), {})
        optional = " (אופציונלי)" if step.get("optional") else ""
        use_ex   = " [קובץ קיים]" if step.get("use_existing") else ""
        print(f"  {i}. {agent_info.get('emoji','')} {agent_info.get('name', step.get('agent',''))}{optional}{use_ex}")
        print(f"     → {step.get('action', '')}")
        if step.get("content_types"):
            types_heb = [CONTENT_TYPES.get(t, t) for t in step["content_types"]]
            print(f"     → תוכן: {', '.join(types_heb)}")
    print()


# ─────────────────────────────────────────────
# Step executor
# ─────────────────────────────────────────────

STEP_TIMEOUT = 1200  # 20 minutes max per step

def _execute_step(step: dict, execution_state: dict) -> str:
    agent = step.get("agent")
    t0    = time.time()
    info  = AGENTS.get(agent, {})
    print(f"\n  {info.get('emoji','▶')} מפעיל {info.get('name', agent)}...")

    try:
        if agent == "planner":
            from agent0_planner import run_planner
            topic = step.get("topic", "חינוך בלתי פורמלי")
            hints = step.get("subtopics", [])
            plan  = run_planner(topic, hints)
            execution_state["last_plan"] = plan
            raw_topics = [t for t in plan.get("topics", []) if t is not None]
            topic_names = [
                t.get("topic", "?") if isinstance(t, dict) else str(t)
                for t in raw_topics
            ]
            return f"Planner החליט: {plan.get('combined_title','')} | {' | '.join(topic_names)}"

        elif agent == "researcher":
            if step.get("use_existing") and execution_state.get("papers_files"):
                pfs = execution_state["papers_files"]
                print(f"    ↩️  משתמש בקיים: {len(pfs)} קבצים")
                return f"קבצים קיימים: {len(pfs)}"
            from agent1_researcher import run_researcher
            last_plan  = execution_state.get("last_plan", {})
            raw_topics = step.get("topics") or last_plan.get("topics") or [
                {"topic": step.get("topic", "non-formal education"), "subtopics": [], "angle": "general"}
            ]
            # Ensure raw_topics is a list of valid items
            if not isinstance(raw_topics, list):
                raw_topics = [{"topic": str(raw_topics), "subtopics": [], "angle": "general"}]
            raw_topics = [t for t in raw_topics if t is not None]
            if not raw_topics:
                return "❌ אין נושאים לחקירה — הרץ planner קודם"

            papers_files = []
            for t_info in raw_topics:
                if isinstance(t_info, dict):
                    t_topic = t_info.get("topic", "non-formal education")
                    t_subs  = t_info.get("subtopics") or []
                elif isinstance(t_info, str):
                    t_topic = t_info
                    t_subs  = last_plan.get("subtopics_map", {}).get(t_info, step.get("subtopics", []))
                else:
                    continue  # skip invalid
                print(f"    🔍 {t_topic}")
                pf = run_researcher(t_topic, t_subs)
                record_research(t_topic, t_subs, pf)
                # Agent 1.5: PDF enrichment
                try:
                    from agent1_5_pdf_reader import run_pdf_reader
                    pf = run_pdf_reader(pf)
                except Exception as e:
                    print(f"    ⚠️  Agent 1.5 נכשל ({e}) — ממשיך עם תקצירים")
                papers_files.append(str(pf))
            execution_state["papers_files"] = papers_files
            execution_state["papers_file"]  = papers_files[-1] if papers_files else None
            # Checkpoint
            _ckpt = execution_state.get("_ckpt")
            if _ckpt:
                _ckpt.save("researcher", {"papers_files": papers_files})
            return f"✅ נאספו מאמרים מ-{len(papers_files)} נושאים (עם PDF enrichment)"

        elif agent == "writer":
            if step.get("use_existing") and execution_state.get("article_paths"):
                paths = execution_state["article_paths"]
                print(f"    ↩️  משתמש במאמר קיים: {Path(list(paths.values())[0]).name}")
                return f"מאמר קיים: {paths}"
            pfs = execution_state.get("papers_files") or (
                [execution_state["papers_file"]] if execution_state.get("papers_file") else []
            )
            # Filter out None/empty and verify files exist
            pfs = [p for p in pfs if p and Path(p).exists()]
            if not pfs:
                return "❌ אין קבצי מאמרים — הרץ researcher קודם"
            from agent2_writer import run_writer
            last_plan      = execution_state.get("last_plan", {})
            combined_title = last_plan.get("combined_title") or last_plan.get("topic", "")
            article_paths  = run_writer([Path(p) for p in pfs], combined_title=combined_title)
            record_article(article_paths, combined_title)
            execution_state["article_paths"] = {k: str(v) for k, v in article_paths.items()}
            _ckpt = execution_state.get("_ckpt")
            if _ckpt:
                _ckpt.save("writer", {k: str(v) for k, v in article_paths.items()})
            # Agent 2.5: Article editor
            try:
                from agent_editor import edit_article
                edit_article(article_paths)
            except Exception as e:
                print(f"    ⚠️  Article editor: {e}")
            names = [Path(v).name for v in article_paths.values()]
            return f"✅ מאמר משולב נכתב ונערך: {', '.join(names[:2])}"

        elif agent == "content":
            ap = execution_state.get("article_paths")
            if not ap:
                return "❌ אין מאמר — הרץ writer קודם"
            valid_types = {"linkedin", "blog", "podcast"}
            raw_types = step.get("content_types", ["linkedin"])
            content_types = [t for t in raw_types if t in valid_types]
            if not content_types:
                content_types = ["linkedin"]  # fallback
                print(f"    ⚠️  content_types שגויים ({raw_types}) → linkedin")
            from agent3_content_creator import run_content_creator
            # Convert string paths back to Path objects
            ap_paths = {k: Path(v) for k, v in ap.items()}
            saved = run_content_creator(ap_paths, content_types)
            execution_state.setdefault("post_paths", {})
            topic_label = execution_state.get("last_plan", {}).get("topic", "")
            for ct, paths in saved.items():
                execution_state["post_paths"][ct] = [str(p) for p in paths]
                for p in paths:
                    record_content(ct, topic_label, str(p))
            # Agent 3.6: Content editor
            try:
                from agent_editor import edit_all_content
                edit_all_content(content_types)
            except Exception as e:
                print(f"    ⚠️  Content editor: {e}")

            created = [f"{ct}: {Path(paths[0]).name}" for ct, paths in saved.items() if paths]

            # Send previews via Telegram
            try:
                from notifications import notify_preview, is_configured
                if is_configured():
                    for ct, paths in saved.items():
                        if paths:
                            preview_text = Path(paths[0]).read_text(encoding="utf-8", errors="replace")
                            notify_preview(ct, preview_text)
            except Exception:
                pass

            # Agent 3.5: Human review (only in interactive mode)
            auto = execution_state.get("auto_approve", True)
            if not auto:
                try:
                    from agent3_5_human_review import review_all
                    reviews = review_all(content_types, auto_approve=False)
                    execution_state["reviews"] = {
                        p: r["decision"] for p, r in reviews.items()
                    }
                    # Handle rejections — re-run content for rejected platforms
                    rejected = [p for p, r in reviews.items() if r["decision"] == "rejected"]
                    for platform in rejected:
                        notes = reviews[platform].get("notes", "")
                        print(f"\n  🔄 כותב מחדש {platform} — {notes}")
                        retry_saved = run_content_creator(
                            ap_paths, [platform],
                            extra_instruction=notes
                        )
                        for ct, paths in retry_saved.items():
                            execution_state["post_paths"][ct] = [str(p) for p in paths]
                except Exception as e:
                    print(f"  ⚠️  Agent 3.5 נכשל ({e}) — ממשיך")

            return f"✅ תוכן נוצר: {', '.join(created)}"

        elif agent == "designer":
            try:
                from agent4_designer import run_designer
            except ImportError:
                return "⚠️  Agent 4 (Designer) טרם מותקן — מדלג"
            ap = execution_state.get("article_paths", {})
            pp = execution_state.get("post_paths", {})
            if not ap and not pp:
                return "❌ אין תוכן לעצב — הרץ content קודם"
            design_types = step.get("design_types", ["linkedin_cover"])
            topic = step.get("topic", "")
            ap_paths = {k: Path(v) for k, v in ap.items()}
            pp_paths = {k: [Path(p) for p in v] for k, v in pp.items()}
            saved = run_designer(ap_paths, pp_paths, design_types, topic)
            execution_state["design_paths"] = {k: str(v) for k, v in saved.items()}
            return f"✅ עיצובים נוצרו: {list(saved.keys())}"

        else:
            return f"❌ Agent לא מוכר: {agent}"

    except Exception as e:
        elapsed = time.time() - t0
        if elapsed > STEP_TIMEOUT:
            return f"❌ {agent} חרג מ-timeout ({elapsed/60:.1f} דק')"
        return f"❌ שגיאה ב-{agent}: {e}"

    elapsed = time.time() - t0
    return f"✅ {agent} הסתיים תוך {elapsed:.1f}s"


# ─────────────────────────────────────────────
# QA gate
# ─────────────────────────────────────────────

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


def _execute_step_with_qa(step: dict, execution_state: dict) -> str:
    """Run a step with QA check and retry up to MAX_RETRIES."""
    agent = step.get("agent", "")
    qa_stages = {
        "researcher": ["research"],
        "writer":     ["article"],
        "content":    step.get("content_types", ["linkedin"]),
    }
    stages = qa_stages.get(agent, [])

    for attempt in range(1, MAX_RETRIES + 2):
        if attempt > 1:
            hint = step.get("retry_hint", "")
            print(f"\n  ♻️  ניסיון חוזר {attempt}/{MAX_RETRIES+1} ל-{agent}"
                  + (f" — {hint}" if hint else ""))

        result = _execute_step(step, execution_state)

        # Don't retry structural errors (bad data, missing deps)
        structural_errors = ["אין קבצי מאמרים", "אין מאמר", "אין תוכן", "Agent לא מוכר"]
        if result.startswith("❌") and any(e in result for e in structural_errors):
            print(f"  ⛔ {agent} — שגיאה מבנית, לא מנסה שוב")
            execution_state.setdefault("errors", []).append(result)
            return result

        # Loop detection
        loop_info = _loop.record(agent, result, not result.startswith("❌"))
        if loop_info["loop_detected"]:
            for issue in loop_info["issues"]:
                print(f"  🔄 Loop detected: {issue}")
            execution_state.setdefault("errors", []).append(
                f"Loop in {agent}: {loop_info['issues']}"
            )
            if loop_info["attempts"] > MAX_RETRIES + 1:
                print(f"  ⛔ {agent} עצר — יותר מדי ניסיונות")
                return result

        if result.startswith("❌"):
            if attempt > MAX_RETRIES:
                return result
            continue

        # QA checks
        all_passed = True
        for stage in stages:
            passed, qa_result = _qa_gate(stage, attempt, execution_state)
            if not passed:
                all_passed = False
                if attempt <= MAX_RETRIES:
                    step = {**step, "retry_hint": "; ".join(qa_result.issues)}
                else:
                    execution_state.setdefault("errors", []).append(
                        f"QA failed for {agent}/{stage}: {qa_result.issues}"
                    )

        if all_passed or attempt > MAX_RETRIES:
            return result

    return result


# ─────────────────────────────────────────────
# Final report
# ─────────────────────────────────────────────

def _print_report(execution_state: dict, completed: list[str], errors: list[str]):
    print(f"\n{'='*60}")
    print(f"✅ PIPELINE COMPLETE — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*60}\n")

    if completed:
        print("📦 שלבים שהושלמו:")
        for s in completed:
            print(f"   ✓ {s}")

    # QA summary
    qa_log = execution_state.get("qa_log", [])
    if qa_log:
        scores = [q["score"] for q in qa_log]
        avg_qa = round(sum(scores) / len(scores))
        print(f"\n📈 ממוצע QA: {avg_qa}/100")
        print("  🔍 QA log:")
        for q in qa_log[-8:]:
            icon = "✅" if q["passed"] else "❌"
            issue_str = f"  ← {q['issues'][0]}" if q["issues"] else ""
            print(f"     {icon} {q['stage']:<12} {q['score']}/100{issue_str}")

    print("\n📁 קבצי פלט:")

    def show(label: str, val):
        if isinstance(val, str) and val:
            print(f"   {label:20} → {Path(val).name}")
        elif isinstance(val, dict):
            for k, v in val.items():
                paths = v if isinstance(v, list) else [v]
                for p in paths:
                    print(f"   {label}/{k:13} → {Path(p).name}")

    show("מחקר",    execution_state.get("papers_file"))
    show("מאמרים",  execution_state.get("article_paths", {}))
    show("תוכן",    execution_state.get("post_paths", {}))
    show("עיצובים", execution_state.get("design_paths", {}))

    all_errors = errors + execution_state.get("errors", [])
    if all_errors:
        print("\n⚠️  שגיאות:")
        for e in all_errors:
            print(f"   • {e}")

    print()


# ─────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────

def run_project_manager(request: str, auto_approve: bool = False) -> dict:
    """
    Main entry point.
    request: בקשה חופשית בעברית
    auto_approve: אם True — לא שואל אישור לפני הרצה
    """
    print(f"\n{'='*60}")
    print(f"📊 Project Manager | {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"   בקשה: {request}")
    print(f"{'='*60}\n")
    log.run_start(request)
    tracker.start_run(request)
    ckpt = Checkpoint()
    # Make checkpoint available to _execute_step via execution_state
    # (will be set in execution_state below)

    # 1. Sync memory + read system state
    print("  [PM] קורא מצב מערכת...")
    sync_memory_from_disk()
    state = _read_system_state()

    # Seed execution_state with existing latest files
    latest_papers = state["latest"].get("papers_files", [])
    execution_state = {
        "papers_files":  latest_papers,                  # רשימה של עד 3 קבצי מחקר
        "papers_file":   latest_papers[-1] if latest_papers else None,  # backward compat
        "article_paths": {"md": state["latest"]["article_md"]} if state["latest"].get("article_md") else {},
        "post_paths":    {},
        "design_paths":  {},
        "last_plan":     {},
        "qa_log":        [],
        "errors":        [],
        "auto_approve":  auto_approve,
        "_ckpt":         ckpt,
    }

    # 2. Create plan
    print("  [PM] מתכנן...")
    try:
        plan = _create_plan(request, state)
    except Exception as e:
        print(f"  [PM] שגיאה בתכנון: {e}")
        return {}

    steps = plan.get("steps", [])
    if not steps:
        print("  [PM] לא הוחלט על שום פעולה.")
        return {}

    # 3. Show plan & get approval
    _show_plan(plan)

    if not auto_approve:
        try:
            answer = input("✋ לאשר את התוכנית? (Enter=כן, n=לא): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer in {"n", "no", "לא", "בטל", "ביטול", "cancel"}:
            print("  [PM] בוטל.")
            return {}

    # 4. Execute steps
    completed = []
    errors    = []
    total_start = time.time()

    PIPELINE_TIMEOUT = 3600  # 60 minutes max for entire pipeline

    for i, step in enumerate(steps, 1):
        # Pipeline timeout check
        if time.time() - total_start > PIPELINE_TIMEOUT:
            print(f"\n  ⛔ Pipeline timeout — חרג מ-60 דקות, עוצר.")
            errors.append("Pipeline timeout (60 min)")
            break

        agent = step.get("agent", "")
        if step.get("optional"):
            try:
                ans = input(f"\n  ⚙️  שלב {i} ({AGENTS.get(agent,{}).get('name',agent)}) אופציונלי — להריץ? (Enter=כן, n=לא): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = ""
            if ans in {"n", "no", "לא", "בטל", "ביטול", "cancel"}:
                print(f"    ↩️  מדלג על {agent}")
                continue

        step_start = time.time()
        result = _execute_step_with_qa(step, execution_state)
        step_dur = time.time() - step_start
        print(f"    {result}")

        qa_entry = next((q for q in reversed(execution_state.get("qa_log",[])) if q.get("stage") in (agent, "research", "article")), None)
        tracker.record_step(agent, step_dur,
                            qa_score=qa_entry["score"] if qa_entry else None,
                            status="error" if result.startswith("❌") else "ok")

        if result.startswith("❌"):
            errors.append(result)
            tracker.record_error(agent, result)
            log.error(agent, result)
        else:
            completed.append(f"{AGENTS.get(agent,{}).get('name',agent)}: {step.get('action','')}")
            log.step(agent, "completed", step_dur)

    # 5. Report
    elapsed = time.time() - total_start
    print(f"\n⏱  סה\"כ: {elapsed/60:.1f} דקות")
    _print_report(execution_state, completed, errors)

    all_errors = errors + execution_state.get("errors", [])
    tracker.end_run(success=not bool(all_errors))
    log.run_end(success=not bool(all_errors), duration=time.time() - total_start)

    # 6. Telegram notifications
    try:
        from notifications import notify_complete, notify_error, is_configured
        if is_configured():
            if all_errors:
                for e in all_errors[:3]:
                    notify_error("pipeline", str(e)[:200])
            else:
                files_summary = {}
                if execution_state.get("article_paths"):
                    files_summary["מאמר"] = Path(list(execution_state["article_paths"].values())[0]).name
                for ct, paths in execution_state.get("post_paths", {}).items():
                    if paths:
                        files_summary[ct] = Path(paths[0]).name
                qa_log = execution_state.get("qa_log", [])
                avg_qa = round(sum(q["score"] for q in qa_log) / len(qa_log)) if qa_log else 0
                notify_complete(request, elapsed / 60, files_summary, avg_qa)
    except Exception:
        pass  # don't crash pipeline on notification failure
    return execution_state


# ─────────────────────────────────────────────
# Session memory — persists between --chat sessions
# ─────────────────────────────────────────────

SESSION_FILE = OUTPUT_DIR / "session_memory.json"

def _load_session() -> dict:
    if SESSION_FILE.exists():
        try:
            return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"topic": "חינוך בלתי פורמלי", "preferences": {}, "last_requests": []}

def _save_session(mem: dict):
    SESSION_FILE.write_text(json.dumps(mem, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────
# Last run explainer
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
# Priority queue
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
# Chat: intent classification (via claude_cli)
# ─────────────────────────────────────────────

def _classify_intent(user_input: str) -> dict:
    """Classify user intent using claude_cli."""
    prompt = f"""המשתמש אמר: "{user_input}"

החזר JSON עם:
  action: "run_pipeline" | "content_only" | "qa_check" | "edit" | "info" | "add_paper" | "chat"
  params: dict עם פרמטרים (topic, content_types, platform, url)
  confirm_needed: true אם הפעולה תיקח יותר מ-2 דקות

JSON בלבד."""
    try:
        return ask_claude_json(prompt, max_budget=0.3)
    except Exception:
        return {"action": "chat", "params": {}, "confirm_needed": False}


def _chat_response(user_input: str, history: list[dict]) -> str:
    """General chat response via claude_cli."""
    system = """אתה מוקי — מנהל פרויקט של מערכת בוטים לחינוך.
מדבר עברית, ישיר ותכליתי. יש לך 8 סוכנים (0-4 + editors).
תענה בקצרה."""
    # Build context from recent history
    context = "\n".join(
        f"{'משתמש' if m['role']=='user' else 'מוקי'}: {m['content']}"
        for m in history[-6:]
    )
    prompt = f"{context}\nמשתמש: {user_input}\n\nענה בקצרה כמוקי:"
    return ask_claude(prompt, system=system, max_budget=0.3)


def _chat_process(user_input: str, session: dict, auto: bool) -> str:
    """Process user message and execute actions."""
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
  הרץ הכל [--parallel] [--bilingual]  — pipeline מלא
  פוסט linkedin / blog / podcast       — רק תוכן
  בדוק איכות                           — QA על קבצים קיימים
  ערוך [article/linkedin/blog/podcast] — הגהה
  מה יש / סטטוס                        — קבצים קיימים
  סיכום שבועי                          — סיכום 7 ימים אחרונים
  ביבליוגרפיה / bib                    — ניהול מקורות
  analytics                             — דוח ביצועים
  dashboard                             — דאשבורד ויזואלי בדפדפן
  checkpoints                           — סטטוס checkpoints
  חפש [מילה] / היסטוריה                — חפש בתוכן שנוצר
  קונטקסט / context                    — הצג/עדכן קונטקסט אישי
  מה קרה / ריצה אחרונה                — פירוט ריצה אחרונה
  תור / תור [בקשה]                    — תור עדיפויות
  הוסף מאמר [URL]                      — הוסף מקור ידני
  עצור                                  — יציאה"""

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

    if low in ("dashboard", "דאשבורד", "לוח בקרה"):
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
        from bibliography import BibManager
        bib = BibManager()
        # Check if search query
        if "חפש" in low or "search" in low:
            words = low.replace("חפש", "").replace("search", "").replace("ביבליוגרפיה", "").strip()
            if words:
                results = bib.search(words)
                lines = [f"🔍 תוצאות עבור '{words}': {len(results)}"]
                for key, entry in results[:8]:
                    cit = entry.get("citation_count", 0)
                    pdf = " 📄" if entry.get("pdf_url") else ""
                    lines.append(f"  [{key}] {entry.get('year','?')} | "
                                 f"{entry.get('title','')[:50]} (×{cit}){pdf}")
                return "\n".join(lines)
            return "מה לחפש? (למשל: ביבליוגרפיה חפש belonging)"
        bib.rebuild()
        bib.print_stats()
        return ""

    if any(w in low for w in ["קונטקסט", "context"]):
        from context_update import show_context
        show_context()
        return ""

    if any(w in low for w in ["היסטוריה", "history", "חפש", "search", "מה כתבנו"]):
        from history import search_content, print_recent
        # Extract search query
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

    if any(w in low for w in ["qa", "איכות", "בדוק איכות"]):
        state = _read_system_state()
        latest_papers = state["latest"].get("papers_files", [])
        tmp_state = {
            "papers_file": latest_papers[-1] if latest_papers else None,
            "article_paths": {"md": state["latest"]["article_md"]} if state["latest"].get("article_md") else {},
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
        topic = params.get("topic") or session.get("topic", "חינוך בלתי פורמלי")
        valid_types = {"linkedin", "blog", "podcast"}
        raw_types = params.get("content_types") or ["linkedin"]
        content_types = [t for t in raw_types if t in valid_types] or ["linkedin"]
        parallel = "--parallel" in user_input
        bilingual = "--bilingual" in user_input

        ct_str = " + ".join(CONTENT_TYPES.get(t, t) for t in content_types)
        print(f"\n  מריץ pipeline — {topic} | {ct_str}...")

        if intent.get("confirm_needed") and not auto:
            try:
                ok = input("  להמשיך? (Enter=כן, n=לא): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ok = ""
            if ok in {"n", "no", "לא", "בטל", "cancel"}:
                return "בוטל."

        req = f"הרץ pipeline: נושא={topic} תוכן={' '.join(content_types)}"
        run_project_manager(req, auto_approve=True)
        return "Pipeline הסתיים."

    elif action == "content_only":
        valid_types = {"linkedin", "blog", "podcast"}
        raw_types = params.get("content_types") or ["linkedin"]
        content_types = [t for t in raw_types if t in valid_types] or ["linkedin"]
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

    else:
        # General chat
        history = session.setdefault("history", [])
        history.append({"role": "user", "content": user_input})
        answer = _chat_response(user_input, history)
        history.append({"role": "assistant", "content": answer})
        return answer


# ─────────────────────────────────────────────
# Chat mode
# ─────────────────────────────────────────────

def run_chat(auto: bool = False):
    """מצב --chat אינטראקטיבי עם session memory."""
    session_mem = _load_session()
    session = {
        "history": [],
        "topic": session_mem.get("topic", "חינוך בלתי פורמלי"),
    }

    last = session_mem.get("last_requests", [])
    last_hint = f"\n  (session קודם: {last[-1]['input'][:40]}...)" if last else ""

    print(f"""
╔══════════════════════════════════════════════════════════╗
║  🤖 מוקי — Project Manager                               ║
║  הקלד '?' לעזרה, 'מה קרה' לריצה אחרונה, 'עצור' לסיום   ║
╚══════════════════════════════════════════════════════════╝
{last_hint}""")

    import random
    greetings = ["מה אפשר לעשות בשבילך?", "במה אני יכול לעזור?", "מה רוצים היום?"]
    print(f"\n  🤖 מוקי: {random.choice(greetings)}")

    while True:
        try:
            user_input = input("\n  👤 אתה: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  🤖 מוקי: להתראות!")
            _save_session(session_mem)
            break

        if not user_input:
            continue

        response = _chat_process(user_input, session, auto)

        if response == "__EXIT__":
            print("  🤖 מוקי: להתראות!")
            _save_session(session_mem)
            break

        if response:
            print(f"\n  🤖 מוקי: {response}")

        # Track request in session memory
        session_mem.setdefault("last_requests", []).append({
            "input": user_input[:80],
            "time": datetime.now().strftime("%d/%m %H:%M"),
        })
        session_mem["last_requests"] = session_mem["last_requests"][-10:]
        if session.get("topic") != session_mem.get("topic"):
            session_mem["topic"] = session.get("topic", session_mem["topic"])


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    if "--chat" in sys.argv:
        run_chat(auto="--auto" in sys.argv)
    elif "--checkpoints" in sys.argv:
        Checkpoint.print_status()
    elif "--analytics" in sys.argv:
        tracker.print_report()
    elif "--last-run" in sys.argv:
        print(_explain_last_run())
    elif "--queue" in sys.argv:
        print(queue_status())
    elif len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        auto = "--auto" in sys.argv
        run_project_manager(sys.argv[1], auto_approve=auto)
    else:
        print("""
Usage:
  python3 agent5_project_manager.py --chat            # מוקי אינטראקטיבי
  python3 agent5_project_manager.py --chat --auto     # ללא אישורים
  python3 agent5_project_manager.py "בקשה" --auto     # one-shot
  python3 agent5_project_manager.py --analytics       # דוח ביצועים
  python3 agent5_project_manager.py --checkpoints     # סטטוס checkpoints
  python3 agent5_project_manager.py --last-run        # ריצה אחרונה
  python3 agent5_project_manager.py --queue           # תור עדיפויות
        """)
