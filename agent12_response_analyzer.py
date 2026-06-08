"""
agent12_response_analyzer.py — Agent 12: Response Analyzer.

After Agent 11 generated a survey (items.csv) and you collected responses
from Google Forms / Typeform / whatever, this analyzes them and produces
a findings.md ready to feed back into the Writer.

Inputs:
  - output/surveys/<slug>/items.csv            (from Agent 11)
  - <responses.csv>                              (export from Forms)

Outputs:
  - output/surveys/<slug>/responses.csv         (cleaned, joined with items)
  - output/surveys/<slug>/findings.md           (statistical + qualitative report)
  - output/surveys/<slug>/charts/*.svg          (distributions, group comparisons)

Reads from memory:
  - survey_methodology.md  (test selection rubric)
  - agent_backstories.md   (persona: פרופ' שמואל אדלר)

Statistics covered:
  - Descriptive: M, SD, range, distribution
  - Reliability: Cronbach's α per framework_anchor
  - Inferential: t-test, Mann-Whitney, Pearson, Spearman, chi-square
  - Qualitative: open-ended → Claude codes themes

Usage:
  python3 agent12_response_analyzer.py \\
      --slug loneliness-in-boarding-school-principals \\
      --responses ~/Downloads/responses.csv

  python3 agent12_response_analyzer.py \\
      --slug ... --responses ... --rq "RQ1: ..." --rq "RQ2: ..."
"""

import argparse
import csv
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR

SURVEYS_DIR = OUTPUT_DIR / "surveys"

# Each open-ended coding call costs ~$0.30 — cap at 3 = $0.90 max
CODING_BUDGET = 0.30
CODING_TIMEOUT = 120
MAX_OPEN_CODING_CALLS = 3


# ─────────────────────────────────────────────
# I/O
# ─────────────────────────────────────────────

def _read_items_csv(path: Path) -> list[dict]:
    """Read the items.csv produced by Agent 11."""
    items = []
    with path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            items.append({
                "section":          row.get("section", ""),
                "order":            int(row.get("order") or 0),
                "text_he":          row.get("text_he", ""),
                "text_en":          row.get("text_en", ""),
                "type":             row.get("type", ""),
                "scale_min":        int(row["scale_min"]) if row.get("scale_min") else None,
                "scale_max":        int(row["scale_max"]) if row.get("scale_max") else None,
                "reverse_coded":    (row.get("reverse_coded", "") or "").lower() == "true",
                "framework_anchor": row.get("framework_anchor", ""),
            })
    return items


def _read_responses_csv(path: Path) -> tuple[list[str], list[dict]]:
    """Read responses; return (headers, rows-as-dicts)."""
    with path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = list(reader)
    return headers, rows


def _match_columns_to_items(headers: list[str], items: list[dict]) -> dict[str, dict]:
    """Heuristic: match each response-CSV column to an items.csv entry by
    fuzzy text match on text_he (preferred) or text_en."""
    out = {}
    norm = lambda s: re.sub(r"\s+", " ", (s or "").strip().lower())[:100]
    item_index = []
    for it in items:
        for key in ("text_he", "text_en"):
            t = norm(it.get(key, ""))
            if t:
                item_index.append((t, it))
    for h in headers:
        nh = norm(h)
        if not nh:
            continue
        best = None
        for t, it in item_index:
            if not t:
                continue
            # Match if header CONTAINS the item text or vice versa
            if t in nh or nh in t:
                best = it
                break
        if best:
            out[h] = best
    return out


# ─────────────────────────────────────────────
# Stats helpers (no scipy required — keep deps light)
# ─────────────────────────────────────────────

def _parse_likert(value: str, item: dict) -> int | None:
    """Convert a Forms cell to an int on the Likert scale. Handles label
    text like '4 - מסכים' or just '4'. Reverse-codes if needed."""
    if not value:
        return None
    s = str(value).strip()
    m = re.match(r"\s*(\d+)", s)
    if not m:
        return None
    try:
        n = int(m.group(1))
    except Exception:
        return None
    if item.get("scale_max") and item.get("reverse_coded"):
        return item["scale_max"] + (item["scale_min"] or 1) - n
    return n


def _descriptive(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "sd": None, "min": None, "max": None,
                "median": None}
    return {
        "n":      len(values),
        "mean":   round(statistics.mean(values), 2),
        "sd":     round(statistics.stdev(values), 2) if len(values) > 1 else 0.0,
        "min":    min(values),
        "max":    max(values),
        "median": statistics.median(values),
    }


