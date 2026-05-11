"""
similarity_checker.py - Post similarity checker for Moki pipeline.

Compares a new post against the most recent published posts on the same
platform using Jaccard similarity on word 3-grams. Pure Python, no
external dependencies.

Usage:
    from similarity_checker import check_post_similarity
    result = check_post_similarity(new_text, platform="linkedin")
    if result["flagged"]:
        ...
"""

from __future__ import annotations

import glob
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# --- Configuration ---------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output" / "posts"

PLATFORM_GLOBS: Dict[str, List[str]] = {
    "linkedin": [str(OUTPUT_DIR / "linkedin" / "*_ready*.txt")],
    "blog":     [str(OUTPUT_DIR / "blog" / "*.md")],
    "podcast":  [str(OUTPUT_DIR / "podcast" / "*_script_*.md")],
}

SIMILARITY_THRESHOLD = 0.4
NGRAM_SIZE = 3
COMMON_NGRAM_FRACTION = 0.5   # n-grams appearing in >50% of past posts are filtered
DEFAULT_TOP_N = 20


# --- Tokenization ---------------------------------------------------
# Hebrew range: \u0590-\u05FF, plus Latin letters and digits.
_WORD_RE = re.compile(r"[A-Za-z0-9\u0590-\u05FF]+")


def _tokenize(text: str) -> List[str]:
    """Split text into lowercase tokens (Hebrew + English aware).
    Hebrew tokens are lemmatized — מנהיגים → מנהיג — for better similarity matching."""
    if not text:
        return []
    raw = [m.group(0).lower() for m in _WORD_RE.finditer(text)]
    # Apply Hebrew lemmatization for better cross-form matching
    try:
        from hebrew_lemma import lemmatize
        return [lemmatize(t) for t in raw]
    except ImportError:
        return raw


def _ngrams(tokens: List[str], n: int = NGRAM_SIZE) -> Set[Tuple[str, ...]]:
    """Build a set of n-grams from a token list."""
    if len(tokens) < n:
        return set()
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def _jaccard(a: Set[Tuple[str, ...]], b: Set[Tuple[str, ...]]) -> float:
    """Jaccard similarity between two sets."""
    if not a or not b:
        return 0.0
    inter = a & b
    union = a | b
    if not union:
        return 0.0
    return len(inter) / len(union)


# --- File discovery -------------------------------------------------
def _collect_recent_posts(platform: str, top_n: int) -> List[Path]:
    """Return the top_n most recent post files for the platform (excluding .bak)."""
    patterns = PLATFORM_GLOBS.get(platform, [])
    files: List[Path] = []
    for pattern in patterns:
        for path in glob.glob(pattern):
            if path.endswith(".bak"):
                continue
            p = Path(path)
            if p.is_file():
                files.append(p)
    # Most recent first by mtime
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:top_n]


def _read_file_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _extract_title(text: str, file_path: Path) -> str:
    """Pull a short title from the post - first markdown heading or first line."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Markdown heading
        if stripped.startswith("#"):
            return stripped.lstrip("# ").strip()[:120]
        # Plain first non-empty line
        return stripped[:120]
    return file_path.stem


# --- Public API -----------------------------------------------------
def check_post_similarity(
    new_text: str,
    platform: str,
    top_n: int = DEFAULT_TOP_N,
) -> dict:
    """
    Compare a new post against the most recent posts on the same platform.

    Uses Jaccard similarity on word 3-grams (no LLM, no external deps).

    Returns a dict with:
      - max_similarity: float 0-1
      - most_similar: {"file": str, "similarity": float, "title": str} | None
      - flagged: bool (True if max_similarity > SIMILARITY_THRESHOLD)
      - warnings: list[str] (human-readable notes about what overlaps)
    """
    result = {
        "max_similarity": 0.0,
        "most_similar": None,
        "flagged": False,
        "warnings": [],
    }

    if platform not in PLATFORM_GLOBS:
        result["warnings"].append(
            f"Unknown platform '{platform}'; skipping similarity check."
        )
        return result

    new_tokens = _tokenize(new_text)
    new_ngrams = _ngrams(new_tokens)
    if not new_ngrams:
        result["warnings"].append("New post too short to compute 3-grams.")
        return result

    recent_files = _collect_recent_posts(platform, top_n)
    if not recent_files:
        result["warnings"].append(
            f"No prior {platform} posts found for comparison."
        )
        return result

    # Build n-gram sets for every past post.
    past_ngram_sets: List[Tuple[Path, Set[Tuple[str, ...]], str]] = []
    ngram_doc_counts: Dict[Tuple[str, ...], int] = {}
    for path in recent_files:
        text = _read_file_safe(path)
        if not text.strip():
            continue
        tokens = _tokenize(text)
        grams = _ngrams(tokens)
        if not grams:
            continue
        title = _extract_title(text, path)
        past_ngram_sets.append((path, grams, title))
        for g in grams:
            ngram_doc_counts[g] = ngram_doc_counts.get(g, 0) + 1

    if not past_ngram_sets:
        result["warnings"].append("Prior posts exist but all were empty.")
        return result

    # Identify n-grams that appear in > COMMON_NGRAM_FRACTION of past posts.
    total_past = len(past_ngram_sets)
    threshold_count = total_past * COMMON_NGRAM_FRACTION
    common_ngrams = {
        g for g, c in ngram_doc_counts.items() if c > threshold_count
    }

    # Filter common n-grams out of both sides before comparison.
    new_filtered = new_ngrams - common_ngrams

    best_sim = 0.0
    best_entry: Optional[Tuple[Path, Set[Tuple[str, ...]], str]] = None
    best_overlap: Set[Tuple[str, ...]] = set()

    for path, grams, title in past_ngram_sets:
        past_filtered = grams - common_ngrams
        sim = _jaccard(new_filtered, past_filtered)
        if sim > best_sim:
            best_sim = sim
            best_entry = (path, grams, title)
            best_overlap = new_filtered & past_filtered

    result["max_similarity"] = round(best_sim, 4)

    if best_entry is not None:
        best_path, _, best_title = best_entry
        try:
            rel = str(best_path.relative_to(BASE_DIR))
        except ValueError:
            rel = str(best_path)
        result["most_similar"] = {
            "file": rel,
            "similarity": round(best_sim, 4),
            "title": best_title,
        }

    # Threshold + warnings
    if best_sim > SIMILARITY_THRESHOLD:
        result["flagged"] = True
        if result["most_similar"]:
            result["warnings"].append(
                f"High overlap ({best_sim:.0%}) with "
                f"'{result['most_similar']['title']}' "
                f"({result['most_similar']['file']})"
            )

        # Surface the top repeating n-grams so the user knows WHAT repeats.
        # Rank by rarity across past posts (rarer = more meaningful signal).
        ranked_overlap = sorted(
            best_overlap,
            key=lambda g: ngram_doc_counts.get(g, 0),
        )[:5]
        for gram in ranked_overlap:
            phrase = " ".join(gram)
            result["warnings"].append(f'Repeating phrase: "{phrase}"')

    return result


# --- CLI test harness -----------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3:
        plat = sys.argv[1]
        text = Path(sys.argv[2]).read_text(encoding="utf-8", errors="ignore")
        out = check_post_similarity(text, platform=plat)
        print(f"max_similarity: {out['max_similarity']}")
        print(f"flagged: {out['flagged']}")
        print(f"most_similar: {out['most_similar']}")
        for w in out["warnings"]:
            print(f"  - {w}")
    else:
        print("Usage: python similarity_checker.py <linkedin|blog|podcast> <path-to-draft>")
