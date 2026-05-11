"""
regenerate_index.py — Auto-build output/_INDEX.md by scanning *.py files.

Reads each .py module's docstring (first line) for description.
Categorizes by filename pattern + tags in docstring.
Outputs Markdown table grouped by category, with wikilinks back to source.

Triggered:
  - Manually: python3 regenerate_index.py
  - Automatically: end of run_pipeline.sh
"""

import re
import ast
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent
INDEX_FILE = ROOT / "output" / "_INDEX.md"


# Category rules — first match wins (order matters)
CATEGORIES = [
    # (display_name, emoji, filename_pattern, description)
    ("5 הסוכנים הראשיים (Pipeline core)", "🎯",
     re.compile(r"^(agent\d|agent_editor|orchestrator)"),
     "סוכני הליבה של הפייפליין"),

    ("כלי כתיבה אקדמית", "🎓",
     re.compile(r"^(thesis_|seminar_|paper_analyzer|meta_analyzer|bibliography|rq_validator)"),
     "תזה / סמינריון / מאמר אקדמי"),

    ("איכות + ולידציה", "🛡",
     re.compile(r"^(qa_|causal_|conflict_|counter_|devils_|anti_|calibration|error_book|hook_tester)"),
     "בקרת איכות, ולידציה, anti-patterns"),

    ("זיכרון + למידה", "🧠",
     re.compile(r"^(memory|obsidian_memory|scratchpad|voice_|reflective_|edit_tracker|active_response|checkpoint|history)"),
     "זיכרון פעיל, למידה מעריכות, רפלקציה"),

    ("Obsidian + פלטים", "🌐",
     re.compile(r"^(obsidian_bridge|daily_note|canvas_builder|corpus_graph|arc_tracker)"),
     "סנכרון לאובסידיאן, ויזואליזציה"),

    ("ניתוח + observability", "📊",
     re.compile(r"^(analytics|observability|dashboard|pacing|performance|provocation|similarity|embeddings|hebrew_lemma|corpus_patterns)"),
     "ניתוח ביצועים, מטריקות"),

    ("Infrastructure", "⚙️",
     re.compile(r"^(config|claude_cli|logger|chat_commands|autopilot|scheduler|notifications|rollback|file_organizer|genre_router|context_update|regenerate_index)"),
     "תשתית, חיבורים, ניהול"),

    ("בדיקות", "🧪",
     re.compile(r"^test"),
     "Unit tests, health checks, regression"),

    ("כלים נוספים", "🎁",
     re.compile(r".*"),  # catch-all
     "כלי עזר נוספים"),
]


def _extract_description(py_path: Path) -> str:
    """Get first non-empty line of module docstring, or filename hint."""
    try:
        # Read full file — partial reads can cut mid-utf8 or before docstring close
        content = py_path.read_text(encoding="utf-8")
        tree = ast.parse(content)
        doc = ast.get_docstring(tree)
        if doc:
            for line in doc.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return line[:120]
    except Exception:
        pass
    return ""


def _categorize(filename: str) -> tuple[str, str, str]:
    for name, emoji, pattern, desc in CATEGORIES:
        if pattern.match(filename):
            return name, emoji, desc
    return "כלים נוספים", "🎁", ""


def build_index() -> Path:
    py_files = sorted(p for p in ROOT.glob("*.py")
                      if not p.name.startswith("_"))

    # Group
    grouped: dict[str, list[tuple[Path, str]]] = {}
    cat_meta: dict[str, tuple[str, str]] = {}
    for py in py_files:
        cat, emoji, desc = _categorize(py.name)
        grouped.setdefault(cat, []).append((py, _extract_description(py)))
        cat_meta[cat] = (emoji, desc)

    # Render
    lines = [
        "# 🦊 Moki — מפת ניווט לקוד",
        "",
        f"> _Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')} · auto-built by `regenerate_index.py`_",
        f"> {len(py_files)} קבצי Python, מסווגים ל-{len(grouped)} קטגוריות.",
        "> כל ה-imports בין הקבצים נשארים פלאטיים (לא מועברים לסאב-תיקיות).",
        "",
        "---",
        "",
    ]

    # Stable category order matches CATEGORIES list
    for cat_name, _, _, _ in CATEGORIES:
        if cat_name not in grouped:
            continue
        emoji, desc = cat_meta[cat_name]
        lines.append(f"## {emoji} {cat_name}")
        if desc:
            lines.append("")
            lines.append(f"_{desc}_")
        lines.append("")
        lines.append("| קובץ | תפקיד |")
        lines.append("|---|---|")
        for py, ds in grouped[cat_name]:
            # Relative path from vault root (output/) back to project root
            rel = f"../{py.name}"
            lines.append(f"| [{py.name}]({rel}) | {ds or '_(no docstring)_'} |")
        lines.append("")

    # Footer
    lines.extend([
        "---",
        "",
        "## 🗂 תיקיות",
        "",
        "```",
        "education-agents/",
        "├── output/              ← Obsidian vault — כל הפלטים והזיכרון",
        "│   ├── _memory/         ← ZIKARON",
        "│   ├── _INDEX.md ← הקובץ הזה",
        "│   └── ...",
        "├── _archive/            ← קבצים ישנים שמחקנו מה-root",
        "├── linkedin_candidates/",
        "├── scripts/ (לעתיד)",
        "└── *.py + *.sh          ← קוד פלאטי",
        "```",
        "",
        "---",
        "",
        "## 🚀 קיצורי דרך (~/.zshrc)",
        "",
        "```bash",
        "moki              # צ'אט אינטראקטיבי",
        "moki-research     # pipeline מחקר",
        "moki-podcast      # פודקאסט",
        "moki-budget       # סטטוס $$",
        "moki-level <0-3>  # רמת אוטונומיה",
        "moki-cap <USD>    # cap יומי",
        "moki-dash         # דאשבורד",
        "moki-vault        # פותח את Obsidian",
        "```",
    ])

    INDEX_FILE.write_text("\n".join(lines), encoding="utf-8")
    return INDEX_FILE


def main():
    path = build_index()
    py_count = len(list(ROOT.glob("*.py")))
    print(f"✅ INDEX rebuilt: {path.relative_to(ROOT)} ({py_count} files indexed)")


if __name__ == "__main__":
    main()
