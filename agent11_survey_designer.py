"""
agent11_survey_designer.py — Agent 11: Survey Methodologist.

Takes a research question (or set of them) and generates a complete
survey instrument grounded in established theoretical frameworks.

Outputs two files per survey:
  output/surveys/<slug>/spec.md   — methodological documentation
  output/surveys/<slug>/items.csv  — for upload to Google Forms

Reads from memory:
  output/_memory/survey_methodology.md (frameworks + design principles)
  output/_memory/agent_backstories.md  (persona: ד"ר מיכל גרין)
  output/_memory/voice_rules.md        (Paz's voice — relevant for Hebrew tone)
  output/_memory/theoretical_anchors.md (concepts Paz already uses)

Usage:
  python3 agent11_survey_designer.py \\
      --topic "loneliness in boarding-school principals" \\
      --rq "What factors predict resilience among isolated principals?" \\
      --rq "How do principals describe their support networks?" \\
      --audience "boarding-school principals in Israel" \\
      --frameworks Hobfoll CD-RISC \\
      --estimated-n 80

  python3 agent11_survey_designer.py --resume <slug>   # regenerate from saved spec
"""

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR

SURVEYS_DIR = OUTPUT_DIR / "surveys"

# Survey design budget — one call (~$1.50). Single shot is fine because the
# survey is small and the methodology memory carries most of the rules.
# 480s timeout — JSON with 15-30 nested items takes Claude 3-7 min on cold CLI.
DESIGN_BUDGET = 1.50
DESIGN_TIMEOUT = 480  # 8 min


def _slug(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9א-ת\s-]", "", text or "")
    return re.sub(r"\s+", "-", text.strip().lower())[:50]


def _load_memory_block() -> str:
    """Pull the methodology + relevant voice/anchors into one prompt block."""
    try:
        from obsidian_memory import format_for_prompt, get_backstory
        mem = format_for_prompt([
            "survey_methodology",
            "voice_rules",
            "theoretical_anchors",
        ], max_chars_per_note=2200)
        backstory = get_backstory("survey_designer")
        if backstory:
            return f"## Your persona\n\n{backstory}\n\n---\n\n{mem}"
        return mem
    except Exception:
        return ""


