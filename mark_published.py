"""
mark_published.py — Log that a post was actually published to LinkedIn/blog.

After Telegram digest sends you posts with tokens, run this to mark one as
published. It updates publish_queue.json with the publish time and (optionally)
the URL, then schedules an engagement-tracking reminder 48h later.

Usage:
  python3 mark_published.py <token>                       # mark as published now
  python3 mark_published.py <token> --url https://...     # also save the URL
  python3 mark_published.py --list                        # show queue status
  python3 mark_published.py --pending                     # show waiting-for-engagement
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from config import OUTPUT_DIR

PUBLISH_QUEUE = OUTPUT_DIR / "_state" / "publish_queue.json"


def _load() -> dict:
    if not PUBLISH_QUEUE.exists():
        return {}
    try:
        return json.loads(PUBLISH_QUEUE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(q: dict):
    PUBLISH_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    PUBLISH_QUEUE.write_text(json.dumps(q, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def mark(token: str, url: str | None = None) -> bool:
    q = _load()
    entry = q.get(token)
    if not entry:
        print(f"❌ token לא נמצא: {token}")
        print("   הרץ `python3 mark_published.py --list` לראות tokens זמינים")
        return False
    if entry.get("published_at"):
        print(f"⚠️  כבר סומן כפורסם ב-{entry['published_at']}")
    entry["published_at"] = datetime.now().isoformat(timespec="seconds")
    if url:
        entry["url"] = url
    entry["engagement_check_due"] = (datetime.now() + timedelta(hours=48)).isoformat(timespec="seconds")
    _save(q)
    print(f"✅ {entry['platform']}: סומן כפורסם · {Path(entry['file']).name}")
    print(f"   תזכורת לאיסוף engagement: {entry['engagement_check_due'][:16].replace('T',' ')}")
    return True


def list_queue():
    q = _load()
    if not q:
        print("הקיו ריק.")
        return
    print(f"{'TOKEN':8} {'PLATFORM':10} {'SENT':17} {'PUBLISHED':17} FILE")
    print("─" * 90)
    for token, e in sorted(q.items(), key=lambda kv: kv[1].get("sent_at", ""), reverse=True):
        sent = (e.get("sent_at") or "—")[:16].replace("T", " ")
        pub  = (e.get("published_at") or "—")[:16].replace("T", " ")
        fn = Path(e.get("file", "?")).name[:40]
        print(f"{token:8} {e.get('platform','?'):10} {sent:17} {pub:17} {fn}")


def list_pending_engagement():
    """Posts that were published but engagement-check is due."""
    q = _load()
    now = datetime.now()
    due = []
    for token, e in q.items():
        if not e.get("published_at"):
            continue
        if e.get("engagement") is not None:
            continue
        check_due = e.get("engagement_check_due")
        if not check_due:
            continue
        try:
            if datetime.fromisoformat(check_due) <= now:
                due.append((token, e))
        except Exception:
            pass
    if not due:
        print("אין engagement-checks ממתינים.")
        return
    print(f"📊 {len(due)} פוסטים מחכים לעדכון engagement:")
    print()
    for token, e in due:
        print(f"  {token}  {e['platform']:10} {Path(e['file']).name[:50]}")
        print(f"    פורסם: {e['published_at'][:16].replace('T',' ')}")
    print()
    print("הרץ: `python3 engagement_tracker.py` כדי להזין מספרים")


def main():
    ap = argparse.ArgumentParser(description="Mark a queued post as published")
    ap.add_argument("token", nargs="?", help="6-char token from Telegram digest")
    ap.add_argument("--url", help="URL of the published post (optional)")
    ap.add_argument("--list", action="store_true", help="Show queue status")
    ap.add_argument("--pending", action="store_true",
                    help="Show posts pending engagement entry")
    args = ap.parse_args()

    if args.list:
        list_queue()
        return
    if args.pending:
        list_pending_engagement()
        return
    if not args.token:
        ap.print_help()
        sys.exit(1)
    ok = mark(args.token, args.url)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
