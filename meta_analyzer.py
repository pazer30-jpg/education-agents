"""
meta_analyzer.py — Statistical meta-analysis of effect sizes.

מאחד effect sizes ממאמרים מרובים → ממוצע משוקלל + heterogeneity.
מבוסס על paper_analyzer שכבר מחלץ n=, d=, p=, CI.

נוסחאות (random-effects):
  - inverse-variance weighting:        w_i = 1 / (var_i + tau²)
  - pooled effect:                     d̄ = Σ(w_i * d_i) / Σ(w_i)
  - heterogeneity:                     I² = (Q - df) / Q × 100

Usage:
  python3 meta_analyzer.py <enriched_papers.json>
  python3 meta_analyzer.py --topic shame_in_education
"""

import sys
import json
import re
import math
from pathlib import Path
from datetime import datetime
from typing import Optional

from config import OUTPUT_DIR, PAPERS_DIR

# scipy is optional — fallback to manual stats if absent
try:
    from scipy import stats as _stats
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False


# ─────────────────────────────────────────────
# Effect size extraction
# ─────────────────────────────────────────────

def _parse_effect_size(s) -> Optional[float]:
    """Parse effect_size string like 'd=0.42' or 'r=0.31' to float."""
    if not s or not isinstance(s, str):
        return None
    m = re.search(r"[dr]\s*=\s*(-?\d+\.?\d*)", s)
    if m:
        return float(m.group(1))
    m = re.search(r"(-?\d+\.\d+)", s)
    if m:
        return float(m.group(1))
    return None


def _parse_sample_size(n) -> Optional[int]:
    """Parse sample_size — handles int, str, or 'n=400' format."""
    if n is None:
        return None
    if isinstance(n, int):
        return n
    if isinstance(n, str):
        m = re.search(r"\d+", n)
        return int(m.group(0)) if m else None
    return None


def extract_effects(papers: list[dict]) -> list[dict]:
    """
    Extract usable effect sizes from a corpus.
    Returns: [{title, year, n, d, variance}]
    """
    effects = []
    for p in papers:
        d = _parse_effect_size(p.get("effect_size") or p.get("methodology_check", {}).get("effect_size"))
        n = _parse_sample_size(p.get("sample_size"))
        if d is None or n is None or n < 5:
            continue
        # Variance of Cohen's d ≈ (n1+n2)/(n1*n2) + d²/(2*(n1+n2))
        # Assume balanced groups: n1 = n2 = n/2
        if n < 10:
            continue
        n_per_group = n / 2
        variance = (2 / n_per_group) + (d * d / (2 * 2 * n_per_group))
        effects.append({
            "title": (p.get("_title") or p.get("title") or "")[:80],
            "year": p.get("_year") or p.get("year"),
            "n": n,
            "d": d,
            "variance": variance,
            "se": math.sqrt(variance),
        })
    return effects


# ─────────────────────────────────────────────
# Meta-analysis (random-effects, DerSimonian-Laird)
# ─────────────────────────────────────────────

def random_effects_meta(effects: list[dict]) -> dict:
    """
    DerSimonian-Laird random-effects meta-analysis.
    Returns: {pooled_d, ci_low, ci_high, heterogeneity_I2, tau2, n_studies, total_n}
    """
    if len(effects) < 2:
        return {"error": "Need at least 2 studies", "n_studies": len(effects)}

    # Step 1: fixed-effects (inverse-variance weighting)
    weights_fe = [1 / e["variance"] for e in effects]
    d_fe = sum(w * e["d"] for w, e in zip(weights_fe, effects)) / sum(weights_fe)

    # Step 2: heterogeneity (Q statistic)
    q = sum(w * (e["d"] - d_fe) ** 2 for w, e in zip(weights_fe, effects))
    df = len(effects) - 1

    # Step 3: tau² (between-study variance)
    c = sum(weights_fe) - sum(w * w for w in weights_fe) / sum(weights_fe)
    tau2 = max(0, (q - df) / c) if c > 0 else 0

    # Step 4: random-effects weights
    weights_re = [1 / (e["variance"] + tau2) for e in effects]
    d_pooled = sum(w * e["d"] for w, e in zip(weights_re, effects)) / sum(weights_re)
    se_pooled = math.sqrt(1 / sum(weights_re))

    # 95% CI
    ci_low = d_pooled - 1.96 * se_pooled
    ci_high = d_pooled + 1.96 * se_pooled

    # I² heterogeneity %
    i2 = max(0, ((q - df) / q) * 100) if q > 0 else 0

    # p-value for overall effect
    z = d_pooled / se_pooled
    if SCIPY_AVAILABLE:
        p_value = 2 * (1 - _stats.norm.cdf(abs(z)))
    else:
        # Approximation
        p_value = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))

    return {
        "pooled_d": round(d_pooled, 3),
        "se": round(se_pooled, 3),
        "ci_low": round(ci_low, 3),
        "ci_high": round(ci_high, 3),
        "ci_str": f"95% CI [{ci_low:.2f}, {ci_high:.2f}]",
        "z": round(z, 2),
        "p_value": round(p_value, 4),
        "p_str": "<.001" if p_value < 0.001 else f"={p_value:.3f}",
        "heterogeneity_I2": round(i2, 1),
        "heterogeneity_verdict": _i2_verdict(i2),
        "tau2": round(tau2, 4),
        "Q": round(q, 2),
        "df": df,
        "n_studies": len(effects),
        "total_n": sum(e["n"] for e in effects),
    }


def _i2_verdict(i2: float) -> str:
    """Higgins' interpretation of I²."""
    if i2 < 25:
        return "low (consistent across studies)"
    elif i2 < 50:
        return "moderate"
    elif i2 < 75:
        return "substantial"
    return "considerable (effect highly context-dependent)"


