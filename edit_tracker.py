"""
edit_tracker.py — Watch Obsidian edits + learn from them.

הרעיון: אתה עורך פוסט ב-Obsidian → מוקי לומד מהדיף.
היום אין דרך לדעת **מה אתה משנה** — רק את הסוף.

איך זה עובד:
  1. Snapshot של כל קובץ moki=true ב-output/_snapshots/<file>.snapshot
  2. כל שעה — מוקי בודק שינויים: diff בין הsnapshot לקובץ הנוכחי
  3. שומר את ה-diff ב-output/edits/<file>_<ts>.md
  4. reflective_loop קורא את ה-edits ומבין דפוסי תיקון
  5. Voice profile updates מבוססות על מה שאתה תיקנת

Usage:
  python3 edit_tracker.py snapshot       # take initial snapshots
  python3 edit_tracker.py diff           # find what changed since last snapshot
  python3 edit_tracker.py learn          # extract patterns from accumulated edits
"""

import sys
import re
import json
import hashlib
import difflib
from pathlib import Path
from datetime import datetime
from collections import Counter

from config import OUTPUT_DIR, ARTICLES_DIR, LINKEDIN_DIR, BLOG_DIR, PODCAST_DIR

SNAPSHOTS_DIR = OUTPUT_DIR / "_snapshots"
EDITS_DIR = OUTPUT_DIR / "edits"
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
EDITS_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
# Snapshot helpers
# ─────────────────────────────────────────────

def _content_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


def _all_moki_files() -> list[Path]:
    """All files with moki=true frontmatter or in posts/articles folders."""
    files = []
    for source in [
        ARTICLES_DIR.glob("*.md"),
        LINKEDIN_DIR.glob("*ready*.txt"),
        BLOG_DIR.glob("*.md"),
        PODCAST_DIR.glob("*script*.md"),
    ]:
        for f in source:
            if f.name.endswith(".bak") or f.name.startswith("_"):
                continue
            files.append(f)
    return files


def take_snapshots() -> int:
    """Snapshot all current Moki files."""
    n = 0
    for f in _all_moki_files():
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        snap_name = f"{f.stem}__{_content_hash(text)}.snapshot"
        snap_path = SNAPSHOTS_DIR / snap_name
        if not snap_path.exists():
            snap_path.write_text(text, encoding="utf-8")
            n += 1
    return n


