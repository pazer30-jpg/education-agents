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
    """מנתח מאמר אחד — מחזיר structured profile."""
    text = (paper.get("fulltext") or paper.get("abstract") or paper.get("title", ""))[:3000]

    if not text.strip():
        return {"thesis": "N/A", "method": "unknown", "findings": [],
                "limitations": [], "open_questions": [], "key_concepts": [],
                "_title": paper.get("title", ""), "_year": paper.get("year")}

    prompt = f"""Paper: {paper.get('title','Untitled')} ({paper.get('year','')})
Authors: {paper.get('authors','')}

Content:
{text}

Analyze and return JSON with:
  thesis: central claim (1 sentence)
  method: empirical|theoretical|review|mixed|case_study
  findings: [3-5 key findings, max 20 words each]
  limitations: [1-2 limitations]
  open_questions: [1-2 questions left unanswered]
  key_concepts: [3 main concepts]
  population: who was studied
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

    lines.append("PAPER QUICK-REFERENCE:")
    for p in profiles[:12]:
        lines.append(f"  [{p.get('_year','')}] {p.get('_title','')[:45]}")
        lines.append(f"    → {p.get('thesis','')[:80]} [{p.get('method','')}]")

    lines += ["", "═" * 45,
              "Use this map to SYNTHESIZE, not summarize.",
              "Every paragraph should connect multiple papers."]

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
