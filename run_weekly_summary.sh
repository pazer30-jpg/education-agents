#!/bin/bash
# run_weekly_summary.sh — סיכום שבועי דרך cron
# cron: 0 10 * * 0 /Users/ASUS/Desktop/education-agents/run_weekly_summary.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LOG="output/cron_$(date +%Y%m%d).log"
echo "" >> "$LOG"
echo "── Weekly Summary $(date '+%d/%m/%Y %H:%M') ──" >> "$LOG"

python3 weekly_summary.py --save >> "$LOG" 2>&1
python3 bibliography.py >> "$LOG" 2>&1

echo "── Summary done ──" >> "$LOG"
