"""
telegram_bot.py — חיבור מוקי לטלגרם
מאפשר לשלוח פקודות למוקי דרך Telegram ולקבל תוצאות.

הגדרה:
  1. צור בוט ב-@BotFather → קבל TOKEN
  2. הגדר: export TELEGRAM_BOT_TOKEN="..."
     או צור קובץ .env עם TELEGRAM_BOT_TOKEN=...
  3. הרץ: python3 telegram_bot.py

פקודות בוט:
  /start          — הפעלה + הסבר
  /status          — מצב המערכת
  /pipeline_status — עדכוני pipeline אחרונים (5 שורות)
  /run             — הרצת pipeline מלא
  /run linkedin    — רק LinkedIn
  /summary         — סיכום שבועי
  /bib             — ביבליוגרפיה
  /qa              — בדיקת איכות
  /dashboard       — קישור לדאשבורד
  /help            — עזרה
  + כל הודעה חופשית → מוקי מענה
"""

import os
import sys
import json
import asyncio
import subprocess
from pathlib import Path
from datetime import datetime

from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters,
)

# Load .env if exists
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USERS = os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",")  # optional whitelist
PROJECT_DIR = Path(__file__).parent.resolve()


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _is_allowed(user_id: int) -> bool:
    """Check if user is allowed (empty list = allow all)."""
    if not ALLOWED_USERS or ALLOWED_USERS == [""]:
        return True
    return str(user_id) in ALLOWED_USERS


def _run_python(script: str, args: list[str] = None, timeout: int = 300) -> str:
    """Run a Python script and return output."""
    cmd = [sys.executable, str(PROJECT_DIR / script)] + (args or [])
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, cwd=str(PROJECT_DIR),
        )
        output = result.stdout.strip()
        if result.returncode != 0 and result.stderr:
            output += f"\n\n⚠️ {result.stderr.strip()[:200]}"
        return output[:4000]  # Telegram limit
    except subprocess.TimeoutExpired:
        return "⏰ הפעולה ארכה יותר מדי — בדוק בטרמינל"
    except Exception as e:
        return f"❌ שגיאה: {e}"


def _get_status() -> str:
    """Get system status."""
    try:
        sys.path.insert(0, str(PROJECT_DIR))
        from memory import load_memory
        from config import OUTPUT_DIR, PAPERS_DIR, ARTICLES_DIR, LINKEDIN_DIR, BLOG_DIR, PODCAST_DIR

        mem = load_memory()
        n_papers = len(list(PAPERS_DIR.glob("*.json")))
        n_articles = len(list(ARTICLES_DIR.glob("*.md")))
        n_linkedin = len(list(LINKEDIN_DIR.glob("*.txt")))
        n_blog = len(list(BLOG_DIR.glob("*.md")))
        n_podcast = len(list(PODCAST_DIR.glob("*.md")))

        return (
            f"📊 *מצב מוקי*\n"
            f"{'─'*25}\n"
            f"🔬 נושאים שנחקרו: {len(mem.get('researched_topics', []))}\n"
            f"📚 מאמרים שנאספו: {len(mem.get('papers', {}))}\n"
            f"📝 מאמרים שנכתבו: {n_articles}\n"
            f"💼 LinkedIn: {n_linkedin}\n"
            f"📰 בלוג: {n_blog}\n"
            f"🎙️ פודקאסט: {n_podcast}\n"
            f"🔄 איטרציות: {mem.get('iterations', 0)}\n"
            f"\n🔮 *הבא בתור:*\n"
            + "\n".join(f"  {i}. {t}" for i, t in enumerate(mem.get('topic_queue', [])[:3], 1))
        )
    except Exception as e:
        return f"❌ שגיאה בקריאת סטטוס: {e}"


