"""
voice_evolution.py — Detect how Paz's voice has evolved over time.

Compares "recent window" (last N days) vs "older window" (preceding N days),
surfaces real shifts (3+ occurrences), and proposes concrete patches to
voice_profile.py.

Pure Python — no LLM. Reads posts from output/posts/linkedin/ and
output/posts/blog/, splits by file mtime.

Public API:
    analyze_evolution(window_days: int = 90) -> dict
    propose_voice_updates(window_days: int = 90) -> dict
    apply_updates(window_days: int = 90, dry_run: bool = True) -> dict
    format_evolution_report(result: dict) -> str
"""

from __future__ import annotations

import re
import time
from collections import Counter
from pathlib import Path

from config import LINKEDIN_DIR, BLOG_DIR
from hebrew_lemma import tokens as lemma_tokens, STOPWORDS as LEMMA_STOPS
from voice_drift import (
    _strip_meta,
    _tokenize,
    _sentences,
    _opening_category,
    _ngrams,
)
from voice_profile import (
    VOICE_PROFILE,
    FIELD_EXAMPLES,
    _FORBIDDEN_PATTERNS,
    _AUTHENTICATING_NAMES,
)


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────

# Min occurrences before a shift counts as "real" (not noise)
MIN_OCCURRENCES = 3

# Minimum delta in document-frequency ratio for a word to count as
# "emerging" or "fading". E.g. 0.15 = 15-percentage-point shift.
MIN_DF_DELTA = 0.15

# Voice profile path (used by apply_updates)
VOICE_PROFILE_PATH = Path(__file__).parent / "voice_profile.py"

# Hebrew stopwords beyond the lemma module's set — function/grammar bits
# that survive lemmatization but aren't content-bearing.
_EXTRA_STOPS = {
    "אבל", "כי", "אם", "אז", "כך", "עם", "של", "את", "על", "אל", "מן",
    "כמו", "כדי", "כאן", "שם", "פה", "מאוד", "יותר", "פחות", "רק",
    "גם", "כל", "לא", "אין", "יש", "מה", "מי", "זה", "זו", "זאת", "אלה",
    "אני", "אתה", "אתם", "הוא", "היא", "הם", "הן", "אנחנו",
    "היה", "הייתה", "היו", "להיות", "אחרי", "לפני", "בין", "תחת",
    "the", "a", "an", "of", "to", "in", "on", "at", "for", "and", "or",
    "but", "is", "are", "was", "were", "be", "this", "that", "it", "as",
    "by", "from", "with", "about", "into", "than", "then", "so", "not",
}


# ─────────────────────────────────────────────
# Post discovery + windowing
# ─────────────────────────────────────────────

def _gather_all_posts() -> list[Path]:
    """All ready linkedin posts + blog posts, excluding .bak files."""
    out: list[Path] = []
    if LINKEDIN_DIR.exists():
        out.extend(
            p for p in LINKEDIN_DIR.glob("*_ready*.txt")
            if not p.name.endswith(".bak") and p.is_file()
        )
    if BLOG_DIR.exists():
        out.extend(
            p for p in BLOG_DIR.glob("*.md")
            if not p.name.endswith(".bak") and p.is_file()
        )
    return out


def _split_by_window(
    posts: list[Path], window_days: int, now: float | None = None
) -> tuple[list[Path], list[Path]]:
    """
    Split posts into (recent, older) by mtime.
      • recent  = mtime within last `window_days`
      • older   = mtime in the `window_days` preceding that
    Anything older than 2*window_days is dropped (we want comparable bands).
    """
    if now is None:
        now = time.time()
    cutoff_recent = now - window_days * 86400
    cutoff_older = now - 2 * window_days * 86400

    recent: list[Path] = []
    older: list[Path] = []
    for p in posts:
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if mt >= cutoff_recent:
            recent.append(p)
        elif mt >= cutoff_older:
            older.append(p)
        # else: too old to compare against, skip
    return recent, older


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


# ─────────────────────────────────────────────
# Per-bucket feature aggregation
# ─────────────────────────────────────────────

