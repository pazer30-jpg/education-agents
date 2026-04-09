#!/bin/bash
# run_telegram.sh — הפעלת בוט טלגרם ברקע
# Usage:
#   ./run_telegram.sh          # הפעלה ברקע
#   ./run_telegram.sh --stop   # עצירה
#   ./run_telegram.sh --status # בדיקת סטטוס

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/output/.telegram_bot.pid"
LOG_FILE="$SCRIPT_DIR/output/telegram_bot.log"

case "${1:-start}" in
  --stop|stop)
    if [ -f "$PID_FILE" ]; then
      PID=$(cat "$PID_FILE")
      kill "$PID" 2>/dev/null && echo "⛔ Bot stopped (PID $PID)" || echo "Bot not running"
      rm -f "$PID_FILE"
    else
      echo "Bot not running"
    fi
    ;;

  --status|status)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "✅ Bot running (PID $(cat "$PID_FILE"))"
    else
      echo "❌ Bot not running"
      rm -f "$PID_FILE" 2>/dev/null
    fi
    ;;

  *)
    # Start
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Bot already running (PID $(cat "$PID_FILE"))"
      exit 0
    fi

    cd "$SCRIPT_DIR"
    nohup python3 telegram_bot.py >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "✅ Bot started (PID $!) — log: $LOG_FILE"
    ;;
esac
