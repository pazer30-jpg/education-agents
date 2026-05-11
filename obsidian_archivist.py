"""
obsidian_archivist.py — ארכיביסט ל-Obsidian vault.

מזהה ומטפל בבעיות נפוצות:
  1. קבצים יתומים (אף wikilink לא מקשר אליהם)
  2. Wikilinks שבורים (קישורים ל-MD שלא קיים)
  3. קבצים ריקים / 0-byte
  4. כפילויות (אותו תוכן בשני קבצים)
  5. קבצים ישנים שלא שונו 60+ יום

Usage:
  python3 obsidian_archivist.py             # דוח בלבד
  python3 obsidian_archivist.py --apply     # מעביר ל-_archive בפועל
  python3 obsidian_archivist.py --md        # כותב דוח לאובסידיאן
"""

import re
import sys
import shutil
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

from config import OUTPUT_DIR


VAULT = OUTPUT_DIR
ARCHIVE_DIR = VAULT / "_archive_orphans"
EXCLUDED_DIRS = {".obsidian", ".trash", "_archive_orphans", "checkpoints",
                 "_state", "_snapshots", "_scratchpad.json"}
STALE_DAYS = 60


# ─────────────────────────────────────────────
# Scan vault
# ─────────────────────────────────────────────

def _all_md_files() -> list[Path]:
    files = []
    for p in VAULT.rglob("*.md"):
        try:
            rel = p.relative_to(VAULT)
        except ValueError:
            continue
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        files.append(p)
    return sorted(files)


def _extract_wikilinks(text: str) -> set[str]:
    """Extract wikilink targets — both [[X]] and [[X|alias]]."""
    pattern = re.compile(r"\[\[([^\]\|#]+?)(?:\|[^\]]+)?(?:#[^\]]*)?\]\]")
    return {m.group(1).strip() for m in pattern.finditer(text)}


def _build_link_graph(files: list[Path]) -> tuple[dict, dict]:
    """
    Returns (outgoing, incoming):
      outgoing[file_stem] = {targets it links to}
      incoming[file_stem] = {files that link to it}
    """
    outgoing = {}
    incoming = defaultdict(set)
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""
        targets = _extract_wikilinks(text)
        outgoing[f.stem] = targets
        for t in targets:
            incoming[t].add(f.stem)
    return outgoing, dict(incoming)


# ─────────────────────────────────────────────
# Issue detectors
# ─────────────────────────────────────────────

def _is_index_file(f: Path) -> bool:
    """Files starting with _ or named like home — never count as orphans."""
    if f.stem.startswith("_"):
        return True
    if any(kw in f.stem.lower() for kw in ("home", "index", "מוקי-בית")):
        return True
    return False


def find_orphans(files: list[Path], incoming: dict) -> list[Path]:
    """Files that no other file links to (excluding index files)."""
    orphans = []
    for f in files:
        if _is_index_file(f):
            continue
        if not incoming.get(f.stem):
            orphans.append(f)
    return orphans


def find_broken_links(files: list[Path], outgoing: dict) -> list[dict]:
    """Wikilinks pointing to files that don't exist."""
    existing_stems = {f.stem for f in files}
    broken = []
    for f in files:
        for target in outgoing.get(f.stem, set()):
            if target not in existing_stems:
                broken.append({"in_file": f, "broken_link": target})
    return broken


def find_empty_files(files: list[Path], min_bytes: int = 50) -> list[Path]:
    """Zero-byte or near-empty MD files."""
    return [f for f in files
            if f.stat().st_size < min_bytes
            and not _is_index_file(f)]


def find_duplicates(files: list[Path]) -> list[tuple[Path, Path]]:
    """Files with identical content (by sha256 hash)."""
    by_hash: dict[str, list[Path]] = defaultdict(list)
    for f in files:
        try:
            content = f.read_bytes()
            if len(content) < 100:
                continue
            h = hashlib.sha256(content).hexdigest()
            by_hash[h].append(f)
        except Exception:
            pass
    pairs = []
    for hsh, group in by_hash.items():
        if len(group) > 1:
            for f in group[1:]:
                pairs.append((group[0], f))
    return pairs


def find_stale(files: list[Path], days: int = STALE_DAYS) -> list[Path]:
    """Files not modified in N+ days."""
    cutoff = datetime.now() - timedelta(days=days)
    cutoff_ts = cutoff.timestamp()
    return [f for f in files
            if f.stat().st_mtime < cutoff_ts
            and not _is_index_file(f)]


# ─────────────────────────────────────────────
# Apply moves
# ─────────────────────────────────────────────