def _content_lemmas(text: str) -> list[str]:
    """Lemmatized content tokens (no stopwords, length≥2)."""
    body = _strip_meta(text)
    out = []
    for t in lemma_tokens(body):
        if t in LEMMA_STOPS or t in _EXTRA_STOPS:
            continue
        if len(t) < 2:
            continue
        out.append(t)
    return out


def _phrases_3to5(text: str) -> set[str]:
    """Extract content-bearing 3-to-5-word phrases (raw, not lemmatized).
    Returns a *set* (per-document), so the same phrase counts once per post."""
    body = _strip_meta(text)
    toks = _tokenize(body)
    phrases: set[str] = set()
    for n in (3, 4, 5):
        for ng in _ngrams(toks, n):
            # Skip if ALL words are stopwords / pure function-words
            if all(w in _EXTRA_STOPS or w in LEMMA_STOPS or len(w) < 2 for w in ng):
                continue
            # Skip phrases that are mostly numbers/punctuation residue
            if any(re.match(r"^\d+$", w) for w in ng):
                continue
            phrases.add(" ".join(ng))
    return phrases


def _avg_sentence_length(text: str) -> float:
    """Average words per sentence in the body."""
    body = _strip_meta(text)
    sents = _sentences(body)
    if not sents:
        return 0.0
    total_words = sum(len(_tokenize(s)) for s in sents)
    return total_words / len(sents)


def _aggregate(posts: list[Path]) -> dict:
    """Compute the per-bucket aggregate features used for comparison."""
    n = len(posts)
    if n == 0:
        return {
            "n_posts": 0,
            "lemma_df": Counter(),
            "phrase_df": Counter(),
            "opening_cats": Counter(),
            "avg_sentence_length": 0.0,
            "avg_post_length": 0.0,
        }

    lemma_df: Counter[str] = Counter()  # in how many posts each lemma appears
    phrase_df: Counter[str] = Counter()  # in how many posts each phrase appears
    opening_cats: Counter[str] = Counter()
    sent_lens: list[float] = []
    post_lens: list[int] = []

    for p in posts:
        txt = _read(p)
        if not txt.strip():
            continue
        body = _strip_meta(txt)

        # Lemma DF (per-doc, unique)
        for lem in set(_content_lemmas(txt)):
            lemma_df[lem] += 1

        # Phrase DF (per-doc, unique)
        for ph in _phrases_3to5(txt):
            phrase_df[ph] += 1

        # Opening category from first sentence
        sents = _sentences(body)
        first = sents[0] if sents else ""
        opening_cats[_opening_category(first)] += 1

        sent_lens.append(_avg_sentence_length(txt))
        post_lens.append(len(body))

    return {
        "n_posts": n,
        "lemma_df": lemma_df,
        "phrase_df": phrase_df,
        "opening_cats": opening_cats,
        "avg_sentence_length": (sum(sent_lens) / len(sent_lens)) if sent_lens else 0.0,
        "avg_post_length": (sum(post_lens) / len(post_lens)) if post_lens else 0.0,
    }


# ─────────────────────────────────────────────
# Comparison helpers
# ─────────────────────────────────────────────

def _df_ratio(df: Counter[str], n: int) -> dict[str, float]:
    if n <= 0:
        return {}
    return {k: v / n for k, v in df.items()}


def _word_shifts(
    recent: dict, older: dict
) -> tuple[list[dict], list[dict]]:
    """Return (emerging, fading) word lists.
    Emerging = appears in 3+ recent posts AND ratio jumped MIN_DF_DELTA+
    Fading   = appeared in 3+ older posts AND ratio dropped MIN_DF_DELTA+"""
    r_n = recent["n_posts"]
    o_n = older["n_posts"]
    r_ratio = _df_ratio(recent["lemma_df"], r_n)
    o_ratio = _df_ratio(older["lemma_df"], o_n)

    emerging: list[dict] = []
    fading: list[dict] = []

    all_lemmas = set(r_ratio) | set(o_ratio)
    for lem in all_lemmas:
        r = r_ratio.get(lem, 0.0)
        o = o_ratio.get(lem, 0.0)
        r_count = recent["lemma_df"].get(lem, 0)
        o_count = older["lemma_df"].get(lem, 0)
        delta = r - o

        if delta >= MIN_DF_DELTA and r_count >= MIN_OCCURRENCES:
            emerging.append({
                "word": lem,
                "recent_count": r_count,
                "older_count": o_count,
                "recent_ratio": round(r, 2),
                "older_ratio": round(o, 2),
                "delta": round(delta, 2),
            })
        elif -delta >= MIN_DF_DELTA and o_count >= MIN_OCCURRENCES:
            fading.append({
                "word": lem,
                "recent_count": r_count,
                "older_count": o_count,
                "recent_ratio": round(r, 2),
                "older_ratio": round(o, 2),
                "delta": round(delta, 2),
            })

    emerging.sort(key=lambda d: -d["delta"])
    fading.sort(key=lambda d: d["delta"])
    return emerging, fading


