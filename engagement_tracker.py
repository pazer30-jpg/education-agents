"""
engagement_tracker.py — Close the learning loop with real-world engagement.

The pipeline generates content but never learns whether it actually worked.
analytics.py / strong_topics.md rank by internal QA score — a guess.
This tool lets you feed REAL LinkedIn/blog numbers back in.

Flow:
  1. Scan recently published posts (output/posts/linkedin, blog)
  2. Prompt for real metrics (likes, comments, shares) — or load from --batch file
  3. Store via performance_log.py
  4. Rebuild output/_memory/engagement.md — REAL top/bottom performers
  5. agent3 reads engagement.md → writes more of what actually works

Usage:
  python3 engagement_tracker.py              # interactive — walk recent posts
  python3 engagement_tracker.py --batch f.txt  # bulk: "filename | likes | comments" per line
  python3 engagement_tracker.py --report     # rebuild engagement.md only
"""

import sys
import json
from pathlib import Path
from datetime import datetime, timedelta

from config import OUTPUT_DIR

try:
    from performance_log import add_entry_quick, _load as _load_perf, _engagement_score
except Exception:
    add_entry_quick = None
    _load_perf = lambda: []
    _engagement_score = lambda e: 0


POSTS_DIRS = {
    "linkedin": OUTPUT_DIR / "posts" / "linkedin",
    "blog":     OUTPUT_DIR / "posts" / "blog",
}


# ─────────────────────────────────────────────
# Find recent posts
# ─────────────────────────────────────────────

def _recent_posts(days: int = 30) -> list[dict]:
    """Posts modified in the last N days, not yet tracked."""
    cutoff = (datetime.now() - timedelta(days=days)).timestamp()
    tracked_titles = {e.get("title", "") for e in _load_perf()}

    posts = []
    for platform, d in POSTS_DIRS.items():
        if not d.exists():
            continue
        for ext in ("*.md", "*.txt"):
            for p in d.glob(ext):
                if p.name.startswith("_") or p.name.endswith(".bak"):
                    continue
                if p.stat().st_mtime < cutoff:
                    continue
                title = _extract_title(p)
                if title in tracked_titles:
                    continue
                posts.append({
                    "platform": platform,
                    "path": p,
                    "title": title,
                    "mtime": p.stat().st_mtime,
                })
    posts.sort(key=lambda x: x["mtime"], reverse=True)
    return posts


