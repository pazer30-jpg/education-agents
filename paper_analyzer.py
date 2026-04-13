"""
paper_analyzer.py — ניתוח מאמרים ומיפוי קשרים (Agent 1.7)
רץ בין Agent 1.5 (PDF) ל-Agent 2 (Writer).

שלב 1: ניתוח כל מאמר (thesis, method, findings, limitations)
שלב 2: מיפוי קשרים (agreements, debates, complements, gaps)
שלב 3: synthesis map ש-Agent 2 כותב ממנו
"""

import json
from pathlib import Path
from datetime import datetime
from config import PAPERS_DIR
from claude_cli import ask_claude_json, ask_claude


# ─────────────────────────────────────────────
# Step 1: Analyze single paper
# ─────────────────────────────────────────────

def analyze_paper(paper: dict) -> dict:
    """מנתח מאמר אחד — מחזיר structured profile + quantitative details."""
    text = (paper.get("fulltext") or paper.get("abstract") or paper.get("title", ""))[:4000]

    if not text.strip():
        return {"thesis": "N/A", "method": "unknown", "findings": [],
                "limitations": [], "open_questions": [], "key_concepts": [],
                "_title": paper.get("title", ""), "_year": paper.get("year")}

    prompt = f"""Paper: {paper.get('title','Untitled')} ({paper.get('year','')})
Authors: {paper.get('authors','')}
Citations: {paper.get('citation_count', 0)}

Content:
{text}

Extract the following information. For quantitative fields: use EXACT
numbers from the text, or null if not stated. DO NOT guess or estimate.

Return JSON with:
  thesis: central claim (1 sentence)
  method: empirical|theoretical|review|meta_analysis|case_study|mixed

  findings: list of objects, each with:
    {
      "claim": "the statement itself (max 20 words)",
      "type": "proven" (past tense, direct result, "we found X")
            | "suggested" (hedged, "we suggest", "may indicate", "seems to")
            | "theoretical" (argument not empirically tested),
      "evidence": "the specific evidence in the paper, or 'none' if unsupported"
    }
  IMPORTANT: distinguish "we found that belonging enhances resilience"
  (proven) from "we suggest belonging may enhance resilience" (suggested).
  Do NOT promote suggestions to findings.

  contradictions: [list of internal contradictions found, or empty list]
  Scan the text carefully: does the paper claim X in one place and
  ¬X in another? List them.

  limitations: [1-2 limitations]
  open_questions: [1-2 questions left unanswered]
  key_concepts: [3 main concepts]
  population: who was studied (age, context, country)

  QUANTITATIVE (null if not explicitly stated):
  sample_size: integer (e.g. 400) or null
  statistical_method: "regression"|"ANOVA"|"SEM"|"qualitative coding"|"thematic analysis"|"grounded theory"|"meta-analysis"|null
  effect_size: string with metric (e.g. "d=0.45", "r=0.31", "OR=1.8") or null
  p_value: string (e.g. "p<.001", "p=.03") or null
  confidence_interval: string (e.g. "95% CI [0.2, 0.6]") or null
  study_duration: string (e.g. "6 months", "2 years") or null
  data_collection_years: string (e.g. "2018-2020") or null

  CRITICAL — only fill quantitative fields if the EXACT value appears
  in the text. If you are not sure or inferring — use null. Never make
  up numbers.

  evidence_strength: "strong" (meta-analysis/RCT n>200) | "moderate" (empirical n=50-200) | "limited" (case study/small/theoretical)
  era: "foundational" (<2005) | "established" (2005-2015) | "recent" (2016+)
JSON only."""

    try:
        profile = ask_claude_json(prompt, max_budget=0.3)
        profile["_paper_id"] = paper.get("paperId", "")
        profile["_title"] = paper.get("title", "")
        profile["_year"] = paper.get("year")
        profile["_source"] = paper.get("source", "")
        return profile
    except Exception as e:
        return {"thesis": text[:100], "method": "unknown", "findings": [],
                "limitations": [], "open_questions": [], "key_concepts": [],
                "_title": paper.get("title", ""), "_error": str(e)}


# ─────────────────────────────────────────────
# Step 2: Map relationships between papers
# ─────────────────────────────────────────────

