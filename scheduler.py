"""
scheduler.py — מתזמן אוטונומי
מריץ את ה-pipeline אוטומטית לפי לוח זמנים.

אפשרויות הפעלה:
  python scheduler.py --every 24h                  # כל יום
  python scheduler.py --every 12h --content blog   # כל 12 שעות, בלוג בלבד
  python scheduler.py --once                        # הרצה חד-פעמית ועצירה
  python scheduler.py --status                      # הצג מצב הרצות אחרונות

הזמנות נשמרות ב: output/scheduler_log.json
"""

import json
import time
import sys
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

from config import OUTPUT_DIR

LOG_FILE = OUTPUT_DIR / "scheduler_log.json"


# ─────────────────────────────────────────────
# Log helpers
# ─────────────────────────────────────────────

def _load_log() -> dict:
    if LOG_FILE.exists():
        try:
            return json.loads(LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"runs": [], "next_run": None, "config": {}}


def _save_log(log: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text(
        json.dumps(log, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8"
    )


def _record_run(status: str, topic: str, duration_s: float,
                content_types: list, error: str = ""):
    log = _load_log()
    log["runs"].append({
        "time":     datetime.now().isoformat(),
        "status":   status,          # "success" | "failed" | "skipped"
        "topic":    topic,
        "content":  content_types,
        "duration": f"{duration_s:.0f}s",
        "error":    error,
    })
    log["runs"] = log["runs"][-50:]  # שמור רק 50 הרצות אחרונות
    _save_log(log)


# ─────────────────────────────────────────────
# Interval parser
# ─────────────────────────────────────────────

def _parse_interval(interval_str: str) -> int:
    """מחזיר שניות. תומך ב: 30m, 6h, 1d, 2w"""
    s = interval_str.strip().lower()
    units = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    for unit, mult in units.items():
        if s.endswith(unit):
            return int(s[:-1]) * mult
    return int(s)


def _seconds_until_next(interval_s: int, log: dict) -> int:
    """כמה שניות עד ההרצה הבאה לפי ההרצה האחרונה."""
    runs = log.get("runs", [])
    if not runs:
        return 0
    last_time = datetime.fromisoformat(runs[-1]["time"])
    elapsed   = (datetime.now() - last_time).total_seconds()
    return max(0, int(interval_s - elapsed))


# ─────────────────────────────────────────────
# Single run
# ─────────────────────────────────────────────

def _run_once(topic: str, content_types: list, hints: list) -> bool:
    """מפעיל pipeline אחד דרך agent5_project_manager. Returns True אם הצליח."""
    content_str = " ".join(content_types)
    request = f"הרץ pipeline מלא על נושא {topic}, תוכן: {content_str}"

    print(f"\n🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')} — מתחיל הרצה")
    print(f"   נושא: {topic} | תוכן: {content_str}\n")

    t0 = time.time()
    try:
        result = subprocess.run(
            [sys.executable, "agent5_project_manager.py", request, "--auto"],
            capture_output=False,
            timeout=3600,  # שעה מקסימום
        )
        duration = time.time() - t0
        success  = result.returncode == 0
        status   = "success" if success else "failed"
        error    = "" if success else f"exit code {result.returncode}"

    except subprocess.TimeoutExpired:
        duration = time.time() - t0
        status, success, error = "failed", False, "timeout after 1h"
    except Exception as e:
        duration = time.time() - t0
        status, success, error = "failed", False, str(e)

    _record_run(status, topic, duration, content_types, error)
    icon = "✅" if success else "❌"
    print(f"\n{icon} הרצה {status} תוך {duration:.0f}s")
    return success


# ─────────────────────────────────────────────
# Status display
# ─────────────────────────────────────────────

def _show_status():
    log  = _load_log()
    runs = log.get("runs", [])
    cfg  = log.get("config", {})

    print(f"\n{'='*55}")
    print(f"📅 Scheduler Status — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*55}")

    if cfg:
        print(f"  הגדרה:    {cfg.get('interval','?')} | "
              f"נושא: {cfg.get('topic','?')} | "
              f"תוכן: {cfg.get('content','?')}")

    nxt = log.get("next_run")
    if nxt:
        nxt_dt    = datetime.fromisoformat(nxt)
        remaining = (nxt_dt - datetime.now()).total_seconds()
        if remaining > 0:
            h, m = divmod(int(remaining // 60), 60)
            print(f"  הרצה הבאה: {nxt_dt.strftime('%d/%m %H:%M')} (בעוד {h}h {m}m)")
        else:
            print(f"  הרצה הבאה: עכשיו")

    if not runs:
        print("  אין הרצות קודמות.")
        return

    print(f"\n  הרצות אחרונות ({min(10, len(runs))} מתוך {len(runs)}):")
    for r in runs[-10:][::-1]:
        icon = {"success": "✅", "failed": "❌", "skipped": "⏭"}.get(r["status"], "?")
        t    = r["time"][:16].replace("T", " ")
        print(f"    {icon} {t}  {r['duration']:>6}  {r['topic'][:30]}"
              + (f"  ← {r['error']}" if r.get("error") else ""))

    successes = sum(1 for r in runs if r["status"] == "success")
    print(f"\n  סה״כ: {len(runs)} הרצות | {successes} הצלחות | {len(runs)-successes} כשלים")


# ─────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────

def run_scheduler(interval_str: str, topic: str, content_types: list,
                  hints: list, run_once: bool = False):
    interval_s = _parse_interval(interval_str)
    h, m = divmod(interval_s // 60, 60)
    interval_label = (f"{h}h {m}m" if h else f"{m}m").strip() or f"{interval_s}s"

    print(f"""
╔══════════════════════════════════════════════════════╗
║  📅 Education Agents Scheduler                       ║
╚══════════════════════════════════════════════════════╝
  תדירות: כל {interval_label}
  נושא:   {topic}
  תוכן:   {' + '.join(content_types)}
  {"הרצה חד-פעמית" if run_once else "לחץ Ctrl+C לעצירה"}
""")

    log = _load_log()
    log["config"] = {
        "interval": interval_label,
        "topic":    topic,
        "content":  content_types,
        "hints":    hints,
        "started":  datetime.now().isoformat(),
    }
    _save_log(log)

    iteration = 0
    try:
        while True:
            iteration += 1
            log = _load_log()

            if iteration > 1 or _seconds_until_next(interval_s, log) > 0:
                wait = _seconds_until_next(interval_s, log)
                if wait > 0:
                    next_run = datetime.now() + timedelta(seconds=wait)
                    log["next_run"] = next_run.isoformat()
                    _save_log(log)

                    h2, rem = divmod(wait, 3600)
                    m2, s2  = divmod(rem, 60)
                    print(f"\n💤 הרצה {iteration} — ממתין {h2}h {m2}m {s2}s "
                          f"(עד {next_run.strftime('%H:%M')})")

                    slept = 0
                    while slept < wait:
                        chunk = min(60, wait - slept)
                        time.sleep(chunk)
                        slept += chunk

            _run_once(topic, content_types, hints)

            if run_once:
                print("\n✅ הרצה חד-פעמית הסתיימה.")
                break

            log = _load_log()
            log["next_run"] = (datetime.now() + timedelta(seconds=interval_s)).isoformat()
            _save_log(log)

    except KeyboardInterrupt:
        print(f"\n\n⛔ Scheduler עצר ידנית אחרי {iteration-1} הרצות.")
        _show_status()


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="מתזמן אוטונומי ל-Education Agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
דוגמאות:
  python3 scheduler.py --every 24h
  python3 scheduler.py --every 12h --content blog linkedin
  python3 scheduler.py --every 1d --topic "שייכות" --hints "מחינות" "נוער"
  python3 scheduler.py --once --content linkedin
  python3 scheduler.py --status
        """,
    )
    parser.add_argument("--every",   default="24h",
                        help="תדירות: 30m / 6h / 1d / 2w (ברירת מחדל: 24h)")
    parser.add_argument("--topic",   default="חינוך בלתי פורמלי",
                        help="נושא ראשי")
    parser.add_argument("--hints",   nargs="*", default=[],
                        help="רמזים לפלאנר")
    parser.add_argument("--content", nargs="+",
                        choices=["linkedin", "blog", "podcast"],
                        default=["linkedin"],
                        help="פלטפורמות")
    parser.add_argument("--once",    action="store_true",
                        help="הרצה חד-פעמית ועצירה")
    parser.add_argument("--status",  action="store_true",
                        help="הצג סטטוס בלבד")

    args = parser.parse_args()

    if args.status:
        _show_status()
    else:
        run_scheduler(
            interval_str  = args.every,
            topic         = args.topic,
            content_types = args.content,
            hints         = args.hints,
            run_once      = args.once,
        )