def _extract_title(path: Path) -> str:
    """First meaningful line / frontmatter title."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return path.stem
    # frontmatter title
    if text.startswith("---"):
        for line in text.split("\n"):
            if line.startswith("title:"):
                return line.split(":", 1)[1].strip().strip('"').strip("'")
    # first non-empty content line
    for line in text.split("\n"):
        line = line.strip()
        if line and not line.startswith(("---", "#", "moki", "tags", "kind")):
            return line[:80]
    return path.stem


# ─────────────────────────────────────────────
# Interactive input
# ─────────────────────────────────────────────

def interactive():
    posts = _recent_posts()
    if not posts:
        print("  ℹ️ אין פוסטים חדשים לעדכן (כולם כבר עם נתונים, או אין מה-30 יום).")
        return

    print(f"\n📊 Engagement Tracker — {len(posts)} פוסטים לעדכון")
    print("   הזן מספרים אמיתיים מ-LinkedIn/בלוג. Enter ריק = דלג.\n")

    updated = 0
    for i, post in enumerate(posts, 1):
        print(f"[{i}/{len(posts)}] {post['platform']} · {post['title'][:60]}")
        try:
            likes = input("   👍 לייקים (Enter=דלג): ").strip()
            if not likes:
                print("   ⏭  דולג\n")
                continue
            comments = input("   💬 תגובות: ").strip() or "0"
            shares = input("   🔁 שיתופים: ").strip() or "0"
        except (EOFError, KeyboardInterrupt):
            print("\n  עצירה.")
            break

        try:
            if add_entry_quick:
                add_entry_quick(
                    platform=post["platform"],
                    title=post["title"],
                    likes=int(likes), comments=int(comments),
                    what_worked="",
                )
                # add shares to the just-saved entry
                data = _load_perf()
                if data:
                    data[-1].setdefault("metrics", {})["shares"] = int(shares)
                    _save_perf(data)
            updated += 1
            print(f"   ✅ נשמר\n")
        except ValueError:
            print(f"   ⚠️ מספר לא תקין — דולג\n")

    print(f"\n✅ {updated} פוסטים עודכנו.")
    if updated:
        build_engagement_report()


def _save_perf(data: list):
    try:
        from performance_log import _save
        _save(data)
    except Exception:
        pass


# ─────────────────────────────────────────────
# Batch input — "filename | likes | comments [| shares]" per line
# ─────────────────────────────────────────────

def batch(batch_file: Path):
    if not batch_file.exists():
        print(f"❌ קובץ לא נמצא: {batch_file}")
        return
    posts = {p["title"]: p for p in _recent_posts(days=120)}
    by_stem = {p["path"].stem: p for p in posts.values()}

    updated = 0
    for line in batch_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split("|")]
        if len(parts) < 3:
            print(f"  ⚠️ שורה לא תקינה: {line}")
            continue
        key, likes, comments = parts[0], parts[1], parts[2]
        shares = parts[3] if len(parts) > 3 else "0"

        post = posts.get(key) or by_stem.get(key)
        if not post:
            print(f"  ⚠️ לא נמצא פוסט: {key}")
            continue
        try:
            if add_entry_quick:
                add_entry_quick(post["platform"], post["title"],
                                likes=int(likes), comments=int(comments))
                data = _load_perf()
                if data:
                    data[-1].setdefault("metrics", {})["shares"] = int(shares)
                    _save_perf(data)
            updated += 1
        except ValueError:
            print(f"  ⚠️ מספרים לא תקינים: {line}")

    print(f"✅ {updated} פוסטים עודכנו מ-batch.")
    if updated:
        build_engagement_report()


# ─────────────────────────────────────────────
# Build engagement.md — real top/bottom performers
# ─────────────────────────────────────────────

def build_engagement_report() -> Path:
    data = _load_perf()
    out_path = OUTPUT_DIR / "_memory" / "engagement.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    parts = [
        "---",
        "moki: true",
        "type: engagement",
        f"updated: {datetime.now().isoformat(timespec='seconds')}",
        "---",
        "",
        "# 📊 Real Engagement — מה באמת עבד",
        "",
        f"_עודכן: {datetime.now().strftime('%d/%m/%Y %H:%M')}_",
        f"_מבוסס על {len(data)} פוסטים עם נתוני engagement אמיתיים._",
        "",
        "_agent3 קורא את הקובץ הזה — בניגוד ל-strong_topics (QA פנימי),_",
        "_כאן זה תגובת קהל אמיתית._",
        "",
    ]

    if len(data) < 3:
        parts.append(f"⚠️ רק {len(data)} פוסטים עם נתונים — צריך ≥3 לניתוח משמעותי.")
        parts.append("הרץ `python3 engagement_tracker.py` כדי להוסיף עוד.")
        out_path.write_text("\n".join(parts), encoding="utf-8")
        return out_path

    ranked = sorted(data, key=_engagement_score, reverse=True)
    n = max(2, len(ranked) // 4)
    top, bottom = ranked[:n], ranked[-n:]

    def _row(e):
        m = e.get("metrics", {})
        return (f"| {e.get('title','?')[:50]} | {e.get('platform','?')} | "
                f"{m.get('likes',0)} | {m.get('comments',0)} | "
                f"{m.get('shares',0)} | {_engagement_score(e):.0f} |")

    parts.extend(["## 🌟 Top performers (engagement אמיתי)", "",
                  "| פוסט | פלטפורמה | 👍 | 💬 | 🔁 | score |",
                  "|---|---|---|---|---|---|"])
    for e in top:
        parts.append(_row(e))

    parts.extend(["", "## 📉 Bottom performers", "",
                  "| פוסט | פלטפורמה | 👍 | 💬 | 🔁 | score |",
                  "|---|---|---|---|---|---|"])
    for e in bottom:
        parts.append(_row(e))

    # Aggregate insight
    avg_top = sum(_engagement_score(e) for e in top) / len(top)
    avg_bot = sum(_engagement_score(e) for e in bottom) / len(bottom)
    parts.extend([
        "", "## 💡 פער",
        "",
        f"- Top ממוצע: **{avg_top:.0f}** · Bottom ממוצע: **{avg_bot:.0f}**",
        f"- יחס: top מקבל **{avg_top / max(avg_bot, 1):.1f}×** engagement",
        "",
        "---",
        "",
        "_להוספת נתונים: `python3 engagement_tracker.py`_",
    ])

    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    if "--report" in sys.argv:
        path = build_engagement_report()
        print(f"✅ {path.relative_to(OUTPUT_DIR.parent)}")
        return
    if "--batch" in sys.argv:
        idx = sys.argv.index("--batch")
        if idx + 1 < len(sys.argv):
            batch(Path(sys.argv[idx + 1]))
        else:
            print("Usage: python3 engagement_tracker.py --batch <file>")
        return
    interactive()


if __name__ == "__main__":
    main()
