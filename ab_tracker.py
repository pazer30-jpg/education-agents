"""
ab_tracker.py — A/B variant winner detection.

Agent 3 generates two LinkedIn variants per post (the main file + a _B_
file with a different opening style). Until now the winner was never
tracked — the data was thrown away. This module pairs A/B variants,
compares their real engagement (from publish_queue), declares a winner,
and writes the winning STYLE back to memory so Agent 3 learns which
opening style actually performs.

Deterministic — no Claude calls.

Sources:
  output/posts/linkedin/*.txt           — A (main) + B (_B_) variants
  output/_state/publish_queue.json      — engagement per file (likes/comments)

Output:
  output/_memory/ab_winners.md          — running tally of A-vs-B outcomes
                                          + which opening styles win

CLI:
  python3 ab_tracker.py            # analyze + write memory
  python3 ab_tracker.py --print    # stdout only
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR

LINKEDIN_DIR = OUTPUT_DIR / "posts" / "linkedin"
PUB_QUEUE    = OUTPUT_DIR / "_state" / "publish_queue.json"
OUT_MEMORY   = OUTPUT_DIR / "_memory" / "ab_winners.md"


def _load_queue() -> dict:
    if not PUB_QUEUE.exists():
        return {}
    try:
        return json.loads(PUB_QUEUE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _engagement_for(file_path: str, queue: dict) -> dict | None:
    """Find engagement for a given file path in publish_queue."""
    for entry in queue.values():
        if entry.get("file") == file_path and entry.get("engagement"):
            return entry["engagement"]
    return None


def _eng_score(e: dict) -> int:
    return e.get("likes", 0) + 2 * e.get("comments", 0) + 3 * e.get("shares", 0)


def _opening_style(text: str) -> str:
    """Classify the opening line into a style bucket (matches Agent 3's 5 styles)."""
    first = next((l.strip() for l in text.split("\n") if l.strip()), "")
    if re.match(r"^[א-ת]+\s+\d{4}|^\d", first):
        return "עיגון-זמני/מספרי"
    if first.endswith("?"):
        return "שאלה"
    if first.startswith('"') or '"' in first[:40] or "'" in first[:40]:
        return "ציטוט/דיאלוג"
    if any(w in first[:40] for w in ("טעיתי", "חשבתי ש", "הייתי בטוח", "אני זוכר")):
        return "הודאה-אישית"
    if re.search(r"[א-ת]+,\s+(כיתה|בן|בת|גיל)", first):
        return "דמות+פעולה"
    return "אחר"


def _pair_variants() -> list[dict]:
    """Pair each main LinkedIn file with its _B_ sibling by shared base slug."""
    if not LINKEDIN_DIR.exists():
        return []
    files = [p for p in LINKEDIN_DIR.glob("*.txt") if not p.name.endswith(".bak")]
    # Group by base (everything before _linkedin)
    by_base: dict[str, dict] = {}
    for p in files:
        name = p.name
        is_b = "_linkedin_B_" in name
        base = name.split("_linkedin")[0]
        slot = by_base.setdefault(base, {"a": None, "b": None})
        if is_b:
            slot["b"] = p
        elif "_ready" in name or "_linkedin_2" not in name:
            # prefer the ready/main file as A
            if slot["a"] is None or "_ready" in name:
                slot["a"] = p
    return [{"base": b, **v} for b, v in by_base.items() if v["a"] and v["b"]]


def analyze() -> dict:
    queue = _load_queue()
    pairs = _pair_variants()
    results = []
    style_wins: dict[str, int] = {}
    style_plays: dict[str, int] = {}

    for pr in pairs:
        a, b = pr["a"], pr["b"]
        ea = _engagement_for(str(a), queue)
        eb = _engagement_for(str(b), queue)
        if not ea or not eb:
            continue  # need engagement on BOTH to compare
        sa, sb = _eng_score(ea), _eng_score(eb)
        a_text = a.read_text(encoding="utf-8", errors="replace")
        b_text = b.read_text(encoding="utf-8", errors="replace")
        a_style = _opening_style(a_text)
        b_style = _opening_style(b_text)
        winner = "A" if sa >= sb else "B"
        win_style = a_style if winner == "A" else b_style
        for st in (a_style, b_style):
            style_plays[st] = style_plays.get(st, 0) + 1
        style_wins[win_style] = style_wins.get(win_style, 0) + 1
        results.append({
            "base": pr["base"][:40], "winner": winner,
            "a_score": sa, "b_score": sb,
            "a_style": a_style, "b_style": b_style, "win_style": win_style,
        })

    return {"results": results, "style_wins": style_wins, "style_plays": style_plays}


def build_memory(data: dict) -> str:
    res = data["results"]
    lines = [
        "---", "moki: true", "type: ab_winners",
        f"updated: {datetime.now().isoformat(timespec='seconds')}",
        "---", "",
        "# 🅰️🅱️ A/B — מה ניצח",
        "",
    ]
    if not res:
        lines.append("_אין עדיין זוגות A/B עם engagement על שתי הגרסאות._")
        lines.append("")
        lines.append("> ברגע שתפרסם שתי גרסאות ותזין engagement (CSV או ידני),")
        lines.append("> מוקי ילמד אילו סגנונות פתיחה באמת עובדים.")
    else:
        # Win-rate per opening style
        lines.append("## סגנונות פתיחה — אחוז ניצחון")
        lines.append("")
        lines.append("| סגנון | ניצחונות | הופעות | win-rate |")
        lines.append("|---|---|---|---|")
        for st in sorted(data["style_plays"], key=lambda s: -data["style_wins"].get(s, 0)):
            w = data["style_wins"].get(st, 0)
            p = data["style_plays"][st]
            rate = f"{w/p*100:.0f}%" if p else "—"
            lines.append(f"| {st} | {w} | {p} | {rate} |")
        lines.append("")
        lines.append(f"## {len(res)} השוואות אחרונות")
        lines.append("")
        for r in res[-10:]:
            mark = "🅰️" if r["winner"] == "A" else "🅱️"
            lines.append(f"- {mark} ניצח ({r['a_score']} vs {r['b_score']}) · "
                         f"סגנון מנצח: **{r['win_style']}** · `{r['base']}`")
        lines.append("")
        lines.append("**הנחיה ל-Agent 3:** העדף סגנונות פתיחה עם win-rate גבוה.")
    OUT_MEMORY.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(lines)
    OUT_MEMORY.write_text(body, encoding="utf-8")
    return body


def main():
    ap = argparse.ArgumentParser(description="A/B variant winner detection")
    ap.add_argument("--print", action="store_true", help="stdout only")
    args = ap.parse_args()
    data = analyze()
    if args.print:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    build_memory(data)
    n = len(data["results"])
    print(f"🅰️🅱️ {OUT_MEMORY.relative_to(OUTPUT_DIR.parent)} "
          f"({n} A/B comparisons with engagement)")


if __name__ == "__main__":
    main()
