"""
obsidian_organizer.py — מנהל ארכיון אינטליגנטי ל-Obsidian vault.

לא מנקה — *מסדר עם הבנה*. סוכן שקורא תוכן, מבין הקשר, ומנתב למקום הנכון.

3 שכבות סיווג (החזק ביותר ראשון):
  1. Frontmatter `kind:` (דטרמיניסטי — אם קיים)
  2. תבנית שם + heuristic תוכן (זול, מהיר)
  3. Claude — קורא 1500 תווים, מבין הקשר, מסווג + נימוק
     (רץ רק על קבצים ש-1+2 לא תפסו, או עם --smart על הכל)

קטגוריות:
  article         → articles/
  linkedin post   → posts/linkedin/
  blog post       → posts/blog/
  podcast script  → posts/podcast/
  research idea   → ideas/
  reflection      → reflections/
  proposal        → proposals/
  newsletter      → newsletters/
  daily note      → _daily/
  briefing        → articles/_briefings/
  thesis          → thesis/<stamp>/
  memory          → _memory/
  index           (stays at root, name starts with _)

Usage:
  python3 obsidian_organizer.py             # דוח
  python3 obsidian_organizer.py --apply     # מעביר
  python3 obsidian_organizer.py --md        # שומר דוח באובסידיאן
"""

import re
import sys
import shutil
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from config import OUTPUT_DIR

try:
    from claude_cli import ask_claude_json
except Exception:
    def ask_claude_json(*a, **kw):
        raise RuntimeError("claude_cli not available")


VAULT = OUTPUT_DIR
EXCLUDED_DIRS = {".obsidian", ".trash", "checkpoints", "_state",
                 "_snapshots", "_archive_orphans", "_archive",
                 "papers", "edits", "notifications"}

# ─────────────────────────────────────────────
# Target folders per kind
# ─────────────────────────────────────────────

TARGET_FOLDERS = {
    "article":      VAULT / "articles",
    "briefing":     VAULT / "articles" / "_briefings",
    "linkedin":     VAULT / "posts" / "linkedin",
    "blog":         VAULT / "posts" / "blog",
    "podcast":      VAULT / "posts" / "podcast",
    "idea":         VAULT / "ideas",
    "reflection":   VAULT / "reflections",
    "proposal":     VAULT / "proposals",
    "newsletter":   VAULT / "newsletters",
    "daily":        VAULT / "_daily",
    "memory":       VAULT / "_memory",
    "devil":        VAULT / "devil",
    "arc":          VAULT / "_arcs",
}


# ─────────────────────────────────────────────
# Frontmatter + content detection
# ─────────────────────────────────────────────

_FM_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


def _read_frontmatter(text: str) -> dict:
    m = _FM_RE.match(text)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm


# ─────────────────────────────────────────────
# Kind detection
# ─────────────────────────────────────────────

