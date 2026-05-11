"""
seminar_writer.py — כותב סמינריון אקדמי 8K-12K מילים מקצה לקצה.

לא תזה. סמינריון — סקירת ספרות + טיעון תיאורטי, ללא שדה.

Pipeline:
  1. קורא הצעת מחקר + 486 מקורות
  2. מייצר outline (סעיפים + יעד מילים)
  3. כותב כל סעיף בנפרד עם top-N מקורות רלוונטיים לסעיף
  4. מאחד למסמך אחד עם רשימת מקורות

Usage:
  python3 seminar_writer.py --proposal output/thesis/<stamp>/01_proposal.md \\
      --papers output/thesis/<stamp>/papers_full.json \\
      --target-words 12000
"""

import sys
import json
import re
import argparse
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR
from claude_cli import ask_claude_json, ask_claude

try:
    from obsidian_memory import format_for_prompt as _obsidian_memory_for_prompt
except Exception:
    def _obsidian_memory_for_prompt(_names: list[str], **_kw) -> str:
        return ""


SEMINAR_DIR = OUTPUT_DIR / "thesis"


# ─────────────────────────────────────────────
# Section blueprint — 7 sections × ~1,700 words = 12K target
# ─────────────────────────────────────────────

SECTION_BLUEPRINT = [
    {
        "id": "abstract",
        "title": "תקציר",
        "target_words": 200,
        "focus": "תקציר APA — רקע, שאלות מחקר, שיטת סקירה (databases, criteria), ממצאי מפתח, השלכות. 150-200 מילים.",
        "search_terms": ["principal loneliness", "boarding school", "school administrator isolation",
                         "headteacher loneliness"],
    },
    {
        "id": "intro",
        "title": "מבוא",
        "target_words": 1000,
        "focus": "פתיחה: התופעה והדחיפות. רקע: מה ידוע (4-6 מקורות עיקריים). פער: מה לא נחקר. 2-3 שאלות מחקר ספציפיות. סיום: מבנה המאמר.",
        "search_terms": ["principal loneliness", "school administrator isolation",
                         "boarding school principal", "headteacher loneliness"],
    },
    {
        "id": "methodology",
        "title": "שיטת הסקירה",
        "target_words": 600,
        "focus": "מאגרי מידע (Semantic Scholar, OpenAlex, ERIC, PubMed, CORE, Crossref, DOAJ). מילות חיפוש. קריטריוני הכללה והדרה. משפך: נמצאו → סוננו → נכללו (PRISMA-style funnel). תאריכים: 2000-2026.",
        "search_terms": ["systematic review", "methodology", "search strategy",
                         "inclusion criteria", "PRISMA", "literature review"],
    },
    {
        "id": "theoretical_framework",
        "title": "מסגרת תיאורטית",
        "target_words": 1800,
        "focus": "4 צירים מסגרים, כל אחד תת-סעיף: (1) Perlman & Peplau — בדידות כפער קוגניטיבי. (2) Buber — אני-אתה. (3) Hobfoll — Conservation of Resources. (4) Goffman — total institution. כל ציר: רעיון מרכזי + ציטוט מקור + איך הוא מאיר את התופעה.",
        "search_terms": ["Perlman Peplau", "Buber dialogue", "Hobfoll conservation",
                         "Goffman total institution", "cognitive discrepancy",
                         "I-Thou", "conservation of resources"],
    },
    {
        "id": "lit_review",
        "title": "סקירת ספרות אמפירית",
        "target_words": 4000,
        "focus": "סקירה אינטגרטיבית עם תת-סעיפים: (א) בדידות מנהלי בתי ספר רגילים — ממצאים מ-Greene, Korumaz, Bayar, Dor-Haim, Yengin Sarpkaya. (ב) שלבי קריירה ותשישות. (ג) הקשר הפנימייתי — Goffman, גזטמבידה-פרננדז, יישומי residential. (ד) הרקע הישראלי. **חובה**: טבלת השוואה (Markdown table) — Study | Year | Method | Sample (n) | Key Finding — של 8-10 מחקרי מפתח. ציין מתודולוגיה לכל ציטוט: '(Greene, 2016, איכותני, n=14)'.",
        "search_terms": ["school principal loneliness", "headteacher isolation",
                         "principal social support", "career stage", "emotional exhaustion",
                         "boarding school", "residential", "youth village", "כפר נוער"],
    },
    {
        "id": "discussion",
        "title": "דיון",
        "target_words": 2200,
        "focus": "סינתזה לפי שאלות המחקר במבוא — תשובה מפורשת לכל RQ. סתירות בין מחקרים. מתח התיאורטי הייחודי: 'המרחב הכולל' לא מוסבר ע\"י מסגרות בתי ספר רגילים. השלכות מתודולוגיות: מה השאלות שניתנות לחקר רק איכותנית-פנומנולוגית. הימנע מטענות סיבתיות חזקות מקרוסס-סקשנל.",
        "search_terms": ["principal loneliness", "school leader isolation",
                         "boarding school principal", "qualitative phenomenology",
                         "future research", "research gap"],
    },
    {
        "id": "limitations",
        "title": "מגבלות הסקירה",
        "target_words": 500,
        "focus": "מגבלות ספציפיות: (1) הטיית פרסום — מאמרים ב-English overrepresented. (2) הקשר תרבותי — רוב המחקר מארה\"ב, טורקיה, אירופה. (3) זמני — מאמרים מסוימים לפני 2010 קשים לאתר. (4) שפה — חיפוש בעברית מצומצם.",
        "search_terms": ["publication bias", "limitations review",
                         "methodological constraints"],
    },
    {
        "id": "conclusion",
        "title": "מסקנות והשלכות",
        "target_words": 700,
        "focus": "תרומה תיאורטית של הסקירה. השלכות מעשיות לפיתוח מנהלי פנימייה בישראל (אמ\"י, מרכזי פסג\"ה, משרד החינוך). כיווני מחקר עתידיים: מחקר פנומנולוגי איכותני עם 12-15 מנהלי פנימייה ישראלים.",
        "search_terms": ["principal training", "school leader support",
                         "policy implications", "professional development",
                         "future research"],
    },
]


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _load_papers(papers_json: Path) -> list[dict]:
    try:
        data = json.loads(papers_json.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        print(f"  ⚠️ Failed to load {papers_json}: {e}")
        return []
    if isinstance(data, dict):
        return data.get("papers", []) or []
    return data if isinstance(data, list) else []


def _score_paper_for_section(paper: dict, section_terms: list[str]) -> float:
    """Cheap relevance scoring per section."""
    title = (paper.get("title") or "").lower()
    abstract = (paper.get("abstract") or "").lower()
    text = f"{title} {abstract}"
    score = 0.0
    for term in section_terms:
        t = term.lower()
        if t in title:
            score += 2.0
        if t in abstract:
            score += 1.0
    score += paper.get("relevance", 0) * 0.5
    return score


def _top_papers_for_section(papers: list[dict], section: dict, k: int = 12) -> list[dict]:
    scored = [(_score_paper_for_section(p, section["search_terms"]), p) for p in papers]
    if not scored:
        return []
    scored.sort(reverse=True, key=lambda x: x[0])
    return [p for _, p in scored[:k]]


def _format_papers_for_prompt(papers: list[dict]) -> list[dict]:
    """Trim papers to fit prompt budget."""
    out = []
    for p in papers:
        authors = p.get("authors", "")
        if isinstance(authors, list):
            authors = ", ".join(str(a) for a in authors[:3])
        out.append({
            "title": (p.get("title") or "")[:150],
            "authors": str(authors)[:120],
            "year": p.get("year"),
            "abstract": (p.get("abstract") or "")[:400],
            "citations": p.get("citation_count", 0) or 0,
        })
    return out


# ─────────────────────────────────────────────
# Section writer
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """אתה כותב אקדמי בעברית, רושם מאמר סקירה (literature review) לפי כללי APA 7.

═════ ציטוטים — APA 7 ═════
• מחבר אחד: (Smith, 2020) או "Smith (2020) טען כי..."
• שני מחברים: (Smith & Jones, 2020) — תמיד & באנגלית; "סמית' וג'ונס (2020)" בעברית
• 3+ מחברים: (Smith et al., 2020) — מהציטוט הראשון
• ציטוט ישיר: (Smith, 2020, p. 45) — חובה מספר עמוד
• מספר מקורות: (Smith, 2020; Jones, 2019) — סדר אלפבתי
• אותו מחבר אותה שנה: (Smith, 2020a, 2020b)
• ציטוט נרטיבי לטענות מפתח: "Smith (2020) הראה ש..." במקום סוגריים
• אסור להמציא ציטוטים — רק מהרשימה שניתנה.

═════ היררכיית ראיות (פרון/הצעה/תיאוריה) ═════
• "Greene (2016) מצא ש..." — proven, ראיה אמפירית מפורשת (זמן עבר)
• "Korumaz (2016) הציע ש..." או "ייתכן ש..." או "עשוי להעיד" — suggested
• "פרלמן ופפלאו (1981) טוענים ש..." — theoretical, טיעון לא מבוסס בחינה
לעולם אל תקדם הצעה לממצא. שמור את ההיררכיה.

═════ חוזק ראיה — חובה לציין ═════
לכל מחקר שצוטט, ציין: שיטה (איכותני/כמותי/meta-analysis/case study) + n.
דוגמה: "Greene (2016, איכותני, n=14) מצא ש..." | "Mäkinen et al. (2026, אורכי, n=420) הראו..."
דרגות: strong (meta או RCT n>200), moderate (אמפירי 50-200), limited (מקרה/קטן/תיאורטי).

═════ שפה סיבתית — אזהרה ═════
חשוד: "גורם ל", "מוביל ל", "משפיע על", "כתוצאה מ", "causes", "leads to", "results in"
תקין רק עם: longitudinal / RCT / control group.
ב-cross-sectional / qualitative / observational — החלף ב:
"קשור ל" / "עשוי להוביל ל" / "מתואם עם" / "נמצא בקשר ל" / "is associated with" / "may contribute to"

═════ דואליות — חובה ═════
כל פסקה טיעונית — חייבת מתח פנימי. השתמש במחברים:
"אבל", "מצד שני", "ובכל זאת", "אף על פי", "למרות", "דווקא", "לעומת זאת"
פסקה ללא מתח פנימי = פסקה לא אקדמית. קול מבקר, לא קול מטיף.

═════ AI tells — אסור ═════
לעולם אל תכתוב את הביטויים האלה:
"חשוב לציין", "מעניין לראות", "נראה כי", "לסיכום", "ככלל", "באופן כללי",
"ראוי לציין", "כפי שצוין לעיל", "as shown above", "it can be shown", "it is important to note",
"במחקר הנוכחי", "המאמר הנוכחי" (זה סקירה, לא מחקר חדש),
"בסקירה זו נראה ש..." (אנונים — האם זו דעתך או של מישהו?)

═════ מבנה רטורי ═════
• עברית אקדמית, גוף שלישי או "אנו" (לא "אני" יחיד).
• פסקאות 80-150 מילים.
• כל פסקה — טענה אחת + 2-3 ציטוטים תומכים + ניואנס/דואליות + משפט סיכום.
• פתח כל סעיף בפסקה ממסגרת. סיים בפסקה שמכינה למעבר.
• מקורות אקדמיים בלבד — ללא בלוגים, חדשות, אתרי דעה.

═════ פלט ═════
• ללא רשימת מקורות בסוף הסעיף — היא תיבנה במאוחד.
• אסור JSON, אסור הסבר, אסור meta-commentary.
• רק תוכן הסעיף, ב-Markdown נקי."""


def write_section(section: dict, papers: list[dict],
                  topic_he: str, prior_summary: str = "") -> str:
    target_words = section["target_words"]
    top = _top_papers_for_section(papers, section, k=15)
    papers_brief = _format_papers_for_prompt(top)

    # Obsidian memory — voice rules, theoretical anchors, APA conventions
    memory_block = _obsidian_memory_for_prompt([
        "academic_writing_apa7",
        "voice_rules",
        "theoretical_anchors",
        "recurring_sources",
    ], max_chars_per_note=1200)

    prompt = f"""{memory_block}

נושא הסמינריון: {topic_he}

הסעיף הנוכחי: {section['title']}
מיקוד: {section['focus']}
יעד אורך: {target_words} מילים (±10%).

{f'סיכום קצר של הסעיפים הקודמים (להמשכיות): {prior_summary}' if prior_summary else 'זהו הסעיף הראשון.'}

מקורות זמינים לסעיף זה ({len(papers_brief)} מקורות):
{json.dumps(papers_brief, ensure_ascii=False, indent=1)}

המשימה: כתוב את סעיף "{section['title']}" — {target_words} מילים.

הוראות נוספות:
- צטט לפחות {max(4, target_words // 350)} מקורות שונים בסעיף
- בסוף הסעיף הוסף שורת ביניים (לא כותרת מודגשת)
- פתח בפסקה שמסגרת את הסעיף (לא חזרה על הכותרת)
- סיים בפסקה שמכינה את המעבר לסעיף הבא

החזר טקסט עברית בלבד — ללא תוויות, ללא JSON, ללא הסברים. רק תוכן הסעיף."""

    return ask_claude(prompt, system=SYSTEM_PROMPT, max_budget=3.0)


def summarize_for_continuity(section_text: str) -> str:
    """One-paragraph summary to feed into the next section's prompt."""
    if not section_text or len(section_text) < 200:
        return ""
    prompt = f"""סכם בפסקה אחת קצרה (50-80 מילים, עברית) את עיקרי הסעיף הבא — לצורך המשכיות בסעיף הבא:

{section_text[:3000]}

סיכום:"""
    try:
        return ask_claude(prompt, max_budget=0.3).strip()
    except Exception:
        return section_text[:300] + "..."


# ─────────────────────────────────────────────
# Bibliography
# ─────────────────────────────────────────────

def _extract_citations_from_text(text: str) -> set[str]:
    """Extract citation keys in any APA 7 format.

    Catches:
      • (Smith, 2020)               parenthetical
      • (Smith & Jones, 2020)       parenthetical, multi-author
      • (Smith et al., 2020)        parenthetical, et al.
      • Smith (2020, ...)           narrative
      • Smith and Jones (2020)      narrative, multi-author
      • פרלמן ופפלאו (1981)         narrative, Hebrew "and"
      • Dor-Haim ו-Oplatka (2020)   narrative, mixed
      • (כהן ועמיתיו, 2018)         parenthetical, Hebrew
    """
    keys = set()

    # Pattern 1: parenthetical (Author[s], YYYY)
    p1 = re.compile(r"\(([^()]{3,120}?,\s*\d{4}[a-z]?)\)")
    for m in p1.finditer(text):
        keys.add(m.group(1).strip())

    # Pattern 2: narrative — Author (YYYY) or Author and/& Author (YYYY)
    # Author = Latin word(s), Hebrew word(s), or hyphenated
    name_re = r"[A-Za-z֐-׿][\w\-']+"
    p2 = re.compile(
        rf"({name_re}(?:\s+(?:&|and|ו|et\s+al\.|ועמיתיו|ועמיתיה|et\.?\s+al)\s*-?\s*{name_re})?"
        rf"(?:\s+(?:&|and|ו)\s*-?\s*{name_re})?)"
        r"\s*\(\s*(\d{4}[a-z]?)"
    )
    for m in p2.finditer(text):
        author = m.group(1).strip()
        year = m.group(2)
        # skip noisy hits (single common words like "Table" or "Section")
        if author.lower() in {"table", "figure", "section", "see", "the", "this"}:
            continue
        keys.add(f"{author}, {year}")

    return keys


def _format_authors_apa7(authors_raw) -> str:
    """Convert raw authors string/list to APA 7 format: 'Lastname, F. M., & Lastname, F. M.'"""
    if isinstance(authors_raw, list):
        names = [str(a).strip() for a in authors_raw if a and str(a).strip()]
    else:
        s = str(authors_raw or "").strip()
        if not s:
            return ""
        # Split by &, ", and ", or commas-then-and
        names = [n.strip() for n in re.split(r"\s*&\s*|\s+and\s+|;\s*", s) if n and n.strip()]
        # Heuristic: if "Last, First, Last, First" → split every 2 commas
        if len(names) == 1 and s.count(",") >= 3:
            parts = [p.strip() for p in s.split(",") if p.strip()]
            paired = []
            for i in range(0, len(parts) - 1, 2):
                paired.append(f"{parts[i]}, {parts[i+1]}")
            names = paired

    formatted = []
    for n in names[:20]:  # APA 7: up to 20 authors before ...
        n = n.strip()
        if not n:
            continue
        # If "Last, First Middle" — keep as-is, normalize initials
        if "," in n:
            last, first = [x.strip() for x in n.split(",", 1)]
            initials = " ".join(f"{w[0]}." for w in first.split() if w)
            formatted.append(f"{last}, {initials}".strip(", "))
        else:
            # "First Last" or "First Middle Last" — flip
            tokens = n.split()
            if len(tokens) >= 2:
                last = tokens[-1]
                initials = " ".join(f"{w[0]}." for w in tokens[:-1])
                formatted.append(f"{last}, {initials}")
            else:
                formatted.append(n)

    if not formatted:
        return ""
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) == 2:
        return f"{formatted[0]}, & {formatted[1]}"
    # 3-20: comma-separated, & before last
    return ", ".join(formatted[:-1]) + ", & " + formatted[-1]


