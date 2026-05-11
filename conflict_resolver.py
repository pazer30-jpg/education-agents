"""
conflict_resolver.py — Moki Agent 1.7.5: contradiction adjudicator.

When two papers in the corpus contradict each other, the team used to "present
both sides" — which is honest but unhelpful. This module weighs the evidence
heuristically (sample size, study type, replication, venue) and picks a winner
when the gap is large enough.

Pure Python, no LLM calls. Designed to plug into paper_analyzer.py after the
existing corpus_methodology_warning step, and to be queryable from the chat via
the `סתירות` / `conflicts` commands.

Public API
──────────
  find_conflicts(papers)          → list[dict]  (topic, paper_a, paper_b, nature)
  weigh_evidence(paper_a, paper_b)→ dict        (a_score, b_score, winner, reasoning)
  format_conflict_report(...)     → str         (Markdown)
"""

from __future__ import annotations

import re
from typing import Iterable


# ─────────────────────────────────────────────
# Constants — heuristic scoring weights
# ─────────────────────────────────────────────

_RCT_TERMS              = ("rct", "randomized controlled", "randomised controlled",
                           "randomized trial", "randomised trial")
_LONGITUDINAL_TERMS     = ("longitudinal", "cohort study", "panel study",
                           "follow-up over", "followed over")
_CROSS_SECTIONAL_TERMS  = ("cross-sectional", "cross sectional")
_CASE_STUDY_TERMS       = ("case study", "case-study", "single case")
_META_ANALYSIS_TERMS    = ("meta-analysis", "meta analysis", "metaanalysis",
                           "systematic review")
_REPLICATION_TERMS      = ("replication", "replicated", "pre-registered",
                           "preregistered", "registered report",
                           "multi-site", "multisite", "across studies")

# Negation markers — a paper that explicitly negates the topic is treated
# as the "opposite finding" side. Lowercase substring match.
_NEGATION_MARKERS = (
    "no effect", "no significant effect", "did not", "does not",
    "not associated", "no association", "no relationship",
    "not found", "no evidence", "fails to", "failed to",
    "cannot", "rejected", "refuted", "contrary to",
    "not improve", "did not improve", "does not improve",
    "harmful", "negative effect", "adverse", "decreased",
    "אין השפעה", "לא נמצא", "לא משפיע", "לא קשור",
    "אין קשר", "סותר", "פוגע",
)

# Positive markers — paper supports/confirms the topic.
_POSITIVE_MARKERS = (
    "improved", "improves", "enhanced", "enhances",
    "increased", "increases", "positive effect",
    "significantly", "supported", "confirmed",
    "associated with", "predicts", "predicted",
    "boosts", "boosted", "strengthens", "strengthened",
    "השפיע", "שיפר", "מחזק", "תרם", "קשור ל",
)


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────

def _coerce_findings_text(paper: dict) -> str:
    """Flatten everything text-ish from a paper profile into one lowercase blob."""
    parts: list[str] = []
    for key in ("thesis", "abstract", "_abstract", "title", "_title"):
        val = paper.get(key)
        if isinstance(val, str):
            parts.append(val)

    findings = paper.get("findings") or []
    for f in findings:
        if isinstance(f, dict):
            parts.append(str(f.get("claim", "")))
            parts.append(str(f.get("evidence", "")))
        elif isinstance(f, str):
            parts.append(f)

    for c in paper.get("contradictions") or []:
        parts.append(str(c))

    for k in paper.get("key_concepts") or []:
        parts.append(str(k))

    return " ".join(parts).lower()


def _extract_topic_signal(paper: dict) -> tuple[str, set[str]]:
    """Pull a representative topic phrase plus a bag of concept tokens."""
    concepts = paper.get("key_concepts") or []
    primary = ""
    if concepts and isinstance(concepts[0], (str, int, float)):
        primary = str(concepts[0])
    if not primary:
        primary = (paper.get("thesis") or paper.get("_title") or "").strip()

    tokens: set[str] = set()
    for c in concepts:
        if isinstance(c, str):
            for w in re.findall(r"[A-Za-z֐-׿]+", c.lower()):
                if len(w) > 3:
                    tokens.add(w)
    return primary[:80], tokens