def _detect_kind(file: Path, text: str = "") -> str | None:
    """Determine the file's category. Returns None if uncertain.

    Order:
      1. Index files (filenames starting with _)
      2. Strong filename patterns (_ready.txt, _script_, briefing) — beat generic kind
      3. Specific frontmatter kind (linkedin, blog, podcast, etc. — not "doc")
      4. Generic "doc" frontmatter → fall through to content/filename
      5. Filename heuristics
      6. Content heuristics
    """
    name = file.name.lower()
    stem = file.stem.lower()

    # 1. Index files (start with _) — never moved
    if file.stem.startswith("_") or stem.startswith("_"):
        return "index"

    fm = _read_frontmatter(text) if text else {}
    fm_kind = (fm.get("kind") or fm.get("type") or "").lower()

    # 2. Strong filename patterns — these beat generic frontmatter
    # Order matters: content-type keywords (linkedin/blog/podcast) win over
    # topic keywords (briefing) when both appear in the filename.

    # 2a. LinkedIn — _ready or "linkedin" anywhere
    if "_ready" in name or "linkedin" in name:
        return "linkedin"

    # 2b. Podcast — _script_ or "podcast" anywhere
    if "_script_" in name or "podcast" in name:
        return "podcast"

    # 2c. Blog — "_blog" anywhere (e.g., topic_blog_20260422.md)
    if "_blog" in name or name.endswith("_blog.md"):
        return "blog"

    # 2d. Briefing — only if NO content-type keyword above matched
    # (a pure briefing has name like "topic_briefing.md", not "topic_briefing_linkedin.txt")
    if "briefing" in stem:
        return "briefing"

    # 3. Specific frontmatter kind (not the generic "doc" default)
    if fm_kind in TARGET_FOLDERS and fm_kind not in {"doc"}:
        return fm_kind

    # 4. Map type variants to canonical (excluding generic "doc")
    type_map = {
        "article":   "article",
        "post":      None,  # ambiguous — fall through to filename
        "linkedin_post": "linkedin",
        "blog_post": "blog",
        "research_proposal": "proposal",
        "voice_rules": "memory",
        "strong_topics": "memory",
        "weak_topics": "memory",
        "editor_corrections": "memory",
        "recurring_sources": "memory",
        "theoretical_anchors": "memory",
        "academic_writing_apa7": "memory",
        "agent_health": "memory",
        "archivist_report": "memory",
        "autonomy": "memory",
        "organizer_report": "memory",
        "index": "index",
    }
    if fm_kind in type_map and type_map[fm_kind]:
        return type_map[fm_kind]

    # 5. Filename heuristics (fallback for files we didn't catch above)
    if "linkedin" in name:
        return "linkedin"
    if "blog" in name:
        return "blog"
    if "newsletter" in name:
        return "newsletter"
    if "proposal" in name:
        return "proposal"
    if "research_idea" in name or "ideas_" in stem:
        return "idea"
    if "reflection" in name or "reflect_" in stem:
        return "reflection"
    if re.match(r"\d{4}-\d{2}-\d{2}", stem):  # YYYY-MM-DD daily
        return "daily"
    if "devil" in name:
        return "devil"
    if "arc_" in stem:
        return "arc"

    # Content heuristics — first 500 chars
    head = (text[:500] or "").lower()
    if "## abstract" in head or "## introduction" in head:
        return "article"

    return None


def _expected_folder(file: Path, text: str = "") -> Path | None:
    kind = _detect_kind(file, text)
    if kind in TARGET_FOLDERS:
        return TARGET_FOLDERS[kind]
    return None


# ─────────────────────────────────────────────
# Smart classifier — Claude reads content, understands context
# ─────────────────────────────────────────────

_CLASSIFICATION_PROMPT = """אתה ספרן של Obsidian vault — תפקידך לסווג קבצים לתיקיות הנכונות.

קטגוריות (בחר אחת):
  - article         — מאמר אקדמי (יש Abstract / Introduction / סקירת ספרות)
  - briefing        — תקציר מהיר של מאמר אקדמי לקריאה
  - linkedin        — פוסט LinkedIn (קצר, hook+תובנה+שאלה)
  - blog            — פוסט בלוג (1500-2500 מילים, פסקאות עם כותרות)
  - podcast         — תסריט פודקאסט (משפטים קצרים, [הפסקה])
  - idea            — רעיון מחקר / נושא לעתיד (קצר, לא מפותח)
  - reflection      — רפלקציה אישית / יומן
  - proposal        — הצעת מחקר אקדמית
  - newsletter      — ניוזלטר שבועי
  - daily           — דף יומן יומי (תאריך בכותרת)
  - memory          — קובץ זיכרון של מוקי (voice_rules, theoretical_anchors, etc.)
  - thesis          — חלק מתזה (proposal/seminar/lit_review)
  - devil           — devils advocate / counter-argument
  - arc             — narrative arc (קשת של 10-15 פוסטים)
  - other           — לא משתבץ באף קטגוריה

המידע שניתן:
שם קובץ: {filename}
תיקייה נוכחית: {current_folder}
תוכן (עד 1500 תווים):
{content}

החזר JSON:
{{
  "kind": "article|briefing|linkedin|...|other",
  "confidence": 0.0-1.0,
  "reasoning": "משפט אחד למה",
  "suggested_filename": "שם קובץ מוצע (או null אם נשאר אותו דבר)"
}}

החזר JSON בלבד."""


