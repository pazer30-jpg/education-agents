"""
tests.py — בדיקות בסיסיות למערכת
מריץ בדיקות imports, APIs, קבצים, ותצורה.

Usage:
  python tests.py          # הרץ הכל
  python tests.py --quick  # רק imports (ללא API calls)
"""

import sys
import json
import importlib
from pathlib import Path
from datetime import datetime

PASS = 0
FAIL = 0
SKIP = 0


def _test(name: str, fn, skip: bool = False):
    global PASS, FAIL, SKIP
    if skip:
        SKIP += 1
        print(f"  ⏭  {name}")
        return
    try:
        result = fn()
        if result is False:
            raise AssertionError("returned False")
        PASS += 1
        print(f"  ✅ {name}")
    except Exception as e:
        FAIL += 1
        print(f"  ❌ {name}: {e}")


# ─────────────────────────────────────────────
# 1. Import tests
# ─────────────────────────────────────────────

def test_imports():
    print("\n── Imports ──────────────────────────")
    modules = [
        "config", "claude_cli", "memory", "checkpoint",
        "voice_profile", "qa_checker", "analytics",
        "agent0_planner", "agent1_researcher", "agent1_5_pdf_reader",
        "agent2_writer", "agent_editor", "agent3_content_creator",
        "agent3_5_human_review", "agent4_designer",
        "agent5_project_manager", "orchestrator", "scheduler",
        "add_paper", "bibliography", "weekly_summary",
        "dashboard", "history", "notifications", "telegram_bot",
    ]
    for m in modules:
        _test(f"import {m}", lambda m=m: importlib.import_module(m))


# ─────────────────────────────────────────────
# 2. Config tests
# ─────────────────────────────────────────────

def test_config():
    print("\n── Config ───────────────────────────")
    from config import OUTPUT_DIR, PAPERS_DIR, ARTICLES_DIR

    _test("OUTPUT_DIR exists", lambda: OUTPUT_DIR.exists())
    _test("PAPERS_DIR exists", lambda: PAPERS_DIR.exists())
    _test("ARTICLES_DIR exists", lambda: ARTICLES_DIR.exists())

    from claude_cli import CLAUDE_BIN, check_cli_available
    _test("Claude CLI found", lambda: check_cli_available())


# ─────────────────────────────────────────────
# 3. Function signature tests
# ─────────────────────────────────────────────

def test_signatures():
    print("\n── Function Signatures ──────────────")
    import inspect

    from agent1_researcher import run_researcher
    sig = inspect.signature(run_researcher)
    _test("researcher has 'force' param",
          lambda: "force" in sig.parameters)

    from agent2_writer import run_writer
    sig = inspect.signature(run_writer)
    _test("writer has 'bilingual' param",
          lambda: "bilingual" in sig.parameters)

    from agent3_content_creator import run_content_creator
    sig = inspect.signature(run_content_creator)
    _test("content_creator has 'extra_instruction' param",
          lambda: "extra_instruction" in sig.parameters)
    _test("content_creator has 'ab_test' param",
          lambda: "ab_test" in sig.parameters)


# ─────────────────────────────────────────────
# 4. Data integrity tests
# ─────────────────────────────────────────────

