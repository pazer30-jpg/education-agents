"""
performance_log.py — מה עבד בשטח
מזין נתוני ביצועים מהשטח ומשפיע על הכתיבה הבאה.

שימוש:
  python performance_log.py --add            # הוסף ביצוע ידנית
  python performance_log.py --show           # דוח מה עבד
  python performance_log.py --insights       # Claude מנתח מגמות

הנתונים משפיעים על:
  - voice_profile (Agent 3 רואה מה resonates)
  - agent0_planner (בוחר נושאים לפי מה שעובד)
  - agent3 system prompt (מדגיש טכניקות מנצחות)
"""

import json
import argparse
from pathlib import Path
from datetime import datetime
from config import OUTPUT_DIR

PERF_FILE = OUTPUT_DIR / "performance_log.json"


# ─────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────

def _load() -> list:
    if PERF_FILE.exists():
        try:
            return json.loads(PERF_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save(data: list):
    PERF_FILE.parent.mkdir(parents=True, exist_ok=True)
    PERF_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ─────────────────────────────────────────────
# Input
# ─────────────────────────────────────────────

def add_entry_interactive():
    """הוספת ביצוע ידנית — שאלות אינטראקטיביות."""
    print(f"""
╔══════════════════════════════════════════════════════╗
║  📊 הוספת ביצוע תוכן                                 ║
╚══════════════════════════════════════════════════════╝
""")
    entry = {}

    print("  פלטפורמה:")
    print("    1. LinkedIn  2. בלוג  3. פודקאסט")
    choice = input("  בחירה (1/2/3): ").strip()
    entry["platform"] = {"1": "linkedin", "2": "blog", "3": "podcast"}.get(choice, "linkedin")

    entry["title"] = input("  כותרת / נושא הפוסט: ").strip()

    date_in = input("  תאריך פרסום (Enter = היום): ").strip()
    entry["published_date"] = date_in if date_in else datetime.now().strftime("%Y-%m-%d")

    if entry["platform"] == "linkedin":
        r = input("  תגובות: ").strip()
        l = input("  לייקים: ").strip()
        s = input("  שיתופים: ").strip()
        i = input("  impressions (אם יש): ").strip()
        entry["metrics"] = {
            "comments":    int(r) if r.isdigit() else 0,
            "likes":       int(l) if l.isdigit() else 0,
            "shares":      int(s) if s.isdigit() else 0,
            "impressions": int(i) if i.isdigit() else 0,
        }
    elif entry["platform"] == "blog":
        v = input("  צפיות: ").strip()
        t = input("  זמן ממוצע בדקות: ").strip()
        entry["metrics"] = {
            "views":    int(v) if v.isdigit() else 0,
            "avg_time": float(t) if t.replace(".","").isdigit() else 0,
        }
    elif entry["platform"] == "podcast":
        d = input("  הורדות/השמעות: ").strip()
        entry["metrics"] = {
            "plays": int(d) if d.isdigit() else 0,
        }

    entry["what_worked"] = input("  מה עבד? (תיאור קצר): ").strip()
    entry["what_didnt"]  = input("  מה לא עבד? (Enter לדלג): ").strip()
    entry["hook_type"]   = input("  סוג ה-hook (שאלה/סיפור/נתון/תובנה): ").strip()
    entry["topic_area"]  = input("  תחום (שייכות/מנהיגות/חוסן/אחר): ").strip()

    score_s = input("  ציון אישי 1-10: ").strip()
    entry["personal_score"] = int(score_s) if score_s.isdigit() else 5

    entry["added_at"] = datetime.now().isoformat()

    data = _load()
    data.append(entry)
    _save(data)

    _update_patterns(data)

    print(f"\n  ✅ נשמר! סה\"כ {len(data)} רשומות.\n")
    return entry


def add_entry_quick(platform: str, title: str,
                    comments: int = 0, likes: int = 0,
                    what_worked: str = "", score: int = 5):
    """הוספה מהירה ללא שאלות."""
    data = _load()
    data.append({
        "platform":       platform,
        "title":          title,
        "published_date": datetime.now().strftime("%Y-%m-%d"),
        "metrics":        {"comments": comments, "likes": likes},
        "what_worked":    what_worked,
        "personal_score": score,
        "added_at":       datetime.now().isoformat(),
    })
    _save(data)
    _update_patterns(data)
    print(f"  ✅ נשמר: {title[:50]}")


# ─────────────────────────────────────────────
# Analysis
# ─────────────────────────────────────────────

def _engagement_score(entry: dict) -> float:
    """ציון מנורמל לפי פלטפורמה."""
    m = entry.get("metrics", {})
    p = entry.get("platform", "")
    if p == "linkedin":
        return m.get("comments", 0) * 3 + m.get("likes", 0) + m.get("shares", 0) * 5
    elif p == "blog":
        return m.get("views", 0) + m.get("avg_time", 0) * 10
    elif p == "podcast":
        return m.get("plays", 0)
    return entry.get("personal_score", 5) * 10


def _update_patterns(data: list):
    """
    מנתח את הנתונים ושומר patterns ב-memory.json.
    Agent 3 ישתמש בזה בכתיבה הבאה.
    """
    if len(data) < 3:
        return

    from memory import load_memory, save_memory
    mem = load_memory()

    ranked = sorted(data, key=_engagement_score, reverse=True)
    top3   = ranked[:3]

    hook_types  = [e.get("hook_type","")  for e in top3 if e.get("hook_type")]
    topic_areas = [e.get("topic_area","") for e in top3 if e.get("topic_area")]
    worked      = [e.get("what_worked","") for e in top3 if e.get("what_worked")]

    from collections import Counter
    best_hooks  = Counter(hook_types).most_common(2)
    best_topics = Counter(topic_areas).most_common(3)

    patterns = {
        "best_hook_types":   [h[0] for h in best_hooks],
        "best_topics":       [t[0] for t in best_topics],
        "top_performers":    [{"title": e["title"][:60], "score": _engagement_score(e)}
                              for e in top3],
        "what_worked_notes": worked[:3],
        "updated_at":        datetime.now().isoformat(),
        "total_tracked":     len(data),
    }

    mem["performance_patterns"] = patterns
    save_memory(mem)


def show_report(last_n: int = 20):
    """דוח ביצועים."""
    data = _load()
    if not data:
        print("  אין נתונים עדיין. הוסף עם: python performance_log.py --add")
        return

    recent = data[-last_n:]
    ranked = sorted(recent, key=_engagement_score, reverse=True)

    from collections import Counter, defaultdict
    platforms    = Counter(e["platform"] for e in recent)
    hook_scores  = defaultdict(list)
    topic_scores = defaultdict(list)

    for e in recent:
        s = _engagement_score(e)
        if e.get("hook_type"):
            hook_scores[e["hook_type"]].append(s)
        if e.get("topic_area"):
            topic_scores[e["topic_area"]].append(s)

    print(f"""
{'='*55}
📊 דוח ביצועים ({len(data)} רשומות)
{'='*55}

  פוסטים לפי פלטפורמה:""")
    for plat, n in platforms.most_common():
        print(f"    {plat:<12} {n}")

    print(f"\n  🏆 Top performers:")
    for i, e in enumerate(ranked[:5], 1):
        score = _engagement_score(e)
        m     = e.get("metrics", {})
        meta  = ""
        if "comments" in m:
            meta = f"💬{m['comments']} ❤️{m.get('likes',0)}"
        print(f"    {i}. [{e['platform']:<9}] {e['title'][:45]}")
        print(f"       {meta}  score={score:.0f}  [{e['published_date']}]")

    if hook_scores:
        print(f"\n  🎣 Hook types (ממוצע engagement):")
        for hook, scores in sorted(hook_scores.items(),
                                   key=lambda x: sum(x[1])/len(x[1]), reverse=True):
            avg = sum(scores) / len(scores)
            print(f"    {hook:<15} {avg:.0f}")

    if topic_scores:
        print(f"\n  📚 נושאים (ממוצע engagement):")
        for topic, scores in sorted(topic_scores.items(),
                                    key=lambda x: sum(x[1])/len(x[1]), reverse=True):
            avg = sum(scores) / len(scores)
            print(f"    {topic:<18} {avg:.0f}")

    from memory import load_memory
    patterns = load_memory().get("performance_patterns", {})
    if patterns.get("best_hook_types"):
        print(f"\n  💡 מה המערכת למדה:")
        print(f"     Hook-ים מנצחים: {', '.join(patterns['best_hook_types'])}")
        print(f"     נושאים חמים:    {', '.join(patterns['best_topics'])}")
        for note in patterns.get("what_worked_notes", [])[:2]:
            print(f"     ✓ {note[:70]}")
    print()


def get_patterns_for_prompt() -> str:
    """
    מחזיר insights בפורמט שAgent 3 יוסיף ל-system prompt.
    נקרא מתוך _build_system ב-agent3_content_creator.
    """
    from memory import load_memory
    patterns = load_memory().get("performance_patterns", {})
    if not patterns or patterns.get("total_tracked", 0) < 3:
        return ""

    lines = ["תובנות מהשטח (מה עבד בפועל — חייב לשקף):"]
    if patterns.get("best_hook_types"):
        lines.append(f"  Hook-ים שעובדים הכי טוב: {', '.join(patterns['best_hook_types'])}")
    if patterns.get("best_topics"):
        lines.append(f"  נושאים שהקהל מגיב אליהם: {', '.join(patterns['best_topics'])}")
    for note in patterns.get("what_worked_notes", [])[:2]:
        lines.append(f"  מה שעבד: {note[:80]}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="מעקב ביצועי תוכן")
    parser.add_argument("--add",      action="store_true", help="הוסף ביצוע ידנית")
    parser.add_argument("--show",     action="store_true", help="הצג דוח")
    parser.add_argument("--last",     type=int, default=20)
    parser.add_argument("--insights", action="store_true", help="ניתוח Claude")
    parser.add_argument("--quick",    nargs="+", metavar=("PLATFORM","TITLE"),
                        help="הוספה מהירה: --quick linkedin 'כותרת' 10 50")
    args = parser.parse_args()

    if args.add:
        add_entry_interactive()
    elif args.quick:
        platform = args.quick[0]
        title    = args.quick[1] if len(args.quick) > 1 else "ללא כותרת"
        comments = int(args.quick[2]) if len(args.quick) > 2 else 0
        likes    = int(args.quick[3]) if len(args.quick) > 3 else 0
        add_entry_quick(platform, title, comments, likes)
    elif args.insights:
        from claude_cli import ask_claude
        data = _load()
        if not data:
            print("אין נתונים.")
        else:
            prompt = (
                f"נתח את נתוני הביצועים הבאים של פז שלמה, חינוך בלתי פורמלי:\n"
                f"{json.dumps(data[-15:], ensure_ascii=False)}\n\n"
                f"ספק 3 תובנות ספציפיות ו-2 המלצות עם נימוק קצר. עברית."
            )
            try:
                out = ask_claude(prompt, max_budget=0.5)
                print(f"\n{out}\n")
            except Exception as e:
                print(f"⚠️  שגיאת ניתוח: {e}")
    else:
        show_report(args.last)
