"""
arc_tracker.py — Long-form Arc Tracker for Moki.

Currently Moki publishes 30 unconnected posts. This module turns the corpus
into "developing theses" — multi-post journeys (10-15 posts) on a connected
theme where each post builds on the last and references prior posts naturally
("כפי שטענתי בפוסט הקודם על X...").

Concept: an *arc* is an ordered sequence of posts grouped by semantic
similarity and tagged with stages — each stage progresses an argument:

    Arc: "What is belonging?"
    ├─ Stage 1 — Definition / opening    (≈5 posts)
    ├─ Stage 2 — How to build it         (≈4 posts)
    ├─ Stage 3 — When it fails           (≈3 posts)
    └─ Stage 4 — Conclusion              (≈1-2 posts)

Public API:
    detect_arcs()                 → list[dict]
    current_arc_status()          → dict
    propose_next_post(arc_id)     → dict
    format_previous_posts_block(arc_id, max_posts=4) → str
    format_arc_status_for_chat()  → str

Storage:
    output/_arcs/arc_<n>.json     — manifest per arc (ordered posts + thesis)

Pure Python — uses embeddings.py for similarity, no new LLM calls.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from config import OUTPUT_DIR, LINKEDIN_DIR, BLOG_DIR
from embeddings import embed, cosine_similarity


# ─────────────────────────────────────────────
# Constants — tunable knobs
# ─────────────────────────────────────────────

ARCS_DIR = OUTPUT_DIR / "_arcs"
ARCS_DIR.mkdir(parents=True, exist_ok=True)

# Min/max posts per arc — anything outside is treated as "not yet an arc" or
# "already concluded" and ignored in current-status calculations.
MIN_POSTS_PER_ARC = 3
MAX_POSTS_PER_ARC = 15
TARGET_ARC_LENGTH = 12  # midpoint of the 10-15 sweet spot from the spec

# Cluster threshold — posts sharing this cosine sim or higher belong to same arc.
# Calibrated empirically: 0.55 catches "same theme, different angles" without
# bleeding two unrelated topics together. Tweak if arcs come out too granular
# or too coarse.
ARC_SIMILARITY_THRESHOLD = 0.55

# Stage labels — ordered. We progress posts through stages by position
# within the arc. The breakdown is heuristic (early/middle/late thirds + tail).
STAGE_LABELS = [
    ("opening",      "פתיחה — מה זה X?"),
    ("building",     "בנייה — איך עושים את X?"),
    ("complication", "מורכבות — מתי X נכשל?"),
    ("conclusion",   "סיכום — מה למדנו על X?"),
]


# ─────────────────────────────────────────────
# Post discovery + parsing
# ─────────────────────────────────────────────

@dataclass
class Post:
    """A single Moki post — minimal metadata for arc clustering."""
    path: Path
    platform: str          # "linkedin" / "blog"
    title: str             # extracted from front-matter or first line
    body: str              # main body text (front-matter stripped)
    date: str              # YYYY-MM-DD or extracted from filename
    mtime: float           # for stable ordering when dates collide

    @property
    def post_id(self) -> str:
        """Stable id — filename stem without extensions/timestamps."""
        return self.path.stem


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_TITLE_RE       = re.compile(r'^title:\s*"?(.+?)"?\s*$', re.MULTILINE)
_DATE_RE        = re.compile(r'^date:\s*"?(\d{4}-\d{2}-\d{2})"?', re.MULTILINE)
_FILENAME_DATE  = re.compile(r"(\d{8})_\d{4}")  # 20260507_0853


def _parse_post(path: Path, platform: str) -> Post | None:
    """Read a post file and extract title/body/date. Returns None on failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    if not text.strip():
        return None

    # Pull front-matter if present
    title = ""
    date = ""
    body = text
    m = _FRONTMATTER_RE.match(text)
    if m:
        fm = m.group(1)
        body = text[m.end():]
        tm = _TITLE_RE.search(fm)
        if tm:
            title = tm.group(1).strip()
        dm = _DATE_RE.search(fm)
        if dm:
            date = dm.group(1)

    # Fallback title — first non-empty line
    if not title:
        for line in body.splitlines():
            line = line.strip()
            if line:
                title = line[:120]
                break

    # Fallback date — pull from filename
    if not date:
        fm_date = _FILENAME_DATE.search(path.name)
        if fm_date:
            try:
                d = datetime.strptime(fm_date.group(1), "%Y%m%d")
                date = d.strftime("%Y-%m-%d")
            except ValueError:
                pass
    if not date:
        try:
            date = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
        except Exception:
            date = ""

    try:
        mtime = path.stat().st_mtime
    except Exception:
        mtime = 0.0

    return Post(
        path=path,
        platform=platform,
        title=title or path.stem,
        body=body.strip(),
        date=date,
        mtime=mtime,
    )