def find_recent_snapshot(file_stem: str) -> Path | None:
    """Find most recent snapshot of a file by stem."""
    candidates = list(SNAPSHOTS_DIR.glob(f"{file_stem}__*.snapshot"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


# ─────────────────────────────────────────────
# Diff detection
# ─────────────────────────────────────────────

def detect_edits() -> list[dict]:
    """Compare current files to last snapshots — return list of edits."""
    edits = []
    for f in _all_moki_files():
        try:
            current = f.read_text(encoding="utf-8")
        except Exception:
            continue

        snap = find_recent_snapshot(f.stem)
        if not snap:
            continue

        original = snap.read_text(encoding="utf-8")
        if current == original:
            continue

        # Diff exists
        diff = list(difflib.unified_diff(
            original.split("\n"),
            current.split("\n"),
            lineterm="",
            n=1,  # minimal context
        ))
        if not diff:
            continue

        added = [l[1:].strip() for l in diff if l.startswith("+") and not l.startswith("+++")]
        removed = [l[1:].strip() for l in diff if l.startswith("-") and not l.startswith("---")]

        edits.append({
            "file": str(f.relative_to(OUTPUT_DIR)),
            "stem": f.stem,
            "snapshot_at": datetime.fromtimestamp(snap.stat().st_mtime).isoformat(),
            "edited_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            "lines_added": len(added),
            "lines_removed": len(removed),
            "added_samples": [l for l in added if l][:5],
            "removed_samples": [l for l in removed if l][:5],
        })
    return edits


def save_edits(edits: list[dict]) -> Path:
    """Save edits to a timestamped log."""
    if not edits:
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = EDITS_DIR / f"edits_{stamp}.json"
    out.write_text(json.dumps(edits, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ─────────────────────────────────────────────
# Learn patterns from accumulated edits
# ─────────────────────────────────────────────

def learn_patterns() -> dict:
    """
    Aggregate all edit logs and extract patterns:
      - Phrases consistently removed (likely AI tells Paz hates)
      - Phrases consistently added (Paz's preferences)
      - Sentence-level rewrites
    """
    all_logs = sorted(EDITS_DIR.glob("edits_*.json"))
    if not all_logs:
        return {"error": "no edit logs yet"}

    removed_phrases = Counter()
    added_phrases = Counter()
    edits_count = 0

    for log_file in all_logs:
        try:
            edits = json.loads(log_file.read_text(encoding="utf-8"))
            for edit in edits:
                edits_count += 1
                for phrase in edit.get("removed_samples", []):
                    if 10 < len(phrase) < 100:
                        removed_phrases[phrase] += 1
                for phrase in edit.get("added_samples", []):
                    if 10 < len(phrase) < 100:
                        added_phrases[phrase] += 1
        except Exception:
            pass

    # Phrases removed multiple times = candidate for forbidden_patterns
    consistently_removed = [p for p, c in removed_phrases.most_common(20) if c >= 2]
    consistently_added = [p for p, c in added_phrases.most_common(20) if c >= 2]

    return {
        "total_edits": edits_count,
        "logs_analyzed": len(all_logs),
        "consistently_removed": consistently_removed[:10],
        "consistently_added": consistently_added[:10],
        "suggestions": {
            "add_to_forbidden": consistently_removed[:5],
            "add_to_authentic_voice": consistently_added[:5],
        },
    }


# ─────────────────────────────────────────────
# Obsidian sync — push learned patterns to memory note
# ─────────────────────────────────────────────

def push_to_obsidian_memory(result: dict) -> bool:
    """Update output/_memory/editor_corrections.md with current learnings."""
    if "error" in result or result.get("total_edits", 0) == 0:
        return False
    try:
        from obsidian_memory import save_memory_note, load_memory_note
    except Exception:
        return False

    removed = result.get("consistently_removed") or []
    added = result.get("consistently_added") or []

    body_parts = [
        "# ✂️ תיקונים שלמדנו מעריכות",
        "",
        f"> מצטבר אוטומטית מ-edit_tracker.py.",
        f"> מבוסס על {result.get('total_edits', 0)} עריכות ב-{result.get('logs_analyzed', 0)} לוגים.",
        f"> agent2_5_editor משתמש בזה לתיקונים מקדימים.",
        "",
        "## 🚫 מילים שאתה מסיר באופן עקבי",
        "",
        "| מילה/ביטוי |",
        "|---|",
    ]
    if not removed:
        body_parts.append("| _(אין דפוסים עקביים עדיין — דרושות 5+ עריכות)_ |")
    for phrase in removed[:10]:
        body_parts.append(f"| {phrase[:120]} |")

    body_parts.extend([
        "",
        "## ✅ מילים שאתה מוסיף באופן עקבי",
        "",
        "| מילה/ביטוי |",
        "|---|",
    ])
    if not added:
        body_parts.append("| _(empty)_ |")
    for phrase in added[:10]:
        body_parts.append(f"| {phrase[:120]} |")

    body_parts.extend([
        "",
        "---",
        "",
        f"_עודכן אוטומטית: {datetime.now().strftime('%d/%m/%Y %H:%M')}_",
    ])

    save_memory_note("editor_corrections", "\n".join(body_parts), note_type="editor_corrections")
    return True


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 edit_tracker.py [snapshot|diff|learn]")
        return

    cmd = sys.argv[1]

    if cmd == "snapshot":
        n = take_snapshots()
        print(f"✅ {n} new snapshots saved to {SNAPSHOTS_DIR}")

    elif cmd == "diff":
        edits = detect_edits()
        if not edits:
            print("  ℹ️ אין שינויים מאז הsnapshot האחרון")
            return
        print(f"\n📝 זוהו {len(edits)} עריכות:")
        for e in edits[:5]:
            print(f"\n  📄 {e['file']}")
            print(f"     +{e['lines_added']} −{e['lines_removed']} שורות")
            for r in e["removed_samples"][:2]:
                if r.strip():
                    print(f"     ❌ הוסר: {r[:80]}")
            for a in e["added_samples"][:2]:
                if a.strip():
                    print(f"     ✅ נוסף: {a[:80]}")
        path = save_edits(edits)
        if path:
            print(f"\n💾 נשמר: {path.name}")

    elif cmd == "learn":
        result = learn_patterns()
        if "error" in result:
            print(f"  ⚠️ {result['error']}")
            return
        print(f"\n🧠 ניתוח עריכות — מתוך {result['total_edits']} עריכות ב-{result['logs_analyzed']} לוגים")
        print(f"\n❌ ביטויים שאתה מסיר באופן עקבי:")
        for p in result["consistently_removed"][:5]:
            print(f"   - {p[:80]}")
        print(f"\n✅ ביטויים שאתה מוסיף באופן עקבי:")
        for p in result["consistently_added"][:5]:
            print(f"   + {p[:80]}")
        print(f"\n💡 הצעות לעדכון voice_profile:")
        if result["suggestions"]["add_to_forbidden"]:
            print(f"   להוסיף ל-forbidden_patterns:")
            for p in result["suggestions"]["add_to_forbidden"][:3]:
                print(f"     • {p[:80]}")
        # Push to Obsidian memory
        if push_to_obsidian_memory(result):
            print(f"\n🧠 עודכן: output/_memory/editor_corrections.md")
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
