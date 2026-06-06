"""
linkedin_analytics_import.py — Ingest LinkedIn analytics CSV → engagement_tracker.

The data path that closes the learning loop without waiting on LinkedIn's
app-approval queue (1-3 days). LinkedIn lets you export your own analytics
to CSV directly from the UI today:

   linkedin.com/feed/analytics
     → "Posts" tab
     → "Export" (top right)
     → choose time range
     → downloads CSV

This module:
  1. Parses that CSV (tolerant to format drift — column names normalized)
  2. Matches each row to a post in output/_state/publish_queue.json via URL
     (the publish_queue is the source-of-truth for what we sent out)
  3. Writes engagement back to publish_queue + performance_log
  4. Regenerates the hook_winners.md memory file so the next pipeline run
     learns from REAL engagement, not just heuristic hook scores

CSV column expectations (case-insensitive, normalized via _norm()):
  - "post url" / "url" / "permalink"
  - "impressions" / "views"
  - "reactions" / "likes" / "total likes"
  - "comments"
  - "shares" / "reposts"
  - "publish date" / "post date" (optional, for sanity)

Usage:
  python3 linkedin_analytics_import.py <path-to-linkedin-export.csv>
  python3 linkedin_analytics_import.py --dry-run <path>     # preview matches
  python3 linkedin_analytics_import.py --list-unmatched <path>
"""

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR

PUB_QUEUE   = OUTPUT_DIR / "_state" / "publish_queue.json"
IMPORT_LOG  = OUTPUT_DIR / "_state" / "linkedin_imports.json"


def _norm(s: str) -> str:
    """Normalize a header so 'Post URL' / 'post_url' / 'POST URL' all match."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _load_csv(path: Path) -> list[dict]:
    """Read CSV, return list of rows with normalized keys."""
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return []
        # Map normalized column → original column name
        norm_to_orig = {_norm(c): c for c in reader.fieldnames}
        out = []
        for row in reader:
            normalized = {_norm(k): (v or "").strip() for k, v in row.items()}
            out.append(normalized)
        return out


def _pick_col(row: dict, candidates: list[str]) -> str:
    """First non-empty value among normalized candidate keys."""
    for c in candidates:
        v = row.get(_norm(c), "")
        if v:
            return v
    return ""


def _int(v: str) -> int:
    """Tolerant int parsing — strips commas, '%', returns 0 on failure."""
    if not v:
        return 0
    v = str(v).replace(",", "").replace("%", "").strip()
    try:
        return int(float(v))
    except Exception:
        return 0


# ─────────────────────────────────────────────
# Match a CSV row → publish_queue entry
# ─────────────────────────────────────────────

def _load_queue() -> dict:
    if not PUB_QUEUE.exists():
        return {}
    try:
        return json.loads(PUB_QUEUE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_queue(q: dict) -> None:
    PUB_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    PUB_QUEUE.write_text(json.dumps(q, ensure_ascii=False, indent=2),
                         encoding="utf-8")


def _row_to_engagement(row: dict) -> dict:
    return {
        "url":         _pick_col(row, ["post url", "url", "permalink", "post link"]),
        "impressions": _int(_pick_col(row, ["impressions", "views"])),
        "likes":       _int(_pick_col(row, ["reactions", "likes", "total likes",
                                            "total reactions"])),
        "comments":    _int(_pick_col(row, ["comments", "total comments"])),
        "shares":      _int(_pick_col(row, ["shares", "reposts", "total shares"])),
        "engagement_rate": _pick_col(row, ["engagement rate", "engagementrate"]),
    }


def _find_queue_match(eng: dict, queue: dict) -> tuple[str, dict] | None:
    """Match CSV row's URL to a publish_queue entry. Returns (token, entry)."""
    if not eng["url"]:
        return None
    url = eng["url"].rstrip("/")
    for token, entry in queue.items():
        # Multiple URL fields might exist
        for k in ("posted_url", "url", "published_url"):
            qurl = (entry.get(k) or "").rstrip("/")
            if qurl and (qurl == url or url.endswith(qurl) or qurl.endswith(url)):
                return token, entry
    return None


# ─────────────────────────────────────────────
# Main ingestion
# ─────────────────────────────────────────────

