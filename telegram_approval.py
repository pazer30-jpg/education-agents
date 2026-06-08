"""
telegram_approval.py — Telegram button-driven approval flow for ready posts.

Closes the publish loop: instead of you copying-pasting posts to LinkedIn
and then running `python3 mark_published.py <token>` manually, posts are
sent to your Telegram with inline-keyboard buttons. Click one of:

    [ ✅ פרסם ]   marks as approved/published → moves to output/published/
    [ ✏️ ערוך ]   re-sends to Telegram with edit prompt → stays in queue
    [ ❌ דחה ]    moves to output/rejected/, won't be offered again

How it works (long-polling — no webhook needed):
  1. On each pipeline run, agent8_publisher sends new posts with buttons
     attached (callback_data = token)
  2. This script's --poll mode hits Telegram getUpdates every N seconds,
     reads callback_query events, dispatches to the right handler
  3. Each handler updates publish_queue.json and the post's filesystem
     location, then edits the original Telegram message to show the
     decision ("✅ פורסם 14:32 ע\"י פז")

Usage:
  python3 telegram_approval.py --send-pending   # send ready posts as approval cards
  python3 telegram_approval.py --poll           # drain pending button clicks (one pass)
  python3 telegram_approval.py --daemon         # poll loop (Ctrl+C to stop)

Requires TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env.
"""

import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR

STATE_FILE   = OUTPUT_DIR / "_state" / "telegram_approval.json"
PUB_QUEUE    = OUTPUT_DIR / "_state" / "publish_queue.json"
PUBLISHED    = OUTPUT_DIR / "published"
REJECTED     = OUTPUT_DIR / "rejected"

PLATFORM_ICON = {"linkedin": "💼", "blog": "📰", "podcast": "🎙️"}

# Telegram API token loaded the same way as notifications.py
try:
    import os
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
except Exception:
    BOT_TOKEN = CHAT_ID = ""


def _load_json(p: Path, default):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────
# State helpers
# ─────────────────────────────────────────────

def _state() -> dict:
    return _load_json(STATE_FILE, {
        "last_update_id":   0,
        "sent_tokens":      {},  # token -> {message_id, sent_at}
        "decisions":        {},  # token -> {decision, decided_at}
    })


def _save_state(s: dict) -> None:
    _save_json(STATE_FILE, s)


def _is_configured() -> bool:
    return bool(BOT_TOKEN and CHAT_ID)


# ─────────────────────────────────────────────
# Telegram API wrappers
# ─────────────────────────────────────────────

def _send_with_buttons(text: str, buttons: list[list[dict]]) -> int | None:
    """Send a message with inline-keyboard. Returns message_id or None."""
    if not _is_configured():
        return None
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id":      CHAT_ID,
                "text":         text[:4000],
                "parse_mode":   "Markdown",
                "reply_markup": {"inline_keyboard": buttons},
            },
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("result", {}).get("message_id")
    except Exception:
        return None
    return None


def _edit_message(message_id: int, new_text: str) -> bool:
    """Edit a previously-sent message (removes buttons too)."""
    if not _is_configured():
        return False
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
            json={
                "chat_id":    CHAT_ID,
                "message_id": message_id,
                "text":       new_text[:4000],
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def _answer_callback(callback_query_id: str, text: str = "") -> bool:
    """Acknowledge a button click so Telegram stops showing the spinner."""
    if not _is_configured():
        return False
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text[:200]},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def _get_updates(offset: int, timeout: int = 25) -> list[dict]:
    """Long-poll Telegram for new callback_query events."""
    if not _is_configured():
        return []
    try:
        import requests
        r = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params={
                "offset":          offset,
                "timeout":         timeout,
                "allowed_updates": json.dumps(["callback_query"]),
            },
            timeout=timeout + 5,
        )
        if r.status_code == 200:
            return r.json().get("result", []) or []
    except Exception:
        return []
    return []


# ─────────────────────────────────────────────
# Send approval cards for new ready posts
# ─────────────────────────────────────────────

