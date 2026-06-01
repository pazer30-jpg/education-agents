"""
research_journal.py — Agent 7: Research Journal (lab notebook).

Documents everything behind the scenes — for the human team.
After each pipeline run, synthesizes the raw artifacts into a readable
Hebrew narrative entry: what was researched, why, the process, questions
that came up, what the agents said to each other, decisions, what failed.

Reads (raw signals the pipeline already produces):
  - analytics.json        — the run (topic, steps, QA, errors, durations)
  - _scratchpad.json      — inter-agent messages
  - proposals/*.md        — the research proposal + reasoning
  - ideas/*.md            — research questions raised
  - checkpoints/*.json    — what deferred
  - _memory/failure_report.md — failure patterns

Writes:
  - output/_journal/<stamp>.md       — one narrative entry per run
  - output/_journal/_יומן-ראשי.md   — running index

Usage:
  python3 research_journal.py              # journal the latest run
  python3 research_journal.py --backfill 5 # journal last 5 runs
"""

import sys
import json
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR

try:
    from claude_cli import ask_claude
except Exception:
    ask_claude = None


JOURNAL_DIR = OUTPUT_DIR / "_journal"
INDEX_FILE = JOURNAL_DIR / "_יומן-ראשי.md"


# ─────────────────────────────────────────────
# Gather raw signals from one run
# ─────────────────────────────────────────────

def _load_runs() -> list[dict]:
    f = OUTPUT_DIR / "analytics.json"
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("runs", [])
    except Exception:
        return []


def _load_scratchpad() -> dict:
    f = OUTPUT_DIR / "_scratchpad.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _latest_file(folder: str, pattern: str = "*.md") -> Path | None:
    d = OUTPUT_DIR / folder
    if not d.exists():
        return None
    files = list(d.glob(pattern))
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _gather_run_context(run: dict) -> dict:
    """Collect every behind-the-scenes signal for one run."""
    ctx = {
        "topic": run.get("topic", ""),
        "started_at": run.get("started_at", ""),
        "duration_min": round(run.get("duration_s", 0) / 60, 1),
        "success": run.get("success"),
        "steps": [],
        "qa_scores": run.get("qa_scores", {}),
        "errors": [],
        "outputs": run.get("outputs", []),
    }
    for s in run.get("steps", []):
        ctx["steps"].append({
            "agent": s.get("agent"),
            "status": s.get("status", "ok"),
            "duration_s": s.get("duration_s", 0),
            "note": s.get("note", ""),
        })
    for e in run.get("errors", []):
        ctx["errors"].append(e.get("error", "") if isinstance(e, dict) else str(e))

    # Inter-agent messages (scratchpad)
    scratch = _load_scratchpad()
    msgs = []
    for agent, entries in scratch.items():
        for key, entry in entries.items():
            v = entry.get("value")
            summary = (v.get("summary") or v.get("issue") or str(v)[:150]
                       if isinstance(v, dict) else str(v)[:150])
            msgs.append(f"{agent}/{key}: {summary}")
    ctx["agent_messages"] = msgs

    # Latest proposal (reasoning) — first 1500 chars
    prop = _latest_file("proposals")
    if prop:
        try:
            ctx["proposal_excerpt"] = prop.read_text(encoding="utf-8")[:1500]
        except Exception:
            ctx["proposal_excerpt"] = ""

    # Latest research ideas / RQs
    idea = _latest_file("ideas")
    if idea:
        try:
            ctx["ideas_excerpt"] = idea.read_text(encoding="utf-8")[:1200]
        except Exception:
            ctx["ideas_excerpt"] = ""

    return ctx


# ─────────────────────────────────────────────
# Synthesize a narrative journal entry
# ─────────────────────────────────────────────

_JOURNAL_PROMPT = """אתה כותב יומן מחקר (lab notebook) של צוות סוכני AI לחינוך.
הקהל: אנשי הצוות האנושיים. המטרה: שקיפות מלאה — מה קרה מאחורי הקלעים.

נתוני הריצה:
{run_data}

כתוב רשומת יומן בעברית, נרטיבית וכנה — לא דוח יבש. מבנה:

## 🔬 מה חקרנו
הנושא, ובמשפט — למה הוא נבחר.

## 🧭 התהליך
איך זה התקדם — אילו סוכנים רצו, מה כל אחד עשה, כמה זמן.

## ❓ שאלות שעלו בדרך
שאלות מחקר, מתחים, ספקות שצצו תוך כדי. אם אין — כתוב "לא תועדו שאלות פתוחות".

## 💬 שיח בין הסוכנים
מה הסוכנים אמרו זה לזה (scratchpad) — בשפה אנושית. אם ריק — דלג.

## ⚖️ החלטות והכרעות
מה הוחלט — אישור הצעה, שערי QA, מה התקבל ומה נדחה.

## 🚧 מה נכשל או נדחה
כנות מלאה — אם משהו נכשל, מה ולמה. אל תייפה.

## 💡 תובנה להמשך
משפט-שניים — מה ללמוד לריצה הבאה.

כתוב כמו חוקר שמתעד לעצמו ולצוות — ישיר, ענייני, בלי קישוטים. 250-400 מילים."""


