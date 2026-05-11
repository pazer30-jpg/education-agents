"""
performance_learner.py — מה מאפיין פוסטים שעובדים.

לוקח את כל הפוסטים בקורפוס, מחלץ פיצ'רים מדידים:
  - אורך, מספר פסקאות, מילים לפסקה
  - hook type (שאלה / הצהרה / רגע / נתון)
  - דואליות (אבל/מצד שני/דווקא...)
  - ציטוטים מספריים (n=..., d=...)
  - אזכור של רגע אישי (כפר נוער / וינגייט / מכינה)
  - שאלות פתוחות בסוף
  - אורך משפט ממוצע

משווה top-20% מול bottom-20% (לפי QA score או performance_log).
מחלץ דפוסים מובדלים → כותב ל-_memory/performance_patterns.md.

Usage:
  python3 performance_learner.py
  python3 performance_learner.py --platform linkedin
"""

import re
import sys
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from config import OUTPUT_DIR


# ─────────────────────────────────────────────
# Feature extractors
# ─────────────────────────────────────────────

PERSONAL_MOMENTS = re.compile(
    r"(כפר\s*נוער|וינגייט|מכינה|שנת\s*שירות|השומר\s*הצעיר|חניך|מדריך|פנימייה|"
    r"נער\b|נערה\b|בן\s*\d+\b)", re.I
)

DUALITY_MARKERS = re.compile(
    r"(\bאבל\b|מצד\s*שני|ובכל\s*זאת|אף\s*על\s*פי|למרות|דווקא|לעומת\s*זאת|בעוד|ולמרות)",
)

QUESTION_PATTERN = re.compile(r"[?؟]\s*$|[?؟]\s*\n")

NUMERIC_CITATION = re.compile(r"\b(n\s*=\s*\d+|d\s*=\s*\d+\.\d+|r\s*=\s*\d+\.\d+|p\s*<\s*\.\d+)")

AI_TELLS = re.compile(
    r"(חשוב\s*לציין|מעניין\s*לראות|נראה\s*כי|לסיכום|כפי\s*שניתן\s*לראות|"
    r"יש\s*לציין|ראוי\s*לציין|בסיכומו\s*של\s*דבר)", re.I
)


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:]
    return text


def _detect_hook_type(first_para: str) -> str:
    """Classify the opening paragraph."""
    first = first_para.strip().split("\n")[0]
    if not first:
        return "empty"
    if "?" in first or "؟" in first:
        return "question"
    if re.search(r"\d", first):
        return "data"
    if PERSONAL_MOMENTS.search(first):
        return "personal_moment"
    if first.split()[0].lower() in ("ראיתי", "כתבתי", "שמעתי", "החלטתי", "אמרתי"):
        return "personal_verb"
    return "statement"


def _extract_features(text: str) -> dict:
    body = _strip_frontmatter(text)
    body = body.strip()
    if not body:
        return None

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    sentences = re.split(r"[.!?]\s+", body)
    sentences = [s for s in sentences if len(s.split()) >= 3]

    word_count = len(body.split())
    para_count = len(paragraphs)
    sentence_count = len(sentences)

    first_para = paragraphs[0] if paragraphs else ""
    last_para = paragraphs[-1] if paragraphs else ""

    avg_words_per_sentence = (
        sum(len(s.split()) for s in sentences) / sentence_count
        if sentence_count else 0
    )
    avg_words_per_para = word_count / para_count if para_count else 0

    return {
        "word_count": word_count,
        "para_count": para_count,
        "sentence_count": sentence_count,
        "avg_words_per_sentence": round(avg_words_per_sentence, 1),
        "avg_words_per_para": round(avg_words_per_para, 1),
        "hook_type": _detect_hook_type(first_para),
        "ends_with_question": bool(QUESTION_PATTERN.search(last_para)),
        "duality_count": len(DUALITY_MARKERS.findall(body)),
        "personal_moments": len(PERSONAL_MOMENTS.findall(body)),
        "numeric_citations": len(NUMERIC_CITATION.findall(body)),
        "ai_tells": len(AI_TELLS.findall(body)),
        "has_dialog_marker": '"' in body or '"' in body or '"' in body,
    }


# ─────────────────────────────────────────────
# Load posts + scores
# ─────────────────────────────────────────────

def _platform_dir(platform: str) -> Path | None:
    mapping = {
        "linkedin": OUTPUT_DIR / "posts" / "linkedin",
        "blog":     OUTPUT_DIR / "posts" / "blog",
        "podcast":  OUTPUT_DIR / "posts" / "podcast",
    }
    d = mapping.get(platform)
    return d if d and d.exists() else None


