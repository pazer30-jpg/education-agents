"""
rq_validator.py — Validate research papers actually address the research question.

הבעיה: Agent 0 קובע RQ, Agent 1 מוצא מאמרים — אבל האם הם **עונים** על השאלה?
לפעמים הם רק "קשורים לנושא" אבל לא נוגעים ב-RQ הספציפי.

הבדיקה:
  1. לכל מאמר → האם abstract/title נוגע ב-RQ ישירות, חלקית, או בכלל לא?
  2. ציון coverage כולל לקורפוס מול RQ
  3. דגלים: "RQ לא נענה" / "סטייה ל-tangents" / "פערים בסוגיות משנה"

Usage:
  python3 rq_validator.py <papers.json> "<RQ>"
  python3 rq_validator.py --auto <topic_slug>   # uses Agent 0's stored RQ
"""

import sys
import json
import re
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR, PAPERS_DIR


# ─────────────────────────────────────────────
# Concept extraction from RQ
# ─────────────────────────────────────────────

_HEBREW_STOPWORDS = {
    "של", "את", "על", "עם", "אל", "מן", "כדי", "אם", "כן", "לא", "זה",
    "האם", "מה", "איך", "למה", "מדוע", "מתי", "איפה", "מי",
    "תחת", "אילו", "תנאים", "ההבדל", "בין", "ל", "ה", "ו", "ב", "כ", "מ",
}
_ENGLISH_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "with",
    "what", "how", "why", "when", "where", "who", "which", "is", "are",
    "and", "or", "but", "as", "by", "from", "about",
    "under", "between", "difference", "relate", "context",
}


# Bilingual concept map — Hebrew → English (for matching against EN abstracts)
_HE_EN_CONCEPT_MAP = {
    "שייכות": ["belonging", "belonging-ness"],
    "חוסן": ["resilience", "resilient"],
    "טראומה": ["trauma", "traumatic"],
    "מנהיגות": ["leadership", "leader"],
    "נוער": ["youth", "adolescent", "teenager"],
    "תנועות": ["movements", "movement"],
    "חינוך": ["education", "educational"],
    "בלתי-פורמלי": ["non-formal", "informal", "out-of-school"],
    "בלתי": ["non", "informal"],
    "מדריכים": ["counselors", "facilitators", "guides"],
    "מדריך": ["counselor", "facilitator", "guide"],
    "קבוצה": ["group"],
    "קבוצתית": ["group", "collective"],
    "זהות": ["identity"],
    "תקווה": ["hope"],
    "דיאלוג": ["dialogue"],
    "ערכים": ["values"],
    "זיכרון": ["memory", "remembrance"],
    "חירום": ["emergency", "crisis", "war"],
    "מתבגרים": ["adolescents", "teenagers"],
    "מצבי": ["situations", "states"],
    "בנוער": ["youth", "adolescent"],
    "ישראל": ["israel", "israeli"],
    "ישראלי": ["israeli"],
    "ערבי": ["arab"],
    "פנימייה": ["boarding", "boarding school"],
    "כפר": ["village"],
    "מכינה": ["pre-military", "preparatory"],
}


def _to_lemma(token: str) -> str:
    """Strip common Hebrew prefixes (ב, מ, ל, ה, ו, כ, ש)."""
    if len(token) > 3 and token[0] in "בלמהוכש":
        return token[1:]
    return token


def extract_concepts(rq: str) -> list[str]:
    """
    Pull substantive concepts from a research question.
    For each Hebrew concept, also include English equivalents — so we can match
    against English-language abstracts.
    """
    cleaned = re.sub(r"[?,.!:;()\"']", " ", rq.lower())
    tokens = cleaned.split()
    stops = _HEBREW_STOPWORDS | _ENGLISH_STOPWORDS

    concepts = []
    seen = set()
    for raw_t in tokens:
        if len(raw_t) <= 2 or raw_t in stops:
            continue
        # Add original form
        if raw_t not in seen:
            seen.add(raw_t)
            concepts.append(raw_t)
        # Try Hebrew lemma + English equivalents
        lemma = _to_lemma(raw_t)
        if lemma != raw_t and lemma not in seen and lemma not in stops:
            seen.add(lemma)
            concepts.append(lemma)
        # Add English translations of known Hebrew concepts
        for he_concept, en_words in _HE_EN_CONCEPT_MAP.items():
            if he_concept in raw_t or raw_t in he_concept:
                for en in en_words:
                    if en not in seen:
                        seen.add(en)
                        concepts.append(en)
    return concepts[:14]  # allow more — we have HE+EN pairs


