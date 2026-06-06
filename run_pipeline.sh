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

cd "$SCRIPT_DIR"

# ── Single-instance lock: prevent two pipelines fighting over CLI ──
LOCK_OK=$(/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -c "
from pipeline_lock import is_locked, status
locked, data = is_locked()
if locked:
    s = status()
    print(f'BLOCKED:pid={s[\"pid\"]} age={s[\"age_min\"]}m')
else:
    print('FREE')
" 2>&1)
if [[ "$LOCK_OK" == BLOCKED:* ]]; then
    echo "  ⏸  pipeline already running — $LOCK_OK"
    echo "     (release manually if stale: python3 pipeline_lock.py --force-release)"
    exit 0
fi
echo "  🔓 lock available"

# ── full health gate (7 checks: compile, imports, CLI, calendar, disk, locks, memory) ──
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

# ── External watchdog (macOS has no `timeout` binary) ────────────────
# Yesterday's run hung 13.7h despite a Python daemon-thread watchdog and
# STEP_TIMEOUT=30min. The thread-level safeguards proved insufficient
# (worker threads can't be killed, subprocesses can hold open fds).
# Last resort: a bash sleep+kill that runs OUTSIDE Python.
HARD_KILL_MINUTES=120                          # 2h ceiling — generous, but real
PIPELINE_PID=""
HARDKILL_PID=""

_start_hardkill() {
    (
        sleep $((HARD_KILL_MINUTES * 60))
        if [ -n "$PIPELINE_PID" ] && kill -0 "$PIPELINE_PID" 2>/dev/null; then
            echo "" >&2
            echo "  🛑 HARD KILL: pipeline exceeded ${HARD_KILL_MINUTES}min — killing PID $PIPELINE_PID and children" >&2
            # Kill the whole process group (Python + any subprocess.run children)
            kill -TERM -"$PIPELINE_PID" 2>/dev/null || kill -TERM "$PIPELINE_PID" 2>/dev/null
            sleep 5
            kill -KILL -"$PIPELINE_PID" 2>/dev/null || kill -KILL "$PIPELINE_PID" 2>/dev/null
        fi
    ) &
    HARDKILL_PID=$!
}

_cancel_hardkill() {
    [ -n "$HARDKILL_PID" ] && kill "$HARDKILL_PID" 2>/dev/null
}

# ── run ───────────────────────────────────────────
case $MODE in
    research)
        echo "  📚 Full pipeline: research + LinkedIn + Blog + images (hard-kill at ${HARD_KILL_MINUTES}min)"
        /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 agent5_project_manager.py \
            "הרץ הכל — מחקר חדש, פוסט LinkedIn ומאמר בלוג. תוכן: linkedin blog" \
            --auto &
        PIPELINE_PID=$!
        _start_hardkill
        wait $PIPELINE_PID
        STATUS=$?
        _cancel_hardkill
        ;;
    podcast)
        echo "  🎙️  Podcast from existing material + image (hard-kill at ${HARD_KILL_MINUTES}min)"
        /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 agent5_project_manager.py \
            "צור פרק פודקאסט ממאמר קיים. תוכן: podcast" \
            --auto &
        PIPELINE_PID=$!
        _start_hardkill
        wait $PIPELINE_PID
        STATUS=$?
        _cancel_hardkill
        ;;
esac

# STATUS already set inside the case

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

    # ── Regenerate hook_winners memory (top hooks from past runs → Agent 3 prompt) ──
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 hook_log.py 2>&1 | tail -1 || true

    # ── Agent 8 — Publisher: daily digest of top ready posts to Telegram ──
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 agent8_publisher.py 2>&1 | tail -2 || true

    # ── Agent 10 — Weekly Curator: ranks queue by predicted performance ──
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 agent10_curator.py 2>&1 | tail -2 || true

    # ── Dedup checker: flag duplicate articles / paragraphs / hooks / over-cited refs ──
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 dedup_checker.py 2>&1 | tail -1 || true

    # ── Memory snapshots: daily versioning of _memory/*.md for diff/audit ──
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 memory_snapshots.py 2>&1 | tail -1 || true

    # ── Series memory: refresh active_series.md so next-run Planner sees it ──
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 series.py --regen 2>&1 | tail -1 || true

    # ── Log router: split cron log into namespaced files (Vercel Workflow pattern) ──
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 log_router.py 2>&1 | tail -2 || true

    # ── Failure analysis + performance learning (free, runs on existing data) ──
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 failure_analyzer.py 2>&1 | tail -2 || true
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 performance_learner.py 2>&1 | tail -2 || true

    # ── Agent 7: Research journal entry for this run ──
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 research_journal.py 2>&1 | tail -1 || true
else
    echo ""
    echo "  ❌ Failed (exit $STATUS) — $(date '+%H:%M')"
fi

exit $STATUS