def map_relationships(profiles: list[dict]) -> dict:
    """ממפה קשרים בין כל המאמרים."""
    summaries = [{
        "title":    p.get("_title", "")[:60],
        "year":     p.get("_year"),
        "thesis":   p.get("thesis", "")[:100],
        "method":   p.get("method"),
        "findings": p.get("findings", [])[:3],
        "key_concepts": p.get("key_concepts", [])[:3],
    } for p in profiles]

    prompt = f"""Analyze relationships between these {len(summaries)} papers:

{json.dumps(summaries, ensure_ascii=False, indent=2)}

Return JSON with:
  agreements: [{{papers: [title1, title2], on: "what they agree about"}}]
  debates: [{{papers: [title1, title2], about: "what they disagree on"}}]
  complements: [{{papers: [title1, title2], how: "how they complement"}}]
  gaps: ["gap 1", "gap 2", "gap 3"]
  consensus: ["consensus claim 1", "consensus claim 2"]
  tensions: ["tension 1", "tension 2"]
  understudied: ["understudied area 1"]
JSON only."""

    try:
        return ask_claude_json(prompt, max_budget=0.8)
    except Exception as e:
        return {"agreements": [], "debates": [], "complements": [],
                "gaps": [], "consensus": [], "tensions": [],
                "understudied": [], "_error": str(e)}


# ─────────────────────────────────────────────
# Step 3: Build synthesis map for Agent 2
# ─────────────────────────────────────────────

def build_synthesis_map(profiles: list[dict], relationships: dict,
                        topic: str) -> str:
    lines = [
        f"SYNTHESIS MAP — {topic}",
        f"({len(profiles)} papers analyzed)",
        "═" * 45, "",
    ]

    consensus = relationships.get("consensus", [])
    if consensus:
        lines.append("CONSENSUS (cross-paper agreement):")
        for c in consensus[:4]:
            lines.append(f"  ✓ {c}")
        lines.append("")

    debates = relationships.get("debates", [])
    if debates:
        lines.append("ACTIVE DEBATES:")
        for d in debates[:3]:
            papers = " vs ".join(d.get("papers", [])[:2])
            lines.append(f"  ↔ {d.get('about','')} [{papers}]")
        lines.append("")

    gaps = relationships.get("gaps", [])
    if gaps:
        lines.append("RESEARCH GAPS (use in Introduction + Conclusions):")
        for g in gaps[:4]:
            lines.append(f"  ? {g}")
        lines.append("")

    tensions = relationships.get("tensions", [])
    if tensions:
        lines.append("FIELD TENSIONS (good for Discussion):")
        for t in tensions[:3]:
            lines.append(f"  ⚡ {t}")
        lines.append("")

    # Evidence strength summary
    strong = [p for p in profiles if p.get("evidence_strength") == "strong"]
    moderate = [p for p in profiles if p.get("evidence_strength") == "moderate"]
    limited = [p for p in profiles if p.get("evidence_strength") == "limited"]
    if strong or moderate or limited:
        lines.append("EVIDENCE STRENGTH (weight citations accordingly):")
        if strong:
            lines.append(f"  ★★★ Strong ({len(strong)}): " +
                         ", ".join(p.get("_title","")[:30] for p in strong[:3]))
        if moderate:
            lines.append(f"  ★★  Moderate ({len(moderate)}): " +
                         ", ".join(p.get("_title","")[:30] for p in moderate[:3]))
        if limited:
            lines.append(f"  ★   Limited ({len(limited)}): " +
                         ", ".join(p.get("_title","")[:30] for p in limited[:3]))
        lines.append("")

    # Temporal evolution
    foundational = [p for p in profiles if p.get("era") == "foundational"]
    established = [p for p in profiles if p.get("era") == "established"]
    recent = [p for p in profiles if p.get("era") == "recent"]
    if foundational or established or recent:
        lines.append("TEMPORAL EVOLUTION (write a narrative arc):")
        if foundational:
            lines.append(f"  Early (<2005): {', '.join(p.get('_title','')[:30] for p in foundational[:2])}")
        if established:
            lines.append(f"  Middle (2005-15): {', '.join(p.get('_title','')[:30] for p in established[:2])}")
        if recent:
            lines.append(f"  Recent (2016+): {', '.join(p.get('_title','')[:30] for p in recent[:2])}")
        lines.append("")

    lines.append("PAPER QUICK-REFERENCE (use exact numbers — never invent):")
    for p in profiles[:12]:
        strength = {"strong": "★★★", "moderate": "★★", "limited": "★"}.get(p.get("evidence_strength",""), "")
        # Build quantitative tag: n=400, d=0.45, p<.001
        quant_parts = []
        if p.get("sample_size"):
            quant_parts.append(f"n={p['sample_size']}")
        if p.get("effect_size"):
            quant_parts.append(p["effect_size"])
        if p.get("p_value"):
            quant_parts.append(p["p_value"])
        quant = " | ".join(quant_parts) if quant_parts else "no quant data"

        stat_method = f" [{p['statistical_method']}]" if p.get("statistical_method") else ""
        duration = f" · {p['study_duration']}" if p.get("study_duration") else ""

        lines.append(f"  [{p.get('_year','')}] {p.get('_title','')[:45]} {strength}")
        lines.append(f"    → {p.get('thesis','')[:70]}")
        lines.append(f"    📊 {quant}{stat_method}{duration}")

        # Show findings by claim type — distinguish proven from suggested
        findings = p.get("findings", [])
        if findings and isinstance(findings[0], dict):
            for f in findings[:3]:
                icon = {"proven": "✓", "suggested": "~", "theoretical": "T"}.get(f.get("type", ""), "?")
                lines.append(f"    {icon} [{f.get('type','?')}] {f.get('claim','')[:75]}")

        # Flag contradictions
        contras = p.get("contradictions", [])
        if contras:
            for c in contras[:2]:
                lines.append(f"    ⚡ CONTRADICTION: {str(c)[:80]}")

    lines += ["", "═" * 45,
              "CRITICAL RULES for the writer:",
              "1. Use EXACT numbers from 📊 — never invent.",
              "2. ✓ proven = direct evidence. Cite as fact.",
              "3. ~ suggested = hedged claim. Write 'X suggested that...' — do NOT upgrade to fact.",
              "4. T theoretical = no empirical test. Write as theoretical argument.",
              "5. ⚡ contradictions = note them in Discussion. Do not hide."]

    return "\n".join(lines)