def _stance(text: str) -> str:
    """Heuristic: does this paper read as 'positive' or 'negative' on its topic?"""
    pos = sum(1 for m in _POSITIVE_MARKERS if m in text)
    neg = sum(1 for m in _NEGATION_MARKERS if m in text)
    if neg > pos and neg >= 1:
        return "negative"
    if pos > neg and pos >= 1:
        return "positive"
    return "neutral"


def _study_type(paper: dict, text: str) -> str:
    """
    Map heuristics + the LLM-extracted `method` field to a coarse study type.
    Priority: meta-analysis > RCT > longitudinal > cross-sectional > case study.
    """
    method = (paper.get("method") or "").strip().lower()
    if method == "meta_analysis" or any(t in text for t in _META_ANALYSIS_TERMS):
        return "meta-analysis"
    if any(t in text for t in _RCT_TERMS):
        return "RCT"
    if any(t in text for t in _LONGITUDINAL_TERMS):
        return "longitudinal"
    if method == "case_study" or any(t in text for t in _CASE_STUDY_TERMS):
        return "case_study"
    if any(t in text for t in _CROSS_SECTIONAL_TERMS):
        return "cross-sectional"
    if method == "empirical":
        return "empirical"
    if method == "theoretical":
        return "theoretical"
    return "unknown"


def _venue_quartile(paper: dict) -> str:
    """Best-effort venue tier extraction (Q1/Q2/other)."""
    for key in ("venue_quartile", "quartile", "_quartile"):
        v = paper.get(key)
        if isinstance(v, str) and v.upper() in ("Q1", "Q2", "Q3", "Q4"):
            return v.upper()
    venue = " ".join(str(paper.get(k, "") or "") for k in
                     ("venue", "_venue", "journal", "_journal")).lower()
    if "q1" in venue:
        return "Q1"
    if "q2" in venue:
        return "Q2"
    return ""


def _label(paper: dict) -> str:
    title = paper.get("_title") or paper.get("title") or "Untitled"
    year = paper.get("_year") or paper.get("year") or "?"
    return f"{title[:55]} ({year})"


# ─────────────────────────────────────────────
# Public function 1: find_conflicts
# ─────────────────────────────────────────────

def find_conflicts(papers: list[dict]) -> list[dict]:
    """
    Scan analyzed papers for conflicts.

    Two sources:
      1. Each profile's `contradictions` field (internal to a paper) — these
         are surfaced as self-conflicts.
      2. Pairs of papers with overlapping concept tokens but opposite stances
         (one "positive", one "negative") on the same topic.

    Returns a list of dicts:
      {"topic": str, "paper_a": dict, "paper_b": dict, "nature_of_conflict": str}
    """
    conflicts: list[dict] = []
    if not papers:
        return conflicts

    # Pre-compute text and stance per paper
    enriched: list[tuple[dict, str, str, tuple[str, set[str]]]] = []
    for p in papers:
        if not isinstance(p, dict):
            continue
        text = _coerce_findings_text(p)
        stance = _stance(text)
        topic_signal = _extract_topic_signal(p)
        enriched.append((p, text, stance, topic_signal))

    # 1) Internal contradictions surfaced by paper_analyzer
    for p, _text, _stance, (primary, _tokens) in enriched:
        for c in p.get("contradictions") or []:
            if not str(c).strip():
                continue
            conflicts.append({
                "topic": primary or "internal claim",
                "paper_a": p,
                "paper_b": p,
                "nature_of_conflict": (
                    f"Internal contradiction within {_label(p)}: {str(c)[:160]}"
                ),
            })

    # 2) Cross-paper opposite stances on overlapping topic
    n = len(enriched)
    for i in range(n):
        p_a, text_a, stance_a, (topic_a, tokens_a) = enriched[i]
        for j in range(i + 1, n):
            p_b, text_b, stance_b, (topic_b, tokens_b) = enriched[j]
            if stance_a == "neutral" or stance_b == "neutral":
                continue
            if stance_a == stance_b:
                continue
            shared = tokens_a & tokens_b
            # Need real topical overlap to avoid spurious conflicts
            if len(shared) < 2:
                continue

            shared_topic = ", ".join(sorted(shared)[:3]) or topic_a or topic_b
            nature = (
                f"{_label(p_a)} reports {stance_a} findings on '{shared_topic}', "
                f"while {_label(p_b)} reports {stance_b} findings."
            )
            conflicts.append({
                "topic": shared_topic,
                "paper_a": p_a,
                "paper_b": p_b,
                "nature_of_conflict": nature,
            })

    return conflicts


