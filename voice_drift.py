"""
voice_drift.py — Detect stylistic drift / repetition in Moki's published posts.

Pure-Python (no LLM, no external libs). Scans the last N LinkedIn-ready posts
and computes a diversity score, surfaces specific patterns (repeated openers,
overused phrases, monotone structure, low opening-pattern variety), and emits
human-readable recommendations.

Public API:
    analyze_voice_drift(top_n: int = 30) -> dict
    format_report(result: dict) -> str
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

from config import LINKEDIN_DIR


# ─────────────────────────────────────────────
# Tokenization helpers (Hebrew-aware)
# ─────────────────────────────────────────────

# Split on whitespace + Hebrew/Latin punctuation. Hebrew letters (U+0590-U+05FF)
# stay inside tokens.
_TOKEN_SPLIT_RE = re.compile(
    r"[\s\.,;:!\?\(\)\[\]\{\}\"'׳״\-—–…\*/\\<>«»\|\+=#]+"
)

# Sentence boundaries — Hebrew text uses the same Western punctuation in
# practice (. ! ? plus newlines). We treat blank lines as soft boundaries too.
_SENT_SPLIT_RE = re.compile(r"(?<=[\.\!\?])\s+|\n{2,}")

# Stopwords — small Hebrew + English set; just enough to avoid obvious junk
# n-grams like "של" / "את" / "the of and".
_STOPWORDS: set[str] = {
    # Hebrew function words
    "של", "את", "על", "אל", "אם", "כי", "גם", "לא", "לו", "לה", "לי",
    "מה", "מי", "זה", "זו", "זאת", "אני", "אתה", "את", "הוא", "היא",
    "אנחנו", "אתם", "הם", "הן", "יש", "אין", "היה", "הייתה", "הייתי",
    "ב", "ל", "מ", "ה", "ו", "כ", "ש", "מן", "אך", "אבל", "או", "וגם",
    "כך", "כן", "רק", "עם", "בלי", "בין", "אצל", "אחר", "אחרי", "לפני",
    "כאן", "שם", "פה", "מאוד", "יותר", "פחות", "כמו", "כדי", "אז", "כש",
    # English function words
    "the", "a", "an", "of", "to", "in", "on", "at", "for", "and", "or",
    "but", "if", "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "it", "its", "as", "by", "from",
    "with", "about", "into", "than", "then", "so", "not", "no", "yes",
    "i", "you", "he", "she", "we", "they", "them", "his", "her", "our",
    "their", "my", "your",
}

# Hashtag / metadata line markers — these should not count as "content" lines.
_META_PREFIXES: tuple[str, ...] = (
    "#", "•", "-", "*",
    "מקורות", "מקורות:", "מקור:", "מקור",
    "📚",
)


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase-ish tokens (Hebrew has no case)."""
    parts = _TOKEN_SPLIT_RE.split(text.strip())
    return [p for p in parts if p]


def _sentences(text: str) -> list[str]:
    """Return non-empty sentences, in order."""
    raw = _SENT_SPLIT_RE.split(text.strip())
    out: list[str] = []
    for s in raw:
        s = s.strip()
        if s:
            out.append(s)
    return out


def _strip_meta(text: str) -> str:
    """Remove hashtag tail, source list, and bullet metadata from the body."""
    lines = text.splitlines()
    kept: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            kept.append(ln)
            continue
        if s.startswith(_META_PREFIXES):
            # Once we hit a meta block, stop including subsequent lines.
            # (Sources and hashtags are always at the bottom.)
            break
        kept.append(ln)
    return "\n".join(kept).strip()


