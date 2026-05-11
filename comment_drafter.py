"""
comment_drafter.py — Comment-reply drafter for Paz on LinkedIn.

After Paz publishes a post and gets engagement, he wants suggested replies
in his voice (engage / challenge / question_back). One LLM call returns
all 3 drafts. Replies are short (100-300 chars) — LinkedIn comment range.

Usage (CLI):
  python3 comment_drafter.py <post-path> "<comment text>"
  python3 comment_drafter.py output/posts/linkedin/foo_ready.txt \\
      "אני מסכים אבל מה לגבי גילאים צעירים?"

Usage (chat):
  תגובה <שם פוסט> <תגובה>
  תגובה דיאלוג "אני מסכים אבל מה לגבי גילאים צעירים?"

Usage (programmatic):
  from comment_drafter import draft_replies, save_drafts
  drafts = draft_replies(post_text, comment_text)
  save_drafts(post_path, drafts)
"""

import argparse
import json
import re
import sys
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR, LINKEDIN_DIR, BLOG_DIR, PODCAST_DIR
from voice_profile import VOICE_PROFILE
from claude_cli import ask_claude


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────

REPLIES_DIR = OUTPUT_DIR / "replies"
REPLIES_DIR.mkdir(parents=True, exist_ok=True)

TONES = ["engage", "challenge", "question_back"]

TONE_DESCRIPTIONS = {
    "engage": (
        "engage — מסכים ומרחיב. 'כן, ועוד...' / 'בדיוק. ויותר מזה...'. "
        "לא חנפנות — הוספה אמיתית של זווית או דוגמה."
    ),
    "challenge": (
        "challenge — חולק בכבוד. 'אני רואה את זה אחרת כי...'. "
        "מציע ניואנס או נקודה שונה, בלי להעליב, בלי להיות פסיביאגרסיבי."
    ),
    "question_back": (
        "question_back — מחזיר שאלה. 'מעניין — ומה לגבי X?'. "
        "פותח את השיחה ולא סוגר אותה."
    ),
}

MIN_LEN = 100
MAX_LEN = 300


# ─────────────────────────────────────────────
# JSON helpers
# ─────────────────────────────────────────────

def _strip_json_wrapper(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.endswith("```"):
            raw = raw[:-3]
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
    return raw.strip()


def _parse_drafts_json(raw: str) -> list[dict]:
    cleaned = _strip_json_wrapper(raw)
    # Extract first JSON array if model added prose
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)
    data = json.loads(cleaned)
    if not isinstance(data, list):
        raise ValueError("expected a JSON array")
    return data


# ─────────────────────────────────────────────
# Voice safeguards on the reply text
# ─────────────────────────────────────────────

_AI_TELLS = [
    "💡", "🔥", "✅", "❌", "👇", "🎯", "📌", "🧵", "🙌", "🚀",
    "חשוב לציין", "מעניין לראות", "בשורה התחתונה", "ראוי לציין",
    "leverage", "robust", "comprehensive", "delve",
]


def _scrub_emojis_and_tells(text: str) -> str:
    """Strip obvious AI tells and emojis from a reply, keep it natural."""
    cleaned = text.strip().strip('"').strip("'")
    for bad in _AI_TELLS:
        cleaned = cleaned.replace(bad, "")
    # Collapse double spaces left after scrubbing
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return cleaned


# ─────────────────────────────────────────────
# Core API
# ─────────────────────────────────────────────