# ─────────────────────────────────────────────
# Public function 2: weigh_evidence
# ─────────────────────────────────────────────

def _credibility_score(paper: dict) -> tuple[int, list[str]]:
    """Compute one paper's credibility score and return (score, reasons)."""
    score = 0
    reasons: list[str] = []
    text = _coerce_findings_text(paper)

    # Sample size — additive (>500 implies >200 too).
    n = paper.get("sample_size")
    if isinstance(n, (int, float)) and n > 0:
        n_int = int(n)
        if n_int > 500:
            score += 30
            reasons.append(f"sample n={n_int} (>500): +30")
        elif n_int > 200:
            score += 20
            reasons.append(f"sample n={n_int} (>200): +20")
        else:
            reasons.append(f"sample n={n_int} (<=200): +0")

    # Study type — pick the strongest applicable bucket
    stype = _study_type(paper, text)
    type_points = {
        "meta-analysis": 35,
        "RCT": 25,
        "longitudinal": 20,
        "cross-sectional": 5,
        "case_study": 0,
        "empirical": 5,
        "theoretical": 0,
        "unknown": 0,
    }
    pts = type_points.get(stype, 0)
    score += pts
    reasons.append(f"study type {stype}: +{pts}")

    # Replication / multi-site / pre-registration
    if any(t in text for t in _REPLICATION_TERMS):
        score += 15
        reasons.append("replication/pre-registration mentioned: +15")

    # Publication venue quartile
    q = _venue_quartile(paper)
    if q == "Q1":
        score += 15
        reasons.append("venue Q1: +15")
    elif q == "Q2":
        score += 10
        reasons.append("venue Q2: +10")

    # Clamp to 0-100
    score = max(0, min(100, score))
    return score, reasons


def weigh_evidence(paper_a: dict, paper_b: dict) -> dict:
    """
    Score two papers and pick a winner.

    Returns:
      {"a_score": int, "b_score": int, "winner": "a"|"b"|"tie", "reasoning": str}

    Tie band: |a-b| < 8 → "tie" (the gap isn't decisive enough to override
    the default "present both" stance).
    """
    a_score, a_reasons = _credibility_score(paper_a or {})
    b_score, b_reasons = _credibility_score(paper_b or {})

    diff = a_score - b_score
    if abs(diff) < 8:
        winner: str = "tie"
    elif diff > 0:
        winner = "a"
    else:
        winner = "b"

    a_label = _label(paper_a or {})
    b_label = _label(paper_b or {})
    reasoning_lines = [
        f"A — {a_label}: {a_score}/100",
        "  " + "; ".join(a_reasons) if a_reasons else "  (no signals)",
        f"B — {b_label}: {b_score}/100",
        "  " + "; ".join(b_reasons) if b_reasons else "  (no signals)",
    ]
    if winner == "tie":
        reasoning_lines.append(
            f"Verdict: tie (gap {abs(diff)} < 8). Present both sides."
        )
    else:
        winning_label = a_label if winner == "a" else b_label
        reasoning_lines.append(
            f"Verdict: {winner.upper()} wins by {abs(diff)} points "
            f"→ {winning_label} carries more weight."
        )

    return {
        "a_score": a_score,
        "b_score": b_score,
        "winner": winner,
        "reasoning": "\n".join(reasoning_lines),
    }


# ─────────────────────────────────────────────
# Public function 3: format_conflict_report
# ─────────────────────────────────────────────