def ingest(csv_path: Path, dry_run: bool = False) -> dict:
    rows = _load_csv(csv_path)
    if not rows:
        return {"rows": 0, "matched": 0, "updated": 0, "unmatched": [],
                "error": "empty CSV or no header"}

    queue = _load_queue()
    matched = 0
    updated = 0
    unmatched = []
    updates = []

    for row in rows:
        eng = _row_to_engagement(row)
        if not eng["url"]:
            continue
        hit = _find_queue_match(eng, queue)
        if not hit:
            unmatched.append({
                "url":         eng["url"],
                "impressions": eng["impressions"],
                "likes":       eng["likes"],
            })
            continue
        token, entry = hit
        matched += 1
        # Don't overwrite higher engagement with lower (idempotent — repeat
        # imports won't shrink the numbers on the same URL)
        old = entry.get("engagement") or {}
        merged = {
            "impressions": max(eng["impressions"], old.get("impressions", 0)),
            "likes":       max(eng["likes"],       old.get("likes", 0)),
            "comments":    max(eng["comments"],    old.get("comments", 0)),
            "shares":      max(eng["shares"],      old.get("shares", 0)),
            "source":      "linkedin_csv",
            "recorded_at": datetime.now().isoformat(timespec="seconds"),
        }
        if merged != old:
            updated += 1
            updates.append({"token": token, "before": old, "after": merged})
            if not dry_run:
                queue[token]["engagement"] = merged

    if not dry_run and updated:
        _save_queue(queue)
        # Also append to performance_log so engagement_tracker's report sees it
        try:
            from performance_log import _save, _load
            log = _load()
            for u in updates:
                entry = queue[u["token"]]
                log.append({
                    "platform":     entry.get("platform", "linkedin"),
                    "title":        Path(entry.get("file", "")).stem[:80],
                    "metrics":      u["after"],
                    "what_worked":  "",
                    "ts":           datetime.now().isoformat(timespec="seconds"),
                    "source":       "linkedin_csv",
                })
            _save(log)
        except Exception as e:
            print(f"  ⚠️ performance_log update skipped: {e}")
        # Update hook_winners memory so next pipeline learns
        try:
            from hook_log import regenerate_memory
            regenerate_memory()
        except Exception as e:
            print(f"  ⚠️ hook_winners regen skipped: {e}")
        # Record this import for audit
        try:
            history = json.loads(IMPORT_LOG.read_text(encoding="utf-8")) \
                      if IMPORT_LOG.exists() else []
        except Exception:
            history = []
        history.append({
            "imported_at": datetime.now().isoformat(timespec="seconds"),
            "csv_file":    csv_path.name,
            "rows":        len(rows),
            "matched":     matched,
            "updated":     updated,
            "unmatched":   len(unmatched),
        })
        IMPORT_LOG.parent.mkdir(parents=True, exist_ok=True)
        IMPORT_LOG.write_text(json.dumps(history, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    return {
        "rows":      len(rows),
        "matched":   matched,
        "updated":   updated,
        "unmatched": unmatched,
        "updates":   updates if dry_run else [],
    }


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Ingest LinkedIn analytics CSV")
    ap.add_argument("csv", nargs="?", help="path to LinkedIn export CSV")
    ap.add_argument("--dry-run",       action="store_true", help="preview, don't write")
    ap.add_argument("--list-unmatched", action="store_true",
                    help="just print rows we couldn't match to a known post")
    args = ap.parse_args()

    if not args.csv:
        print("Usage: linkedin_analytics_import.py <linkedin-export.csv> [--dry-run]")
        print()
        print("To export from LinkedIn:")
        print("  1. Open linkedin.com/feed/analytics")
        print("  2. Click 'Posts' tab")
        print("  3. Click 'Export' (top right)")
        print("  4. Pick time range, download CSV")
        print("  5. Run this script on the file")
        sys.exit(1)

    path = Path(args.csv)
    if not path.exists():
        print(f"❌ file not found: {path}")
        sys.exit(1)

    result = ingest(path, dry_run=args.dry_run)
    print(f"📥 rows: {result['rows']} · matched: {result['matched']} · "
          f"updated: {result['updated']} · unmatched: {len(result['unmatched'])}")

    if args.list_unmatched and result["unmatched"]:
        print("\nUnmatched (not in publish_queue — maybe published before queue tracking):")
        for u in result["unmatched"][:20]:
            print(f"  • {u['likes']:>4} 👍 · {u['impressions']:>6} views · {u['url'][:100]}")

    if args.dry_run and result["updates"]:
        print("\nWould update (dry-run):")
        for u in result["updates"][:10]:
            print(f"  {u['token']}: 👍 {u['before'].get('likes',0)} → {u['after']['likes']}, "
                  f"💬 {u['before'].get('comments',0)} → {u['after']['comments']}")


if __name__ == "__main__":
    main()
