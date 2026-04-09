"""
Auto-Pilot — מריץ את כל ה-pipeline אוטונומית, iteration אחרי iteration.

Usage:
  python autopilot.py "חינוך בלתי פורמלי" --iterations 3
  python autopilot.py "non-formal education" --loop
  python autopilot.py "חינוך בלתי פורמלי" --hints "שייכות" "תנועות נוער" --iterations 5
  python autopilot.py "חינוך בלתי פורמלי" --plan-only
  python autopilot.py "חינוך בלתי פורמלי" --skip-research --content blog podcast
"""

import time
import argparse
from datetime import datetime
from pathlib import Path

from agent0_planner import run_planner
from agent1_researcher import run_researcher
from agent2_writer import run_writer
from agent3_content_creator import run_content_creator, CONTENT_TYPES
from memory import (
    load_memory, record_research, record_article,
    record_content, get_summary
)
from config import ARTICLES_DIR


def parse_args():
    parser = argparse.ArgumentParser(
        description="הרצה אוטונומית של pipeline המחקר",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
דוגמאות:
  python3 autopilot.py "חינוך בלתי פורמלי" --iterations 3
  python3 autopilot.py "non-formal education" --loop --content linkedin blog
  python3 autopilot.py "חינוך בלתי פורמלי" --hints "שייכות" "זהות" --iterations 5
  python3 autopilot.py "חינוך בלתי פורמלי" --plan-only
        """,
    )
    parser.add_argument("main_field", help="תחום המחקר הראשי")
    parser.add_argument("--hints", nargs="*", default=[], help="רמזים לפלאנר")
    parser.add_argument("--iterations", type=int, default=1, help="כמה איטרציות (ברירת מחדל: 1)")
    parser.add_argument("--loop", action="store_true", help="הרץ ללא הגבלה עד Ctrl+C")
    parser.add_argument(
        "--content", nargs="+",
        choices=["linkedin", "blog", "podcast", "auto"],
        default=["auto"],
        help="סוגי תוכן — 'auto' = הפלאנר מחליט",
    )
    parser.add_argument("--plan-only", action="store_true", help="רק הצג תוכנית, אל תריץ")
    parser.add_argument("--skip-research", action="store_true", help="דלג על Agent 1+2, רק צור תוכן")
    return parser.parse_args()


def run_iteration(
    main_field: str,
    hints: list[str],
    content_override: list[str] | None,
    skip_research: bool,
    iteration_num: int,
) -> bool:
    iter_start = time.time()

    print(f"""
╔══════════════════════════════════════════════════════════╗
║  🔄 איטרציה {iteration_num} — {datetime.now().strftime('%d/%m/%Y %H:%M')}
╚══════════════════════════════════════════════════════════╝
""")
    print(get_summary())
    print()

    # Agent 0: Plan
    plan = run_planner(main_field, hints)
    topic     = plan["topic"]         # combined display title
    topics    = plan.get("topics", [topic])
    subtopics = plan.get("subtopics", [])
    content_types = content_override if content_override else plan.get("content_types", ["linkedin", "blog"])

    print(f"\n📋 תוכנית האיטרציה:")
    for i, t in enumerate(topics, 1):
        print(f"   נושא {i}: {t}")
    print(f"   כותרת: {topic}")
    print(f"   תוכן:  {' + '.join(CONTENT_TYPES.get(t, t) for t in content_types)}")
    print(f"   סיבה:  {plan.get('reasoning', '')[:120]}\n")

    article_paths = {}

    if not skip_research:
        # Agent 1: Research — once per topic (3 calls)
        topics = plan.get("topics", [topic])  # list of 3
        subtopics_map = plan.get("subtopics_map", {})
        papers_files = []

        for t in topics:
            t_subtopics = subtopics_map.get(t, subtopics[:4] if subtopics else [])
            pf = run_researcher(t, t_subtopics)
            record_research(t, t_subtopics, pf)
            # Agent 1.5: PDF enrichment
            try:
                from agent1_5_pdf_reader import run_pdf_reader
                pf = run_pdf_reader(pf)
            except Exception as e:
                print(f"  ⚠️  Agent 1.5 נכשל ({e}) — ממשיך עם תקצירים")
            papers_files.append(pf)

        # Agent 2: Write ONE combined article from all 3 research files
        combined_title = plan.get("topic", " + ".join(topics))
        article_paths = run_writer(papers_files, combined_title=combined_title)
        record_article(article_paths, combined_title)

    else:
        mds = [f for f in ARTICLES_DIR.glob("*.md") if "_he" not in f.stem]
        if mds:
            latest = max(mds, key=lambda p: p.stat().st_mtime)
            article_paths = {"md": latest}
            print(f"  ↩️  Skip research — using {latest.name}")
        else:
            print("  ❌ לא נמצאו מאמרים. הרץ בלי --skip-research.")
            return False

    # Agent 3: Content
    if article_paths:
        saved = run_content_creator(article_paths, content_types)
        for ct, paths in saved.items():
            for p in paths:
                record_content(ct, topic, str(p))

    elapsed = time.time() - iter_start
    print(f"\n⏱  איטרציה {iteration_num} הסתיימה תוך {elapsed/60:.1f} דקות")
    print(get_summary())
    return True


def main():
    args = parse_args()
    content_override = None if "auto" in args.content else args.content

    if args.plan_only:
        print("\n🔍 מחשב תוכנית בלבד...\n")
        plan = run_planner(args.main_field, args.hints)
        print(f"\n📋 הנושא הבא:")
        print(f"   נושא:   {plan['topic']}")
        print(f"   משנה:   {', '.join(plan['subtopics'])}")
        print(f"   תוכן:   {plan.get('content_types', ['linkedin'])}")
        print(f"   סיבה:   {plan.get('reasoning', '')}")
        return

    if args.loop:
        print(f"\n🔁 מצב לופ — מריץ עד Ctrl+C\n")
        i = 1
        try:
            while True:
                ok = run_iteration(args.main_field, args.hints, content_override, args.skip_research, i)
                if not ok:
                    break
                i += 1
                print(f"\n⏸  ממתין 10 שניות...\n")
                time.sleep(10)
        except KeyboardInterrupt:
            print(f"\n\n⛔ הופסק אחרי {i-1} איטרציות.")
    else:
        n = args.iterations
        print(f"\n🎯 מריץ {n} איטרציות\n")
        for i in range(1, n + 1):
            ok = run_iteration(args.main_field, args.hints, content_override, args.skip_research, i)
            if not ok:
                print(f"⚠️  עצר בשגיאה באיטרציה {i}")
                break
            if i < n:
                time.sleep(5)

        print(f"\n✅ הושלמו {n} איטרציות.")
        print(get_summary())


if __name__ == "__main__":
    main()