def test_data():
    print("\n── Data Integrity ───────────────────")
    from config import OUTPUT_DIR

    # Memory
    mem_file = OUTPUT_DIR / "memory.json"
    def check_memory():
        assert mem_file.exists(), "memory.json not found"
        data = json.loads(mem_file.read_text(encoding="utf-8"))
        assert "researched_topics" in data
        assert "papers" in data
        return True
    _test("memory.json valid", check_memory)

    # Analytics
    ana_file = OUTPUT_DIR / "analytics.json"
    def check_analytics():
        if not ana_file.exists():
            return True  # OK if no runs yet
        data = json.loads(ana_file.read_text(encoding="utf-8"))
        assert "runs" in data
        return True
    _test("analytics.json valid", check_analytics)

    # Bibliography
    bib_file = OUTPUT_DIR / "references.json"
    def check_bib():
        if not bib_file.exists():
            return True
        data = json.loads(bib_file.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        return True
    _test("references.json valid", check_bib)

    # Checkpoint class
    def check_checkpoint():
        from checkpoint import Checkpoint
        ckpt = Checkpoint("test_run")
        ckpt.save("test_step", {"key": "value"})
        assert ckpt.done("test_step")
        assert ckpt.get("test_step") == {"key": "value"}
        ckpt.delete()
        return True
    _test("Checkpoint save/load/delete", check_checkpoint)


# ─────────────────────────────────────────────
# 5. API tests (optional — makes actual calls)
# ─────────────────────────────────────────────

def test_apis(skip: bool = False):
    print("\n── API Connectivity ─────────────────")

    def check_openalex():
        from agent1_researcher import search_openalex
        results = search_openalex("education", limit=2)
        assert len(results) > 0, "no results"
        return True
    _test("OpenAlex API", check_openalex, skip=skip)

    def check_eric():
        from agent1_researcher import search_eric
        results = search_eric("education", limit=2)
        assert len(results) > 0, "no results"
        return True
    _test("ERIC API", check_eric, skip=skip)

    def check_core():
        from agent1_researcher import search_core
        results = search_core("education", limit=2)
        assert len(results) > 0, "no results"
        return True
    _test("CORE API", check_core, skip=skip)

    def check_crossref():
        from agent1_researcher import search_crossref
        results = search_crossref("education", limit=2)
        assert len(results) > 0, "no results"
        return True
    _test("Crossref API", check_crossref, skip=skip)

    def check_ss():
        from agent1_researcher import search_semantic_scholar
        results = search_semantic_scholar("education", limit=2)
        # SS might be rate limited — that's OK
        return True
    _test("Semantic Scholar API (may be rate limited)", check_ss, skip=skip)


# ─────────────────────────────────────────────
# 6. QA checker tests
# ─────────────────────────────────────────────

def test_qa():
    print("\n── QA Checker ───────────────────────")
    from qa_checker import LoopDetector

    def check_loop_detector():
        ld = LoopDetector()
        r1 = ld.record("test", "output1", True)
        assert not r1["loop_detected"]
        r2 = ld.record("test", "output1", True)
        assert r2["loop_detected"], "should detect identical output"
        return True
    _test("LoopDetector works", check_loop_detector)

    def check_qa_none_handling():
        from qa_checker import check_research, check_article, check_content
        r1 = check_research(None)
        assert not r1.passed
        r2 = check_article(None)
        assert not r2.passed
        r3 = check_content("linkedin", None)
        assert not r3.passed
        return True
    _test("QA handles None inputs", check_qa_none_handling)


# ─────────────────────────────────────────────
# 7. No anthropic SDK in core files
# ─────────────────────────────────────────────

def test_no_sdk():
    print("\n── No Anthropic SDK ─────────────────")
    import ast
    core_files = [
        "agent0_planner.py", "agent1_researcher.py", "agent2_writer.py",
        "agent3_content_creator.py", "agent_editor.py",
        "agent5_project_manager.py", "orchestrator.py",
    ]
    def check_no_anthropic():
        bad = []
        for f in core_files:
            p = Path(f)
            if not p.exists():
                continue
            tree = ast.parse(p.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "anthropic" in alias.name:
                            bad.append(f)
                elif isinstance(node, ast.ImportFrom):
                    if node.module and "anthropic" in node.module:
                        bad.append(f)
        assert not bad, f"anthropic SDK found in: {set(bad)}"
        return True
    _test("No anthropic SDK imports in core", check_no_anthropic)


# ─────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────

if __name__ == "__main__":
    quick = "--quick" in sys.argv

    print(f"""
{'='*55}
  🧪 Moki Tests — {datetime.now().strftime('%d/%m/%Y %H:%M')}
{'='*55}""")

    test_imports()
    test_config()
    test_signatures()
    test_data()
    test_apis(skip=quick)
    test_qa()
    test_no_sdk()

    print(f"""
{'='*55}
  Results: ✅ {PASS} passed  ❌ {FAIL} failed  ⏭ {SKIP} skipped
{'='*55}
""")
    sys.exit(1 if FAIL else 0)