def _load_qa_scores() -> dict[str, float]:
    """
    Try to attach QA scores to file paths via analytics.json runs.
    Falls back to file mtime if no scores available.
    """
    analytics_f = OUTPUT_DIR / "analytics.json"
    scores = {}
    if not analytics_f.exists():
        return scores
    try:
        data = json.loads(analytics_f.read_text(encoding="utf-8"))
        for r in data.get("runs", []):
            qa_score = r.get("avg_qa")
            if not isinstance(qa_score, (int, float)):
                continue
            for out in r.get("outputs", []):
                fp = out.get("file")
                if fp:
                    scores[str(fp)] = float(qa_score)
    except Exception:
        pass
    return scores


def _load_performance_log() -> dict[str, dict]:
    """Per-platform engagement data, if performance_log.py has it."""
    try:
        from performance_log import _load
        data = _load()
        return data
    except Exception:
        return {}


def collect_posts(platform: str) -> list[dict]:
    """Return list of {path, text, features, qa, mtime}."""
    d = _platform_dir(platform)
    if not d:
        return []

    qa_scores = _load_qa_scores()
    posts = []

    patterns = ["*.md", "*.txt"] if platform != "podcast" else ["*.md"]
    for pat in patterns:
        for p in d.glob(pat):
            if p.name.startswith("_") or p.name.endswith(".bak"):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            features = _extract_features(text)
            if not features or features["word_count"] < 50:
                continue
            posts.append({
                "path": p,
                "text": text,
                "features": features,
                "qa": qa_scores.get(str(p), None),
                "mtime": p.stat().st_mtime,
            })
    return posts


# ─────────────────────────────────────────────
# Compare top vs bottom
# ─────────────────────────────────────────────

def _rank_score(post: dict) -> float:
    """How 'good' is this post? Uses QA if available, else recency proxy."""
    if post["qa"] is not None:
        return post["qa"]
    # Fallback: posts that survived (weren't deleted) are mid-tier
    return 50.0