def draft_replies(post_text: str, comment_text: str, num_drafts: int = 3) -> dict:
    """
    Given the original post + a comment from someone, draft 3 reply variants
    in Paz's voice — one per tone (engage / challenge / question_back).

    Returns:
        {
          "comment": str,
          "drafts": [
            {"reply": str, "tone": "engage"|"challenge"|"question_back",
             "length": int}
          ]
        }
    """
    post_text = (post_text or "").strip()
    comment_text = (comment_text or "").strip()
    if not comment_text:
        return {"comment": "", "drafts": [], "error": "empty comment"}

    # Limit num_drafts to the 3 known tones — request always returns all 3
    n = max(1, min(num_drafts, len(TONES)))
    tones = TONES[:n]

    tones_block = "\n".join(f"  - {TONE_DESCRIPTIONS[t]}" for t in tones)

    prompt = f"""אתה כותב תגובות בלינקדאין בקולו של פז שלמה.

{VOICE_PROFILE}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
המשימה: ניסוח תגובות לתגובה שהתקבלה על פוסט של פז
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

הפוסט המקורי של פז (לקונטקסט בלבד — אל תצטט ממנו):
---
{post_text[:2500]}
---

התגובה שהתקבלה (זאת התגובה שצריך להגיב לה):
---
{comment_text}
---

נסח {n} תגובות-תגובה, כל אחת בטון אחר:
{tones_block}

חוקים:
  • כל תגובה: {MIN_LEN}-{MAX_LEN} תווים (טווח תגובה בלינקדאין). אל תחרוג.
  • גוף ראשון. קולו של פז. בלי להישמע כמו AI.
  • בלי אימוג'ים, בלי "חשוב לציין", בלי "בשורה התחתונה".
  • מותרים מארקרי הסתייגות: "אני נוטה לחשוב", "אולי", "(לא בטוח)".
  • שום חנופה בסגנון "תגובה מעולה!". ישר לעניין.
  • engage = מסכים+מרחיב. challenge = חולק בכבוד. question_back = מחזיר שאלה.
  • אל תחזור על מה שכתוב בתגובה — תוסיף.
  • אל תזכיר את המילה "פוסט". תכתוב כאילו אתה משוחח.

החזר JSON בלבד (ללא markdown, ללא הסבר), בפורמט הזה:
[
  {{"tone": "engage", "reply": "..."}},
  {{"tone": "challenge", "reply": "..."}},
  {{"tone": "question_back", "reply": "..."}}
]"""

    drafts: list[dict] = []
    error = None
    try:
        raw = ask_claude(prompt, system="", max_budget=0.3, timeout=120)
        items = _parse_drafts_json(raw)

        # Index returned items by tone (defensive — model may reorder)
        by_tone = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            tone = (item.get("tone") or "").strip().lower()
            reply = (item.get("reply") or "").strip()
            if tone in tones and reply:
                by_tone[tone] = reply

        for tone in tones:
            reply = by_tone.get(tone, "").strip()
            if not reply:
                continue
            reply = _scrub_emojis_and_tells(reply)
            # Soft trim if model overshoots
            if len(reply) > MAX_LEN + 50:
                reply = reply[:MAX_LEN].rstrip() + "…"
            drafts.append({
                "reply": reply,
                "tone": tone,
                "length": len(reply),
            })
    except Exception as e:
        error = f"draft_replies LLM call failed: {e}"

    result = {
        "comment": comment_text,
        "drafts": drafts,
    }
    if error:
        result["error"] = error
    return result


# ─────────────────────────────────────────────
# Save to markdown for easy copy-paste
# ─────────────────────────────────────────────

def save_drafts(post_path: Path, drafts: dict) -> Path:
    """Save to output/replies/<post_name>_replies_<ts>.md for easy copy-paste."""
    post_path = Path(post_path)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = post_path.stem[:40] if post_path.name else "post"
    out_path = REPLIES_DIR / f"{stem}_replies_{ts}.md"

    comment = drafts.get("comment", "")
    items = drafts.get("drafts", [])
    err = drafts.get("error")

    lines = [
        f"# תגובות מוצעות — {stem}",
        "",
        f"**נוצר:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**פוסט מקור:** `{post_path}`",
        "",
        "## התגובה שהתקבלה",
        "",
        f"> {comment}",
        "",
        "## טיוטות לבחירה",
        "",
    ]

    if err:
        lines.append(f"> שגיאה: {err}")
        lines.append("")

    if not items:
        lines.append("(לא נוצרו טיוטות)")
    else:
        tone_label = {
            "engage": "מסכים ומרחיב (engage)",
            "challenge": "חולק בכבוד (challenge)",
            "question_back": "מחזיר שאלה (question_back)",
        }
        for i, d in enumerate(items, 1):
            tone = d.get("tone", "?")
            label = tone_label.get(tone, tone)
            length = d.get("length", len(d.get("reply", "")))
            lines.append(f"### {i}. {label}  _({length} תווים)_")
            lines.append("")
            lines.append("```")
            lines.append(d.get("reply", ""))
            lines.append("```")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("_העתק/הדבק לתגובה ב-LinkedIn._")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# ─────────────────────────────────────────────
