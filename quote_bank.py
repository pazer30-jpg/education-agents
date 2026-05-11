"""
quote_bank.py
בנק ציטוטים מאוצר עבור מוקי / פז שלמה.

מטרה: למנוע חזרה על אותו הוגה (בעיקר בובר) ב-5 פוסטים ברצף.
- בנק סטטי של ~25-30 ציטוטים מהוגי חינוך מובילים.
- כל ציטוט מתויג בתגיות נושא תואמות ל-FIELD_EXAMPLES (שייכות, מנהיגות,
  חוסן, פדגוגיה וכד').
- get_recently_used_authors() סורק פוסטים אחרונים ב-output/posts/ ומחזיר
  שמות הוגים שכבר הוזכרו ב-N הימים האחרונים.
- format_quote_for_prompt() מחזיר בלוק טקסט להזרקה לפרומפט של Agent 3.

ההזרקה לסוכן 3 היא רמז (suggest), לא חובה — הוא יכול לבחור להשתמש
בציטוט או להתעלם.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from config import POSTS_DIR


# ─────────────────────────────────────────────
# Static quote bank
# ─────────────────────────────────────────────

QUOTE_BANK: list[dict] = [
    # ── Martin Buber — דיאלוג, אני-אתה ─────
    {
        "author": "Martin Buber",
        "hebrew_name": "בובר",
        "year": 1923,
        "quote_he": "כל חיים אמיתיים הם מפגש.",
        "quote_en": "All real living is meeting.",
        "themes": ["דיאלוג", "מפגש", "נוכחות", "שייכות"],
        "context": "מתוך 'אני ואתה' (1923). הציטוט המכונן של פילוסופיית הדיאלוג.",
    },
    {
        "author": "Martin Buber",
        "hebrew_name": "בובר",
        "year": 1947,
        "quote_he": "המחנך מחנך באמצעות מי שהוא, לא באמצעות מה שהוא יודע.",
        "quote_en": "The educator educates through who he is, not through what he knows.",
        "themes": ["מחנך", "פדגוגיה", "נוכחות", "מדריכים"],
        "context": "מתוך 'נתיבות באוטופיה' / 'בסוד שיח'. המחנך כנוכחות, לא ככלי.",
    },

    # ── Paulo Freire — פדגוגיה ביקורתית ─────
    {
        "author": "Paulo Freire",
        "hebrew_name": "פריירה",
        "year": 1970,
        "quote_he": "אין חינוך נייטרלי. כל מעשה חינוכי הוא מעשה פוליטי.",
        "quote_en": "There is no such thing as a neutral educational process.",
        "themes": ["פדגוגיה", "כוח", "ביקורת", "מנהיגות"],
        "context": "מתוך 'פדגוגיה של המדוכאים' (1970). חינוך כפעולה מכוונת ערכית.",
    },
    {
        "author": "Paulo Freire",
        "hebrew_name": "פריירה",
        "year": 1970,
        "quote_he": "אף אחד לא מחנך אף אחד — אנחנו מחנכים את עצמנו ביחד, דרך העולם.",
        "quote_en": "No one educates anyone — we educate each other in communion, mediated by the world.",
        "themes": ["דיאלוג", "פדגוגיה", "קבוצה", "למידה"],
        "context": "מתוך 'פדגוגיה של המדוכאים'. הביקורת על מודל הבנקאות בחינוך.",
    },

    # ── Viktor Frankl — משמעות ─────
    {
        "author": "Viktor Frankl",
        "hebrew_name": "פרנקל",
        "year": 1946,
        "quote_he": "כשאי אפשר לשנות את המצב — מאתגרים את עצמנו לשנות את עצמנו.",
        "quote_en": "When we are no longer able to change a situation, we are challenged to change ourselves.",
        "themes": ["משמעות", "חוסן", "חירום", "תקווה"],
        "context": "מתוך 'האדם מחפש משמעות' (1946). היסוד של הלוגותרפיה.",
    },
    {
        "author": "Viktor Frankl",
        "hebrew_name": "פרנקל",
        "year": 1946,
        "quote_he": "בין הגירוי לתגובה יש מרחב. במרחב הזה נמצא הכוח שלנו לבחור.",
        "quote_en": "Between stimulus and response there is a space. In that space is our power to choose.",
        "themes": ["משמעות", "סף", "מעברים", "חוסן"],
        "context": "מיוחס לפרנקל, מבטא את ליבת הלוגותרפיה — חירות הבחירה גם בתנאים קיצוניים.",
    },

    # ── Jack Mezirow — למידה טרנספורמטיבית ─────
    {
        "author": "Jack Mezirow",
        "hebrew_name": "מזירו",
        "year": 1991,
        "quote_he": "למידה טרנספורמטיבית מתחילה ברגע של חוסר נוחות — דילמה שאינה מתיישבת.",
        "quote_en": "Transformative learning begins with a disorienting dilemma.",
        "themes": ["למידה", "פדגוגיה", "אי-נוחות", "מעברים"],
        "context": "מתוך תיאוריית הלמידה הטרנספורמטיבית (1991). מקור אי-הנוחות כמנוע שינוי.",
    },
    {
        "author": "Jack Mezirow",
        "hebrew_name": "מזירו",
        "year": 2000,
        "quote_he": "אנחנו לא לומדים מהניסיון — אנחנו לומדים מהרפלקציה על הניסיון.",
        "quote_en": "We do not learn from experience — we learn from reflecting on experience.",
        "themes": ["למידה", "רפלקציה", "פדגוגיה", "מדידה"],
        "context": "מסורת הלמידה הטרנספורמטיבית. הרפלקציה כמרכיב חיוני.",
    },

    # ── Reuven Kahana — חינוך בלתי-פורמלי ─────
    {
        "author": "Reuven Kahana",
        "hebrew_name": "כהנא",
        "year": 1988,
        "quote_he": "החינוך הבלתי-פורמלי הוא קוד מסוג שונה — וולונטרי, רב-ממדי, ומבוסס על שייכות.",
        "quote_en": "Non-formal education is a different code — voluntary, multidimensional, belonging-based.",
        "themes": ["חינוך בלתי-פורמלי", "שייכות", "קבוצה", "תנועת נוער"],
        "context": "ראובן כהנא, 'קוד הבלתי-פורמלי' (1988). התשתית התיאורטית לחבל בלתי-פורמלי בישראל.",
    },
    {
        "author": "Reuven Kahana",
        "hebrew_name": "כהנא",
        "year": 1988,
        "quote_he": "תנועת הנוער היא מרחב שבו הצעיר עצמו יוצר את הסדר — לא נכפה עליו.",
        "quote_en": "The youth movement is a space where young people create the order — it is not imposed.",
        "themes": ["תנועת נוער", "מנהיגות", "שייכות", "כוח"],
        "context": "ראובן כהנא — תנועת הנוער כמרחב לאוטונומיה ומשמעות עצמית של בני נוער.",
    },

    # ── Erik Erikson — זהות ─────
    {
        "author": "Erik Erikson",
        "hebrew_name": "אריקסון",
        "year": 1968,
        "quote_he": "המתבגר חיפש לחזרה לעצמו דרך המראה של חברתו.",
        "quote_en": "In the social jungle of human existence, there is no feeling of being alive without a sense of identity.",
        "themes": ["זהות", "מתבגרים", "שייכות", "מעברים"],
        "context": "אריקסון, 'זהות: נעורים ומשבר' (1968). גיבוש זהות כמשימת ההתבגרות.",
    },

    # ── Lev Vygotsky — אזור התפתחות מקורבת ─────
    {
        "author": "Lev Vygotsky",
        "hebrew_name": "ויגוצקי",
        "year": 1978,
        "quote_he": "מה שילד יכול לעשות היום בעזרת מבוגר — מחר יוכל לעשות לבד.",
        "quote_en": "What a child can do with assistance today, she will be able to do by herself tomorrow.",
        "themes": ["למידה", "פדגוגיה", "מדריכים", "סף"],
        "context": "ויגוצקי, 'מחשבה ושפה' / 'מיינד בחברה' (1978). ZPD — אזור ההתפתחות המקורבת.",
    },

    # ── John Dewey — חינוך מהניסיון ─────
    {
        "author": "John Dewey",
        "hebrew_name": "דיואי",
        "year": 1938,
        "quote_he": "החינוך אינו הכנה לחיים — החינוך הוא החיים עצמם.",
        "quote_en": "Education is not preparation for life; education is life itself.",
        "themes": ["פדגוגיה", "למידה", "חינוך בלתי-פורמלי"],
        "context": "ג'ון דיואי, 'ניסיון וחינוך' (1938). חינוך כפרקטיקה חיה, לא הכנה.",
    },
    {
        "author": "John Dewey",
        "hebrew_name": "דיואי",
        "year": 1916,
        "quote_he": "אנחנו לא לומדים מהניסיון עצמו אלא מהמשמעות שאנחנו מייחסים לו.",
        "quote_en": "We do not learn from experience; we learn from reflecting on experience.",
        "themes": ["למידה", "רפלקציה", "פדגוגיה"],
        "context": "דיואי, 'דמוקרטיה וחינוך' (1916). הניסיון לבדו לא מספיק — נדרשת רפלקציה.",
    },

    # ── Maxine Greene — דמיון, חזון חברתי ─────
    {
        "author": "Maxine Greene",
        "hebrew_name": "גרין",
        "year": 1995,
        "quote_he": "דמיון הוא לא בריחה מהמציאות — הוא היכולת לראות אותה אחרת.",
        "quote_en": "Imagination is what makes empathy possible — it allows us to see other ways of being.",
        "themes": ["פדגוגיה", "דמיון", "תקווה", "מנהיגות"],
        "context": "מקסין גרין, 'שחרור הדמיון' (1995). הדמיון כתשתית לאמפתיה ולשינוי חברתי.",
    },

    # ── Parker Palmer — האומץ ללמד ─────
    {
        "author": "Parker Palmer",
        "hebrew_name": "פאלמר",
        "year": 1998,
        "quote_he": "אנחנו מלמדים את מי שאנחנו — לא רק את מה שאנחנו יודעים.",
        "quote_en": "We teach who we are.",
        "themes": ["מחנך", "נוכחות", "פדגוגיה", "מדריכים"],
        "context": "פארקר פאלמר, 'האומץ ללמד' (1998). הזהות של המחנך כתוכן הלימוד.",
    },

    # ── Donna Haraway — ידע ממוקם ─────
    {
        "author": "Donna Haraway",
        "hebrew_name": "הראווי",
        "year": 1988,
        "quote_he": "כל ידע הוא ידע ממוקם — נכתב מאיפשהו, על ידי מישהו.",
        "quote_en": "All knowledge is situated — partial, located, embodied.",
        "themes": ["ידע", "פדגוגיה", "ביקורת", "מדידה"],
        "context": "דונה הראווי, 'ידע ממוקם' (1988). חלופה ביקורתית לטענת האובייקטיביות.",
    },

    # ── bell hooks — פדגוגיה מעורבת ─────
    {
        "author": "bell hooks",
        "hebrew_name": "הוקס",
        "year": 1994,
        "quote_he": "הכיתה היא המרחב הרדיקלי ביותר של אפשרות.",
        "quote_en": "The classroom remains the most radical space of possibility in the academy.",
        "themes": ["פדגוגיה", "כוח", "שייכות", "ביקורת"],
        "context": "בל הוקס, 'ללמד לחצות גבולות' (1994). פדגוגיה מעורבת כפרקטיקה של חירות.",
    },
    {
        "author": "bell hooks",
        "hebrew_name": "הוקס",
        "year": 1994,
        "quote_he": "ללמד באהבה זה לא סנטימנטליות — זו פרקטיקה של חירות.",
        "quote_en": "To teach in a manner that respects and cares for the souls of our students is essential.",
        "themes": ["פדגוגיה", "מחנך", "שייכות"],
        "context": "בל הוקס, 'ללמד לחצות גבולות' (1994). אהבה כתשתית פדגוגית.",
    },

    # ── Etienne Wenger — קהילות פרקטיקה ─────
    {
        "author": "Etienne Wenger",
        "hebrew_name": "ונגר",
        "year": 1998,
        "quote_he": "למידה היא תוצר של השתתפות — לא של הוראה.",
        "quote_en": "Learning is an issue of engagement — of becoming a participant in a community of practice.",
        "themes": ["למידה", "קבוצה", "שייכות", "צוות"],
        "context": "אטיין ונגר, 'קהילות פרקטיקה' (1998). למידה כהשתייכות לקהילה לומדת.",
    },

    # ── Carol Dweck — תפיסת התפתחות ─────
    {
        "author": "Carol Dweck",
        "hebrew_name": "דוויק",
        "year": 2006,
        "quote_he": "להעריך מאמץ — לא תוצאה — זה מה שמייצר תפיסת התפתחות.",
        "quote_en": "Praising effort, not talent, is what builds a growth mindset.",
        "themes": ["למידה", "פדגוגיה", "חוסן", "מדידה"],
        "context": "קרול דוויק, 'Mindset' (2006). תפיסת התפתחות מול תפיסה קבועה.",
    },

    # ── Robert Putnam — הון חברתי ─────
    {
        "author": "Robert Putnam",
        "hebrew_name": "פוטנם",
        "year": 2000,
        "quote_he": "הון חברתי אינו תוצר של הזדמנות — הוא נבנה ברגעים יומיומיים של אמון.",
        "quote_en": "Social capital is built in everyday moments of trust, not grand events.",
        "themes": ["שייכות", "קבוצה", "צוות", "ניהול"],
        "context": "רוברט פוטנם, 'באולינג לבד' (2000). הון חברתי כתשתית לקהילה.",
    },

    # ── Ronald Heifetz — מנהיגות אדפטיבית ─────
    {
        "author": "Ronald Heifetz",
        "hebrew_name": "הייפץ",
        "year": 1994,
        "quote_he": "המנהיג לא נותן את התשובה — הוא מחזיק את השאלה כדי שאחרים יוכלו לעבוד עליה.",
        "quote_en": "Leadership is the activity of mobilizing people to tackle tough challenges.",
        "themes": ["מנהיגות", "כוח", "ניהול", "צוות"],
        "context": "רונלד הייפץ, 'מנהיגות בלי תשובות קלות' (1994). מנהיגות אדפטיבית.",
    },
    {
        "author": "Ronald Heifetz",
        "hebrew_name": "הייפץ",
        "year": 2002,
        "quote_he": "הקושי האמיתי במנהיגות הוא להחזיק את האנשים בתוך אי-הנוחות מספיק זמן כדי שילמדו.",
        "quote_en": "The hard work of adaptive leadership is holding people in productive disequilibrium.",
        "themes": ["מנהיגות", "אי-נוחות", "למידה", "פדגוגיה"],
        "context": "הייפץ ולינסקי, 'מנהיגות על הקו' (2002). העמדה האדפטיבית.",
    },

    # ── Donald Schön — הפרקטיקנט הרפלקטיבי ─────
    {
        "author": "Donald Schön",
        "hebrew_name": "שון",
        "year": 1983,
        "quote_he": "הפרקטיקנט המומחה חושב תוך כדי פעולה — לא לפניה ולא אחריה.",
        "quote_en": "The reflective practitioner thinks in action, not just about it.",
        "themes": ["רפלקציה", "מדריכים", "מחנך", "למידה"],
        "context": "דונלד שון, 'הפרקטיקנט הרפלקטיבי' (1983). חשיבה תוך כדי פעולה.",
    },

    # ── Howard Gardner — אינטליגנציות מרובות ─────
    {
        "author": "Howard Gardner",
        "hebrew_name": "גרדנר",
        "year": 1983,
        "quote_he": "השאלה אינה כמה אינטליגנטי אתה — אלא איך אתה אינטליגנטי.",
        "quote_en": "The question is not how smart you are, but how you are smart.",
        "themes": ["פדגוגיה", "למידה", "מדידה", "זהות"],
        "context": "הווארד גרדנר, 'מסגרות החשיבה' (1983). תיאוריית האינטליגנציות המרובות.",
    },

    # ── תוספת Buber נוספת ─────
    {
        "author": "Martin Buber",
        "hebrew_name": "בובר",
        "year": 1947,
        "quote_he": "החינוך הוא הסיכון של אדם אחד באדם שני.",
        "quote_en": "Education is the venture of one human being into another.",
        "themes": ["מחנך", "מפגש", "פדגוגיה", "דיאלוג"],
        "context": "בובר על מהות החינוך — סיכון של נוכחות, לא העברת תוכן.",
    },

    # ── תוספת Freire ─────
    {
        "author": "Paulo Freire",
        "hebrew_name": "פריירה",
        "year": 1970,
        "quote_he": "להעז להגיד את העולם זה הצעד הראשון לשנות אותו.",
        "quote_en": "To exist, humanly, is to name the world, to change it.",
        "themes": ["שיח", "כוח", "תקווה", "מנהיגות"],
        "context": "פריירה, 'פדגוגיה של המדוכאים' (1970). השפה כפעולה משחררת.",
    },
]


# ─────────────────────────────────────────────
# Selection helpers
# ─────────────────────────────────────────────

def _score_quote(quote: dict, themes: list[str]) -> int:
    """Number of theme overlaps (case-insensitive)."""
    if not themes:
        return 0
    quote_themes = {t.lower() for t in quote.get("themes", [])}
    target = {t.lower() for t in themes}
    return len(quote_themes & target)


def get_quote(themes: list[str], avoid_authors: list[str] = None) -> dict | None:
    """
    Return a quote that matches `themes` and avoids `avoid_authors`.
    Selection rules:
      1. Prefer quotes from authors NOT in avoid_authors.
      2. Among those, prefer highest theme overlap.
      3. Tie-break by preferring less common authors (alphabetical fallback).
    Returns None if the bank is empty.
    """
    avoid = {a.lower() for a in (avoid_authors or [])}
    if not QUOTE_BANK:
        return None

    # Filter eligible (not avoided)
    eligible = [
        q for q in QUOTE_BANK
        if q["author"].lower() not in avoid
        and q["hebrew_name"].lower() not in avoid
    ]
    pool = eligible if eligible else list(QUOTE_BANK)

    # Score and sort
    scored = [(_score_quote(q, themes or []), q) for q in pool]
    scored.sort(key=lambda x: (-x[0], x[1]["author"]))

    # If best score is 0 and no themes given, return first.
    # If themes given but no overlap, fall back to highest-priority eligible
    # (still avoiding repeated authors).
    return scored[0][1] if scored else None


# ─────────────────────────────────────────────
# Recently-used author scanner
# ─────────────────────────────────────────────

def _build_author_patterns() -> list[tuple[str, re.Pattern]]:
    """
    Pre-compile regex patterns for each author in the bank.
    Matches:
      - "(שם, year)"  e.g. "(בובר, 1923)"
      - "שם (year)"   e.g. "בובר (1923)"
      - "שם כתב"      bare hebrew name as standalone word
      - English author name as a standalone phrase
    """
    seen = set()
    patterns: list[tuple[str, re.Pattern]] = []
    for q in QUOTE_BANK:
        author = q["author"]
        hebrew = q["hebrew_name"]
        if author in seen:
            continue
        seen.add(author)

        # Build alternatives: hebrew name (as a word), author english, "(שם, "
        hebrew_esc = re.escape(hebrew)
        author_esc = re.escape(author)
        # Hebrew word boundary is tricky; rely on non-letter delimiters.
        pat = re.compile(
            r"(?:(?<![֐-׿A-Za-z])"
            + hebrew_esc
            + r"(?![֐-׿A-Za-z]))"
            + r"|(?:\b" + author_esc + r"\b)",
            re.IGNORECASE,
        )
        patterns.append((author, pat))
    return patterns


_AUTHOR_PATTERNS = _build_author_patterns()


def get_recently_used_authors(days: int = 14) -> list[str]:
    """
    Scan recent post files in POSTS_DIR for author mentions.
    Returns list of author canonical names (English) used in last `days` days.
    """
    posts_dir = Path(POSTS_DIR)
    if not posts_dir.exists():
        return []

    cutoff = time.time() - days * 86400
    used: set[str] = set()

    # Look at *_ready*.txt and *.md files (and other .txt for safety)
    candidates: list[Path] = []
    for pattern in ("*_ready*.txt", "*.md", "*.txt"):
        candidates.extend(posts_dir.glob(pattern))
    # Also recursive one level (e.g., posts/linkedin/, posts/blog/)
    for sub in posts_dir.iterdir():
        if sub.is_dir():
            for pattern in ("*_ready*.txt", "*.md", "*.txt"):
                candidates.extend(sub.glob(pattern))

    for p in set(candidates):
        try:
            if p.stat().st_mtime < cutoff:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for author, pat in _AUTHOR_PATTERNS:
            if author in used:
                continue
            if pat.search(text):
                used.add(author)

    return sorted(used)


# ─────────────────────────────────────────────
# Prompt formatter
# ─────────────────────────────────────────────

def format_quote_for_prompt(themes: list[str]) -> str:
    """
    Build a prompt block suggesting (not forcing) a quote for Agent 3.
    Avoids authors mentioned in the last 14 days. Returns "" if no quote.
    """
    try:
        recent = get_recently_used_authors(days=14)
    except Exception:
        recent = []

    quote = get_quote(themes or [], avoid_authors=recent)
    if not quote:
        return ""

    overlap = _score_quote(quote, themes or [])
    relevance_note = (
        "(תואם לנושא)" if overlap >= 2
        else "(מומלץ להגוון מההוגים האחרונים)" if overlap == 0
        else ""
    )

    avoided_note = ""
    if recent:
        avoided_note = (
            "\nהוגים שכבר הוזכרו ב-14 הימים האחרונים — עדיף להימנע: "
            + ", ".join(recent)
        )

    block = [
        "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"הצעת ציטוט (אופציונלי) {relevance_note}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f'"{quote["quote_he"]}"',
        f"— {quote['hebrew_name']} ({quote['year']})",
        f"  הקשר: {quote['context']}",
        "",
        "השתמש בציטוט הזה רק אם הוא מתחבר טבעית לסיפור — אל תכריח.",
        "אם הוא לא מתאים, פשוט ספר את הסיפור בלעדיו.",
    ]
    if avoided_note:
        block.append(avoided_note)
    return "\n".join(block)


# ─────────────────────────────────────────────
# CLI smoke test
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    print(f"Quote bank size: {len(QUOTE_BANK)}")
    authors = sorted({q["author"] for q in QUOTE_BANK})
    print(f"Unique authors: {len(authors)}")
    for a in authors:
        print(f"  - {a}")

    print("\n--- Recently used authors (last 14 days) ---")
    recent = get_recently_used_authors(days=14)
    print(recent or "(none)")

    test_themes = sys.argv[1:] or ["שייכות", "מנהיגות"]
    print(f"\n--- get_quote(themes={test_themes}) ---")
    q = get_quote(test_themes, avoid_authors=recent)
    if q:
        print(f"  {q['hebrew_name']} ({q['year']}): {q['quote_he']}")

    print("\n--- format_quote_for_prompt() ---")
    print(format_quote_for_prompt(test_themes))