def _smart_classify(file: Path, text: str, max_chars: int = 1500) -> dict | None:
    """Use Claude to classify a single file by content. Returns None on failure."""
    snippet = (text or "").strip()[:max_chars]
    if len(snippet) < 50:
        return None

    try:
        rel = file.relative_to(VAULT)
        current = str(rel.parent) if rel.parent != Path(".") else "(root)"
    except ValueError:
        current = "(unknown)"

    prompt = _CLASSIFICATION_PROMPT.format(
        filename=file.name,
        current_folder=current,
        content=snippet,
    )
    try:
        result = ask_claude_json(prompt, max_budget=0.05)
        if isinstance(result, dict) and result.get("kind") in TARGET_FOLDERS:
            return result
    except Exception as e:
        return None
    return None


def _classify_with_smart_fallback(file: Path, text: str,
                                   smart_mode: bool = False) -> dict:
    """
    Returns: {"kind": str, "confidence": float, "method": "rule"|"smart"|"none",
              "reasoning": str (only for smart)}
    """
    # Layer 1: rule-based
    rule_kind = _detect_kind(file, text)

    if rule_kind == "index":
        return {"kind": "index", "confidence": 1.0, "method": "rule",
                "reasoning": "filename starts with _"}

    # If rules found something AND we're not in --smart mode, trust it
    if rule_kind and not smart_mode:
        return {"kind": rule_kind, "confidence": 0.85, "method": "rule",
                "reasoning": "frontmatter or filename pattern"}

    # Layer 2: smart classifier
    if smart_mode or not rule_kind:
        smart = _smart_classify(file, text)
        if smart:
            return {**smart, "method": "smart"}

    # Fallback to rule even with low confidence
    if rule_kind:
        return {"kind": rule_kind, "confidence": 0.5, "method": "rule",
                "reasoning": "weak rule match"}

    return {"kind": None, "confidence": 0.0, "method": "none",
            "reasoning": "could not classify"}


# ─────────────────────────────────────────────
# Scan
# ─────────────────────────────────────────────

def _all_files() -> list[Path]:
    """All MD/TXT files in vault, excluding control dirs."""
    files = []
    for ext in ("*.md", "*.txt"):
        for p in VAULT.rglob(ext):
            try:
                rel = p.relative_to(VAULT)
            except ValueError:
                continue
            # Skip excluded dirs
            if any(part in EXCLUDED_DIRS for part in rel.parts):
                continue
            # Skip the thesis folder — has its own structure
            if rel.parts and rel.parts[0] == "thesis":
                continue
            files.append(p)
    return sorted(files)