def _opening_shift(recent: dict, older: dict) -> dict:
    """Detect dominant opening categories in each window and the shift."""
    def _top(cat_counter: Counter[str], total: int) -> tuple[str, float]:
        if total <= 0 or not cat_counter:
            return ("", 0.0)
        # Don't credit "other"/"empty" as the dominant opener.
        useful = {k: v for k, v in cat_counter.items()
                  if k not in {"other", "empty"}}
        if not useful:
            return ("", 0.0)
        cat, c = max(useful.items(), key=lambda kv: kv[1])
        return (cat, c / total)

    r_top, r_share = _top(recent["opening_cats"], recent["n_posts"])
    o_top, o_share = _top(older["opening_cats"], older["n_posts"])
    return {
        "recent_top": r_top,
        "recent_share": round(r_share, 2),
        "older_top": o_top,
        "older_share": round(o_share, 2),
        "shifted": bool(r_top and o_top and r_top != o_top),
        "recent_distribution": dict(recent["opening_cats"]),
        "older_distribution": dict(older["opening_cats"]),
    }


def _new_phrases(recent: dict, older: dict) -> list[dict]:
    """Phrases appearing in 3+ recent posts that DID NOT appear in voice
    profile AND DID NOT appear in older posts."""
    profile_blob = _voice_profile_text().lower()
    older_phrases = set(older["phrase_df"].keys())

    out: list[dict] = []
    for ph, c in recent["phrase_df"].items():
        if c < MIN_OCCURRENCES:
            continue
        if ph in older_phrases:
            continue
        if ph.lower() in profile_blob:
            continue
        out.append({"phrase": ph, "count": c})
    out.sort(key=lambda d: -d["count"])
    return out[:15]


def _voice_profile_text() -> str:
    """Concat'd searchable text from voice_profile (prompt + examples)."""
    parts = [VOICE_PROFILE]
    for ex in FIELD_EXAMPLES:
        parts.append(ex.get("moment", ""))
        parts.extend(ex.get("themes", []))
    parts.extend(_AUTHENTICATING_NAMES)
    return "\n".join(parts)


# ─────────────────────────────────────────────
# Public API: analyze_evolution
# ─────────────────────────────────────────────

