"""
agent8_publisher.py — Daily digest of top-ranked ready posts → Telegram.

The pipeline produces posts but they sit unread in output/posts/. This agent:
  1. Finds posts that are ready_to_publish but not yet logged as published
  2. Ranks them (recency × hook score × QA)
  3. Sends top N to Telegram as a daily digest with the post text and a token
  4. User runs `mark_published.py <token>` after copy-pasting to LinkedIn

Designed to be called from run_pipeline.sh at end-of-run, OR as its own cron
("morning briefing" at 09:00). Idempotent — never sends the same post twice.

Usage:
  python3 agent8_publisher.py              # send digest now
  python3 agent8_publisher.py --dry-run    # print, don't send
  python3 agent8_publisher.py --top 5      # change digest size (default 3)
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from config import OUTPUT_DIR

LINKEDIN_DIR = OUTPUT_DIR / "posts" / "linkedin"
BLOG_DIR     = OUTPUT_DIR / "posts" / "blog"
PODCAST_DIR  = OUTPUT_DIR / "posts" / "podcast"

PUBLISH_QUEUE = OUTPUT_DIR / "_state" / "publish_queue.json"
PUBLISH_QUEUE.parent.mkdir(parents=True, exist_ok=True)

MAX_AGE_DAYS = 21  # don't surface posts older than 3 weeks


# ─────────────────────────────────────────────
# Queue persistence
# ─────────────────────────────────────────────

def _load_queue() -> dict:
    """Returns: {token: {file, platform, sent_at, published_at, engagement}}"""
    if not PUBLISH_QUEUE.exists():
        return {}
    try:
        return json.loads(PUBLISH_QUEUE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_queue(q: dict):
    PUBLISH_QUEUE.write_text(json.dumps(q, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def _already_seen(file_path: str, q: dict) -> bool:
    """Has this file been queued before (sent to Telegram already)?"""
    return any(entry.get("file") == file_path for entry in q.values())


def _make_token(file_path: str) -> str:
    """Stable 6-char token: hash of basename."""
    import hashlib
    h = hashlib.sha1(Path(file_path).name.encode("utf-8")).hexdigest()
    return h[:6]


# ─────────────────────────────────────────────
# Discover & rank candidates
# ─────────────────────────────────────────────

def _candidates() -> list[dict]:
    """Find ready posts not yet sent to Telegram."""
    q = _load_queue()
    cutoff = (datetime.now() - timedelta(days=MAX_AGE_DAYS)).timestamp()

    found = []
    sources = [
        (LINKEDIN_DIR, "*_ready*.txt",            "linkedin"),
        (BLOG_DIR,     "*.md",                     "blog"),
        (PODCAST_DIR,  "*_script_*.md",            "podcast"),
    ]
    for directory, pattern, platform in sources:
        if not directory.exists():
            continue
        for p in directory.glob(pattern):
            if p.name.endswith(".bak"):
                continue
            mtime = p.stat().st_mtime
            if mtime < cutoff:
                continue
            if _already_seen(str(p), q):
                continue
            found.append({
                "file":     str(p),
                "name":     p.name,
                "platform": platform,
                "mtime":    mtime,
                "size":     p.stat().st_size,
            })
    return found


def _score(c: dict) -> float:
    """Recency dominant. Newer files score higher."""
    age_hours = (datetime.now().timestamp() - c["mtime"]) / 3600
    recency = max(0, 100 - age_hours / 2)  # 100 at 0h → 0 at ~200h
    # Slight boost for LinkedIn (priority platform)
    platform_boost = {"linkedin": 10, "blog": 5, "podcast": 0}.get(c["platform"], 0)
    return recency + platform_boost


def _rank(candidates: list[dict], top_n: int) -> list[dict]:
    candidates.sort(key=_score, reverse=True)
    return candidates[:top_n]


# ─────────────────────────────────────────────
# Send digest
# ─────────────────────────────────────────────

PLATFORM_ICON = {"linkedin": "💼", "blog": "📰", "podcast": "🎙️"}


def _build_message(picks: list[dict]) -> str:
    """One Telegram message per pick (Markdown). Returns list — caller sends each."""
    if not picks:
        return "🤖 *מוקי — דיגסט יומי*\n\nאין פוסטים חדשים לפרסום היום."

    lines = [f"📬 *דיגסט יומי — {len(picks)} פוסטים מוכנים*",
             f"_{datetime.now().strftime('%d/%m/%Y %H:%M')}_", ""]
    for i, c in enumerate(picks, 1):
        icon = PLATFORM_ICON.get(c["platform"], "📄")
        age_h = int((datetime.now().timestamp() - c["mtime"]) / 3600)
        token = _make_token(c["file"])
        lines.append(f"{i}. {icon} *{c['platform']}* · {age_h}h · `{token}`")
        lines.append(f"   `{c['name'][:60]}`")
    lines.append("")
    lines.append("─" * 20)
    lines.append("אחרי שתפרסם — הרץ:")
    lines.append("`python3 mark_published.py <token>`")
    return "\n".join(lines)


def _send_post_body(pick: dict) -> str:
    """Build a per-post message with the actual content + token."""
    text = Path(pick["file"]).read_text(encoding="utf-8", errors="replace")
    body = text[:3500]
    if len(text) > 3500:
        body += f"\n\n_...(נחתך, סה\"כ {len(text)} תווים)_"
    icon = PLATFORM_ICON.get(pick["platform"], "📄")
    token = _make_token(pick["file"])
    return (f"{icon} *{pick['platform']}* · token `{token}`\n\n"
            f"```\n{body}\n```\n"
            f"_פרסם וסמן:_ `python3 mark_published.py {token}`")


def send_digest(top_n: int = 3, dry_run: bool = False) -> dict:
    """
    Main entry. Returns: {"sent": int, "tokens": [token], "skipped_no_candidates": bool}
    """
    cands = _candidates()
    picks = _rank(cands, top_n)

    if not picks:
        if not dry_run:
            try:
                from notifications import notify, is_configured
                if is_configured():
                    notify("📬 דיגסט יומי: אין פוסטים חדשים לפרסום היום.")
            except Exception:
                pass
        return {"sent": 0, "tokens": [], "skipped_no_candidates": True}

    summary = _build_message(picks)
    if dry_run:
        print("=" * 60)
        print("DRY RUN — summary message:")
        print("=" * 60)
        print(summary)
        print()
        for p in picks:
            print("=" * 60)
            print(_send_post_body(p)[:500])
        return {"sent": 0, "tokens": [_make_token(p["file"]) for p in picks],
                "skipped_no_candidates": False, "dry_run": True}

    # Send via Telegram
    try:
        from notifications import _send, is_configured
    except Exception as e:
        print(f"⚠️  notifications module not available: {e}")
        return {"sent": 0, "tokens": [], "error": str(e)}

    if not is_configured():
        print("⚠️  Telegram not configured — set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID")
        return {"sent": 0, "tokens": [], "error": "telegram_not_configured"}

    # 1) summary
    _send(summary, parse_mode="Markdown")
    # 2) one per pick
    sent_tokens = []
    q = _load_queue()
    for pick in picks:
        ok = _send(_send_post_body(pick), parse_mode="Markdown")
        token = _make_token(pick["file"])
        sent_tokens.append(token)
        if ok:
            q[token] = {
                "file":         pick["file"],
                "platform":     pick["platform"],
                "sent_at":      datetime.now().isoformat(timespec="seconds"),
                "published_at": None,
                "engagement":   None,
            }
    _save_queue(q)
    print(f"✅ שלח {len(sent_tokens)} פוסטים — tokens: {', '.join(sent_tokens)}")
    return {"sent": len(sent_tokens), "tokens": sent_tokens,
            "skipped_no_candidates": False}


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Daily digest of ready posts to Telegram")
    ap.add_argument("--top", type=int, default=3, help="How many posts in the digest")
    ap.add_argument("--dry-run", action="store_true", help="Print, don't send")
    args = ap.parse_args()
    result = send_digest(top_n=args.top, dry_run=args.dry_run)
    sys.exit(0 if result.get("sent", 0) >= 0 else 1)


if __name__ == "__main__":
    main()
