"""
obsidian_memory.py — Moki's working memory, stored as Markdown in Obsidian.

Idea: ~7 memory notes in output/_memory/ that are:
  - read by agents BEFORE generating (loaded into prompts)
  - written by agents AFTER reflection (with structured updates)
  - editable by Paz directly in Obsidian — no deploy, no code change

Each note has frontmatter:
  ---
  moki: true
  type: voice_rules | strong_topics | recurring_sources | ...
  updated: ISO8601
  ---

Usage:
  from obsidian_memory import load_memory_note, format_for_prompt

  rules = load_memory_note("voice_rules")          # raw markdown
  ctx = format_for_prompt(["voice_rules",          # injectable string
                            "recurring_sources"])
"""

import re
from pathlib import Path
from datetime import datetime
from config import OUTPUT_DIR


MEMORY_DIR = OUTPUT_DIR / "_memory"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
# Frontmatter parsing
# ─────────────────────────────────────────────

_FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def _parse(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body). Returns ({}, text) on any parse failure."""
    if not text:
        return {}, ""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    fm_text, body = m.group(1), m.group(2)
    fm = {}
    try:
        for line in fm_text.splitlines():
            if ":" not in line:
                continue
            parts = line.split(":", 1)
            if len(parts) != 2:
                continue
            k, v = parts
            k = k.strip()
            if k:
                fm[k] = v.strip()
    except Exception:
        # malformed YAML — fall back to body-only
        return {}, text
    return fm, body


def _serialize(fm: dict, body: str) -> str:
    fm_lines = [f"{k}: {v}" for k, v in fm.items()]
    return "---\n" + "\n".join(fm_lines) + "\n---\n" + body


# ─────────────────────────────────────────────
# Read / Write
# ─────────────────────────────────────────────

def _path(name: str) -> Path:
    name = name.replace(".md", "")
    return MEMORY_DIR / f"{name}.md"


def load_memory_note(name: str, body_only: bool = True) -> str:
    """Return markdown content. If body_only, strip frontmatter."""
    p = _path(name)
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8")
    if body_only:
        _, body = _parse(text)
        return body.strip()
    return text


def save_memory_note(name: str, body: str, note_type: str = None,
                     extra_fm: dict = None) -> Path:
    """Save with frontmatter. Preserves any existing extra fields."""
    p = _path(name)
    fm = {}
    if p.exists():
        existing_fm, _ = _parse(p.read_text(encoding="utf-8"))
        fm.update(existing_fm)
    fm["moki"] = "true"
    fm["type"] = note_type or fm.get("type") or name
    fm["updated"] = datetime.now().isoformat(timespec="seconds")
    if extra_fm:
        fm.update({k: str(v) for k, v in extra_fm.items()})
    p.write_text(_serialize(fm, body.strip() + "\n"), encoding="utf-8")
    return p


def list_memory_notes() -> list[str]:
    return sorted(p.stem for p in MEMORY_DIR.glob("*.md")
                  if not p.stem.startswith("_"))


def all_memory_notes(body_only: bool = True) -> dict[str, str]:
    return {n: load_memory_note(n, body_only=body_only)
            for n in list_memory_notes()}


# ─────────────────────────────────────────────
# Prompt injection
# ─────────────────────────────────────────────

_NOTE_LABELS = {
    "voice_rules":          "🎙 כללי קול",
    "strong_topics":        "🌟 נושאים שעבדו",
    "weak_topics":          "❌ נושאים שלא עבדו",
    "recurring_sources":    "📚 הוגים שאתה מצטט",
    "editor_corrections":   "✂️ תיקונים שלמדנו מעריכות",
    "theoretical_anchors":  "🧠 עוגנים תיאורטיים",
    "academic_writing_apa7": "🎓 כללי כתיבה אקדמית APA 7",
}


def format_for_prompt(note_names: list[str], max_chars_per_note: int = 1500) -> str:
    """Build a context block for system prompts. Skips empty/missing notes."""
    blocks = []
    for name in note_names:
        body = load_memory_note(name)
        if not body:
            continue
        label = _NOTE_LABELS.get(name, name)
        truncated = body[:max_chars_per_note]
        if len(body) > max_chars_per_note:
            truncated += "\n... (truncated)"
        blocks.append(f"━━━ {label} ({name}.md) ━━━\n{truncated}")
    if not blocks:
        return ""
    return ("ZIKARON FROM OBSIDIAN — read carefully, these reflect the user's preferences:\n\n"
            + "\n\n".join(blocks))


# ─────────────────────────────────────────────
# Append helpers (for reflective updates)
# ─────────────────────────────────────────────

def append_to_note(name: str, addition: str, section: str = None):
    """Append a bullet under a section (or end of file).

    If section is given but not found, creates it. Always returns the new body
    so callers can verify insertion (no silent loss).
    """
    body = load_memory_note(name) or f"# {name}\n"
    if not addition:
        return body
    if section:
        marker = f"## {section}"
        if marker in body:
            lines = body.split("\n")
            out = []
            inserted = False
            for line in lines:
                out.append(line)
                if not inserted and line.strip() == marker:
                    out.append("")
                    out.append(addition)
                    inserted = True
            if not inserted:
                # marker found in body but not on its own line — append section at end
                out.append(f"\n## {section}\n\n{addition}")
            body = "\n".join(out)
        else:
            body += f"\n\n## {section}\n\n{addition}\n"
    else:
        body += f"\n\n{addition}\n"
    save_memory_note(name, body)
    return body


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def get_backstory(agent_name: str) -> str:
    """
    Extract a single agent's backstory section from agent_backstories.md.
    Returns the markdown body of `## {agent_name}` section, or "" if not found.

    Used by each agent to prepend its persona to the system prompt without
    leaking the OTHER agents' backstories into context.
    """
    body = load_memory_note("agent_backstories")
    if not body:
        return ""
    marker = f"## {agent_name}"
    if marker not in body:
        return ""
    section = body.split(marker, 1)[1]
    # Stop at next "## " header or end of file
    next_header = section.find("\n## ")
    if next_header > 0:
        section = section[:next_header]
    return section.strip()


def _cli():
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 obsidian_memory.py [list|read NAME|format NAME1,NAME2,...]")
        return
    cmd = sys.argv[1]
    if cmd == "list":
        for n in list_memory_notes():
            body = load_memory_note(n)
            words = len(body.split())
            print(f"  {n:<25} {words} words")
    elif cmd == "read" and len(sys.argv) >= 3:
        print(load_memory_note(sys.argv[2]))
    elif cmd == "format" and len(sys.argv) >= 3:
        names = sys.argv[2].split(",")
        print(format_for_prompt(names))
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    _cli()