def analyze_evolution(window_days: int = 90) -> dict:
    """Compare last `window_days` of posts vs the preceding `window_days`.

    Returns:
        {
          "window_days": int,
          "recent": {"n_posts": int, "avg_sentence_length": float, ...},
          "older":  {"n_posts": int, ...},
          "emerging": [ {word, recent_count, older_count, delta}, ... ],
          "fading":   [ {word, ...}, ... ],
          "opening_shift": {...},
          "sentence_length_trend": {recent, older, delta, direction},
          "new_patterns": [ {phrase, count}, ... ],
          "drift_direction": str   # human-readable summary
        }
    """
    posts = _gather_all_posts()
    recent_posts, older_posts = _split_by_window(posts, window_days)
    recent = _aggregate(recent_posts)
    older = _aggregate(older_posts)

    # Skip stats if either bucket is empty
    if recent["n_posts"] == 0 or older["n_posts"] == 0:
        return {
            "window_days": window_days,
            "recent_summary": {
                "n_posts": recent["n_posts"],
                "avg_sentence_length": round(recent["avg_sentence_length"], 1),
                "avg_post_length": round(recent["avg_post_length"], 0),
            },
            "older_summary": {
                "n_posts": older["n_posts"],
                "avg_sentence_length": round(older["avg_sentence_length"], 1),
                "avg_post_length": round(older["avg_post_length"], 0),
            },
            "emerging": [],
            "fading": [],
            "opening_shift": {},
            "sentence_length_trend": {},
            "new_patterns": [],
            "drift_direction": (
                "אין מספיק נתונים לניתוח התפתחות "
                f"(recent={recent['n_posts']}, older={older['n_posts']})"
            ),
            "insufficient_data": True,
        }

    emerging, fading = _word_shifts(recent, older)
    opening_shift = _opening_shift(recent, older)
    new_patterns = _new_phrases(recent, older)

    sl_delta = recent["avg_sentence_length"] - older["avg_sentence_length"]
    if abs(sl_delta) < 0.5:
        sl_dir = "stable"
    elif sl_delta > 0:
        sl_dir = "longer"
    else:
        sl_dir = "shorter"

    sl_trend = {
        "recent": round(recent["avg_sentence_length"], 1),
        "older": round(older["avg_sentence_length"], 1),
        "delta": round(sl_delta, 1),
        "direction": sl_dir,
    }

    # ── Compose a human-readable drift_direction summary ──
    bits: list[str] = []
    if opening_shift.get("shifted"):
        bits.append(
            f"פתיחות עברו מ-{opening_shift['older_top']} "
            f"ל-{opening_shift['recent_top']}"
        )
    if sl_dir != "stable":
        word = "התקצרו" if sl_dir == "shorter" else "התארכו"
        bits.append(f"משפטים {word} ({sl_trend['older']}→{sl_trend['recent']} מילים)")
    if emerging:
        top_e = ", ".join(d["word"] for d in emerging[:3])
        bits.append(f"מילים חדשות בולטות: {top_e}")
    if fading:
        top_f = ", ".join(d["word"] for d in fading[:3])
        bits.append(f"דעכו: {top_f}")
    if new_patterns:
        bits.append(f"{len(new_patterns)} ביטויים חדשים שלא בפרופיל")

    drift_direction = " · ".join(bits) if bits else "אין שינויים משמעותיים"

    return {
        "window_days": window_days,
        "recent_summary": {
            "n_posts": recent["n_posts"],
            "avg_sentence_length": sl_trend["recent"],
            "avg_post_length": round(recent["avg_post_length"], 0),
        },
        "older_summary": {
            "n_posts": older["n_posts"],
            "avg_sentence_length": sl_trend["older"],
            "avg_post_length": round(older["avg_post_length"], 0),
        },
        "emerging": emerging[:15],
        "fading": fading[:15],
        "opening_shift": opening_shift,
        "sentence_length_trend": sl_trend,
        "new_patterns": new_patterns,
        "drift_direction": drift_direction,
        "insufficient_data": False,
    }


# ─────────────────────────────────────────────
# Public API: propose_voice_updates
# ─────────────────────────────────────────────