# Post lookup helpers (used by chat command + CLI)
# ─────────────────────────────────────────────

_POST_DIRS = [LINKEDIN_DIR, BLOG_DIR, PODCAST_DIR]


def find_post_by_name(name_fragment: str) -> Path | None:
    """
    Find the most-recent post whose filename contains the fragment.
    Searches LinkedIn → Blog → Podcast (LinkedIn priority for replies).
    Case-insensitive substring match. Returns None if no match.
    """
    needle = (name_fragment or "").strip().lower()
    if not needle:
        return None

    candidates: list[Path] = []
    for d in _POST_DIRS:
        if not d.exists():
            continue
        for p in d.glob("*"):
            if not p.is_file():
                continue
            if needle in p.name.lower() or needle in p.stem.lower():
                candidates.append(p)

    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _read_post(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def _format_for_terminal(post_path: Path, drafts: dict, saved_to: Path) -> str:
    items = drafts.get("drafts", [])
    err = drafts.get("error")
    lines = []
    lines.append(f"\n💬 תגובות מוצעות לפוסט: {post_path.name}")
    lines.append(f"   תגובה שהתקבלה: {drafts.get('comment','')[:100]}")
    lines.append("")
    if err:
        lines.append(f"  ⚠️ {err}")
    if not items:
        lines.append("  (לא נוצרו טיוטות)")
    else:
        tone_label = {
            "engage": "מסכים+מרחיב",
            "challenge": "חולק בכבוד",
            "question_back": "מחזיר שאלה",
        }
        for i, d in enumerate(items, 1):
            tone = d.get("tone", "?")
            label = tone_label.get(tone, tone)
            length = d.get("length", 0)
            lines.append(f"  {i}. [{label}] ({length} תווים)")
            lines.append(f"     {d.get('reply','')}")
            lines.append("")
    lines.append(f"📁 נשמר ב: {saved_to}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Comment-reply drafter for Paz on LinkedIn",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
דוגמאות:
  python3 comment_drafter.py output/posts/linkedin/foo_ready.txt "מסכים אבל מה עם גילאים צעירים?"
  python3 comment_drafter.py דיאלוג "אני מסכים אבל מה לגבי גילאים צעירים?"
        """,
    )
    parser.add_argument("post", help="נתיב לפוסט, או חלק משם הקובץ")
    parser.add_argument("comment", help="התגובה שהתקבלה")
    parser.add_argument("--num", type=int, default=3,
                        help="כמות טיוטות (1-3, ברירת מחדל 3)")
    args = parser.parse_args(argv)

    # Resolve post path: try as direct path first, then as name fragment
    post_path = Path(args.post)
    if not post_path.exists():
        match = find_post_by_name(args.post)
        if match:
            post_path = match
        else:
            print(f"  ⚠️ לא נמצא פוסט לפי '{args.post}'")
            return 1

    post_text = _read_post(post_path)
    if not post_text.strip():
        print(f"  ⚠️ הפוסט ריק או לא קריא: {post_path}")
        return 1

    print(f"  📄 פוסט מקור: {post_path.name}")
    print(f"  💬 תגובה: {args.comment}")
    print(f"  🤖 מנסח {args.num} טיוטות בקול של פז...")

    drafts = draft_replies(post_text, args.comment, num_drafts=args.num)
    saved_to = save_drafts(post_path, drafts)
    print(_format_for_terminal(post_path, drafts, saved_to))
    return 0 if drafts.get("drafts") else 1


if __name__ == "__main__":
    sys.exit(main())
