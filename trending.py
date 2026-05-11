"""
Trending topics watcher for Moki.
Scans public sources (Hacker News, Reddit r/education, arXiv cs.CY) for currently-trending
education topics so the planner can react to what's hot — not just historical research.

All sources are free and require no API keys. Each source is wrapped in try/except so a
failure in one does not break the others. Results are cached to output/trending_cache.json
for 24h to avoid re-fetching.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional

from config import OUTPUT_DIR

# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────

CACHE_PATH = OUTPUT_DIR / "trending_cache.json"
CACHE_TTL_HOURS = 24
HTTP_TIMEOUT = 15
USER_AGENT = "MokiTrendingBot/1.0 (education-agents; contact: pazer30@gmail.com)"

# Skip items whose title contains any of these tokens (lowercased substring match).
BLOCKLIST_TERMS = [
    "spam",
    "advertisement",
    "bitcoin",
    "crypto",
    "nsfw",
]

# Items whose title (or snippet) contains any of these get a relevance bonus.
RELEVANT_TERMS = [
    "youth",
    "learning",
    "pedagogy",
    "mentoring",
    "teaching",
    "identity",
    "community",
]


# ─────────────────────────────────────────────
# HTTP helper
# ─────────────────────────────────────────────

def _http_get(url: str, accept: str = "application/json") -> Optional[bytes]:
    """GET with timeout + User-Agent. Returns bytes or None on failure."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        print(f"  [trending] HTTP error for {url[:80]}...: {e}")
        return None


# ─────────────────────────────────────────────
# Filtering / scoring
# ─────────────────────────────────────────────

def _is_blocked(title: str, snippet: str = "") -> bool:
    text = f"{title} {snippet}".lower()
    return any(term in text for term in BLOCKLIST_TERMS)


def _relevance_bonus(title: str, snippet: str = "") -> int:
    text = f"{title} {snippet}".lower()
    return sum(5 for term in RELEVANT_TERMS if term in text)


# ─────────────────────────────────────────────
# Source: Hacker News (Algolia search)
# ─────────────────────────────────────────────

def _fetch_hn(days: int, limit: int = 5) -> list[dict]:
    cutoff = int((datetime.utcnow() - timedelta(days=days)).timestamp())
    url = (
        "https://hn.algolia.com/api/v1/search"
        f"?query=education&tags=story&numericFilters=created_at_i>{cutoff}"
    )
    raw = _http_get(url, accept="application/json")
    if not raw:
        return []
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"  [trending] HN JSON parse error: {e}")
        return []

    hits = data.get("hits") or []
    items: list[dict] = []
    for hit in hits:
        title = (hit.get("title") or hit.get("story_title") or "").strip()
        if not title:
            continue
        snippet = ""  # HN search doesn't return a story snippet
        if _is_blocked(title, snippet):
            continue
        story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        points = int(hit.get("points") or 0)
        comments = int(hit.get("num_comments") or 0)
        score = points + comments + _relevance_bonus(title, snippet)
        items.append({
            "title": title,
            "source": "hackernews",
            "url": story_url,
            "score": score,
            "snippet": snippet,
        })

    items.sort(key=lambda x: x["score"], reverse=True)
    return items[:limit]


# ─────────────────────────────────────────────
# Source: Reddit r/education
# ─────────────────────────────────────────────

def _fetch_reddit(limit: int = 5) -> list[dict]:
    url = "https://www.reddit.com/r/education/top.json?t=week&limit=10"
    raw = _http_get(url, accept="application/json")
    if not raw:
        return []
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"  [trending] Reddit JSON parse error: {e}")
        return []

    children = (data.get("data") or {}).get("children") or []
    items: list[dict] = []
    for child in children:
        post = (child or {}).get("data") or {}
        title = (post.get("title") or "").strip()
        if not title:
            continue
        snippet = (post.get("selftext") or "").strip()[:280]
        if _is_blocked(title, snippet):
            continue
        permalink = post.get("permalink") or ""
        post_url = f"https://www.reddit.com{permalink}" if permalink else (post.get("url") or "")
        ups = int(post.get("ups") or 0)
        comments = int(post.get("num_comments") or 0)
        score = ups + comments + _relevance_bonus(title, snippet)
        items.append({
            "title": title,
            "source": "reddit",
            "url": post_url,
            "score": score,
            "snippet": snippet,
        })

    items.sort(key=lambda x: x["score"], reverse=True)
    return items[:limit]


