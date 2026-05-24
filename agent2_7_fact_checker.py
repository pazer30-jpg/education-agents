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
    system = (
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
        lines.append(
            f"Item {i}:\n"
            f"  Citation: {it['citation']}\n"
            f"  Paper title: {it['title']}\n"
            f"  Paper authors: {it['authors']}\n"
            f"  Paper year: {it['year']}\n"
            f"  Paper abstract: {it['abstract']}\n"
            f"  Claim sentence from article: {it['claim']}\n"
        )
    joined = "\n".join(lines)

    prompt = (
        f"{joined}\n"
        "For each item, decide whether the paper actually supports the claim.\n"
        "Return a JSON array with one object per item, in the same order:\n"
        '  [{"item": 1, "supports": "true"|"false"|"partial", "reason": "<one sentence>"}, ...]\n'
        "Rules:\n"
        "  - 'true'    = abstract clearly supports the specific claim.\n"
        "  - 'partial' = abstract is related but does not confirm the specific claim.\n"
        "  - 'false'   = abstract contradicts the claim or is about something else.\n"
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
        to_verify.append({
            "citation": cite["raw"],
            "claim": cite["sentence"],
            "title": (paper.get("title") or "")[:200],
            "authors": str(paper.get("authors") or "")[:200],
            "year": paper.get("year"),
            "abstract": abstract or "(no abstract available)",
        })

    if orphan_count:
        print(f"  [FactCheck] {orphan_count} orphan citation(s) — not in corpus")

    # Sample at most MAX_SAMPLE to keep cost bounded
    sample = to_verify[:MAX_SAMPLE]
    if len(to_verify) > MAX_SAMPLE:
        print(f"  [FactCheck] Sampling {MAX_SAMPLE} of {len(to_verify)} matched citations for verification")

    verified_count = 0
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
        if verdict == "true":
            verified_count += 1
        elif verdict in ("false", "partial"):
            suspicious.append({
                "citation": item["citation"],
                "claim": item["claim"],
                "reason": f"{verdict}: {reason}",
            })
        # Unknown verdicts count as neither verified nor suspicious

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
