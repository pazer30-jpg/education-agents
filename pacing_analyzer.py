"""
Pacing Analyzer — מודול ניתוח קריאות וקצב לעברית.

שני כלים עיקריים:
  • reading_level(text)  — ציון Flesch-מעובד לעברית (היוריסטי)
  • analyze_pacing(text) — מזהה קשת רגשית, שטחות, ומילות מתח

Pure Python, stdlib only. אין קריאות ל-LLM.
"""

from __future__ import annotations

import re


# ─────────────────────────────────────────────
# Tokenization
# ─────────────────────────────────────────────

# מפרידי משפט: נקודה, קריאה, שאלה, סוף-פסוק עברי (׃), נקודה-פסיק
_SENTENCE_SPLIT_RE = re.compile(r"[.!?׃;]+")

# מילים: רצפים של אותיות עבריות/לטיניות/ספרות
_WORD_RE = re.compile(r"[֐-׿\w']+")


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences on Hebrew/Latin terminal punctuation."""
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [s.strip() for s in parts if s and s.strip()]


def _tokenize_words(text: str) -> list[str]:
    """Extract word tokens (Hebrew, Latin, digits)."""
    return _WORD_RE.findall(text)


# ─────────────────────────────────────────────
# Reading level (Hebrew-adapted Flesch-like)
# ─────────────────────────────────────────────

def reading_level(text: str) -> dict:
    """
    Compute a Hebrew-adapted Flesch-like readability score.

    אין נוסחה רשמית ל-Flesch בעברית, ולכן זוהי היוריסטיקה שמתבססת על:
      • אורך משפט ממוצע (sentences ארוכים → קשה יותר)
      • אורך מילה ממוצע (מילים ארוכות → קשה יותר; בעברית בעיקר בגלל שורש+הטיות)

    Score interpretation:
        score < 60   → academic
        60 <= s < 75 → complex
        75 <= s < 90 → moderate
        s >= 90      → easy

    Returns:
        {"score": float, "grade": str, "metrics": {...}}
    """
    sentences = _split_sentences(text)
    words = _tokenize_words(text)

    sentence_count = len(sentences)
    word_count = len(words)

    if word_count == 0 or sentence_count == 0:
        return {
            "score": 0.0,
            "grade": "academic",
            "metrics": {
                "avg_sentence_length": 0.0,
                "avg_word_length": 0.0,
                "word_count": 0,
                "sentence_count": 0,
            },
        }

    avg_sentence_length = word_count / sentence_count
    avg_word_length = sum(len(w) for w in words) / word_count

    # היוריסטיקה: התחלה ב-100, מורידים לפי שני הפקטורים.
    # משקלים נבחרו כך שטקסט "ממוצע" יקבל ~75-85.
    # avg_sentence_length טיפוסי בעברית: 12-18 מילים
    # avg_word_length טיפוסי בעברית: 4-5 תווים
    score = 100.0 - (avg_sentence_length * 1.6) - (avg_word_length * 5.0)
    score = max(0.0, min(100.0, score))

    if score < 60:
        grade = "academic"
    elif score < 75:
        grade = "complex"
    elif score < 90:
        grade = "moderate"
    else:
        grade = "easy"

    return {
        "score": round(score, 1),
        "grade": grade,
        "metrics": {
            "avg_sentence_length": round(avg_sentence_length, 2),
            "avg_word_length": round(avg_word_length, 2),
            "word_count": word_count,
            "sentence_count": sentence_count,
        },
    }


# ─────────────────────────────────────────────
# Pacing analyzer
# ─────────────────────────────────────────────

# מילות מתח טיפוסיות בעברית — מסמנות שינוי כיוון, ניגוד, או הפתעה.
TENSION_WORDS = ("אבל", "מול", "אם", "פתאום", "לפתע")

# אינטנסיביות: מילים מסמנות שיא/רגש חזק (לזיהוי climax)
INTENSITY_MARKERS = (
    "מאוד", "מאד", "ממש", "תמיד", "אף-פעם", "לעולם",
    "חייב", "חייבים", "חייבת", "ברור", "ודאי",
    "!", "?", "כל", "אף", "שום",
)


def _count_tension(sentences: list[str]) -> int:
    """Count sentences that contain at least one tension word."""
    count = 0
    for s in sentences:
        # word-boundary-ish check using simple substring (Hebrew lacks \b)
        # we use whitespace + punctuation surrounds to approximate
        for w in TENSION_WORDS:
            # check the word appears as a token, not inside another word
            if re.search(rf"(^|[\s,;:.!?״׳'\"\(\)\[\]\-]){re.escape(w)}([\s,;:.!?״׳'\"\(\)\[\]\-]|$)", s):
                count += 1
                break
    return count


def _intensity_score(sentence: str) -> int:
    """Rough intensity for climax detection: count exclamations, intensity markers, length."""
    score = 0
    score += sentence.count("!") * 3
    score += sentence.count("?") * 2
    for marker in INTENSITY_MARKERS:
        if marker in sentence:
            score += 1
    return score


def _split_paragraphs(text: str) -> list[str]:
    """Split on blank lines."""
    parts = re.split(r"\n\s*\n", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _count_flat_zones(paragraphs: list[str]) -> int:
    """
    Identify 'flat zones' — runs of 3+ consecutive paragraphs at roughly the same length.
    Uses bucketed word-count (rounded to nearest 10) so near-identical lengths group together.
    """
    if len(paragraphs) < 3:
        return 0

    buckets = [round(len(_tokenize_words(p)) / 10) for p in paragraphs]
    flat_zones = 0
    i = 0
    while i < len(buckets):
        j = i
        while j + 1 < len(buckets) and buckets[j + 1] == buckets[i]:
            j += 1
        run_len = j - i + 1
        if run_len >= 3:
            flat_zones += 1
        i = j + 1
    return flat_zones


def _detect_arc(sentences: list[str]) -> str:
    """
    Detect the emotional arc by computing intensity per sentence and looking at:
      • opening (first 20%)
      • climax (peak intensity)
      • resolution (last 20%)

    rising   → climax in late half, opening calm
    falling  → opening intense, resolution calm
    flat     → low variance overall
    complex  → multiple peaks / mixed
    """
    n = len(sentences)
    if n == 0:
        return "flat"

    intensities = [_intensity_score(s) for s in sentences]
    if max(intensities) == 0:
        # no markers at all — look at sentence length variance instead
        lengths = [len(_tokenize_words(s)) for s in sentences]
        if not lengths:
            return "flat"
        avg = sum(lengths) / len(lengths)
        variance = sum((l - avg) ** 2 for l in lengths) / len(lengths)
        return "flat" if variance < 5 else "complex"

    open_end = max(1, n // 5)
    close_start = max(open_end, n - max(1, n // 5))

    open_intensity = sum(intensities[:open_end]) / open_end
    close_intensity = (
        sum(intensities[close_start:]) / max(1, n - close_start)
    )
    peak_idx = max(range(n), key=lambda i: intensities[i])
    peak_val = intensities[peak_idx]

    # count distinct peaks (local maxima above threshold)
    threshold = max(2, peak_val * 0.7)
    peaks = sum(1 for v in intensities if v >= threshold)

    if peaks >= 3 and peak_val >= 4:
        return "complex"

    # peak position relative to text
    peak_position = peak_idx / max(1, n - 1)

    if peak_position >= 0.6 and close_intensity >= open_intensity:
        return "rising"
    if peak_position <= 0.4 and open_intensity >= close_intensity:
        return "falling"
    if peak_val <= 2:
        return "flat"
    return "complex"


def analyze_pacing(text: str) -> dict:
    """
    Analyze emotional arc and pacing of a Hebrew text.

    Returns:
        {
            "arc": "rising" | "flat" | "falling" | "complex",
            "flat_zones": int,
            "tension_count": int,
            "verdict": "engaging" | "acceptable" | "flat",
        }
    """
    sentences = _split_sentences(text)
    paragraphs = _split_paragraphs(text)

    arc = _detect_arc(sentences)
    flat_zones = _count_flat_zones(paragraphs)
    tension_count = _count_tension(sentences)

    # Verdict — combine signals
    if arc == "flat" and flat_zones >= 1 and tension_count <= 1:
        verdict = "flat"
    elif arc in ("rising", "complex") and tension_count >= 3 and flat_zones == 0:
        verdict = "engaging"
    elif arc == "flat" or (flat_zones >= 2 and tension_count <= 2):
        verdict = "flat"
    elif tension_count >= 2 and flat_zones <= 1:
        verdict = "engaging"
    else:
        verdict = "acceptable"

    return {
        "arc": arc,
        "flat_zones": flat_zones,
        "tension_count": tension_count,
        "verdict": verdict,
    }


# ─────────────────────────────────────────────
# CLI for quick smoke-testing
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        sample = open(sys.argv[1], encoding="utf-8").read()
    else:
        sample = (
            "ילד נכנס לחדר. הוא היה שקט. אבל פתאום, הוא צעק! "
            "כולם הסתכלו עליו. למה הוא צעק? אם זה היה בכוונה, "
            "אז משהו מטריד אותו. מול הקבוצה, הוא נשבר. "
            "לפתע, הוא חייך. הכל היה בסדר."
        )

    rl = reading_level(sample)
    pacing = analyze_pacing(sample)
    print("Reading level:", rl)
    print("Pacing:", pacing)
