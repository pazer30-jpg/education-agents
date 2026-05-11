"""
embeddings.py — Semantic embeddings for Moki.

Multilingual sentence embeddings (Hebrew + English) — replaces keyword-based
matching across the pipeline (similarity_checker, corpus_patterns, coverage_map, etc.).

Model: paraphrase-multilingual-MiniLM-L12-v2 (~120MB, Hebrew + 50 langs).
Local — no API calls, $0 cost.

Cache: embeddings persisted to output/embeddings_cache.json (text → vector).
"""

import json
import hashlib
from pathlib import Path
from typing import Iterable

from config import OUTPUT_DIR

CACHE_FILE = OUTPUT_DIR / "embeddings_cache.json"
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

# Lazy-loaded singleton
_model = None
_cache: dict[str, list[float]] | None = None


def _get_model():
    """Lazy-load the model (heavy import, only when needed)."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        print(f"  [embeddings] Loading {MODEL_NAME} (first time may take ~30s)...")
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def _load_cache() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    if CACHE_FILE.exists():
        try:
            _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}
    else:
        _cache = {}
    return _cache


def _save_cache():
    if _cache is None:
        return
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")


def _hash(text: str) -> str:
    """Stable cache key — first 80 chars of text + sha256 prefix."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def embed(text: str) -> list[float]:
    """Return embedding vector for text. Cached."""
    if not text or not text.strip():
        return []
    cache = _load_cache()
    key = _hash(text)
    if key in cache:
        return cache[key]
    model = _get_model()
    vec = model.encode(text, convert_to_numpy=False, show_progress_bar=False)
    # Convert to list of floats for JSON
    if hasattr(vec, "tolist"):
        vec = vec.tolist()
    cache[key] = vec
    return vec


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed multiple texts. Uses cache for already-seen items."""
    cache = _load_cache()
    results = [None] * len(texts)
    to_compute = []
    indices = []

    for i, t in enumerate(texts):
        if not t or not t.strip():
            results[i] = []
            continue
        key = _hash(t)
        if key in cache:
            results[i] = cache[key]
        else:
            to_compute.append(t)
            indices.append(i)

    if to_compute:
        model = _get_model()
        vecs = model.encode(to_compute, convert_to_numpy=False, show_progress_bar=False)
        for idx, t, v in zip(indices, to_compute, vecs):
            v_list = v.tolist() if hasattr(v, "tolist") else list(v)
            cache[_hash(t)] = v_list
            results[idx] = v_list
        _save_cache()

    return results


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in pure Python (no numpy needed for callers)."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def similarity(text_a: str, text_b: str) -> float:
    """Direct similarity between two texts (0-1)."""
    a = embed(text_a)
    b = embed(text_b)
    return cosine_similarity(a, b)


def find_similar(query: str, corpus: Iterable[str], top_k: int = 5) -> list[dict]:
    """
    Find top-K most similar texts in corpus.
    Returns: [{"text": str, "similarity": float, "rank": int}, ...]
    """
    corpus = list(corpus)
    if not corpus:
        return []
    query_vec = embed(query)
    corpus_vecs = embed_batch(corpus)
    scored = [
        (cosine_similarity(query_vec, v), text)
        for v, text in zip(corpus_vecs, corpus) if v
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    _save_cache()
    return [
        {"text": text, "similarity": round(score, 3), "rank": i + 1}
        for i, (score, text) in enumerate(scored[:top_k])
    ]


def cluster_similar(texts: list[str], threshold: float = 0.7) -> list[list[int]]:
    """
    Group similar texts into clusters.
    Returns: list of clusters, each = list of indices in original `texts`.
    Useful for: "which papers are talking about the same thing?"
    """
    if not texts:
        return []
    vecs = embed_batch(texts)
    n = len(texts)
    visited = [False] * n
    clusters = []

    for i in range(n):
        if visited[i] or not vecs[i]:
            continue
        cluster = [i]
        visited[i] = True
        for j in range(i + 1, n):
            if visited[j] or not vecs[j]:
                continue
            if cosine_similarity(vecs[i], vecs[j]) >= threshold:
                cluster.append(j)
                visited[j] = True
        clusters.append(cluster)

    _save_cache()
    return clusters


# ─────────────────────────────────────────────
# CLI for testing
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        # Cross-language demo
        pairs = [
            ("trauma-informed care", "טיפול מודע טראומה"),
            ("youth movements in Israel", "תנועות נוער בישראל"),
            ("Buber's I-Thou", "האני-אתה של בובר"),
            ("trauma-informed care", "machine learning"),  # control: should be low
        ]
        print(f"Testing cross-language similarity ({MODEL_NAME}):\n")
        for a, b in pairs:
            sim = similarity(a, b)
            icon = "✅" if sim > 0.6 else "⚠️" if sim > 0.4 else "❌"
            print(f"  {icon} {sim:.3f}  '{a}'  ↔  '{b}'")
    elif len(sys.argv) > 2:
        a, b = sys.argv[1], sys.argv[2]
        print(f"similarity: {similarity(a, b):.3f}")
    else:
        print("Usage: python3 embeddings.py --demo  |  <text_a> <text_b>")
