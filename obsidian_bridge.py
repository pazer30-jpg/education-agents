"""
obsidian_bridge.py — Convert Moki output to Obsidian-friendly format.

מה זה עושה:
  1. מוסיף frontmatter עם tags + backlinks לכל פוסט/מאמר
  2. ממיר ציטוטים (Author, Year) → wikilinks [[Author Year]]
  3. בונה דף 'אינדקס מקורות' עם כל ההוגים המצוטטים
  4. בונה דף 'מפה' של קישורים בין פוסטים

Usage:
  python3 obsidian_bridge.py                 # transform all
  python3 obsidian_bridge.py --dry-run       # preview
  python3 obsidian_bridge.py --citations-only # only citation→wikilink
"""

import sys
import re
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from config import OUTPUT_DIR, ARTICLES_DIR, LINKEDIN_DIR, BLOG_DIR, PODCAST_DIR

# Index pages
SOURCES_INDEX = OUTPUT_DIR / "_מקורות.md"
TOPICS_INDEX  = OUTPUT_DIR / "_נושאים.md"
BACKLINK_DIR  = OUTPUT_DIR / "_authors"  # one .md per author


# ─────────────────────────────────────────────
# Theme detection (for tags)
# ─────────────────────────────────────────────

THEME_KEYWORDS = {
    "שייכות": ["שייכות", "belonging"],
    "חוסן": ["חוסן", "resilience"],
    "מנהיגות": ["מנהיגות", "leadership"],
    "טראומה": ["טראומה", "trauma"],
    "דיאלוג": ["דיאלוג", "dialogue"],
    "זהות": ["זהות", "identity"],
    "תקווה": ["תקווה", "hope"],
    "פדגוגיה": ["פדגוגיה", "pedagogy"],
    "תנועות-נוער": ["תנועות נוער", "youth movements"],
    "חירום": ["חירום", "crisis", "emergency", "war"],
    "זיכרון": ["זיכרון", "memory", "remembrance"],
    "בלתי-פורמלי": ["בלתי פורמלי", "non-formal", "informal"],
}


def detect_themes(text: str) -> list[str]:
    """Return list of theme tags present in text."""
    lower = text.lower()
    themes = []
    for theme, keywords in THEME_KEYWORDS.items():
        if any(kw.lower() in lower for kw in keywords):
            themes.append(theme)
    return themes


# ─────────────────────────────────────────────
# Citation parsing → wikilinks
# ─────────────────────────────────────────────

# Match: (Author, Year) or (Author Year) or Author (Year) — Hebrew + English
CITE_PATTERNS = [
    # (Smith, 2019)  /  (Smith & Jones, 2019)
    re.compile(r"\(([A-Z][a-zA-Z\-']+(?:\s+&\s+[A-Z][a-zA-Z\-']+)?)(?:,?\s+(?:et\s+al\.,?))?,?\s+(\d{4})\)"),
    # Hebrew: (בובר, 1923)
    re.compile(r"\(([֐-׿']+(?:[\s־-][֐-׿']+)?),?\s+(\d{4})\)"),
    # Author (Year) — narrative
    re.compile(r"\b([A-Z][a-zA-Z\-']+(?:\s+et\s+al\.)?)\s+\((\d{4})\)"),
]


def citations_to_wikilinks(text: str, collected: dict = None) -> str:
    """Replace (Author, Year) with [[Author Year]] for Obsidian backlinks."""
    if collected is None:
        collected = {}

    def _replace(match):
        author = match.group(1).strip()
        year = match.group(2).strip()
        # Skip if author is just a year or empty
        if not author or author.isdigit():
            return match.group(0)
        # Normalize author key
        author_clean = re.sub(r"\s+&\s+.*", "", author).strip()
        author_clean = re.sub(r"\s+et\s+al\.?", "", author_clean).strip()
        link_key = f"{author_clean} {year}"
        collected[link_key] = collected.get(link_key, 0) + 1
        # Preserve the original parens style
        if match.group(0).startswith("("):
            return f"([[{link_key}]])"
        return f"[[{link_key}]]"

    for pattern in CITE_PATTERNS:
        text = pattern.sub(_replace, text)

    return text


# ─────────────────────────────────────────────
# Frontmatter injection
# ─────────────────────────────────────────────

def has_frontmatter(text: str) -> bool:
    return text.lstrip().startswith("---\n")