def synthesize_entry(ctx: dict) -> str:
    """Use Claude to turn raw run data into a narrative journal entry."""
    if ask_claude is None:
        return _fallback_entry(ctx)
    run_data = json.dumps(ctx, ensure_ascii=False, indent=1)[:6000]

    # ── Inject system memory: strong/weak topics + failure trends ──
    mem_block = ""
    try:
        from obsidian_memory import format_for_prompt as _obs_for_prompt
        mem_block = _obs_for_prompt(
            ["strong_topics", "weak_topics", "failure_report"],
            max_chars_per_note=800,
        )
    except Exception:
        pass
    context_section = ""
    if mem_block:
        context_section = (f"\n\n--- מגמות מהריצות הקודמות (לשימוש בקטע 'תובנה') ---\n"
                           f"{mem_block}\n--- end ---\n")

    prompt = _JOURNAL_PROMPT.format(run_data=run_data) + context_section
    try:
        # Journal is post-pipeline + has structured fallback — one attempt only.
        # 3× 180s retries cost ~10 min for a fully-optional narrative entry.
        return ask_claude(prompt, max_budget=0.4, timeout=180,
                          max_retries=1).strip()
    except Exception as e:
        print(f"  [Journal] synthesis failed ({e}) — using structured fallback")
        return _fallback_entry(ctx)


def _fallback_entry(ctx: dict) -> str:
    """Structured (non-narrative) entry when Claude is unavailable."""
    lines = ["## 🔬 מה חקרנו", "", ctx.get("topic", "?"), ""]
    lines.append("## 🧭 התהליך")
    lines.append("")
    for s in ctx.get("steps", []):
        icon = "✅" if s["status"] == "ok" else "❌"
        lines.append(f"- {icon} {s['agent']} — {s['duration_s']:.0f}s")
    lines.append("")
    if ctx.get("agent_messages"):
        lines.append("## 💬 שיח בין הסוכנים")
        lines.append("")
        for m in ctx["agent_messages"][:10]:
            lines.append(f"- {m}")
        lines.append("")
    if ctx.get("errors"):
        lines.append("## 🚧 מה נכשל או נדחה")
        lines.append("")
        for e in ctx["errors"][:5]:
            lines.append(f"- {e[:200]}")
        lines.append("")
    lines.append("_רשומה אוטומטית (Claude לא היה זמין לסינתזה נרטיבית)._")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Write entry + update index
# ─────────────────────────────────────────────

def write_entry(run: dict) -> Path:
    ctx = _gather_run_context(run)
    body = synthesize_entry(ctx)

    started = ctx.get("started_at", "")[:16].replace("T", " ") or \
        datetime.now().strftime("%Y-%m-%d %H:%M")
    stamp = (ctx.get("started_at", "")[:16].replace("T", "_").replace(":", "")
             or datetime.now().strftime("%Y-%m-%d_%H%M"))

    qa = ctx.get("qa_scores", {})
    avg_qa = round(sum(qa.values()) / len(qa)) if qa else "—"
    status_icon = "✅" if ctx.get("success") else "⚠️"

    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    entry_path = JOURNAL_DIR / f"{stamp}.md"

    full = "\n".join([
        "---",
        "moki: true",
        "type: research_journal",
        f"date: {started[:10]}",
        f"updated: {datetime.now().isoformat(timespec='seconds')}",
        "---",
        "",
        f"# 📓 יומן מחקר — {started}",
        "",
        f"_{status_icon} משך: {ctx['duration_min']} דק' · QA ממוצע: {avg_qa} · "
        f"{len(ctx.get('outputs', []))} תוצרים_",
        "",
        body,
        "",
        "---",
        f"_נכתב אוטומטית ע\"י Agent 7 — Research Journal · {datetime.now().strftime('%d/%m/%Y %H:%M')}_",
    ])
    entry_path.write_text(full, encoding="utf-8")
    _update_index()
    return entry_path


def _update_index():
    """Rebuild the running journal index."""
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    entries = sorted(
        (p for p in JOURNAL_DIR.glob("*.md") if not p.stem.startswith("_")),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    lines = [
        "---",
        "moki: true",
        "type: journal_index",
        "---",
        "",
        "# 📓 יומן המחקר של מוקי",
        "",
        "> תיעוד מאחורי-הקלעים של כל ריצה — לאנשי הצוות.",
        "> כל רשומה: מה נחקר, התהליך, שאלות שעלו, החלטות, מה נכשל.",
        "",
        f"_{len(entries)} רשומות._",
        "",
        "## 📅 רשומות",
        "",
    ]
    for p in entries[:60]:
        # Pull the title line
        title = p.stem
        try:
            for line in p.read_text(encoding="utf-8").split("\n"):
                if line.startswith("# 📓"):
                    title = line[2:].strip()
                    break
        except Exception:
            pass
        lines.append(f"- [[{p.stem}|{title}]]")
    INDEX_FILE.write_text("\n".join(lines), encoding="utf-8")


# ─────────────────────────────────────────────
# Entry point — called by agent5 after each run
# ─────────────────────────────────────────────

def journal_latest_run() -> Path | None:
    """Journal the most recent run. Called at end of pipeline."""
    runs = _load_runs()
    if not runs:
        return None
    return write_entry(runs[-1])


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    runs = _load_runs()
    if not runs:
        print("⚠️ No runs in analytics.json")
        return

    if "--backfill" in sys.argv:
        idx = sys.argv.index("--backfill")
        n = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) and sys.argv[idx+1].isdigit() else 5
        print(f"📓 Backfilling journal for last {n} runs...")
        for run in runs[-n:]:
            path = write_entry(run)
            print(f"  ✅ {path.name}")
        return

    path = journal_latest_run()
    if path:
        print(f"📓 Journal entry: {path.relative_to(OUTPUT_DIR.parent)}")
        print(f"   Index: {INDEX_FILE.relative_to(OUTPUT_DIR.parent)}")


if __name__ == "__main__":
    main()