def scan(smart_mode: bool = False, max_smart_calls: int = 200) -> dict:
    """Identify misplaced files. Returns dict of moves needed.

    smart_mode=False (default): rules only — fast, free.
    smart_mode=True: Claude classifies every file — slow, costs ~$0.05/file.

    Even when smart_mode=False, files with ambiguous rules go through Claude
    (capped at max_smart_calls per scan to avoid runaway costs).
    """
    files = _all_files()
    moves = []
    skipped = []
    smart_used = 0
    by_kind: dict[str, int] = defaultdict(int)
    misplaced_by_kind: dict[str, int] = defaultdict(int)
    classifications = []

    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""

        # Decide mode for this file
        use_smart_for_file = smart_mode and smart_used < max_smart_calls

        if use_smart_for_file:
            cls = _classify_with_smart_fallback(f, text, smart_mode=True)
            if cls.get("method") == "smart":
                smart_used += 1
        else:
            cls = _classify_with_smart_fallback(f, text, smart_mode=False)
            # If rules failed, still try Claude (capped)
            if cls["kind"] is None and smart_used < max_smart_calls:
                smart = _smart_classify(f, text)
                if smart:
                    cls = {**smart, "method": "smart"}
                    smart_used += 1

        kind = cls.get("kind")
        classifications.append({"file": f, **cls})

        if not kind or kind == "index":
            skipped.append(f)
            continue

        by_kind[kind] += 1

        target_dir = TARGET_FOLDERS.get(kind)
        if not target_dir:
            continue

        # Check if file is already in the right place
        try:
            current_dir = f.parent
            if current_dir == target_dir:
                continue
            if str(target_dir) in str(current_dir):
                continue
        except Exception:
            pass

        # Only suggest move if confidence reasonable
        if cls.get("confidence", 0) < 0.5:
            continue

        misplaced_by_kind[kind] += 1
        moves.append({
            "file": f,
            "kind": kind,
            "method": cls.get("method"),
            "confidence": cls.get("confidence"),
            "reasoning": cls.get("reasoning", ""),
            "current": f.parent,
            "target": target_dir,
            "preserve_name": cls.get("suggested_filename") or f.name,
        })

    return {
        "files_scanned": len(files),
        "moves": moves,
        "skipped": skipped,
        "by_kind": dict(by_kind),
        "misplaced_by_kind": dict(misplaced_by_kind),
        "smart_calls": smart_used,
        "classifications": classifications,
    }


# ─────────────────────────────────────────────
# Apply moves
# ─────────────────────────────────────────────

def apply_moves(moves: list[dict]) -> list[dict]:
    """Execute the moves. Returns the list of completed actions."""
    completed = []
    for m in moves:
        f = m["file"]
        target_dir = m["target"]
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / m["preserve_name"]

        # Avoid clobber
        if dest.exists():
            stem = dest.stem
            ext = dest.suffix
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = target_dir / f"{stem}_{stamp}{ext}"

        try:
            shutil.move(str(f), str(dest))
            completed.append({
                "from": f,
                "to": dest,
                "kind": m["kind"],
            })
        except Exception as e:
            completed.append({
                "from": f,
                "to": None,
                "kind": m["kind"],
                "error": str(e)[:100],
            })

    return completed


# ─────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────

def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(VAULT))
    except ValueError:
        return str(p)