def _pearson(xs: list[float], ys: list[float]) -> tuple[float, float] | tuple[None, None]:
    """Pearson r + two-tailed p (approximated via t-distribution). Returns
    (r, p) or (None, None) if not enough data."""
    if len(xs) != len(ys) or len(xs) < 4:
        return None, None
    mx = statistics.mean(xs); my = statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx2 = sum((x - mx) ** 2 for x in xs)
    sy2 = sum((y - my) ** 2 for y in ys)
    if sx2 == 0 or sy2 == 0:
        return None, None
    r = num / math.sqrt(sx2 * sy2)
    # t = r * sqrt((n-2) / (1-r^2)) ; p via normal approx for n>20
    n = len(xs)
    if r >= 0.9999:
        return round(r, 3), 0.0
    t = r * math.sqrt((n - 2) / max(1e-9, 1 - r * r))
    # Crude two-tailed p using normal approximation (n>20: t ≈ z)
    p = 2 * (1 - _phi(abs(t)))
    return round(r, 3), round(p, 4)


def _phi(x: float) -> float:
    """Standard-normal CDF approximation (Abramowitz & Stegun 26.2.17)."""
    a1, a2, a3 = 0.254829592, -0.284496736, 1.421413741
    a4, a5     = -1.453152027, 1.061405429
    p_ = 0.3275911
    sign = 1 if x >= 0 else -1
    x = abs(x) / math.sqrt(2)
    t = 1.0 / (1.0 + p_ * x)
    y = 1.0 - (((((a5*t + a4)*t) + a3)*t + a2)*t + a1) * t * math.exp(-x * x)
    return 0.5 * (1 + sign * y)


def _cronbach_alpha(item_responses: list[list[float]]) -> float | None:
    """Compute α from a list of [resp1_item1, resp2_item1, ...] per item.
    Returns None if fewer than 3 items or 5 respondents."""
    if len(item_responses) < 3:
        return None
    n_resp = min(len(col) for col in item_responses)
    if n_resp < 5:
        return None
    # Trim to common length
    cols = [col[:n_resp] for col in item_responses]
    k = len(cols)
    # Per-item variance
    item_vars = [statistics.variance(c) for c in cols]
    # Total variance (sum of person totals)
    totals = [sum(cols[i][r] for i in range(k)) for r in range(n_resp)]
    total_var = statistics.variance(totals)
    if total_var == 0:
        return None
    alpha = (k / (k - 1)) * (1 - sum(item_vars) / total_var)
    return round(alpha, 3)


# ─────────────────────────────────────────────
# Qualitative coding (Claude)
# ─────────────────────────────────────────────

def _code_open_responses(question: str, responses: list[str],
                         max_calls: int = MAX_OPEN_CODING_CALLS) -> dict:
    """Send open-ended responses to Claude; return {themes, illustrative_quotes}."""
    if not responses:
        return {"themes": [], "quotes": []}
    try:
        from claude_cli import ask_claude_json
    except Exception:
        return {"themes": [], "quotes": [], "error": "claude_cli unavailable"}
    cleaned = [r.strip() for r in responses if r and r.strip() and len(r.strip()) > 8]
    if len(cleaned) < 3:
        return {"themes": [], "quotes": cleaned, "skipped": "too few responses"}
    blob = "\n\n".join(f"- {r[:400]}" for r in cleaned[:60])
    prompt = (
        f"שאלה: {question}\n\n"
        f"להלן {len(cleaned)} תגובות פתוחות. עבד עליהן בקידוד תמטי איכותי:\n\n"
        f"{blob}\n\n"
        "החזר JSON:\n"
        '{\n'
        '  "themes": [\n'
        '    {"name": "<שם תמה>", "count": <כמה תגובות נוגעות בה>, "description": "<משפט>"}\n'
        '  ],\n'
        '  "illustrative_quotes": ["<ציטוט מעניין 1>", "<ציטוט 2>", "<ציטוט 3>"]\n'
        "}\n"
        "כללים:\n"
        "- 3-6 תמות מקסימום\n"
        "- ציטוטים מילה במילה מהתגובות (לא לשכתב)\n"
        "- כשמשהו לא ברור — תאר כ-'לא היה מספיק מידע ל-X'"
    )
    try:
        result = ask_claude_json(prompt, max_budget=CODING_BUDGET,
                                 timeout=CODING_TIMEOUT, max_retries=1)
    except Exception as e:
        return {"themes": [], "quotes": cleaned[:3], "error": str(e)[:120]}
    return {
        "themes": result.get("themes", []) if isinstance(result, dict) else [],
        "quotes": result.get("illustrative_quotes", []) if isinstance(result, dict) else [],
    }


# ─────────────────────────────────────────────
# Main analyze
# ─────────────────────────────────────────────

