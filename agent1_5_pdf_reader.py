"""
Agent 1.5 — PDF Reader
רץ בין Agent 1 (Researcher) ל-Agent 2 (Writer).

מה הוא עושה:
  1. פותח את קובץ המאמרים מ-Agent 1
  2. לכל מאמר שיש לו pdf_url — מוריד ומחלץ טקסט מלא
  3. שומר enriched_papers.json עם טקסט מלא במקום תקצירים בלבד
  4. Agent 2 מקבל תוכן אמיתי, לא רק 500 תווים של תקציר

אסטרטגיית חילוץ (לפי skill):
  - pdfplumber לטקסט מובנה (מאמרים אקדמיים רגילים)
  - pypdf כ-fallback
  - אם PDF לא נגיש — משאיר את התקציר המקורי
"""

import json
import re
import time
import tempfile
import requests
from pathlib import Path
from datetime import datetime

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

try:
    from pypdf import PdfReader
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

from config import PAPERS_DIR

# כמה תווים מהטקסט המלא לשמור (כדי לא לפוצץ את ה-context של Agent 2)
MAX_CHARS_PER_PAPER = 12000
DOWNLOAD_TIMEOUT    = 15   # שניות
MAX_PAPERS_TO_FETCH = 12   # לא מורידים יותר מ-X PDFs בהרצה אחת


# ─────────────────────────────────────────────
# PDF extraction helpers
# ─────────────────────────────────────────────

def _extract_text_from_bytes(pdf_bytes: bytes) -> str:
    """חולץ טקסט מ-PDF bytes — מנסה pdfplumber קודם, אחר כך pypdf."""
    text = ""

    # ניסיון 1: pdfplumber (עדיף לפריסה אקדמית מרובת עמודות)
    if HAS_PDFPLUMBER:
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(pdf_bytes)
                tmp_path = f.name
            with pdfplumber.open(tmp_path) as pdf:
                pages = pdf.pages[:25]  # מקסימום 25 עמודים
                text = "\n\n".join(
                    p.extract_text() or "" for p in pages
                ).strip()
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass

    # ניסיון 2: pypdf fallback
    if not text and HAS_PYPDF:
        try:
            import io
            reader = PdfReader(io.BytesIO(pdf_bytes))
            pages  = reader.pages[:25]
            text   = "\n\n".join(
                (p.extract_text() or "") for p in pages
            ).strip()
        except Exception:
            pass

    return text


def _clean_academic_text(text: str) -> str:
    """מנקה טקסט אקדמי — מוריד headers/footers, מספרי עמוד, שורות ריקות עודפות."""
    # הסר מספרי עמוד בודדים בשורה
    text = re.sub(r'^\s*\d{1,4}\s*$', '', text, flags=re.MULTILINE)
    # הסר שורות של מקפים/נקודות (קווים מפרידים ב-PDF)
    text = re.sub(r'^[-–—=_.]{5,}\s*$', '', text, flags=re.MULTILINE)
    # צמצם שורות ריקות מרובות
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _fetch_pdf(url: str) -> str | None:
    """
    מוריד PDF מ-URL ומחזיר את הטקסט המחולץ.
    מחזיר None אם נכשל.
    """
    if not url:
        return None
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; AcademicBot/1.0; "
                "+https://education-agents)"
            )
        }
        resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT,
                            headers=headers, allow_redirects=True)
        if resp.status_code != 200:
            return None
        ct = resp.headers.get("content-type", "").lower()
        # Accept PDF, octet-stream (common for PDFs), or check magic bytes
        is_pdf = "pdf" in ct or "octet-stream" in ct or resp.content[:5] == b'%PDF-'
        if not is_pdf:
            return None

        text = _extract_text_from_bytes(resp.content)
        if not text or len(text) < 200:
            return None
        return _clean_academic_text(text)

    except Exception:
        return None


