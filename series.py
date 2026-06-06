"""
series.py — Topic-series tracker.

Today, each pipeline run picks 3 fresh topics. There's no concept of a
"series" — so a great topic ("בדידות של מנהלי פנימייה") gets one article
and dies. Series mode lets the Planner detect that 2-3 recent topics share
a theme and propose the next episode in that arc, instead of always
starting from scratch.

Data model: output/_state/series.json
  {
    "series": [
      {
        "id": "loneliness-boarding-school-principals",
        "theme": "Loneliness in boarding-school principals",
        "started_at": "2026-05-23T07:00",
        "last_episode_at": "2026-06-04T07:00",
        "episodes": [
          {"topic_slug": "...", "angle": "...", "article_path": "...", "published_at": null},
          ...
        ],
        "next_angle": "what supports principals after the first crisis",
        "status": "active"  # active | paused | concluded
      }
    ]
  }

The series file is regenerated/updated on every pipeline run. Memory file
output/_memory/active_series.md is the public read surface for Writer +
Content Creator (they see "this is episode 3 of N — refer to past
episodes").

Two entry points:
  - detect_or_create_series(recent_topics) -> active_series | None
  - register_episode(series_id, topic_slug, angle, article_path)

Usage from agent0_planner:
  from series import detect_active_series, propose_next_episode
  series = detect_active_series()
  if series and series.get("next_angle"):
      # use this series + angle instead of a fresh topic
      topic_hint = series["next_angle"]
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from config import OUTPUT_DIR

SERIES_FILE = OUTPUT_DIR / "_state" / "series.json"
MEMORY_FILE = OUTPUT_DIR / "_memory" / "active_series.md"

# How recent does the last episode need to be for the series to count as "active"?
ACTIVE_WINDOW_DAYS = 21

# Minimum theme-keyword overlap to consider a topic part of an existing series
MIN_OVERLAP = 2


def _load() -> dict:
    if not SERIES_FILE.exists():
        return {"series": []}
    try:
        return json.loads(SERIES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"series": []}


def _save(data: dict) -> None:
    SERIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SERIES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")


def _slug(text: str) -> str:
    """Compact ID from a topic string."""
    text = re.sub(r"[^a-zA-Zא-ת0-9\s-]", "", (text or ""))
    return re.sub(r"\s+", "-", text.strip().lower())[:60]


def _keywords(text: str) -> set[str]:
    """4+ char English / 3+ char Hebrew tokens for theme matching."""
    text = (text or "").lower()
    en = set(re.findall(r"[a-z][a-z'\-]{3,}", text))
    he = set(re.findall(r"[א-ת][א-ת]{2,}", text))
    return en | he


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def detect_active_series() -> dict | None:
    """Return the most recently active series, or None if none qualify.

    A series is 'active' if its last_episode_at is within ACTIVE_WINDOW_DAYS
    and status != 'concluded'.
    """
    data = _load()
    cutoff = datetime.now() - timedelta(days=ACTIVE_WINDOW_DAYS)
    actives = []
    for s in data.get("series", []):
        if s.get("status") == "concluded":
            continue
        last = s.get("last_episode_at", "")
        try:
            if datetime.fromisoformat(last) < cutoff:
                continue
        except Exception:
            continue
        actives.append(s)
    if not actives:
        return None
    actives.sort(key=lambda s: s.get("last_episode_at", ""), reverse=True)
    return actives[0]


def register_episode(series_id: str, theme: str,
                     topic_slug: str, angle: str,
                     article_path: str = "") -> dict:
    """Record a new episode against a series. Creates series if missing."""
    data = _load()
    series_list = data.setdefault("series", [])
    series = next((s for s in series_list if s["id"] == series_id), None)
    now_iso = datetime.now().isoformat(timespec="seconds")
    if not series:
        series = {
            "id":              series_id,
            "theme":           theme,
            "started_at":      now_iso,
            "last_episode_at": now_iso,
            "episodes":        [],
            "next_angle":      "",
            "status":          "active",
        }
        series_list.append(series)
    series["episodes"].append({
        "topic_slug":   topic_slug,
        "angle":        angle,
        "article_path": article_path,
        "added_at":     now_iso,
    })
    series["last_episode_at"] = now_iso
    series["next_angle"] = ""  # consumed
    _save(data)
    return series


def set_next_angle(series_id: str, next_angle: str) -> None:
    """Planner sets the angle for the NEXT episode after writing the current one."""
    data = _load()
    for s in data["series"]:
        if s["id"] == series_id:
            s["next_angle"] = next_angle
            _save(data)
            return


def conclude_series(series_id: str) -> None:
    """Mark a series as finished (status=concluded). Won't trigger again."""
    data = _load()
    for s in data["series"]:
        if s["id"] == series_id:
            s["status"] = "concluded"
            s["last_episode_at"] = datetime.now().isoformat(timespec="seconds")
            _save(data)
            return