def send_pending() -> dict:
    """Find ready posts that haven't been sent for approval yet, send them."""
    if not _is_configured():
        print("⚠️  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skip")
        return {"sent": 0, "skipped_no_telegram": True}

    queue = _load_json(PUB_QUEUE, {})
    state = _state()
    sent = state["sent_tokens"]
    decisions = state["decisions"]

    candidates = []
    for token, entry in queue.items():
        if token in sent or token in decisions:
            continue
        if entry.get("published_at"):
            continue
        if not entry.get("file") or not Path(entry["file"]).exists():
            continue
        candidates.append((token, entry))

    if not candidates:
        return {"sent": 0, "skipped_no_candidates": True}

    sent_count = 0
    for token, entry in candidates[:5]:  # cap at 5 per run to avoid spam
        file_path = Path(entry["file"])
        platform = entry.get("platform", "?")
        body = file_path.read_text(encoding="utf-8", errors="replace")[:1800]
        icon = PLATFORM_ICON.get(platform, "📄")

        text = (
            f"{icon} *{platform}*  ·  token `{token}`\n\n"
            f"```\n{body}\n```\n"
            f"_מה לעשות עם זה?_"
        )
        buttons = [
            [
                {"text": "✅ פרסם",  "callback_data": f"approve:{token}"},
                {"text": "✏️ ערוך",  "callback_data": f"edit:{token}"},
                {"text": "❌ דחה",   "callback_data": f"reject:{token}"},
            ]
        ]
        msg_id = _send_with_buttons(text, buttons)
        if msg_id:
            sent[token] = {
                "message_id": msg_id,
                "sent_at":    datetime.now().isoformat(timespec="seconds"),
                "platform":   platform,
                "file":       str(file_path),
            }
            sent_count += 1

    _save_state(state)
    return {"sent": sent_count, "candidates": len(candidates)}


# ─────────────────────────────────────────────
# Handle one click
# ─────────────────────────────────────────────

def _move_to(src: Path, dst_dir: Path) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    target = dst_dir / src.name
    # If a file with the same name already exists, append timestamp
    if target.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = dst_dir / f"{src.stem}_{ts}{src.suffix}"
    shutil.move(str(src), str(target))
    return target


def _handle_approve(token: str, entry: dict) -> str:
    """User clicked ✅ — try LinkedIn auto-publish (if configured), then mark
    as published in queue and move file to published/."""
    queue = _load_json(PUB_QUEUE, {})
    platform = entry.get("platform", "misc")
    src = Path(entry["file"])

    posted_url = ""
    li_msg = ""

    # If this is a LinkedIn post AND linkedin_publisher is set up, push it
    if platform == "linkedin":
        try:
            import linkedin_publisher
            if linkedin_publisher.is_configured() and src.exists():
                body = src.read_text(encoding="utf-8", errors="replace").strip()
                res = linkedin_publisher.publish(body)
                if res.get("ok"):
                    posted_url = res.get("url", "")
                    li_msg = f"\n🔗 פורסם ל-LinkedIn: {posted_url}"
                else:
                    li_msg = f"\n⚠️ LinkedIn publish failed: {res.get('error', '?')[:120]}"
        except Exception as e:
            li_msg = f"\n⚠️ LinkedIn publish error: {e}"

    if token in queue:
        queue[token]["published_at"] = datetime.now().isoformat(timespec="seconds")
        queue[token]["decision"]     = "approved"
        if posted_url:
            queue[token]["posted_url"] = posted_url
        _save_json(PUB_QUEUE, queue)

    if src.exists():
        try:
            moved = _move_to(src, PUBLISHED / platform)
            return (f"✅ אושר ועבר ל-`{moved.relative_to(OUTPUT_DIR)}`" + li_msg)
        except Exception as e:
            return f"✅ אושר (queue עודכן) · ⚠️ כשל בהעברת קובץ: {e}" + li_msg
    return "✅ אושר ב-queue (קובץ לא נמצא)" + li_msg


def _handle_reject(token: str, entry: dict) -> str:
    """User clicked ❌ — move file to rejected/, mark in queue."""
    queue = _load_json(PUB_QUEUE, {})
    if token in queue:
        queue[token]["decision"]    = "rejected"
        queue[token]["rejected_at"] = datetime.now().isoformat(timespec="seconds")
        _save_json(PUB_QUEUE, queue)
    src = Path(entry["file"])
    if src.exists():
        try:
            moved = _move_to(src, REJECTED / entry.get("platform", "misc"))
            return f"❌ נדחה והועבר ל-`{moved.relative_to(OUTPUT_DIR)}`"
        except Exception as e:
            return f"❌ נדחה (queue עודכן) · ⚠️ כשל בהעברה: {e}"
    return "❌ נדחה ב-queue (קובץ לא נמצא)"


