"""
history.py — חיפוש בהיסטוריית התוכן
מחפש במאמרים, פוסטים ומחקרים שנוצרו.

שימוש:
  python history.py "שייכות"           # חפש בכל התוכן
  python history.py --type article "hope"  # רק מאמרים
  python history.py --recent 5         # 5 אחרונים
  python history.py --list             # רשימת הכל

קוד:
  from history import search_content, recent_content, list_all
"""

import json
import re
import sys
import argparse
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR, PAPERS_DIR, ARTICLES_DIR, LINKEDIN_DIR, BLOG_DIR, PODCAST_DIR


# ─────────────────────────────────────────────
# Content types and locations
# ─────────────────────────────────────────────

CONTENT_DIRS = {
    "article":  (ARTICLES_DIR, ["*.md"]),
    "linkedin": (LINKEDIN_DIR, ["*_ready.txt", "*.txt"]),
    "blog":     (BLOG_DIR, ["*.md"]),
    "podcast":  (PODCAST_DIR, ["*_script_*.md"]),
    "research": (PAPERS_DIR, ["*.json"]),
}


def _get_files(content_type: str = None) -> list[dict]:
    """Get all content files with metadata."""
    files = []
    types = {content_type: CONTENT_DIRS[content_type]} if content_type else CONTENT_DIRS

    for ctype, (directory, patterns) in types.items():
        if not directory.exists():
            continue
        for pattern in patterns:
            for f in directory.glob(pattern):
                files.append({
                    "path": f,
                    "type": ctype,
                    "name": f.name,
                    "date": datetime.fromtimestamp(f.stat().st_mtime),
                    "size": f.stat().st_size,
                })

    files.sort(key=lambda x: x["date"], reverse=True)
    return files


def _read_preview(file_info: dict, max_chars: int = 300) -> str:
    """Read a preview of the file content."""
    f = file_info["path"]
    try:
        if f.suffix == ".json":
            data = json.loads(f.read_text(encoding="utf-8"))
            topic = data.get("topic", "") if isinstance(data, dict) else ""
            papers = data.get("papers", []) if isinstance(data, dict) else []
            return f"Topic: {topic} | {len(papers)} papers"
        else:
            text = f.read_text(encoding="utf-8", errors="replace")
            # Skip frontmatter
            if text.startswith("---"):
                end = text.find("---", 3)
                if end > 0:
                    text = text[end+3:].strip()
            # Get first meaningful line
            lines = [l.strip() for l in text.split("\n") if l.strip() and not l.startswith("#")]
            return " ".join(lines)[:max_chars]
    except Exception:
        return ""


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def search_content(query: str, content_type: str = None, limit: int = 10) -> list[dict]:
    """Search across all content files."""
    query_lower = query.lower()
    results = []

    for info in _get_files(content_type):
        f = info["path"]
        try:
            if f.suffix == ".json":
                text = f.read_text(encoding="utf-8")
            else:
                text = f.read_text(encoding="utf-8", errors="replace")

            if query_lower in text.lower():
                # Find matching snippet
                idx = text.lower().find(query_lower)
                start = max(0, idx - 50)
                end = min(len(text), idx + len(query) + 100)
                snippet = text[start:end].replace("\n", " ").strip()
                if start > 0:
                    snippet = "..." + snippet
                if end < len(text):
                    snippet = snippet + "..."

                results.append({
                    **info,
                    "snippet": snippet,
                })
        except Exception:
            continue

    return results[:limit]


def recent_content(limit: int = 10, content_type: str = None) -> list[dict]:
    """Get most recent content files."""
    files = _get_files(content_type)[:limit]
    for f in files:
        f["preview"] = _read_preview(f)
    return files


def list_all(content_type: str = None) -> dict[str, list[dict]]:
    """List all content grouped by type."""
    files = _get_files(content_type)
    grouped = {}
    for f in files:
        grouped.setdefault(f["type"], []).append(f)
    return grouped


def print_search(query: str, content_type: str = None):
    results = search_content(query, content_type)
    if not results:
        print(f"\n  לא נמצאו תוצאות עבור '{query}'")
        return

    print(f"\n  🔍 {len(results)} תוצאות עבור '{query}':\n")
    for r in results:
        icon = {"article": "📝", "linkedin": "💼", "blog": "📰",
                "podcast": "🎙️", "research": "📚"}.get(r["type"], "📄")
        date = r["date"].strftime("%d/%m/%Y")
        print(f"  {icon} [{date}] {r['name']}")
        print(f"     {r['snippet'][:80]}")
        print()


def print_recent(limit: int = 10, content_type: str = None):
    files = recent_content(limit, content_type)
    if not files:
        print("\n  אין תוכן.")
        return

    print(f"\n  📁 {len(files)} קבצים אחרונים:\n")
    for f in files:
        icon = {"article": "📝", "linkedin": "💼", "blog": "📰",
                "podcast": "🎙️", "research": "📚"}.get(f["type"], "📄")
        date = f["date"].strftime("%d/%m/%Y %H:%M")
        size = f"{f['size']/1024:.0f}KB"
        print(f"  {icon} [{date}] {f['name']} ({size})")
        if f.get("preview"):
            print(f"     {f['preview'][:70]}")
        print()


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="חיפוש בהיסטוריית התוכן")
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--type", choices=["article", "linkedin", "blog", "podcast", "research"])
    parser.add_argument("--recent", type=int, metavar="N", help="הצג N אחרונים")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        grouped = list_all(args.type)
        for ctype, files in grouped.items():
            print(f"\n  ── {ctype} ({len(files)}) ──")
            for f in files[:8]:
                date = f["date"].strftime("%d/%m")
                print(f"    {date} {f['name']}")

    elif args.recent:
        print_recent(args.recent, args.type)

    elif args.query:
        print_search(args.query, args.type)

    else:
        print_recent(8)
