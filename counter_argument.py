"""
counter_argument.py
Counter-argument injector — make posts less predictable by surfacing opposing views.

Used by Agent 3 between Voice QA and Devil's Advocate. The goal isn't to weaken
the post — it's to flag when Paz's argument lacks internal tension. Paz's voice
profile values "duality" (feature 4), so a post with no counter-signal is
authentically off-voice, not just rhetorically thin.
"""

import json
import re

from claude_cli import ask_claude


# ─────────────────────────────────────────────
# Heuristics — pure-Python signal detection
# ─────────────────────────────────────────────

# Hebrew counter-signal markers — phrases that already inject opposition.
# If any are present, the post already contains its own counter-argument.
_INTERNAL_COUNTER_MARKERS = [
    "אבל",
    "מצד שני",
    "ובכל זאת",
    "אף על פי",
    "למרות",
    "דווקא",
    "לעומת זאת",
    "מנגד",
    "אלא ש",
    "אם כי",
    "אבל גם",
    "ועדיין",
    "יש מי שיגיד",
    "יש שיטענו",
    "אפשר לטעון",
    "אני יודע שאני מסתכן",
    "אני לא בטוח",
]


def _has_internal_counter(text: str) -> bool:
    """True if the post already contains a counter-argument signal."""
    return any(marker in text for marker in _INTERNAL_COUNTER_MARKERS)


def _strip_json_wrapper(raw: str) -> str:
    """Strip ```json fences if the model returned them."""
    raw = raw.strip()
    if raw.startswith("```"):
        # remove first fence line
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        # remove closing fence
        if raw.endswith("```"):
            raw = raw[: -3]
        # leading 'json' label
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
    return raw.strip()


# ─────────────────────────────────────────────
# Main API
# ─────────────────────────────────────────────

def suggest_counter(post_text: str, topic_themes: list[str]) -> dict:
    """
    Identify the post's main claim and suggest 1-2 counter-arguments.

    Returns:
        {
            "main_claim": str,
            "counter_arguments": [
                {"argument": str, "source_perspective": str},
                ...
            ],
            "should_inject": bool   # True only if post lacks internal counter signals
        }
    """
    text = (post_text or "").strip()
    if not text:
        return {
            "main_claim": "",
            "counter_arguments": [],
            "should_inject": False,
        }

    has_counter = _has_internal_counter(text)
    should_inject = not has_counter

    themes_str = ", ".join(t for t in topic_themes if t) or "חינוך בלתי-פורמלי"

    prompt = f"""קרא את הפוסט הבא של פז שלמה (איש חינוך בלתי-פורמלי) וזהה:
1. הטענה המרכזית שהפוסט עומד מאחוריה (משפט אחד, בעברית).
2. 1-2 טענות-נגד אמיתיות מנקודות מבט שונות.
   כל טענת-נגד צריכה להיות ענייניות — לא קש-מן.
   ציין מאיזו זווית/דמות/תחום היא מגיעה (למשל: "מורה במערכת הפורמלית",
   "חוקר כמותני", "הורה ספקן", "מדריך ותיק שראה אופנות עוברות").

הנושאים הרלוונטיים: {themes_str}

הפוסט:
---
{text}
---

החזר JSON בלבד בפורמט הזה (ללא markdown, ללא הסבר):
{{
  "main_claim": "...",
  "counter_arguments": [
    {{"argument": "...", "source_perspective": "..."}},
    {{"argument": "...", "source_perspective": "..."}}
  ]
}}"""

    main_claim = ""
    counter_arguments: list[dict] = []

    try:
        raw = ask_claude(prompt, system="", max_budget=0.3, timeout=120)
        cleaned = _strip_json_wrapper(raw)
        # Try to recover JSON even if model added prose around it.
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(0)
        data = json.loads(cleaned)
        main_claim = (data.get("main_claim") or "").strip()
        raw_counters = data.get("counter_arguments") or []
        for c in raw_counters[:2]:
            arg = (c.get("argument") or "").strip()
            persp = (c.get("source_perspective") or "").strip()
            if arg:
                counter_arguments.append({
                    "argument": arg,
                    "source_perspective": persp or "נקודת מבט חלופית",
                })
    except Exception as e:
        # Soft-fail — we never want to break Agent 3 because of this enrichment.
        main_claim = ""
        counter_arguments = []
        # Surface the error in the result so callers can log it.
        return {
            "main_claim": "",
            "counter_arguments": [],
            "should_inject": should_inject,
            "error": f"counter_argument LLM call failed: {e}",
        }

    return {
        "main_claim": main_claim,
        "counter_arguments": counter_arguments,
        "should_inject": should_inject,
    }


# ─────────────────────────────────────────────
# Pretty-printer for Agent 3 console output
# ─────────────────────────────────────────────

def format_counter_report(result: dict) -> str:
    """Format a suggest_counter() result for human-readable terminal output."""
    lines = []
    if result.get("error"):
        lines.append(f"  ⚠️ Counter-arg: {result['error']}")
        return "\n".join(lines)

    if not result.get("should_inject"):
        lines.append("  🪞 Counter-arg: כבר יש מתח פנימי בפוסט (אבל/מצד שני/ובכל זאת)")
        return "\n".join(lines)

    claim = result.get("main_claim") or "?"
    lines.append(f"  🪞 Counter-arg suggestion (חסר מתח פנימי)")
    lines.append(f"     טענה מרכזית: {claim[:120]}")
    counters = result.get("counter_arguments") or []
    if not counters:
        lines.append("     (אין הצעות נגד)")
    else:
        for i, c in enumerate(counters, 1):
            persp = c.get("source_perspective", "?")
            arg = c.get("argument", "")
            lines.append(f"     {i}. [{persp}] {arg[:140]}")
    return "\n".join(lines)