def _truncate_smart(text: str, max_chars: int) -> str:
    """
    חותך בחוכמה — שומר 4 חלקים: intro + Methods + Results/Discussion + Conclusions.
    Methods קריטי להערכת אמינות המחקר — לא לדלג עליו.
    """
    if len(text) <= max_chars:
        return text

    # Allocate: intro 25% · methods 25% · results 25% · conclusions 25%
    slice_size = max_chars // 4
    text_lower = text.lower()

    # Section finder — returns index or -1
    def find_section(markers: list[str], start: int = 0) -> int:
        for m in markers:
            idx = text_lower.find(m, start)
            if idx != -1:
                return idx
        return -1

    # Intro: always the beginning
    intro = text[:slice_size]

    # Methods: look for Methods/Methodology/Participants/Procedure
    methods_start = find_section(
        ["\nmethods", "\nmethodology", "\nparticipants", "\nprocedure",
         "\nresearch design", "\nשיטה", "\nמתודולוגיה"],
        slice_size,
    )
    if methods_start != -1:
        methods = text[methods_start:methods_start + slice_size]
    else:
        # Fallback: take middle
        mid = len(text) // 2
        methods = text[mid - slice_size // 2:mid + slice_size // 2]

    # Results/Findings
    results_start = find_section(
        ["\nresults", "\nfindings", "\nממצאים", "\nתוצאות"],
        methods_start + slice_size if methods_start != -1 else len(text) // 2,
    )
    if results_start != -1:
        results = text[results_start:results_start + slice_size]
    else:
        results = ""

    # Conclusions/Discussion (from end)
    concl_markers = ["\nconclusion", "\nמסקנות", "\ndiscussion", "\nדיון",
                     "\nsummary", "\nסיכום", "\nimplications"]
    concl_start = len(text) - slice_size
    for m in concl_markers:
        idx = text_lower.rfind(m, len(text) - slice_size * 2)
        if idx != -1:
            concl_start = idx
            break
    conclusions = text[concl_start:concl_start + slice_size]

    parts = [intro, "[...METHODS...]", methods]
    if results:
        parts += ["[...RESULTS...]", results]
    parts += ["[...DISCUSSION/CONCLUSIONS...]", conclusions]
    return "\n\n".join(parts)


# ─────────────────────────────────────────────
# Main runner
# ─────────────────────────────────────────────

def run_pdf_reader(papers_file: Path) -> Path:
    """
    קורא את papers_file, מוריד PDFs, שומר enriched_papers.json.
    Returns: Path לקובץ המועשר.
    """
    print(f"\n{'='*60}")
    print(f"📄 Agent 1.5 — PDF Reader | {papers_file.name}")
    print(f"{'='*60}\n")

    if not papers_file.exists():
        raise FileNotFoundError(f"Papers file not found: {papers_file}")

    with open(papers_file, encoding="utf-8") as f:
        data = json.load(f)

    topic  = data.get("topic", "") if isinstance(data, dict) else ""
    papers = data.get("papers", data) if isinstance(data, dict) else data

    stats = {"fetched": 0, "skipped": 0, "failed": 0, "abstract_only": 0}
    fetched_count = 0

    for i, paper in enumerate(papers):
        title = paper.get("title", f"Paper {i+1}")
        print(f"  [{i+1}/{len(papers)}] {title[:55]}...")

        # כבר יש fulltext מריצה קודמת — דלג
        if paper.get("fulltext") and len(paper["fulltext"]) > 500:
            print(f"           ↩️  יש fulltext — דולג")
            stats["skipped"] += 1
            continue

        if fetched_count >= MAX_PAPERS_TO_FETCH:
            print(f"           ⏸  הגענו ל-{MAX_PAPERS_TO_FETCH} PDFs — עוצר")
            stats["abstract_only"] += 1
            continue

        # מחפש URL של PDF
        open_access = paper.get("openAccessPdf")
        pdf_url = (
            paper.get("pdf_url")
            or (open_access.get("url") if isinstance(open_access, dict) else None)
            or (paper.get("url") if str(paper.get("url", "")).endswith(".pdf") else None)
        )

        if not pdf_url:
            print(f"           ⚠️  אין PDF URL — שומר תקציר בלבד")
            stats["abstract_only"] += 1
            continue

        print(f"           ⬇️  מוריד מ-{pdf_url[:60]}...")
        text = _fetch_pdf(pdf_url)

        if text:
            paper["fulltext"]        = _truncate_smart(text, MAX_CHARS_PER_PAPER)
            paper["fulltext_chars"]  = len(paper["fulltext"])
            paper["fulltext_source"] = "pdf"
            fetched_count += 1
            stats["fetched"] += 1
            print(f"           ✅  {len(paper['fulltext']):,} תווים")
        else:
            print(f"           ❌  נכשל — שומר תקציר")
            stats["failed"] += 1

        time.sleep(0.5)  # נימוס לשרתים

    # שמירה — strip BOTH "_papers" and any trailing "_enriched" suffixes so
    # rerunning on an already-enriched file doesn't grow "_enriched_enriched_..."
    base = papers_file.stem.replace("_papers", "")
    while base.endswith("_enriched"):
        base = base[:-len("_enriched")]
    enriched_name = base + "_enriched.json"
    enriched_path = PAPERS_DIR / enriched_name
    output = {
        "topic":       topic,
        "source_file": str(papers_file),
        "enriched_at": datetime.now().isoformat(),
        "stats":       stats,
        "papers":      papers,
    }
    with open(enriched_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"""
  📊 סיכום:
     ✅ PDFs שהורדו:    {stats['fetched']}
     ⚠️  תקציר בלבד:   {stats['abstract_only']}
     ❌ נכשלו:          {stats['failed']}
     ⏭  דולגו (קיים):  {stats['skipped']}

  ✅ Agent 1.5 complete → {enriched_path}
""")
    return enriched_path


# ─────────────────────────────────────────────
# Standalone test
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        pf = Path(sys.argv[1])
    else:
        candidates = list(PAPERS_DIR.glob("*_papers.json"))
        if not candidates:
            print("לא נמצאו קבצי מאמרים ב-output/papers/")
            sys.exit(1)
        pf = max(candidates, key=lambda p: p.stat().st_mtime)

    run_pdf_reader(pf)