def format_conflict_report(conflicts: list[dict]) -> str:
    """Markdown summary of all conflicts with verdicts."""
    if not conflicts:
        return "# Conflict Report\n\nNo conflicts detected in the corpus.\n"

    lines: list[str] = ["# Conflict Report", ""]
    lines.append(f"Detected **{len(conflicts)}** conflict(s) in the corpus.")
    lines.append("")

    for i, conf in enumerate(conflicts, 1):
        topic = conf.get("topic", "?")
        nature = conf.get("nature_of_conflict", "")
        p_a = conf.get("paper_a") or {}
        p_b = conf.get("paper_b") or {}

        lines.append(f"## {i}. {topic}")
        lines.append("")
        lines.append(f"**Nature:** {nature}")
        lines.append("")
        lines.append(f"- **Paper A:** {_label(p_a)}")
        lines.append(f"- **Paper B:** {_label(p_b)}")
        lines.append("")

        # Self-conflicts: only one paper involved — skip the duel.
        if p_a is p_b or (
            p_a.get("_paper_id")
            and p_a.get("_paper_id") == p_b.get("_paper_id")
        ):
            lines.append("_Internal contradiction — no inter-paper duel applicable._")
            lines.append("")
            continue

        verdict = weigh_evidence(p_a, p_b)
        lines.append(
            f"**Scores:** A = {verdict['a_score']}/100  ·  "
            f"B = {verdict['b_score']}/100"
        )
        winner = verdict["winner"]
        if winner == "tie":
            lines.append("**Verdict:** tie — present both sides.")
        else:
            label = _label(p_a) if winner == "a" else _label(p_b)
            lines.append(f"**Verdict:** {winner.upper()} wins → cite {label}.")
        lines.append("")
        lines.append("```")
        lines.append(verdict["reasoning"])
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# Convenience: run on profiles list (used by paper_analyzer)
# ─────────────────────────────────────────────

def resolve_corpus_conflicts(profiles: list[dict]) -> dict:
    """
    Run the full pipeline on a profiles list. Returns:
      {
        "count": int,
        "conflicts": [
            {topic, paper_a_title, paper_b_title, nature_of_conflict,
             a_score, b_score, winner, reasoning},
            ...
        ],
        "report_md": str,
      }
    Lightweight (no full paper objects nested) so it embeds cleanly into the
    Agent 1.7 JSON output.
    """
    raw_conflicts = find_conflicts(profiles or [])
    flattened: list[dict] = []
    for c in raw_conflicts:
        p_a = c.get("paper_a") or {}
        p_b = c.get("paper_b") or {}
        is_self = p_a is p_b or (
            p_a.get("_paper_id") and p_a.get("_paper_id") == p_b.get("_paper_id")
        )
        entry = {
            "topic": c.get("topic", ""),
            "paper_a_title": _label(p_a),
            "paper_b_title": _label(p_b),
            "nature_of_conflict": c.get("nature_of_conflict", ""),
            "self_conflict": bool(is_self),
        }
        if not is_self:
            verdict = weigh_evidence(p_a, p_b)
            entry.update({
                "a_score": verdict["a_score"],
                "b_score": verdict["b_score"],
                "winner": verdict["winner"],
                "reasoning": verdict["reasoning"],
            })
        flattened.append(entry)

    result = {
        "count": len(flattened),
        "conflicts": flattened,
        "report_md": format_conflict_report(raw_conflicts),
    }

    # ── Reciprocal feedback: tell writer about contradictions in corpus ──
    # If the corpus has contradicting findings, the writer should explicitly
    # acknowledge them rather than picking one side silently.
    real_conflicts = [c for c in flattened if not c.get("self_conflict")]
    if real_conflicts:
        try:
            from scratchpad import note as _scratch_note
            top = real_conflicts[:3]
            summaries = [
                f"{c.get('paper_a_title', '?')[:40]} vs {c.get('paper_b_title', '?')[:40]} "
                f"({c.get('nature_of_conflict', '?')[:50]})"
                for c in top
            ]
            _scratch_note("conflict_resolver", "corpus_contradictions", {
                "issue": f"{len(real_conflicts)} סתירות בקורפוס — חובה להזכיר במאמר",
                "examples": summaries,
                "summary": "הצג את הדיבייט: 'חוקרים חלוקים — X טוען Y, בעוד Z טוען W'",
            })
        except Exception:
            pass

    return result


# ─────────────────────────────────────────────
# CLI: pretty-print the latest analysis file's conflicts
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path
    from config import PAPERS_DIR

    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        candidates = sorted(PAPERS_DIR.glob("*analysis*.json"),
                            key=lambda p: p.stat().st_mtime)
        if not candidates:
            print("No analysis files found in PAPERS_DIR.")
            sys.exit(1)
        target = candidates[-1]

    data = json.loads(target.read_text(encoding="utf-8"))
    profiles = data.get("profiles") or []
    out = resolve_corpus_conflicts(profiles)
    print(out["report_md"])
    print(f"\n[source: {target.name} — {out['count']} conflict(s)]")
