"""
genre_router.py
Genre-aware voice router — different post types need slightly different voice.

Paz's overall voice (voice_profile.py) stays the same. This module fine-tunes
which traits to *emphasize* and which to *avoid* per genre. Agent 3 detects
the genre BEFORE writing and appends a small adjustment block to the system
prompt.

Pure Python — no LLM calls. Heuristics over a 500-1500 char input are reliable
and fast.

Genres
──────
  • "explanation"        — direct teaching ("איך ל…", "5 דרכים…")
  • "personal_reflection" — first-person memory / self-questioning
  • "news_commentary"    — anchored in a recent event / date
  • "research_summary"   — citation-heavy, study-anchored
"""

import re


GENRES = (
    "explanation",
    "personal_reflection",
    "news_commentary",
    "research_summary",
)


# ─────────────────────────────────────────────
# Heuristic signal extractors
# ─────────────────────────────────────────────

# First-person Hebrew markers (whole-word-ish — uses spaces / punctuation).
_FIRST_PERSON_MARKERS = [
    "אני ", "אני,", "אני.", "אני?", "אני!",
    " לי ", " שלי ", " אותי ",
    "אצלי", "ראיתי", "חשבתי", "ניסיתי", "הרגשתי",
    "כתבתי", "לימדתי", "שאלתי", "טעיתי", "למדתי",
    "הכנתי",
]

# Direct teaching markers — explanation / how-to.
# Includes both modern Hebrew imperative patterns and listicle structures.
_TEACHING_MARKERS = [
    "איך ל",        # "איך ללמד"
    "איך אפשר",
    "איך עושים",
    "5 דרכים",
    "3 דרכים",
    "4 דרכים",
    "שבע דרכים",
    "חמש דרכים",
    "5 שלבים",
    "3 שלבים",
    "מדריך ל",
    "המדריך ל",
    "כך ת",         # "כך תזהו", "כך תיצרו"
    "ככה ת",
    "צ'ק ליסט",
    "checklist",
]

# News / recency markers — proper-noun events, current dates, urgency words.
_NEWS_EVENT_MARKERS = [
    "השבוע",
    "אתמול",
    "השבוע שעבר",
    "החודש",
    "לאחרונה",
    "ימים האחרונים",
    "השבועות האחרונים",
    "אוקטובר 2023",
    "7 באוקטובר",
    "המלחמה",
    "החטופים",
    "הפינוי",
    "מפונים",
    "קורונה",
    "התקשורת דיווחה",
    "פורסם",
    "דווח",
    "התראיינתי",
    "כתבה",
]

# Date pattern: 2023, 2024, 2025, 2026 — mentioned multiple times = recency anchor.
_YEAR_RE = re.compile(r"\b(202[0-9])\b")

# Citation patterns — Hebrew name + (year) OR English Author (Year) / Author et al.
# Hebrew block is U+0590..U+05FF (matches the convention used in voice_profile.py).
_CITATION_RE_HE = re.compile(r"[֐-׿][֐-׿'\-\s]{1,30}\(\s*\d{4}\s*\)")
_CITATION_RE_EN = re.compile(r"[A-Z][a-zA-Z\-]+(?:\s+(?:et\s+al\.|&\s+[A-Z][a-zA-Z\-]+))?\s*\(\s*\d{4}\s*\)")

# Research framing words — when paired with citations, strong research signal.
_RESEARCH_MARKERS = [
    "מחקר",
    "מחקרים",
    "המחקר",
    "מטא-אנליזה",
    "מטא אנליזה",
    "ממצאים",
    "ניסוי",
    "מדגם",
    "סטטיסטית",
    "מובהקות",
    "study",
    "studies",
    "research",
    "meta-analysis",
]


def _count_first_person(text: str) -> int:
    """Count first-person markers (with overlaps allowed)."""
    n = 0
    for m in _FIRST_PERSON_MARKERS:
        n += text.count(m)
    return n


def _has_teaching_marker(text: str) -> bool:
    return any(m in text for m in _TEACHING_MARKERS)


def _count_news_signals(text: str) -> int:
    n = sum(1 for m in _NEWS_EVENT_MARKERS if m in text)
    # Years repeated 2+ times within a single post = news/recency anchor.
    years = _YEAR_RE.findall(text)
    if len(years) >= 2:
        n += 1
    return n


def _citation_density(text: str) -> float:
    """Citations per 100 words. Robust to short inputs (returns 0 if <50 words)."""
    word_count = len(text.split())
    if word_count < 50:
        return 0.0
    cites = len(_CITATION_RE_HE.findall(text)) + len(_CITATION_RE_EN.findall(text))
    return (cites / word_count) * 100.0


def _has_research_framing(text: str) -> bool:
    return any(m in text for m in _RESEARCH_MARKERS)


# ─────────────────────────────────────────────
# Genre detection
# ─────────────────────────────────────────────