def _is_canonical_post(path: Path) -> bool:
    """Filter out backups, briefing scratch, and English-only variants.

    We want one representative per logical post. Heuristics:
      - skip *.bak
      - skip files containing '_briefing_' (intermediate)
      - skip '_en_' variants if there's a Hebrew sibling
      - skip files with 'B_' (A/B test variants — keep A)
    """
    name = path.name
    if name.endswith(".bak"):
        return False
    if "_briefing_" in name:
        return False
    if "_linkedin_B_" in name:
        return False
    return True


def _collect_posts() -> list[Post]:
    """Scan posts/ directories and return canonical Post objects."""
    posts: list[Post] = []

    # LinkedIn — *.txt
    if LINKEDIN_DIR.exists():
        for p in LINKEDIN_DIR.glob("*.txt"):
            if _is_canonical_post(p):
                pp = _parse_post(p, "linkedin")
                if pp and len(pp.body) > 50:
                    posts.append(pp)

    # Blog — *.md
    if BLOG_DIR.exists():
        for p in BLOG_DIR.glob("*.md"):
            if _is_canonical_post(p):
                pp = _parse_post(p, "blog")
                if pp and len(pp.body) > 50:
                    posts.append(pp)

    # Sort newest-first by mtime — recent activity matters most for "current arc"
    posts.sort(key=lambda x: x.mtime, reverse=True)
    return posts


# ─────────────────────────────────────────────
# Clustering — group posts into arcs
# ─────────────────────────────────────────────

def _embed_post(post: Post) -> list[float]:
    """Embed a post — uses title + first 600 chars of body (signal-rich part)."""
    text = (post.title + ". " + post.body[:600]).strip()
    return embed(text)


def _cluster_into_arcs(posts: list[Post],
                       threshold: float = ARC_SIMILARITY_THRESHOLD
                       ) -> list[list[int]]:
    """Greedy clustering by cosine similarity.

    Each post joins the first existing cluster whose centroid (= first member)
    is at least `threshold` similar. Otherwise starts its own cluster.

    Returns: list of clusters, each a list of indices into `posts`.
    """
    if not posts:
        return []

    vecs = [_embed_post(p) for p in posts]
    clusters: list[list[int]] = []
    centroids: list[list[float]] = []

    for i, v in enumerate(vecs):
        if not v:
            continue
        placed = False
        for c_idx, centroid in enumerate(centroids):
            if cosine_similarity(v, centroid) >= threshold:
                clusters[c_idx].append(i)
                placed = True
                break
        if not placed:
            clusters.append([i])
            centroids.append(v)

    return clusters


def _derive_anchor_theme(posts: list[Post]) -> str:
    """Pick a representative theme phrase from a cluster of posts.

    Strategy: take the title of the most recent post in the cluster, trimmed.
    A heavier approach (e.g. extract noun phrases, top-tf terms) is plausible
    but the title is human-curated and already captures the angle well.
    """
    if not posts:
        return ""
    most_recent = max(posts, key=lambda p: p.mtime)
    return most_recent.title.strip()


def _stage_for_position(position: int, total: int) -> str:
    """Map a 0-indexed position in an arc to a stage label.

    Heuristic split:
      - first 40% → opening
      - next 30%  → building
      - next 20%  → complication
      - last 10%  → conclusion
    """
    if total <= 0:
        return STAGE_LABELS[0][0]
    pct = position / total
    if pct < 0.40:
        return STAGE_LABELS[0][0]
    if pct < 0.70:
        return STAGE_LABELS[1][0]
    if pct < 0.90:
        return STAGE_LABELS[2][0]
    return STAGE_LABELS[3][0]


# ─────────────────────────────────────────────
# Arc manifest — disk format
# ─────────────────────────────────────────────

def _arc_path(arc_id: str) -> Path:
    return ARCS_DIR / f"{arc_id}.json"