# ─────────────────────────────────────────────
# Per-paper scoring
# ─────────────────────────────────────────────

def score_paper_vs_rq(paper: dict, concepts: list[str]) -> dict:
    """
    Score how directly a paper addresses the RQ concepts.
    Returns: {"score": 0-100, "matched": [concept], "verdict": str}
    """
    text = " ".join([
        (paper.get("title") or ""),
        (paper.get("abstract") or "")[:500],
    ]).lower()

    if not text.strip() or not concepts:
        return {"score": 0, "matched": [], "verdict": "no_data"}

    matched = [c for c in concepts if c in text]
    coverage = len(matched) / len(concepts)
    score = round(coverage * 100)

    # Bonus: concepts in title carry more weight
    title_lower = (paper.get("title") or "").lower()
    title_hits = sum(1 for c in concepts if c in title_lower)
    if title_hits >= 2:
        score = min(100, score + 15)

    if score >= 60:
        verdict = "direct"
    elif score >= 30:
        verdict = "tangential"
    else:
        verdict = "off_topic"

    return {
        "score": score,
        "matched": matched,
        "verdict": verdict,
    }


# ─────────────────────────────────────────────
# Corpus-level validation
# ─────────────────────────────────────────────

def validate_corpus_vs_rq(papers: list[dict], rq: str) -> dict:
    """
    Validate that a corpus of papers actually addresses an RQ.
    Returns: {
      "rq": str,
      "concepts": [list],
      "coverage_score": 0-100,  # how well does corpus answer RQ?
      "direct_papers": [...],
      "tangential_papers": [...],
      "off_topic_papers": [...],
      "verdict": "well_answered" | "partial" | "weak" | "off_target",
      "missing_concepts": [list],  # concepts not appearing in any paper
      "recommendation": str,
    }
    """
    concepts = extract_concepts(rq)
    if not concepts:
        return {
            "rq": rq, "concepts": [], "coverage_score": 0,
            "verdict": "no_concepts_extracted",
            "recommendation": "RQ too vague to extract concepts — reformulate",
        }

    direct = []
    tangential = []
    off_topic = []
    concept_hits = {c: 0 for c in concepts}

    for p in papers:
        scored = score_paper_vs_rq(p, concepts)
        for c in scored["matched"]:
            concept_hits[c] += 1
        entry = {
            "title": (p.get("title") or "")[:100],
            "year": p.get("year"),
            "score": scored["score"],
            "matched": scored["matched"],
        }
        if scored["verdict"] == "direct":
            direct.append(entry)
        elif scored["verdict"] == "tangential":
            tangential.append(entry)
        else:
            off_topic.append(entry)

    # Corpus coverage
    n_direct = len(direct)
    n_tangential = len(tangential)
    total = len(papers) or 1
    direct_ratio = n_direct / total
    coverage_score = round(direct_ratio * 70 + (n_tangential / total) * 30 * 100 / 100)
    coverage_score = min(100, round(direct_ratio * 100 + (n_tangential / total) * 30))

    missing_concepts = [c for c, hits in concept_hits.items() if hits == 0]

    # Verdict
    if direct_ratio >= 0.4 and len(missing_concepts) <= 1:
        verdict = "well_answered"
        rec = "המאמרים עונים על השאלה. אפשר להמשיך לכתיבה."
    elif direct_ratio >= 0.2:
        verdict = "partial"
        rec = f"רק {n_direct}/{total} מאמרים נוגעים ישירות. שקול חיפוש נוסף או הצרה של RQ."
    elif n_tangential >= total * 0.5:
        verdict = "weak"
        rec = "רוב המאמרים tangential — RQ אולי רחב מדי או הקורפוס סוטה."
    else:
        verdict = "off_target"
        rec = f"רוב המאמרים לא נוגעים ב-RQ. מומלץ לחזור ל-Researcher עם queries חדים יותר."

    if missing_concepts:
        rec += f" מושגים חסרים: {', '.join(missing_concepts)}"

    return {
        "rq": rq,
        "concepts": concepts,
        "coverage_score": coverage_score,
        "direct_papers": direct[:10],
        "tangential_papers": tangential[:10],
        "off_topic_papers": off_topic[:5],
        "n_direct": n_direct,
        "n_tangential": n_tangential,
        "n_off_topic": len(off_topic),
        "missing_concepts": missing_concepts,
        "concept_hits": concept_hits,
        "verdict": verdict,
        "recommendation": rec,
    }


