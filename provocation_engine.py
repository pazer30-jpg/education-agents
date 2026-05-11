"""
provocation_engine.py — Suggests bold/contrarian post angles.

הבעיה: מוקי כותב פוסטים מאוזנים מדי. אין "פז, זה הזמן לקעוע ב-X."

מה זה עושה:
  1. סורק 30 פוסטים אחרונים
  2. מזהה: כל פוסט נגמר ב-balanced/diplomatic
  3. מציע 3 זוויות התגרות:
     - Sacred cow: רעיון בקורפוס שכולם מקבלים — אבל ראוי לערער
     - Generational gap: דבר שמותר היה ב-2010, מטיל חשד היום
     - Counter-establishment: מה הממסד החינוכי לא רוצה לדון בו

Usage:
  python3 provocation_engine.py
  python3 provocation_engine.py --topic שייכות
"""

import sys
import json
from pathlib import Path
from datetime import datetime
from collections import Counter

from config import OUTPUT_DIR, LINKEDIN_DIR, BLOG_DIR


def _recent_posts(n: int = 30) -> list[dict]:
    """Get last N posts."""
    posts = []
    for d, pattern, kind in [
        (LINKEDIN_DIR, "*ready*.txt", "linkedin"),
        (BLOG_DIR, "*.md", "blog"),
    ]:
        if not d.exists():
            continue
        for f in d.glob(pattern):
            if f.name.endswith(".bak"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
                posts.append({"file": f.name, "kind": kind, "text": text[:2000],
                              "ts": f.stat().st_mtime})
            except Exception:
                pass
    return sorted(posts, key=lambda p: p["ts"], reverse=True)[:n]


def detect_diplomacy(text: str) -> dict:
    """Find diplomatic markers — balanced phrases that soften claims."""
    diplomatic = ["מצד אחד... מצד שני", "ככל הנראה", "ייתכן ש", "אפשר לטעון",
                  "תלוי בהקשר", "אין תשובה אחת", "תמיד יש מקרים",
                  "כל ילד שונה", "זה מורכב", "אבל גם", "ובאותה נשימה"]
    matches = [d for d in diplomatic if d in text]
    return {
        "diplomatic_count": len(matches),
        "examples": matches[:3],
        "is_overly_diplomatic": len(matches) >= 3,
    }


def find_sacred_cows(posts: list[dict]) -> list[str]:
    """Find ideas that appear in many posts as positive — never questioned."""
    sacred_terms = {
        "שייכות": "הכל ילדים זקוקים לשייכות — אבל מתי שייכות הופכת למלכודת?",
        "דיאלוג": "בובר אמר 'אני-אתה'. אבל מה אם הילד לא רוצה דיאלוג?",
        "חוסן": "כולם מדברים על חוסן. אולי 'חוסן' הוא הדרך שלנו לא לדבר על שבירות?",
        "מנהיגות": "פז ביקר את 'תוכניות מנהיגות' פעם אחת. הגיע הזמן לחזור.",
        "תקווה": "תקווה כמיומנות. אבל מה זה אומר על מי שהתייאש?",
        "קבוצה": "הקבוצה כאמצעי. אבל הקבוצה גם עשויה לדכא את היחיד.",
        "מדריך": "המדריך כדמות מפתח. אבל לפעמים ההורה מסוכן יותר מההיעדר שלו.",
    }
    counts = Counter()
    for p in posts:
        for term in sacred_terms:
            if term in p["text"]:
                counts[term] += 1

    # Sacred cow = appears in 5+ posts
    return [
        {"term": t, "frequency": c, "provocation": sacred_terms[t]}
        for t, c in counts.most_common() if c >= 5
    ]


def topics_never_questioned(posts: list[dict]) -> list[str]:
    """Topics that always get positive treatment — never criticized."""
    triggers = {
        "תנועות הנוער": "האם תנועות הנוער עדיין רלוונטיות לדור Z?",
        "כפר נוער": "כפר נוער — מודל מנצח או שריד מהמאה ה-20?",
        "הוראה פרטנית": "האם ניתוק חברתי במסגרת תרפויטית הוא חינוך או הסתגרות?",
        "מכינה": "המכינה — שלב מעבר חיוני, או דחיית התבגרות?",
        "אקדמיה": "מחקר חינוכי בעברית — האם בכלל קוראים אותו?",
        "פרקטיקום": "האם ניסיון בפועל באמת מחנך — או רק מנציח?",
    }
    return [
        {"topic": t, "provocation": p}
        for t, p in triggers.items()
    ]


def what_paz_never_said(posts: list[dict]) -> list[str]:
    """Identify topics conspicuously absent."""
    expected = {
        "דת": "פז כותב על חינוך בלתי-פורמלי אבל לא על המקום של דת בו.",
        "מגדר": "אין דיון על איך שונה לבנים מבנות בקבוצות.",
        "כלכלה": "כפר נוער עולה כסף. מי משלם, ומה זה אומר?",
        "כישלון": "פז מודה בכישלונות אישיים — אבל לא בכישלונות מערכתיים.",
        "פוליטיקה": "חינוך בלתי-פורמלי הוא פוליטי. למה פז מתחמק מזה?",
        "טכנולוגיה": "AI בחינוך — נושא חם. פז שותק.",
    }
    full_text = " ".join(p["text"] for p in posts).lower()
    return [
        {"topic": t, "provocation": p}
        for t, p in expected.items() if t.lower() not in full_text
    ]


def suggest_provocations(window: int = 30) -> dict:
    """Main analysis — returns provocation suggestions."""
    posts = _recent_posts(window)
    if len(posts) < 5:
        return {"error": f"Need ≥5 posts, found {len(posts)}"}

    # Count diplomacy
    diplomatic_posts = [p for p in posts if detect_diplomacy(p["text"])["is_overly_diplomatic"]]
    diplomacy_ratio = len(diplomatic_posts) / len(posts)

    sacred = find_sacred_cows(posts)
    silence = what_paz_never_said(posts)
    angles = topics_never_questioned(posts)

    return {
        "window_posts": len(posts),
        "diplomacy_ratio": round(diplomacy_ratio, 2),
        "verdict": "needs_provocation" if diplomacy_ratio > 0.5 else "balanced",
        "sacred_cows": sacred[:3],
        "missing_topics": silence[:3],
        "questionable_norms": angles[:3],
    }


def format_report(result: dict) -> str:
    if "error" in result:
        return f"  ⚠️ {result['error']}"

    lines = [f"\n🔥 Provocation Engine — {datetime.now().strftime('%d/%m/%Y %H:%M')}"]
    lines.append(f"   Posts analyzed:    {result['window_posts']}")
    lines.append(f"   Diplomacy ratio:   {result['diplomacy_ratio']:.0%}")

    icon = "🔥" if result["verdict"] == "needs_provocation" else "✅"
    lines.append(f"   {icon} Verdict:        {result['verdict']}")
    lines.append("")

    if result.get("sacred_cows"):
        lines.append("🐄 Sacred Cows (כתבת עליהם הרבה — חתוך:")
        for sc in result["sacred_cows"]:
            lines.append(f"   ➜ {sc['term']} ({sc['frequency']}x)")
            lines.append(f"     💡 {sc['provocation']}")
        lines.append("")

    if result.get("missing_topics"):
        lines.append("🔇 Topics You Never Touched:")
        for t in result["missing_topics"]:
            lines.append(f"   • {t['topic']}")
            lines.append(f"     💡 {t['provocation']}")
        lines.append("")

    if result.get("questionable_norms"):
        lines.append("⚖️  Sacred Cows of the Field:")
        for n in result["questionable_norms"][:3]:
            lines.append(f"   • {n['topic']}")
            lines.append(f"     💡 {n['provocation']}")

    return "\n".join(lines)


if __name__ == "__main__":
    result = suggest_provocations()
    print(format_report(result))
