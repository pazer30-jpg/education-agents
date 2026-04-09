"""
weekly_summary.py — סיכום שבועי אוטומטי
מרכז את כל מה שמוקי עשה בשבוע האחרון:
  - כמה ריצות, הצלחות, כשלים
  - נושאים שכוסו
  - מאמרים שנכתבו
  - תוכן שנוצר
  - ציוני QA
  - פערים ונושאים הבאים בתור

פלט: Markdown + הדפסה לטרמינל
שימוש:
  python weekly_summary.py              # סיכום שבועי
  python weekly_summary.py --days 14    # שבועיים אחורה
  python weekly_summary.py --save       # שמור לקובץ
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter

from config import OUTPUT_DIR, PAPERS_DIR, ARTICLES_DIR, LINKEDIN_DIR, BLOG_DIR, PODCAST_DIR
from memory import load_memory


SUMMARY_DIR = OUTPUT_DIR / "summaries"
SUMMARY_DIR.mkdir(parents=True, exist_ok=True)


def _load_analytics() -> dict:
    f = OUTPUT_DIR / "analytics.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"runs": []}


def _files_since(directory: Path, since: datetime, patterns: list[str]) -> list[Path]:
    """Find files modified since a given date."""
    files = []
    if not directory.exists():
        return files
    for pattern in patterns:
        for f in directory.glob(pattern):
            if datetime.fromtimestamp(f.stat().st_mtime) >= since:
                files.append(f)
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def generate_summary(days: int = 7) -> str:
    """Generate weekly summary as Markdown string."""
    since = datetime.now() - timedelta(days=days)
    since_str = since.strftime("%d/%m/%Y")
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    # ── Analytics data ────────────────────────
    analytics = _load_analytics()
    recent_runs = [
        r for r in analytics.get("runs", [])
        if r.get("started_at", "") >= since.isoformat()
    ]

    total_runs = len(recent_runs)
    successes = sum(1 for r in recent_runs if r.get("success"))
    failures = total_runs - successes

    durations = [r["duration_s"] for r in recent_runs if r.get("duration_s")]
    avg_duration = sum(durations) / len(durations) / 60 if durations else 0
    total_time = sum(durations) / 60

    qa_scores = [r.get("avg_qa") for r in recent_runs if r.get("avg_qa")]
    avg_qa = sum(qa_scores) / len(qa_scores) if qa_scores else 0

    topics = [r.get("topic", "") for r in recent_runs if r.get("topic")]
    topic_counts = Counter(topics)

    errors = []
    for r in recent_runs:
        errors.extend(r.get("errors", []))

    # ── Files created ─────────────────────────
    new_papers = _files_since(PAPERS_DIR, since, ["*.json"])
    new_articles = _files_since(ARTICLES_DIR, since, ["*.md"])
    new_linkedin = _files_since(LINKEDIN_DIR, since, ["*.txt"])
    new_blog = _files_since(BLOG_DIR, since, ["*.md"])
    new_podcast = _files_since(PODCAST_DIR, since, ["*.md"])
    new_designs = _files_since(OUTPUT_DIR / "designs", since, ["*.svg"])

    # ── Memory state ──────────────────────────
    mem = load_memory()
    total_papers = len(mem.get("papers", {}))
    total_topics = len(mem.get("researched_topics", []))
    total_articles = len(mem.get("articles", []))
    total_content = len(mem.get("content_created", []))
    queue = mem.get("topic_queue", [])[:5]
    gaps = mem.get("gaps", [])[:5]

    # ── Build summary ─────────────────────────
    md = f"""# 📊 סיכום שבועי — מוקי
**תקופה:** {since_str} — {now_str}

---

## 🏃 ריצות

