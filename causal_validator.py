"""
Causal Chain Validator for Moki pipeline.

Detects when posts/articles incorrectly state causation while only correlation
exists, or when causal claims aren't supported by methodology.

Pure heuristic — no LLM calls. Cheap to run on every post.
"""

from __future__ import annotations

import re
from typing import Optional


# ─────────────────────────────────────────────────────────────────────
# Pattern catalogues
# ─────────────────────────────────────────────────────────────────────

# Causal verbs / phrases — Hebrew
HE_CAUSAL_PATTERNS = [
    r"גורם\s+ל",
    r"גורמת\s+ל",
    r"גורמים\s+ל",
    r"מוביל\s+ל",
    r"מובילה\s+ל",
    r"מובילים\s+ל",
    r"יוצר(?:\s+|ת\s+|ים\s+)",
    r"משפיע\s+על",
    r"משפיעה\s+על",
    r"משפיעים\s+על",
    r"מחזק(?:\s+|ת\s+|ים\s+)",
    r"מחליש(?:\s+|ה\s+|ים\s+)",
    r"הסיבה\s+ל",
    r"בגלל\s+ש?",
    r"כתוצאה\s+מ",
    r"התוצאה\s+של",
    r"בעקבות\s+",
    r"הופך\s+ל",
    r"הופכת\s+ל",
]

# Causal verbs / phrases — English
EN_CAUSAL_PATTERNS = [
    r"\bcauses?\b",
    r"\bcaused\b",
    r"\bcausing\b",
    r"\bleads?\s+to\b",
    r"\bled\s+to\b",
    r"\bleading\s+to\b",
    r"\bproduces?\b",
    r"\bproduced\b",
    r"\bresults?\s+in\b",
    r"\bresulted\s+in\b",
    r"\bbecause\s+of\b",
    r"\bdue\s+to\b",
    r"\bas\s+a\s+result\s+of\b",
    r"\bmakes?\s+\w+\s+(?:more|less)\b",
    r"\btriggers?\b",
    r"\btriggered\b",
    r"\bdrives?\b",
    r"\bdriven\s+by\b",
]

# "אם X אז Y" — Hebrew strong implication
HE_IMPLICATION = [
    r"אם\s+[^.,;\n]{1,40}\s+אז\s+",
    r"כש[^.,;\n]{1,40}\s+ואז\s+",
]

EN_IMPLICATION = [
    r"\bif\s+[^.,;\n]{1,40}\s+then\s+",
]

ALL_PATTERNS = (
    [("causal_verb_he", p) for p in HE_CAUSAL_PATTERNS]
    + [("causal_verb_en", p) for p in EN_CAUSAL_PATTERNS]
    + [("implication_he", p) for p in HE_IMPLICATION]
    + [("implication_en", p) for p in EN_IMPLICATION]
)

# Methodology hints in surrounding text that justify a causal claim
METHODOLOGY_HINTS = [
    "longitudinal",
    "long-term",
    "RCT",
    "randomized",
    "randomised",
    "control group",
    "controlled trial",
    "experiment",
    "experimental",
    "intervention",
    "אורך",
    "אורכי",
    "ניסוי",
    "ניסויי",
    "בקרה",
    "התערבות",
    "אקראי",
]

# Methodology phrases that REFUTE causation (only correlation possible)
WEAK_METHODOLOGY = {
    "cross-sectional",
    "qualitative",
    "survey",
    "observational",
    "correlational",
    "case study",
    "חתך",
    "סקר",
    "איכותני",
    "מתאמי",
    "תצפיתי",
}

# Replacement suggestions
SUGGESTIONS_HE = {
    "גורם ל": "קשור ל / עשוי להוביל ל",
    "גורמת ל": "קשורה ל / עשויה להוביל ל",
    "גורמים ל": "קשורים ל / עשויים להוביל ל",
    "מוביל ל": "קשור ל / נמצא בקשר עם",
    "מובילה ל": "קשורה ל / נמצאת בקשר עם",
    "מובילים ל": "קשורים ל / נמצאים בקשר עם",
    "מחזק": "מחקרים מציעים קשר חיובי בין",
    "מחזקת": "מחקרים מציעים קשר חיובי בין",
    "מחזקים": "מחקרים מציעים קשר חיובי בין",
    "משפיע על": "מתואם עם / נמצא בקשר עם",
    "משפיעה על": "מתואמת עם / נמצאת בקשר עם",
    "משפיעים על": "מתואמים עם / נמצאים בקשר עם",
    "הסיבה ל": "אחד הגורמים האפשריים ל",
    "כתוצאה מ": "במקביל ל / בהקשר של",
    "התוצאה של": "מה שמלווה את",
    "בעקבות": "בהקשר של",
}