def design(topic: str, rqs: list[str], audience: str,
           frameworks: list[str] | None = None,
           estimated_n: int = 80,
           bilingual: bool = True) -> dict:
    """
    Generate a complete survey instrument.
    Returns: {"spec_md": str, "items": list[dict], "slug": str, "saved_to": [paths]}
    """
    from claude_cli import ask_claude_json
    slug = _slug(topic)
    out_dir = SURVEYS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    memory = _load_memory_block()
    frameworks_str = ", ".join(frameworks) if frameworks else "(let methodology memory suggest)"
    rqs_block = "\n".join(f"  RQ{i+1}: {q}" for i, q in enumerate(rqs))

    prompt = f"""{memory}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are designing a survey instrument as ד"ר מיכל גרין.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Topic: {topic}
Audience: {audience}
Estimated sample size: n ≈ {estimated_n}
Preferred frameworks: {frameworks_str}
Language: {'Hebrew + English (bilingual)' if bilingual else 'Hebrew only'}

Research questions:
{rqs_block}

Build a complete survey instrument. Return JSON with EXACTLY this shape:

{{
  "spec": {{
    "rationale": "<1-2 paragraphs Hebrew: why this design fits these RQs>",
    "target_audience": "<who exactly + recruitment plan>",
    "estimated_n": {estimated_n},
    "estimated_completion_min": <integer 5-15>,
    "frameworks_used": ["<framework 1>", "<framework 2>", ...],
    "expected_test_per_rq": [
      {{"rq": "RQ1: ...", "stat_test": "Pearson correlation", "rationale": "..."}},
      ...
    ],
    "limitations": ["<3-4 specific methodological limitations>"]
  }},
  "items": [
    {{
      "section": "demographics" | "<framework name>" | "open" | ...,
      "order": <integer>,
      "text_he": "<Hebrew item text>",
      "text_en": "<English equivalent>",
      "type": "likert5" | "likert7" | "dichotomous" | "frequency" | "open" | "demo_age" | "demo_gender" | "demo_tenure",
      "scale_min": <int or null>,
      "scale_max": <int or null>,
      "scale_labels_he": ["<low>", "<mid>", "<high>"],
      "scale_labels_en": ["<low>", "<mid>", "<high>"],
      "required": true | false,
      "reverse_coded": true | false,
      "framework_anchor": "<framework name or 'demographic' or 'qualitative'>"
    }},
    ... 15-30 items total ...
  ]
}}

Constraints (broken = unusable instrument):
  1. Total time-to-complete ≤ 15 minutes (count ~30s per Likert item, ~90s per open)
  2. 20-30% of Likert items must be reverse_coded=true (detect acquiescence)
  3. Each RQ must be answerable from the items (no orphan RQs)
  4. Demographics LAST, not first (lower drop-off)
  5. Open-ended questions ≤ 3 in total
  6. NEVER include leading or double-barrel items
  7. Every Likert item gets a non-empty framework_anchor

Return ONLY the JSON. No prose before or after.
"""
    print(f"  [Agent11] Designing survey for: {topic[:60]}...")
    try:
        result = ask_claude_json(prompt, max_budget=DESIGN_BUDGET,
                                 timeout=DESIGN_TIMEOUT)
    except Exception as e:
        return {"error": f"Claude call failed: {e}"}

    if not isinstance(result, dict) or "items" not in result:
        return {"error": "Claude returned malformed response (missing 'items')"}

    items = result["items"]
    spec = result.get("spec", {})
    if not items:
        return {"error": "No items in returned design"}

    # ── Save items.csv (Google-Forms-friendly columns) ──
    csv_path = out_dir / "items.csv"
    cols = ["section", "order", "text_he", "text_en", "type",
            "scale_min", "scale_max", "scale_labels_he", "scale_labels_en",
            "required", "reverse_coded", "framework_anchor"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for it in items:
            w.writerow([
                it.get("section", ""),
                it.get("order", 0),
                it.get("text_he", ""),
                it.get("text_en", ""),
                it.get("type", ""),
                it.get("scale_min", ""),
                it.get("scale_max", ""),
                " | ".join(it.get("scale_labels_he", []) or []),
                " | ".join(it.get("scale_labels_en", []) or []),
                str(it.get("required", False)).lower(),
                str(it.get("reverse_coded", False)).lower(),
                it.get("framework_anchor", ""),
            ])

    # ── Save spec.md (human-readable methodology doc) ──
    spec_md = [
        "---",
        "moki: true",
        "type: survey_spec",
        f"updated: {datetime.now().isoformat(timespec='seconds')}",
        f"topic: {topic}",
        "---",
        "",
        f"# 📋 שאלון: {topic}",
        "",
        f"**Slug:** `{slug}`  ·  **Generated:** {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
        "## נימוק עיצובי",
        "",
        spec.get("rationale", "—"),
        "",
        "## שאלות מחקר",
        "",
    ]
    for i, q in enumerate(rqs, 1):
        spec_md.append(f"  - **RQ{i}:** {q}")
    spec_md.extend([
        "",
        "## אוכלוסיית יעד",
        "",
        spec.get("target_audience", audience),
        "",
        f"**גודל דגימה מומלץ:** n ≈ {spec.get('estimated_n', estimated_n)}",
        f"**זמן השלמה מוערך:** {spec.get('estimated_completion_min', '?')} דק'",
        "",
        "## מסגרות תיאורטיות בשימוש",
        "",
    ])
    for f in spec.get("frameworks_used", []):
        spec_md.append(f"  - {f}")
    spec_md.extend([
        "",
        "## מבחן סטטיסטי לכל RQ",
        "",
    ])
    for tp in spec.get("expected_test_per_rq", []):
        spec_md.append(f"  - **{tp.get('rq', '?')}** → {tp.get('stat_test', '?')}")
        spec_md.append(f"    _{tp.get('rationale', '')}_ ")
    spec_md.extend([
        "",
        "## מגבלות מתודולוגיות",
        "",
    ])
    for lim in spec.get("limitations", []):
        spec_md.append(f"  - {lim}")
    spec_md.extend([
        "",
        f"## פריטים ({len(items)})",
        "",
        f"ראה `items.csv` — להעלאה ל-Google Forms / Typeform / כל פלטפורמת סקרים.",
        "",
        "| # | סעיף | סוג | RC | טקסט (HE) |",
        "|---|---|---|---|---|",
    ])
    for it in sorted(items, key=lambda x: x.get("order", 0)):
        rc = "✓" if it.get("reverse_coded") else ""
        txt = (it.get("text_he", "") or "")[:80].replace("|", "\\|")
        spec_md.append(f"| {it.get('order','?')} | {it.get('section','')} | "
                       f"{it.get('type','')} | {rc} | {txt} |")
    spec_md.append("")
    spec_md.append("---")
    spec_md.append("")
    spec_md.append("**הצעדים הבאים:**")
    spec_md.append("")
    spec_md.append("1. עיין בפריטים, וודא שאתה עומד מאחורי כל אחד")
    spec_md.append("2. העלה את `items.csv` ל-Google Forms / Typeform")
    spec_md.append("3. הרץ פילוט (5-10 משיבים), אסוף הערות")
    spec_md.append("4. אחרי השקה מלאה, הורד CSV של תגובות והרץ:")
    spec_md.append(f"   `python3 agent12_response_analyzer.py --slug {slug} --responses path/to/responses.csv`")

    spec_path = out_dir / "spec.md"
    spec_path.write_text("\n".join(spec_md), encoding="utf-8")

    return {
        "slug":     slug,
        "items":    items,
        "spec":     spec,
        "saved_to": [str(spec_path), str(csv_path)],
    }


def main():
    ap = argparse.ArgumentParser(description="Agent 11 — Survey Designer")
    ap.add_argument("--topic",       required=True,
                    help="Short description of what the survey is about")
    ap.add_argument("--rq",          action="append", default=[],
                    help="Research question (can pass multiple times)")
    ap.add_argument("--audience",    default="general",
                    help="Target audience description")
    ap.add_argument("--frameworks",  nargs="*", default=[],
                    help="Theoretical frameworks to prefer (e.g. Hobfoll CD-RISC)")
    ap.add_argument("--estimated-n", type=int, default=80,
                    help="Target sample size (default 80)")
    ap.add_argument("--he-only",     action="store_true",
                    help="Hebrew-only (skip English equivalents)")
    args = ap.parse_args()

    if not args.rq:
        print("❌ at least one --rq required")
        sys.exit(1)

    result = design(
        topic       = args.topic,
        rqs         = args.rq,
        audience    = args.audience,
        frameworks  = args.frameworks,
        estimated_n = args.estimated_n,
        bilingual   = not args.he_only,
    )
    if "error" in result:
        print(f"❌ {result['error']}")
        sys.exit(1)

    print(f"✅ Designed survey: {result['slug']}")
    for p in result["saved_to"]:
        print(f"   {Path(p).relative_to(OUTPUT_DIR.parent)}")
    print(f"   {len(result['items'])} items")


if __name__ == "__main__":
    main()
