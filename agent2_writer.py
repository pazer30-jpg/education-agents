"""
Agent 2 - Writer
קורא מאמרים וכותב מאמר אקדמי מלא עם Claude CLI
פלט: Markdown + DOCX — בעברית ובאנגלית
"""

import json
from pathlib import Path
from datetime import datetime
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from config import ARTICLES_DIR
from claude_cli import ask_claude


# ─────────────────────────────────────────────
# DOCX builder
# ─────────────────────────────────────────────

def _markdown_to_docx(md_text: str, title: str, output_path: Path):
    doc = Document()
    for section in doc.sections:
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1.25)
        section.right_margin = Inches(1.25)

    # Title
    title_para = doc.add_paragraph()
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title_para.add_run(title)
    run.font.size = Pt(16)
    run.font.bold = True
    run.font.color.rgb = RGBColor(0x1A, 0x1A, 0x2E)

    # Date
    date_para = doc.add_paragraph()
    date_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    date_run = date_para.add_run(datetime.now().strftime("%B %Y"))
    date_run.font.size = Pt(10)
    date_run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
    doc.add_paragraph()

    for line in md_text.split("\n"):
        line = line.rstrip()
        if line.startswith("## "):
            h = doc.add_heading(line[3:], level=1)
            if h.runs:
                h.runs[0].font.color.rgb = RGBColor(0x1A, 0x1A, 0x2E)
        elif line.startswith("### "):
            h = doc.add_heading(line[4:], level=2)
            if h.runs:
                h.runs[0].font.color.rgb = RGBColor(0x2D, 0x2D, 0x5E)
        elif line.startswith("**") and line.endswith("**"):
            p = doc.add_paragraph()
            r = p.add_run(line.strip("*"))
            r.bold = True
            r.font.size = Pt(11)
        elif line.startswith("- ") or line.startswith("* "):
            p = doc.add_paragraph(line[2:], style="List Bullet")
            if p.runs:
                p.runs[0].font.size = Pt(11)
        elif line.strip():
            p = doc.add_paragraph(line)
            if p.runs:
                p.runs[0].font.size = Pt(11)
            p.paragraph_format.space_after = Pt(6)
        else:
            doc.add_paragraph()

    doc.save(str(output_path))
    return output_path


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _split_title(article_md: str, default_title: str) -> tuple[str, str]:
    """Extract # title from first line, return (title, body)."""
    lines = article_md.strip().split("\n")
    if lines[0].startswith("# "):
        return lines[0][2:].strip(), "\n".join(lines[1:]).strip()
    return default_title, article_md.strip()


# ─────────────────────────────────────────────
# Main agent function
# ─────────────────────────────────────────────

def _load_papers_from_files(papers_files: list[Path]) -> tuple[list[dict], list[str]]:
    """Load and merge papers from multiple JSON files. Returns (papers, topics)."""
    all_papers = []
    topics = []
    seen_ids = set()

    for pf in papers_files:
        if not pf.exists():
            print(f"  [Agent2] ⚠️  {pf.name} לא נמצא — מדלג")
            continue
        with open(pf, encoding="utf-8") as f:
            data = json.load(f)
        topic = data.get("topic", pf.stem)
        papers = data.get("papers", data) if isinstance(data, dict) else data
        topics.append(topic)
        for p in papers:
            pid = p.get("paperId") or p.get("title", "")[:60]
            if pid not in seen_ids:
                seen_ids.add(pid)
                p["_source_topic"] = topic
                all_papers.append(p)

    # Prefer fulltext over abstract; trim to context-safe size
    for p in all_papers:
        if p.get("fulltext") and len(p["fulltext"]) > 200:
            # Use fulltext — trim if too long
            if len(p["fulltext"]) > 8000:
                p["fulltext"] = p["fulltext"][:8000] + "..."
            p.pop("abstract", None)   # drop abstract to save tokens
        elif isinstance(p.get("abstract"), str) and len(p["abstract"]) > 500:
            p["abstract"] = p["abstract"][:500] + "..."

    return all_papers, topics