def _is_hebrew(s: str) -> bool:
    return bool(re.search(r"[֐-׿]", s or ""))


def build_bibliography(papers: list[dict], cited_keys: set[str]) -> str:
    """Return APA 7 bibliography of cited papers.

    Format:
      Lastname, F. M., & Lastname, F. M. (Year). Title of article.
      *Journal Name*, *Volume*(Issue), pages. https://doi.org/xxx

    Sorted: Hebrew first (alphabetical), then English (alphabetical).
    """
    refs_he = []
    refs_en = []
    seen = set()

    for p in papers:
        authors_raw = p.get("authors", "")
        year = p.get("year") or "n.d."
        title = (p.get("title") or "").strip().rstrip(".")
        venue = (p.get("venue") or p.get("source") or "").strip()
        url = (p.get("url") or p.get("pdf_url") or "").strip()
        doi = (p.get("doi") or "").strip()

        # Strip DOI prefix if it's already a full URL
        if doi.startswith("http"):
            doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)

        # Find first author surname for matching against cited_keys
        if isinstance(authors_raw, list):
            first_raw = str(authors_raw[0]) if authors_raw else ""
        else:
            first_raw = re.split(r"[,&]", str(authors_raw))[0].strip()
        first_tokens = first_raw.replace(",", "").split()
        first_author = first_tokens[-1] if first_tokens else first_tokens[0] if first_tokens else ""
        if not first_author or not year:
            continue

        # Word-boundary author match in cited_keys
        author_re = re.compile(r"\b" + re.escape(first_author) + r"\b", re.IGNORECASE)
        if not any(author_re.search(k) and str(year) in k for k in cited_keys):
            continue

        key = f"{first_author}_{year}"
        if key in seen:
            continue
        seen.add(key)

        # APA 7 formatted authors
        apa_authors = _format_authors_apa7(authors_raw)
        if not apa_authors:
            continue

        ref = f"{apa_authors} ({year}). {title}."
        if venue:
            ref += f" *{venue}*."
        if doi:
            ref += f" https://doi.org/{doi}"
        elif url and url.startswith("http"):
            ref += f" {url}"

        if _is_hebrew(title) or _is_hebrew(apa_authors):
            refs_he.append((apa_authors, ref))
        else:
            refs_en.append((apa_authors, ref))

    refs_he.sort(key=lambda x: x[0])
    refs_en.sort(key=lambda x: x[0])

    lines = []
    if refs_he:
        lines.append("### מקורות בעברית")
        lines.append("")
        for _, r in refs_he:
            lines.append(r)
            lines.append("")
    if refs_en:
        lines.append("### מקורות באנגלית")
        lines.append("")
        for _, r in refs_en:
            lines.append(r)
            lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="חוויית הבדידות של מנהל הפנימייה בישראל")
    ap.add_argument("--proposal", help="Path to proposal MD (for context)")
    ap.add_argument("--papers", required=True, help="Path to papers_full.json")
    ap.add_argument("--target-words", type=int, default=12000)
    ap.add_argument("--out-dir", help="Output dir (default: same as papers parent)")
    ap.add_argument("--no-resume", action="store_true",
                    help="Ignore cached sections and regenerate everything")
    ap.add_argument("--no-summary", action="store_true",
                    help="Skip continuity summarization (saves ~$1, sections won't reference prior)")
    args = ap.parse_args()

    papers_path = Path(args.papers)
    papers = _load_papers(papers_path)
    print(f"\n{'='*60}")
    print(f"📝 Seminar Writer")
    print(f"   Topic: {args.topic}")
    print(f"   Target: {args.target_words} מילים")
    print(f"   Papers: {len(papers)} ב-{papers_path}")
    print(f"{'='*60}\n")

    # Scale section targets to total
    total_blueprint = sum(s["target_words"] for s in SECTION_BLUEPRINT)
    scale = args.target_words / total_blueprint
    sections = [{**s, "target_words": int(s["target_words"] * scale)}
                for s in SECTION_BLUEPRINT]
    print("מבנה הסמינריון:")
    for s in sections:
        print(f"  · {s['title']:<55} ~{s['target_words']:>5} מילים")
    total = sum(s["target_words"] for s in sections)
    print(f"  ────────────────────────────────────────────────────")
    print(f"  סה\"כ: ~{total} מילים\n")

    out_dir = Path(args.out_dir) if args.out_dir else papers_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    sections_dir = out_dir / "_seminar_sections"
    sections_dir.mkdir(parents=True, exist_ok=True)

    # Write each section — checkpoint to disk after each one (resumable)
    section_texts = {}
    prior_summary = ""

    def _continuity(text: str) -> str:
        if args.no_summary:
            return ""
        return summarize_for_continuity(text)

    for i, sec in enumerate(sections, 1):
        cache_path = sections_dir / f"{sec['id']}.txt"
        if (not args.no_resume and cache_path.exists()
                and cache_path.stat().st_size > 200):
            text = cache_path.read_text(encoding="utf-8")
            words = len(text.split())
            print(f"\n[{i}/{len(sections)}] ♻️  משחזר: {sec['title']} ({words} מילים מ-cache)")
            section_texts[sec["id"]] = text
            prior_summary = _continuity(text)
            continue

        print(f"\n[{i}/{len(sections)}] ✍️  כותב: {sec['title']} (~{sec['target_words']} מילים)")
        try:
            text = write_section(sec, papers, args.topic, prior_summary)
            section_texts[sec["id"]] = text
            cache_path.write_text(text, encoding="utf-8")
            words = len(text.split())
            print(f"      → {words} מילים (saved: {cache_path.name})")
            prior_summary = _continuity(text)
        except Exception as e:
            print(f"      ⚠️ Failed: {e}")
            section_texts[sec["id"]] = f"[ERROR — לא נכתב: {e}]"

    # Concat
    print(f"\n📦 מאחד מסמך סופי...")
    parts = [
        f"# {args.topic}",
        "",
        f"_Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')} · 🦊 Moki · seminar_writer_",
        f"_מבוסס על {len(papers)} מקורות אקדמיים._",
        "",
        "---",
        "",
        "## תקציר",
        "",
        "(תקציר יתווסף בעריכה הסופית — 250-300 מילים שמסכמות את הטענה המרכזית, השיטה ותרומת הסמינריון.)",
        "",
        "---",
        "",
    ]
    for sec in sections:
        parts.append(f"## {sec['title']}")
        parts.append("")
        parts.append(section_texts.get(sec["id"], "[ERROR]"))
        parts.append("")

    # Bibliography
    full_text = "\n".join(parts)
    cited_keys = _extract_citations_from_text(full_text)
    print(f"   • {len(cited_keys)} ציטוטים שונים בטקסט")
    bib = build_bibliography(papers, cited_keys)
    parts.extend(["", "---", "", "## רשימת מקורות", "", bib])

    final_text = "\n".join(parts)
    word_count = len(final_text.split())

    out_path = out_dir / "04_seminar_paper.md"
    out_path.write_text(final_text, encoding="utf-8")

    structured_path = out_dir / "04_seminar_paper.json"
    structured_path.write_text(json.dumps({
        "topic": args.topic,
        "generated_at": datetime.now().isoformat(),
        "target_words": args.target_words,
        "actual_words": word_count,
        "sections": [
            {"id": s["id"], "title": s["title"],
             "target_words": s["target_words"],
             "actual_words": len(section_texts.get(s["id"], "").split()),
             "text": section_texts.get(s["id"], "")}
            for s in sections
        ],
        "cited_keys": sorted(cited_keys),
        "papers_count": len(papers),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"✅ סמינריון מוכן → {out_path}")
    print(f"{'='*60}")
    print(f"  📝 {word_count} מילים (יעד: {args.target_words})")
    print(f"  📚 {len(cited_keys)} ציטוטים")
    print(f"  📂 {out_path.name} + {structured_path.name}")
    print(f"\n  שורת המוצא בעריכה: {out_path}")

    # Sync to Obsidian — frontmatter, wikilinks, sources/topics indexes
    try:
        from obsidian_bridge import bridge_all
        print(f"\n  🌐 Syncing to Obsidian...")
        bridge_all()
    except Exception as e:
        print(f"  ⚠️ Obsidian sync skipped: {e}")


if __name__ == "__main__":
    main()
