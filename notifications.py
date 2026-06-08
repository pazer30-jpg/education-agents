"""
notifications.py — STUB. Telegram bot integration was removed; this file
remains so existing callers (`from notifications import ...`) keep working
without modification.

All send functions are silent no-ops. is_configured() returns False so
guarded code paths simply skip the notification step.

If you ever want to re-add a notification channel (Telegram, Slack,
Discord, email), implement it in this file. The public API contract is:

  _send(text, parse_mode="Markdown", inline_keyboard=None) -> bool
  is_configured() -> bool
  notify(message) -> bool
  notify_complete(topic, duration_min, files, qa_score=0) -> bool
  notify_error(agent, error) -> bool
  notify_preview(platform, content, callback_data="") -> bool
"""


def _send(text: str, parse_mode: str = "Markdown",
          inline_keyboard=None) -> bool:
    return False


def is_configured() -> bool:
    return False


def notify(message: str) -> bool:
    return False


def notify_complete(topic: str, duration_min: float, files: dict,
                    qa_score: int = 0) -> bool:
    return False


def notify_error(agent: str, error: str) -> bool:
    return False


def notify_preview(platform: str, content: str, callback_data: str = "") -> bool:
    return False
