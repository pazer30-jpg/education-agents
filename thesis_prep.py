"""
thesis_prep.py — Single-topic deep prep for a thesis.

לא מתאים לפוסטים — לתזה אקדמית בלבד.
מריץ ברצף: חיפוש מקורות → הצעת מחקר → 15 שאלות מחקר → תקצירי קריאה.

Usage:
  python3 thesis_prep.py "בדידות של מנהל פנימייה"
  python3 thesis_prep.py --topic "X" --angles a,b,c
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR
from agent1_researcher import run_researcher
from claude_cli import ask_claude_json, ask_claude


THESIS_DIR = OUTPUT_DIR / "thesis"


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M")


def _load_papers(papers_path: Path) -> list[dict]:
    data = json.loads(papers_path.read_text(encoding="utf-8"))
    return data.get("papers", []) if isinstance(data, dict) else data


def generate_proposal(topic_he: str, topic_en: str, angles: list[str],
                      papers: list[dict]) -> dict:
    """10-section research proposal grounded in the actual paper list."""
    papers_brief = [
        {
            "title": p.get("title", "")[:140],
            "authors": p.get("authors", "")[:80] if isinstance(p.get("authors"), str)
                       else ", ".join(str(a) for a in (p.get("authors") or [])[:2])[:80],
            "year": p.get("year"),
            "citations": p.get("citation_count", 0),
            "abstract": (p.get("abstract") or "")[:300],
        }
        for p in papers[:15]
    ]

    prompt = f"""אתה חוקר בכיר במדעי החינוך שעוזר לכתוב הצעת מחקר לתזה.

נושא התזה: {topic_he}
({topic_en})

זוויות שזוהו:
{json.dumps(angles, ensure_ascii=False, indent=1)}

מקורות שאותרו ({len(papers_brief)}):
{json.dumps(papers_brief, ensure_ascii=False, indent=1)}

המשימה: בנה הצעת מחקר מלאה לתזת מאסטר באוניברסיטה ישראלית — איכותנית.
הקפד:
- כל טענה רקעית — מעוגנת בלפחות מקור אחד מהרשימה (שם מחבר + שנה).
- אל תמציא מקורות.
- כתוב בעברית אקדמית, גוף ראשון יחיד.
- היה ספציפי וצנוע. תזת מאסטר, לא דוקטורט.

החזר JSON עם השדות הבאים (כל הטקסטים בעברית, שדות שצוין בהם 'באנגלית' באנגלית):
{{
  "title_he": "כותרת התזה (עד 90 תווים)",
  "title_en": "English title for abstract",
  "background": "רקע — 250-350 מילים, 3-4 פסקאות, עם ציטוטים מהמקורות",
  "rationale": "למה דווקא הנושא הזה — 150 מילים",
  "research_questions": {{
    "main": "שאלת מחקר ראשית אחת חדה",
    "sub": ["שאלת משנה 1", "שאלת משנה 2", "שאלת משנה 3"]
  }},
  "theoretical_framework": "מסגרת תיאורטית — 2-3 תיאוריות מקשרות (פרום? בובר? Heifetz?). 200 מילים.",
  "methodology": {{
    "approach": "איכותני / איכותני-פנומנולוגי / מעורב",
    "participants": "12-15 מנהלי פנימייה (תיאור מדגם)",
    "data_collection": "ראיון עומק חצי-מובנה (60-90 דקות), אפשרות לפגישת follow-up",
    "analysis": "Thematic analysis לפי Braun & Clarke (2006), קידוד ב-MAXQDA או ATLAS.ti",
    "validity": "Member checking, peer debriefing, audit trail"
  }},
  "ethics": "סוגיות אתיקה ייחודיות — אישור ועדה מוסדית, סודיות, רגישות (מנהל יושב על נתונים אישיים של חניכים)",
  "expected_contribution": "תרומה צפויה — 3-4 שורות, ספציפי ולא מנופח",
  "timeline_months": "12-18 חודשים — חלק לאבני דרך",
  "risks": ["סיכון 1", "סיכון 2", "סיכון 3"],
  "limitations": ["מגבלה 1", "מגבלה 2"],
  "open_questions_for_advisor": ["שאלה 1 שצריך לדון עליה עם המנחה", "שאלה 2"]
}}

