"""
error_book.py — מאגר שגיאות ידוע + פתרונות.
מחפש בכל הלוגים, מאגרר לפי error code, מציע פתרון.

Usage:
  python3 error_book.py                  # all errors grouped
  python3 error_book.py TIMEOUT          # only TIMEOUT errors
  python3 error_book.py --solutions      # show known solutions
"""

import sys
import re
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime

from config import OUTPUT_DIR

KNOWN_FIXES = {
    "TIMEOUT": "Step took >30 min. Hard timeout fires now. Check VSCode CLI updating mid-run.",
    "RATE_LIMIT": "Semantic Scholar throttle. Retry budget cut to 30s. Other 6 sources cover.",
    "CTX_OVERFLOW": "Prompt too long. Auto-trim active. Check papers_json size.",
    "RENDER_FAIL": "SVG render failed. Falls back to template. Designer will retry next run.",
    "SCORE_LOW": "QA score <60. Auto-fix triggered. Check Voice Drift for systemic.",
    "CLIUnavailable": "VSCode extension updating. Wait-for-CLI 60s + retry × 3 active.",
    "JSON parse": "Truncated/malformed JSON. _repair_truncated_json runs auto.",
    "Both CLI and API": "CLI failed AND no API key. Set up .env or wait for CLI.",
    "FileNotFoundError": "Path issue — usually Desktop/permissions. Project moved to ~/.",
    "Operation not permitted": "macOS TCC. launchd uses ~/moki_*.sh wrappers from home.",
    "NoneType": "Defensive .get() needed. Common with nested API responses.",
}


def _scan_logs() -> list[dict]:
    """Scan all log files for error patterns."""
    errors = []
    log_dir = OUTPUT_DIR
    for f in log_dir.glob("cron_*.log"):
        date_match = re.search(r"cron_(\d{8})", f.name)
        date_str = date_match.group(1) if date_match else "unknown"
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        # Find error lines
        for line in text.split("\n"):
            if "ERROR" in line or "❌" in line or "⛔" in line:
                # Classify by code
                code = "UNKNOWN"
                for known in KNOWN_FIXES:
                    if known.lower() in line.lower():
                        code = known
                        break
                errors.append({
                    "date": date_str,
                    "code": code,
                    "line": line.strip()[:200],
                    "file": f.name,
                })
    # Also scan moki.log
    main_log = log_dir / "moki.log"
    if main_log.exists():
        try:
            for line in main_log.read_text(encoding="utf-8", errors="ignore").split("\n"):
                if "ERROR" in line:
                    code = "UNKNOWN"
                    for known in KNOWN_FIXES:
                        if known.lower() in line.lower():
                            code = known
                            break
                    errors.append({
                        "date": "main",
                        "code": code,
                        "line": line.strip()[:200],
                        "file": "moki.log",
                    })
        except Exception:
            pass
    return errors


def report(filter_code: str = None) -> str:
    errors = _scan_logs()
    if filter_code:
        errors = [e for e in errors if filter_code.lower() in e["code"].lower()]

    if not errors:
        return f"  ✅ אין שגיאות{' עם ' + filter_code if filter_code else ''}"

    # Group by code
    by_code = defaultdict(list)
    for e in errors:
        by_code[e["code"]].append(e)

    lines = [f"\n📕 Error Book — {datetime.now().strftime('%d/%m/%Y %H:%M')}"]
    lines.append(f"   Total errors: {len(errors)} | Unique codes: {len(by_code)}\n")

    # Sort by count
    for code, instances in sorted(by_code.items(), key=lambda x: -len(x[1])):
        fix = KNOWN_FIXES.get(code, "(no known fix)")
        lines.append(f"  📍 {code}: {len(instances)} occurrences")
        lines.append(f"     💡 {fix}")
        # Show 3 most recent
        for inst in instances[-3:]:
            line_short = inst["line"][:120]
            lines.append(f"     [{inst['date']}] {line_short}")
        lines.append("")
    return "\n".join(lines)


def solutions_only() -> str:
    """Print only the known fixes (cheat sheet)."""
    lines = ["\n📕 Known Error Fixes:\n"]
    for code, fix in KNOWN_FIXES.items():
        lines.append(f"  {code}")
        lines.append(f"    → {fix}\n")
    return "\n".join(lines)


def main():
    if "--solutions" in sys.argv:
        print(solutions_only())
    elif len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        print(report(filter_code=sys.argv[1]))
    else:
        print(report())


if __name__ == "__main__":
    main()
