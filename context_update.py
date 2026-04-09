"""
context_update.py — עדכון קונטקסט אישי
מאפשר לפז לעדכן את המערכת על מה שקורה לו עכשיו.

Usage:
  python context_update.py          # ממשק שאלות אינטראקטיבי
  python context_update.py --show   # הצג קונטקסט נוכחי
  python context_update.py --quick "מחפש עבודה, כותב תזה על חוסן"
"""

import sys
import json
import re
import argparse
from datetime import datetime
from memory import get_context, save_context, format_rules_for_prompt, get_rejection_rules


def _ask(prompt: str, current: str = "", multiline: bool = False) -> str:
    if current:
        print(f"  נוכחי: {current[:80]}")
    if multiline:
        print(f"  {prompt} (שורה ריקה לסיום):")
        lines = []
        while True:
            line = input("  > ").strip()
            if not line:
                break
            lines.append(line)
        return "\n".join(lines) if lines else current
    else:
        val = input(f"  {prompt}: ").strip()
        return val if val else current


def _ask_list(prompt: str, current: list) -> list:
    if current:
        print(f"  נוכחי:")
        for i, item in enumerate(current, 1):
            print(f"    {i}. {item}")
    print(f"  {prompt} (Enter ריק לשמור, 'נקה' למחוק הכל):")
    items = list(current)
    while True:
        line = input("  + ").strip()
        if not line:
            break
        if line == "נקה":
            items = []
            break
        items.append(line)
    return items


def interactive_update():
    ctx = get_context()

    print(f"""
╔══════════════════════════════════════════════════════╗
║  🧠 עדכון קונטקסט אישי                               ║
║  המידע הזה ישפיע על כל תוכן שייוצר מעכשיו            ║
╚══════════════════════════════════════════════════════╝

Enter ריק = שמור ערך נוכחי
""")

    print("1️⃣  באיזו עונה אתה נמצא עכשיו?")
    print("   דוגמאות: 'מחפש עבודה', 'בונה קהל LinkedIn', 'כותב תזה', 'משלב הכל'")
    ctx["season"] = _ask("העונה שלך", ctx.get("season",""))

    print("\n2️⃣  למה אתה כותב תוכן עכשיו?")
    ctx["content_purpose"] = _ask("מטרת התוכן", ctx.get("content_purpose",""))

    print("\n3️⃣  מה השאלות הפתוחות שמטרידות אותך?")
    ctx["open_questions"] = _ask_list("הוסף שאלה", ctx.get("open_questions", []))

    print("\n4️⃣  מה קרה לאחרונה שמשפיע על החשיבה שלך?")
    ctx["recent_experiences"] = _ask_list("הוסף חוויה", ctx.get("recent_experiences", []))

    print("\n5️⃣  מה המתחים שאתה חי איתם עכשיו?")
    ctx["current_tensions"] = _ask_list("הוסף מתח", ctx.get("current_tensions", []))

    save_context(ctx)
    print(f"\n✅ קונטקסט עודכן!")
    show_context()


def quick_update(text: str):
    from claude_cli import ask_claude_json

    ctx = get_context()
    prompt = f"""המשתמש כתב על מה שקורה לו עכשיו:
"{text}"

הקונטקסט הנוכחי שלו:
{json.dumps(ctx, ensure_ascii=False, indent=2)}

עדכן את הקונטקסט. החזר JSON עם:
season, content_purpose, open_questions (list), recent_experiences (list), current_tensions (list)

רק JSON."""

    try:
        updated = ask_claude_json(prompt, max_budget=0.2)
        for k, v in updated.items():
            if v:
                ctx[k] = v
        save_context(ctx)
        print("✅ קונטקסט עודכן:")
        show_context()
    except Exception as e:
        print(f"❌ שגיאה: {e}")


def show_context():
    ctx = get_context()
    rules = get_rejection_rules()
    updated = ctx.get("updated_at","")[:10] or "לא עודכן"

    print(f"""
  🧠 קונטקסט נוכחי (עודכן: {updated})
  {'─'*40}
  עונה:          {ctx.get('season','—') or '—'}
  מטרת תוכן:    {ctx.get('content_purpose','—') or '—'}""")

    if ctx.get("open_questions"):
        print("\n  שאלות פתוחות:")
        for q in ctx["open_questions"]:
            print(f"    ? {q}")

    if ctx.get("recent_experiences"):
        print("\n  חוויות אחרונות:")
        for e in ctx["recent_experiences"]:
            print(f"    • {e}")

    if ctx.get("current_tensions"):
        print("\n  מתחים נוכחיים:")
        for t in ctx["current_tensions"]:
            print(f"    ↔ {t}")

    if rules:
        print("\n  כללים מדחיות:")
        for r in rules:
            print(f"    [{r['platform']}] {r['rule']}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="עדכון קונטקסט אישי")
    parser.add_argument("--show",  action="store_true")
    parser.add_argument("--quick", metavar="TEXT")
    args = parser.parse_args()

    if args.show:
        show_context()
    elif args.quick:
        quick_update(args.quick)
    else:
        interactive_update()