def inject_frontmatter(text: str, fields: dict) -> str:
    """Add or merge YAML frontmatter."""
    if has_frontmatter(text):
        return text  # don't touch existing

    lines = ["---"]
    for k, v in fields.items():
        if isinstance(v, list):
            if v:
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f"  - {item}")
        elif v:
            lines.append(f'{k}: "{v}"')
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + text


# ─────────────────────────────────────────────
# Bridge a single file
# ─────────────────────────────────────────────

def bridge_file(path: Path, dry_run: bool = False, all_authors: dict = None) -> dict:
    """
    Add frontmatter + convert citations to wikilinks.
    Returns: {"path": ..., "themes": [...], "citations_added": int, "modified": bool}
    """
    if all_authors is None:
        all_authors = {}

    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return {"path": str(path), "error": str(e)}

    original = text
    file_authors = {}

    # Convert citations
    text = citations_to_wikilinks(text, file_authors)
    for k, v in file_authors.items():
        all_authors[k] = all_authors.get(k, 0) + v

    # Detect themes
    themes = detect_themes(text)

    # Build frontmatter
    if not has_frontmatter(text):
        # Determine kind from path
        if "linkedin" in path.name.lower():
            kind = "linkedin"
        elif "podcast" in path.name.lower():
            kind = "podcast"
        elif "blog" in path.name.lower():
            kind = "blog"
        elif "briefing" in path.name.lower():
            kind = "briefing"
        elif "_he" in path.name.lower() or "_en" in path.name.lower():
            kind = "article"
        else:
            kind = "doc"

        # Title = first non-empty heading or filename
        title = path.stem
        for line in text.split("\n"):
            stripped = line.strip().lstrip("#").strip()
            if stripped and len(stripped) > 5 and not stripped.startswith("---"):
                title = stripped[:100]
                break

        # Smart tags: detect quality issues from related files
        smart_tags = []
        # 1. kill_switch: if devil's advocate file flagged this post
        try:
            devil_file = OUTPUT_DIR / "devil" / f"{path.stem}_devil.md"
            if devil_file.exists() and "kill_switch" in devil_file.read_text(encoding="utf-8", errors="ignore"):
                smart_tags.append("status/blocked")
        except Exception:
            pass
        # 2. weak methodology: if related paper analysis flagged
        if "weak" in text.lower() or "methodology weakness" in text.lower():
            smart_tags.append("status/weak-methodology")
        # 3. needs review: low QA score in nearby file
        try:
            qa_file = OUTPUT_DIR / "qa_scores.json"
            if qa_file.exists():
                import json as _j
                qa_data = _j.loads(qa_file.read_text(encoding="utf-8"))
                score = qa_data.get(path.stem, {}).get("score", 100)
                if score < 70:
                    smart_tags.append("status/needs-review")
                elif score >= 90:
                    smart_tags.append("status/strong")
        except Exception:
            pass
        # 4. arc membership: if part of detected arc
        try:
            arcs_dir = OUTPUT_DIR / "_arcs"
            if arcs_dir.exists():
                for arc_file in arcs_dir.glob("arc_*.json"):
                    arc_data = json.loads(arc_file.read_text(encoding="utf-8"))
                    for post in arc_data.get("posts", []):
                        if path.stem in (post.get("post_id", "") or ""):
                            smart_tags.append(f"arc/{arc_data.get('arc_id', 'unknown')}")
                            break
        except Exception:
            pass

        fm = {
            "title": title,
            "kind": kind,
            "tags": [f"moki/{kind}"] + [f"theme/{t}" for t in themes] + smart_tags,
            "date": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d"),
            "moki": True,
        }
        text = inject_frontmatter(text, fm)

    modified = text != original

    if modified and not dry_run:
        path.write_text(text, encoding="utf-8")

    return {
        "path": str(path.relative_to(OUTPUT_DIR)),
        "themes": themes,
        "citations": len(file_authors),
        "modified": modified,
    }


# ─────────────────────────────────────────────
# Author/source index pages
# ─────────────────────────────────────────────

