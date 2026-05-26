"""
pipeline_lock.py — Single-instance guard for the pipeline.

Prevents two concurrent runs from fighting over the Claude CLI (which is
single-threaded per machine and rate-limited). Common scenario this fixes:
  - Cron fires at 07:00
  - You manually start ./run_pipeline.sh at 07:02 to test something
  - Both runs fight, both fail, $40 wasted on doomed retries

Lock format: output/_state/pipeline.lock contains JSON {pid, started_at, host}.
Stale lock detection: if the PID isn't alive (process died without cleanup),
auto-acquire. Lock auto-releases on process exit (atexit hook).

Usage from agent5 or run_pipeline.sh:
  from pipeline_lock import acquire, release, is_locked
  if not acquire(blocking=False):
      print("⏸  pipeline already running — deferring")
      sys.exit(0)
  try:
      ... run pipeline ...
  finally:
      release()
"""

import atexit
import json
import os
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR

LOCK_FILE = OUTPUT_DIR / "_state" / "pipeline.lock"
LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)


def _read_lock() -> dict | None:
    if not LOCK_FILE.exists():
        return None
    try:
        return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    """Cross-platform PID check (POSIX). Returns True if process exists."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)  # signal 0 = check existence without killing
        return True
    except (OSError, ProcessLookupError):
        return False


def is_locked() -> tuple[bool, dict | None]:
    """Returns (locked, lock_data_or_None). Cleans stale locks transparently."""
    data = _read_lock()
    if not data:
        return False, None
    pid = int(data.get("pid", 0))
    if not _pid_alive(pid):
        # Stale lock — owner died
        try:
            LOCK_FILE.unlink()
        except Exception:
            pass
        return False, None
    return True, data


def acquire(blocking: bool = False, max_wait: int = 60) -> bool:
    """
    Try to acquire the lock. Returns True if acquired, False if held by another.

    blocking=False (default): fail immediately if held.
    blocking=True: poll for up to max_wait seconds before giving up.
    """
    start = time.time()
    while True:
        locked, existing = is_locked()
        if not locked:
            # Write our lock
            payload = {
                "pid":        os.getpid(),
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "host":       socket.gethostname(),
                "argv":       sys.argv[:5],  # truncate
            }
            try:
                # Atomic-ish: write then rename
                tmp = LOCK_FILE.with_suffix(".lock.tmp")
                tmp.write_text(json.dumps(payload), encoding="utf-8")
                tmp.replace(LOCK_FILE)
                atexit.register(release)
                return True
            except Exception:
                return False
        if not blocking:
            return False
        if time.time() - start > max_wait:
            return False
        time.sleep(2)


def release() -> bool:
    """Release the lock if we own it (PID match). Returns True if released."""
    data = _read_lock()
    if not data:
        return True
    if int(data.get("pid", 0)) != os.getpid():
        return False  # not ours
    try:
        LOCK_FILE.unlink()
        return True
    except Exception:
        return False


def status() -> dict:
    """For CLI / dashboard — describe the lock state."""
    locked, data = is_locked()
    if not locked:
        return {"locked": False}
    started = data.get("started_at", "")
    try:
        age_min = (datetime.now() - datetime.fromisoformat(started)).total_seconds() / 60
    except Exception:
        age_min = 0
    return {
        "locked":     True,
        "pid":        data.get("pid"),
        "started_at": started,
        "host":       data.get("host"),
        "age_min":    round(age_min, 1),
        "argv":       data.get("argv"),
    }


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--status":
        st = status()
        if st["locked"]:
            print(f"🔒 LOCKED · pid={st['pid']} · age={st['age_min']} min · started {st['started_at']}")
        else:
            print("🔓 unlocked")
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "--force-release":
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
            print("✅ lock force-released")
        else:
            print("ℹ️ no lock file present")
        sys.exit(0)
    # Default: smoke test
    print("Lock status:", status())