SUGGESTIONS_EN = {
    "causes": "may contribute to / is associated with",
    "caused": "was associated with",
    "causing": "associated with",
    "leads to": "is associated with",
    "led to": "was associated with",
    "leading to": "associated with",
    "produces": "is linked to",
    "produced": "was linked to",
    "results in": "is associated with",
    "resulted in": "was associated with",
    "because of": "in the context of",
    "due to": "in association with",
    "as a result of": "alongside",
    "triggers": "is associated with",
    "triggered": "was associated with",
    "drives": "is correlated with",
    "driven by": "correlated with",
}


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _surrounding_window(text: str, start: int, end: int, radius: int = 50) -> str:
    """Return ~radius chars on each side of a match."""
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return text[lo:hi]


def _suggestion_for(claim: str) -> str:
    """Pick the best replacement suggestion for a detected claim phrase."""
    norm = claim.strip().lower()

    # Exact key match in English dict
    for key, val in SUGGESTIONS_EN.items():
        if key in norm:
            return f"החלף '{claim.strip()}' ב-'{val}'"

    # Hebrew — match by substring (Hebrew is case-insensitive anyway)
    for key, val in SUGGESTIONS_HE.items():
        if key in claim:
            return f"החלף '{claim.strip()}' ב-'{val}'"

    # Generic fallback
    return "שקול ניסוח עדין יותר: 'קשור ל' / 'עשוי להוביל ל' / 'מתואם עם'"


def _paper_supports_causation(paper: dict) -> Optional[bool]:
    """
    Decide whether a paper's methodology supports a causal claim.

    Returns:
        True  → methodology supports causation
        False → methodology does NOT support causation (correlation only)
        None  → cannot decide
    """
    if not paper:
        return None

    method = str(paper.get("method", "")).lower()
    duration = paper.get("study_duration")  # may be int (years), str, or None
    text_blob = " ".join(
        str(paper.get(k, "")) for k in ("method", "design", "notes", "abstract")
    ).lower()

    # Strong support
    if "longitudinal" in method or "longitudinal" in text_blob:
        return True
    if "experimental" in method or "rct" in method or "randomized" in method:
        return True
    if "control group" in text_blob or "controlled trial" in text_blob:
        return True

    # Duration > 1 year supports causation
    if isinstance(duration, (int, float)) and duration > 1:
        return True
    if isinstance(duration, str):
        m = re.search(r"(\d+(?:\.\d+)?)", duration)
        if m:
            try:
                val = float(m.group(1))
                # Heuristic: if "month" mentioned and >12, OK; else compare as years
                if "month" in duration.lower() or "חודש" in duration:
                    if val > 12:
                        return True
                else:
                    if val > 1:
                        return True
            except ValueError:
                pass

    # Weak methodology — explicit
    weak_hits = [w for w in WEAK_METHODOLOGY if w in method or w in text_blob]
    if weak_hits:
        return False

    # No study_duration AND no strong method → cannot support causation
    if not duration and not method:
        return None
    if not duration and method and "longitudinal" not in method and "experiment" not in method:
        return False

    return None


def _find_supporting_paper(
    claim_context: str, papers_metadata: list[dict]
) -> Optional[dict]:
    """
    Find a paper from metadata whose title/keyword appears in the claim context.
    """
    if not papers_metadata:
        return None
    ctx_lower = claim_context.lower()
    best = None
    for p in papers_metadata:
        title = str(p.get("title", "")).lower()
        author = str(p.get("author", "")).lower()
        # Match by author surname or significant title token
        if author and author.split()[0] and author.split()[0] in ctx_lower:
            return p
        # Title token of length >= 5 appearing in context
        for tok in title.split():
            if len(tok) >= 5 and tok in ctx_lower:
                best = p
                break
    return best


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────


