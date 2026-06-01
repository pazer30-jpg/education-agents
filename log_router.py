"""
log_router.py — Post-pipeline log splitter (namespaced streams pattern).

Inspired by Vercel Workflow's getWritable({namespace}) — instead of one giant
cron_YYYYMMDD.log mega-stream, route lines into per-concern files:

  output/logs/2026-06-02/
  ├── default.log     ← pipeline-level milestones (START / DONE / FAILED)
  ├── agent.log       ← per-agent step boundaries
  ├── claude.log      ← CLI retries, rate limits, timeouts
  ├── trending.log    ← Reddit/HN/arXiv source fetches
  ├── factcheck.log   ← citation verification
  └── debug.log       ← everything (full mirror of the input log)

Runs as a post-pipeline step (NO code changes anywhere else). Reads
output/cron_YYYYMMDD.log line-by-line, regex-routes to namespaces.
Idempotent: rerunning on the same log overwrites the namespace files
with the latest content.

Usage:
  python3 log_router.py                # split today's cron log
  python3 log_router.py --date 20260601  # historical
  python3 log_router.py --file path/to/log.log
"""

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR

LOGS_ROOT = OUTPUT_DIR / "logs"


# ─── Routing rules (regex → namespaces) ───
# A line matches a namespace if ANY of its patterns hit. One line can fan out
# to multiple namespaces (e.g. "[Agent 2] CLI timeout" → agent + claude).
# default.log only gets the highest-signal events — see DEFAULT_PATTERNS.

ROUTING = {
    "claude": [
        r"\[CLI\]",
        r"Claude CLI",
        r"timeout after \d+s",
        r"DailyBudgetExceeded",
        r"Rate limit:",
        r"all \d+ retries exhausted",
    ],
    "agent": [
        r"━━━ \[\d+/\d+\] Agent",
        r"\[Agent\d+(?:\.\d+)?\]",
        r"✅ Agent \d+",
        r"❌ Agent \d+",
        r"⏸\s+Agent \d+",
        r"mapping Agent",
        r"⛔ Agent \d+",
        r"מפעיל Agent",
        r"\[Editor\]",
    ],
    "trending": [
        r"\[trending\]",
        r"HackerNews|HN|arXiv|Reddit",  # broad — fine for trending file
        r"reddit\.com",
    ],
    "factcheck": [
        r"\[FactCheck\]",
        r"orphan citation",
        r"Triangulation:",
        r"weak.claim|contested.claim",
    ],
    "publisher": [
        r"\[Publisher\]",
        r"Telegram",
        r"publish_queue",
        r"Curator",
    ],
    "research": [
        r"\[Agent1/[A-Za-z\-]+\]",  # source-specific (SS, OpenAlex, etc.)
        r"PDFs שהורדו",
        r"papers\.json",
        r"Unpaywall",
    ],
}

# default.log: only the "headline" events. Use these to scan a run in <30 sec.
DEFAULT_PATTERNS = [
    r"PIPELINE START",
    r"PIPELINE COMPLETE",
    r"PIPELINE FAILED",
    r"━━━ \[\d+/\d+\]",          # step boundaries
    r"^🎉 Pipeline complete",
    r"^✅ PIPELINE",
    r"^❌ PIPELINE",
    r"^  ✅ Done",
    r"^  ❌ Failed",
    r"Health gate (passed|failed)",
    r"📁 \d+ new files ready",
    r"💾 checkpoint:",
    r"⏸  pipeline already running",
    r"🛑 WATCHDOG",
]


def _matches_any(line: str, patterns: list[str]) -> bool:
    return any(re.search(p, line) for p in patterns)


def split_log(log_path: Path, out_dir: Path) -> dict[str, int]:
    """
    Read log_path line-by-line, route into namespaced files under out_dir.
    Returns count-per-namespace dict.
    """
    if not log_path.exists():
        raise FileNotFoundError(f"log not found: {log_path}")
    out_dir.mkdir(parents=True, exist_ok=True)

    buffers: dict[str, list[str]] = defaultdict(list)

    text = log_path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        # debug.log gets EVERY line (full mirror)
        buffers["debug"].append(line)

        # default.log: high-signal headlines only
        if _matches_any(line, DEFAULT_PATTERNS):
            buffers["default"].append(line)

        # Namespace routing
        for ns, patterns in ROUTING.items():
            if _matches_any(line, patterns):
                buffers[ns].append(line)

    counts: dict[str, int] = {}
    for ns, lines in buffers.items():
        path = out_dir / f"{ns}.log"
        path.write_text("\n".join(lines) + ("\n" if lines else ""),
                        encoding="utf-8")
        counts[ns] = len(lines)
    return counts


def main():
    ap = argparse.ArgumentParser(description="Split cron log into namespaces")
    ap.add_argument("--date", help="YYYYMMDD — default today")
    ap.add_argument("--file", help="explicit log file path (overrides --date)")
    args = ap.parse_args()

    if args.file:
        log_path = Path(args.file)
        date_str = log_path.stem.replace("cron_", "")
    else:
        date_str = args.date or datetime.now().strftime("%Y%m%d")
        log_path = OUTPUT_DIR / f"cron_{date_str}.log"

    if not log_path.exists():
        print(f"⚠️  log not found: {log_path}")
        sys.exit(1)

    # output/logs/2026-06-02/
    pretty_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    out_dir = LOGS_ROOT / pretty_date

    counts = split_log(log_path, out_dir)
    total = counts.get("debug", 0)
    summary = " · ".join(
        f"{ns}={n}" for ns, n in sorted(counts.items())
        if ns not in ("debug",)
    )
    print(f"📂 {out_dir.relative_to(OUTPUT_DIR.parent)} ({total} total lines)")
    print(f"   {summary}")


if __name__ == "__main__":
    main()
