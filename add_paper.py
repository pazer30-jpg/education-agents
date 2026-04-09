"""
add_paper.py — הוספת מאמרים ידנית
מאפשר להכניס מאמרים שמצאת בעצמך לתוך המערכת.

שימושים:
  python add_paper.py --url "https://..." --title "..." --topic "שייכות"
  python add_paper.py --file /path/to/paper.pdf --topic "חינוך"
  python add_paper.py --manual --topic "מנהיגות"
  python add_paper.py --list
  python add_paper.py --bibtex refs.bib --topic "שייכות"
"""

import json
import sys
import argparse
import re
from pathlib import Path
from datetime import datetime

from config import PAPERS_DIR
from agent1_5_pdf_reader import (
    _fetch_pdf, _extract_text_from_bytes,
    _clean_academic_text, _truncate_smart, MAX_CHARS_PER_PAPER
)

MANUAL_PAPERS_FILE = PAPERS_DIR / "manual_papers.json"


# ─────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────

def _load_manual() -> dict:
    if MANUAL_PAPERS_FILE.exists():
        try:
            return json.loads(MANUAL_PAPERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"papers": [], "topics": {}}


def _save_manual(db: dict):
    MANUAL_PAPERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    MANUAL_PAPERS_FILE.write_text(
        json.dumps(db, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _add_paper(paper: dict, topic: str) -> bool:
    db = _load_manual()
    paper["added_at"] = datetime.now().isoformat()
    paper["source"]   = "manual"
    paper["topic"]    = topic

    existing_titles = {p.get("title","").lower() for p in db["papers"]}
    if paper.get("title","").lower() in existing_titles:
        print(f"  ⚠️  מאמר עם כותרת זו כבר קיים — מדלג")
        return False

    db["papers"].append(paper)
    db["topics"][topic] = db["topics"].get(topic, 0) + 1
    _save_manual(db)
    _merge_into_topic_file(paper, topic)
    print(f"  ✅ נוסף: {paper.get('title','?')[:60]}")
    return True


def _merge_into_topic_file(paper: dict, topic: str):
    topic_slug = topic.replace(" ", "_").lower()[:40]
    candidates = (list(PAPERS_DIR.glob(f"*{topic_slug}*papers*.json")) +
                  list(PAPERS_DIR.glob(f"*{topic_slug}*enriched*.json")))

    if candidates:
        target = max(candidates, key=lambda p: p.stat().st_mtime)
        try:
            with open(target, encoding="utf-8") as f:
                data = json.load(f)
            papers = data.get("papers", []) if isinstance(data, dict) else data
            papers.append(paper)
            if isinstance(data, dict):
                data["papers"] = papers
            else:
                data = papers
            with open(target, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"  📎 גם נוסף ל: {target.name}")
        except Exception as e:
            print(f"  ⚠️  לא הצלחתי לעדכן {target.name}: {e}")
    else:
        new_file = PAPERS_DIR / f"{topic_slug}_manual_papers.json"
        with open(new_file, "w", encoding="utf-8") as f:
            json.dump({"topic": topic, "papers": [paper]}, f,
                      ensure_ascii=False, indent=2)
        print(f"  📄 נוצר קובץ חדש: {new_file.name}")


# ─────────────────────────────────────────────
# Input modes
# ─────────────────────────────────────────────

def add_from_url(url: str, topic: str,
                 title: str = "", authors: str = "", year: int = None) -> bool:
    print(f"  ⬇️  מוריד מ-{url[:60]}...")
    text = _fetch_pdf(url)
    paper = {
        "title":           title or _guess_title_from_url(url),
        "authors":         authors,
        "year":            year,
        "url":             url,
        "pdf_url":         url,
        "abstract":        text[:600] if text else "",
        "fulltext":        _truncate_smart(text, MAX_CHARS_PER_PAPER) if text else "",
        "fulltext_source": "manual_url",
        "citation_count":  0,
    }
    if text:
        print(f"  📄 חולץ טקסט: {len(text):,} תווים")
    else:
        print(f"  ⚠️  לא הצלחתי לחלץ טקסט — שומר ללא fulltext")
    return _add_paper(paper, topic)


def add_from_file(pdf_path: Path, topic: str,
                  title: str = "", authors: str = "", year: int = None) -> bool:
    if not pdf_path.exists():
        print(f"  ❌ קובץ לא נמצא: {pdf_path}")
        return False
    print(f"  📂 קורא: {pdf_path.name}")
    pdf_bytes = pdf_path.read_bytes()
    text = _extract_text_from_bytes(pdf_bytes)
    if text:
        text = _clean_academic_text(text)
    paper = {
        "title":           title or pdf_path.stem.replace("_", " ").replace("-", " "),
        "authors":         authors,
        "year":            year,
        "url":             str(pdf_path),
        "pdf_url":         str(pdf_path),
        "abstract":        text[:600] if text else "",
        "fulltext":        _truncate_smart(text, MAX_CHARS_PER_PAPER) if text else "",
        "fulltext_source": "manual_file",
        "citation_count":  0,
    }
    if text:
        print(f"  📄 חולץ טקסט: {len(text):,} תווים")
    return _add_paper(paper, topic)


def add_manual(topic: str) -> bool:
    print("\n  📝 הוספה ידנית — מלא את הפרטים (Enter לדלג):\n")
    title = input("  כותרת: ").strip()
    if not title:
        print("  ❌ כותרת חובה")
        return False
    authors = input("  מחברים: ").strip()
    year_s  = input("  שנה: ").strip()
    year    = int(year_s) if year_s.isdigit() else None
    url     = input("  URL (אופציונלי): ").strip()
    print("  תקציר (Enter ריק פעמיים לסיום):")
    abstract_lines = []
    while True:
        line = input()
        if line == "" and abstract_lines and abstract_lines[-1] == "":
            break
        abstract_lines.append(line)
    abstract = "\n".join(abstract_lines).strip()
    paper = {
        "title":           title,
        "authors":         authors,
        "year":            year,
        "url":             url,
        "pdf_url":         url,
        "abstract":        abstract,
        "fulltext":        abstract,
        "fulltext_source": "manual_text",
        "citation_count":  0,
    }
    return _add_paper(paper, topic)


def import_bibtex(bib_path: Path, topic: str) -> int:
    if not bib_path.exists():
        print(f"  ❌ קובץ BibTeX לא נמצא: {bib_path}")
        return 0
    content = bib_path.read_text(encoding="utf-8", errors="replace")
    entries = re.split(r'@\w+\{', content)[1:]
    added = 0
    for entry in entries:
        def _get(field):
            m = re.search(rf'{field}\s*=\s*[{{"](.+?)[}}"]\s*[,}}]',
                          entry, re.IGNORECASE | re.DOTALL)
            return m.group(1).strip() if m else ""
        title = _get("title").replace("\n", " ").replace("  ", " ")
        if not title:
            continue
        year_s = _get("year")
        url    = _get("url") or _get("doi")
        if url and not url.startswith("http"):
            url = f"https://doi.org/{url}"
        paper = {
            "title":           title,
            "authors":         _get("author"),
            "year":            int(year_s) if year_s.isdigit() else None,
            "url":             url,
            "pdf_url":         url,
            "abstract":        _get("abstract"),
            "fulltext":        _get("abstract"),
            "fulltext_source": "bibtex",
            "citation_count":  0,
        }
        if _add_paper(paper, topic):
            added += 1
    print(f"\n  ✅ יובאו {added} מאמרים מ-BibTeX")
    return added


def list_papers():
    db = _load_manual()
    papers = db.get("papers", [])
    if not papers:
        print("  אין מאמרים ידניים.")
        return
    print(f"\n  📚 מאמרים ידניים ({len(papers)}):\n")
    by_topic: dict = {}
    for p in papers:
        t = p.get("topic", "כללי")
        by_topic.setdefault(t, []).append(p)
    for topic, ps in sorted(by_topic.items()):
        print(f"  ── {topic} ({len(ps)}) ──")
        for p in ps:
            has_full = "📄" if len(p.get("fulltext","")) > 200 else "📋"
            print(f"    {has_full} {p.get('year','')} | {p.get('title','')[:55]}")
        print()


def _guess_title_from_url(url: str) -> str:
    name = url.rstrip("/").split("/")[-1]
    name = re.sub(r'\.(pdf|html|htm)$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[-_]', ' ', name)
    return name[:80].title()


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="הוסף מאמרים ידנית למערכת",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
דוגמאות:
  python3 add_paper.py --url "https://arxiv.org/pdf/..." --topic "שייכות" --title "..."
  python3 add_paper.py --file ~/Downloads/paper.pdf --topic "חינוך" --authors "Smith, J."
  python3 add_paper.py --manual --topic "מנהיגות"
  python3 add_paper.py --bibtex refs.bib --topic "חינוך בלתי פורמלי"
  python3 add_paper.py --list
        """,
    )
    parser.add_argument("--url",     help="URL של PDF")
    parser.add_argument("--file",    help="נתיב לקובץ PDF מקומי")
    parser.add_argument("--manual",  action="store_true")
    parser.add_argument("--bibtex",  help="קובץ BibTeX לייבוא")
    parser.add_argument("--list",    action="store_true")
    parser.add_argument("--topic",   default="חינוך בלתי פורמלי")
    parser.add_argument("--title",   default="")
    parser.add_argument("--authors", default="")
    parser.add_argument("--year",    type=int)

    args = parser.parse_args()

    if args.list:
        list_papers()
    elif args.url:
        add_from_url(args.url, args.topic, args.title, args.authors, args.year)
    elif args.file:
        add_from_file(Path(args.file), args.topic, args.title, args.authors, args.year)
    elif args.manual:
        add_manual(args.topic)
    elif args.bibtex:
        import_bibtex(Path(args.bibtex), args.topic)
    else:
        parser.print_help()