# ─────────────────────────────────────────────
# Format report
# ─────────────────────────────────────────────

def format_report(result: dict) -> str:
    icons = {"well_answered": "✅", "partial": "⚠️", "weak": "❌", "off_target": "🚨"}
    icon = icons.get(result.get("verdict"), "?")

    lines = [
        f"\n🔬 RQ Validator — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"\n  RQ: {result.get('rq', '?')}",
        f"  Concepts: {', '.join(result.get('concepts', []))}",
        f"\n  {icon} Verdict: {result.get('verdict')} (coverage {result.get('coverage_score', 0)}/100)",
        f"  💡 {result.get('recommendation', '')}",
        f"\n  📊 Distribution:",
        f"    ✓ Direct:     {result.get('n_direct', 0)}",
        f"    ~ Tangential: {result.get('n_tangential', 0)}",
        f"    ✗ Off-topic:  {result.get('n_off_topic', 0)}",
    ]

    if result.get("missing_concepts"):
        lines.append(f"\n  🔎 Missing concepts: {', '.join(result['missing_concepts'])}")

    if result.get("direct_papers"):
        lines.append(f"\n  ✓ Direct papers (top 5):")
        for p in result["direct_papers"][:5]:
            lines.append(f"    [{p['score']}%] {p['title']} ({p.get('year','?')})")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    if "--auto" in sys.argv:
        # Use Agent 0's stored RQ for a topic
        idx = sys.argv.index("--auto")
        if idx + 1 < len(sys.argv):
            topic_slug = sys.argv[idx + 1]
            from memory import load_memory
            mem = load_memory()
            plan = mem.get("next_plan", {})
            rqs = plan.get("research_questions", [])
            rq = next((r["question"] for r in rqs if topic_slug.lower() in r["topic"].lower()), None)
            if not rq:
                print(f"  ❌ No RQ for topic matching '{topic_slug}'")
                return
            # Find papers
            papers_file = next(PAPERS_DIR.glob(f"*{topic_slug}*_papers.json"), None)
            if not papers_file:
                print(f"  ❌ No papers file for '{topic_slug}'")
                return
            papers = json.loads(papers_file.read_text(encoding="utf-8")).get("papers", [])
            result = validate_corpus_vs_rq(papers, rq)
            print(format_report(result))
        return

    if len(sys.argv) < 3:
        print("Usage: python3 rq_validator.py <papers.json> \"<RQ>\"")
        print("       python3 rq_validator.py --auto <topic_slug>")
        return

    papers_path = Path(sys.argv[1])
    rq = sys.argv[2]
    if not papers_path.exists():
        print(f"  ❌ File not found: {papers_path}")
        return

    data = json.loads(papers_path.read_text(encoding="utf-8"))
    papers = data.get("papers", data) if isinstance(data, dict) else data
    result = validate_corpus_vs_rq(papers, rq)
    print(format_report(result))


if __name__ == "__main__":
    main()
