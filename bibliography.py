"""
bibliography.py — ביבליוגרפיה מצטברת
מייצר ומנהל ביבליוגרפיה מכל הריצות של Agent 1.

פלטים:
  output/references.bib   — BibTeX (לייבוא ל-Zotero/Mendeley)
  output/references.json  — JSON מלא לשימוש פנימי
  output/references.csv   — CSV לאקסל

Usage:
  python bibliography.py                  # עדכן מכל קבצי papers
  python bibliography.py --stats          # סטטיסטיקות
  python bibliography.py --search "belonging"  # חיפוש
  python bibliography.py --export bib     # ייצוא BibTeX
  python bibliography.py --topic "שייכות" # כל המאמרים בנושא
"""

import json
import csv
import re
import unicodedata
import sys
import argparse
from pathlib import Path
from datetime import datetime
from collections import Counter

from config import OUTPUT_DIR, PAPERS_DIR

BIB_FILE  = OUTPUT_DIR / "references.bib"
JSON_FILE = OUTPUT_DIR / "references.json"
CSV_FILE  = OUTPUT_DIR / "references.csv"


# ─────────────────────────────────────────────
# BibTeX helpers
# ─────────────────────────────────────────────

def _bibtex_key(paper: dict) -> str:
    """מייצר מפתח BibTeX ייחודי: Author2024keyword"""
    authors = paper.get("authors", "Unknown")
    if isinstance(authors, list):
        authors = ", ".join(
            a.get("name", str(a)) if isinstance(a, dict) else str(a)
            for a in authors
        )
    first_author = authors.split(",")[0].split()[-1] if authors else "Unknown"
    # הסר תווים לא-ASCII
    first_author = unicodedata.normalize("NFKD", first_author)
    first_author = "".join(c for c in first_author if c.isascii() and c.isalpha())
    year   = paper.get("year") or "n.d."
    title  = paper.get("title", "")
    # מילה ראשונה משמעותית מהכותרת
    words  = [w for w in title.split() if len(w) > 4]
    kw     = words[0].lower() if words else "paper"
    kw     = re.sub(r"[^a-z]", "", kw)[:10]
    return f"{first_author}{year}{kw}"