# ─────────────────────────────────────────────
# Source: arXiv (cs.CY + education)
# ─────────────────────────────────────────────

def _fetch_arxiv(limit: int = 5) -> list[dict]:
    url = (
        "http://export.arxiv.org/api/query"
        "?search_query=cat:cs.CY+AND+education"
        "&sortBy=submittedDate&sortOrder=descending&max_results=10"
    )
    raw = _http_get(url, accept="application/atom+xml")
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        print(f"  [trending] arXiv XML parse error: {e}")
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items: list[dict] = []
    for entry in root.findall("atom:entry", ns):
        title_el = entry.find("atom:title", ns)
        title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""
        if not title:
            continue
        summary_el = entry.find("atom:summary", ns)
        snippet = (summary_el.text or "").strip().replace("\n", " ")[:280] if summary_el is not None else ""
        if _is_blocked(title, snippet):
            continue
        link = ""
        for link_el in entry.findall("atom:link", ns):
            if link_el.get("rel") == "alternate" or link_el.get("type") == "text/html":
                link = link_el.get("href") or ""
                break
        if not link:
            id_el = entry.find("atom:id", ns)
            link = (id_el.text or "").strip() if id_el is not None else ""

        # Recency score: newer = higher. Uses submitted/published date.
        recency_score = 0
        published_el = entry.find("atom:published", ns)
        if published_el is not None and published_el.text:
            try:
                pub = datetime.strptime(published_el.text.strip()[:10], "%Y-%m-%d")
                age_days = max(0, (datetime.utcnow() - pub).days)
                recency_score = max(0, 30 - age_days)  # 30 down to 0 over a month
            except ValueError:
                pass

        score = recency_score + _relevance_bonus(title, snippet)
        items.append({
            "title": title,
            "source": "arxiv",
            "url": link,
            "score": score,
            "snippet": snippet,
        })

    items.sort(key=lambda x: x["score"], reverse=True)
    return items[:limit]


# ─────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────

def _read_cache() -> Optional[dict]:
    if not CACHE_PATH.exists():
        return None
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    ts = data.get("fetched_at")
    if not ts:
        return None
    try:
        fetched_at = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if datetime.utcnow() - fetched_at > timedelta(hours=CACHE_TTL_HOURS):
        return None
    return data


def _write_cache(items: list[dict]) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": datetime.utcnow().isoformat(timespec="seconds"),
            "items": items,
        }
        CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"  [trending] cache write failed: {e}")


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def fetch_trending_topics(max_topics: int = 10, days: int = 7) -> list[dict]:
    """
    Fetch trending education topics from public sources (no API keys).
    Returns: [{"title": str, "source": str, "url": str, "score": int, "snippet": str}, ...]

    Caches results for 24h to output/trending_cache.json.
    """
    cached = _read_cache()
    if cached is not None:
        items = cached.get("items") or []
        return items[:max_topics]

    all_items: list[dict] = []

    # Each source isolated — failure in one shouldn't kill the others.
    for fetcher_name, fetcher in (
        ("hackernews", lambda: _fetch_hn(days=days, limit=5)),
        ("reddit", lambda: _fetch_reddit(limit=5)),
        ("arxiv", lambda: _fetch_arxiv(limit=5)),
    ):
        try:
            sourced = fetcher()
            all_items.extend(sourced)
            print(f"  [trending] {fetcher_name}: {len(sourced)} items")
        except Exception as e:
            print(f"  [trending] {fetcher_name} failed: {e}")

    # Sort by score across all sources, return top N.
    all_items.sort(key=lambda x: x.get("score", 0), reverse=True)
    _write_cache(all_items)
    return all_items[:max_topics]


if __name__ == "__main__":
    # Manual smoke test
    topics = fetch_trending_topics(max_topics=10)
    print(f"\nFetched {len(topics)} trending items:\n")
    for t in topics:
        print(f"  [{t['source']:10s}] score={t['score']:>4d}  {t['title'][:90]}")
        if t.get("url"):
            print(f"               {t['url']}")