def maybe_create_series_from_recent(recent_topics: list[str],
                                    min_overlap: int = MIN_OVERLAP) -> dict | None:
    """If 2+ recent topics share keyword overlap >= min_overlap, propose a
    new series spanning them. Returns the created series, or None.
    """
    if len(recent_topics) < 2:
        return None
    # Compute pairwise overlap
    token_sets = [(t, _keywords(t)) for t in recent_topics if t]
    if len(token_sets) < 2:
        return None
    # Take the 2 most-overlapping topics
    best = (0, None, None)
    for i in range(len(token_sets)):
        for j in range(i + 1, len(token_sets)):
            ti, ki = token_sets[i]
            tj, kj = token_sets[j]
            overlap = len(ki & kj)
            if overlap > best[0]:
                best = (overlap, ti, tj)
    if best[0] < min_overlap:
        return None
    # Derive a theme from the overlapping keywords
    overlapping = _keywords(best[1]) & _keywords(best[2])
    theme_words = sorted(overlapping, key=len, reverse=True)[:3]
    theme = " ".join(theme_words) or best[1][:40]
    series_id = _slug(theme)
    # Don't create if already exists
    data = _load()
    if any(s["id"] == series_id for s in data.get("series", [])):
        return None
    # Backfill the 2 topics as episodes
    now_iso = datetime.now().isoformat(timespec="seconds")
    new_series = {
        "id":              series_id,
        "theme":           theme,
        "started_at":      now_iso,
        "last_episode_at": now_iso,
        "episodes": [
            {"topic_slug": _slug(best[1]), "angle": best[1],
             "article_path": "", "added_at": now_iso},
            {"topic_slug": _slug(best[2]), "angle": best[2],
             "article_path": "", "added_at": now_iso},
        ],
        "next_angle": "",
        "status":     "active",
        "_auto_created": True,
    }
    data.setdefault("series", []).append(new_series)
    _save(data)
    return new_series


def regenerate_memory() -> Path:
    """Rebuild output/_memory/active_series.md so Writer + Content see the
    series context in their prompts."""
    data = _load()
    active = [s for s in data.get("series", []) if s.get("status") == "active"]
    lines = [
        "---",
        "moki: true",
        "type: active_series",
        f"updated: {datetime.now().isoformat(timespec='seconds')}",
        "---",
        "",
        "# 📺 סדרות פעילות",
        "",
    ]
    if not active:
        lines.append("_אין סדרות פעילות כרגע. Planner יציע נושאים חדשים._")
    else:
        lines.append(f"> {len(active)} סדרות פעילות. Writer + Content Creator"
                     f" צריכים לקרוא את הקובץ הזה ולהתייחס לסדרה הרלוונטית.")
        lines.append("")
        for s in active:
            lines.append(f"## {s['theme']}")
            lines.append("")
            lines.append(f"- **id:** `{s['id']}`")
            lines.append(f"- **התחיל:** {s['started_at'][:10]}")
            lines.append(f"- **פרק אחרון:** {s['last_episode_at'][:10]}")
            lines.append(f"- **# פרקים:** {len(s['episodes'])}")
            if s.get("next_angle"):
                lines.append(f"- **זווית הבאה (מומלצת ע\"י Planner):** {s['next_angle']}")
            lines.append("")
            lines.append("**פרקים שכבר נכתבו:**")
            for i, ep in enumerate(s["episodes"], 1):
                lines.append(f"  {i}. {ep['angle'][:120]}")
            lines.append("")
            lines.append("**הנחיה ל-Writer:** הוסף משפט קישור לפרק קודם בסדרה "
                         "(למשל \"בפרק הקודם בחנו את X — כאן נשאל Y\"). "
                         "**אסור** לפתוח כאילו הקורא רואה את הנושא לראשונה.")
            lines.append("")
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text("\n".join(lines), encoding="utf-8")
    return MEMORY_FILE


# ─────────────────────────────────────────────
# CLI for manual use
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--regen":
        path = regenerate_memory()
        print(f"📺 {path.relative_to(OUTPUT_DIR.parent)}")
    elif len(sys.argv) > 1 and sys.argv[1] == "--list":
        data = _load()
        for s in data.get("series", []):
            n = len(s.get("episodes", []))
            print(f"  [{s['status']}] {s['id']} — {n} episodes — last: {s['last_episode_at'][:10]}")
    elif len(sys.argv) > 1 and sys.argv[1] == "--detect":
        s = detect_active_series()
        if s:
            print(f"Active: {s['theme']}  ({len(s['episodes'])} episodes)")
            if s.get("next_angle"):
                print(f"  → next angle: {s['next_angle']}")
        else:
            print("No active series.")
    else:
        print("Usage: series.py [--regen | --list | --detect]")
