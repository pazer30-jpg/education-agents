"""
test_health_gate.py — Pre-pipeline health gate.
Runs in <60 seconds before any pipeline starts.
Returns exit 0 if safe to proceed, 1 if not.

Usage:
  python3 test_health_gate.py            # full check
  python3 test_health_gate.py --quiet    # only print failures
"""

import sys
import subprocess
from pathlib import Path
from datetime import datetime

PROJECT_DIR = Path(__file__).parent
QUIET = "--quiet" in sys.argv

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"

results = []


def check(name: str, fn) -> bool:
    """Run a check, record result."""
    try:
        ok, msg = fn()
        results.append((ok, name, msg))
        if not QUIET or not ok:
            icon = PASS if ok else FAIL
            print(f"  {icon} {name}: {msg}")
        return ok
    except Exception as e:
        results.append((False, name, f"crashed: {e}"))
        print(f"  {FAIL} {name}: crashed: {e}")
        return False


def check_compile_all():
    """All Python files must compile."""
    files = list(PROJECT_DIR.glob("*.py"))
    failed = []
    for f in files:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(f)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            failed.append(f.name)
    if failed:
        return False, f"{len(failed)} files broken: {', '.join(failed[:3])}"
    return True, f"{len(files)} files compile clean"


def check_imports():
    """Critical modules can import without errors."""
    modules = [
        "claude_cli", "memory", "agent0_planner", "agent1_researcher",
        "agent2_writer", "agent3_content_creator", "agent5_project_manager",
        "voice_profile", "qa_checker",
    ]
    failed = []
    for m in modules:
        result = subprocess.run(
            [sys.executable, "-c", f"import {m}"],
            capture_output=True, text=True, timeout=20, cwd=str(PROJECT_DIR),
        )
        if result.returncode != 0:
            failed.append(f"{m}: {result.stderr.strip().split(chr(10))[-1][:60]}")
    if failed:
        return False, f"{len(failed)} import errors: {failed[0]}"
    return True, f"{len(modules)} modules import OK"


def check_claude_cli():
    """Claude CLI binary is reachable."""
    result = subprocess.run(
        [sys.executable, "-c", "from claude_cli import _find_claude_bin; b = _find_claude_bin(); print(b or '')"],
        capture_output=True, text=True, timeout=15, cwd=str(PROJECT_DIR),
    )
    path = result.stdout.strip()
    if not path or not Path(path).exists():
        return False, f"CLI not found (path: {path or 'None'})"
    return True, f"found at {path[-60:]}"


def check_calendar_dates():
    """Hebrew calendar arrays are populated for current + next year (catches stale data, not legitimately quiet periods)."""
    current_year = datetime.now().year
    result = subprocess.run(
        [sys.executable, "-c",
         f"from agent0_planner import HEBREW_CALENDAR; "
         f"print(len(HEBREW_CALENDAR.get({current_year}, [])), len(HEBREW_CALENDAR.get({current_year + 1}, [])))"],
        capture_output=True, text=True, timeout=10, cwd=str(PROJECT_DIR),
    )
    try:
        n_curr, n_next = map(int, result.stdout.strip().split())
    except ValueError:
        return False, f"calendar broken: {result.stderr[:100]}"
    if n_curr == 0 or n_next == 0:
        return False, f"calendar missing entries: {current_year}={n_curr}, {current_year + 1}={n_next}"
    # Info-only: how many upcoming events
    upcoming = subprocess.run(
        [sys.executable, "-c",
         "from agent0_planner import _get_upcoming_events; print(len(_get_upcoming_events(60)))"],
        capture_output=True, text=True, timeout=10, cwd=str(PROJECT_DIR),
    )
    try:
        n_up = int(upcoming.stdout.strip())
    except ValueError:
        n_up = 0
    return True, f"{current_year}={n_curr}, {current_year + 1}={n_next} populated · {n_up} upcoming in 60d"


def check_disk_space():
    """At least 500MB free in output directory."""
    import shutil
    output_dir = PROJECT_DIR / "output"
    if not output_dir.exists():
        return False, "output/ directory missing"
    stat = shutil.disk_usage(output_dir)
    free_mb = stat.free / (1024 * 1024)
    if free_mb < 500:
        return False, f"only {free_mb:.0f}MB free — need 500MB+"
    return True, f"{free_mb:.0f}MB free"


def check_no_stale_lock():
    """No leftover lock/pid files from crashed runs."""
    lock_files = list(PROJECT_DIR.glob("output/*.lock")) + list(PROJECT_DIR.glob("*.pid"))
    if lock_files:
        return False, f"stale lock(s): {[f.name for f in lock_files]}"
    return True, "no stale locks"


def check_memory_size():
    """Memory.json is not corrupted."""
    mem_file = PROJECT_DIR / "output" / "memory.json"
    if not mem_file.exists():
        return True, "memory.json absent (will be created)"
    import json
    try:
        data = json.loads(mem_file.read_text(encoding="utf-8"))
        return True, f"{len(data.get('papers', {}))} papers, {len(data.get('researched_topics', []))} topics"
    except Exception as e:
        return False, f"memory.json corrupted: {e}"


def main():
    print(f"\n🛡  Pre-pipeline health gate — {datetime.now().strftime('%d/%m/%Y %H:%M')}\n")

    checks = [
        ("compile",       check_compile_all),
        ("imports",       check_imports),
        ("claude_cli",    check_claude_cli),
        ("calendar",      check_calendar_dates),
        ("disk_space",    check_disk_space),
        ("locks",         check_no_stale_lock),
        ("memory",        check_memory_size),
    ]

    for name, fn in checks:
        check(name, fn)

    # Summary
    n_pass = sum(1 for ok, _, _ in results if ok)
    n_fail = len(results) - n_pass
    print(f"\n  {n_pass}/{len(results)} checks passed")

    if n_fail:
        print(f"\n  ⛔ {n_fail} blockers — pipeline should NOT run")
        return 1
    print("\n  ✅ All checks passed — safe to start pipeline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