# ─────────────────────────────────────────────
# Main runner
# ─────────────────────────────────────────────

def run_paper_analyzer(papers_file: Path) -> dict:
    print(f"\n{'='*60}")
    print(f"🔬 Agent 1.7 — Paper Analyzer | {papers_file.name}")
    print(f"{'='*60}\n")

    data = json.loads(papers_file.read_text(encoding="utf-8"))
    papers = data.get("papers", data) if isinstance(data, dict) else data
    topic = data.get("topic", "") if isinstance(data, dict) else ""

    print(f"  שלב 1: מנתח {len(papers)} מאמרים...")
    profiles = []
    for i, paper in enumerate(papers, 1):
        title = paper.get("title", "")[:45]
        print(f"  [{i}/{len(papers)}] {title}...")
        profiles.append(analyze_paper(paper))

    print(f"\n  שלב 2: ממפה קשרים...")
    relationships = map_relationships(profiles)

    print(f"\n  שלב 3: בונה synthesis map...")
    synthesis_map = build_synthesis_map(profiles, relationships, topic)

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    slug = papers_file.stem.replace("_papers", "").replace("_enriched", "")
    save_path = PAPERS_DIR / f"{slug}_analysis_{ts}.json"
    output = {
        "topic": topic,
        "source_file": str(papers_file),
        "analyzed_at": datetime.now().isoformat(),
        "paper_count": len(papers),
        "profiles": profiles,
        "relationships": relationships,
        "synthesis_map": synthesis_map,
    }
    save_path.write_text(json.dumps(output, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    deb = len(relationships.get("debates", []))
    gap = len(relationships.get("gaps", []))
    con = len(relationships.get("consensus", []))
    print(f"""
  ✅ ניתוח הושלם:
     קונסנסוס:  {con} טענות מוסכמות
     מחלוקות:   {deb} דיבייטים פעילים
     פערים:     {gap} שאלות פתוחות
     נשמר:      {save_path.name}
""")
    return {
        "profiles": profiles,
        "relationships": relationships,
        "synthesis_map": synthesis_map,
        "saved_to": save_path,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        pf = Path(sys.argv[1])
    else:
        candidates = sorted(PAPERS_DIR.glob("*enriched*.json"),
                            key=lambda p: p.stat().st_mtime)
        if not candidates:
            candidates = sorted(PAPERS_DIR.glob("*papers*.json"),
                                key=lambda p: p.stat().st_mtime)
        if not candidates:
            print("לא נמצאו קבצי מאמרים")
            sys.exit(1)
        pf = candidates[-1]

    result = run_paper_analyzer(pf)
    print(result["synthesis_map"][:1000])
