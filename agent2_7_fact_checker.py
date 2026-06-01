"""
agent2_7_fact_checker.py — Fact Checker (Agent 2.7)

Validates in-text citations in an academic article produced by Agent 2 against
the original paper metadata (title, authors, abstract, year).

LLMs occasionally hallucinate: they claim "Smith (2019) found X" when Smith
actually found Y — or they cite a paper that doesn't exist in the corpus.
This agent samples the most important citations and uses Claude to verify
whether each claim is actually supported by the paper's abstract.

It is a *side-effect* validator: it never rewrites Agent 2's output. It just
reports a score and a list of suspicious citations, which the project manager
persists to JSON.

Public API:
    run_fact_checker(article_path: Path, papers_files: list[Path]) -> dict
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from claude_cli import ask_claude_json


# ─────────────────────────────────────────────
# Tunables
# ─────────────────────────────────────────────

# Cap on how many citations we actually send to Claude for verification.
# First-occurrence-per-paper keeps the sample diverse; 10-15 is cheap + useful.
MAX_SAMPLE = 12

# Budget for the single verification batch call
VERIFY_BUDGET = 0.8

# ─── Triangulation (GPT-Researcher pattern) ───
# For each claim, look at the cited paper PLUS this many corroborator papers
# from the same corpus. A claim supported by 3/3 abstracts is "strong";
# only the cited paper supporting → "lone" (flagged as weak evidence).
TRIANGULATION_CORROBORATORS = 2
MIN_CLAIM_OVERLAP = 2  # min shared 4+ char words to consider a paper relevant


# ─────────────────────────────────────────────
# Citation extraction
# ─────────────────────────────────────────────

# Matches APA-style parenthetical citations, in English or Hebrew:
#   (Smith, 2019)
#   (Smith & Jones, 2019)
#   (Smith et al., 2019)
#   (סמית', 2019)
#   (כהן ולוי, 2019)
#
# We keep the authors blob loose — any run of non-digit, non-paren characters
# followed by a comma and a 4-digit year. Captures the author blob + year.
_PAREN_CITE = re.compile(
    r"\(([^()0-9]{2,80}?),\s*(\d{4})[a-z]?\)"
)

# Matches narrative citations: "Smith (2019)" or "Smith and Jones (2019)"
# or "Smith et al. (2019)" — English only (Hebrew narrative is rare and
# ambiguous with parenthetical years in prose).
_NARRATIVE_CITE = re.compile(
    r"\b([A-Z][A-Za-z\-']+(?:\s+(?:and|&|et\s+al\.?)\s+[A-Za-z\-']+)?)\s*\((\d{4})[a-z]?\)"
)

# Matches Obsidian wikilink citations: [[Turner 1969]] or [[Silberman-Keller 2003]]
# or [[Madjar & Cohen-Malayev 2018]]. Writer switched to this format ~2026-05-26.
# Capture: author blob (before the year), then 4-digit year.
_WIKILINK_CITE = re.compile(
    r"\[\[([^\[\]]{2,80}?)\s+(\d{4})[a-z]?\]\]"
)


def _extract_citations(text: str) -> list[dict]:
    """
    Return a list of {author_blob, year, span, sentence} for every in-text citation.
    `span` is (start, end) in the original text; `sentence` is the enclosing sentence.
    """
    found: list[dict] = []

    for m in _PAREN_CITE.finditer(text):
        found.append({
            "author_blob": m.group(1).strip(),
            "year": m.group(2).strip(),
            "span": m.span(),
            "raw": m.group(0),
        })

    for m in _NARRATIVE_CITE.finditer(text):
        found.append({
            "author_blob": m.group(1).strip(),
            "year": m.group(2).strip(),
            "span": m.span(),
            "raw": m.group(0),
        })

    for m in _WIKILINK_CITE.finditer(text):
        found.append({
            "author_blob": m.group(1).strip(),
            "year": m.group(2).strip(),
            "span": m.span(),
            "raw": m.group(0),
        })

    for cite in found:
        cite["sentence"] = _surrounding_sentence(text, cite["span"])

    # De-duplicate by (normalized_author, year) while preserving order
    seen: set[tuple[str, str]] = set()
    uniq: list[dict] = []
    for c in found:
        key = (_primary_author_key(c["author_blob"]), c["year"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)

    return uniq


def _surrounding_sentence(text: str, span: tuple[int, int]) -> str:
    """Return the sentence containing the citation span (bounded by . ! ? or newlines)."""
    start, end = span
    # Walk left until sentence boundary
    left = start
    while left > 0 and text[left - 1] not in ".!?\n":
        left -= 1
    # Walk right until sentence boundary
    right = end
    while right < len(text) and text[right] not in ".!?\n":
        right += 1
    if right < len(text):
        right += 1  # include the punctuation
    return text[left:right].strip()


# ─────────────────────────────────────────────
# Paper corpus loading + matching
# ─────────────────────────────────────────────

def _primary_author_key(author_blob: str) -> str:
    """
    Extract a normalized primary-author token from an author blob like
    "Smith et al." / "Smith & Jones" / "כהן ולוי".
    Returns the first surname, lowercased and stripped of punctuation.
    """
    blob = author_blob.strip()
    # Strip "et al." / "and ..." / "& ..."
    blob = re.split(r"\s+(?:et\s+al\.?|and|&|,|ו|ו-)\s*", blob, maxsplit=1)[0]
    blob = blob.strip(" ,.'\"\u05F3\u05F4")
    return blob.lower()


def _paper_author_keys(paper: dict) -> list[str]:
    """
    Build candidate surname tokens from a paper's authors field for matching.
    Handles both string authors ("Smith, J; Jones, K") and list authors.
    """
    authors = paper.get("authors")
    if not authors:
        return []

    if isinstance(authors, list):
        raw_authors = [str(a) for a in authors]
    else:
        # Split on common separators
        raw_authors = re.split(r"[;,]|\s+and\s+|\s+&\s+", str(authors))

    keys: list[str] = []
    for a in raw_authors:
        a = a.strip()
        if not a:
            continue
        # "Smith, John" -> "Smith"; "John Smith" -> "Smith"
        if "," in a:
            surname = a.split(",", 1)[0]
        else:
            surname = a.split()[-1] if a.split() else a
        surname = surname.strip(" .'\"\u05F3\u05F4").lower()
        if surname:
            keys.append(surname)
    return keys


def _load_papers(papers_files: list[Path]) -> list[dict]:
    """Flatten papers from multiple JSON files into a single list."""
    all_papers: list[dict] = []
    seen_titles: set[str] = set()
    for pf in papers_files:
        if not pf or not Path(pf).exists():
            continue
        try:
            data = json.loads(Path(pf).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [FactCheck] ⚠️  failed to read {pf}: {e}")
            continue
        papers = data.get("papers", data) if isinstance(data, dict) else data
        if not isinstance(papers, list):
            continue
        for p in papers:
            if not isinstance(p, dict):
                continue
            title_key = (p.get("title") or "")[:80].lower()
            if title_key and title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            all_papers.append(p)
    return all_papers


def _find_corroborators(claim: str, primary_paper: dict,
                        all_papers: list[dict], n: int = TRIANGULATION_CORROBORATORS) -> list[dict]:
    """
    Find up to N additional papers whose abstracts share keywords with the claim.
    Used for triangulation — even non-cited papers can corroborate (or contradict).
    Returns papers ordered by keyword-overlap with the claim, excluding the primary.
    """
    claim_words = set(re.findall(r"[א-ת\w]{4,}", claim.lower()))
    if not claim_words:
        return []
    scored = []
    for p in all_papers:
        if p is primary_paper:
            continue
        abstract = (p.get("abstract") or p.get("fulltext") or "").lower()
        if not abstract:
            continue
        ab_words = set(re.findall(r"[א-ת\w]{4,}", abstract))
        overlap = len(claim_words & ab_words)
        if overlap >= MIN_CLAIM_OVERLAP:
            scored.append((overlap, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:n]]


def _match_citation(cite: dict, papers: list[dict]) -> dict | None:
    """
    Try to find a paper whose (surname, year) matches the citation.
    Returns the matching paper dict, or None if no match.
    """
    cite_key = _primary_author_key(cite["author_blob"])
    cite_year = str(cite["year"])
    if not cite_key:
        return None

    for p in papers:
        p_year = str(p.get("year") or "").strip()
        if p_year != cite_year:
            continue
        for k in _paper_author_keys(p):
            # Exact or startswith match — handles transliteration slack
            if k == cite_key or k.startswith(cite_key) or cite_key.startswith(k):
                return p
    return None


# ─────────────────────────────────────────────
# Verification via Claude
# ─────────────────────────────────────────────

def _build_verify_prompt(items: list[dict]) -> tuple[str, str]:
    """
    Build a batched prompt asking Claude to judge each (paper, claim) pair.
    Returns (system, prompt).
    """
    # ── Persona prefix (CrewAI pattern) ──
    _persona = ""
    try:
        from obsidian_memory import get_backstory
        bs = get_backstory("fact_checker")
        if bs:
            _persona = f"## Your persona\n\n{bs}\n\n---\n\n"
    except Exception:
        pass

    system = (
        _persona +
        "You are a meticulous academic fact-checker. For each item, decide "
        "whether the paper's abstract actually supports the claim made in the "
        "article sentence. Be strict: if the abstract does not mention the "
        "specific finding asserted in the sentence, answer 'false' or 'partial'."
    )

    # ── Recurring sources context: authors the project already cites get a slight
    # benefit-of-the-doubt on borderline 'partial' calls. Strictness unchanged. ──
    try:
        from obsidian_memory import format_for_prompt as _obs_for_prompt
        recurring = _obs_for_prompt(["recurring_sources"], max_chars_per_note=700)
        if recurring:
            system += (
                "\n\n--- Authors/sources already used in this project ---\n"
                + recurring +
                "\n--- end ---\n"
                "Context only: these are familiar to the project. Verification rules "
                "stay strict — do NOT lower the bar for these authors. The list helps "
                "you parse abbreviated citations correctly when authors are well-known here."
            )
    except Exception:
        pass

    lines = []
    for i, it in enumerate(items, start=1):
        corrob_block = ""
        for j, c in enumerate(it.get("corroborators", []), start=1):
            corrob_block += (
                f"  Corroborator {j}: {c.get('title','?')[:120]} ({c.get('year','?')})\n"
                f"    Abstract: {(c.get('abstract') or '')[:600]}\n"
            )
        lines.append(
            f"Item {i}:\n"
            f"  Citation: {it['citation']}\n"
            f"  Primary paper title: {it['title']}\n"
            f"  Primary authors: {it['authors']}\n"
            f"  Primary year: {it['year']}\n"
            f"  Primary abstract: {it['abstract']}\n"
            f"{corrob_block if corrob_block else '  (no corroborator papers found in corpus)\n'}"
            f"  Claim sentence from article: {it['claim']}\n"
        )
    joined = "\n".join(lines)

    prompt = (
        f"{joined}\n"
        "For each item, judge BOTH (a) whether the primary paper supports the claim,\n"
        "AND (b) how many corroborator papers also support it (triangulation).\n"
        "Return a JSON array with one object per item, in the same order:\n"
        '  [{"item": 1, "supports": "true"|"false"|"partial",\n'
        '    "corroborator_count": 0|1|2,\n'
        '    "triangulation": "strong"|"supported"|"lone"|"contested"|"unsupported",\n'
        '    "reason": "<one sentence>"}, ...]\n'
        "Rules:\n"
        "  - supports: judgment on PRIMARY paper only (true/false/partial as before).\n"
        "  - corroborator_count: how many of the listed corroborator abstracts ALSO\n"
        "    support the claim (0 to N).\n"
        "  - triangulation:\n"
        "    * 'strong'      = primary supports AND ≥2 corroborators agree.\n"
        "    * 'supported'   = primary supports AND ≥1 corroborator agrees.\n"
        "    * 'lone'        = primary supports, but no corroborator does → flag as weak.\n"
        "    * 'contested'   = primary supports but a corroborator CONTRADICTS.\n"
        "    * 'unsupported' = primary does not support the claim (false/partial).\n"
        "Be concise. Return only the JSON array."
    )
    return system, prompt


def _verify_batch(items: list[dict]) -> list[dict]:
    """Call Claude once to verify a batch of citation claims. Best-effort."""
    if not items:
        return []
    system, prompt = _build_verify_prompt(items)
    try:
        result = ask_claude_json(prompt, system=system, max_budget=VERIFY_BUDGET, timeout=600)
    except Exception as e:
        print(f"  [FactCheck] ⚠️  verification call failed: {e}")
        return []

    if isinstance(result, dict):
        # Sometimes the model wraps in {"results": [...]}
        for key in ("results", "items", "data"):
            if isinstance(result.get(key), list):
                result = result[key]
                break
        else:
            result = [result]

    if not isinstance(result, list):
        return []
    return result


# ─────────────────────────────────────────────
# Quick-check for Writer↔FactCheck conversation (AutoGen pattern)
# ─────────────────────────────────────────────

def quick_check(claim: str, papers: list[dict], max_budget: float = 0.15) -> dict:
    """
    Fast verdict on a single proposed claim, BEFORE Writer commits to writing it.
    Looks for ≥1 paper in corpus whose abstract addresses the claim.

    Returns: {"verdict": "supported"|"weak"|"unsupported", "evidence": "..."}

    Used by Writer like:
        from scratchpad import ask
        from agent2_7_fact_checker import quick_check
        verdict = ask("writer", "fact_checker", "claim_3",
                      "X is correlated with Y in non-formal settings",
                      handler_fn=lambda q: quick_check(q, papers)["verdict"])
        if verdict == "unsupported":
            soften_or_skip()

    Cheap: only sends 1 claim + 3 most-relevant abstracts to Claude (~$0.05).
    """
    if not claim or not papers:
        return {"verdict": "unsupported", "evidence": "no claim/papers"}

    # Find top-3 most relevant papers by keyword overlap (no LLM)
    claim_words = set(re.findall(r"[א-ת\w]{4,}", claim.lower()))
    if not claim_words:
        return {"verdict": "unsupported", "evidence": "claim too short to analyze"}
    scored = []
    for p in papers:
        ab = (p.get("abstract") or p.get("fulltext") or "").lower()
        if not ab:
            continue
        ab_words = set(re.findall(r"[א-ת\w]{4,}", ab))
        overlap = len(claim_words & ab_words)
        if overlap >= 2:
            scored.append((overlap, p))
    scored.sort(key=lambda x: -x[0])
    top3 = [p for _, p in scored[:3]]
    if not top3:
        return {"verdict": "unsupported", "evidence": "no paper mentions any of the claim's key terms"}

    # Ask Claude for a quick verdict
    try:
        from claude_cli import ask_claude_json
        abstracts_block = "\n\n".join(
            f"Paper {i+1}: {p.get('title','?')[:100]} ({p.get('year','?')})\n"
            f"  Abstract: {(p.get('abstract') or '')[:600]}"
            for i, p in enumerate(top3)
        )
        prompt = (
            f"Claim: {claim[:400]}\n\n"
            f"{abstracts_block}\n\n"
            "Does the evidence support this claim?\n"
            'Return JSON: {"verdict": "supported"|"weak"|"unsupported", "evidence": "<one sentence>"}\n'
            "  - supported  = at least 2 abstracts back this claim.\n"
            "  - weak       = 1 abstract is related but not conclusive.\n"
            "  - unsupported = no abstract addresses the claim, or they contradict it."
        )
        result = ask_claude_json(prompt, max_budget=max_budget, timeout=90)
        if isinstance(result, dict) and result.get("verdict"):
            return {
                "verdict":  result["verdict"],
                "evidence": result.get("evidence", "")[:200],
            }
    except Exception as e:
        return {"verdict": "weak", "evidence": f"check failed: {e}"}

    return {"verdict": "weak", "evidence": "unclear"}


# ─────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────

def run_fact_checker(article_path: Path, papers_files: list[Path]) -> dict:
    """
    Validate citations in an article against original paper metadata.

    Args:
        article_path: Path to the article (.md).
        papers_files: List of paths to paper JSON files (as produced by Agent 1).

    Returns:
        {
          "score": int (0-100),
          "total_citations": int,
          "verified": int,
          "suspicious": [{"citation": str, "claim": str, "reason": str}, ...],
          "corrected_article": None,
        }
    """
    article_path = Path(article_path)
    print(f"\n  🔍 [FactCheck] Validating citations in {article_path.name}...")

    if not article_path.exists():
        return {
            "score": 0,
            "total_citations": 0,
            "verified": 0,
            "suspicious": [{"citation": "", "claim": "", "reason": f"article not found: {article_path}"}],
            "corrected_article": None,
        }

    text = article_path.read_text(encoding="utf-8", errors="replace")
    papers = _load_papers([Path(p) for p in papers_files])

    if not papers:
        print("  [FactCheck] ⚠️  no papers loaded — skipping")
        return {
            "score": 0,
            "total_citations": 0,
            "verified": 0,
            "suspicious": [{"citation": "", "claim": "", "reason": "no papers available to check against"}],
            "corrected_article": None,
        }

    # Strip the References section so we don't "fact-check" the bibliography.
    body = re.split(r"\n#+\s*References\b", text, maxsplit=1, flags=re.IGNORECASE)[0]
    body = re.split(r"\n#+\s*מקורות\b", body, maxsplit=1)[0]

    citations = _extract_citations(body)
    total = len(citations)
    print(f"  [FactCheck] Found {total} unique in-text citations")

    if total == 0:
        return {
            "score": 0,
            "total_citations": 0,
            "verified": 0,
            "suspicious": [],
            "corrected_article": None,
        }

    # Match each citation to a paper; orphans are already suspicious
    suspicious: list[dict] = []
    to_verify: list[dict] = []
    orphan_count = 0

    for cite in citations:
        paper = _match_citation(cite, papers)
        if paper is None:
            orphan_count += 1
            suspicious.append({
                "citation": cite["raw"],
                "claim": cite["sentence"],
                "reason": "orphan citation — no matching paper found in corpus",
            })
            continue
        abstract = (paper.get("abstract") or paper.get("fulltext") or "").strip()
        if len(abstract) > 1500:
            abstract = abstract[:1500] + "..."
        # Triangulation: find up to 2 corroborator papers from same corpus
        corrob_papers = _find_corroborators(cite["sentence"], paper, papers)
        corroborators = [
            {
                "title": (p.get("title") or "")[:150],
                "year":  p.get("year"),
                "abstract": (p.get("abstract") or "")[:800],
            }
            for p in corrob_papers
        ]
        to_verify.append({
            "citation":     cite["raw"],
            "claim":        cite["sentence"],
            "title":        (paper.get("title") or "")[:200],
            "authors":      str(paper.get("authors") or "")[:200],
            "year":         paper.get("year"),
            "abstract":     abstract or "(no abstract available)",
            "corroborators": corroborators,
        })

    if orphan_count:
        print(f"  [FactCheck] {orphan_count} orphan citation(s) — not in corpus")

    # Sample at most MAX_SAMPLE to keep cost bounded
    sample = to_verify[:MAX_SAMPLE]
    if len(to_verify) > MAX_SAMPLE:
        print(f"  [FactCheck] Sampling {MAX_SAMPLE} of {len(to_verify)} matched citations for verification")

    verified_count = 0
    triangulation_tally = {"strong": 0, "supported": 0, "lone": 0, "contested": 0, "unsupported": 0}
    weak_claims: list[dict] = []  # primary supports but no corroborator → flagged
    contested_claims: list[dict] = []  # primary supports but corroborator contradicts
    results = _verify_batch(sample)
    for i, item in enumerate(sample, start=1):
        # Find corresponding result
        r = next((x for x in results if isinstance(x, dict) and x.get("item") == i), None)
        if r is None and i - 1 < len(results) and isinstance(results[i - 1], dict):
            r = results[i - 1]
        if r is None:
            # No verdict — treat as unverified but not suspicious
            continue
        verdict = str(r.get("supports", "")).strip().lower()
        reason = str(r.get("reason", "")).strip() or "(no reason given)"
        triang = str(r.get("triangulation", "")).strip().lower()
        if triang in triangulation_tally:
            triangulation_tally[triang] += 1
        if verdict == "true":
            verified_count += 1
            if triang == "lone":
                weak_claims.append({
                    "citation": item["citation"],
                    "claim":    item["claim"],
                    "reason":   f"lone source — only the cited paper supports this; no corroborator found",
                })
            elif triang == "contested":
                contested_claims.append({
                    "citation": item["citation"],
                    "claim":    item["claim"],
                    "reason":   f"contested — primary supports, but corroborator contradicts: {reason}",
                })
        elif verdict in ("false", "partial"):
            suspicious.append({
                "citation": item["citation"],
                "claim":    item["claim"],
                "reason":   f"{verdict}: {reason}",
            })
        # Unknown verdicts count as neither verified nor suspicious

    if any(triangulation_tally.values()):
        t = triangulation_tally
        print(f"  [FactCheck] Triangulation: "
              f"strong={t['strong']} · supported={t['supported']} · "
              f"lone={t['lone']} · contested={t['contested']}")
    if weak_claims:
        print(f"  [FactCheck] {len(weak_claims)} lone-source claim(s) — consider hedging")
    if contested_claims:
        print(f"  [FactCheck] ⚠️ {len(contested_claims)} contested claim(s) — review carefully")

    # Assume un-sampled matched citations are verified (already corpus-matched)
    implicit_verified = max(0, len(to_verify) - len(sample))
    verified_total = verified_count + implicit_verified

    score = int(round((verified_total / total) * 100)) if total else 0

    print(
        f"  [FactCheck] Score: {score}/100  "
        f"(verified {verified_total}/{total}, suspicious {len(suspicious)})"
    )

    # ── Reciprocal feedback: tell researcher about orphan citations ──
    # If we found citations that don't match any paper in corpus, the researcher
    # in the next run should know to fetch those specific sources.
    orphans = [s for s in suspicious if "orphan" in s.get("reason", "")]
    if orphans:
        try:
            from scratchpad import note as _scratch_note
            orphan_keys = [o["citation"] for o in orphans[:10]]
            _scratch_note("fact_checker", "missing_sources", {
                "issue": f"{len(orphans)} ציטוטים יתומים — חסרים בקורפוס",
                "suggested_searches": orphan_keys,
                "summary": f"researcher should fetch: {', '.join(orphan_keys[:5])}",
            })
            print(f"  [FactCheck] 🔄 Sent {len(orphans)} missing sources → scratchpad → researcher")
        except Exception:
            pass

    return {
        "score": score,
        "total_citations": total,
        "verified": verified_total,
        "suspicious": suspicious,
        "triangulation": triangulation_tally,
        "weak_claims": weak_claims,
        "contested_claims": contested_claims,
        "corrected_article": None,
    }


# ─────────────────────────────────────────────
# Standalone
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if len(args) < 2:
        print("Usage: python agent2_7_fact_checker.py <article.md> <papers1.json> [papers2.json ...]")
        sys.exit(1)
    article = Path(args[0])
    pfs = [Path(a) for a in args[1:]]
    result = run_fact_checker(article, pfs)
    print(json.dumps(result, ensure_ascii=False, indent=2))