def build_sources_index(all_authors: dict) -> Path:
    """Generate _מקורות.md — list of all cited authors with backlinks."""
    if not all_authors:
        return None

    by_author = defaultdict(list)
    for key, count in all_authors.items():
        # Split "Author 2020" → ("Author", "2020")
        parts = key.rsplit(" ", 1)
        if len(parts) == 2:
            author, year = parts
            by_author[author].append((year, count))

    lines = [
        "---",
        "title: 📚 אינדקס מקורות",
        "tags: [moki/index]",
        "---",
        "",
        f"# 📚 אינדקס מקורות",
        f"",
        f"_Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')} · {sum(all_authors.values())} ציטוטים סה\"כ_",
        f"",
    ]

    sorted_authors = sorted(
        by_author.items(),
        key=lambda x: -sum(c for _, c in x[1]),
    )

    for author, items in sorted_authors:
        total = sum(c for _, c in items)
        lines.append(f"## {author}")
        for year, count in sorted(items, key=lambda x: -x[1]):
            lines.append(f"- [[{author} {year}]] — מצוטט {count}x")
        lines.append(f"_סך הכל: {total} ציטוטים_")
        lines.append("")

    SOURCES_INDEX.write_text("\n".join(lines), encoding="utf-8")
    return SOURCES_INDEX


# ─────────────────────────────────────────────
# Topics index
# ─────────────────────────────────────────────

def build_topics_index() -> Path:
    """Generate _נושאים.md from coverage_map."""
    from memory import load_memory
    mem = load_memory()
    coverage = mem.get("coverage_map", {})
    if not coverage:
        return None

    lines = [
        "---",
        "title: 🗺 אינדקס נושאים",
        "tags: [moki/index]",
        "---",
        "",
        "# 🗺 אינדקס נושאים",
        "",
        f"_{len(coverage)} נושאים בקורפוס_",
        "",
    ]

    for status, threshold in [("🔴 BLOCKED", 7), ("🟡 CAUTION", 4), ("🟢 AVAILABLE", 0)]:
        if threshold == 7:
            items = [(t, s) for t, s in coverage.items() if s >= 7]
        elif threshold == 4:
            items = [(t, s) for t, s in coverage.items() if 4 <= s < 7]
        else:
            items = [(t, s) for t, s in coverage.items() if s < 4]
        if items:
            lines.append(f"## {status} ({len(items)})")
            lines.append("")
            for topic, score in sorted(items, key=lambda x: -x[1])[:30]:
                lines.append(f"- {topic} — {score} pts")
            lines.append("")

    TOPICS_INDEX.write_text("\n".join(lines), encoding="utf-8")
    return TOPICS_INDEX


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def bridge_all(dry_run: bool = False, citations_only: bool = False) -> dict:
    all_authors = {}
    results = []

    targets = [
        ARTICLES_DIR.glob("*.md"),
        BLOG_DIR.glob("*.md"),
        PODCAST_DIR.glob("*.md"),
        LINKEDIN_DIR.glob("*_ready*.txt"),
        (OUTPUT_DIR / "ideas").glob("*.md"),
        (OUTPUT_DIR / "reflections").glob("*.md"),
        (OUTPUT_DIR / "proposals").glob("*.md"),
        (OUTPUT_DIR / "newsletters").glob("*.md"),
        (OUTPUT_DIR / "devil").glob("*.md"),
        # Thesis & seminar tools — recursive over all dated subfolders
        (OUTPUT_DIR / "thesis").rglob("*.md"),
    ]

    n = 0
    for target in targets:
        for f in target:
            if f.name.endswith(".bak") or f.name.startswith("_"):
                continue
            r = bridge_file(f, dry_run=dry_run, all_authors=all_authors)
            results.append(r)
            n += 1

    print(f"\n📚 Obsidian Bridge — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"   Files processed: {n}")
    print(f"   Authors collected: {len(all_authors)}")
    modified_count = sum(1 for r in results if r.get("modified"))
    print(f"   Files modified:    {modified_count}")
    if dry_run:
        print(f"   (dry-run — no files written)")

    if not dry_run:
        sources_path = build_sources_index(all_authors)
        topics_path = build_topics_index()
        if sources_path:
            print(f"   📋 Sources index:  {sources_path.name}")
        if topics_path:
            print(f"   🗺 Topics index:   {topics_path.name}")

    return {
        "files": n,
        "authors": all_authors,
        "modified": modified_count,
        "results": results,
    }


def main():
    dry_run = "--dry-run" in sys.argv
    citations_only = "--citations-only" in sys.argv
    bridge_all(dry_run=dry_run, citations_only=citations_only)


if __name__ == "__main__":
    main()
