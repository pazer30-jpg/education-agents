"""
test_mini_pipeline.py — מבחן end-to-end קצר (1 topic, 1 platform).
מטרה: לתפוס באגים בפייפליין מבלי לחכות 30 דקות.

זמן: 8-12 דקות. עלות: ~$3.
ריצה: python3 test_mini_pipeline.py [--platform linkedin|blog|podcast]

Exit codes:
  0 — passed
  1 — partial (some agents failed but pipeline finished)
  2 — failed (critical agent crashed)
"""

import sys
import time
import shutil
from pathlib import Path
from datetime import datetime

PROJECT_DIR = Path(__file__).parent
TMP_DIR = PROJECT_DIR / "output" / "_test_mini"


def _cleanup():
    if TMP_DIR.exists():
        shutil.rmtree(TMP_DIR)
    TMP_DIR.mkdir(parents=True, exist_ok=True)


def _log(msg: str):
    print(f"  {msg}")


def main():
    platform = "linkedin"
    if "--platform" in sys.argv:
        idx = sys.argv.index("--platform")
        if idx + 1 < len(sys.argv):
            platform = sys.argv[idx + 1]

    print(f"\n🧪 Mini Pipeline Test — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"   Platform: {platform}")
    print(f"   Topic: 'group belonging in informal education' (fixed for repeatability)")
    print(f"   Expected time: 8-12 min, expected cost: ~$3\n")

    start = time.time()
    failures = []
    passed = []

    # ── 1. Health check ──
    _log("🛡  Health check...")
    try:
        from claude_cli import require_health
        require_health()
        passed.append("health")
    except Exception as e:
        print(f"  ❌ Health failed: {e}")
        return 2

    # ── 2. Planner (skip — fixed topic for repeatability) ──
    fixed_topic = "group belonging in informal education"
    fixed_subtopics = ["youth groups identity", "non-formal community"]
    _log(f"🧠 Skipping Planner — using fixed topic for test reproducibility")
    passed.append("planner_skip")

    # ── 3. Researcher (mini: only 1 topic, low limit) ──
    _log(f"🔍 Researcher: '{fixed_topic}' (mini)...")
    t0 = time.time()
    try:
        from agent1_researcher import run_researcher
        papers_file = run_researcher(fixed_topic, fixed_subtopics, force=False)
        if not papers_file or not Path(papers_file).exists():
            failures.append("researcher: no output file")
            _log(f"  ❌ no papers file")
        else:
            elapsed = time.time() - t0
            _log(f"  ✅ {Path(papers_file).name} ({elapsed:.0f}s)")
            passed.append("researcher")
    except Exception as e:
        failures.append(f"researcher: {e}")
        _log(f"  ❌ {e}")
        return 2  # critical

    # ── 4. PDF reader (skip if too slow) ──
    _log("📄 PDF reader (limited to 3 papers for speed)...")
    try:
        from agent1_5_pdf_reader import run_pdf_reader
        papers_file = run_pdf_reader(papers_file)
        passed.append("pdf_reader")
    except Exception as e:
        failures.append(f"pdf_reader: {e}")
        _log(f"  ⚠️  {e} (continuing)")

    # ── 5. Writer (mini: short article) ──
    _log(f"✍️  Writer (mini, 1 topic)...")
    t0 = time.time()
    try:
        from agent2_writer import run_writer
        article_paths = run_writer([Path(papers_file)],
                                   combined_title=fixed_topic,
                                   bilingual=False)
        if not article_paths.get("md"):
            failures.append("writer: no markdown output")
            _log(f"  ❌ no MD output")
            return 2
        elapsed = time.time() - t0
        word_count = len(Path(article_paths["md"]).read_text(encoding="utf-8").split())
        _log(f"  ✅ {word_count} words ({elapsed/60:.1f} min)")
        passed.append("writer")
    except Exception as e:
        failures.append(f"writer: {e}")
        _log(f"  ❌ {e}")
        return 2  # critical

    # ── 6. Content (only chosen platform) ──
    _log(f"✨ Content creator ({platform} only)...")
    t0 = time.time()
    try:
        from agent3_content_creator import run_content_creator
        saved = run_content_creator(article_paths, [platform])
        if not saved.get(platform):
            failures.append(f"content: no {platform} output")
            _log(f"  ❌ no {platform} files saved")
        else:
            elapsed = time.time() - t0
            _log(f"  ✅ {len(saved[platform])} files ({elapsed/60:.1f} min)")
            passed.append("content")
    except Exception as e:
        failures.append(f"content: {e}")
        _log(f"  ❌ {e}")

    # ── Report ──
    total = time.time() - start
    print(f"\n{'='*60}")
    print(f"🎯 Test complete — {total/60:.1f} min")
    print(f"   Passed:   {len(passed)}/{len(passed)+len(failures)} ({', '.join(passed)})")
    if failures:
        print(f"   Failed:   {len(failures)}")
        for f in failures:
            print(f"     ❌ {f}")
        return 1  # partial
    print(f"   ✅ All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
