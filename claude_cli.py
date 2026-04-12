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
    """מחפש את Claude CLI בנתיבים אפשריים."""
    candidates = [
        os.environ.get("CLAUDE_BIN"),
        # VSCode extension binary
        *[str(p) for p in Path.home().glob(
            ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude"
        )],
        shutil.which("claude"),
        str(Path.home() / ".claude" / "local" / "claude"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


CLAUDE_BIN   = _find_claude_bin()
CLAUDE_MODEL = "sonnet"  # "sonnet" | "opus"


# ─────────────────────────────────────────────
# Rate limiter — prevent budget blowup
# ─────────────────────────────────────────────

import time as _time
import threading

class _RateLimiter:
    """Simple rate limiter: max N calls per minute."""
    def __init__(self, max_per_minute: int = 8):
        self.max_per_minute = max_per_minute
        self.calls: list[float] = []
        self.total_budget: float = 0
        self.max_budget: float = 100.0  # $100 per session (pipeline with self-review ~$8-12)
        self._lock = threading.Lock()

    def wait(self, budget: float):
        with self._lock:
            now = _time.time()
            # Remove calls older than 60s
            self.calls = [t for t in self.calls if now - t < 60]
            # Wait if at limit
            if len(self.calls) >= self.max_per_minute:
                wait = 60 - (now - self.calls[0])
                if wait > 0:
                    print(f"  ⏳ Rate limit: waiting {wait:.0f}s...")
                    _time.sleep(wait)
            # Budget guard
            if self.total_budget + budget > self.max_budget:
                raise RuntimeError(
                    f"Budget limit reached: ${self.total_budget:.2f} used, "
                    f"${budget:.2f} requested, max ${self.max_budget:.2f}"
                )
            self.calls.append(_time.time())
            self.total_budget += budget

_limiter = _RateLimiter()


# ─────────────────────────────────────────────
# Core call
# ─────────────────────────────────────────────

def ask_claude(prompt: str, system: str = "", max_budget: float = 2.0) -> str:
    """
    Call Claude CLI with a prompt, return text response.
    Falls back to Anthropic API if CLI not available.
    """
    if not CLAUDE_BIN:
        return _api_fallback(prompt, system, max_budget)

    _limiter.wait(max_budget)

    cmd = [
        CLAUDE_BIN,
        "-p",
        "--model", CLAUDE_MODEL,
        "--dangerously-skip-permissions",
        f"--max-budget-usd={max_budget}",
    ]

    if system:
        cmd += ["--append-system-prompt", system]

    cmd.append(prompt)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minutes for long articles
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            # Try API fallback on CLI error
            print(f"  [CLI] Error (exit {result.returncode}), trying API fallback...")
            return _api_fallback(prompt, system, max_budget)

        return result.stdout.strip()

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        print(f"  [CLI] {e}, trying API fallback...")
        return _api_fallback(prompt, system, max_budget)


def ask_claude_json(prompt: str, system: str = "", max_budget: float = 2.0) -> dict | list:
    """
    Call Claude CLI and parse JSON from the response.
    """
    json_prompt = prompt + "\n\nIMPORTANT: Return ONLY valid JSON, no markdown, no explanation."
    raw = ask_claude(json_prompt, system=system, max_budget=max_budget)

    # Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    match = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', raw)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try extracting first JSON array or object
    match = re.search(r'(\[[\s\S]+\]|\{[\s\S]+\})', raw)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from Claude output:\n{raw[:400]}")


# ─────────────────────────────────────────────
# API fallback
# ─────────────────────────────────────────────

def _api_fallback(prompt: str, system: str, max_budget: float) -> str:
    """משתמש ב-Anthropic API ישיר אם CLI לא זמין."""
    _limiter.wait(max_budget)  # rate limit also applies to API fallback
    try:
        import anthropic
        client = anthropic.Anthropic()
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
    except ImportError:
        raise RuntimeError(
            "Claude CLI not found and anthropic package not installed. "
            "Install with: pip install anthropic"
        )
    except Exception as e:
        raise RuntimeError(f"Both CLI and API failed: {e}")


# ─────────────────────────────────────────────
# Status check
# ─────────────────────────────────────────────

def check_cli_available() -> bool:
    return CLAUDE_BIN is not None


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