def _serialize_arc(arc_id: str, theme: str, posts: list[Post]) -> dict:
    """Build the JSON manifest for an arc."""
    # Order by date asc (oldest first) — arcs read chronologically
    ordered = sorted(posts, key=lambda p: p.mtime)
    total = len(ordered)
    return {
        "arc_id": arc_id,
        "anchor_theme": theme,
        "thesis": theme,  # initial thesis = anchor theme; can be overwritten
        "post_count": total,
        "target_length": TARGET_ARC_LENGTH,
        "is_complete": total >= TARGET_ARC_LENGTH,
        "last_updated": datetime.now().isoformat(timespec="seconds"),
        "posts": [
            {
                "post_id":  p.post_id,
                "title":    p.title,
                "platform": p.platform,
                "date":     p.date,
                "path":     str(p.path),
                "stage":    _stage_for_position(i, total),
                "position": i + 1,
            }
            for i, p in enumerate(ordered)
        ],
    }


def _save_arc(arc_id: str, manifest: dict) -> Path:
    out = _arc_path(arc_id)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return out


def _load_arc(arc_id: str) -> dict | None:
    p = _arc_path(arc_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_all_arcs() -> list[dict]:
    out = []
    for f in sorted(ARCS_DIR.glob("arc_*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


# ─────────────────────────────────────────────
# Public: detect_arcs
# ─────────────────────────────────────────────

def detect_arcs(min_size: int = MIN_POSTS_PER_ARC,
                threshold: float = ARC_SIMILARITY_THRESHOLD,
                persist: bool = True,
                ) -> list[dict]:
    """Cluster the post corpus into arcs and persist manifests.

    Args:
        min_size: discard clusters smaller than this (not arcs, just one-offs)
        threshold: cosine similarity required to merge into an existing cluster
        persist: write arc_<n>.json manifests to disk

    Returns:
        list of arc manifests (newest-first by anchor post mtime).
    """
    posts = _collect_posts()
    if not posts:
        return []

    clusters = _cluster_into_arcs(posts, threshold=threshold)

    arcs = []
    arc_idx = 1
    # Sort clusters by recency of newest member — newest arc = arc_1
    clusters.sort(
        key=lambda c: max(posts[i].mtime for i in c) if c else 0,
        reverse=True,
    )

    for cluster in clusters:
        if len(cluster) < min_size:
            continue
        cluster_posts = [posts[i] for i in cluster][:MAX_POSTS_PER_ARC]
        arc_id = f"arc_{arc_idx:02d}"
        theme = _derive_anchor_theme(cluster_posts)
        manifest = _serialize_arc(arc_id, theme, cluster_posts)
        if persist:
            _save_arc(arc_id, manifest)
        arcs.append(manifest)
        arc_idx += 1

    return arcs


# ─────────────────────────────────────────────
# Public: current_arc_status
# ─────────────────────────────────────────────

def current_arc_status() -> dict:
    """Find the active arc and report what stage we're in.

    "Active" = the arc whose most recent post is newest overall AND that
    isn't yet at TARGET_ARC_LENGTH posts. If every arc is complete, returns
    a "needs_new_arc" flag.

    Returns: {
        "active_arc_id":    str | None,
        "anchor_theme":     str,
        "current_stage":    str,
        "next_stage":       str,
        "posts_in_arc":     int,
        "posts_remaining":  int,
        "next_logical_post": str,    # human-readable suggestion
        "needs_new_arc":    bool,
    }
    """
    arcs = _load_all_arcs()
    # Auto-detect if nothing on disk yet — convenience for first-time use
    if not arcs:
        arcs = detect_arcs()
    if not arcs:
        return {
            "active_arc_id": None,
            "anchor_theme": "",
            "current_stage": "",
            "next_stage": "",
            "posts_in_arc": 0,
            "posts_remaining": TARGET_ARC_LENGTH,
            "next_logical_post": "אין עדיין פוסטים לקבץ לקשת.",
            "needs_new_arc": True,
        }

    # Pick most-recently-active arc that isn't full
    def _arc_recency(arc: dict) -> float:
        try:
            return max(
                datetime.fromisoformat(p["date"]).timestamp()
                if p.get("date") else 0
                for p in arc.get("posts", [])
            )
        except Exception:
            return 0

    incomplete = [a for a in arcs if not a.get("is_complete")]
    if not incomplete:
        return {
            "active_arc_id": None,
            "anchor_theme": "",
            "current_stage": "",
            "next_stage": "",
            "posts_in_arc": 0,
            "posts_remaining": 0,
            "next_logical_post": (
                "כל הקשתות הקיימות הושלמו — שווה להתחיל קשת חדשה."
            ),
            "needs_new_arc": True,
        }

    active = max(incomplete, key=_arc_recency)
    posts_so_far = active.get("post_count", 0)
    last_stage = (
        active["posts"][-1]["stage"] if active.get("posts") else "opening"
    )
    next_stage = _stage_for_position(posts_so_far, TARGET_ARC_LENGTH)
    stage_he = dict(STAGE_LABELS).get(next_stage, next_stage)

    next_logical = (
        f"הפוסט הבא בקשת '{active['anchor_theme']}' — "
        f"שלב: {stage_he} ({posts_so_far + 1}/{TARGET_ARC_LENGTH})"
    )

    return {
        "active_arc_id":     active["arc_id"],
        "anchor_theme":      active["anchor_theme"],
        "current_stage":     last_stage,
        "next_stage":        next_stage,
        "posts_in_arc":      posts_so_far,
        "posts_remaining":   max(0, TARGET_ARC_LENGTH - posts_so_far),
        "next_logical_post": next_logical,
        "needs_new_arc":     False,
    }


# ─────────────────────────────────────────────
# Public: propose_next_post
# ─────────────────────────────────────────────

def propose_next_post(arc_id: str) -> dict:
    """Given an arc and its history, suggest the next post.

    Returns:
        {
          "thesis":              str,
          "references_to_make":  [{"post": str, "as": str}, ...],
          "arc_position":        str,
        }

    The "as" field tells Agent 3 *how* to reference the prior post — e.g.
    "כפי שטענתי בפוסט הקודם על X" — so the LLM can integrate it naturally.
    """
    arc = _load_arc(arc_id)
    if not arc:
        return {
            "thesis": "",
            "references_to_make": [],
            "arc_position": "",
            "error": f"arc {arc_id} not found",
        }

    posts = arc.get("posts", [])
    n_existing = len(posts)
    next_position = n_existing + 1
    next_stage = _stage_for_position(n_existing, TARGET_ARC_LENGTH)
    stage_he = dict(STAGE_LABELS).get(next_stage, next_stage)
    theme = arc.get("anchor_theme", "")

    # Stage-shaped thesis hint — drives what the next post should *do*
    thesis_by_stage = {
        "opening":      f"הגדר את '{theme}' מזווית חדשה — מה זה בעצם?",
        "building":     f"איך בונים '{theme}' בשטח? — שיטה / כלי / תרגול.",
        "complication": f"מתי '{theme}' נכשל? — מקרה גבול / סתירה / כישלון.",
        "conclusion":   f"מה למדנו במסע על '{theme}'? — סינתזה של הקשת.",
    }
    thesis = thesis_by_stage.get(next_stage, theme)

    # References — pick up to 2 prior posts, framed as natural callbacks.
    # Strategy: always reference the *immediate previous* post (continuity),
    # plus the arc-opener (anchor) if it's not the same post.
    refs: list[dict] = []
    if posts:
        prev = posts[-1]
        refs.append({
            "post": prev["title"],
            "as":   f"כפי שטענתי בפוסט הקודם על '{prev['title']}'...",
        })
    if len(posts) >= 3:
        opener = posts[0]
        refs.append({
            "post": opener["title"],
            "as":   f"בתחילת הקשת שאלתי '{opener['title']}' — עכשיו אנחנו מעמיקים.",
        })

    return {
        "thesis":              thesis,
        "references_to_make":  refs,
        "arc_position":        f"{next_position}/{TARGET_ARC_LENGTH} — {stage_he}",
        "arc_id":              arc_id,
        "anchor_theme":        theme,
        "next_stage":          next_stage,
    }


# ─────────────────────────────────────────────
# Wiring helpers — used by Agent 0 (planner) + Agent 3 (writer)
# ─────────────────────────────────────────────

def has_unfinished_arc() -> bool:
    """Quick check for the planner — should we surface an arc-post option?"""
    status = current_arc_status()
    return bool(status.get("active_arc_id")) and not status.get("needs_new_arc")


def planner_arc_option() -> dict | None:
    """Returns a 4th-option dict for Agent 0 alongside its 3 trending topics.

    Shape mirrors the existing planner topic format so it can be slotted in:
        {"topic": str, "subtopics": [...], "angle": "arc_continuation",
         "research_question": str, "arc_id": str, "is_arc_post": True}

    Returns None if there's no unfinished arc.
    """
    status = current_arc_status()
    if status.get("needs_new_arc") or not status.get("active_arc_id"):
        return None
    arc_id = status["active_arc_id"]
    proposal = propose_next_post(arc_id)
    return {
        "topic":              status["anchor_theme"],
        "subtopics":          [],
        "angle":              "arc_continuation",
        "research_question":  proposal["thesis"],
        "arc_id":             arc_id,
        "arc_position":       proposal["arc_position"],
        "is_arc_post":        True,
    }


def format_previous_posts_block(arc_id: str, max_posts: int = 4) -> str:
    """Render a "PREVIOUS POSTS IN ARC" block to inject into Agent 3 prompts.

    Limits to last `max_posts` so the prompt doesn't balloon. Includes title
    + a 250-char snippet of the body so the LLM can reference content,
    not just titles.

    Returns "" if arc not found.
    """
    arc = _load_arc(arc_id)
    if not arc:
        return ""
    posts = arc.get("posts", [])
    if not posts:
        return ""

    # Take the most recent N (chronologically: end of the list)
    recent = posts[-max_posts:]

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"PREVIOUS POSTS IN ARC — '{arc.get('anchor_theme', '')}'",
        f"(this is post {len(posts) + 1}/{arc.get('target_length', TARGET_ARC_LENGTH)})",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "הפוסט הזה הוא חלק מקשת (מסע רב-פוסטים) שכבר התחילה.",
        "תרגיש חופשי להפנות לפוסטים קודמים בצורה טבעית —",
        "למשל: 'כפי שטענתי בפוסט הקודם על X...' — כדי לבנות חוט מחשבה מתפתח.",
        "",
    ]
    for i, p in enumerate(recent, 1):
        snippet = ""
        try:
            text = Path(p["path"]).read_text(encoding="utf-8")
            # Skip front-matter to get to body
            m = _FRONTMATTER_RE.match(text)
            if m:
                text = text[m.end():]
            snippet = text.strip()[:250].replace("\n", " ")
        except Exception:
            pass
        lines.append(f"{i}. [{p.get('stage', '?')}] {p.get('title', '')}")
        if snippet:
            lines.append(f"   ↳ {snippet}...")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_arc_status_for_chat() -> str:
    """Human-readable arc status — used by the 'קשת' / 'arc' chat command."""
    status = current_arc_status()

    if status.get("needs_new_arc"):
        if status.get("posts_in_arc", 0) == 0:
            return ("📚 אין עדיין קשתות. הרץ 'detect_arcs' או צבר עוד פוסטים — "
                    "הקבצנו מצריך לפחות 3 פוסטים בנושא קרוב כדי לזהות קשת.")
        return ("📚 כל הקשתות הקיימות הושלמו. "
                "שווה להתחיל קשת חדשה — הצע נושא ב-`רעיונות`.")

    arc_id = status["active_arc_id"]
    proposal = propose_next_post(arc_id)
    refs = proposal.get("references_to_make", [])

    lines = [
        f"🎯 קשת פעילה: {arc_id}",
        f"   עוגן: {status['anchor_theme']}",
        f"   התקדמות: {status['posts_in_arc']}/{TARGET_ARC_LENGTH}",
        f"   שלב נוכחי → הבא: {status['current_stage']} → {status['next_stage']}",
        "",
        "📝 הפוסט הבא המומלץ:",
        f"   טענה: {proposal['thesis']}",
        f"   מיקום: {proposal['arc_position']}",
    ]
    if refs:
        lines.append("   הפניות לפוסטים קודמים:")
        for r in refs:
            lines.append(f"     • {r['as']}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# CLI entry point — quick sanity check
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "detect":
        arcs = detect_arcs()
        print(f"Detected {len(arcs)} arcs.")
        for a in arcs:
            print(f"  {a['arc_id']:8s} ({a['post_count']:2d} posts)  "
                  f"{a['anchor_theme'][:60]}")
    elif cmd == "status":
        print(format_arc_status_for_chat())
    elif cmd == "propose" and len(sys.argv) >= 3:
        arc_id = sys.argv[2]
        result = propose_next_post(arc_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("Usage: arc_tracker.py [detect | status | propose <arc_id>]")