# ─────────────────────────────────────────────
# Forest plot (text-based — no matplotlib needed)
# ─────────────────────────────────────────────

def forest_plot_ascii(effects: list[dict], pooled: dict) -> str:
    """ASCII forest plot — works in terminal + markdown."""
    if not effects:
        return ""

    # Find scale
    all_d = [e["d"] for e in effects] + [pooled.get("ci_low", 0), pooled.get("ci_high", 0)]
    min_d = min(all_d)
    max_d = max(all_d)
    range_d = max_d - min_d or 1
    width = 40

    def _pos(d):
        return int((d - min_d) / range_d * width)

    lines = ["", "Effect Size Forest Plot", ""]
    for e in effects:
        d = e["d"]
        ci_low = d - 1.96 * e["se"]
        ci_high = d + 1.96 * e["se"]
        line = [" "] * (width + 2)
        line[_pos(d)] = "■"
        # CI bars
        for i in range(_pos(ci_low), _pos(ci_high) + 1):
            if 0 <= i < len(line) and line[i] == " ":
                line[i] = "─"
        line[_pos(d)] = "■"
        title = e["title"][:30].ljust(30)
        lines.append(f"  {title} {''.join(line)} d={d:+.2f} n={e['n']}")

    # Pooled
    p = pooled
    if "pooled_d" in p:
        line = [" "] * (width + 2)
        for i in range(_pos(p["ci_low"]), _pos(p["ci_high"]) + 1):
            if 0 <= i < len(line):
                line[i] = "═"
        line[_pos(p["pooled_d"])] = "◆"
        lines.append("  " + "─" * 30 + " " + "─" * (width + 2))
        lines.append(f"  {'POOLED (random-effects)'.ljust(30)} {''.join(line)} d={p['pooled_d']:+.2f}")
        lines.append(f"     {p['ci_str']}, p{p['p_str']}, I²={p['heterogeneity_I2']}%")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────

def format_report(effects: list[dict], pooled: dict, topic: str = "") -> str:
    if "error" in pooled:
        return f"  ⚠️ {pooled['error']} — Need ≥2 studies with effect sizes"

    lines = [
        f"\n📊 Meta-Analysis Report — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
    ]
    if topic:
        lines.append(f"   Topic: {topic}")
    lines.append("")
    lines.append(f"   Studies:           {pooled['n_studies']}")
    lines.append(f"   Total participants: {pooled['total_n']:,}")
    lines.append(f"")
    lines.append(f"   📐 Pooled effect:   d = {pooled['pooled_d']:+.3f}")
    lines.append(f"   📏 95% CI:          [{pooled['ci_low']:.3f}, {pooled['ci_high']:.3f}]")
    lines.append(f"   🎯 Significance:    z={pooled['z']}, p{pooled['p_str']}")
    lines.append(f"   🔀 Heterogeneity:   I² = {pooled['heterogeneity_I2']}% ({pooled['heterogeneity_verdict']})")
    lines.append(f"   τ² (tau²):          {pooled['tau2']}")
    lines.append("")

    # Interpretation in Hebrew
    d = abs(pooled["pooled_d"])
    if d < 0.2:
        size_label = "אפקט זניח"
    elif d < 0.5:
        size_label = "אפקט קטן"
    elif d < 0.8:
        size_label = "אפקט בינוני"
    else:
        size_label = "אפקט גדול"
    lines.append(f"   📝 פרשנות: {size_label} (d={pooled['pooled_d']:+.2f})")

    if pooled["heterogeneity_I2"] > 75:
        lines.append(f"   ⚠️  hetero גבוה — האפקט תלוי הקשר; יש להיזהר מהכללה")
    elif pooled["p_value"] >= 0.05:
        lines.append(f"   ⚠️  p ≥ .05 — תוצאה לא משמעותית סטטיסטית")
    else:
        lines.append(f"   ✅ תוצאה משמעותית סטטיסטית")

    lines.append("")
    lines.append(forest_plot_ascii(effects, pooled))

    return "\n".join(lines)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def analyze_file(papers_path: Path) -> Optional[dict]:
    """Run meta-analysis on a papers JSON file."""
    if not papers_path.exists():
        print(f"  ❌ Not found: {papers_path}")
        return None

    data = json.loads(papers_path.read_text(encoding="utf-8"))
    papers = data.get("papers", data) if isinstance(data, dict) else data
    topic = data.get("topic", papers_path.stem) if isinstance(data, dict) else papers_path.stem

    # Try analyzed papers first (has methodology_check), fall back to raw
    effects = extract_effects(papers)
    pooled = random_effects_meta(effects) if effects else {"error": "no effects extracted"}
    return {"topic": topic, "effects": effects, "pooled": pooled}


def main():
    if "--topic" in sys.argv:
        idx = sys.argv.index("--topic")
        if idx + 1 < len(sys.argv):
            slug = sys.argv[idx + 1]
            f = next(PAPERS_DIR.glob(f"*{slug}*_enriched.json"), None)
            if not f:
                print(f"  ❌ No file matching '{slug}'")
                return
            r = analyze_file(f)
            if r:
                print(format_report(r["effects"], r["pooled"], r["topic"]))
        return

    if len(sys.argv) < 2:
        # Try analyzing recent papers
        files = sorted(PAPERS_DIR.glob("*_enriched.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            print("Usage: python3 meta_analyzer.py <papers.json>")
            return
        f = files[0]
        print(f"Using most recent: {f.name}")
        r = analyze_file(f)
        if r:
            print(format_report(r["effects"], r["pooled"], r["topic"]))
        return

    f = Path(sys.argv[1])
    r = analyze_file(f)
    if r:
        print(format_report(r["effects"], r["pooled"], r["topic"]))


if __name__ == "__main__":
    main()