def propose_voice_updates(window_days: int = 90) -> dict:
    """Translate evolution findings into concrete voice_profile.py changes.

    Returns:
        {
          "additions": [
             {"section": "language_features"|"vocabulary"|"FIELD_EXAMPLES"|...,
              "text": str, "reason": str},
             ...
          ],
          "removals": [
             {"section": "_FORBIDDEN_PATTERNS",
              "text": str, "reason": str},
             ...
          ],
          "rationale": str,
        }
    """
    ev = analyze_evolution(window_days)
    if ev.get("insufficient_data"):
        return {
            "additions": [],
            "removals": [],
            "rationale": ev["drift_direction"],
        }

    additions: list[dict] = []
    removals: list[dict] = []

    # 1) Emerging words → add to "אוצר מילים מרכזי" (vocabulary section)
    profile_blob = _voice_profile_text()
    for d in ev["emerging"][:8]:
        w = d["word"]
        # Already covered (approximate match — lemma vs full-form)?
        if any(w in line for line in profile_blob.splitlines()):
            continue
        additions.append({
            "section": "vocabulary",
            "text": w,
            "reason": (
                f"מופיע ב-{d['recent_count']} פוסטים אחרונים "
                f"(לעומת {d['older_count']} בחלון הקודם, "
                f"+{int(d['delta']*100)} נק' אחוז)"
            ),
        })

    # 2) New phrases (3+ posts, not in profile) → add to FIELD_EXAMPLES note
    #    or "language features" — we expose them as proposed phrasing samples.
    for d in ev["new_patterns"][:6]:
        additions.append({
            "section": "language_features",
            "text": d["phrase"],
            "reason": f"דפוס חדש שעולה ב-{d['count']} פוסטים, לא קיים בפרופיל",
        })

    # 3) Opening pattern shift → suggest updating opening list
    op_shift = ev["opening_shift"]
    if op_shift.get("shifted") and op_shift.get("recent_share", 0) >= 0.4:
        additions.append({
            "section": "openings",
            "text": (
                f"פז עבר לפתיחה מסוג '{op_shift['recent_top']}' "
                f"({int(op_shift['recent_share']*100)}% מהפוסטים האחרונים)"
            ),
            "reason": (
                f"בעבר היה '{op_shift['older_top']}' "
                f"({int(op_shift['older_share']*100)}%) — שווה לעדכן את 4 הפתיחות"
            ),
        })

    # 4) Sentence length trend → note in language rules
    sl = ev["sentence_length_trend"]
    if sl.get("direction") == "longer" and abs(sl.get("delta", 0)) >= 1.5:
        additions.append({
            "section": "language_rules",
            "text": (
                f"משפטים מתארכים: ממוצע {sl['older']}→{sl['recent']} מילים"
            ),
            "reason": "כדאי לבדוק אם זה כיוון רצוי או דריפט",
        })
    elif sl.get("direction") == "shorter" and abs(sl.get("delta", 0)) >= 1.5:
        additions.append({
            "section": "language_rules",
            "text": (
                f"משפטים מתקצרים: ממוצע {sl['older']}→{sl['recent']} מילים — "
                f"מתחזק את כלל 'משפטים קצרים'"
            ),
            "reason": "האכיפה של הכלל הקיים מתעצמת",
        })

    # 5) Forbidden-pattern removals: if a forbidden phrase appears in 3+
    #    recent posts, Paz now uses it intentionally — propose removal.
    forbidden_uses = _detect_intentional_forbidden(window_days)
    for item in forbidden_uses:
        removals.append({
            "section": "_FORBIDDEN_PATTERNS",
            "text": item["pattern"],
            "reason": (
                f"מופיע ב-{item['count']} פוסטים אחרונים — "
                f"כנראה פז משתמש בכוונה"
            ),
        })

    # ── Compose rationale ──
    rationale_parts: list[str] = [ev["drift_direction"]]
    if additions:
        rationale_parts.append(f"{len(additions)} הצעות הוספה")
    if removals:
        rationale_parts.append(f"{len(removals)} הצעות הסרה")
    if not additions and not removals:
        rationale_parts.append("אין שינוי מהותי שדורש עדכון לפרופיל")

    return {
        "additions": additions,
        "removals": removals,
        "rationale": " · ".join(rationale_parts),
        "evolution": ev,
    }


def _detect_intentional_forbidden(window_days: int) -> list[dict]:
    """Find _FORBIDDEN_PATTERNS substrings that appear in 3+ recent posts."""
    posts = _gather_all_posts()
    recent_posts, _ = _split_by_window(posts, window_days)
    if not recent_posts:
        return []

    out: list[dict] = []
    for pat in _FORBIDDEN_PATTERNS:
        # Skip emoji patterns — never want to whitelist those
        if any(ord(c) > 0x1F000 for c in pat):
            continue
        cnt = 0
        for p in recent_posts:
            txt = _read(p)
            if pat in txt:
                cnt += 1
        if cnt >= MIN_OCCURRENCES:
            out.append({"pattern": pat, "count": cnt})
    out.sort(key=lambda d: -d["count"])
    return out


# ─────────────────────────────────────────────
# Public API: apply_updates (carefully patches voice_profile.py)
# ─────────────────────────────────────────────