| מדד | ערך |
|-----|-----|
| סה"כ ריצות | {total_runs} |
| הצלחות | {successes} ({successes/max(1,total_runs)*100:.0f}%) |
| כשלים | {failures} |
| זמן ממוצע | {avg_duration:.1f} דק' |
| סה"כ זמן | {total_time:.0f} דק' |
| QA ממוצע | {avg_qa:.0f}/100 |

"""

    if topic_counts:
        md += "## 📚 נושאים שטופלו\n\n"
        for topic, count in topic_counts.most_common(10):
            md += f"- **{topic}** ({count}x)\n"
        md += "\n"

    md += f"""## 📁 קבצים שנוצרו

| סוג | כמות |
|-----|------|
| מחקרים (JSON) | {len(new_papers)} |
| מאמרים (MD) | {len(new_articles)} |
| LinkedIn | {len(new_linkedin)} |
| בלוג | {len(new_blog)} |
| פודקאסט | {len(new_podcast)} |
| עיצובים (SVG) | {len(new_designs)} |
| **סה"כ** | **{len(new_papers)+len(new_articles)+len(new_linkedin)+len(new_blog)+len(new_podcast)+len(new_designs)}** |

"""

    if new_articles:
        md += "### מאמרים שנכתבו\n\n"
        for f in new_articles[:5]:
            title = f.read_text(encoding="utf-8").split("\n")[0].lstrip("# ").strip()[:60]
            md += f"- {title} (`{f.name}`)\n"
        md += "\n"

    if new_linkedin:
        md += "### פוסטי LinkedIn\n\n"
        for f in new_linkedin[:5]:
            preview = f.read_text(encoding="utf-8")[:80].replace("\n", " ")
            md += f"- {preview}... (`{f.name}`)\n"
        md += "\n"

    md += f"""## 🧠 מצב מצטבר

| מדד | סה"כ |
|-----|------|
| נושאים שנחקרו | {total_topics} |
| מאמרים שנאספו | {total_papers} |
| מאמרים שנכתבו | {total_articles} |
| תוכן שנוצר | {total_content} |

"""

    if queue:
        md += "## 🔮 הבא בתור\n\n"
        for i, t in enumerate(queue, 1):
            md += f"{i}. {t}\n"
        md += "\n"

    if gaps:
        md += "## ⚠️ פערים שזוהו\n\n"
        for g in gaps:
            md += f"- {g}\n"
        md += "\n"

    if errors:
        md += f"## 🔴 שגיאות ({len(errors)})\n\n"
        error_agents = Counter(e.get("agent", "?") for e in errors)
        for agent, count in error_agents.most_common():
            md += f"- **{agent}**: {count} שגיאות\n"
        md += "\n"

    md += f"---\n*נוצר אוטומטית ב-{now_str}*\n"
    return md


def print_summary(days: int = 7):
    """Print summary to terminal."""
    md = generate_summary(days)
    # Convert markdown to terminal-friendly
    for line in md.split("\n"):
        if line.startswith("# "):
            print(f"\n{'='*55}")
            print(f"  {line[2:]}")
            print(f"{'='*55}")
        elif line.startswith("## "):
            print(f"\n  {line[3:]}")
            print(f"  {'─'*40}")
        elif line.startswith("### "):
            print(f"\n  {line[4:]}")
        elif line.startswith("| ") and "---" not in line:
            print(f"  {line}")
        elif line.startswith("- "):
            print(f"    {line}")
        elif line.startswith("---"):
            pass
        elif line.strip():
            print(f"  {line}")


def save_summary(days: int = 7) -> Path:
    """Save summary to file."""
    md = generate_summary(days)
    ts = datetime.now().strftime("%Y%m%d")
    path = SUMMARY_DIR / f"weekly_{ts}.md"
    path.write_text(md, encoding="utf-8")
    print(f"  💾 סיכום נשמר: {path}")
    return path


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    days = 7
    if "--days" in sys.argv:
        idx = sys.argv.index("--days")
        days = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 7

    print_summary(days)

    if "--save" in sys.argv:
        save_summary(days)