def detect_genre(text: str) -> str:
    """
    Classify text into one of GENRES using weighted heuristic signals.

    Decision order matters: explanation is checked first because listicle
    markers ("5 דרכים") are unambiguous. Research is checked next because
    citation density is hard to fake. Then news vs personal is decided by
    first-person count vs event/date density.
    """
    if not text or not text.strip():
        return "personal_reflection"  # safe default — Paz's home base

    word_count = max(len(text.split()), 1)

    # 1) Explicit teaching / listicle → explanation.
    if _has_teaching_marker(text):
        return "explanation"

    # 2) Research-summary: high citation density + research framing.
    cit_density = _citation_density(text)
    if cit_density >= 1.0 and _has_research_framing(text):
        return "research_summary"
    # Even without explicit framing, very citation-dense text is a research summary.
    if cit_density >= 2.0:
        return "research_summary"

    fp = _count_first_person(text)
    fp_density = (fp / word_count) * 100.0
    news = _count_news_signals(text)

    # 3) News commentary: multiple event/date markers and low first-person density.
    if news >= 2 and fp_density < 2.0:
        return "news_commentary"

    # 4) High first-person → personal reflection.
    if fp >= 3 or fp_density >= 1.5:
        return "personal_reflection"

    # 5) Tie-breakers when nothing dominates.
    if news >= 1 and fp < 2:
        return "news_commentary"
    if _has_research_framing(text) and cit_density >= 0.5:
        return "research_summary"

    return "personal_reflection"  # Paz's default register


# ─────────────────────────────────────────────
# Voice adjustments per genre
# ─────────────────────────────────────────────

# Each entry maps a genre → which voice traits to lean into ("emphasize")
# and which to dial back ("avoid"). These don't replace voice_profile.py —
# they nudge Agent 3 within Paz's existing register.
_VOICE_ADJUSTMENTS: dict[str, dict] = {
    "explanation": {
        "emphasize": [
            "מבנה ברור: צעד אחר צעד או טענה ← דוגמה ← השלכה",
            "פעלים בציווי רך ('נסה', 'שים לב', 'בדוק')",
            "דוגמה מהשטח לכל עיקרון — אחרת זה הופך לבלוג של ניסים",
            "ענווה אפיסטמית: 'זה עובד אצלי, אצלך אולי אחרת'",
        ],
        "avoid": [
            "טון מומחה-מרצה — פז לא מטיף",
            "רשימות ממוספרות יבשות בלי סיפור מאחוריהן",
            "כללים אבסולוטיים ('תמיד', 'אף פעם')",
            "פתיחה בהגדרה — גם הסבר נפתח ברגע אוניברסלי",
        ],
    },
    "personal_reflection": {
        "emphasize": [
            "גוף ראשון אמיתי, כולל הודאה בכישלון",
            "זיכרון ספציפי עם שמות ומספרים (אגוז, 33 נערים, 2021)",
            "סוגריים כתיקון עצמי בשקט",
            "שאלה פתוחה שגם פז עצמו לא יודע לענות",
        ],
        "avoid": [
            "סנטימנטליות — אישי לא אומר מתוק",
            "סיכום מסקנה מסודר — לא לסגור את הלולאה",
            "הכללה גדולה מדי על 'הדור הצעיר' או 'מערכת החינוך'",
        ],
    },
    "news_commentary": {
        "emphasize": [
            "עיגון בזמן ספציפי (תאריך / שבוע / אירוע)",
            "מתח: מה התקשורת אומרת מול מה שראיתי בשטח",
            "ענווה אפיסטמית — 'אני כותב את זה ב-X, יכול להיות שאני טועה'",
            "חיבור לאירוע אישי קונקרטי, לא לפרשנות כללית",
        ],
        "avoid": [
            "טון של פרשן מדיני / מומחה-טלוויזיה",
            "טענות חזקות בלי סייג זמני",
            "אקטואליה בלי עיגון בשטח של פז",
            "האשמות סוחפות של 'המערכת' / 'הם'",
        ],
    },
    "research_summary": {
        "emphasize": [
            "הבחנה בין proven / suggested / theoretical",
            "מחקר אחד מציע ש... (לא 'המחקר מוכיח')",
            "חיבור בין הממצא לדוגמה ספציפית מהשטח",
            "ציטוט אחד עוצמתי מהוגה רלוונטי, בעברית",
        ],
        "avoid": [
            "ערימת ציטוטים בלי סיפור",
            "שמות חוקרים באנגלית",
            "טון של סקירת ספרות אקדמית",
            "וודאות מוגזמת לגבי גודל אפקט",
        ],
    },
}


def voice_adjustments(genre: str) -> dict:
    """
    Return {"emphasize": [...], "avoid": [...]} for the given genre.
    Unknown genres fall back to personal_reflection (Paz's home register).
    """
    return _VOICE_ADJUSTMENTS.get(genre, _VOICE_ADJUSTMENTS["personal_reflection"])


# ─────────────────────────────────────────────
# Prompt formatting for Agent 3
# ─────────────────────────────────────────────

_GENRE_LABEL_HE = {
    "explanation": "הסבר / הוראה",
    "personal_reflection": "רפלקציה אישית",
    "news_commentary": "פרשנות אקטואלית",
    "research_summary": "סיכום מחקרי",
}


def format_genre_for_prompt(genre: str) -> str:
    """Return a short Hebrew block to append to Agent 3's system prompt."""
    adj = voice_adjustments(genre)
    label = _GENRE_LABEL_HE.get(genre, genre)
    lines = [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"כיוון ז'אנר לפוסט הזה: {label}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "להדגיש:",
    ]
    for item in adj["emphasize"]:
        lines.append(f"  • {item}")
    lines.append("להימנע:")
    for item in adj["avoid"]:
        lines.append(f"  • {item}")
    lines.append(
        "הכיוון הזה מעדן את הקול הכללי של פז — לא מחליף אותו."
    )
    return "\n".join(lines)