# Markers we use to find/extend sections in voice_profile.py
_VOCAB_HEADER = "אוצר מילים מרכזי"
_AUTO_BLOCK_BEGIN = "# ─── voice_evolution: AUTO-ADDED VOCABULARY ───"
_AUTO_BLOCK_END = "# ─── /voice_evolution AUTO-ADDED VOCABULARY ───"


def apply_updates(window_days: int = 90, dry_run: bool = True) -> dict:
    """Patch voice_profile.py with the proposals from propose_voice_updates.

    Strategy (deliberately conservative):
      • Vocabulary additions → appended as a comma-separated line under the
        existing "אוצר מילים מרכזי" section (inside VOICE_PROFILE string),
        wrapped in AUTO-ADDED markers so we can update / undo idempotently.
      • Forbidden-pattern removals → the patterns are *commented out* (not
        deleted) inside _FORBIDDEN_PATTERNS, with a reason note.
      • Other proposals (openings, language rules, new phrases) → returned
        as "manual" suggestions; we don't auto-edit prose-style sections.

    Returns:
        {
          "dry_run": bool,
          "would_apply": [str, ...],
          "manual": [str, ...],
          "applied": bool,
          "backup_path": str | None,
          "diff": str,
        }
    """
    proposal = propose_voice_updates(window_days)
    additions = proposal["additions"]
    removals = proposal["removals"]

    # Split into "auto-applicable" and "manual"
    auto_vocab = [a for a in additions if a["section"] == "vocabulary"]
    manual_items: list[str] = []
    for a in additions:
        if a["section"] != "vocabulary":
            manual_items.append(
                f"[{a['section']}] {a['text']}  ({a['reason']})"
            )

    if not auto_vocab and not removals:
        return {
            "dry_run": dry_run,
            "would_apply": [],
            "manual": manual_items,
            "applied": False,
            "backup_path": None,
            "diff": "אין שינויים אוטומטיים להחלה.",
        }

    if not VOICE_PROFILE_PATH.exists():
        return {
            "dry_run": dry_run,
            "would_apply": [],
            "manual": manual_items,
            "applied": False,
            "backup_path": None,
            "diff": f"voice_profile.py לא נמצא ב-{VOICE_PROFILE_PATH}",
        }

    original = VOICE_PROFILE_PATH.read_text(encoding="utf-8")
    patched = original

    # ── 1. Vocabulary additions (idempotent block) ──
    auto_words = [a["text"] for a in auto_vocab]
    would_apply: list[str] = []
    if auto_words:
        word_line = ", ".join(auto_words) + "."
        new_block = (
            f"{_AUTO_BLOCK_BEGIN}\n"
            f"מילים שעלו אורגנית בכתיבה האחרונה ({window_days} יום):\n"
            f"{word_line}\n"
            f"{_AUTO_BLOCK_END}"
        )
        # Replace existing auto-block if present, else insert after vocab header
        block_re = re.compile(
            re.escape(_AUTO_BLOCK_BEGIN) + r".*?" + re.escape(_AUTO_BLOCK_END),
            re.DOTALL,
        )
        if block_re.search(patched):
            patched = block_re.sub(new_block, patched)
            would_apply.append(
                f"עדכון אוטו-בלוק אוצר מילים: {len(auto_words)} מילים"
            )
        else:
            # Insert after the line containing the vocab header — find the
            # *next* horizontal-rule line and insert just before it.
            lines = patched.splitlines(keepends=True)
            inserted = False
            for i, ln in enumerate(lines):
                if _VOCAB_HEADER in ln:
                    # find the line *after* the next divider that follows
                    j = i + 1
                    # advance past divider line (━━…)
                    while j < len(lines) and "━" in lines[j]:
                        j += 1
                    # skip the existing vocab content lines until blank line
                    while j < len(lines) and lines[j].strip():
                        j += 1
                    # insert auto block at j (before the blank/next section)
                    lines.insert(j, new_block + "\n")
                    inserted = True
                    break
            if inserted:
                patched = "".join(lines)
                would_apply.append(
                    f"הוספת אוטו-בלוק אוצר מילים: {len(auto_words)} מילים"
                )
            else:
                manual_items.append(
                    f"[vocabulary] לא נמצאה כותרת '{_VOCAB_HEADER}' "
                    f"— הוסף ידנית: {word_line}"
                )

    # ── 2. Forbidden-pattern removals (comment-out, don't delete) ──
    # Patterns may appear alone on a line OR inline with siblings:
    #   "חשוב לציין", "מעניין לראות", ...
    # Strategy: find the first occurrence of "<pat>", inside the
    # _FORBIDDEN_PATTERNS list and (a) remove the literal, (b) append a
    # trailing "# REMOVED by voice_evolution: <pat> (<reason>)" comment to
    # that same line so the change is auditable.
    forbidden_block_re = re.compile(
        r"_FORBIDDEN_PATTERNS\s*=\s*\[(?P<body>.*?)^\]",
        re.DOTALL | re.MULTILINE,
    )
    fb_match = forbidden_block_re.search(patched)
    for r in removals:
        pat = r["text"]
        # Re-fetch the block each iteration so successive edits accumulate
        fb_match = forbidden_block_re.search(patched)
        if not fb_match:
            manual_items.append(
                f"[_FORBIDDEN_PATTERNS] רשימה לא נמצאה — דלג על '{pat}'"
            )
            continue
        block_start, block_end = fb_match.start("body"), fb_match.end("body")
        block_text = patched[block_start:block_end]

        # Look for "<pat>", with optional surrounding spaces
        token_re = re.compile(r'"' + re.escape(pat) + r'",\s*')
        tm = token_re.search(block_text)
        if not tm:
            manual_items.append(
                f"[_FORBIDDEN_PATTERNS] לא נמצא '{pat}' לעריכה — "
                f"בדוק ידנית ({r['reason']})"
            )
            continue
        # Find the end-of-line for the line containing this token, so we can
        # tack a comment onto that line.
        abs_token_start = block_start + tm.start()
        abs_token_end = block_start + tm.end()
        # End of line that contains the token
        line_end = patched.find("\n", abs_token_end)
        if line_end == -1:
            line_end = len(patched)
        # Compose the replacement: remove token, append annotated comment.
        new_chunk = (
            patched[block_start:abs_token_start]
            + patched[abs_token_end:line_end]
            + f"  # REMOVED by voice_evolution: \"{pat}\" "
            + f"({r['reason']})"
        )
        patched = patched[:block_start] + new_chunk + patched[line_end:]
        would_apply.append(f"השבתת ביטוי אסור: '{pat}'")

    # Compute diff snapshot
    diff_lines = _mini_diff(original, patched)

    if dry_run or not would_apply:
        return {
            "dry_run": dry_run,
            "would_apply": would_apply,
            "manual": manual_items,
            "applied": False,
            "backup_path": None,
            "diff": diff_lines,
        }

    # Live apply: backup + write
    backup = VOICE_PROFILE_PATH.with_suffix(
        VOICE_PROFILE_PATH.suffix + f".bak.{int(time.time())}"
    )
    backup.write_text(original, encoding="utf-8")
    VOICE_PROFILE_PATH.write_text(patched, encoding="utf-8")

    return {
        "dry_run": False,
        "would_apply": would_apply,
        "manual": manual_items,
        "applied": True,
        "backup_path": str(backup),
        "diff": diff_lines,
    }


