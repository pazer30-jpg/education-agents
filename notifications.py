"""
notifications.py — התראות טלגרם
שולח הודעות למשתמש כשהפייפליין מסתיים/נכשל/צריך אישור.

שימוש:
  from notifications import notify, notify_error, notify_preview

  notify("Pipeline הסתיים בהצלחה! 3 קבצים נוצרו.")
  notify_error("writer", "Claude CLI error exit 1")
  notify_preview("linkedin", "טקסט הפוסט כאן...")
"""

import os
import json
import requests
from pathlib import Path


# Load config
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")  # Your personal chat ID


def _send(text: str, parse_mode: str = "Markdown",
          inline_keyboard: list | None = None) -> bool:
    """Send a message via Telegram Bot API.
    inline_keyboard: optional list-of-rows, each row a list of {text, callback_data} dicts.
    Used by telegram_approval flow to attach ✅ / ✏️ / ❌ buttons to each post."""
    if not BOT_TOKEN or not CHAT_ID:
        return False
    payload = {
        "chat_id": CHAT_ID,
        "text": text[:4000],
        "parse_mode": parse_mode,
    }
    if inline_keyboard:
        payload["reply_markup"] = {"inline_keyboard": inline_keyboard}
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def is_configured() -> bool:
    return bool(BOT_TOKEN and CHAT_ID)


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def notify(message: str) -> bool:
    """Send a general notification."""
    return _send(f"🤖 *מוקי*\n\n{message}")


def notify_complete(topic: str, duration_min: float, files: dict, qa_score: int = 0):
    """Send completion notification."""
    files_text = "\n".join(f"  • {k}: {v}" for k, v in files.items()) if files else "  אין"
    return _send(
        f"✅ *Pipeline הסתיים*\n\n"
        f"📚 נושא: {topic}\n"
        f"⏱ זמן: {duration_min:.1f} דק'\n"
        f"📊 QA: {qa_score}/100\n\n"
        f"📁 קבצים:\n{files_text}"
    )


def notify_error(agent: str, error: str):
    """Send error notification."""
    return _send(
        f"❌ *שגיאה ב-{agent}*\n\n"
        f"`{error[:500]}`\n\n"
        f"בדוק בטרמינל או בדאשבורד."
    )


def notify_preview(platform: str, content: str, callback_data: str = ""):
    """Send content preview for approval."""
    icon = {"linkedin": "💼", "blog": "📰", "podcast": "🎙️"}.get(platform, "📄")
    preview = content[:1500]
    return _send(
        f"{icon} *תוכן חדש — {platform}*\n\n"
        f"{preview}\n\n"
        f"{'─'*20}\n"
        f"📏 {len(content)} תווים | {len(content.split())} מילים",
        parse_mode="Markdown",
    )


def notify_weekly(summary_text: str):
    """Send weekly summary."""
    return _send(f"📊 *סיכום שבועי*\n\n{summary_text[:3500]}")
