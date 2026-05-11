"""
newsletter.py — Weekly digest aggregator.
פעם בשבוע — אגגרגציה של 3-5 פוסטים אחרונים → newsletter draft.

Usage:
  python3 newsletter.py                    # generate weekly digest
  python3 newsletter.py --days 14          # past 14 days
"""

import sys
import json
from pathlib import Path
from datetime import datetime, timedelta

from config import OUTPUT_DIR, LINKEDIN_DIR, BLOG_DIR

DIGEST_DIR = OUTPUT_DIR / "newsletters"
DIGEST_DIR.mkdir(parents=True, exist_ok=True)


def _recent_posts(days: int = 7) -> list[dict]:
    """Collect posts from last N days across all platforms."""
    cutoff = datetime.now() - timedelta(days=days)
    posts = []

    sources = [
        (LINKEDIN_DIR, "*ready*.txt", "LinkedIn"),
        (BLOG_DIR, "*.md", "Blog"),
    ]
    for d, pattern, kind in sources:
        if not d.exists():
            continue
        for f in d.glob(pattern):
            if f.name.endswith(".bak"):
                continue
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff:
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            # Extract title from first meaningful line
            title = ""
            for line in text.split("\n"):
                line = line.strip().lstrip("#").strip()
                if line and not line.startswith("-") and len(line) > 10:
                    title = line[:80]
                    break
            # First paragraph as teaser
            paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 30]
            teaser = paragraphs[1] if len(paragraphs) > 1 else (paragraphs[0] if paragraphs else "")[:300]

            posts.append({
                "title": title or f.stem,
                "kind": kind,
                "file": f.name,
                "date": mtime.strftime("%d/%m"),
                "teaser": teaser[:300],
                "ts": mtime,
            })
    posts.sort(key=lambda x: x["ts"], reverse=True)
    return posts


def generate_digest(days: int = 7) -> Path | None:
    """Build a weekly digest as Markdown."""
    posts = _recent_posts(days)
    if not posts:
        print(f"  ℹ️  No posts in last {days} days")
        return None

    week_label = datetime.now().strftime("%d/%m/%Y")
    out_path = DIGEST_DIR / f"newsletter_{datetime.now().strftime('%Y%m%d')}.md"

    lines = [
        f"# מוקי 🦊 — סיכום שבועי {week_label}",
        f"",
        f"_{len(posts)} פרסומים בשבוע האחרון_",
        f"",
        f"---",
        f"",
    ]

    # Group by kind
    from collections import defaultdict
    by_kind = defaultdict(list)
    for p in posts:
        by_kind[p["kind"]].append(p)

    for kind, items in by_kind.items():
        emoji = {"LinkedIn": "💼", "Blog": "📰", "Podcast": "🎙️"}.get(kind, "📄")
        lines.append(f"## {emoji} {kind}")
        lines.append("")
        for p in items:
            lines.append(f"### {p['title']}")
            lines.append(f"_{p['date']}_")
            lines.append("")
            lines.append(p["teaser"])
            lines.append("")
            lines.append(f"_[קובץ: {p['file']}]_")
            lines.append("")
            lines.append("---")
            lines.append("")

    # Footer
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("_מוקי הוא צוות AI שמייצר תוכן בקולו של פז שלמה._")
    lines.append("_ריצה אוטומטית: שני, רביעי, חמישי בבוקר._")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def main():
    days = 7
    if "--days" in sys.argv:
        idx = sys.argv.index("--days")
        if idx + 1 < len(sys.argv):
            try:
                days = int(sys.argv[idx + 1])
            except ValueError:
                pass
    path = generate_digest(days=days)
    if path:
        print(f"\n✅ Newsletter saved: {path}")
        print(f"   {path.read_text(encoding='utf-8').count(chr(10))} lines")


if __name__ == "__main__":
    main()