def _to_bibtex(paper: dict, key: str) -> str:
    """ממיר paper dict ל-BibTeX entry."""
    authors = paper.get("authors", "")
    if isinstance(authors, list):
        authors = ", ".join(
            a.get("name", str(a)) if isinstance(a, dict) else str(a)
            for a in authors
        )
    # BibTeX דורש " and " בין מחברים
    if "," in authors and " and " not in authors.lower():
        parts   = [a.strip() for a in authors.split(",")]
        authors = " and ".join(parts)

    title   = paper.get("title", "Unknown").replace("{", "").replace("}", "")
    year    = paper.get("year") or ""
    journal = paper.get("venue", "") or paper.get("source", "")
    url     = paper.get("url", "") or paper.get("pdf_url", "")
    doi     = paper.get("doi", "") or ""
    abstract= paper.get("abstract", "").replace("{","").replace("}","")[:300]

    entry_type = "article"
    if any(w in journal.lower() for w in ["conference","proceedings","workshop"]):
        entry_type = "inproceedings"
    elif not journal:
        entry_type = "misc"

    lines = [f"@{entry_type}{{{key},"]
    lines.append(f"  title     = {{{title}}},")
    if authors:
        lines.append(f"  author    = {{{authors}}},")
    if year:
        lines.append(f"  year      = {{{year}}},")
    if journal:
        field = "journal" if entry_type == "article" else "booktitle"
        lines.append(f"  {field:<9} = {{{journal}}},")
    if doi:
        lines.append(f"  doi       = {{{doi}}},")
    if url and not doi:
        lines.append(f"  url       = {{{url}}},")
    if abstract:
        lines.append(f"  abstract  = {{{abstract}}},")
    lines.append("}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Load / Save
# ─────────────────────────────────────────────

def _load_db() -> dict:
    """טוען ביבליוגרפיה קיימת (key → paper)."""
    if JSON_FILE.exists():
        try:
            return json.loads(JSON_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_db(db: dict):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_FILE.write_text(
        json.dumps(db, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ─────────────────────────────────────────────
# Import from papers files
# ─────────────────────────────────────────────

def update_from_papers(papers_dir: Path = PAPERS_DIR) -> tuple[int, int]:
    """
    סורק את כל קבצי papers JSON ומוסיף מאמרים חדשים.
    Returns: (added, total)
    """
    db      = _load_db()
    added   = 0
    keys_used = set(db.keys())

    for f in sorted(papers_dir.glob("*.json")):
        try:
            data   = json.loads(f.read_text(encoding="utf-8"))
            papers = data.get("papers", data) if isinstance(data, dict) else data
            topic  = data.get("topic", f.stem) if isinstance(data, dict) else f.stem
        except Exception:
            continue

        for p in papers:
            title = (p.get("title") or "").strip()
            if not title:
                continue

            # בדוק כפילות לפי כותרת
            title_key = title.lower()[:70]
            existing  = next((k for k, v in db.items()
                               if (v.get("title") or "").lower()[:70] == title_key), None)
            if existing:
                # עדכן topic אם חסר
                if topic not in db[existing].get("topics", []):
                    db[existing].setdefault("topics", []).append(topic)
                continue

            # מפתח ייחודי
            base_key = _bibtex_key(p)
            key = base_key
            suffix = 1
            while key in keys_used:
                key = f"{base_key}{chr(96 + suffix)}"
                suffix += 1
            keys_used.add(key)

            # Normalize authors
            authors = p.get("authors", "")
            if isinstance(authors, list):
                authors = ", ".join(
                    a.get("name", str(a)) if isinstance(a, dict) else str(a)
                    for a in authors
                )

            db[key] = {
                "title": title,
                "authors": authors,
                "year": p.get("year"),
                "abstract": (p.get("abstract") or "")[:500],
                "url": p.get("url", ""),
                "pdf_url": p.get("pdf_url", ""),
                "citation_count": p.get("citation_count", 0),
                "venue": p.get("venue", ""),
                "source": p.get("source", ""),
                "topics": [topic],
                "added_at": datetime.now().isoformat(),
            }
            added += 1

    _save_db(db)
    _export_bib(db)
    _export_csv(db)

    return added, len(db)


# ─────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────

def _export_bib(db: dict):
    lines = [
        f"% Education Agents — ביבליוגרפיה מצטברת",
        f"% עודכן: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"% {len(db)} ערכים",
        "",
    ]
    for key, paper in sorted(db.items()):
        lines.append(_to_bibtex(paper, key))
        lines.append("")
    BIB_FILE.write_text("\n".join(lines), encoding="utf-8")


def _export_csv(db: dict):
    fields = ["key","title","authors","year","venue","source",
              "citation_count","url","pdf_url","topics","added_at"]
    with open(CSV_FILE, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for key, paper in sorted(db.items(), key=lambda x: -(x[1].get("year") or 0)):
            row = {**paper, "key": key,
                   "topics": ", ".join(paper.get("topics", []))}
            w.writerow(row)


# ─────────────────────────────────────────────
# Query helpers
# ─────────────────────────────────────────────

def search(query: str, db: dict = None) -> list[dict]:
    """חיפוש בביבליוגרפיה לפי מילות מפתח."""
    if db is None:
        db = _load_db()
    q = query.lower()
    results = []
    for key, paper in db.items():
        searchable = " ".join([
            paper.get("title",""),
            paper.get("abstract",""),
            paper.get("authors",""),
            " ".join(paper.get("topics",[])),
        ]).lower()
        if q in searchable:
            results.append({"key": key, **paper})
    return sorted(results, key=lambda x: -(x.get("citation_count") or 0))


def by_topic(topic: str, db: dict = None) -> list[dict]:
    if db is None:
        db = _load_db()
    t = topic.lower()
    return [{"key": k, **v} for k, v in db.items()
            if any(t in tp.lower() for tp in v.get("topics", []))]


def stats(db: dict = None) -> str:
    if db is None:
        db = _load_db()
    if not db:
        return "ביבליוגרפיה ריקה — הרץ: python bibliography.py"

    years   = [p.get("year") for p in db.values() if p.get("year")]
    sources = Counter(p.get("source","unknown") for p in db.values())
    topics  = Counter(t for p in db.values() for t in p.get("topics",[]))
    cited   = sorted(db.values(), key=lambda x: -(x.get("citation_count") or 0))
    has_pdf = sum(1 for p in db.values() if p.get("pdf_url"))

    lines = [
        f"\n{'='*55}",
        f"📚 ביבליוגרפיה — {len(db)} מאמרים",
        f"{'='*55}",
        f"  שנים: {min(years) if years else '?'} – {max(years) if years else '?'}",
        f"  עם PDF:  {has_pdf} ({has_pdf*100//len(db)}%)",
        "",
        "  מקורות:",
    ]
    for src, n in sources.most_common():
        bar = "█" * (n * 20 // max(sources.values()))
        lines.append(f"    {src:<20} {n:>3}  {bar}")

    lines += ["", "  נושאים פעילים:"]
    for t, n in topics.most_common(8):
        lines.append(f"    {t[:35]:<35} {n}")

    lines += ["", "  מצוטטים ביותר:"]
    for p in cited[:5]:
        lines.append(f"    {p.get('citation_count',0):>5}× {p.get('title','')[:50]}")

    lines += [
        "",
        f"  קבצים:",
        f"    BibTeX: {BIB_FILE}",
        f"    CSV:    {CSV_FILE}",
        f"    JSON:   {JSON_FILE}",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Auto-update hook (called by Agent 1 after save)
# ─────────────────────────────────────────────

def auto_update() -> str:
    """קצר — נקרא אוטומטית אחרי כל ריצת Agent 1."""
    added, total = update_from_papers()
    return f"📚 Bibliography: +{added} מאמרים חדשים | סה\"כ {total}"


# Backward compat alias
def update_bibliography():
    """Called by orchestrator after pipeline."""
    added, total = update_from_papers()
    print(f"  📚 ביבליוגרפיה: {total} ערכים ({added} חדשים)")


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ביבליוגרפיה מצטברת")
    parser.add_argument("--stats",   action="store_true")
    parser.add_argument("--search",  metavar="QUERY")
    parser.add_argument("--topic",   metavar="TOPIC")
    parser.add_argument("--export",  choices=["bib","csv","json"])
    parser.add_argument("--update",  action="store_true",
                        help="עדכן מכל קבצי papers (ברירת מחדל)")
    args = parser.parse_args()

    if args.search:
        results = search(args.search)
        print(f"\n  נמצאו {len(results)} תוצאות לחיפוש '{args.search}':\n")
        for r in results[:15]:
            print(f"  [{r.get('year','')}] {r.get('title','')[:60]}")
            print(f"    {r.get('authors','')[:40]} | {r.get('citation_count',0)} ציטוטים")
        sys.exit(0)

    if args.topic:
        results = by_topic(args.topic)
        print(f"\n  {len(results)} מאמרים בנושא '{args.topic}':\n")
        for r in results:
            print(f"  [{r.get('year','')}] {r.get('title','')[:60]}")
        sys.exit(0)

    if args.export == "bib":
        db = _load_db()
        _export_bib(db)
        print(f"✅ BibTeX: {BIB_FILE}")
        sys.exit(0)

    if args.stats:
        print(stats())
        sys.exit(0)

    # ברירת מחדל: עדכון
    print("  מעדכן ביבליוגרפיה...")
    added, total = update_from_papers()
    print(f"  ✅ נוספו: {added} | סה\"כ: {total}")
    print(stats())
