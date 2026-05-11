"""
devils_advocate.py — Critical reviewer for Paz's posts.
מבקר את הפוסט מזווית הקהל הביקורתי ביותר —
חוקר אקדמי, מדריכה ותיקה, או מי שלא מסכים עם פז.

הפלט: הערות שיכולות לשפר את הפוסט לפני פרסום.
לא כותב מחדש — רק מצביע.
"""

from pathlib import Path
from datetime import datetime
from claude_cli import ask_claude_json
from config import OUTPUT_DIR

DEVIL_DIR = OUTPUT_DIR / "devil"
DEVIL_DIR.mkdir(parents=True, exist_ok=True)


_SYSTEM = """אתה Devil's Advocate — קורא ביקורתי של פוסטים בחינוך בלתי-פורמלי.
תפקידך להגן על הקהל מפוסטים חלשים — לא לכתוב טוב יותר, רק להצביע על חולשות.

אתה משחק 3 דמויות במקביל:
1. **חוקרת אקדמית** שיודעת את הספרות בתחום — תזהה הכללה, ציטוט מעוות, או חוסר ניואנס
2. **מדריכה ותיקה (15+ שנות שטח)** שעבדה עם 1000+ נערים — תזהה תיאוריה שלא מתחברת לשטח
3. **מי שלא מסכים עם פז** — תאתגר את ההנחות הסמויות

כללים:
- אתה מאתר חולשות, לא משבח
- אם פוסט באמת חזק — תאמר זאת בקצרה ותעבור הלאה
- ביקורת ספציפית > כללית. "המשפט X חלש כי Y" עדיף על "הפוסט שטחי"
- מקסימום 3-5 הערות איכותיות. עדיף פחות וטובות יותר."""


def review_post(post_text: str, platform: str = "linkedin",
                topic_context: str = "") -> dict:
    """
    Review a post and return critical objections.
    Returns: {
      "verdict": "strong" | "okay" | "weak",
      "objections": [{"role": str, "issue": str, "suggestion": str}],
      "fact_check_concerns": [str],
      "missing_perspective": str,
      "kill_switch": bool,  # True if post should not be published
    }
    """
    prompt = f"""פלטפורמה: {platform}
נושא רחב: {topic_context or "חינוך בלתי-פורמלי"}

הפוסט:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{post_text[:3500]}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

תן ביקורת חדה. החזר JSON:

{{
  "verdict": "strong" | "okay" | "weak",
  "objections": [
    {{
      "role": "חוקרת אקדמית" | "מדריכה ותיקה" | "מי שלא מסכים",
      "issue": "מה הבעיה (משפט אחד חד)",
      "suggestion": "מה אפשר לעשות אחרת (משפט אחד)"
    }}
  ],
  "fact_check_concerns": [
    "ציטוטים או טענות שדורשים אימות (אם יש)"
  ],
  "missing_perspective": "זווית/קהל/הקשר שחסר בפוסט (משפט אחד או null)",
  "kill_switch": false,
  "kill_reason": "רק אם kill_switch=true: למה אסור לפרסם"
}}

חוקים קשיחים:
- 3-5 objections מקסימום, רק החזקות
- kill_switch=true רק אם יש: שגיאה עובדתית, סטיגמה, פגיעה באוכלוסייה
- אם הפוסט באמת חזק (verdict="strong") — צמצם objections ל-1-2

החזר JSON בלבד."""

    try:
        result = ask_claude_json(prompt, system=_SYSTEM, max_budget=1.0, timeout=300)
    except Exception as e:
        return {
            "verdict": "unknown",
            "objections": [],
            "error": str(e),
            "kill_switch": False,
        }

    return result


def save_review(post_path: Path, review: dict) -> Path:
    """Save the review as a markdown file alongside the post."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = DEVIL_DIR / f"{post_path.stem}_devil_{timestamp}.md"

    verdict = review.get("verdict", "unknown")
    verdict_icon = {"strong": "✅", "okay": "⚠️", "weak": "❌", "unknown": "❓"}.get(verdict, "❓")

    lines = [
        f"# Devil's Advocate — {post_path.name}",
        f"",
        f"**Verdict:** {verdict_icon} {verdict}",
        f"**תאריך:** {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"",
    ]

    if review.get("kill_switch"):
        lines.append(f"## 🛑 STOP — אסור לפרסם")
        lines.append(f"{review.get('kill_reason', 'N/A')}")
        lines.append("")

    objections = review.get("objections", [])
    if objections:
        lines.append("## ✋ Objections")
        for i, obj in enumerate(objections, 1):
            lines.append(f"")
            lines.append(f"### {i}. {obj.get('role', '?')}")
            lines.append(f"**הבעיה:** {obj.get('issue', '')}")
            lines.append(f"**הצעה:** {obj.get('suggestion', '')}")

    fact_concerns = review.get("fact_check_concerns", [])
    if fact_concerns:
        lines.append("")
        lines.append("## 🔍 Fact-check concerns")
        for c in fact_concerns:
            lines.append(f"- {c}")

    missing = review.get("missing_perspective")
    if missing:
        lines.append("")
        lines.append("## 👁 Missing perspective")
        lines.append(missing)

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def review_and_save(post_path: Path, platform: str = "linkedin",
                    topic_context: str = "") -> dict:
    """Review a post file and save the review. Returns the review dict + path."""
    text = post_path.read_text(encoding="utf-8", errors="replace")
    review = review_post(text, platform=platform, topic_context=topic_context)
    review["_saved_to"] = str(save_review(post_path, review))
    return review


# ─────────────────────────────────────────────
# CLI for testing
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json
    if len(sys.argv) < 2:
        print("Usage: python3 devils_advocate.py <path_to_post> [platform]")
        sys.exit(1)
    path = Path(sys.argv[1])
    platform = sys.argv[2] if len(sys.argv) > 2 else "linkedin"
    result = review_and_save(path, platform=platform)
    print(json.dumps(result, ensure_ascii=False, indent=2))