החזר JSON בלבד."""

    return ask_claude_json(prompt, max_budget=2.5)


def generate_rqs(topic_he: str, papers: list[dict]) -> dict:
    """15 candidate research questions in 3 tiers."""
    papers_brief = [{"t": p.get("title", "")[:120], "y": p.get("year")} for p in papers[:15]]

    prompt = f"""נושא: {topic_he}
מקורות שזוהו: {json.dumps(papers_brief, ensure_ascii=False)}

צור 15 שאלות מחקר מועמדות לתזה — ב-3 שכבות:

א) שאלות תיאורטיות (5) — מה ידוע, מה חסר, איזה מתח מושגי
ב) שאלות איכותניות לראיונות (5) — שאלות שאפשר לחקור עם 12-15 מנהלים
ג) שאלות יישומיות / השלכות (5) — מה זה אומר על מדיניות, על הכשרה, על הפנימייה כמערכת

לכל שאלה:
- ספציפית (לא "האם בדידות חשובה?")
- ניתנת לחקירה
- מכילה מתח/דיבט/הבחנה
- מנוסחת בעברית אקדמית

החזר JSON:
{{
  "theoretical": ["שאלה 1", ...],
  "qualitative": ["שאלה 1", ...],
  "applied": ["שאלה 1", ...],
  "recommended_main_rq": "השאלה שאני ממליץ עליה כ-RQ ראשית של התזה",
  "why_recommended": "למה דווקא היא — 2-3 משפטים"
}}