def run_writer(papers_files: Path | list[Path], combined_title: str = "",
               bilingual: bool = True) -> dict[str, Path]:
    """
    papers_files: single Path or list of Paths (one per researched topic)
    combined_title: optional display title for the combined article
    bilingual: if True (default), write both EN and HE articles
    """
    if isinstance(papers_files, Path):
        papers_files = [papers_files]

    names = " + ".join(p.name for p in papers_files[:3])
    print(f"\n{'='*60}")
    print(f"✍️  Agent 2 - Writer | {len(papers_files)} קבצי מחקר: {names}")
    print(f"{'='*60}\n")

    all_papers, topics = _load_papers_from_files(papers_files)
    topics_str = " × ".join(topics)
    display_title = combined_title or topics_str

    if not all_papers:
        raise ValueError("No papers found in any of the provided files.")

    print(f"  [Agent2] {len(all_papers)} מאמרים מ-{len(topics)} נושאים: {topics_str}")

    # ── Paper Analyzer (Agent 1.7) — synthesis map ──
    synthesis_block = ""
    try:
        from paper_analyzer import run_paper_analyzer
        analysis = run_paper_analyzer(papers_files[-1])
        synthesis_block = analysis["synthesis_map"]
        print(f"  [Agent2] Synthesis map מוכן — {len(analysis.get('profiles',[]))} profiles")
    except Exception as e:
        print(f"  [Agent2] ⚠️  Paper analysis דולג ({e})")

    # Build topic breakdown for prompt
    topic_breakdown = "\n".join(
        f"  - {t}: {sum(1 for p in all_papers if p.get('_source_topic') == t)} papers"
        for t in topics
    )

    system = """You are a senior academic writer specializing in education research.
You write strictly according to APA 7th edition.
You write review/synthesis articles, NOT empirical studies.

Write a SYNTHESIZED article that weaves together multiple topics into one coherent argument.
Do NOT write separate sections per topic — integrate them throughout.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARTICLE STRUCTURE — MANDATORY SECTIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You MUST include ALL of the following sections in this exact order.
Omitting any section is a failure.

## Abstract (150-200 words)
## Introduction
  - Open with the broad problem in the field
  - State 2-3 explicit Research Questions (RQ1, RQ2, RQ3)
  - Each RQ must be answerable from the reviewed literature
  - End with article purpose and scope

## Methodology
  - Databases searched (Semantic Scholar, OpenAlex, ERIC, CORE, Crossref)
  - Search terms used
  - Inclusion criteria: peer-reviewed, years, language
  - Exclusion criteria
  - Total found → screened → included (state numbers)
  - This is a review article — the methodology describes HOW you searched, not an experiment

## Theoretical Framework
## Literature Review (integrated synthesis with subsections)
## Discussion
## Limitations of This Review
  - Language bias (English-only sources)
  - Database limitations
  - Date range constraints
  - Gaps in available literature
## Conclusions
## References

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CRITICAL SOURCE EVALUATION — MANDATORY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
When citing a source, you MUST evaluate it — not just report it:
  - Method type: "In a quantitative study (n=400)..." / "Using qualitative interviews (n=12)..."
  - Sample size: always state n= when available
  - Generalizability: "While limited to [context], the findings suggest..."
  - Contrast weak vs strong evidence: "X (n=23) found A, but the larger study by Y (n=1,200) contradicts this"
  - State limitations: "however, participants were self-selected" / "the sample was limited to [country]"

Do NOT treat all sources as equal. A meta-analysis carries more weight than a case study.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COMPARISON TABLE — MANDATORY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Include AT LEAST one Markdown comparison table in the Literature Review:

| Study | Year | Method | Sample | Key Finding |
|-------|------|--------|--------|-------------|
| Author et al. | 2019 | Quantitative | n=400 | ... |

The table should compare 6-10 key studies side by side.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
APA 7 CITATION RULES — MANDATORY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
In-text citations:
  One author:    (Smith, 2019)
  Two authors:   (Smith & Jones, 2019)
  3+ authors:    (Smith et al., 2019)
  Direct quote:  (Smith, 2019, p. 45)
  Narrative:     Smith (2019) argued that...

Every claim, finding, or idea from a source MUST have an in-text citation.
Minimum 20 in-text citations across the article.
Do NOT write a paragraph without at least one citation.

References list (APA 7):
  Author, A. A., & Author, B. B. (Year). Title of article.
  Journal Name, Volume(Issue), pages. https://doi.org/xxxxx

Rules:
  - Only list sources actually cited in the text
  - DOI as URL when available
  - Alphabetical by first author last name
  - Never: "important to note", "it can be seen", "as shown above"
"""

    synthesis_section = f"\n\nSYNTHESIS MAP (use this to structure your article — write about the debates, fill the gaps, reference the consensus):\n{synthesis_block}\n" if synthesis_block else ""

    prompt = f"""Research topics (to be synthesized into ONE article): {topics_str}

Topic breakdown:
{topic_breakdown}
{synthesis_section}
All papers available ({len(all_papers)} total):
{json.dumps(all_papers, ensure_ascii=False, indent=1)}

Write a full synthesized academic article (3,000–4,500 words) that argues ONE central thesis
connecting all {len(topics)} topics.

MANDATORY structure — do NOT skip any section:

## Abstract
(150–200 words — thesis + RQs + key conclusions)

## Introduction
- Broad problem → specific gap → 2-3 Research Questions (RQ1, RQ2, RQ3)
- Each RQ is answered later in the article

## Methodology
- Databases: Semantic Scholar, OpenAlex, ERIC, CORE, Crossref, Unpaywall
- Search terms used (list the actual keywords)
- Inclusion: peer-reviewed, 2000-2026, English
- Found: ~{len(all_papers)*4} → Screened: ~{len(all_papers)*2} → Included: {len(all_papers)}

## Theoretical Framework
(Shared theories across all {len(topics)} topics)

## Literature Review
- Integrated synthesis with 4-6 thematic subsections
- Include a comparison table (Markdown table with Study | Year | Method | Sample | Key Finding)
- Critically evaluate sources: state n=, method type, generalizability
- Do NOT summarize paper by paper — synthesize by theme

## Discussion
- Answer each RQ explicitly
- Tensions between findings, practical implications

## Limitations of This Review
- Language bias, database limitations, date range, gaps

## Conclusions
(Summary + specific future research directions tied to the gaps)

## References
(APA 7 — only papers actually cited, alphabetical)

Important: The article must feel like ONE coherent piece, not {len(topics)} separate reviews joined together.
Every paragraph must have at least one citation with critical evaluation."""

    base = "_x_".join(t.replace(" ", "_").lower()[:15] for t in topics)[:50]

    # ── English article ──────────────────────────
    print("  [Agent2] Writing English article (1-2 min)...")
    article_en = ask_claude(prompt, system=system, max_budget=3.5)
    title_en, content_en = _split_title(article_en, f"Synthesized Article: {display_title}")

    md_en   = ARTICLES_DIR / f"{base}_en.md"
    docx_en = ARTICLES_DIR / f"{base}_en.docx"
    with open(md_en, "w", encoding="utf-8") as f:
        f.write(f"# {title_en}\n\n{content_en}")
    _markdown_to_docx(content_en, title_en, docx_en)
    print(f"  [Agent2] English saved: {md_en.name}, {docx_en.name}")

    if not bilingual:
        saved_paths = {"md": md_en, "docx": docx_en}
        print(f"\n✅ Agent 2 complete → 2 files saved in {ARTICLES_DIR}\n")
        return saved_paths

    # ── Hebrew article — TRANSLATION (not rewriting) ──
    print("  [Agent2] Translating to Hebrew (חוסך זמן וטוקנים)...")

    system_he = """אתה מתרגם אקדמי. תרגם את המאמר הזה מאנגלית לעברית טבעית.
שמור על:
  - כל הציטוטים בפורמט המקורי (Smith, 2019)
  - כל השמות והמספרים
  - מבנה הסעיפים (## headers)
  - הטון האקדמי
זה תרגום, לא כתיבה מחדש."""

    prompt_he = f"""תרגם את המאמר הבא לעברית טבעית. שמור על המבנה והציטוטים:

{article_en}"""

    try:
        article_he = ask_claude(prompt_he, system=system_he, max_budget=2.5)
        title_he, content_he = _split_title(article_he, f"מאמר סינתטי: {display_title}")

        md_he   = ARTICLES_DIR / f"{base}_he.md"
        docx_he = ARTICLES_DIR / f"{base}_he.docx"
        with open(md_he, "w", encoding="utf-8") as f:
            f.write(f"# {title_he}\n\n{content_he}")
        _markdown_to_docx(content_he, title_he, docx_he)
        print(f"  [Agent2] Hebrew saved: {md_he.name}, {docx_he.name}")

        saved_paths = {"md": md_en, "docx": docx_en, "md_he": md_he, "docx_he": docx_he}
        print(f"\n✅ Agent 2 complete → 4 files saved in {ARTICLES_DIR}\n")
    except Exception as e:
        print(f"  ⚠️  [Agent2] Hebrew article failed ({e}) — continuing with English only")
        saved_paths = {"md": md_en, "docx": docx_en}
        print(f"\n✅ Agent 2 complete → 2 files saved in {ARTICLES_DIR}\n")

    return saved_paths


# ─────────────────────────────────────────────
# Standalone
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if not args:
        # Use all JSON files in papers dir
        papers_list = sorted(Path("output/papers").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    else:
        papers_list = [Path(a) for a in args]
    paths = run_writer(papers_list)
    print(f"Article saved: {paths}")