def _archive_file(f: Path, reason: str) -> Path:
    """Move file to _archive_orphans/<reason>/<original_subpath>."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        rel = f.relative_to(VAULT)
    except ValueError:
        rel = f.name
    dest = ARCHIVE_DIR / reason / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        # Avoid clobber — append timestamp
        dest = dest.with_stem(f"{dest.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.move(str(f), str(dest))
    return dest


# ─────────────────────────────────────────────
# Reports
# ─────────────────────────────────────────────

def _format_path(p: Path) -> str:
    try:
        return str(p.relative_to(VAULT))
    except ValueError:
        return str(p)


def text_report(scan: dict) -> str:
    """Human-readable summary of scan results."""
    lines = []
    lines.append(f"\n{'='*60}")
    lines.append(f"📂 Vault Archivist — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    lines.append(f"   Scanned: {scan['total_files']} MD files")
    lines.append(f"{'='*60}\n")

    counts = [
        ("🗑  קבצים ריקים (<50B)", scan["empty"]),
        ("👻 קבצים יתומים (אף קישור)", scan["orphans"]),
        ("🔗 wikilinks שבורים", scan["broken"]),
        ("👯 כפילויות", scan["duplicates"]),
        (f"📦 ישנים (>{STALE_DAYS} ימים, ללא שינוי)", scan["stale"]),
    ]
    for label, items in counts:
        lines.append(f"  {label}: {len(items)}")
    lines.append("")

    # Show samples
    if scan["orphans"]:
        lines.append("👻 דוגמאות יתומים (5 ראשונים):")
        for f in scan["orphans"][:5]:
            lines.append(f"   • {_format_path(f)}")
        lines.append("")

    if scan["broken"]:
        lines.append("🔗 wikilinks שבורים (5 ראשונים):")
        for b in scan["broken"][:5]:
            lines.append(f"   • [[{b['broken_link']}]] ב-{_format_path(b['in_file'])}")
        lines.append("")

    if scan["duplicates"]:
        lines.append("👯 כפילויות (5 ראשונות):")
        for orig, dup in scan["duplicates"][:5]:
            lines.append(f"   • {_format_path(dup)} (כמו {_format_path(orig)})")
        lines.append("")

    return "\n".join(lines)


def md_report(scan: dict) -> Path:
    """Write markdown report to output/_memory/archivist_report.md."""
    parts = [
        "---",
        "moki: true",
        "type: archivist_report",
        f"updated: {datetime.now().isoformat(timespec='seconds')}",
        "---",
        "",
        "# 📂 Vault Archivist — דוח ניקיון",
        "",
        f"_עודכן: {datetime.now().strftime('%d/%m/%Y %H:%M')}_",
        f"_נסקרו: **{scan['total_files']}** קבצי MD ב-vault_",
        "",
        "## 📊 סיכום",
        "",
        "| בעיה | כמות |",
        "|---|---|",
        f"| 🗑  קבצים ריקים (<50B) | {len(scan['empty'])} |",
        f"| 👻 קבצים יתומים | {len(scan['orphans'])} |",
        f"| 🔗 wikilinks שבורים | {len(scan['broken'])} |",
        f"| 👯 כפילויות | {len(scan['duplicates'])} |",
        f"| 📦 ישנים (>{STALE_DAYS} יום) | {len(scan['stale'])} |",
        "",
    ]

    if scan["orphans"]:
        parts.extend(["## 👻 קבצים יתומים", "",
                     "_אף קובץ אחר לא מקשר אליהם. כדאי לבדוק אם הם רלוונטיים._",
                     ""])
        for f in scan["orphans"][:30]:
            parts.append(f"- [{f.stem}]({_format_path(f)})")
        if len(scan["orphans"]) > 30:
            parts.append(f"- _... ועוד {len(scan['orphans']) - 30} קבצים_")
        parts.append("")

    if scan["broken"]:
        parts.extend(["## 🔗 Wikilinks שבורים", "",
                     "_קישורים שמובילים לקובץ שלא קיים ב-vault._", ""])
        for b in scan["broken"][:30]:
            parts.append(f"- `[[{b['broken_link']}]]` ב-[{b['in_file'].stem}]({_format_path(b['in_file'])})")
        parts.append("")

    if scan["duplicates"]:
        parts.extend(["## 👯 קבצים כפולים", "",
                     "_תוכן זהה בייט-אחר-בייט. שמור אחד, מחק את השני._", ""])
        for orig, dup in scan["duplicates"][:20]:
            parts.append(f"- [{dup.stem}]({_format_path(dup)}) ↔ [{orig.stem}]({_format_path(orig)})")
        parts.append("")

    if scan["empty"]:
        parts.extend(["## 🗑 קבצים ריקים", ""])
        for f in scan["empty"][:20]:
            parts.append(f"- [{f.stem}]({_format_path(f)}) ({f.stat().st_size}B)")
        parts.append("")

    if scan["stale"]:
        parts.extend([f"## 📦 קבצים ישנים (>{STALE_DAYS} יום)", "",
                     "_לא שונו זמן רב. כדאי לבדוק אם רלוונטיים או להעביר ל-_archive_orphans/._",
                     ""])
        for f in scan["stale"][:20]:
            age_days = (datetime.now() - datetime.fromtimestamp(f.stat().st_mtime)).days
            parts.append(f"- [{f.stem}]({_format_path(f)}) — {age_days} יום")
        if len(scan["stale"]) > 20:
            parts.append(f"- _... ועוד {len(scan['stale']) - 20} קבצים_")
        parts.append("")

    parts.extend([
        "---",
        "",
        "## 🛠 פעולה",
        "",
        "```bash",
        "# הרצת חוזרת (דוח בלבד)",
        "python3 obsidian_archivist.py",
        "",
        "# יישום אוטומטי — מעביר ל-_archive_orphans/",
        "python3 obsidian_archivist.py --apply",
        "```",
        "",
        "**מה שמועבר ב---apply:** קבצים ריקים + יתומים שלא שונו 30+ יום + כפילויות.",
        "",
        "**אף פעם לא נמחקים** — עוברים ל-`output/_archive_orphans/`. תמיד אפשר לשחזר.",
    ])

    out_path = OUTPUT_DIR / "_memory" / "archivist_report.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path


# ─────────────────────────────────────────────
# Main scan
# ─────────────────────────────────────────────

def scan_vault() -> dict:
    """Run all detectors. Returns dict of issues."""
    files = _all_md_files()
    outgoing, incoming = _build_link_graph(files)
    return {
        "total_files": len(files),
        "files": files,
        "orphans": find_orphans(files, incoming),
        "broken": find_broken_links(files, outgoing),
        "empty": find_empty_files(files),
        "duplicates": find_duplicates(files),
        "stale": find_stale(files),
    }


def apply_archive(scan: dict, conservative: bool = True) -> dict:
    """
    Move problem files to _archive_orphans/.
    Conservative mode: only files that are BOTH orphan AND stale, plus duplicates.
    """
    moved = {"empty": [], "orphan_stale": [], "duplicates": []}

    cutoff = datetime.now() - timedelta(days=30)
    cutoff_ts = cutoff.timestamp()
    orphan_set = set(scan["orphans"])

    # Empty files always moved
    for f in scan["empty"]:
        try:
            new_path = _archive_file(f, "empty")
            moved["empty"].append((f, new_path))
        except Exception:
            pass

    # Orphan + stale (>30d) only if conservative
    for f in scan["orphans"]:
        if not f.exists():
            continue
        if f.stat().st_mtime < cutoff_ts:
            try:
                new_path = _archive_file(f, "orphan_stale")
                moved["orphan_stale"].append((f, new_path))
            except Exception:
                pass

    # Duplicates: archive the duplicate (not the original)
    for orig, dup in scan["duplicates"]:
        if not dup.exists() or dup in orphan_set:
            # Already moved — skip
            continue
        try:
            new_path = _archive_file(dup, "duplicate")
            moved["duplicates"].append((dup, new_path))
        except Exception:
            pass

    return moved


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    apply = "--apply" in sys.argv
    write_md = "--md" in sys.argv or apply  # always write MD on apply

    scan = scan_vault()
    print(text_report(scan))

    if write_md:
        md_path = md_report(scan)
        print(f"📝 Report saved → {md_path.relative_to(VAULT.parent)}")

    if apply:
        print("\n🔧 Applying — מעביר ל-_archive_orphans/...")
        moved = apply_archive(scan)
        total = sum(len(v) for v in moved.values())
        print(f"   • Empty:        {len(moved['empty'])}")
        print(f"   • Orphan+stale: {len(moved['orphan_stale'])}")
        print(f"   • Duplicates:   {len(moved['duplicates'])}")
        print(f"   ✅ {total} files moved to _archive_orphans/")
    else:
        total_issues = (len(scan["orphans"]) + len(scan["broken"]) +
                        len(scan["empty"]) + len(scan["duplicates"]) +
                        len(scan["stale"]))
        if total_issues > 0:
            print(f"\n💡 Run with --apply to move files to _archive_orphans/")


if __name__ == "__main__":
    main()