def _mini_diff(before: str, after: str, ctx: int = 0) -> str:
    """Tiny line-level diff — enough to eyeball changes."""
    if before == after:
        return "(אין שינוי)"
    b = before.splitlines()
    a = after.splitlines()
    # Naive: just show first 8 changed lines from each side
    bset = set(b)
    aset = set(a)
    added = [ln for ln in a if ln not in bset][:10]
    removed = [ln for ln in b if ln not in aset][:10]
    out: list[str] = []
    for ln in removed:
        out.append("- " + ln)
    for ln in added:
        out.append("+ " + ln)
    return "\n".join(out) if out else "(שינוי לבן/רווח בלבד)"


# ─────────────────────────────────────────────
# Pretty report (for chat command)
# ─────────────────────────────────────────────

def format_evolution_report(result: dict) -> str:
    lines: list[str] = []
    wd = result.get("window_days", 90)
    rs = result.get("recent_summary", {})
    os_ = result.get("older_summary", {})

    lines.append(f"ניתוח התפתחות קולית — חלון {wd} ימים")
    lines.append(
        f"  אחרונים: {rs.get('n_posts', 0)} פוסטים   "
        f"קודמים: {os_.get('n_posts', 0)} פוסטים"
    )
    lines.append("")

    if result.get("insufficient_data"):
        lines.append(result.get("drift_direction", ""))
        return "\n".join(lines)

    lines.append("כיוון:")
    lines.append(f"  → {result.get('drift_direction', '')}")
    lines.append("")

    sl = result.get("sentence_length_trend") or {}
    if sl:
        lines.append(
            f"אורך משפט ממוצע: {sl.get('older')} → {sl.get('recent')} "
            f"({sl.get('direction')})"
        )

    op = result.get("opening_shift") or {}
    if op.get("recent_top"):
        lines.append(
            f"פתיחה דומיננטית: {op.get('older_top') or '?'} "
            f"({int((op.get('older_share') or 0)*100)}%) → "
            f"{op.get('recent_top')} "
            f"({int((op.get('recent_share') or 0)*100)}%)"
        )

    lines.append("")
    em = result.get("emerging") or []
    if em:
        lines.append("מילים שעולות:")
        for d in em[:8]:
            lines.append(
                f"  + {d['word']:<14}  {d['older_count']}→{d['recent_count']} פוסטים  "
                f"(+{int(d['delta']*100)} נק')"
            )
    fa = result.get("fading") or []
    if fa:
        lines.append("")
        lines.append("מילים שדועכות:")
        for d in fa[:8]:
            lines.append(
                f"  - {d['word']:<14}  {d['older_count']}→{d['recent_count']} פוסטים  "
                f"({int(d['delta']*100)} נק')"
            )

    np_ = result.get("new_patterns") or []
    if np_:
        lines.append("")
        lines.append("ביטויים חדשים שלא קיימים בפרופיל הקול:")
        for d in np_[:8]:
            lines.append(f"  • '{d['phrase']}'  (×{d['count']})")

    return "\n".join(lines)


