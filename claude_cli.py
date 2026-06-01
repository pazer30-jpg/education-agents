"""
claude_cli.py — Wrapper ל-Claude CLI
משתמש ב-Claude Code CLI כ-subprocess, עם fallback ל-Anthropic API.

יתרונות:
  - לא צריך ANTHROPIC_API_KEY בקוד (Claude CLI מנהל auth)
  - תומך ב-extended thinking ופיצ'רים מתקדמים
  - מצב --dangerously-skip-permissions להרצה אוטומטית

שימוש:
  from claude_cli import ask_claude, ask_claude_json

  text = ask_claude("סכם את המאמר הזה: ...")
  data = ask_claude_json("החזר JSON עם שדות title, summary: ...")
"""

import subprocess
import json
import re
import shutil
import os
from pathlib import Path


# ─────────────────────────────────────────────
# Find Claude CLI binary
# ─────────────────────────────────────────────

def _find_claude_bin() -> str | None:
    """
    מחפש את Claude CLI בנתיבים אפשריים.
    נקרא בכל קריאה (לא cache) — כדי לעמוד בעדכוני גרסאות של VSCode extension.
    """
    # Env override wins
    env = os.environ.get("CLAUDE_BIN")
    if env and Path(env).exists():
        return env

    # VSCode extension — prefer newest version (sort descending)
    vscode_bins = sorted(
        Path.home().glob(
            ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude"
        ),
        reverse=True,  # newest version first
    )
    for p in vscode_bins:
        if p.exists():
            return str(p)

    # Other locations
    for c in (
        shutil.which("claude"),
        str(Path.home() / ".claude" / "local" / "claude"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ):
        if c and Path(c).exists():
            return c
    return None


# Kept for backward compat. Do NOT use for dispatch — call _find_claude_bin() fresh.
CLAUDE_BIN = _find_claude_bin()
CLAUDE_MODEL = "sonnet"  # "sonnet" | "opus"


# ─────────────────────────────────────────────
# Rate limiter — prevent budget blowup
# ─────────────────────────────────────────────

import os as _os
import time as _time
import threading
import json as _json
from datetime import datetime as _dt
from pathlib import Path as _Path

# Daily budget — persists across runs. File path resolved lazily.
_DAILY_BUDGET_FILE = None


def _get_daily_file():
    global _DAILY_BUDGET_FILE
    if _DAILY_BUDGET_FILE is None:
        try:
            from config import OUTPUT_DIR as _OD
            _DAILY_BUDGET_FILE = _OD / "_state" / "daily_budget.json"
            _DAILY_BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            _DAILY_BUDGET_FILE = _Path("/tmp/moki_daily_budget.json")
    return _DAILY_BUDGET_FILE


def _load_daily_budget() -> dict:
    f = _get_daily_file()
    if f.exists():
        try:
            return _json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_daily_budget(data: dict):
    try:
        _get_daily_file().write_text(_json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _today() -> str:
    return _dt.now().strftime("%Y-%m-%d")


def _daily_cap_usd() -> float:
    """Daily cap from env (MOKI_DAILY_BUDGET) or default.

    Default is $50 — a full pipeline run costs ~$15-20, so $50 leaves room
    for two runs plus ad-hoc work in a single day. $30 was too tight and
    killed runs mid-pipeline.
    """
    env = _os.environ.get("MOKI_DAILY_BUDGET")
    if env:
        try:
            return float(env)
        except Exception:
            pass
    return 50.0


class DailyBudgetExceeded(Exception):
    """Raised when today's Claude spend exceeds daily cap."""
    pass


class _RateLimiter:
    """Rate limiter + per-session + daily budget tracking."""
    def __init__(self, max_per_minute: int = 8):
        self.max_per_minute = max_per_minute
        self.calls: list[float] = []
        self.total_budget: float = 0
        self.max_budget: float = 100.0  # session cap — separate from daily
        self._lock = threading.Lock()

    def _daily_total(self) -> float:
        data = _load_daily_budget()
        return data.get(_today(), 0.0)

    def _record_daily(self, budget: float):
        data = _load_daily_budget()
        today = _today()
        # Garbage-collect entries older than 30 days
        cutoff = (_dt.now() - __import__("datetime").timedelta(days=30)).strftime("%Y-%m-%d")
        data = {k: v for k, v in data.items() if k >= cutoff}
        data[today] = round(data.get(today, 0.0) + budget, 4)
        _save_daily_budget(data)

    def wait(self, budget: float):
        with self._lock:
            # Daily cap check — hard stop
            cap = _daily_cap_usd()
            today_total = self._daily_total()
            if today_total + budget > cap:
                msg = (f"❌ Daily budget cap reached: "
                       f"${today_total:.2f}/{cap:.2f} used today. "
                       f"Refusing to spend ${budget:.2f}. "
                       f"Set MOKI_DAILY_BUDGET=<higher> to override.")
                print(f"\n  {msg}\n")
                raise DailyBudgetExceeded(msg)

            now = _time.time()
            # Remove calls older than 60s
            self.calls = [t for t in self.calls if now - t < 60]
            # Wait if at limit
            if len(self.calls) >= self.max_per_minute:
                wait = 60 - (now - self.calls[0])
                if wait > 0:
                    print(f"  ⏳ Rate limit: waiting {wait:.0f}s...")
                    _time.sleep(wait)
            # Session budget guard — warn but don't crash
            if self.total_budget + budget > self.max_budget:
                print(f"  ⚠️  Session budget: ${self.total_budget:.2f}/${self.max_budget:.2f} used.")
            self.calls.append(_time.time())
            self.total_budget += budget
            self._record_daily(budget)


_limiter = _RateLimiter()


def reset_budget():
    """Reset session budget counter — call at start of new pipeline run."""
    _limiter.total_budget = 0


def daily_budget_status() -> dict:
    """Returns current daily spend status."""
    data = _load_daily_budget()
    today = _today()
    spent = data.get(today, 0.0)
    cap = _daily_cap_usd()
    return {
        "today": today,
        "spent_usd": round(spent, 4),
        "cap_usd": cap,
        "remaining_usd": round(cap - spent, 4),
        "percent": round((spent / cap) * 100, 1) if cap > 0 else 0,
    }


# ─────────────────────────────────────────────
# Core call
# ─────────────────────────────────────────────

class CLIUnavailable(Exception):
    """Raised when Claude CLI is unavailable after all retries — caller should defer."""
    pass


def _wait_for_cli(max_wait: int = 60) -> str | None:
    """
    Wait for Claude CLI to become available (handles VSCode extension updates).
    Returns binary path or None if exhausted.
    """
    waited = 0
    while waited < max_wait:
        path = _find_claude_bin()
        if path:
            return path
        if waited == 0:
            print(f"  [CLI] ⏳ binary not found — waiting (VSCode may be updating)...")
        _time.sleep(10)
        waited += 10
    return None


def ask_claude(prompt: str, system: str = "", max_budget: float = 2.0,
               timeout: int = 900, max_retries: int = 3) -> str:
    """
    Call Claude CLI with a prompt, return text response.
    Strategy: 3 retries with exponential backoff before falling back to API.
    Falls back to Anthropic API if CLI not available AND api key set.
    If neither works → raises CLIUnavailable (caller can defer).

    timeout: subprocess timeout in seconds. Default 900 (15 min).
    max_retries: number of CLI retries before fallback (default 3).
    """
    # Wait for binary if not immediately available
    claude_bin = _find_claude_bin() or _wait_for_cli(max_wait=60)
    if not claude_bin:
        return _api_fallback_or_defer(prompt, system, max_budget)

    _limiter.wait(max_budget)

    cmd = [
        claude_bin,
        "-p",
        "--model", CLAUDE_MODEL,
        "--dangerously-skip-permissions",
        f"--max-budget-usd={max_budget}",
    ]

    if system:
        cmd += ["--append-system-prompt", system]

    cmd.append(prompt)

    # Smart retry: 3 attempts with exponential backoff (5s, 15s, 30s)
    backoffs = [5, 15, 30]
    last_error = ""
    for attempt in range(1, max_retries + 1):
        # Re-resolve binary in case VSCode updated mid-loop
        claude_bin = _find_claude_bin()
        if not claude_bin:
            last_error = "binary disappeared mid-retry"
            if attempt < max_retries:
                wait = backoffs[min(attempt - 1, len(backoffs) - 1)]
                print(f"  [CLI] retry {attempt}/{max_retries} — wait {wait}s")
                _time.sleep(wait)
                continue
            break
        cmd[0] = claude_bin  # update path in case it changed

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            last_error = (result.stderr or "").strip()[:200] or f"exit {result.returncode}"
        except subprocess.TimeoutExpired:
            last_error = f"timeout after {timeout}s"
            print(f"  [CLI] ⏱  attempt {attempt}/{max_retries}: {last_error}")
            break  # don't retry timeouts — call probably stuck
        except (FileNotFoundError, OSError) as e:
            last_error = str(e)

        if attempt < max_retries:
            wait = backoffs[min(attempt - 1, len(backoffs) - 1)]
            print(f"  [CLI] attempt {attempt}/{max_retries} failed ({last_error[:80]}) — retry in {wait}s")
            _time.sleep(wait)

    print(f"  [CLI] all {max_retries} retries exhausted — last error: {last_error[:120]}")
    return _api_fallback_or_defer(prompt, system, max_budget)


def _api_fallback_or_defer(prompt: str, system: str, max_budget: float) -> str:
    """
    Try API fallback. If no API key, raise CLIUnavailable to let caller defer.
    """
    if has_api_key():
        return _api_fallback(prompt, system, max_budget)
    raise CLIUnavailable(
        "Claude CLI unavailable after retries, no API key for fallback. "
        "Pipeline should defer this step and retry next run."
    )


def ask_claude_json(prompt: str, system: str = "", max_budget: float = 2.0,
                    timeout: int = 900) -> dict | list:
    """
    Call Claude CLI and parse JSON from the response.
    """
    json_prompt = prompt + "\n\nIMPORTANT: Return ONLY valid JSON, no markdown, no explanation."
    raw = ask_claude(json_prompt, system=system, max_budget=max_budget, timeout=timeout)

    # Strip markdown wrapper if present (```json ... ```)
    stripped = raw.strip()
    if stripped.startswith("```"):
        # Remove opening ```json or ```
        first_nl = stripped.find("\n")
        if first_nl > 0:
            stripped = stripped[first_nl + 1:]
        # Remove closing ``` if present
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3].rstrip()
        raw = stripped

    # Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try extracting first JSON array or object
    match = re.search(r'(\[[\s\S]+\]|\{[\s\S]+\})', raw)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try repairing truncated JSON — close open braces/brackets
    repaired = _repair_truncated_json(raw)
    if repaired is not None:
        return repaired

    raise ValueError(f"Could not parse JSON from Claude output:\n{raw[:400]}")


def _repair_truncated_json(raw: str) -> dict | list | None:
    """Attempt to repair truncated JSON by closing open strings/braces/brackets."""
    # Find the start of JSON
    start = -1
    for i, c in enumerate(raw):
        if c in ('{', '['):
            start = i
            break
    if start < 0:
        return None

    text = raw[start:].rstrip()

    # Count open structures
    open_braces = text.count('{') - text.count('}')
    open_brackets = text.count('[') - text.count(']')

    if open_braces <= 0 and open_brackets <= 0:
        return None  # not a truncation issue

    # Strategy: cut back to last complete key-value pair, then close structures
    # Find last complete entry point (after a comma or opening brace/bracket)
    best_cut = -1
    for candidate in [
        text.rfind('",'),         # end of string value + comma
        text.rfind('"},'),        # end of object in array
        text.rfind('}]'),         # end of last object in array
        text.rfind('],'),         # end of array + comma
        text.rfind(': "'),        # start of string value (cut value)
    ]:
        if candidate > best_cut:
            best_cut = candidate

    attempts = []

    # Attempt 1: cut at last clean boundary
    if best_cut > 0:
        for cut_offset in [2, 3, 1]:  # try after '",', after '"},' etc.
            cut_text = text[:best_cut + cut_offset].rstrip().rstrip(',')
            ob = cut_text.count('{') - cut_text.count('}')
            ol = cut_text.count('[') - cut_text.count(']')
            closed = cut_text + ']' * max(0, ol) + '}' * max(0, ob)
            attempts.append(closed)

    # Attempt 2: close unterminated string + structures
    if text.count('"') % 2 != 0:
        fixed = text + '"'
        ob = fixed.count('{') - fixed.count('}')
        ol = fixed.count('[') - fixed.count(']')
        closed = fixed + ']' * max(0, ol) + '}' * max(0, ob)
        attempts.append(closed)

    # Attempt 3: brute force close
    closed = text + ']' * max(0, open_brackets) + '}' * max(0, open_braces)
    attempts.append(closed)

    for attempt in attempts:
        try:
            result = json.loads(attempt)
            print("  [JSON] ⚠️  Repaired truncated JSON — some data may be incomplete")
            return result
        except json.JSONDecodeError:
            continue

    return None


# ─────────────────────────────────────────────
# API fallback
# ─────────────────────────────────────────────

def _load_dotenv_into_environ() -> None:
    """
    Load .env into os.environ (idempotent — won't overwrite already-set vars).
    Reads: project/.env, ~/.env, ~/.claude/.env in order.

    Without this, MOKI_DAILY_BUDGET=75 in .env was silently ignored — cron
    shells don't inherit user shell env, so the file values never reached
    Python. Triggered today's 'Daily budget cap $52.70/50.00' surprise.
    """
    for env_path in [
        Path(__file__).parent / ".env",
        Path.home() / ".env",
        Path.home() / ".claude" / ".env",
    ]:
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                # Don't clobber values already in env (shell-set wins over .env)
                if key and key not in _os.environ:
                    _os.environ[key] = value
        except Exception:
            pass


# Load .env at module import — must run BEFORE _daily_cap_usd is called.
_load_dotenv_into_environ()


def _load_dotenv_key() -> str | None:
    """Backwards-compat: return ANTHROPIC_API_KEY (now via os.environ after autoload)."""
    return _os.environ.get("ANTHROPIC_API_KEY") or None


def has_api_key() -> bool:
    """Check whether an Anthropic API key is available (env var or .env file)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY") or _load_dotenv_key())


def _api_fallback(prompt: str, system: str, max_budget: float) -> str:
    """משתמש ב-Anthropic API ישיר אם CLI לא זמין."""
    _limiter.wait(max_budget)  # rate limit also applies to API fallback
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "Claude CLI not found and anthropic package not installed. "
            "Install with: pip install anthropic"
        )

    api_key = (
        os.environ.get("ANTHROPIC_API_KEY")
        or _load_dotenv_key()
    )

    if not api_key:
        print("  ⚠️ API fallback: no ANTHROPIC_API_KEY found. Create .env file with ANTHROPIC_API_KEY=sk-...")
        raise RuntimeError(
            "Claude CLI not available and no ANTHROPIC_API_KEY found. "
            "Set the env var or create a .env file with ANTHROPIC_API_KEY=sk-..."
        )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        messages = [{"role": "user", "content": prompt}]
        kwargs = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 8192,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        resp = client.messages.create(**kwargs)
        return resp.content[0].text
    except Exception as e:
        raise RuntimeError(f"Both CLI and API failed: {e}")


# ─────────────────────────────────────────────
# Status check
# ─────────────────────────────────────────────

def check_cli_available() -> bool:
    return CLAUDE_BIN is not None


def health_check(timeout: int = 120) -> dict:
    """
    Verify Claude CLI is available and responsive.
    Returns {"ok": True, "bin": path, "response_time": elapsed, "api_fallback": bool} on success,
    or {"ok": False, "error": "descriptive message", "api_fallback": bool} on failure.
    Never raises — catches all exceptions.

    timeout default 120s: cold-start of Claude CLI can take 60-90s under load.
    Previous 30s caused false-positive failures that blocked the entire pipeline.
    """
    api_fallback_available = has_api_key()
    try:
        claude_bin = _find_claude_bin()
        if not claude_bin:
            return {"ok": False, "error": "Claude CLI binary not found in any known location",
                    "api_fallback": api_fallback_available}

        start = _time.time()
        response = ask_claude("Say OK", max_budget=0.05, timeout=timeout)
        elapsed = round(_time.time() - start, 2)

        if not response or not response.strip():
            return {"ok": False, "error": "Claude CLI returned empty response",
                    "api_fallback": api_fallback_available}

        return {"ok": True, "bin": claude_bin, "response_time": elapsed,
                "api_fallback": api_fallback_available}

    except Exception as e:
        return {"ok": False, "error": f"Health check failed: {e}",
                "api_fallback": api_fallback_available}


def require_health(retries: int = 3, delay: int = 5) -> None:
    """
    Call at pipeline start to ensure Claude CLI is ready.
    Retries up to N times with exponential backoff (2s, 10s, 30s) —
    handles cold-start, transient rate-limits, and VSCode extension updates.
    Raises RuntimeError with helpful guidance if all retries fail.
    """
    last_error = ""
    silence_warning = os.environ.get("MOKI_SILENCE_NO_API_KEY", "").lower() in ("1", "true", "yes")
    backoffs = [2, 10, 30]  # exponential: cold-start gets longer cushion each retry
    for attempt in range(1, retries + 1):
        result = health_check()
        if result["ok"]:
            if not result.get("api_fallback") and not silence_warning:
                print("  ⚠️ No API key found — CLI works but there's no fallback if it fails")
            return
        last_error = result.get("error", "unknown")
        if attempt < retries:
            wait = backoffs[min(attempt - 1, len(backoffs) - 1)]
            print(f"  ⏳ Health check attempt {attempt}/{retries} failed — retrying in {wait}s...")
            _time.sleep(wait)

    raise RuntimeError(
        f"Claude CLI health check failed after {retries} attempts.\n"
        f"  Last error: {last_error}\n"
        f"\n"
        f"  Possible fixes:\n"
        f"  1. Wait for VSCode Claude extension to finish updating, then retry\n"
        f"  2. Set CLAUDE_BIN env var: export CLAUDE_BIN=/path/to/claude\n"
        f"  3. Set ANTHROPIC_API_KEY env var (or create .env file with the key)\n"
        f"  4. Check `ls ~/.vscode/extensions/anthropic.claude-code-*` for installed versions"
    )


def print_status():
    if CLAUDE_BIN:
        print(f"  ✅ Claude CLI: {CLAUDE_BIN}")
        try:
            v = subprocess.run([CLAUDE_BIN, "--version"],
                               capture_output=True, text=True, timeout=5)
            print(f"     גרסה: {v.stdout.strip()}")
        except Exception:
            pass
    else:
        print("  ⚠️  Claude CLI לא נמצא — משתמש ב-API ישיר")
        try:
            import anthropic
            print("  ✅ anthropic SDK זמין")
        except ImportError:
            print("  ❌ גם anthropic SDK לא מותקן")


if __name__ == "__main__":
    print_status()
    if check_cli_available():
        resp = ask_claude("Say 'CLI working' in Hebrew", max_budget=0.1)
        print(f"  תשובה: {resp}")