def text_report(s: dict) -> str:
    lines = []
    lines.append(f"\n{'='*60}")
    lines.append(f"📂 Vault Organizer — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    lines.append(f"   נסקרו: {s['files_scanned']} קבצים")
    lines.append(f"{'='*60}\n")

    if s["by_kind"]:
        lines.append("📊 חלוקה לפי סוג:")
        for k, n in sorted(s["by_kind"].items(), key=lambda x: -x[1]):
            mis = s["misplaced_by_kind"].get(k, 0)
            mark = f" ({mis} לא במקום)" if mis else ""
            lines.append(f"   • {k:<12} {n:>4}{mark}")
        lines.append("")

    if s["moves"]:
        lines.append(f"📦 קבצים להעברה: {len(s['moves'])}")
        for m in s["moves"][:15]:
            lines.append(f"   {m['kind']:<10} {_rel(m['file'])} → {_rel(m['target'])}/")
        if len(s["moves"]) > 15:
            lines.append(f"   ... ועוד {len(s['moves']) - 15}")
    else:
        lines.append("✅ הכל מסודר — אין קבצים שצריכים העברה.")

    return "\n".join(lines)


def md_report(s: dict, completed: list = None) -> Path:
    parts = [
        "---",
        "moki: true",
        "type: organizer_report",
        f"updated: {datetime.now().isoformat(timespec='seconds')}",
        "---",
        "",
        "# 📂 Vault Organizer — דוח סידור",
        "",
        f"_עודכן: {datetime.now().strftime('%d/%m/%Y %H:%M')}_",
        f"_נסקרו: **{s['files_scanned']}** קבצים._",
        "",
        "## 📊 חלוקה לפי סוג",
        "",
        "| סוג | סה\"כ | במקום הנכון | להעברה |",
        "|---|---|---|---|",
    ]
    for k, n in sorted(s["by_kind"].items(), key=lambda x: -x[1]):
        mis = s["misplaced_by_kind"].get(k, 0)
        ok = n - mis
        parts.append(f"| `{k}` | {n} | {ok} | {mis if mis else '✅'} |")

    parts.extend(["", "## 🎯 מבנה תיקיות יעד", "",
                  "| סוג | תיקייה |", "|---|---|"])
    for k, target in TARGET_FOLDERS.items():
        parts.append(f"| `{k}` | `{_rel(target)}/` |")

    if s["moves"]:
        parts.extend(["", "## 📦 העברות מומלצות", ""])
        if completed:
            parts.append("**הסטטוס:** הועברו בפועל.")
            parts.append("")
            parts.append("| קובץ | סוג | מ- | אל |")
            parts.append("|---|---|---|---|")
            for m in completed[:50]:
                if m.get("to"):
                    parts.append(f"| [{m['from'].name}]({_rel(m['to'])}) | "
                                 f"{m['kind']} | `{_rel(m['from'].parent)}` | "
                                 f"`{_rel(m['to'].parent)}` |")
        else:
            parts.append("_עוד לא הועברו. הרץ `--apply` כדי לבצע._")
            parts.append("")
            parts.append("| קובץ | סוג | יעד |")
            parts.append("|---|---|---|")
            for m in s["moves"][:50]:
                parts.append(f"| `{_rel(m['file'])}` | {m['kind']} | `{_rel(m['target'])}/` |")
    else:
        parts.extend(["", "## ✅ כל הקבצים מסודרים", "",
                      "אין קבצים במקום הלא נכון."])

    parts.extend([
        "",
        "---",
        "",
        "## 🛠 איך להפעיל",
        "",
        "```bash",
        "# דוח (לא מעביר):",
        "python3 obsidian_organizer.py",
        "",
        "# העברה בפועל:",
        "python3 obsidian_organizer.py --apply",
        "",
        "# דוח + שמירה ב-Obsidian:",
        "python3 obsidian_organizer.py --md",
        "```",
        "",
        "**אסטרטגיה:** מבוססת על `kind:` ב-frontmatter (אם קיים), ואז על שם הקובץ ותוכן הפתיחה.",
        "**אף פעם לא מוחק** — תמיד מעביר. אם יש קונפליקט שם — מוסיף timestamp.",
    ])

    out_path = VAULT / "_memory" / "organizer_report.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    apply = "--apply" in sys.argv
    smart = "--smart" in sys.argv
    write_md = "--md" in sys.argv or apply

    if smart:
        print("🧠 SMART mode — Claude מסווג כל קובץ (יקר אך מדויק)")
    else:
        print("⚡ FAST mode — rules עם Claude כ-fallback לקבצים מעורפלים")
        print("   (להפעיל סיווג מלא ע\"י Claude: --smart)")

    s = scan(smart_mode=smart)
    print(text_report(s))

    if s["smart_calls"]:
        cost_est = s["smart_calls"] * 0.05
        print(f"\n💰 Claude calls: {s['smart_calls']} (≈ ${cost_est:.2f})")

    completed = None
    if apply and s["moves"]:
        print(f"\n🔧 מעביר {len(s['moves'])} קבצים...")
        completed = apply_moves(s["moves"])
        ok_count = sum(1 for c in completed if c.get("to"))
        err_count = sum(1 for c in completed if c.get("error"))
        print(f"   ✅ הועברו: {ok_count}")
        if err_count:
            print(f"   ⚠️ נכשלו: {err_count}")

    if write_md:
        md_path = md_report(s, completed)
        print(f"\n📝 דוח נשמר → {_rel(md_path)}")

    if not apply and s["moves"]:
        print(f"\n💡 הרץ עם --apply כדי להעביר {len(s['moves'])} קבצים בפועל")


if __name__ == "__main__":
    main()