def split_top_bottom(posts: list[dict], split_pct: int = 20) -> tuple[list, list]:
    if len(posts) < 10:
        return [], []
    sorted_posts = sorted(posts, key=_rank_score, reverse=True)
    n = max(2, len(sorted_posts) * split_pct // 100)
    return sorted_posts[:n], sorted_posts[-n:]


def _avg(items: list, key: str) -> float:
    vals = [item["features"][key] for item in items
            if isinstance(item["features"].get(key), (int, float))]
    return round(sum(vals) / len(vals), 2) if vals else 0


def _bool_pct(items: list, key: str) -> int:
    if not items:
        return 0
    return round(100 * sum(1 for i in items if i["features"].get(key)) / len(items))


def _hook_distribution(items: list) -> dict[str, int]:
    counts = defaultdict(int)
    for i in items:
        counts[i["features"].get("hook_type", "?")] += 1
    return dict(counts)


def compare(top: list, bottom: list) -> dict:
    """Compute differences between top and bottom groups."""
    fields = ["word_count", "para_count", "avg_words_per_sentence",
              "avg_words_per_para", "duality_count", "personal_moments",
              "numeric_citations", "ai_tells"]
    diffs = {}
    for f in fields:
        top_avg = _avg(top, f)
        bot_avg = _avg(bottom, f)
        delta = round(top_avg - bot_avg, 2)
        if abs(delta) > 0:
            diffs[f] = {"top": top_avg, "bottom": bot_avg, "delta": delta}

    diffs["ends_with_question"] = {
        "top": _bool_pct(top, "ends_with_question"),
        "bottom": _bool_pct(bottom, "ends_with_question"),
    }
    diffs["hooks_top"] = _hook_distribution(top)
    diffs["hooks_bottom"] = _hook_distribution(bottom)
    return diffs


def derive_insights(diffs: dict) -> list[str]:
    """Plain-language rules of thumb."""
    insights = []

    def _interpret(field, label, units="", inverse=False):
        if field not in diffs or "delta" not in diffs[field]:
            return
        d = diffs[field]
        if abs(d["delta"]) < 1:
            return
        better = "פוסטים חזקים" if not inverse else "פוסטים חלשים"
        direction = "יותר" if (d["delta"] > 0) != inverse else "פחות"
        insights.append(
            f"**{label}**: {better} משתמשים ב{direction} ({d['top']} ↔ {d['bottom']}{units})"
        )

    _interpret("word_count", "אורך", " מילים")
    _interpret("para_count", "פסקאות", "")
    _interpret("avg_words_per_sentence", "אורך משפט ממוצע", " מילים")
    _interpret("duality_count", "סימני דואליות (אבל/מצד שני)", "")
    _interpret("personal_moments", "אזכורים אישיים (כפר נוער/מכינה/...)", "")
    _interpret("numeric_citations", "ציטוטים מספריים (n=, d=)", "")
    _interpret("ai_tells", "ביטויי AI ('חשוב לציין')", "", inverse=True)

    # Ending pattern
    if "ends_with_question" in diffs:
        d = diffs["ends_with_question"]
        if abs(d["top"] - d["bottom"]) >= 10:
            who = "חזקים" if d["top"] > d["bottom"] else "חלשים"
            insights.append(
                f"**סיום בשאלה**: יותר נפוץ בפוסטים {who} ({d['top']}% ↔ {d['bottom']}%)"
            )

    # Hook type preference
    top_hooks = diffs.get("hooks_top", {})
    bot_hooks = diffs.get("hooks_bottom", {})
    if top_hooks and bot_hooks:
        for hook in set(top_hooks) | set(bot_hooks):
            t = top_hooks.get(hook, 0)
            b = bot_hooks.get(hook, 0)
            if t + b == 0:
                continue
            if t > b * 1.5 and t >= 2:
                insights.append(f"**Hook '{hook}'**: שכיח יותר בפוסטים חזקים ({t} מול {b})")

    return insights


# ─────────────────────────────────────────────
# Reports
# ─────────────────────────────────────────────

def md_report(by_platform: dict[str, dict]) -> Path:
    parts = [
        "---",
        "moki: true",
        "type: performance_patterns",
        f"updated: {datetime.now().isoformat(timespec='seconds')}",
        "---",
        "",
        "# 📈 Performance Patterns — מה עובד אצל הקהל",
        "",
        f"_עודכן: {datetime.now().strftime('%d/%m/%Y %H:%M')}_",
        "",
        "_השוואת top 20% מול bottom 20% של פוסטים, לפי QA score._",
        "_agent3 קורא את הקובץ הזה לפני שהוא כותב פוסט חדש._",
        "",
    ]

    for platform, result in by_platform.items():
        parts.append(f"## 🎯 {platform.upper()}")
        parts.append("")
        if result["error"]:
            parts.append(f"_{result['error']}_")
            parts.append("")
            continue
        parts.append(f"_{result['total']} פוסטים נסקרו · top={result['n_top']} · bottom={result['n_bottom']}_")
        parts.append("")

        if result["insights"]:
            parts.append("### 💡 תובנות עיקריות")
            parts.append("")
            for ins in result["insights"]:
                parts.append(f"- {ins}")
            parts.append("")
        else:
            parts.append("_אין הבדלים מובהקים עדיין — דרושים יותר פוסטים עם QA scores._")
            parts.append("")

        # Numeric table
        diffs = result["diffs"]
        parts.append("### 📊 נתונים מספריים")
        parts.append("")
        parts.append("| מאפיין | Top 20% | Bottom 20% | הפרש |")
        parts.append("|---|---|---|---|")
        for f, label in [
            ("word_count", "אורך (מילים)"),
            ("para_count", "מספר פסקאות"),
            ("avg_words_per_sentence", "מילים למשפט"),
            ("duality_count", "סימני דואליות"),
            ("personal_moments", "אזכורים אישיים"),
            ("numeric_citations", "ציטוטים מספריים"),
            ("ai_tells", "AI tells"),
        ]:
            if f in diffs and "delta" in diffs[f]:
                d = diffs[f]
                sign = "+" if d["delta"] > 0 else ""
                parts.append(f"| {label} | {d['top']} | {d['bottom']} | {sign}{d['delta']} |")
        parts.append("")

    parts.extend([
        "---",
        "",
        "## 🤖 איך agent3 משתמש בזה",
        "",
        "1. לפני כתיבת פוסט חדש — קורא את הקובץ הזה",
        "2. מאמץ את ה-hook type המומלץ",
        "3. מכוון אורך לסביבת ה-top",
        "4. מקפיד על דואליות + רגע אישי לפי המסקנות",
        "5. הימנע מ-AI tells (הקודמים הוכיחו שזה מוריד QA)",
        "",
        "**הקובץ מתעדכן** אוטומטית בכל ריצת `run_pipeline.sh` או `performance_learner.py` ידנית.",
    ])

    out_path = OUTPUT_DIR / "_memory" / "performance_patterns.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    platforms = ["linkedin", "blog", "podcast"]
    if "--platform" in sys.argv:
        idx = sys.argv.index("--platform")
        if idx + 1 < len(sys.argv):
            platforms = [sys.argv[idx + 1]]

    by_platform = {}
    for platform in platforms:
        posts = collect_posts(platform)
        if len(posts) < 10:
            by_platform[platform] = {
                "error": f"רק {len(posts)} פוסטים — צריך ≥10 לניתוח",
                "total": len(posts),
            }
            continue

        top, bottom = split_top_bottom(posts)
        diffs = compare(top, bottom)
        insights = derive_insights(diffs)

        by_platform[platform] = {
            "error": None,
            "total": len(posts),
            "n_top": len(top),
            "n_bottom": len(bottom),
            "diffs": diffs,
            "insights": insights,
        }

        print(f"\n📈 {platform.upper()}: {len(posts)} פוסטים · top={len(top)}/bottom={len(bottom)}")
        for ins in insights[:5]:
            # Strip markdown for console
            clean = ins.replace("**", "")
            print(f"   • {clean}")

    path = md_report(by_platform)
    print(f"\n📝 דוח: {path.relative_to(OUTPUT_DIR.parent)}")


if __name__ == "__main__":
    main()