החזר JSON בלבד."""

    return ask_claude_json(prompt, max_budget=1.0)


def render_proposal_md(prop: dict, topic_he: str, papers_count: int) -> str:
    rqs = prop.get("research_questions", {})
    method = prop.get("methodology", {})
    sub_rqs = rqs.get("sub", [])
    risks = prop.get("risks", [])
    limitations = prop.get("limitations", [])
    advisor_qs = prop.get("open_questions_for_advisor", [])

    parts = [
        f"# הצעת מחקר לתזה — {prop.get('title_he', topic_he)}",
        "",
        f"_Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')} · 🦊 Moki · thesis_prep_",
        f"_מבוסס על {papers_count} מקורות אקדמיים שאותרו אוטומטית._",
        "",
        f"**English title:** {prop.get('title_en', '')}",
        "",
        "---",
        "",
        "## 1. רקע",
        "",
        prop.get("background", ""),
        "",
        "## 2. רציונל",
        "",
        prop.get("rationale", ""),
        "",
        "## 3. שאלות מחקר",
        "",
        f"**שאלת מחקר ראשית:**",
        "",
        f"> {rqs.get('main', '')}",
        "",
        "**שאלות משנה:**",
        "",
    ]
    for sq in sub_rqs:
        parts.append(f"- {sq}")

    parts.extend([
        "",
        "## 4. מסגרת תיאורטית",
        "",
        prop.get("theoretical_framework", ""),
        "",
        "## 5. מתודולוגיה",
        "",
        f"**גישה:** {method.get('approach', '')}  ",
        f"**משתתפים:** {method.get('participants', '')}  ",
        f"**איסוף נתונים:** {method.get('data_collection', '')}  ",
        f"**ניתוח:** {method.get('analysis', '')}  ",
        f"**תוקף ומהימנות:** {method.get('validity', '')}",
        "",
        "## 6. אתיקה",
        "",
        prop.get("ethics", ""),
        "",
        "## 7. תרומה צפויה",
        "",
        prop.get("expected_contribution", ""),
        "",
        "## 8. לו\"ז",
        "",
        prop.get("timeline_months", ""),
        "",
        "## 9. סיכונים ומגבלות",
        "",
        "**סיכונים:**",
        "",
    ])
    for r in risks:
        parts.append(f"- {r}")
    parts.extend(["", "**מגבלות:**", ""])
    for l in limitations:
        parts.append(f"- {l}")

    parts.extend([
        "",
        "## 10. שאלות פתוחות למנחה",
        "",
    ])
    for q in advisor_qs:
        parts.append(f"- {q}")

    parts.extend([
        "",
        "---",
        "",
        "## ✅ צ'ק-ליסט להמשך",
        "",
        "- [ ] בדיקת עומק עם מנחה — האם השאלה הראשית בת-מחקר?",
        "- [ ] אישור ועדת אתיקה מוסדית (IRB)",
        "- [ ] גיוס משתתפים — קשר עם מועצת המנהלים של פנימיות עליית הנוער / כפרי נוער",
        "- [ ] הרחבת סקירת ספרות ל-50-80 מקורות",
        "- [ ] פיילוט עם 2 מנהלים לפני הראיונות הרשמיים",
    ])

    return "\n".join(parts)


def render_rqs_md(rqs: dict, topic_he: str) -> str:
    parts = [
        f"# 15 שאלות מחקר מועמדות — {topic_he}",
        "",
        f"_Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')}_",
        "",
        "---",
        "",
        "## 🌟 שאלת המחקר המומלצת",
        "",
        f"> **{rqs.get('recommended_main_rq', '')}**",
        "",
        rqs.get("why_recommended", ""),
        "",
        "---",
        "",
        "## א) שאלות תיאורטיות",
        "",
    ]
    for q in rqs.get("theoretical", []):
        parts.append(f"- {q}")
    parts.extend(["", "## ב) שאלות איכותניות (לראיונות)", ""])
    for q in rqs.get("qualitative", []):
        parts.append(f"- {q}")
    parts.extend(["", "## ג) שאלות יישומיות / השלכות", ""])
    for q in rqs.get("applied", []):
        parts.append(f"- {q}")
    return "\n".join(parts)


def render_lit_review_skeleton(papers: list[dict], topic_he: str) -> str:
    parts = [
        f"# סקירת ספרות — שלד — {topic_he}",
        "",
        f"_Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')} · {len(papers)} מקורות_",
        "",
        "_זה שלד אוטומטי. הספרות הסופית דורשת קריאה מעמיקה ופירוש שלך._",
        "",
        "---",
        "",
        "## רשימת מקורות",
        "",
    ]
    sorted_papers = sorted(papers, key=lambda p: -(p.get("citation_count") or 0))
    for i, p in enumerate(sorted_papers, 1):
        authors = p.get("authors", "")
        if isinstance(authors, list):
            authors = ", ".join(str(a) for a in authors[:3])
        year = p.get("year", "?")
        title = p.get("title", "")
        cites = p.get("citation_count", 0)
        url = p.get("url") or p.get("pdf_url", "")
        relevance = p.get("relevance_note", "")
        parts.append(f"### {i}. {title}")
        parts.append(f"")
        parts.append(f"**{authors}** ({year}) · {cites} ציטוטים")
        if url:
            parts.append(f"")
            parts.append(f"🔗 {url}")
        if relevance:
            parts.append(f"")
            parts.append(f"_{relevance}_")
        abstract = (p.get("abstract") or "")[:400]
        if abstract:
            parts.append(f"")
            parts.append(f"> {abstract}...")
        parts.append("")
        parts.append("---")
        parts.append("")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("topic", nargs="?", help="נושא התזה בעברית")
    ap.add_argument("--topic-en", help="English query for paper search")
    ap.add_argument("--angles", help="3 search angles, comma-separated (English)")
    ap.add_argument("--skip-search", action="store_true", help="Reuse existing papers JSON if present")
    ap.add_argument("--papers-json", help="Use a specific papers JSON (e.g. from thesis_lit_collector)")
    args = ap.parse_args()

    topic_he = args.topic or "בדידות של מנהל פנימייה"

    # Defaults tuned for the boarding-school-principal-loneliness thesis
    if "בדידות" in topic_he and "פנימי" in topic_he:
        topic_en = args.topic_en or "loneliness of boarding school principals"
        angles = (args.angles.split(",") if args.angles else [
            "loneliness school leadership principal isolation",
            "boarding school residential care principal experience phenomenology",
            "educational leadership burnout social support",
        ])
    else:
        topic_en = args.topic_en or topic_he
        angles = args.angles.split(",") if args.angles else [topic_en]

    THESIS_DIR.mkdir(parents=True, exist_ok=True)
    work_dir = THESIS_DIR / _stamp()
    work_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"🎓 Thesis Prep — {topic_he}")
    print(f"   English: {topic_en}")
    print(f"   Output:  {work_dir}")
    print(f"{'='*60}\n")

    # 1. Get papers — either from collector JSON or via Agent 1
    if args.papers_json:
        papers_path = Path(args.papers_json)
        print(f"📚 [1/3] טוען מקורות מ-{papers_path}...")
        papers = _load_papers(papers_path)
        # Use top-50 most relevant for proposal; rest goes into lit review skeleton
        if papers and "relevance" in papers[0]:
            papers = sorted(papers, key=lambda p: (p.get("relevance", 0),
                                                    p.get("citation_count", 0) or 0),
                            reverse=True)
        print(f"   → {len(papers)} מקורות זמינים (top 30 ייכנסו ל-proposal)\n")
    else:
        print("📚 [1/3] חיפוש מקורות אקדמיים...")
        papers_path = run_researcher(topic_en, angles, force=not args.skip_search)
        papers = _load_papers(papers_path)
        print(f"   → {len(papers)} מקורות זמינים\n")

    # 2. Generate proposal — use top 30 most relevant for grounding
    print("📋 [2/3] בניית הצעת מחקר (10 חלקים)...")
    proposal_papers = papers[:30] if len(papers) > 30 else papers
    try:
        proposal = generate_proposal(topic_he, topic_en, angles, proposal_papers)
        proposal_md = render_proposal_md(proposal, topic_he, len(papers))
        (work_dir / "01_proposal.md").write_text(proposal_md, encoding="utf-8")
        (work_dir / "01_proposal.json").write_text(
            json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   → 01_proposal.md ({len(proposal_md)} תווים)\n")
    except Exception as e:
        print(f"   ⚠️ Proposal failed: {e}\n")
        proposal = {}

    # 3. Generate 15 RQs — use top 20 papers for grounding
    print("🔬 [3/3] 15 שאלות מחקר מועמדות...")
    try:
        rqs = generate_rqs(topic_he, papers[:20])
        rqs_md = render_rqs_md(rqs, topic_he)
        (work_dir / "02_research_questions.md").write_text(rqs_md, encoding="utf-8")
        (work_dir / "02_research_questions.json").write_text(
            json.dumps(rqs, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   → 02_research_questions.md\n")
    except Exception as e:
        print(f"   ⚠️ RQs failed: {e}\n")

    # 4. Lit review skeleton
    print("📚 [bonus] שלד סקירת ספרות...")
    lit_md = render_lit_review_skeleton(papers, topic_he)
    (work_dir / "03_lit_review_skeleton.md").write_text(lit_md, encoding="utf-8")
    print(f"   → 03_lit_review_skeleton.md\n")

    print(f"{'='*60}")
    print(f"✅ Thesis prep complete → {work_dir}")
    print(f"{'='*60}\n")
    print("הפלט:")
    print(f"  📋 01_proposal.md             — הצעת מחקר 10 חלקים")
    print(f"  🔬 02_research_questions.md   — 15 RQs + המלצה")
    print(f"  📚 03_lit_review_skeleton.md  — {len(papers)} מקורות עם תקצירים")
    print(f"\nפתח באובסידיאן: thesis/{work_dir.name}/")

    # Sync to Obsidian
    try:
        from obsidian_bridge import bridge_all
        print(f"\n🌐 Syncing to Obsidian...")
        bridge_all()
    except Exception as e:
        print(f"⚠️ Obsidian sync skipped: {e}")


if __name__ == "__main__":
    main()
