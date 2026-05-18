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


def _notify(message: str):
    """Write pipeline notification to a shared file. Telegram bot can poll this."""
    notify_path = OUTPUT_DIR / "pipeline_status.txt"
    timestamp = datetime.now().strftime("%H:%M:%S")
    with open(notify_path, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


def _progress(step: int, total: int, agent_name: str, started_at: float):
    """Print a progress bar with elapsed time for long pipeline runs."""
    elapsed = time.time() - started_at
    elapsed_min = elapsed / 60
    print(f"\n  ━━━ [{step}/{total}] {agent_name} ━━━  ({elapsed_min:.0f} min elapsed)")


# ─────────────────────────────────────────────
# Agents registry
# ─────────────────────────────────────────────

_loop = LoopDetector()

# Agent criticality levels for graceful degradation:
#   critical     — pipeline stops on failure (no point continuing)
#   semi-critical — individual sub-tasks can fail (e.g. one platform)
#   non-critical  — failure is logged, pipeline continues
CRITICAL_AGENTS = {"planner", "researcher", "writer"}


# ─────────────────────────────────────────────
# Autonomy level — how much Moki decides without asking
# ─────────────────────────────────────────────
# Level 0: ask before every gate (safest, slow)
# Level 1: auto-approve proposals + content (default — "Trust me")
# Level 2: + auto-update voice rules + auto-publish
# Level 3: full autonomy (no manual gates)
def _autonomy_level() -> int:
    """Read from env var MOKI_AUTONOMY_LEVEL or _memory/autonomy.md (default: 1)."""
    import os
    env = os.environ.get("MOKI_AUTONOMY_LEVEL")
    if env and env.isdigit():
        return int(env)
    try:
        from obsidian_memory import load_memory_note
        body = load_memory_note("autonomy")
        for line in body.splitlines():
            if line.startswith("level:"):
                v = line.split(":", 1)[1].strip()
                if v.isdigit():
                    return int(v)
    except Exception:
        pass
    return 1  # default: Trust me

AUTONOMY = _autonomy_level()
AUTO_APPROVE_DEFAULT = AUTONOMY >= 1
AUTO_PUBLISH_DEFAULT = AUTONOMY >= 1
# "content" is semi-critical: handled inside _execute_step (per-platform)
# "designer", "editor" are non-critical

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
    "video": {
        "id": "6", "name": "Agent 6 — Video Creator",
        "desc": "מייצר וידאו קצר (5-10 שניות) לפוסט LinkedIn דרך fal.ai (Seedance/Kling/Veo)",
        "emoji": "🎬",
    },
    "journal": {
        "id": "7", "name": "Agent 7 — Research Journal",
        "desc": "מתעד יומן מחקר נרטיבי אחרי כל ריצה — תהליך, שאלות, החלטות, כשלים",
        "emoji": "📓",
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

_MAX_MODE_KEYWORDS = (
    "moki: full", "moki:full", "מוקי: מלא", "מוקי:מלא",
    "מקסימום", "max", "מלא", "הכל", "כל הסוכנים", "all agents",
    "פייפליין מלא", "full pipeline",
)


def _is_max_mode(request: str) -> bool:
    """Check if request asks for the full pipeline (all 5 agents)."""
    if not request:
        return False
    r = request.lower()
    return any(kw in r for kw in _MAX_MODE_KEYWORDS)


def _build_max_plan(request: str) -> dict:
    """Forced full pipeline — all 5 agents on every request, no skips."""
    return {
        "summary": f"MAX MODE — pipeline מלא (5 סוכנים) על: {request[:80]}",
        "estimated_time": "30-50 דקות",
        "max_mode": True,
        "steps": [
            {
                "agent": "planner",
                "action": "בוחר 3 נושאים + הצעת מחקר + 3 RQs",
                "topic": request,
                "optional": False,
            },
            {
                "agent": "researcher",
                "action": "חוקר 3 נושאים במקביל מ-7 מקורות אקדמיים",
                "use_existing": False,
                "optional": False,
            },
            {
                "agent": "writer",
                "action": "כותב מאמר אקדמי משולב APA 7 (2,000-3,000 מילים)",
                "use_existing": False,
                "optional": False,
            },
            {
                "agent": "content",
                "action": "יוצר 3 גרסאות תוכן: LinkedIn + בלוג + פודקאסט",
                "content_types": ["linkedin", "blog", "podcast"],
                "use_existing": False,
                "optional": False,
            },
            {
                "agent": "designer",
                "action": "מעצב 3 ויזואלים: LinkedIn cover + blog banner + podcast cover",
                "design_types": ["linkedin_cover", "blog_banner", "podcast_cover"],
                "optional": False,
            },
            {
                "agent": "video",
                "action": "מייצר וידאו קצר (5s, vertical) לפוסט LinkedIn האחרון",
                "model": "seedance_lite",
                "platform": "linkedin",
                "optional": True,  # video only if FAL_KEY exists
            },
        ],
    }


def _create_plan(request: str, state: dict) -> dict:
    """Ask Claude to decide what to run.

    If request contains MAX-MODE keyword (e.g., 'moki: full', 'מלא', 'הכל') —
    returns the forced 5-agent plan without consulting Claude.
    """
    if _is_max_mode(request):
        print(f"  🚀 MAX MODE detected — running all 5 agents on this request")
        return _build_max_plan(request)

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
- content_types: תמיד ["linkedin", "blog", "podcast"] — שלושתם בכל ריצה, ללא יוצא מן הכלל
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

STEP_TIMEOUT = 3600  # 60 minutes max per step (writer+content can take 40-55 min)

# ─────────────────────────────────────────────
# Research Proposal Approval Gate
# Moki reviews Agent 0's proposal before Researcher starts.
# ─────────────────────────────────────────────

def _approve_proposal(proposal: dict, proposal_path: str, plan: dict,
                      auto_mode: bool = False) -> dict:
    """
    Moki reviews the research proposal. Returns:
      {"approved": bool, "reason": str, "approver": "moki" | "user" | "auto"}
    """
    print("\n" + "=" * 60)
    print("📋 הצעת מחקר — Moki בוחנת לפני אישור")
    print("=" * 60)
    print(f"\nכותרת: {proposal.get('title', '?')}")
    print(f"רקע:    {(proposal.get('background') or '')[:150]}...")
    print(f"השערה:  {(proposal.get('hypothesis') or '')[:120]}")
    print(f"קהל:    {(proposal.get('audience') or '?')[:80]}")
    print(f"זמן:    {proposal.get('timeline_estimate', '?')}")
    if proposal_path:
        print(f"\n📄 הצעה מלאה: {proposal_path}")

    # ── Moki's automatic checks (heuristic — runs always) ──
    issues = []

    required_fields = ["title", "background", "rationale", "hypothesis",
                       "methodology", "expected_contribution", "audience"]
    missing = [f for f in required_fields if not proposal.get(f)]
    if missing:
        issues.append(f"שדות חסרים בהצעה: {', '.join(missing)}")

    # RQs must exist
    rqs = plan.get("research_questions", [])
    if len(rqs) < 2:
        issues.append(f"רק {len(rqs)} RQs — צריך לפחות 2")
    for i, rq in enumerate(rqs, 1):
        q = rq.get("question", "")
        if len(q) < 20:
            issues.append(f"RQ{i} קצרה מדי ({len(q)} תווים)")
        if q.startswith("האם"):
            issues.append(f"RQ{i} שאלת כן/לא — נסח כשאלת 'תחת אילו תנאים' או 'איך'")

    # Hypothesis must be a real claim, not "we don't know"
    hyp = proposal.get("hypothesis", "").lower()
    if hyp and any(p in hyp for p in ["לא יודעים", "אנחנו לא", "we don't know"]):
        issues.append("השערה אינה claim — צריכה להיות תחזית קונקרטית")

    # Decision
    if issues:
        print(f"\n⚠️  Moki זיהתה {len(issues)} בעיות בהצעה:")
        for i in issues:
            print(f"   • {i}")
    else:
        print(f"\n✅ Moki: הצעת המחקר עומדת בכל הקריטריונים")

    # In auto mode: approve if no critical issues
    if auto_mode:
        critical = [i for i in issues if "חסרים" in i or "קצרה מדי" in i]
        if critical:
            return {
                "approved": False,
                "reason": f"Moki auto-rejected: {len(critical)} critical issues",
                "approver": "moki",
                "issues": issues,
            }
        if issues:
            print(f"  [Moki auto-mode] אישור עם {len(issues)} אזהרות")
        return {
            "approved": True,
            "reason": "Moki auto-approved",
            "approver": "moki" if not issues else "moki_with_warnings",
            "issues": issues,
        }

    # Interactive mode: ask user
    print("\n" + "=" * 60)
    try:
        ans = input("האם לאשר את הצעת המחקר? [Y]es / [N]o / [E]dit (open file): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "y"

    if ans in ("n", "no", "לא"):
        return {"approved": False, "reason": "User rejected", "approver": "user"}
    if ans in ("e", "edit"):
        if proposal_path:
            import subprocess
            try:
                subprocess.run(["open", str(proposal_path)], check=False)
                print(f"  📝 הקובץ נפתח לעריכה. אשר אחרי שסיימת.")
                input("Press Enter when done editing...")
            except Exception:
                pass
        return {"approved": True, "reason": "User edited and approved", "approver": "user"}
    return {"approved": True, "reason": "User approved", "approver": "user"}


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

            # ── PROPOSAL APPROVAL GATE ──
            # Moki must approve research proposal before Researcher starts.
            # Auto-mode: Moki approves on its own (heuristic check). Else: prompt user.
            proposal = plan.get("proposal", {})
            proposal_path = plan.get("proposal_path")
            if proposal:
                approval_result = _approve_proposal(
                    proposal=proposal,
                    proposal_path=proposal_path,
                    plan=plan,
                    auto_mode=execution_state.get("auto_approve", AUTO_APPROVE_DEFAULT),
                )
                if not approval_result["approved"]:
                    return f"❌ הצעת המחקר נדחתה: {approval_result['reason']}"
                execution_state["proposal_approved"] = True
                execution_state["proposal_approval"] = approval_result

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

            # Parallelize 3 topics — each topic runs full researcher independently
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def _research_one(t_info):
                """Research one topic + PDF enrichment. Returns (topic, subs, papers_file)."""
                if isinstance(t_info, dict):
                    t_topic = t_info.get("topic", "non-formal education")
                    t_subs  = t_info.get("subtopics") or []
                elif isinstance(t_info, str):
                    t_topic = t_info
                    t_subs  = last_plan.get("subtopics_map", {}).get(t_info, step.get("subtopics", []))
                else:
                    return None
                print(f"    🔍 [start] {t_topic}")
                pf = run_researcher(t_topic, t_subs)
                # Agent 1.5: PDF enrichment
                try:
                    from agent1_5_pdf_reader import run_pdf_reader
                    pf = run_pdf_reader(pf)
                except Exception as e:
                    print(f"    [Agent1.5] {t_topic}: {e}")
                print(f"    ✅ [done] {t_topic}")
                return (t_topic, t_subs, pf)

            papers_files = []
            with ThreadPoolExecutor(max_workers=min(3, len(raw_topics))) as executor:
                futures = [executor.submit(_research_one, t) for t in raw_topics]
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        if result is None:
                            continue
                        t_topic, t_subs, pf = result
                        record_research(t_topic, t_subs, pf)
                        papers_files.append(pf)
                    except Exception as e:
                        print(f"    ⚠️ Topic failed: {e}")

            # Convert to strings + sort to keep deterministic order
            papers_files = [str(p) for p in papers_files]
            execution_state["papers_files"] = papers_files
            execution_state["papers_file"]  = papers_files[-1] if papers_files else None
            # Checkpoint
            _ckpt = execution_state.get("_ckpt")
            if _ckpt:
                _ckpt.save("researcher", {"papers_files": papers_files})

            # ── RQ Validation: do these papers ACTUALLY answer the RQs? ──
            try:
                from rq_validator import validate_corpus_vs_rq
                last_plan = execution_state.get("last_plan", {})
                rqs = last_plan.get("research_questions", [])
                if rqs and papers_files:
                    print("\n  🔬 RQ Validation — checking corpus alignment with research questions...")
                    weak_rqs = []
                    for rq in rqs:
                        # Find papers file matching this topic
                        topic_slug = rq.get("topic", "")[:30].replace(" ", "_").lower()
                        match = next((pf for pf in papers_files if topic_slug[:15] in pf.lower()), None)
                        if not match:
                            continue
                        try:
                            data = json.loads(Path(match).read_text(encoding="utf-8"))
                            papers = data.get("papers", data) if isinstance(data, dict) else data
                            result = validate_corpus_vs_rq(papers, rq.get("question", ""))
                            verdict = result.get("verdict")
                            score = result.get("coverage_score", 0)
                            icon = {"well_answered": "✅", "partial": "⚠️", "weak": "❌", "off_target": "🚨"}.get(verdict, "?")
                            print(f"    {icon} '{rq.get('topic','')[:40]}': {score}/100 ({verdict})")
                            if verdict in ("weak", "off_target"):
                                weak_rqs.append(rq.get("topic", ""))
                                print(f"       💡 {result.get('recommendation', '')[:120]}")
                        except Exception as e:
                            print(f"    ⚠️ RQ validation failed for {topic_slug}: {e}")
                    if weak_rqs:
                        print(f"  🚨 {len(weak_rqs)}/{len(rqs)} RQs poorly covered — Writer may struggle")
            except ImportError:
                pass
            except Exception as e:
                print(f"  ⚠️ RQ Validator skipped: {e}")

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
            # Pass research questions from Planner (Agent 0) — sharpens outline + final write
            research_questions = last_plan.get("research_questions") or []
            combined_rq = last_plan.get("combined_research_question") or ""
            article_paths  = run_writer(
                [Path(p) for p in pfs],
                combined_title=combined_title,
                research_questions=research_questions,
                combined_research_question=combined_rq,
            )
            record_article(article_paths, combined_title)
            execution_state["article_paths"] = {k: str(v) for k, v in article_paths.items()}
            _ckpt = execution_state.get("_ckpt")
            if _ckpt:
                _ckpt.save("writer", {k: str(v) for k, v in article_paths.items()})
            # Agent 2.7: Fact-checker — validate citations against paper corpus.
            # Runs AFTER Agent 2 completes, BEFORE Agent 3 starts. Side-effect only —
            # never modifies Agent 2's output, just prints a score and persists
            # the suspicious list to output/fact_check_<timestamp>.json.
            try:
                from agent2_7_fact_checker import run_fact_checker
                md_for_check = article_paths.get("md") or next(
                    (v for k, v in article_paths.items() if str(v).endswith(".md")),
                    None,
                )
                if md_for_check:
                    fc = run_fact_checker(Path(md_for_check), [Path(p) for p in pfs])
                    print(f"    📊 Fact-check score: {fc['score']}/100 "
                          f"({fc['verified']}/{fc['total_citations']} verified, "
                          f"{len(fc['suspicious'])} suspicious)")
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    fc_path = OUTPUT_DIR / f"fact_check_{ts}.json"
                    fc_path.parent.mkdir(parents=True, exist_ok=True)
                    fc_path.write_text(
                        json.dumps({
                            "article": str(md_for_check),
                            "score": fc["score"],
                            "total_citations": fc["total_citations"],
                            "verified": fc["verified"],
                            "suspicious": fc["suspicious"],
                        }, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print(f"    📝 Fact-check report: {fc_path.name}")
            except Exception as e:
                print(f"    ⚠️  Fact-checker: {e}")
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
            raw_types = ["linkedin", "blog", "podcast"]  # תמיד כולם
            content_types = [t for t in raw_types if t in valid_types]
            if not content_types:
                content_types = ["linkedin", "blog", "podcast"]  # fallback
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
            # ברירת מחדל: עיצוב לכל פלטפורמה שיש לה תוכן
            content_platforms = set((execution_state.get("post_paths") or {}).keys())
            default_designs = []
            if "linkedin" in content_platforms: default_designs.append("linkedin_cover")
            if "blog" in content_platforms:     default_designs.append("blog_banner")
            if "podcast" in content_platforms:  default_designs.append("podcast_cover")
            if not default_designs:
                default_designs = ["linkedin_cover", "blog_banner"]
            design_types = step.get("design_types") or default_designs
            topic = step.get("topic", "")
            ap_paths = {k: Path(v) for k, v in ap.items()}
            pp_paths = {k: [Path(p) for p in v] for k, v in pp.items()}
            saved = run_designer(ap_paths, pp_paths, design_types, topic)
            execution_state["design_paths"] = {k: str(v) for k, v in saved.items()}
            return f"✅ עיצובים נוצרו: {list(saved.keys())}"

        elif agent == "video":
            try:
                from agent6_video_creator import (
                    create_video_for_post, _latest_post, FalError
                )
            except ImportError as e:
                return f"⚠️  Agent 6 (Video) לא זמין: {e}"

            platform = step.get("platform", "linkedin")
            model = step.get("model", "seedance_lite")
            post_path = _latest_post(platform)
            if not post_path:
                return f"⚠️  אין פוסט {platform} ליצור עליו וידאו — מדלג"

            try:
                result = create_video_for_post(post_path, model_key=model)
            except FalError as fe:
                if "No FAL_KEY" in str(fe):
                    return "⚠️  Agent 6 דלג — אין FAL_KEY מוגדר (.env)"
                return f"❌ Agent 6 fal.ai error: {fe}"

            if result.get("status") == "ok":
                execution_state.setdefault("video_paths", {})[platform] = result["video_path"]
                return f"✅ וידאו נוצר: {Path(result['video_path']).name} (${result.get('cost_usd', '?')})"
            if result.get("status") == "skipped_budget":
                return "⚠️  Agent 6 דלג — daily budget cap"
            return f"❌ Agent 6 נכשל: {result.get('error', 'unknown')}"

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

    # researcher/writer/content have internal retry/review/fallback logic.
    # Don't retry from outside — it causes loop false positives and waste.
    #   researcher: retries per source internally, returns cached if topic exists
    #   writer: self-review loop
    #   content: rejection learning loop
    no_external_retry = {"researcher", "writer", "content"}

    qa_stages = {
        "researcher": ["research"],
        "writer":     ["article"],
        "content":    ["linkedin", "blog", "podcast"],
    }
    stages = qa_stages.get(agent, [])
    max_attempts = 1 if agent in no_external_retry else MAX_RETRIES + 1

    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            hint = step.get("retry_hint", "")
            print(f"\n  ♻️  ניסיון חוזר {attempt}/{MAX_RETRIES+1} ל-{agent}"
                  + (f" — {hint}" if hint else ""))
            # Push retry_hint to scratchpad so the agent picks it up in its prompt
            if hint:
                try:
                    from scratchpad import note as _scratch_note
                    _scratch_note("qa_gate", "retry_hint",
                                  {"agent": agent, "attempt": attempt, "issues": hint})
                except Exception:
                    pass

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
    # Pre-flight: verify Claude CLI is available before wasting time
    from claude_cli import require_health, reset_budget
    require_health()

    # Reset budget for new pipeline run
    reset_budget()

    # ── Budget pre-check: refuse to start if we can't afford a full run ──
    # A full pipeline costs ~$15-20. Checking only mid-step kills runs halfway.
    EST_FULL_RUN_USD = 18.0
    try:
        from claude_cli import daily_budget_status
        bs = daily_budget_status()
        if bs["remaining_usd"] < EST_FULL_RUN_USD:
            print(f"\n  ⚠️  Budget pre-check: ${bs['remaining_usd']:.2f} left today, "
                  f"a full run needs ~${EST_FULL_RUN_USD}.")
            if bs["remaining_usd"] < 3.0:
                print(f"  ⛔ Too little budget to start ({bs['spent_usd']:.2f}/{bs['cap_usd']} used).")
                print(f"     Raise with: export MOKI_DAILY_BUDGET={int(bs['cap_usd'])+30}")
                return {"status": "blocked_budget", "budget": bs}
            print(f"  ⚠️  Starting anyway — run may stop mid-way. "
                  f"Consider: export MOKI_DAILY_BUDGET={int(bs['cap_usd'])+30}")
    except Exception:
        pass

    print(f"\n{'='*60}")
    print(f"📊 Project Manager | {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"   בקשה: {request}")
    print(f"{'='*60}\n")
    log.run_start(request)
    tracker.start_run(request)
    _notify(f"🚀 Pipeline started: {request[:60]}")
    ckpt = Checkpoint()
    # Make checkpoint available to _execute_step via execution_state
    # (will be set in execution_state below)

    # 1. Sync memory + read system state
    print("  [PM] קורא מצב מערכת...")
    sync_memory_from_disk()
    state = _read_system_state()

    # Seed execution_state with existing latest files
    latest = state.get("latest") or {}
    latest_papers = latest.get("papers_files", [])
    execution_state = {
        "papers_files":  latest_papers,
        "papers_file":   latest_papers[-1] if latest_papers else None,
        "article_paths": {"md": latest["article_md"]} if latest.get("article_md") else {},
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

    PIPELINE_TIMEOUT = 5400  # 90 minutes max for entire pipeline

    for i, step in enumerate(steps, 1):
        # Pipeline timeout check
        if time.time() - total_start > PIPELINE_TIMEOUT:
            print(f"\n  ⛔ Pipeline timeout — חרג מ-{PIPELINE_TIMEOUT//60} דקות, עוצר.")
            errors.append(f"Pipeline timeout ({PIPELINE_TIMEOUT//60} min)")
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

        agent_info = AGENTS.get(agent, {})
        agent_display = agent_info.get("name", agent)
        _progress(i, len(steps), agent_display, total_start)
        _notify(f"⏳ [{i}/{len(steps)}] {agent_display} started")

        # ── Pre-flight CLI check for heavy Claude agents ──
        # Before a heavy step, verify the CLI still responds (it may have hit
        # a rate limit since the run started). If dead — defer NOW, don't burn
        # 20+ min on doomed retries. (Insight from the research journal.)
        if agent in ("writer", "content"):
            try:
                from claude_cli import health_check as _hc
                hc = _hc()
                if not hc.get("ok"):
                    print(f"\n  ⏸  {agent_display} — pre-flight: CLI לא מגיב "
                          f"({hc.get('error', '?')[:80]}). דוחה מיד.")
                    _ckpt = execution_state.get("_ckpt")
                    if _ckpt:
                        try:
                            _ckpt.save(f"{agent}_deferred", {
                                "reason": f"pre-flight CLI check failed: {hc.get('error','')[:150]}",
                                "deferred_at": datetime.now().isoformat(),
                                "topic": execution_state.get("last_plan", {}).get("topic", ""),
                            })
                        except Exception:
                            pass
                    errors.append(f"⏸  {agent} deferred (pre-flight CLI check failed)")
                    _notify(f"⏸  {agent_display} deferred — CLI unresponsive")
                    if agent in CRITICAL_AGENTS:
                        break
                    continue
            except Exception:
                pass  # health check itself failed — proceed, the step's own retry handles it

        step_start = time.time()
        # Hard timeout enforcement: don't wait for thread to finish on timeout
        import concurrent.futures as _cf
        _ex = _cf.ThreadPoolExecutor(max_workers=1)
        _future = _ex.submit(_execute_step_with_qa, step, execution_state)

        # Pre-timeout checkpoint: 30s before STEP_TIMEOUT, save partial state
        # so resume after timeout has valid state to work with.
        _pre_timeout = max(STEP_TIMEOUT - 30, 60)
        try:
            result = _future.result(timeout=_pre_timeout)
            _ex.shutdown(wait=False)  # clean exit on success
        except _cf.TimeoutError:
            # Pre-timeout reached but step not done — save checkpoint marking
            # this step as "in_progress" so resume can detect it
            _ckpt = execution_state.get("_ckpt")
            if _ckpt:
                try:
                    _ckpt.save(f"{agent}_pre_timeout", {
                        "agent": agent,
                        "elapsed_s": round(time.time() - step_start, 1),
                        "saved_at": datetime.now().isoformat(),
                        "state": "in_progress",
                    })
                except Exception:
                    pass
            # Now wait the remaining 30s for grace period
            try:
                result = _future.result(timeout=30)
                _ex.shutdown(wait=False)
            except _cf.TimeoutError:
                result = f"❌ {agent} hard timeout — exceeded {STEP_TIMEOUT/60:.0f} min"
                _future.cancel()
                try:
                    _ex.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    _ex.shutdown(wait=False)  # Python <3.9 fallback
        except Exception as _step_exc:
            # Defer-and-resume: if CLI is unavailable, save checkpoint and stop gracefully
            from claude_cli import CLIUnavailable
            if isinstance(_step_exc, CLIUnavailable) or "CLIUnavailable" in str(type(_step_exc)):
                _ckpt = execution_state.get("_ckpt")
                if _ckpt:
                    _ckpt.save(f"{agent}_deferred", {
                        "reason": str(_step_exc)[:200],
                        "deferred_at": datetime.now().isoformat(),
                    })
                print(f"\n  ⏸  {agent_display} deferred — Claude CLI unavailable.")
                print(f"     Checkpoint saved. Run 'moki' → 'המשך' to resume later.")
                _notify(f"⏸  Pipeline deferred at {agent_display} — CLI unavailable")
                errors.append(f"⏸  {agent} deferred (CLI unavailable, checkpoint saved)")
                break  # Stop pipeline cleanly, don't mark as failure
            result = f"❌ שגיאה ב-{agent}: {_step_exc}"
        step_dur = time.time() - step_start
        print(f"    {result}")

        qa_entry = next((q for q in reversed(execution_state.get("qa_log",[])) if q.get("stage") in (agent, "research", "article")), None)
        tracker.record_step(agent, step_dur,
                            qa_score=qa_entry.get("score") if qa_entry else None,
                            status="error" if result.startswith("❌") else "ok")

        if result.startswith("❌"):
            errors.append(result)
            tracker.record_error(agent, result)
            log.error(agent, result)
            _notify(f"❌ {agent_display} failed: {result[:100]}")

            # CLI-unavailable / rate-limit → save a defer checkpoint so the
            # next run can pick this step up (the exception was swallowed
            # inside the agent and returned as an error string).
            if any(sig in result for sig in
                   ("Claude CLI unavailable", "defer this step", "CLIUnavailable")):
                _ckpt = execution_state.get("_ckpt")
                if _ckpt:
                    try:
                        _ckpt.save(f"{agent}_deferred", {
                            "reason": result[:200],
                            "deferred_at": datetime.now().isoformat(),
                            "topic": execution_state.get("last_plan", {}).get("topic", ""),
                        })
                        print(f"  ⏸  {agent_display} deferred — checkpoint saved, "
                              f"next run will retry this step first.")
                    except Exception:
                        pass

            # Graceful degradation: only stop for critical agents
            if agent in CRITICAL_AGENTS:
                print(f"\n  ⛔ {agent_display} failed — pipeline cannot continue without this step.")
                break
            else:
                print(f"\n  ⚠️ {agent_display} failed — skipping, pipeline continues")
                continue
        else:
            completed.append(f"{agent_display}: {step.get('action','')}")
            log.step(agent, "completed", step_dur)
            _notify(f"✅ [{i}/{len(steps)}] {agent_display} completed")

    # 5. Report
    elapsed = time.time() - total_start
    total_min = elapsed / 60
    print(f"\n🎉 Pipeline complete — {total_min:.0f} minutes, {len(completed)} steps")
    _notify(f"🎉 Pipeline complete — {total_min:.0f} min")
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

    # ── Auto-sync to Obsidian (frontmatter, wikilinks, indexes, daily note) ──
    try:
        from obsidian_bridge import bridge_all as _bridge_all
        from daily_note import generate_daily as _gen_daily
        print(f"\n  🌐 Syncing to Obsidian...")
        _bridge_all()
        try:
            _gen_daily()
        except Exception:
            pass
    except Exception as _bridge_err:
        print(f"  ⚠️ Obsidian sync skipped: {_bridge_err}")

    # ── Build agent health card (QA trends + scratchpad usage + per-agent status) ──
    try:
        from agent_health import build_health_card as _build_health
        _build_health()
        print(f"  🩺 Agent health card updated")
    except Exception as _health_err:
        print(f"  ⚠️ Health card skipped: {_health_err}")

    # ── Agent 7: Research Journal — narrative behind-the-scenes record ──
    try:
        from research_journal import journal_latest_run as _journal
        jpath = _journal()
        if jpath:
            print(f"  📓 Research journal: {jpath.name}")
    except Exception as _journal_err:
        print(f"  ⚠️ Journal skipped: {_journal_err}")

    # ── Auto-organize misplaced files in vault (rules-based, conservative) ──
    try:
        from obsidian_organizer import scan as _org_scan, apply_moves as _org_apply
        s = _org_scan(smart_mode=False)
        if s["moves"]:
            applied = _org_apply(s["moves"])
            ok = sum(1 for c in applied if c.get("to"))
            print(f"  📂 Vault organized: {ok}/{len(s['moves'])} files moved")
    except Exception as _org_err:
        print(f"  ⚠️ Organizer skipped: {_org_err}")

    # ── Failure analysis (free, runs on analytics data) ──
    try:
        from failure_analyzer import analyze as _fa, md_report as _fa_report, push_to_scratchpad as _fa_push
        a = _fa(window_days=30)
        _fa_report(a)
        _fa_push(a)
        if a.get("alerts"):
            print(f"  🚨 Failure analyzer: {len(a['alerts'])} alert(s) — see _memory/failure_report.md")
    except Exception as _fa_err:
        print(f"  ⚠️ Failure analyzer skipped: {_fa_err}")

    # ── Performance learner (free, analyzes corpus patterns) ──
    try:
        from performance_learner import collect_posts as _pl_collect, split_top_bottom as _pl_split, compare as _pl_cmp, derive_insights as _pl_ins, md_report as _pl_md
        bp = {}
        for plat in ("linkedin", "blog", "podcast"):
            posts = _pl_collect(plat)
            if len(posts) < 10:
                bp[plat] = {"error": f"only {len(posts)} posts", "total": len(posts)}
                continue
            top, bot = _pl_split(posts)
            d = _pl_cmp(top, bot)
            bp[plat] = {"error": None, "total": len(posts), "n_top": len(top),
                        "n_bottom": len(bot), "diffs": d, "insights": _pl_ins(d)}
        _pl_md(bp)
        print(f"  📈 Performance patterns updated for {sum(1 for v in bp.values() if not v.get('error'))} platforms")
    except Exception as _pl_err:
        print(f"  ⚠️ Performance learner skipped: {_pl_err}")

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

LONG_RUN_THRESHOLD_MINS = 5  # pipeline ארוך מזה דורש אישור מפורש

def _classify_intent(user_input: str) -> dict:
    """Classify user intent using claude_cli."""
    prompt = f"""המשתמש אמר: "{user_input}"

החזר JSON עם:
  action: "run_pipeline" | "content_only" | "qa_check" | "edit" | "info" | "add_paper" | "chat"
  params: dict עם פרמטרים (topic, content_types, platform, url)
  eta_mins: הערכת זמן ריצה בדקות (מספר שלם)

JSON בלבד."""
    try:
        result = ask_claude_json(prompt, max_budget=0.3)
        # Backward compat: confirm_needed נגזר מ-eta_mins אם לא קיים
        if "confirm_needed" not in result:
            result["confirm_needed"] = result.get("eta_mins", 0) >= LONG_RUN_THRESHOLD_MINS
        return result
    except Exception:
        return {"action": "chat", "params": {}, "eta_mins": 1, "confirm_needed": False}


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
    try:
        return ask_claude(prompt, system=system, max_budget=0.3, timeout=120)
    except Exception as e:
        # CLIUnavailable or any CLI failure — don't crash the chat
        from claude_cli import CLIUnavailable
        if isinstance(e, CLIUnavailable) or "CLIUnavailable" in str(type(e)):
            return ("⚠️  Claude CLI לא זמין כרגע (תקלה רגעית). "
                    "נסה שוב בעוד רגע, או הוסף ANTHROPIC_API_KEY ל-.env ל-fallback.")
        return f"⚠️  שגיאה זמנית: {str(e)[:120]}"


def _chat_process(user_input: str, session: dict, auto: bool) -> str:
    """Process user message and execute actions."""
    from chat_commands import handle_chat_command

    result = handle_chat_command(user_input, session, auto)
    if result is not None:
        return result

    # General chat — no command matched
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

        try:
            response = _chat_process(user_input, session, auto)
        except KeyboardInterrupt:
            print("\n  🤖 מוקי: להתראות!")
            _save_session(session_mem)
            break
        except Exception as e:
            # Never let a single message crash the whole chat session
            from claude_cli import CLIUnavailable
            if isinstance(e, CLIUnavailable) or "CLIUnavailable" in str(type(e)):
                response = ("⚠️  Claude CLI לא זמין כרגע — תקלה רגעית. "
                            "נסה שוב, או הוסף ANTHROPIC_API_KEY ל-.env.")
            else:
                response = f"⚠️  שגיאה: {str(e)[:150]} — הצ'אט ממשיך."

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

def _cli_arg(flag: str) -> str | None:
    """Get value after a flag: --flag value → 'value'"""
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("-"):
            return sys.argv[idx + 1]
    return None


def _cli_args_after(flag: str) -> list[str]:
    """Get all values after a flag until next --flag"""
    if flag not in sys.argv:
        return []
    idx = sys.argv.index(flag) + 1
    result = []
    while idx < len(sys.argv) and not sys.argv[idx].startswith("-"):
        result.append(sys.argv[idx])
        idx += 1
    return result


def _cli_status():
    """Show detailed system status."""
    state = _read_system_state()
    files = state.get("existing_files", {})
    print(f"""
  📊 מצב מוקי
  {'─'*40}
  מחקרים:  {len(files.get('papers', []))} קבצים
  מאמרים:  {len(files.get('articles', []))} קבצים
  פוסטים:  {len(files.get('posts', []))} קבצים
  עיצובים: {len(files.get('designs', []))} קבצים

  🧠 זיכרון:
""")
    mem = load_memory()
    print(f"  נושאים שנחקרו: {len(mem.get('researched_topics', []))}")
    print(f"  מאמרים נאספו: {len(mem.get('papers', {}))}")
    print(f"  איטרציות: {mem.get('iterations', 0)}")
    if mem.get("topic_queue"):
        print(f"\n  🔮 הבא בתור:")
        for i, t in enumerate(mem["topic_queue"][:5], 1):
            print(f"    {i}. {t}")


def _cli_qa():
    """Run QA on all existing files."""
    from config import LINKEDIN_DIR, BLOG_DIR, PODCAST_DIR
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


def _cli_logs(n: int = 50):
    """Show last N lines of moki.log"""
    log_file = OUTPUT_DIR / "moki.log"
    if not log_file.exists():
        print("  אין לוג עדיין.")
        return
    lines = log_file.read_text(encoding="utf-8").splitlines()
    print(f"\n  📝 לוג (אחרונים {min(n, len(lines))} שורות):\n")
    for line in lines[-n:]:
        print(f"  {line}")


def _print_usage():
    print("""
╔══════════════════════════════════════════════════════════╗
║  🤖 מוקי — כל הפקודות מהטרמינל                           ║
╚══════════════════════════════════════════════════════════╝

🎯 Pipeline
  --chat                            מצב אינטראקטיבי
  --chat --auto                     ללא אישורים
  "בקשה" --auto                     one-shot
  --run --topic "X" --platforms linkedin blog   pipeline ממוקד
  --resume                          המשך מ-checkpoint

📊 מידע וסטטוס
  --status                          מצב מערכת (קבצים + זיכרון)
  --analytics                       דוח ביצועים מצטבר
  --last-run                        פירוט ריצה אחרונה
  --checkpoints                     סטטוס checkpoints
  --dashboard                       פותח דאשבורד בדפדפן
  --logs [N]                        N שורות אחרונות מהלוג (default: 50)

📚 ידע ותוכן
  --bib                             סטטיסטיקות ביבליוגרפיה
  --bib "query"                     חיפוש בביבליוגרפיה
  --search "query"                  חיפוש בהיסטוריית התוכן
  --summary                         סיכום שבועי
  --summary --save                  + שמירה לקובץ
  --add-paper "URL"                 הוספת מאמר ידני

✏️ עריכה ואיכות
  --qa                              בדיקת QA על כל הקבצים
  --edit article|linkedin|blog|podcast   עריכת קובץ אחרון

🧠 קונטקסט
  --context                         הצג קונטקסט אישי
  --context "טקסט"                  עדכון מהיר

📊 ביצועים (performance log)
  --perf                            הצג דוח ביצועי תוכן
  --perf-add                        הוסף ביצוע חדש (אינטראקטיבי)
  --perf-insights                   Claude מנתח מגמות

♻️ Repurposing
  --repurpose-list                  הצג קבצים זמינים להמרה
  --repurpose --from FILE --to linkedin blog podcast
  --repurpose --latest blog --to linkedin

📋 תור ועוד
  --queue                           הצג תור עדיפויות
  --queue-add "בקשה"                הוסף לתור
  --test                            הרץ בדיקות (tests.py --quick)

דוגמאות:
  python3 agent5_project_manager.py --chat
  python3 agent5_project_manager.py --run --topic "שייכות בחירום" --platforms linkedin blog
  python3 agent5_project_manager.py --bib "belonging"
  python3 agent5_project_manager.py --edit linkedin
  python3 agent5_project_manager.py --logs 100
""")


if __name__ == "__main__":
    args = sys.argv

    # ── Interactive / pipeline ──────────────────────
    if "--chat" in args:
        run_chat(auto="--auto" in args)

    elif "--run" in args:
        topic = _cli_arg("--topic") or "חינוך בלתי פורמלי"
        platforms = _cli_args_after("--platforms") or ["linkedin"]
        req = f"הרץ pipeline: נושא={topic} תוכן={' '.join(platforms)}"
        run_project_manager(req, auto_approve="--auto" in args)

    elif "--resume" in args:
        ckpt = Checkpoint.latest()
        if ckpt:
            print(f"  ♻️ ממשיך: {ckpt.summary()}")
            # Pipeline resume needs to be handled by orchestrator
            import subprocess
            planner_data = ckpt.get("planner") or {}
            title = planner_data.get("combined_title", "continue") if isinstance(planner_data, dict) else "continue"
            subprocess.run([sys.executable, "orchestrator.py", title, "--resume"])
        else:
            print("  אין checkpoint לחידוש.")

    # ── Info & Status ────────────────────────────
    elif "--status" in args:
        _cli_status()

    elif "--analytics" in args:
        tracker.print_report()

    elif "--last-run" in args:
        print(_explain_last_run())

    elif "--checkpoints" in args:
        Checkpoint.print_status()

    elif "--dashboard" in args:
        from dashboard import build_dashboard
        build_dashboard(open_browser=True)

    elif "--logs" in args:
        n = _cli_arg("--logs")
        _cli_logs(int(n) if n and n.isdigit() else 50)

    # ── Knowledge & Content ──────────────────────
    elif "--bib" in args:
        from bibliography import stats as bib_stats, search as bib_search
        query = _cli_arg("--bib")
        if query:
            results = bib_search(query)
            print(f"\n  🔍 {len(results)} תוצאות עבור '{query}':\n")
            for r in results[:15]:
                print(f"  [{r.get('year','?')}] {r.get('title','')[:60]}")
                print(f"    {r.get('authors','')[:40]} | {r.get('citation_count',0)} ציטוטים")
        else:
            print(bib_stats())

    elif "--search" in args:
        from history import print_search
        query = _cli_arg("--search")
        if query:
            print_search(query)
        else:
            print("  Usage: --search \"query\"")

    elif "--summary" in args:
        from weekly_summary import print_summary, save_summary
        print_summary()
        if "--save" in args:
            save_summary()

    elif "--add-paper" in args:
        url = _cli_arg("--add-paper")
        if url:
            from add_paper import add_from_url
            add_from_url(url, "manual")
            print(f"  ✅ מאמר נוסף: {url[:60]}")
        else:
            print("  Usage: --add-paper \"URL\"")

    # ── Editing & QA ─────────────────────────────
    elif "--qa" in args:
        _cli_qa()

    elif "--edit" in args:
        target = _cli_arg("--edit") or "article"
        from agent_editor import edit_article, edit_content
        from config import ARTICLES_DIR
        if target == "article":
            mds = sorted(ARTICLES_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime)
            if mds:
                edit_article({"md": mds[-1], "docx": mds[-1].with_suffix(".docx")})
                print(f"  ✅ מאמר נערך: {mds[-1].name}")
            else:
                print("  לא נמצא מאמר.")
        elif target in ("linkedin", "blog", "podcast"):
            r = edit_content(target)
            if r.get("file"):
                print(f"  ✅ {target} נערך: {r['file'].name}")
            else:
                print(f"  לא נמצא קובץ ל-{target}")
        else:
            print("  Usage: --edit article|linkedin|blog|podcast")

    # ── Context ──────────────────────────────────
    elif "--context" in args:
        from context_update import show_context, quick_update
        text = _cli_arg("--context")
        if text:
            quick_update(text)
        else:
            show_context()

    # ── Queue ────────────────────────────────────
    elif "--queue-add" in args:
        req = _cli_arg("--queue-add")
        if req:
            print(queue_add(req))
        else:
            print("  Usage: --queue-add \"בקשה\"")

    elif "--queue" in args:
        print(queue_status())

    # ── Performance log ──────────────────────────
    elif "--perf-add" in args:
        from performance_log import add_entry_interactive
        add_entry_interactive()

    elif "--perf-insights" in args:
        import subprocess
        subprocess.run([sys.executable, "performance_log.py", "--insights"])

    elif "--perf" in args:
        from performance_log import show_report
        show_report()

    # ── Repurposing ──────────────────────────────
    elif "--repurpose-list" in args:
        from repurpose_tool import list_available
        list_available()

    elif "--repurpose" in args:
        from repurpose_tool import repurpose, repurpose_all, _find_latest, PLATFORM_DIRS
        targets = _cli_args_after("--to")
        latest  = _cli_arg("--latest")
        src_arg = _cli_arg("--from")

        if not targets:
            print("  דרוש --to linkedin|blog|podcast")
        else:
            source = None
            if latest:
                source = _find_latest(latest)
                if not source:
                    print(f"  לא נמצאו קבצים ב-{latest}")
            elif src_arg:
                source = Path(src_arg)
                if not source.exists():
                    for d in PLATFORM_DIRS.values():
                        candidate = d / src_arg
                        if candidate.exists():
                            source = candidate
                            break
            else:
                print("  דרוש --from FILE או --latest PLATFORM")

            if source and source.exists():
                if len(targets) == 1:
                    repurpose(source, targets[0])
                else:
                    repurpose_all(source, targets)

    # ── Testing ──────────────────────────────────
    elif "--test" in args:
        import subprocess
        subprocess.run([sys.executable, "tests.py", "--quick"])

    # ── One-shot request ─────────────────────────
    elif len(args) > 1 and not args[1].startswith("-"):
        run_project_manager(args[1], auto_approve="--auto" in args)

    else:
        _print_usage()