# ─────────────────────────────────────────────
# Command handlers
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ אין לך הרשאה.")
        return

    await update.message.reply_text(
        "🤖 *מוקי — מנהל הפרויקט שלך*\n\n"
        "אני מריץ pipeline אקדמי: מחקר → מאמר → תוכן → עיצוב\n\n"
        "*פקודות:*\n"
        "/status — מצב המערכת\n"
        "/pipeline\\_status — עדכוני pipeline אחרונים\n"
        "/run — הרצת pipeline מלא\n"
        "/run linkedin — רק LinkedIn\n"
        "/run blog — רק בלוג\n"
        "/summary — סיכום שבועי\n"
        "/bib — ביבליוגרפיה\n"
        "/qa — בדיקת איכות\n"
        "/dashboard — דאשבורד\n\n"
        "או שלח הודעה חופשית ואנסה לעזור 🙂",
        parse_mode="Markdown",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return
    await update.message.reply_text(_get_status(), parse_mode="Markdown")


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return

    args = context.args or []
    content = " ".join(args) if args else "linkedin blog"

    await update.message.reply_text(
        f"🚀 מתחיל pipeline...\n"
        f"תוכן: {content}\n"
        f"זה ייקח 10-15 דקות — אעדכן כשיסיים."
    )

    # Run in background
    loop = asyncio.get_event_loop()
    output = await loop.run_in_executor(
        None,
        lambda: _run_python(
            "agent5_project_manager.py",
            [f"הרץ pipeline מלא, תוכן: {content}", "--auto"],
            timeout=1800,
        ),
    )

    # Send result (split if too long)
    if len(output) > 4000:
        # Send just the summary part
        lines = output.split("\n")
        summary_start = next(
            (i for i, l in enumerate(lines) if "COMPLETE" in l or "סיימתי" in l),
            max(0, len(lines) - 30),
        )
        output = "\n".join(lines[summary_start:])[:4000]

    await update.message.reply_text(f"✅ Pipeline הסתיים:\n\n```\n{output[-3000:]}\n```", parse_mode="Markdown")


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return
    await update.message.reply_text("📊 מכין סיכום שבועי...")
    output = _run_python("weekly_summary.py")
    await update.message.reply_text(f"```\n{output[:4000]}\n```", parse_mode="Markdown")


async def cmd_bib(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return
    output = _run_python("bibliography.py", ["--stats"])
    await update.message.reply_text(f"```\n{output[:4000]}\n```", parse_mode="Markdown")


async def cmd_qa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return
    await update.message.reply_text("🔍 בודק איכות...")
    output = _run_python("agent5_project_manager.py", ["בדוק איכות", "--auto"], timeout=120)
    await update.message.reply_text(f"```\n{output[:4000]}\n```", parse_mode="Markdown")


async def cmd_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return
    _run_python("dashboard.py", ["--no-open"])
    await update.message.reply_text(
        "📊 דאשבורד עודכן!\n\n"
        "לפתיחה בדפדפן:\n"
        "`python3 dashboard.py`\n\n"
        "או במצב שרת:\n"
        "`python3 dashboard.py --serve`",
        parse_mode="Markdown",
    )


async def cmd_pipeline_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show last 5 lines from pipeline_status.txt — live progress."""
    if not _is_allowed(update.effective_user.id):
        return
    status_file = PROJECT_DIR / "output" / "pipeline_status.txt"
    if not status_file.exists():
        await update.message.reply_text("📭 אין עדכוני pipeline עדיין.")
        return
    lines = status_file.read_text(encoding="utf-8").splitlines()
    last_lines = lines[-5:] if len(lines) >= 5 else lines
    text = "📡 *Pipeline Status (last 5):*\n\n" + "\n".join(last_lines)
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


# ─────────────────────────────────────────────
# Free text handler (chat with Moki)
# ─────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update.effective_user.id):
        return

    text = update.message.text.strip()
    if not text:
        return

    # Quick patterns
    low = text.lower()
    if any(w in low for w in ["סטטוס", "מה יש", "status"]):
        await cmd_status(update, context)
        return

    if any(w in low for w in ["סיכום", "summary"]):
        await cmd_summary(update, context)
        return

    if any(w in low for w in ["הרץ הכל", "run all", "pipeline"]):
        await cmd_run(update, context)
        return

    # General: pass to Moki chat
    await update.message.reply_text("🤔 חושב...")

    loop = asyncio.get_event_loop()
    output = await loop.run_in_executor(
        None,
        lambda: _run_python(
            "agent5_project_manager.py",
            [text, "--auto"],
            timeout=600,
        ),
    )

    if output:
        # Clean up output
        lines = output.split("\n")
        # Skip verbose lines
        clean = [l for l in lines if not l.startswith("  [") and "Iteration" not in l]
        response = "\n".join(clean)[-3500:]
        await update.message.reply_text(response or "✅ בוצע.")
    else:
        await update.message.reply_text("לא הצלחתי לעבד את הבקשה. נסה שוב.")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    if not TOKEN:
        print("""
❌ TELEGRAM_BOT_TOKEN לא מוגדר!

הגדרה:
  1. פתח @BotFather בטלגרם
  2. שלח /newbot
  3. בחר שם (למשל: MokiBot)
  4. העתק את ה-TOKEN
  5. הגדר:
     export TELEGRAM_BOT_TOKEN="YOUR_TOKEN_HERE"

     או צור קובץ .env:
     echo 'TELEGRAM_BOT_TOKEN=YOUR_TOKEN_HERE' > .env

  6. אופציונלי — הגבל משתמשים:
     export TELEGRAM_ALLOWED_USERS="123456789,987654321"
     (מצא את ה-ID שלך ב-@userinfobot)

  7. הרץ שוב:
     python3 telegram_bot.py
        """)
        sys.exit(1)

    print(f"""
╔══════════════════════════════════════════════════════════╗
║  🤖 מוקי — Telegram Bot                                  ║
║  שולח: {TOKEN[:8]}...{TOKEN[-4:]}                                  ║
║  Ctrl+C לעצירה                                          ║
╚══════════════════════════════════════════════════════════╝
""")

    app = Application.builder().token(TOKEN).build()

    # Register commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("bib", cmd_bib))
    app.add_handler(CommandHandler("qa", cmd_qa))
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("pipeline_status", cmd_pipeline_status))

    # Free text
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run
    print("  ✅ Bot running — waiting for messages...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
