"""
hook_tester.py — Scoring hooks for LinkedIn posts.
מנתח 3-5 hooks ובוחר את החזק ביותר לפי קריטריוני engagement.

ללא LLM call — heuristics בלבד (מהיר, חינם, דטרמיניסטי).
"""

import re
from typing import Iterable

# ─────────────────────────────────────────────
# Heuristic scorers
# ─────────────────────────────────────────────

# Common openers Paz overuses or are too generic
_CLICHE_STARTS = [
    "יש רגע ש",
    "כל מי ש",
    "אני חושב ש",
    "לפעמים אני",
    "האם אי פעם",
    "תמיד אומרים ש",
    "כולם יודעים ש",
    "מחקרים מראים",
]

# Words/phrases that signal Paz's authentic voice
_AUTHENTIC_MARKERS = [
    "אגוז", "דניאל", "וינגייט", "מכינה", "כפר הנוער", "קיבוץ", "שומר",
    "2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026",
]

# Strong opening patterns
_STRONG_PATTERNS = [
    (r"^[א-ת]+\s+\d{4}", 8),                      # "אוקטובר 2023" — temporal anchor
    (r"^\d+\s+[א-ת]+", 6),                         # "33 נערים" — number opening
    (r"^[נ-ת].*\?$", 5),                # ends with question
    (r"\".+\"", 4),                                # contains quote
    (r"\[.+\]|\(.+\)", 3),                         # contains parenthetical aside
    (r"—|–", 2),                                   # contains em-dash (rhythm)
]


def score_hook(hook: str) -> dict:
    """
    Score a single hook 0-100 based on engagement heuristics.
    Returns: {"score": int, "reasons": list, "warnings": list}
    """
    if not hook or len(hook.strip()) < 10:
        return {"score": 0, "reasons": [], "warnings": ["empty or too short"]}

    text = hook.strip()
    score = 50  # baseline
    reasons = []
    warnings = []

    # ── Length (LinkedIn shows ~210 chars before "see more") ──
    char_len = len(text)
    if 60 <= char_len <= 200:
        score += 10
        reasons.append(f"אורך אופטימלי ({char_len} תווים — לפני 'see more')")
    elif char_len < 60:
        score -= 10
        warnings.append(f"קצר מדי ({char_len} תווים)")
    elif char_len > 280:
        score -= 5
        warnings.append(f"ארוך מדי ({char_len} תווים — חצי יקטע)")

    # ── Cliché penalty ──
    for cliche in _CLICHE_STARTS:
        if text.startswith(cliche):
            score -= 15
            warnings.append(f"פתיחה שחוקה: '{cliche}'")
            break

    # ── Authenticity bonus ──
    auth_hits = [m for m in _AUTHENTIC_MARKERS if m in text]
    if auth_hits:
        score += min(15, len(auth_hits) * 7)
        reasons.append(f"פרטים מאמתים: {', '.join(auth_hits[:3])}")

    # ── Strong patterns ──
    for pattern, points in _STRONG_PATTERNS:
        if re.search(pattern, text):
            score += points

    # ── First-person presence (Paz's signature) ──
    if re.search(r"\bאני\b|\bלי\b|\bשלי\b", text):
        score += 5
        reasons.append("גוף ראשון")

    # ── Specificity: numbers, places, names ──
    has_number = bool(re.search(r"\d+", text))
    has_proper_noun = bool(re.search(r"[א-ת]{3,}\s+[א-ת]{3,}", text))  # rough
    if has_number:
        score += 5
        reasons.append("מספר ספציפי")

    # ── Question/curiosity gap ──
    if "?" in text:
        score += 5
        reasons.append("פותח שאלה")

    # ── Tension words ──
    tension_words = ["אבל", "ובכל זאת", "דווקא", "מול ", "לעומת"]
    if any(w in text for w in tension_words):
        score += 5
        reasons.append("מתח/דואליות")

    # ── Generic "should/must" red flags ──
    if any(w in text for w in ["צריך ל", "חייבים ל", "כדאי ל", "זה הזמן"]):
        score -= 8
        warnings.append("טון מטיף")

    # ── Emoji penalty (Paz never uses) ──
    if re.search(r"[\U0001F300-\U0001F9FF☀-➿]", text):
        score -= 20
        warnings.append("מכיל אימוג'י (פז לא משתמש)")

    # Clamp
    score = max(0, min(100, score))

    return {
        "score": score,
        "reasons": reasons,
        "warnings": warnings,
        "char_len": char_len,
    }


def rank_hooks(hooks: Iterable[str]) -> list[dict]:
    """
    Score all hooks and rank by score (descending).
    Returns: list of {"hook": str, "score": int, "reasons": list, "warnings": list}
    """
    ranked = []
    for h in hooks:
        if not h:
            continue
        result = score_hook(h)
        result["hook"] = h
        ranked.append(result)
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


def pick_best_hook(hooks: Iterable[str], current_opening: str = "") -> dict:
    """
    Pick the best hook from a list of alternatives.
    If current_opening (first line of generated post) scores highest, keep it.
    Returns: {"best": str, "score": int, "alternatives": list, "switched": bool}
    """
    candidates = list(hooks)
    if current_opening:
        # Add current opening to candidates, mark it as "current"
        candidates_with_current = [current_opening] + candidates
    else:
        candidates_with_current = candidates

    ranked = rank_hooks(candidates_with_current)
    if not ranked:
        return {"best": current_opening, "score": 0, "alternatives": [], "switched": False}

    best = ranked[0]
    switched = current_opening and best["hook"] != current_opening

    return {
        "best": best["hook"],
        "score": best["score"],
        "reasons": best["reasons"],
        "warnings": best["warnings"],
        "alternatives": [
            {"hook": r["hook"][:80], "score": r["score"]}
            for r in ranked[1:4]
        ],
        "switched": bool(switched),
    }


# ─────────────────────────────────────────────
# CLI for testing
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        demo_hooks = [
            "יש רגע שכל מי שעבד עם בני נוער מכיר.",
            "אוקטובר 2023. 33 מפונים מעוטף עזה הגיעו לכפר הנוער.",
            "כל פעם אני שואל את עצמי את אותה שאלה.",
            "דניאל, כיתה י', סירב להוביל פעילות. שבוע אחרי, ארגן ערב שירה.",
        ]
        result = pick_best_hook(demo_hooks)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif len(sys.argv) > 1:
        result = score_hook(sys.argv[1])
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Usage: python3 hook_tester.py <hook_text>  |  --demo")
