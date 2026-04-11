"""
Orchestrator — pipeline ישיר עם Parallel + Checkpoint + Dedup + Bilingual
Usage:
  python orchestrator.py "non-formal education" --content linkedin blog podcast
  python orchestrator.py "non-formal education" --resume          # המשך ריצה קודמת
  python orchestrator.py "non-formal education" --parallel        # מחקר מקבילי
  python orchestrator.py "non-formal education" --bilingual       # EN + HE
"""

import sys
import time
import argparse
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from agent0_planner      import run_planner
from agent1_researcher   import run_researcher
from agent1_5_pdf_reader import run_pdf_reader
from agent2_writer       import run_writer
from agent3_content_creator import run_content_creator, CONTENT_TYPES
from checkpoint          import Checkpoint
from memory              import load_memory


def parse_args():
    p = argparse.ArgumentParser(description="Education agents pipeline")
    p.add_argument("topic",     help="נושא ראשי")
    p.add_argument("subtopics", nargs="*")
    p.add_argument("--content", nargs="+",
                   choices=["linkedin","blog","podcast"], default=["linkedin"])
    p.add_argument("--skip-planner",  action="store_true")
    p.add_argument("--skip-agent1",   nargs="+", metavar="FILE")
    p.add_argument("--skip-agent2",   metavar="FILE")
    p.add_argument("--only",    choices=["0","1","2","3"])
    p.add_argument("--resume",  action="store_true", help="המשך ריצה אחרונה")
    p.add_argument("--parallel",action="store_true", help="מחקר מקבילי ×3")
    p.add_argument("--bilingual",action="store_true",help="פלט EN + HE")
    p.add_argument("--auto",    action="store_true", help="דלג על human review")
    return p.parse_args()


# ─────────────────────────────────────────────
# Dedup guard
# ─────────────────────────────────────────────

def _already_researched(topic: str) -> bool:
    mem = load_memory()
    researched = [t.lower() for t in mem.get("researched_topics", [])]
    return topic.lower() in researched


# ─────────────────────────────────────────────
# Parallel research helper
# ─────────────────────────────────────────────

def _research_one(t_info: dict, idx: int) -> tuple[int, Path]:
    """רץ ב-thread נפרד — מחקר + PDF enrichment לנושא אחד."""
    topic     = t_info["topic"]
    subtopics = t_info.get("subtopics", [])

    if _already_researched(topic):
        print(f"  ⏭  נושא {idx} דולג (כבר נחקר): {topic}")
        from config import PAPERS_DIR
        slug = topic.replace(" ", "_").lower()[:40]
        candidates = list(PAPERS_DIR.glob(f"*{slug}*"))
        if candidates:
            return idx, max(candidates, key=lambda p: p.stat().st_mtime)

    print(f"  🔍 [{idx}] מתחיל: {topic}")
    pf = run_researcher(topic, subtopics)

    try:
        ef = run_pdf_reader(pf)
        return idx, ef
    except Exception as e:
        print(f"  ⚠️  PDF reader [{idx}]: {e}")
        return idx, pf


# ─────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────

