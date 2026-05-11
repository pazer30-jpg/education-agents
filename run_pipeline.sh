#!/bin/bash
# run_pipeline.sh — הרצה מתוזמנת של מוקי
#
# לו"ז:
#   שני + רביעי 07:00 — מחקר + LinkedIn + בלוג + תמונות
#   חמישי 07:00       — פודקאסט ממאמר קיים + תמונה
#
# cron:
#   0 7 * * 1,3 /Users/ASUS/education-agents/run_pipeline.sh research
#   0 7 * * 4   /Users/ASUS/education-agents/run_pipeline.sh podcast
#
# שימוש ישיר:
#   ./run_pipeline.sh research    # מחקר + linkedin + blog
#   ./run_pipeline.sh podcast     # פודקאסט ממאמר קיים
#   ./run_pipeline.sh             # auto-detect by day

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/output"
LOG_FILE="$LOG_DIR/cron_$(date +%Y%m%d).log"
VENV="$SCRIPT_DIR/venv/bin/activate"

mkdir -p "$LOG_DIR"
exec >> "$LOG_FILE" 2>&1

# ── Detect mode ───────────────────────────────────
MODE="${1:-auto}"
if [ "$MODE" = "auto" ]; then
    DOW=$(date +%u)  # 1=Mon ... 7=Sun
    case $DOW in
        1|3) MODE="research" ;;  # Mon, Wed
        4)   MODE="podcast"  ;;  # Thu
        *)   echo "  ⏭  No pipeline scheduled for today ($(date +%A))"; exit 0 ;;
    esac
fi

echo ""
echo "══════════════════════════════════════════════"
echo "  Moki — $(date '+%d/%m/%Y %H:%M') | mode: $MODE"
echo "══════════════════════════════════════════════"

# ── activate venv ─────────────────────────────────
if [ -f "$VENV" ]; then
    source "$VENV"
    echo "  venv: active"
else
    echo "  venv: not found (using system python)"
fi

# ── full health gate (7 checks: compile, imports, CLI, calendar, disk, locks, memory) ──
cd "$SCRIPT_DIR"
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 test_health_gate.py --quiet
HEALTH=$?
if [ $HEALTH -ne 0 ]; then
    echo "  ❌ Health gate failed — pipeline blocked. Run: python3 test_health_gate.py"
    exit 1
fi
echo "  ✅ Health gate passed (7/7 checks)"

# ── performance regression check ──
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 test_perf_regression.py 2>&1 | grep -E "ratio|חריגה|בסדר"
PERF=$?
if [ $PERF -eq 2 ]; then
    echo "  🚨 Critical performance regression detected — aborting"
    exit 1
fi

# ── run ───────────────────────────────────────────
case $MODE in
    research)
        echo "  📚 Full pipeline: research + LinkedIn + Blog + images"
        /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 agent5_project_manager.py \
            "הרץ הכל — מחקר חדש, פוסט LinkedIn ומאמר בלוג. תוכן: linkedin blog" \
            --auto
        ;;
    podcast)
        echo "  🎙️  Podcast from existing material + image"
        /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 agent5_project_manager.py \
            "צור פרק פודקאסט ממאמר קיים. תוכן: podcast" \
            --auto
        ;;
esac

STATUS=$?

if [ $STATUS -eq 0 ]; then
    echo ""
    echo "  ✅ Done — $(date '+%H:%M')"
    READY=$(find "$LOG_DIR/ready" -type f -newer "$LOG_FILE" 2>/dev/null | wc -l | tr -d ' ')
    echo "  📁 $READY new files ready"

    # ── Auto-sync Obsidian (wikilinks + indexes + daily note) ──
    echo "  🌐 Syncing Obsidian..."
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 obsidian_bridge.py 2>&1 | tail -3
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 daily_note.py 2>&1 | tail -1

    # ── Sync learning to Obsidian memory (strong/weak topics + edit corrections) ──
    echo "  🧠 Syncing learning to memory..."
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 analytics.py --sync 2>&1 | tail -1 || true
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 edit_tracker.py learn 2>&1 | tail -1 || true

    # ── Regenerate code index (catches new/removed *.py files) ──
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 regenerate_index.py 2>&1 | tail -1 || true

    # ── Build agent health card + check QA trends ──
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 agent_health.py 2>&1 | tail -1 || true

    # ── Auto-organize Obsidian vault (move misplaced files to right folders) ──
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 obsidian_organizer.py --apply 2>&1 | tail -3 || true

    # ── Failure analysis + performance learning (free, runs on existing data) ──
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 failure_analyzer.py 2>&1 | tail -2 || true
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 performance_learner.py 2>&1 | tail -2 || true
else
    echo ""
    echo "  ❌ Failed (exit $STATUS) — $(date '+%H:%M')"
fi

exit $STATUS
