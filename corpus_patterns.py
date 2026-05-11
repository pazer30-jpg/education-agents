"""
corpus_patterns.py — Cross-corpus pattern detector for Moki.

Before Agent 3 writes a new post, this module surfaces what's already been said
about the topic across ALL past posts (LinkedIn, Blog, Podcast scripts).

Pure Python — no LLM, no external libraries. Hebrew + English keyword matching.

Public API:
    find_related_posts(topic, top_n=5)        -> list[dict]
    extract_past_claims(topic_words, days=90, max_claims=8) -> list[dict]
    format_pattern_brief(topic, themes)       -> str
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

# ─── Configuration ─────────────────────────────────────────────────────────
BASE = Path(__file__).resolve().parent
POSTS_ROOT = BASE / "output" / "posts"

# Glob patterns for the three corpus locations
CORPUS_GLOBS = [
    ("linkedin", "linkedin", "*_ready*.txt"),
    ("blog", "blog", "*.md"),
    ("podcast", "podcast", "*_script*.md"),
]

# Hebrew thesis/claim markers — sentences containing these usually carry a position
CLAIM_MARKERS = [
    "אני חושב ש",
    "אני חושב",
    "המסקנה היא",
    "המסקנה",
    "מה שלמדתי",
    "אני מאמין",
    "חשוב להבחין",
    "בסופו של דבר",
    "מה שמעסיק אותי",
    "מה שאני רואה",
    "מה שמטריד אותי",
    "אני קורא לזה",
    "השאלה היא",
]

# Stopwords — Hebrew + English — to avoid being dominated by filler tokens
HEBREW_STOPWORDS = {
    "של", "את", "על", "עם", "אל", "מן", "כי", "גם", "לא", "כן", "זה", "זו",
    "זאת", "הוא", "היא", "הם", "הן", "אני", "אתה", "את", "אנחנו", "אתם",
    "יש", "אין", "היה", "הייתה", "היו", "להיות", "כך", "כמו", "אבל", "או",
    "אם", "מה", "מי", "איך", "למה", "מתי", "איפה", "אז", "רק", "כל", "יותר",
    "פחות", "כבר", "עוד", "שוב", "שם", "פה", "כאן", "ועוד", "בין", "תוך",
    "אחרי", "לפני", "כדי", "בגלל", "ש", "ב", "ל", "מ", "ו", "ה", "כש",
    "להם", "לנו", "להן", "אותם", "אותו", "אותה", "אותן", "שלו", "שלה",
    "שלהם", "שלנו", "שלכם", "אינו", "אינה",
}
ENGLISH_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "must", "can", "this", "that",
    "these", "those", "i", "me", "my", "we", "us", "our", "you", "your",
    "he", "him", "his", "she", "her", "it", "its", "they", "them", "their",
    "what", "which", "who", "when", "where", "why", "how", "all", "any",
    "each", "few", "more", "most", "other", "some", "such", "no", "not",
    "only", "own", "same", "so", "than", "too", "very", "just", "also",
    "to", "of", "in", "on", "at", "by", "for", "with", "from", "as", "into",
    "about", "between", "through", "after", "before", "above", "below",
    "up", "down", "out", "off", "over", "under", "again", "then", "once",
}
STOPWORDS = HEBREW_STOPWORDS | ENGLISH_STOPWORDS

# Front-matter / source / hashtag noise patterns to strip before tokenizing
_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
_HASHTAG_RE = re.compile(r"#[\w֐-׿א-ת]+")
_URL_RE = re.compile(r"https?://\S+")
_DATE_IN_NAME_RE = re.compile(r"_(\d{8})_(\d{4})")
_DATE_FRONTMATTER_RE = re.compile(r"^date:\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE)
_TITLE_FRONTMATTER_RE = re.compile(r'^title:\s*"?([^"\n]+)"?', re.MULTILINE)
_SOURCES_BLOCK_RE = re.compile(
    r"(📚\s*מקורות.*$|## (?:Sources|מקורות|References).*$)",
    re.DOTALL | re.MULTILINE,
)

# Tokenizer — keeps Hebrew, Latin letters and digits; splits on everything else
_TOKEN_RE = re.compile(r"[A-Za-z֐-׿א-ת0-9]+")


# ─── Internal helpers ──────────────────────────────────────────────────────
def _iter_corpus_files() -> list[Path]:
    """Return all corpus files (linkedin ready, blog md, podcast scripts)."""
    files: list[Path] = []
    for _label, sub, pattern in CORPUS_GLOBS:
        d = POSTS_ROOT / sub
        if not d.is_dir():
            continue
        for p in d.glob(pattern):
            # Skip backup files
            if p.name.endswith(".bak"):
                continue
            files.append(p)
    return files


def _read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _strip_noise(text: str) -> str:
    """Remove front-matter, sources block, hashtags, URLs."""
    text = _FRONTMATTER_RE.sub("", text)
    text = _SOURCES_BLOCK_RE.sub("", text)
    text = _HASHTAG_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    return text


def _tokenize(text: str) -> list[str]:
    """Lowercase tokens minus stopwords and very short tokens."""
    tokens = _TOKEN_RE.findall(text.lower())
    return [t for t in tokens if len(t) > 2 and t not in STOPWORDS]


def _extract_date(path: Path, text: str) -> str:
    """Best-effort date extraction. Returns YYYY-MM-DD or empty string."""
    # Front-matter date wins for blog posts
    m = _DATE_FRONTMATTER_RE.search(text)
    if m:
        return m.group(1)
    # Filename embedded date: _YYYYMMDD_HHMM
    m = _DATE_IN_NAME_RE.search(path.name)
    if m:
        d = m.group(1)
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    # mtime fallback
    try:
        ts = datetime.fromtimestamp(path.stat().st_mtime)
        return ts.strftime("%Y-%m-%d")
    except Exception:
        return ""


def _extract_title(path: Path, text: str) -> str:
    """Title from frontmatter, or first non-empty markdown heading, or filename."""
    m = _TITLE_FRONTMATTER_RE.search(text)
    if m:
        return m.group(1).strip().strip('"').strip()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
        if line and not line.startswith("---") and not line.startswith("title:"):
            # First substantive line as fallback title
            return (line[:80] + "…") if len(line) > 80 else line
    return path.stem


def _split_sentences(text: str) -> list[str]:
    """Naive sentence splitter for Hebrew + English."""
    # Split on . ? ! and Hebrew period equivalents + newlines (but keep heuristic loose)
    parts = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # Skip lines that are clearly metadata/headings/list bullets
        if p.startswith(("#", "---", "•", "-", "*", ">")):
            continue
        if len(p) < 20 or len(p) > 400:
            continue
        out.append(p)
    return out


def _date_str_to_date(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except Exception:
        return None


# ─── Public API ────────────────────────────────────────────────────────────
def find_related_posts(topic: str, top_n: int = 5) -> list[dict]:
    """Find posts most related to the given topic by keyword overlap.

    Returns:
        [{"file": str, "title": str, "date": str,
          "snippet": str, "similarity": float}, ...]
    """
    topic_tokens = set(_tokenize(topic or ""))
    if not topic_tokens:
        return []

    scored: list[dict] = []
    for path in _iter_corpus_files():
        raw = _read_text_safe(path)
        if not raw:
            continue
        cleaned = _strip_noise(raw)
        post_tokens = _tokenize(cleaned)
        if not post_tokens:
            continue
        post_set = set(post_tokens)
        overlap = topic_tokens & post_set
        if not overlap:
            continue
        # Jaccard-style similarity tilted toward topic coverage
        coverage = len(overlap) / max(1, len(topic_tokens))
        density = len(overlap) / max(1, len(post_set))
        similarity = round(0.7 * coverage + 0.3 * density, 4)
        if similarity <= 0:
            continue

        # Snippet: first paragraph of cleaned text, capped
        snippet = ""
        for line in cleaned.splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "---", "•", "-", "*")):
                snippet = line
                break
        if len(snippet) > 220:
            snippet = snippet[:220].rstrip() + "…"

        scored.append({
            "file": str(path),
            "title": _extract_title(path, raw),
            "date": _extract_date(path, raw),
            "snippet": snippet,
            "similarity": similarity,
        })

    scored.sort(key=lambda d: d["similarity"], reverse=True)
    return scored[:top_n]


def extract_past_claims(
    topic_words: list[str],
    days: int = 90,
    max_claims: int = 8,
) -> list[dict]:
    """Extract specific claims/conclusions Paz has made on related topics.

    Returns:
        [{"claim": str, "source": str, "date": str}, ...]
    """
    if not topic_words:
        return []
    topic_tokens = set()
    for w in topic_words:
        topic_tokens |= set(_tokenize(w))
    if not topic_tokens:
        return []

    cutoff = datetime.now() - timedelta(days=days)
    out: list[dict] = []

    for path in _iter_corpus_files():
        raw = _read_text_safe(path)
        if not raw:
            continue
        date_str = _extract_date(path, raw)
        d = _date_str_to_date(date_str)
        if d is None or d < cutoff:
            continue

        cleaned = _strip_noise(raw)
        post_tokens = set(_tokenize(cleaned))
        # Quick relevance filter — at least one topic token must appear in the post
        if not (topic_tokens & post_tokens):
            continue

        for sent in _split_sentences(cleaned):
            if not any(marker in sent for marker in CLAIM_MARKERS):
                continue
            sent_tokens = set(_tokenize(sent))
            if not (topic_tokens & sent_tokens):
                # Sentence must connect to the topic, not just be any thesis
                continue
            claim = sent.strip()
            if len(claim) > 280:
                claim = claim[:280].rstrip() + "…"
            out.append({
                "claim": claim,
                "source": path.name,
                "date": date_str,
            })
            if len(out) >= max_claims * 4:
                # Collected plenty — break inner loop, we'll trim later
                break

    # De-duplicate by claim text and prefer most recent
    seen: dict[str, dict] = {}
    for item in out:
        key = re.sub(r"\s+", " ", item["claim"]).strip()
        prev = seen.get(key)
        if prev is None or item["date"] > prev["date"]:
            seen[key] = item

    deduped = list(seen.values())
    deduped.sort(key=lambda x: x["date"], reverse=True)
    return deduped[:max_claims]


def format_pattern_brief(topic: str, themes: list[str]) -> str:
    """Format a Hebrew prompt block for Agent 3 listing what Paz already said.

    Returns empty string if nothing related is found.
    """
    related = find_related_posts(topic, top_n=5)
    if not related:
        return ""

    claims = extract_past_claims(themes or [topic], days=90, max_claims=6)

    lines = ["━━━ מה כבר אמרת על זה ━━━"]
    if claims:
        for c in claims:
            date = c.get("date") or "לא ידוע"
            claim_text = c.get("claim", "").strip()
            if not claim_text:
                continue
            lines.append(f'📝 ב-{date} כתבת: "{claim_text}"')
    else:
        # Fall back to listing related post titles so Agent 3 still has signal
        for r in related[:3]:
            date = r.get("date") or "לא ידוע"
            title = r.get("title", "").strip()
            lines.append(f"📝 ב-{date} עסקת ב: \"{title}\"")

    lines.append("")
    lines.append("הימנע מחזרה — הרחב, סתור, או קח לזווית חדשה.")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


# ─── CLI smoke-test ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    topic = " ".join(sys.argv[1:]) or "חינוך בלתי פורמלי טראומה זיכרון"
    print(f"[corpus_patterns] topic = {topic!r}\n")

    print("── Related posts ──")
    for r in find_related_posts(topic, top_n=5):
        print(f"  {r['similarity']:.3f}  {r['date']}  {r['title'][:70]}")
        print(f"           {r['file']}")

    print("\n── Past claims ──")
    for c in extract_past_claims(topic.split(), days=120, max_claims=8):
        print(f"  [{c['date']}] {c['claim'][:120]}")
        print(f"           ({c['source']})")

    print("\n── Brief ──")
    print(format_pattern_brief(topic, topic.split()))