def run_pipeline(topic, subtopics, args):
    start   = time.time()
    results = {}

    # ── Checkpoint: resume or new ────────────
    if args.resume:
        ckpt = Checkpoint.latest() or Checkpoint()
    else:
        ckpt = Checkpoint()

    content_types   = args.content
    content_display = " + ".join(CONTENT_TYPES[t] for t in content_types)

    print(f"""
╔══════════════════════════════════════════════════════════╗
║  Education Agents — {datetime.now().strftime('%d/%m/%Y %H:%M')}
╚══════════════════════════════════════════════════════════╝
נושא:      {topic}
תוצרים:   {content_display}
מקבילי:   {'כן' if args.parallel else 'לא'}
דו-לשוני: {'כן' if args.bilingual else 'לא'}
Checkpoint: {ckpt.run_id}
""")

    # ── Agent 0: Planner ──────────────────────
    if not args.skip_planner and args.only in (None, "0"):
        if ckpt.done("planner"):
            plan = ckpt.get("planner")
            print(f"  ♻️  Planner מ-checkpoint: {plan['combined_title']}")
        else:
            t0   = time.time()
            plan = run_planner(topic, subtopics or [])
            ckpt.save("planner", plan)
            results["agent0"] = {"time": f"{time.time()-t0:.1f}s", **plan}
    else:
        plan = {
            "combined_title": topic,
            "topics": [
                {"topic": topic,             "subtopics": subtopics or [], "angle": "theoretical"},
                {"topic": topic+" research", "subtopics": [],              "angle": "empirical"},
                {"topic": topic+" practice", "subtopics": [],              "angle": "practical"},
            ],
            "content_types": content_types,
        }

    if args.only == "0":
        return _summary(results, content_types, time.time()-start)

    # ── Agent 1 ×3 (parallel or sequential) ──
    enriched_files = []

    if args.skip_agent1:
        enriched_files = [Path(f) for f in args.skip_agent1]
        print(f"  ⏭  Agent 1 דולג — {len(enriched_files)} קבצים")

    elif ckpt.done("research_all"):
        enriched_files = [Path(f) for f in ckpt.get("research_all")]
        print(f"  ♻️  Research מ-checkpoint: {len(enriched_files)} קבצים")

    elif args.only in (None, "1"):
        t0 = time.time()

        if args.parallel:
            # ── Parallel ×3 ────────────────────
            print(f"  ⚡ מריץ 3 מחקרים במקביל...")
            ordered = [None, None, None]
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = {
                    pool.submit(_research_one, t_info, i+1): i
                    for i, t_info in enumerate(plan["topics"])
                }
                for fut in as_completed(futures):
                    idx, ef = fut.result()
                    ordered[idx - 1] = ef
                    print(f"  ✅ נושא {idx} הסתיים")
            enriched_files = [f for f in ordered if f]

        else:
            # ── Sequential ×3 ──────────────────
            for i, t_info in enumerate(plan["topics"], 1):
                step_key = f"research_{i}"
                if ckpt.done(step_key):
                    ef = Path(ckpt.get(step_key)["file"])
                    enriched_files.append(ef)
                    print(f"  ♻️  Research {i} מ-checkpoint")
                    continue
                _, ef = _research_one(t_info, i)
                enriched_files.append(ef)
                ckpt.save(step_key, {"file": str(ef)})

        ckpt.save("research_all", [str(f) for f in enriched_files])
        results["agent1"] = {
            "files": [f.name for f in enriched_files],
            "time":  f"{time.time()-t0:.1f}s",
            "parallel": args.parallel,
        }
        print(f"  ⏱  Research: {results['agent1']['time']}")

    if args.only == "1":
        return _summary(results, content_types, time.time()-start)

    # ── Agent 2: Writer ───────────────────────
    article_paths = {}

    if args.skip_agent2:
        md = Path(args.skip_agent2)
        article_paths = {"md": md, "docx": md.with_suffix(".docx")} \
                        if md.with_suffix(".docx").exists() else {"md": md}
        print(f"  ⏭  Agent 2 דולג")

    elif ckpt.done("writer"):
        saved = ckpt.get("writer")
        article_paths = {k: Path(v) for k, v in saved.items()}
        print(f"  ♻️  Writer מ-checkpoint")

    elif enriched_files and args.only in (None, "2"):
        t0 = time.time()
        article_paths = run_writer(
            papers_files   = enriched_files,
            combined_title = plan.get("combined_title", ""),
            bilingual      = args.bilingual,
        )
        ckpt.save("writer", {k: str(v) for k, v in article_paths.items()})
        results["agent2"] = {
            "files": {k: str(v) for k, v in article_paths.items()},
            "time":  f"{time.time()-t0:.1f}s",
        }
        print(f"  ⏱  Writer: {results['agent2']['time']}")

        # Agent 2.5: Article editor
        try:
            from agent_editor import edit_article
            edit_article(article_paths)
        except Exception as e:
            print(f"  ⚠️  Article editor: {e}")

    if args.only == "2":
        return _summary(results, content_types, time.time()-start)

    # ── Agent 3: Content ──────────────────────
    if article_paths and args.only in (None, "3"):
        t0    = time.time()
        saved = run_content_creator(article_paths, content_types)
        results["agent3"] = {
            "files": {ct: [str(p) for p in paths] for ct, paths in saved.items()},
            "time":  f"{time.time()-t0:.1f}s",
        }
        ckpt.save("content", results["agent3"]["files"])
        print(f"  ⏱  Content: {results['agent3']['time']}")

        # Agent 3.5: Human Review
        if not args.auto:
            try:
                from agent3_5_human_review import review_all
                review_all(content_types, auto_approve=False)
            except Exception as e:
                print(f"  ⚠️  Human review: {e}")

        # Agent 3.6: Content editor
        try:
            from agent_editor import edit_all_content
            edit_all_content(content_types)
        except Exception as e:
            print(f"  ⚠️  Content editor: {e}")

    # ── Update bibliography ─────────────────────
    try:
        from bibliography import update_bibliography
        update_bibliography()
    except Exception as e:
        print(f"  ⚠️  Bibliography update: {e}")

    return _summary(results, content_types, time.time()-start)


def _summary(results, content_types, total):
    print(f"""
╔══════════════════════════════════════════════════════════╗
║  PIPELINE COMPLETE  ✅  ({total:.0f}s / {total/60:.1f} דק')
╚══════════════════════════════════════════════════════════╝""")
    for k, v in results.items():
        print(f"  {k}: {v.get('time','')}")
    print()
    return results


if __name__ == "__main__":
    args = parse_args()
    run_pipeline(args.topic, args.subtopics or [], args)
