"""
anti_patterns.py — מאגר דפוסי פוסטים שכשלו.

עוקב אחרי פוסטים שקיבלו ציון QA נמוך, ציון Voice נמוך, או engagement חלש.
מזהה דפוסים שחוזרים (פתיחה זהה, מבנה דומה, נושא חוזר) ומחזיר אזהרה ל-Agent 3
שיוסיף לפרומפט "AVOID THESE PATTERNS:".

API:
  record_failure(post_path, qa_score, voice_score, engagement=None)
      רושם רשומת כשל. מחלץ:
        - 5 מילים פותחות
        - structure_type (שאלה / סיפור / נתון / הצהרה)
        - topics (מילים משמעותיות)

  get_anti_patterns(min_failures=2) -> list[dict]
      מחזיר דפוסים שכשלו לפחות פעמיים.

  format_for_prompt() -> str
      מחזיר בלוק טקסט לפרומפט של Agent 3.

Data store: output/anti_patterns.json — list of failure records.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR

ANTI_PATTERNS_FILE = OUTPUT_DIR / "anti_patterns.json"

# Thresholds
QA_FAIL_THRESHOLD = 60      # QA score below this = failure
VOICE_FAIL_THRESHOLD = 60   # voice score below this = failure
LOW_ENGAGEMENT_THRESHOLD = 20  # normalized engagement score below this = failure


# ─────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────

def _load() -> list[dict]:
    if ANTI_PATTERNS_FILE.exists():
        try:
            return json.loads(ANTI_PATTERNS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save(data: list[dict]) -> None:
    ANTI_PATTERNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ANTI_PATTERNS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ─────────────────────────────────────────────
# Feature extraction
# ─────────────────────────────────────────────

# Hebrew/English stopwords for topic extraction
_STOPWORDS = {
    "של", "את", "על", "עם", "אבל", "וגם", "כדי", "זה", "הוא", "היא",
    "אני", "לא", "כן", "כמו", "אם", "כי", "מה", "מי", "מתי", "איך",
    "יש", "אין", "היה", "היתה", "הם", "הן", "אנחנו", "אתה", "אתם",
    "the", "and", "but", "for", "with", "this", "that", "from", "have",
    "are", "was", "were", "been", "being", "into", "they", "them",
}


def _extract_opening_words(text: str, n: int = 5) -> str:
    """החזר את n המילים הראשונות בפוסט (לאחר ניקוי שורות ריקות וכותרת ML)."""
    if not text:
        return ""
    # Skip header lines (--- yaml frontmatter, ╔ ascii box, # markdown headers)
    cleaned: list[str] = []
    in_frontmatter = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        if line.startswith(("#", "╔", "╚", "║", "─", "━", "**אורך")):
            continue
        cleaned.append(line)
        if len(cleaned) >= 2:
            break
    if not cleaned:
        return ""
    first = cleaned[0]
    # Strip leading punctuation/emoji
    first = re.sub(r"^[\W_]+", "", first, flags=re.UNICODE)
    words = first.split()
    return " ".join(words[:n]).strip()


def _detect_structure_type(text: str) -> str:
    """זיהוי סוג מבנה הפתיחה: question / story / data / statement / quote."""
    opening = _extract_opening_words(text, n=20)
    if not opening:
        return "unknown"
    # Quote markers
    if opening.startswith(('"', "'", "«", "״")) or '"' in opening[:30]:
        return "quote"
    # Question marker (anywhere in first sentence)
    first_sentence = re.split(r"[.!?]", text, maxsplit=1)[0][:200] if text else ""
    if "?" in first_sentence or "؟" in first_sentence:
        return "question"
    # Data: starts with a number or has digits in first 30 chars
    if re.match(r"^\d", opening) or re.search(r"\d{2,}", opening[:40]):
        return "data"
    # Story: first-person past tense markers
    story_markers = ("היינו", "הייתי", "ראיתי", "פגשתי", "זכור", "באתי",
                     "הגעתי", "כשהייתי", "לפני", "אתמול")
    if any(m in opening for m in story_markers):
        return "story"
    # Default: statement
    return "statement"


def _extract_topics(text: str, max_topics: int = 5) -> list[str]:
    """החזר רשימת מילות-מפתח משמעותיות מהפוסט (Hebrew + English, length>=4)."""
    if not text:
        return []
    # Drop hashtags + URLs + sources block
    cut = re.split(r"📚\s*מקורות|## מקורות|^References:", text, maxsplit=1, flags=re.M)[0]
    cut = re.sub(r"https?://\S+", " ", cut)
    cut = re.sub(r"#\S+", " ", cut)
    # Words: Hebrew unicode range or alpha
    words = re.findall(r"[֐-׿A-Za-z]{4,}", cut)
    counter = Counter(w for w in words if w.lower() not in _STOPWORDS)
    return [w for w, _ in counter.most_common(max_topics)]


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def record_failure(
    post_path: Path,
    qa_score: int,
    voice_score: int,
    engagement: dict | None = None,
) -> dict:
    """
    Save failure pattern: extract opening 5 words, structure type, topics.
    Triggered when qa_score < 60, voice_score < 60, or engagement is bottom-tier.

    Returns the failure record (also persisted to anti_patterns.json).
    """
    post_path = Path(post_path)
    text = ""
    if post_path.exists():
        try:
            text = post_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            text = ""

    # Decide why it failed
    reasons: list[str] = []
    if qa_score is not None and qa_score < QA_FAIL_THRESHOLD:
        reasons.append(f"qa<{QA_FAIL_THRESHOLD}")
    if voice_score is not None and voice_score < VOICE_FAIL_THRESHOLD:
        reasons.append(f"voice<{VOICE_FAIL_THRESHOLD}")
    if engagement:
        eng_score = _engagement_score(engagement)
        if eng_score is not None and eng_score < LOW_ENGAGEMENT_THRESHOLD:
            reasons.append(f"engagement<{LOW_ENGAGEMENT_THRESHOLD}")

    record = {
        "post": post_path.name,
        "path": str(post_path),
        "recorded_at": datetime.now().isoformat(),
        "qa_score": qa_score,
        "voice_score": voice_score,
        "engagement": engagement or {},
        "opening": _extract_opening_words(text, n=5),
        "structure": _detect_structure_type(text),
        "topics": _extract_topics(text, max_topics=5),
        "reasons": reasons or ["below_threshold"],
    }

    data = _load()
    data.append(record)
    _save(data)
    return record


def _engagement_score(engagement: dict) -> float | None:
    """ציון engagement מנורמל לפי פלטפורמה (זהה לזה ב-performance_log)."""
    if not engagement:
        return None
    m = engagement.get("metrics", engagement)
    p = engagement.get("platform", "")
    if p == "linkedin" or "comments" in m:
        return (
            m.get("comments", 0) * 3
            + m.get("likes", 0)
            + m.get("shares", 0) * 5
        )
    if p == "blog" or "views" in m:
        return m.get("views", 0) + m.get("avg_time", 0) * 10
    if p == "podcast" or "plays" in m:
        return m.get("plays", 0)
    return None


def get_anti_patterns(min_failures: int = 2) -> list[dict]:
    """
    Return patterns that failed `min_failures`+ times — Agent 3 should avoid these.

    Returns: [{"pattern": str, "failures": int, "examples": [...], "avoid_reason": str}]
    """
    data = _load()
    if not data:
        return []

    # Group by three pattern axes:
    # 1) opening (first 5 words)
    # 2) structure type
    # 3) topic combos (sorted top-2 topics)
    by_opening: dict[str, list[dict]] = defaultdict(list)
    by_structure: dict[str, list[dict]] = defaultdict(list)
    by_topic_combo: dict[str, list[dict]] = defaultdict(list)

    for r in data:
        op = (r.get("opening") or "").strip()
        if op:
            by_opening[op].append(r)
        st = (r.get("structure") or "").strip()
        if st and st != "unknown":
            by_structure[st].append(r)
        topics = sorted([t.lower() for t in (r.get("topics") or [])[:2]])
        if len(topics) >= 2:
            combo = " + ".join(topics)
            by_topic_combo[combo].append(r)

    patterns: list[dict] = []

    for opening, recs in by_opening.items():
        if len(recs) >= min_failures:
            patterns.append({
                "pattern": f"opening: \"{opening}...\"",
                "kind": "opening",
                "failures": len(recs),
                "examples": [r["post"] for r in recs[-3:]],
                "avoid_reason": (
                    f"פתיחה זו כשלה {len(recs)} פעמים "
                    f"(QA/Voice/Engagement נמוכים)"
                ),
            })

    for structure, recs in by_structure.items():
        if len(recs) >= min_failures:
            # Only flag if this structure is over-represented vs. a baseline.
            patterns.append({
                "pattern": f"structure: {structure}",
                "kind": "structure",
                "failures": len(recs),
                "examples": [r["post"] for r in recs[-3:]],
                "avoid_reason": (
                    f"מבנה '{structure}' כשל {len(recs)} פעמים — "
                    f"גוון לסוג פתיחה אחר"
                ),
            })

    for combo, recs in by_topic_combo.items():
        if len(recs) >= min_failures:
            patterns.append({
                "pattern": f"topics: {combo}",
                "kind": "topics",
                "failures": len(recs),
                "examples": [r["post"] for r in recs[-3:]],
                "avoid_reason": (
                    f"שילוב נושאים '{combo}' לא מהדהד "
                    f"({len(recs)} פוסטים חלשים)"
                ),
            })

    # Sort by failures desc
    patterns.sort(key=lambda x: -x["failures"])
    return patterns


def format_for_prompt() -> str:
    """
    Inject as 'AVOID THESE PATTERNS:' block in Agent 3's prompt.
    Empty string if there are no anti-patterns yet.
    """
    patterns = get_anti_patterns(min_failures=2)
    if not patterns:
        return ""

    lines = ["AVOID THESE PATTERNS (דפוסים שכשלו בעבר — אל תחזור עליהם):"]
    for p in patterns[:6]:  # cap to keep prompt lean
        lines.append(f"  ✗ {p['pattern']}  ({p['failures']} כשלונות)")
        lines.append(f"     סיבה: {p['avoid_reason']}")
    lines.append("הימנע מההתאמות הללו. אם רעיון הפתיחה דומה — שנה זווית.")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] in ("--show", "-s"):
        patterns = get_anti_patterns(min_failures=2)
        if not patterns:
            print("אין דפוסי-נגד עדיין (פחות מ-2 כשלונות חוזרים).")
        else:
            print(f"\n📕 Anti-patterns ({len(patterns)}):\n")
            for p in patterns:
                print(f"  ✗ {p['pattern']}")
                print(f"    כשלונות: {p['failures']}")
                print(f"    {p['avoid_reason']}")
                print(f"    דוגמאות: {', '.join(p['examples'][:2])}")
                print()
    elif len(sys.argv) > 1 and sys.argv[1] in ("--prompt", "-p"):
        print(format_for_prompt() or "(אין anti-patterns)")
    else:
        # Default: show stats
        data = _load()
        print(f"רשומות כשל: {len(data)}")
        if data:
            print(f"קובץ: {ANTI_PATTERNS_FILE}")
            structures = Counter(r.get("structure", "?") for r in data)
            print("מבנים:")
            for s, n in structures.most_common():
                print(f"  {s}: {n}")