def _ngrams(tokens: list[str], n: int) -> list[tuple[str, ...]]:
    if len(tokens) < n:
        return []
    return [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def _ngram_has_content(ng: tuple[str, ...]) -> bool:
    """An n-gram is 'content-bearing' if at least one token isn't a stopword."""
    return any(tok not in _STOPWORDS for tok in ng)


# ─────────────────────────────────────────────
# Opening-pattern categorization
# ─────────────────────────────────────────────

_QUESTION_STARTERS = {"האם", "מה", "מי", "מתי", "איך", "כיצד", "למה", "מדוע",
                      "האמנם", "what", "why", "how", "when", "who", "where"}
_MEMORY_STARTERS = ("אני זוכר", "אני זוכרת", "זכור לי", "ביום ש", "פעם",
                    "i remember", "once,")
_STATEMENT_STARTERS = {"יש", "אין", "כל", "כשאני", "תמיד", "לפעמים", "כשאתה",
                       "there", "every", "always", "sometimes"}
_YEAR_RE = re.compile(r"^(19|20)\d{2}\b")
_NUMBER_RE = re.compile(r"^\d+\b")


def _opening_category(first_sentence: str) -> str:
    """Classify the *kind* of opening line."""
    s = first_sentence.strip()
    if not s:
        return "empty"

    # Remove a leading "ב" preposition for year detection ("ביולי 2014")
    bare = s.lstrip("בכלמשהו ").strip()
    if _YEAR_RE.search(s) or _YEAR_RE.search(bare):
        return "year"

    first_tok = _tokenize(s)[:1]
    if not first_tok:
        return "other"
    tok = first_tok[0]

    if _NUMBER_RE.match(tok):
        return "number"

    # Question — either ends with ? or starts with an interrogative.
    if s.rstrip().endswith("?") or tok in _QUESTION_STARTERS:
        return "question"

    low = s.lower()
    if any(low.startswith(p) for p in _MEMORY_STARTERS):
        return "memory"

    if tok in _STATEMENT_STARTERS:
        return "statement"

    return "other"


# ─────────────────────────────────────────────
# Per-post feature extraction
# ─────────────────────────────────────────────

def _extract_features(text: str) -> dict:
    body = _strip_meta(text)
    sents = _sentences(body)
    first_sent = sents[0] if sents else ""
    last_sent = sents[-1] if sents else ""

    tokens = _tokenize(body)
    first_5 = tuple(_tokenize(first_sent)[:5])

    trigrams = [ng for ng in _ngrams(tokens, 3) if _ngram_has_content(ng)]

    # Structural fingerprint per sentence: 'Q' if sentence ends with '?',
    # else 'S'. Then collapse to a coarse pattern.
    seq = "".join("Q" if s.rstrip().endswith("?") else "S" for s in sents)
    # Coarse pattern: question→story→question — needs a Q early, an S in
    # the middle (story), and a Q at the end.
    has_qsq = (
        len(seq) >= 3
        and "Q" in seq[: max(1, len(seq) // 2)]
        and "S" in seq[1:-1]
        and seq.endswith("Q")
    )

    return {
        "first_sentence": first_sent,
        "last_sentence": last_sent,
        "first_5": first_5,
        "trigrams": trigrams,
        "length": len(body),
        "opening_category": _opening_category(first_sent),
        "structure_seq": seq,
        "has_question_story_question": has_qsq,
    }


# ─────────────────────────────────────────────
# File discovery
# ─────────────────────────────────────────────

def _gather_posts(top_n: int) -> list[Path]:
    """Last `top_n` ready posts by mtime, excluding .bak files."""
    if not LINKEDIN_DIR.exists():
        return []
    candidates = [
        p for p in LINKEDIN_DIR.glob("*_ready*.txt")
        if not p.name.endswith(".bak") and p.is_file()
    ]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[:top_n]


# ─────────────────────────────────────────────
# Main analyzer
# ─────────────────────────────────────────────

def analyze_voice_drift(top_n: int = 30) -> dict:
    """Analyze recent posts for stylistic drift / repetition."""
    files = _gather_posts(top_n)
    if not files:
        return {
            "samples": 0,
            "diversity_score": 0,
            "patterns": [],
            "verdict": "stuck",
            "recommendations": [
                f"לא נמצאו פוסטים תחת {LINKEDIN_DIR}",
            ],
        }

    feats: list[dict] = []
    for p in files:
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        f = _extract_features(txt)
        f["_path"] = str(p)
        feats.append(f)

    n = len(feats)

    # ── Pattern 1: opening_repetition (same first 5 words) ──
    opening_counter: Counter[tuple[str, ...]] = Counter(
        f["first_5"] for f in feats if f["first_5"]
    )
    repeated_openings = [
        (op, c) for op, c in opening_counter.items() if c >= 3
    ]
    repeated_openings.sort(key=lambda x: -x[1])

    opening_patterns: list[dict] = []
    # Keep the first_5 tuple alongside each pattern (used for recommendations)
    opening_pattern_first5: list[tuple[str, ...]] = []
    for op, c in repeated_openings:
        examples = [
            f["first_sentence"]
            for f in feats
            if f["first_5"] == op
        ][:3]
        opening_patterns.append({
            "type": "opening_repetition",
            "count": c,
            "examples": examples,
        })
        opening_pattern_first5.append(op)

    # ── Pattern 2: phrase_overuse (n-grams in >40% of posts) ──
    # Track *document frequency* (# of posts containing the n-gram), not
    # raw count — that's a better signal of "default phrase".
    df: Counter[tuple[str, ...]] = Counter()
    for f in feats:
        for ng in set(f["trigrams"]):
            df[ng] += 1

    # >40% of posts. For n=30 → 13. For n=2 → 1, but require at least 2.
    threshold = max(2, math.ceil(0.4 * n) + (0 if 0.4 * n == int(0.4 * n) else 0))
    # ensure strict ">40%" semantics (e.g. n=10 -> threshold 5 means 50%)
    if threshold / n <= 0.4:
        threshold += 1
    overused = [(ng, c) for ng, c in df.items() if c >= threshold]
    overused.sort(key=lambda x: -x[1])

    phrase_patterns: list[dict] = []
    for ng, c in overused[:5]:
        phrase_patterns.append({
            "type": "phrase_overuse",
            "count": c,
            "phrase": " ".join(ng),
        })

    # ── Pattern 3: structure_monotone (>60% question→...→question) ──
    qsq_count = sum(1 for f in feats if f["has_question_story_question"])
    structure_patterns: list[dict] = []
    structure_monotone = qsq_count / n > 0.6
    if structure_monotone:
        structure_patterns.append({
            "type": "structure_monotone",
            "count": qsq_count,
            "structure": "question -> story -> question",
        })

    # ── Pattern 4: opening_pattern_variety ──
    cat_counter: Counter[str] = Counter(f["opening_category"] for f in feats)
    # Don't credit "other"/"empty" buckets toward "variety".
    meaningful_categories = {
        c for c in cat_counter
        if c not in {"other", "empty"} and cat_counter[c] > 0
    }
    variety_count = len(meaningful_categories)
    low_variety = variety_count < 3

    # ── Diversity score ──
    score = 100
    score -= 10 * len(opening_patterns)
    score -= 5 * len(phrase_patterns)
    if structure_monotone:
        score -= 15
    if low_variety:
        score -= 10
    score = max(0, min(100, score))

    if score >= 80:
        verdict = "diverse"
    elif score >= 50:
        verdict = "drifting"
    else:
        verdict = "stuck"

    # ── Recommendations ──
    recs: list[str] = []

    questions_used = cat_counter.get("question", 0)
    if questions_used == 0:
        recs.append(
            f"Try opening with a question (last {n} posts: 0 questions)"
        )
    elif questions_used < max(1, n // 10):
        recs.append(
            f"Questions are rare in openers ({questions_used}/{n}) — "
            f"vary by leading with one"
        )

    for pat in phrase_patterns[:3]:
        recs.append(
            f"Phrase '{pat['phrase']}' appears in {pat['count']}/{n} posts "
            f"— vary the opener"
        )

    for pat, first5 in list(zip(opening_patterns, opening_pattern_first5))[:2]:
        ex = pat["examples"][0] if pat["examples"] else "?"
        recs.append(
            f"Opening pattern '{' '.join(first5)}' repeats {pat['count']} "
            f"times — e.g. \"{ex[:60]}\""
        )

    if structure_monotone:
        recs.append(
            f"Structure 'question -> story -> question' in {qsq_count}/{n} "
            f"posts — try ending with a statement instead"
        )

    if low_variety:
        present = ", ".join(sorted(meaningful_categories)) or "none"
        missing = sorted({"year", "number", "question", "statement", "memory"}
                         - meaningful_categories)
        recs.append(
            f"Only {variety_count} opener category/-ies in use ({present}); "
            f"try: {', '.join(missing[:3])}"
        )

    if not recs:
        recs.append("Voice looks healthy — no major drift detected.")

    patterns: list[dict] = []
    patterns.extend(opening_patterns)
    patterns.extend(phrase_patterns)
    patterns.extend(structure_patterns)

    return {
        "samples": n,
        "diversity_score": score,
        "patterns": patterns,
        "verdict": verdict,
        "recommendations": recs,
    }


# ─────────────────────────────────────────────
# Pretty report (used by chat command)
# ─────────────────────────────────────────────

_VERDICT_LABEL = {
    "diverse": "מגוון",
    "drifting": "סוחף לרוטינה",
    "stuck": "תקוע",
}


def format_report(result: dict) -> str:
    n = result.get("samples", 0)
    if n == 0:
        return "אין פוסטים לניתוח (תיקיית linkedin ריקה)."

    score = result.get("diversity_score", 0)
    verdict = result.get("verdict", "?")
    label = _VERDICT_LABEL.get(verdict, verdict)

    lines: list[str] = []
    lines.append(f"ניתוח סחף קולי — {n} פוסטים אחרונים")
    lines.append(f"  ציון מגוון: {score}/100   ({label})")
    lines.append("")

    patterns = result.get("patterns") or []
    if patterns:
        lines.append("דפוסים שזוהו:")
        for pat in patterns:
            t = pat.get("type")
            if t == "opening_repetition":
                ex = pat.get("examples", [""])[0]
                lines.append(
                    f"  • פתיחה חוזרת ({pat['count']} פעמים): "
                    f"\"{ex[:70]}\""
                )
            elif t == "phrase_overuse":
                lines.append(
                    f"  • ביטוי בשימוש-יתר ({pat['count']}/{n}): "
                    f"'{pat['phrase']}'"
                )
            elif t == "structure_monotone":
                lines.append(
                    f"  • מבנה מונוטוני ({pat['count']}/{n}): "
                    f"{pat['structure']}"
                )
        lines.append("")
    else:
        lines.append("לא זוהו דפוסים בעייתיים.")
        lines.append("")

    recs = result.get("recommendations") or []
    if recs:
        lines.append("המלצות:")
        for r in recs:
            lines.append(f"  → {r}")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# CLI smoke-test
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    n = 30
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        n = int(sys.argv[1])
    res = analyze_voice_drift(top_n=n)
    print(format_report(res))