def _handle_edit(token: str, entry: dict) -> str:
    """User clicked ✏️ — leave the file in place, mark in queue for re-review."""
    queue = _load_json(PUB_QUEUE, {})
    if token in queue:
        queue[token]["decision"]    = "edit_requested"
        queue[token]["edited_at"]   = datetime.now().isoformat(timespec="seconds")
        _save_json(PUB_QUEUE, queue)
    return (f"✏️ סומן לעריכה. הקובץ נשאר ב-`{entry['file']}` — "
            f"ערוך ידנית ואז תפעיל `--send-pending` שוב.")


HANDLERS = {
    "approve": _handle_approve,
    "reject":  _handle_reject,
    "edit":    _handle_edit,
}


# ─────────────────────────────────────────────
# Poll once
# ─────────────────────────────────────────────

def poll_once() -> dict:
    if not _is_configured():
        return {"processed": 0, "error": "no_telegram"}

    state = _state()
    offset = state.get("last_update_id", 0) + 1
    updates = _get_updates(offset)
    if not updates:
        return {"processed": 0}

    sent = state["sent_tokens"]
    decisions = state["decisions"]
    processed = 0

    for upd in updates:
        state["last_update_id"] = max(state["last_update_id"], upd.get("update_id", 0))
        cb = upd.get("callback_query")
        if not cb:
            continue
        data = cb.get("data", "") or ""
        if ":" not in data:
            _answer_callback(cb["id"], "")
            continue
        action, token = data.split(":", 1)
        handler = HANDLERS.get(action)
        if not handler:
            _answer_callback(cb["id"], "פעולה לא ידועה")
            continue
        if token in decisions:
            _answer_callback(cb["id"], "כבר טופל")
            continue
        entry = sent.get(token)
        if not entry:
            _answer_callback(cb["id"], "טוקן לא ידוע")
            continue

        result_msg = handler(token, entry)
        _answer_callback(cb["id"], result_msg[:100])
        decisions[token] = {
            "decision":     action,
            "decided_at":   datetime.now().isoformat(timespec="seconds"),
            "result_msg":   result_msg,
        }
        # Edit original message to reflect decision (and remove buttons)
        if entry.get("message_id"):
            orig_icon = PLATFORM_ICON.get(entry.get("platform"), "📄")
            _edit_message(entry["message_id"],
                          f"{orig_icon} *{entry.get('platform', '?')}*  ·  `{token}`\n\n"
                          f"{result_msg}\n\n"
                          f"_החלטה: {datetime.now().strftime('%d/%m %H:%M')}_")
        processed += 1

    _save_state(state)
    return {"processed": processed}


def daemon(interval: int = 60) -> None:
    """Run poll_once in a loop. Ctrl+C to stop."""
    if not _is_configured():
        print("⚠️ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing — daemon won't poll")
        return
    print(f"📡 telegram approval daemon — polling every {interval}s. Ctrl+C to stop.")
    try:
        while True:
            res = poll_once()
            if res.get("processed"):
                print(f"  [{datetime.now().strftime('%H:%M:%S')}] processed {res['processed']} clicks")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n👋 stopped")


def main():
    ap = argparse.ArgumentParser(description="Telegram approval flow")
    ap.add_argument("--send-pending", action="store_true",
                    help="send approval cards for new ready posts")
    ap.add_argument("--poll", action="store_true",
                    help="drain pending button clicks (one pass)")
    ap.add_argument("--daemon", action="store_true",
                    help="poll in a loop (Ctrl+C to stop)")
    ap.add_argument("--interval", type=int, default=60,
                    help="daemon poll interval seconds (default 60)")
    ap.add_argument("--status", action="store_true",
                    help="show counts of sent / decided / pending")
    args = ap.parse_args()

    if args.status:
        s = _state()
        print(f"sent:      {len(s['sent_tokens'])}")
        print(f"decided:   {len(s['decisions'])}")
        pending = [t for t in s["sent_tokens"] if t not in s["decisions"]]
        print(f"pending:   {len(pending)}")
        for t in pending[:5]:
            print(f"  - {t}  ({s['sent_tokens'][t].get('platform')})")
        return

    if args.send_pending:
        r = send_pending()
        print(json.dumps(r, ensure_ascii=False))
        return

    if args.poll:
        r = poll_once()
        print(json.dumps(r, ensure_ascii=False))
        return

    if args.daemon:
        daemon(args.interval)
        return

    ap.print_help()


if __name__ == "__main__":
    main()