def validate_causal_claims(
    text: str, papers_metadata: Optional[list[dict]] = None
) -> dict:
    """
    Scan text for causal claims and check if methodology supports them.

    Returns:
        {
            "claims_found": int,
            "weak_claims": [
                {"claim": str, "issue": str, "suggestion": str}
            ],
            "score": int  # 0-100, higher = better epistemics
        }
    """
    if not text or not isinstance(text, str):
        return {"claims_found": 0, "weak_claims": [], "score": 100}

    weak_claims: list[dict] = []
    strong_unsupported = 0
    seen_spans: set[tuple[int, int]] = set()
    claims_found = 0

    for kind, pattern in ALL_PATTERNS:
        try:
            regex = re.compile(pattern, flags=re.IGNORECASE)
        except re.error:
            continue
        for m in regex.finditer(text):
            span = (m.start(), m.end())
            # Avoid double-counting overlapping spans
            if any(
                s <= span[0] < e or s < span[1] <= e for (s, e) in seen_spans
            ):
                continue
            seen_spans.add(span)
            claims_found += 1

            claim_text = m.group(0)
            window = _surrounding_window(text, m.start(), m.end(), radius=50)
            window_lower = window.lower()

            # Check methodology hints in window
            has_method_hint = any(
                h.lower() in window_lower for h in METHODOLOGY_HINTS
            )

            # Check papers_metadata if provided
            paper_verdict: Optional[bool] = None
            if papers_metadata:
                paper = _find_supporting_paper(window, papers_metadata)
                if paper is not None:
                    paper_verdict = _paper_supports_causation(paper)

            issue: Optional[str] = None
            is_strong = kind.startswith("implication")

            if paper_verdict is True:
                # Methodology supports causation — claim is fine
                continue
            elif paper_verdict is False:
                issue = (
                    "המאמר התומך הוא חתך/איכותני/תצפיתי — תומך בקורלציה בלבד, "
                    "לא בסיבתיות."
                )
                strong_unsupported += 1
            else:
                # No metadata or no matching paper — fall back to heuristic
                if has_method_hint:
                    # Surrounding text mentions longitudinal/RCT/etc — OK
                    continue
                else:
                    issue = (
                        "טענה סיבתית ללא רמז למתודולוגיה תומכת "
                        "(longitudinal/RCT/ניסוי/בקרה) בסביבה הקרובה."
                    )

            weak_claims.append(
                {
                    "claim": claim_text,
                    "issue": issue,
                    "suggestion": _suggestion_for(claim_text),
                }
            )

            if is_strong and paper_verdict is not True:
                # Strong implication ("אם X אז Y" / "if X then Y") without
                # explicit support — extra penalty
                strong_unsupported += 1

    # Score: start at 100, -10 per weak claim, -15 per strong unsupported, floor 0
    score = 100 - 10 * len(weak_claims) - 15 * strong_unsupported
    score = max(0, min(100, score))

    # ── Reciprocal feedback: tell writer about weak causal claims ──
    # If we found 2+ weak causal claims, the writer in next run should know
    # to use hedged language (קשור ל / עשוי / may contribute) by default.
    if len(weak_claims) >= 2:
        try:
            from scratchpad import note as _scratch_note
            samples = [c.get("claim", "")[:60] for c in weak_claims[:5]]
            _scratch_note("causal_validator", "weak_claims_warning", {
                "issue": f"{len(weak_claims)} טענות סיבתיות חזקות מדי",
                "score": score,
                "examples": samples,
                "summary": "השתמש ב'קשור ל' / 'עשוי להוביל ל' אלא אם יש longitudinal/RCT",
            })
        except Exception:
            pass

    return {
        "claims_found": claims_found,
        "weak_claims": weak_claims,
        "score": score,
    }


def soften_causal_language(text: str) -> str:
    """
    Suggest replacements for overly causal language.

    Returns the text with inline annotations of the form:
        ...גורם ל[suggest: קשור ל / עשוי להוביל ל]...

    Original text is preserved; annotations are added in brackets so the
    author can review and accept manually.
    """
    if not text or not isinstance(text, str):
        return text or ""

    result = text
    # Process longer keys first to avoid partial overlaps
    he_keys = sorted(SUGGESTIONS_HE.keys(), key=len, reverse=True)
    en_keys = sorted(SUGGESTIONS_EN.keys(), key=len, reverse=True)

    annotated_spans: list[tuple[int, int]] = []

    def _already_annotated(start: int, end: int) -> bool:
        return any(s <= start < e or s < end <= e for (s, e) in annotated_spans)

    # Hebrew — direct substring replacement (Hebrew has no case)
    for key in he_keys:
        suggestion = SUGGESTIONS_HE[key]
        idx = 0
        new_result = []
        last = 0
        for match in re.finditer(re.escape(key), result):
            s, e = match.start(), match.end()
            if _already_annotated(s, e):
                continue
            new_result.append(result[last:e])
            new_result.append(f"[suggest: {suggestion}]")
            annotated_spans.append((s, e + len(f"[suggest: {suggestion}]")))
            last = e
            idx += 1
        if idx:
            new_result.append(result[last:])
            result = "".join(new_result)
            # spans are now stale because indices shifted; rebuild
            annotated_spans = []

    # English — case-insensitive word-boundary
    for key in en_keys:
        suggestion = SUGGESTIONS_EN[key]
        pattern = re.compile(r"\b" + re.escape(key) + r"\b", flags=re.IGNORECASE)
        new_result = []
        last = 0
        changed = False
        for match in pattern.finditer(result):
            s, e = match.start(), match.end()
            new_result.append(result[last:e])
            new_result.append(f"[suggest: {suggestion}]")
            last = e
            changed = True
        if changed:
            new_result.append(result[last:])
            result = "".join(new_result)

    return result


# ─────────────────────────────────────────────────────────────────────
# CLI smoke test
# ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sample = (
        "מחקר חדש מראה שלמידה חברתית גורמת לשיפור משמעותי בביטחון העצמי. "
        "Smartphone use leads to anxiety in teens. "
        "במחקר longitudinal של 5 שנים, חינוך בלתי פורמלי משפיע על תחושת השייכות. "
        "אם נחשוף ילדים לטבע אז הם יהיו מאושרים יותר."
    )
    result = validate_causal_claims(sample)
    print(f"Claims found: {result['claims_found']}")
    print(f"Score: {result['score']}/100")
    print("Weak claims:")
    for c in result["weak_claims"]:
        print(f"  - '{c['claim']}': {c['issue']}")
        print(f"    → {c['suggestion']}")
    print("\nSoftened:")
    print(soften_causal_language(sample))