def format_proposals_report(proposal: dict) -> str:
    lines: list[str] = ["הצעות עדכון לפרופיל הקול:"]
    lines.append(f"  {proposal.get('rationale', '')}")
    lines.append("")
    adds = proposal.get("additions") or []
    if adds:
        lines.append("הוספות מוצעות:")
        for a in adds:
            lines.append(f"  + [{a['section']}] {a['text']}")
            lines.append(f"      ↳ {a['reason']}")
    rems = proposal.get("removals") or []
    if rems:
        lines.append("")
        lines.append("הסרות מוצעות:")
        for r in rems:
            lines.append(f"  - [{r['section']}] '{r['text']}'")
            lines.append(f"      ↳ {r['reason']}")
    if not adds and not rems:
        lines.append("  (אין הצעות פעילות)")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    wd = 90
    mode = "analyze"
    for arg in sys.argv[1:]:
        if arg.isdigit():
            wd = int(arg)
        elif arg in ("propose", "proposals", "suggest"):
            mode = "propose"
        elif arg in ("apply",):
            mode = "apply"
        elif arg in ("apply!",):
            mode = "apply_live"

    if mode == "analyze":
        print(format_evolution_report(analyze_evolution(wd)))
    elif mode == "propose":
        print(format_proposals_report(propose_voice_updates(wd)))
    elif mode == "apply":
        res = apply_updates(window_days=wd, dry_run=True)
        print("[dry-run]")
        for w in res["would_apply"]:
            print(f"  ✓ {w}")
        for m in res["manual"]:
            print(f"  • {m}")
        print("\n--- diff ---")
        print(res["diff"])
    elif mode == "apply_live":
        res = apply_updates(window_days=wd, dry_run=False)
        print(f"applied={res['applied']}, backup={res['backup_path']}")
        for w in res["would_apply"]:
            print(f"  ✓ {w}")