def analyze(slug: str, responses_csv: Path, rqs: list[str]) -> dict:
    """End-to-end analysis. Writes findings.md + cleaned responses."""
    survey_dir = SURVEYS_DIR / slug
    items_csv = survey_dir / "items.csv"
    if not items_csv.exists():
        return {"error": f"items.csv not found at {items_csv}"}

    items = _read_items_csv(items_csv)
    headers, rows = _read_responses_csv(responses_csv)
    if not rows:
        return {"error": "no responses in CSV"}

    col_to_item = _match_columns_to_items(headers, items)
    matched = len(col_to_item)
    print(f"  [Agent12] matched {matched}/{len(headers)} response columns to items")

    # ── Aggregate per-item numeric values + per-framework totals ──
    per_item_values: dict[str, list[int]] = defaultdict(list)
    per_item_meta: dict[str, dict] = {}
    per_open: dict[str, list[str]] = defaultdict(list)

    for header, item in col_to_item.items():
        key = item.get("text_he") or item.get("text_en") or header
        per_item_meta[key] = item
        for row in rows:
            v = row.get(header, "")
            if item["type"] in ("likert5", "likert7", "frequency"):
                n = _parse_likert(v, item)
                if n is not None:
                    per_item_values[key].append(n)
            elif item["type"] == "open":
                if v:
                    per_open[key].append(v)

    # ── Descriptive per item ──
    descriptives = {}
    for key, vals in per_item_values.items():
        descriptives[key] = {
            "framework":   per_item_meta[key].get("framework_anchor", ""),
            "type":        per_item_meta[key].get("type", ""),
            "stats":       _descriptive([float(v) for v in vals]),
            "n_responded": len(vals),
        }

    # ── Cronbach's α per framework with ≥3 items ──
    by_framework: dict[str, list[list[float]]] = defaultdict(list)
    for key, vals in per_item_values.items():
        fw = per_item_meta[key].get("framework_anchor", "")
        if fw and fw not in ("demographic", "qualitative") and len(vals) >= 5:
            by_framework[fw].append([float(v) for v in vals])
    alphas = {}
    for fw, cols in by_framework.items():
        if len(cols) >= 3:
            a = _cronbach_alpha(cols)
            if a is not None:
                alphas[fw] = a

    # ── Inter-framework correlations (e.g. resilience × belonging) ──
    framework_totals: dict[str, list[float]] = {}
    for fw, cols in by_framework.items():
        n_resp = min(len(c) for c in cols)
        framework_totals[fw] = [sum(cols[i][r] for i in range(len(cols))) for r in range(n_resp)]
    correlations = []
    fw_keys = sorted(framework_totals.keys())
    for i in range(len(fw_keys)):
        for j in range(i + 1, len(fw_keys)):
            fa, fb = fw_keys[i], fw_keys[j]
            # Trim to same length
            n = min(len(framework_totals[fa]), len(framework_totals[fb]))
            r, p = _pearson(framework_totals[fa][:n], framework_totals[fb][:n])
            if r is not None:
                correlations.append({"a": fa, "b": fb, "r": r, "p": p, "n": n})

    # ── Qualitative coding (one call per open-ended question, capped) ──
    open_results = {}
    n_calls = 0
    for key, resps in per_open.items():
        if n_calls >= MAX_OPEN_CODING_CALLS:
            open_results[key] = {"skipped": "max_calls_reached"}
            continue
        open_results[key] = _code_open_responses(key, resps)
        n_calls += 1

    # ── Compose findings.md ──
    out_md = _compose_findings(
        slug, rqs, items, rows, matched,
        descriptives, alphas, correlations, open_results,
    )
    findings_path = survey_dir / "findings.md"
    findings_path.write_text(out_md, encoding="utf-8")
    print(f"  [Agent12] findings.md: {findings_path.relative_to(OUTPUT_DIR.parent)}")

    return {
        "slug":         slug,
        "n_responses":  len(rows),
        "matched":      matched,
        "alphas":       alphas,
        "correlations": correlations,
        "saved_to":     str(findings_path),
    }


