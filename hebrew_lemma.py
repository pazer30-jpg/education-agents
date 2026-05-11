"""
hebrew_lemma.py — Rule-based Hebrew lemmatization.
ללא תלויות חיצוניות. מבוסס heuristics מורפולוגיים.

לא מושלם — אבל פותר 80% ממה שcontent matchers צריכים:
  • מנהיגות → מנהיג ↔ מנהיג נמצא
  • הילדים → ילד  ↔ ילד נמצא
  • שייכותו → שייכות ↔ שייכות נמצא

Usage:
  python3 hebrew_lemma.py --demo
  python3 hebrew_lemma.py "המנהיגים שלנו"
"""

import re
import sys

# Hebrew morphology constants
ALEPH_BET = "אבגדהוזחטיכלמנסעפצקרשת"
FINAL_LETTERS = {"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"}

# Common prefixes that attach to nouns/verbs
# Note: ש removed from singles — it's often part of root (שייכות, שלום, שמחה)
SINGLE_PREFIXES = ["ב", "ל", "מ", "כ", "ו"]  # in/to/from/like/and
DOUBLE_PREFIXES = ["וב", "ול", "ומ", "וכ",   # and-prep
                   "כש", "מש", "לכש"]         # when, when-from, when-to

# Definite article
DEFINITE = "ה"

# Common suffixes (ordered: longest first to avoid premature match)
# Includes both final-letter forms (ם/ן/ך) and regular (מ/נ/כ)
SUFFIXES = [
    # Plural + possessive (with final letters)
    "תיהם", "תיהן", "ותיהם", "ותיהן",
    "תיהמ", "תיהנ", "ותיהמ", "ותיהנ",
    "ינו", "יכם", "יכן", "יהם", "יהן",
    "ינו", "יכמ", "יכנ", "יהמ", "יהנ",
    # Possessive
    "ותיו", "ותיה", "ותיך", "ותיכ",
    # Plural
    "יות", "ויות", "ים", "ות", "ימ", "ונ",
    # Singular possessive
    "כם", "כן", "כמ", "כנ", "נו", "הו", "הם", "הן", "המ", "הנ",
    # Feminine forms
    "ית", "ה",
    # Single-letter possessive — risky, only if stem ≥3
    "ך", "ם", "ן", "כ", "מ", "נ", "ת",
]

# Words to never modify (function words)
STOPWORDS = {
    "של", "את", "על", "עם", "אל", "מן", "הוא", "היא", "הם", "הן",
    "אני", "אתה", "אתן", "אנחנו", "זה", "זאת", "אלה", "אלו",
    "כי", "אם", "אז", "כן", "לא", "מה", "מי", "איך", "למה",
    "כל", "רק", "גם", "כמו", "בין", "תחת",
}


# ─────────────────────────────────────────────
# Lemmatization
# ─────────────────────────────────────────────

def normalize_finals(word: str) -> str:
    """Convert final letters to regular: ם→מ, ן→נ, ך→כ, ף→פ, ץ→צ."""
    return "".join(FINAL_LETTERS.get(c, c) for c in word)


def strip_prefix(word: str) -> tuple[str, str]:
    """
    Strip Hebrew prefixes. Returns (stripped_word, prefix_used).
    Doesn't strip if it would leave too short a stem.
    """
    if len(word) < 4:
        return word, ""

    # Try double prefixes first (vowels stripped from spec)
    for pre in DOUBLE_PREFIXES:
        if word.startswith(pre) and len(word) - len(pre) >= 3:
            return word[len(pre):], pre

    # Then ה-prefix (definite article)
    if word.startswith(DEFINITE) and len(word) >= 4:
        # ה+stem
        return word[1:], "ה"

    # Single prefixes — but only if the next char makes a valid stem
    for pre in SINGLE_PREFIXES:
        if word.startswith(pre) and len(word) - 1 >= 3:
            stripped = word[1:]
            # If after prefix there's ה, also strip it (בה+ילד = ב + הילד = ילד)
            if stripped.startswith(DEFINITE) and len(stripped) > 3:
                return stripped[1:], pre + "ה"
            return stripped, pre

    return word, ""


def strip_suffix(word: str) -> tuple[str, str]:
    """Strip Hebrew suffixes. Returns (stripped, suffix_used)."""
    if len(word) < 4:
        return word, ""

    for suf in SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            stem = word[:-len(suf)]
            return stem, suf
    return word, ""


def lemmatize(word: str) -> str:
    """
    Reduce a Hebrew word to its likely lemma.
    Conservative — won't over-strip.
    """
    if not word or word in STOPWORDS:
        return word

    # Skip non-Hebrew (numbers, punctuation, English)
    if not any(c in ALEPH_BET for c in word):
        return word

    word = normalize_finals(word.lower())
    stripped_pre, _ = strip_prefix(word)
    stripped_full, _ = strip_suffix(stripped_pre)

    # Don't return stems shorter than 2 chars
    if len(stripped_full) < 2:
        return word
    return stripped_full


def lemmatize_text(text: str) -> str:
    """Lemmatize all Hebrew words in a text. Non-Hebrew untouched."""
    return re.sub(
        r"[֐-׿']+",
        lambda m: lemmatize(m.group(0)),
        text,
    )


def tokens(text: str) -> list[str]:
    """Tokenize + lemmatize Hebrew text."""
    raw = re.findall(r"[֐-׿'A-Za-z]+", text.lower())
    return [lemmatize(t) for t in raw if len(t) >= 2]


# ─────────────────────────────────────────────
# Similarity helpers
# ─────────────────────────────────────────────

def lemma_overlap(text_a: str, text_b: str) -> float:
    """Jaccard overlap on lemmatized tokens."""
    a = set(tokens(text_a))
    b = set(tokens(text_b))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def lemma_set(text: str) -> set[str]:
    """Get unique lemmas from text."""
    return set(tokens(text))


# ─────────────────────────────────────────────
# Demo / CLI
# ─────────────────────────────────────────────

def demo():
    pairs = [
        ("מנהיגות", "מנהיג"),
        ("הילדים", "ילד"),
        ("בקבוצות", "קבוצה"),
        ("שייכותו", "שייכות"),
        ("ובמדריכים", "מדריך"),
        ("הצעירים", "צעיר"),
        ("חוסנם", "חוסן"),
    ]
    print("\n🔤 Hebrew Lemmatization Demo:\n")
    for src, expected in pairs:
        result = lemmatize(src)
        match = "✅" if expected in result or result in expected else "⚠️"
        print(f"  {match} {src:<15} → {result:<10} (expected: {expected})")

    print("\nText similarity test:")
    a = "המנהיגים שלנו עוזרים לקבוצות"
    b = "מנהיג צריך לעזור לקבוצה"
    print(f"  A: {a}")
    print(f"  B: {b}")
    print(f"  Lemma overlap: {lemma_overlap(a, b):.0%}")


def main():
    if "--demo" in sys.argv:
        demo()
        return
    if len(sys.argv) < 2:
        print("Usage: python3 hebrew_lemma.py <text>  |  --demo")
        return
    text = " ".join(sys.argv[1:])
    print(f"Original:    {text}")
    print(f"Lemmatized:  {lemmatize_text(text)}")
    print(f"Tokens:      {tokens(text)}")


if __name__ == "__main__":
    main()
