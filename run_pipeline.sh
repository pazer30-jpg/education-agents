#!/bin/bash
# run_pipeline.sh — הרצה יומית דרך cron
#
# הגדרת cron (כל יום ב-09:00):
#   crontab -e
#   0 9 * * * /Users/ASUS/Desktop/education-agents/run_pipeline.sh
#
# שימוש ישיר:
#   ./run_pipeline.sh
#   ./run_pipeline.sh --parallel --bilingual
#   TOPIC="שייכות בחינוך" ./run_pipeline.sh

set -euo pipefail

# ── הגדרות ────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOPIC="${TOPIC:-חינוך בלתי פורמלי}"
CONTENT="${CONTENT:-linkedin blog}"
LOG_DIR="$SCRIPT_DIR/output"
LOG_FILE="$LOG_DIR/cron_$(date +%Y%m%d).log"
VENV="$SCRIPT_DIR/venv/bin/activate"

# ── לוגינג ────────────────────────────────────────
mkdir -p "$LOG_DIR"
exec >> "$LOG_FILE" 2>&1

echo ""
echo "══════════════════════════════════════════════"
echo "  Education Agents — $(date '+%d/%m/%Y %H:%M')"
echo "  נושא: $TOPIC"
echo "══════════════════════════════════════════════"

# ── activate venv ─────────────────────────────────
if [ -f "$VENV" ]; then
    source "$VENV"
    echo "  venv: active"
else
    echo "  venv: not found (using system python)"
fi

# ── run pipeline (uses claude_cli — no API key needed) ──
cd "$SCRIPT_DIR"

EXTRA_FLAGS=""
for arg in "$@"; do
    EXTRA_FLAGS="$EXTRA_FLAGS $arg"
done

python3 orchestrator.py "$TOPIC" \
    --content $CONTENT \
    --parallel \
    $EXTRA_FLAGS

STATUS=$?

if [ $STATUS -eq 0 ]; then
    echo ""
    echo "  ✅ הריצה הסתיימה בהצלחה — $(date '+%H:%M')"
else
    echo ""
    echo "  ❌ הריצה נכשלה (exit code: $STATUS) — $(date '+%H:%M')"
fi

exit $STATUS