def _compose_findings(slug, rqs, items, rows, matched,
                      descriptives, alphas, correlations, open_results) -> str:
    n = len(rows)
    L = [
        "---", "moki: true", "type: survey_findings",
        f"updated: {datetime.now().isoformat(timespec='seconds')}",
        f"slug: {slug}", f"n: {n}", "---", "",
        f"# 📊 ממצאי הסקר: {slug}",
        "",
        f"**n = {n} משיבים**  ·  **{matched}/{len(items)} פריטים נמדדו**  ·  "
        f"_{datetime.now().strftime('%d/%m/%Y')}_",
        "",
    ]
    if rqs:
        L.append("## שאלות מחקר")
        L.append("")
        for i, q in enumerate(rqs, 1):
            L.append(f"  - **RQ{i}:** {q}")
        L.append("")

    L.extend(["## 1. הקשר הדגימה", ""])
    L.append(f"נאספו {n} תגובות. נדרשת זהירות כשמסיקים: גדלי דגימה קטנים "
             f"מספקים *כיוון*, לא ראיה סגורה. כל הניתוחים שלהלן הם תיאוריים "
             f"במהותם, ומבחנים אינפרנציאליים מוצגים עם הסתייגות.")
    L.append("")

    L.extend(["## 2. סטטיסטיקה תיאורית — לפי פריט", ""])
    L.append("| מסגרת | פריט | M | SD | n |")
    L.append("|---|---|---|---|---|")
    for key, d in sorted(descriptives.items(), key=lambda kv: kv[1]["framework"]):
        s = d["stats"]
        fw = d["framework"][:25]
        item_text = key[:80].replace("|", "\\|")
        if s["mean"] is not None:
            L.append(f"| {fw} | {item_text} | {s['mean']} | {s['sd']} | {s['n']} |")
    L.append("")

    if alphas:
        L.extend(["## 3. מהימנות פנימית (Cronbach's α)", ""])
        L.append("| מסגרת | α | פירוש |")
        L.append("|---|---|---|")
        for fw, a in sorted(alphas.items(), key=lambda kv: -kv[1]):
            interp = ("מצוין" if a >= 0.9 else "טוב" if a >= 0.7
                      else "סביר" if a >= 0.6 else "נמוך — לבדוק")
            L.append(f"| {fw} | {a} | {interp} |")
        L.append("")

    if correlations:
        L.extend(["## 4. מתאמים בין מסגרות", ""])
        L.append("| משתנה A | משתנה B | r | p | n | פירוש |")
        L.append("|---|---|---|---|---|---|")
        for c in sorted(correlations, key=lambda x: -abs(x["r"])):
            strength = ("חזק" if abs(c["r"]) >= 0.5 else "בינוני" if abs(c["r"]) >= 0.3
                        else "חלש")
            sig = "**מובהק**" if c["p"] is not None and c["p"] < 0.05 else "לא מובהק"
            L.append(f"| {c['a']} | {c['b']} | {c['r']} | "
                     f"{c['p'] if c['p'] is not None else '—'} | {c['n']} | "
                     f"{strength}, {sig} |")
        L.append("")
        L.append("_זכור: r לבדו אינו סיבתיות. p < .05 ב-n קטן עדיין מצריך זהירות._")
        L.append("")

    if open_results:
        L.extend(["## 5. ניתוח שאלות פתוחות", ""])
        for question, result in open_results.items():
            L.append(f"### {question[:120]}")
            L.append("")
            themes = result.get("themes", [])
            if themes:
                L.append("**תמות שעלו:**")
                L.append("")
                for t in themes:
                    L.append(f"  - **{t.get('name', '?')}** "
                             f"(×{t.get('count', '?')}) — "
                             f"{t.get('description', '')[:200]}")
                L.append("")
            quotes = result.get("quotes", [])
            if quotes:
                L.append("**ציטוטים מאפיינים:**")
                L.append("")
                for q in quotes[:3]:
                    L.append(f"  > {q[:300]}")
                L.append("")

    L.extend([
        "## 6. מגבלות מתודולוגיות",
        "",
        f"  - n = {n} — דגימה קטנה; ממצאים תיאוריים בלבד.",
        "  - דגימת נוחות — אינה מייצגת אוכלוסייה.",
        "  - מבחני מובהקות במדגם קטן נוטים ל-Type II error גבוה.",
        "  - שאלות פתוחות קודדו ע\"י LLM — בדיקת אמינות אנושית מומלצת.",
        "",
        "---",
        "",
        f"_נוצר ע\"י Agent 12 — Response Analyzer · {datetime.now().strftime('%d/%m/%Y %H:%M')}_",
    ])
    return "\n".join(L)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Agent 12 — Survey Response Analyzer")
    ap.add_argument("--slug",      required=True,
                    help="Survey slug (folder under output/surveys/)")
    ap.add_argument("--responses", required=True,
                    help="Path to responses CSV exported from Forms")
    ap.add_argument("--rq",        action="append", default=[],
                    help="Research question (can pass multiple times)")
    args = ap.parse_args()

    responses_path = Path(args.responses).expanduser()
    if not responses_path.exists():
        print(f"❌ responses file not found: {responses_path}")
        sys.exit(1)

    res = analyze(args.slug, responses_path, args.rq)
    if "error" in res:
        print(f"❌ {res['error']}")
        sys.exit(1)
    print(f"✅ analyzed {res['n_responses']} responses")
    print(f"   {res['saved_to']}")


if __name__ == "__main__":
    main()
