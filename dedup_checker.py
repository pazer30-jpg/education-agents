"""
dedup_checker.py — Catches duplicate content across articles + posts.

Scans output/ for:
  1. Near-duplicate articles (Jaccard similarity ≥85% on word 5-grams)
  2. Duplicate paragraphs WITHIN an article (repeated chunks)
  3. Duplicate citations (Author, Year) appearing >2 times in same article
  4. Duplicate hooks across LinkedIn posts (exact + fuzzy)

Reports findings to output/_memory/dedup_report.md. Does NOT auto-delete —
flags suspects so you can review and decide.

Usage:
  python3 dedup_checker.py                # full scan, write report
  python3 dedup_checker.py --json         # machine-readable output
  python3 dedup_checker.py --apply-merge  # (future) auto-resolve obvious dups
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR

ARTICLES_DIR = OUTPUT_DIR / "articles"
LINKEDIN_DIR = OUTPUT_DIR / "posts" / "linkedin"
BLOG_DIR     = OUTPUT_DIR / "posts" / "blog"

DEDUP_REPORT = OUTPUT_DIR / "_memory" / "dedup_report.md"

SIMILARITY_THRESHOLD = 0.85  # Jaccard on 5-grams
PARAGRAPH_REPEAT_MIN_CHARS = 100  # ignore short repeated lines (formatting)
CITATION_REPEAT_LIMIT = 2


# ─────────────────────────────────────────────
# Text helpers
# ─────────────────────────────────────────────

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2] if len(parts) >= 3 else text


def _normalize(text: str) -> str:
    text = re.sub(r"\s+", " ", text.lower())
    text = re.sub(r"[^\w\s֐-׿]", " ", text)
    return text.strip()


def _ngrams(text: str, n: int = 5) -> set:
    words = _normalize(text).split()
    if len(words) < n:
        return set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ─────────────────────────────────────────────
# Scans
# ─────────────────────────────────────────────

def find_near_duplicate_articles() -> list[dict]:
    """Pairs of articles with Jaccard similarity ≥ threshold."""
    if not ARTICLES_DIR.exists():
        return []
    md_files = [p for p in ARTICLES_DIR.glob("*.md") if not p.name.endswith(".bak")]
    profiles = [(p, _ngrams(_strip_frontmatter(_read(p)))) for p in md_files]
    profiles = [(p, ng) for p, ng in profiles if ng]

    dups = []
    for i, (p1, n1) in enumerate(profiles):
        for p2, n2 in profiles[i + 1:]:
            sim = _jaccard(n1, n2)
            if sim >= SIMILARITY_THRESHOLD:
                dups.append({
                    "file_a":      p1.name,
                    "file_b":      p2.name,
                    "similarity":  round(sim, 3),
                    "size_a":      p1.stat().st_size,
                    "size_b":      p2.stat().st_size,
                })
    return dups


def find_repeated_paragraphs(article_path: Path) -> list[dict]:
    """Paragraphs that appear 2+ times in the same article."""
    text = _strip_frontmatter(_read(article_path))
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    counts: Counter = Counter()
    for para in paragraphs:
        if len(para) < PARAGRAPH_REPEAT_MIN_CHARS:
            continue
        # Normalize for matching but keep original for display
        counts[_normalize(para)] += 1

    repeats = []
    for normalized, count in counts.items():
        if count >= 2:
            # find original
            for orig in paragraphs:
                if _normalize(orig) == normalized:
                    repeats.append({
                        "preview": orig[:120],
                        "count":   count,
                    })
                    break
    return repeats


def find_repeated_citations(article_path: Path) -> list[dict]:
    """(Author, Year) tuples appearing more than CITATION_REPEAT_LIMIT times."""
    text = _read(article_path)
    pattern = re.compile(r"\(([A-Z][a-zA-Z\-]+(?:\s*(?:et\s*al\.?|&\s*[A-Z][a-zA-Z\-]+)?)?),\s*(\d{4}[a-z]?)\)")
    matches = pattern.findall(text)
    counter = Counter(matches)
    return [
        {"author": author, "year": year, "count": count}
        for (author, year), count in counter.items()
        if count > CITATION_REPEAT_LIMIT
    ]


def find_duplicate_hooks() -> list[dict]:
    """Hooks (first line of LinkedIn .txt) that repeat across files."""
    if not LINKEDIN_DIR.exists():
        return []
    files = [p for p in LINKEDIN_DIR.glob("*_ready*.txt") if not p.name.endswith(".bak")]
    by_hook: dict[str, list[str]] = defaultdict(list)
    for p in files:
        text = _read(p)
        # First non-empty line is the hook
        first_line = next((l.strip() for l in text.split("\n") if l.strip()), "")
        if first_line:
            by_hook[_normalize(first_line)].append(p.name)
    dups = []
    for normalized, files_using in by_hook.items():
        if len(files_using) > 1:
            # Find original for display
            for fname in files_using:
                p = LINKEDIN_DIR / fname
                first = next((l.strip() for l in _read(p).split("\n") if l.strip()), "")
                if _normalize(first) == normalized:
                    dups.append({
                        "hook":  first[:120],
                        "count": len(files_using),
                        "files": files_using[:5],
                    })
                    break
    return dups


# ─────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────

def run_scan() -> dict:
    near_dup_articles = find_near_duplicate_articles()
    repeated_paragraphs: dict[str, list[dict]] = {}
    repeated_citations: dict[str, list[dict]] = {}
    if ARTICLES_DIR.exists():
        for art in ARTICLES_DIR.glob("*.md"):
            if art.name.endswith(".bak"):
                continue
            paras = find_repeated_paragraphs(art)
            if paras:
                repeated_paragraphs[art.name] = paras
            cits = find_repeated_citations(art)
            if cits:
                repeated_citations[art.name] = cits
    dup_hooks = find_duplicate_hooks()

    return {
        "scanned_at":          datetime.now().isoformat(timespec="seconds"),
        "near_dup_articles":   near_dup_articles,
        "repeated_paragraphs": repeated_paragraphs,
        "repeated_citations":  repeated_citations,
        "dup_hooks":           dup_hooks,
    }


def write_report(result: dict) -> Path:
    lines = [
        "---",
        "moki: true",
        "type: dedup_report",
        f"updated: {result['scanned_at']}",
        "---",
        "",
        "# 🔁 Dedup Report",
        "",
        f"> סריקה: {result['scanned_at']}. הקובץ הזה מציג חשודים — לא מוחק כלום אוטומטית.",
        "",
    ]

    nda = result["near_dup_articles"]
    lines.append(f"## 📄 מאמרים כפולים-כמעט ({len(nda)})")
    lines.append("")
    if not nda:
        lines.append("_אין כפילויות מעל סף {:.0%}._".format(SIMILARITY_THRESHOLD))
    else:
        lines.append("| similarity | קובץ A | קובץ B |")
        lines.append("|---|---|---|")
        for d in nda:
            lines.append(f"| {d['similarity']:.0%} | `{d['file_a']}` | `{d['file_b']}` |")
    lines.append("")

    rp = result["repeated_paragraphs"]
    lines.append(f"## 🔁 פסקאות חוזרות בתוך מאמר ({len(rp)} מאמרים)")
    lines.append("")
    if not rp:
        lines.append("_אין חזרות פנימיות._")
    else:
        for fname, paras in rp.items():
            lines.append(f"### `{fname}`")
            for p in paras:
                lines.append(f"- × {p['count']} — `{p['preview']}...`")
            lines.append("")

    rc = result["repeated_citations"]
    lines.append(f"## 📚 ציטוטים חוזרים (>{CITATION_REPEAT_LIMIT}× במאמר אחד) — {len(rc)} מאמרים")
    lines.append("")
    if not rc:
        lines.append("_אין ציטוטים שחורגים מהמותר._")
    else:
        for fname, cits in rc.items():
            cit_str = ", ".join(f"({c['author']}, {c['year']})×{c['count']}" for c in cits[:5])
            lines.append(f"- `{fname}` — {cit_str}")
    lines.append("")

    dh = result["dup_hooks"]
    lines.append(f"## 🎣 Hooks זהים בין פוסטים ({len(dh)})")
    lines.append("")
    if not dh:
        lines.append("_כל ה-hooks ייחודיים._")
    else:
        for d in dh:
            lines.append(f"- × {d['count']} — `{d['hook']}`")
            for f in d["files"]:
                lines.append(f"  - {f}")
    lines.append("")

    DEDUP_REPORT.parent.mkdir(parents=True, exist_ok=True)
    DEDUP_REPORT.write_text("\n".join(lines), encoding="utf-8")
    return DEDUP_REPORT


def main():
    ap = argparse.ArgumentParser(description="Scan for content duplications")
    ap.add_argument("--json", action="store_true", help="Print JSON instead of writing report")
    args = ap.parse_args()
    result = run_scan()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return
    path = write_report(result)
    summary = (
        f"Near-dup articles: {len(result['near_dup_articles'])} · "
        f"Repeated paragraphs: {sum(len(v) for v in result['repeated_paragraphs'].values())} · "
        f"Over-cited refs: {sum(len(v) for v in result['repeated_citations'].values())} · "
        f"Dup hooks: {len(result['dup_hooks'])}"
    )
    print(f"📝 {path.relative_to(OUTPUT_DIR.parent)}")
    print(f"   {summary}")


if __name__ == "__main__":
    main()
