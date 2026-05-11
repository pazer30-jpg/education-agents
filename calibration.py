"""
calibration.py — כיול בין QA חזוי ל-engagement בפועל.

הרעיון:
  אם Moki אמר "QA 95" אבל הפוסט קיבל bottom-3 engagement → הכיול שגוי.
  אנחנו רוצים לדעת אם המודל overconfident, underconfident, או well_calibrated,
  ולחשב את סף ה-QA שבאמת מנבא engagement גבוה.

נתונים:
  - output/performance_log.json — engagement בפועל
  - QA scores נסרקים מקבצי הפוסטים האחרונים (חיפוש דפוסים כמו
    "Voice QA: 87/100" בקבצים סמוכים) — או נשמרים בקובץ
    output/qa_scores.json אם קיים (אופציונלי, נכתב מ-Agent 3).

API:
  calibrate() -> dict
      Match QA scores to actual engagement, compute correlation.
      Returns: {
        "samples": int,
        "correlation": float,         # -1 to 1
        "verdict": str,               # well_calibrated / overconfident / underconfident / no_signal
        "qa_threshold_for_high_engagement": int,
        "drift": float,               # 0..1 — divergence between predictions and reality
      }

  adjustment_recommendation() -> str
      Hebrew text — what to tell Moki to adjust based on calibration.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from statistics import mean

from config import OUTPUT_DIR

PERF_FILE = OUTPUT_DIR / "performance_log.json"
QA_SCORES_FILE = OUTPUT_DIR / "qa_scores.json"  # optional sidecar log

# Verdict thresholds
DRIFT_GOOD = 0.15      # below this drift is fine
DRIFT_WARN = 0.30      # above this is overconfident/underconfident
MIN_SAMPLES = 5        # below this we cannot conclude


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def _engagement_score(entry: dict) -> float:
    """ציון engagement מנורמל לפי פלטפורמה (זהה לזה ב-performance_log)."""
    m = entry.get("metrics", {})
    p = entry.get("platform", "")
    if p == "linkedin":
        return (
            m.get("comments", 0) * 3
            + m.get("likes", 0)
            + m.get("shares", 0) * 5
        )
    if p == "blog":
        return m.get("views", 0) + m.get("avg_time", 0) * 10
    if p == "podcast":
        return m.get("plays", 0)
    return float(entry.get("personal_score", 5)) * 10


def _normalize(values: list[float]) -> list[float]:
    """Min-max normalize to [0, 1]; flat list returns 0.5 each."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def _pearson(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation. Returns 0.0 if undefined."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return 0.0
    mx, my = mean(xs), mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


# ─────────────────────────────────────────────
# QA score discovery
# ─────────────────────────────────────────────

# Scan posts/* for embedded "Voice QA: 87/100" markers as a fallback QA source.
_QA_PATTERNS = [
    re.compile(r"Voice\s*QA[^\d]{0,10}(\d{1,3})\s*/\s*100", re.I),
    re.compile(r"QA[^\d]{0,10}(\d{1,3})\s*/\s*100"),
    re.compile(r'"qa_score"\s*:\s*(\d{1,3})'),
    re.compile(r'"score"\s*:\s*(\d{1,3})'),
]


def _qa_from_text(text: str) -> int | None:
    for pat in _QA_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                v = int(m.group(1))
                if 0 <= v <= 100:
                    return v
            except (ValueError, TypeError):
                pass
    return None


def _build_title_to_qa() -> dict[str, int]:
    """
    Build a map title-snippet → qa_score by scanning:
      1) qa_scores.json sidecar (if exists)
      2) Recent post files (linkedin/blog/podcast) for embedded QA markers
    """
    title_to_qa: dict[str, int] = {}

    sidecar = _load_json(QA_SCORES_FILE, [])
    if isinstance(sidecar, list):
        for entry in sidecar:
            t = (entry.get("title") or entry.get("post") or "").strip().lower()
            qa = entry.get("qa_score") or entry.get("voice_score")
            if t and isinstance(qa, (int, float)):
                title_to_qa[t] = int(qa)

    # Scan post files for QA markers
    for sub in ("posts/linkedin", "posts/blog", "posts/podcast", "posts"):
        d = OUTPUT_DIR / sub
        if not d.exists():
            continue
        for f in d.iterdir():
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            qa = _qa_from_text(text)
            if qa is None:
                continue
            # Use filename stem as a title proxy + first 60 chars
            stem = f.stem.lower()
            title_to_qa.setdefault(stem, qa)
            first_line = next(
                (ln.strip().lower() for ln in text.splitlines() if ln.strip()),
                "",
            )[:80]
            if first_line:
                title_to_qa.setdefault(first_line, qa)
    return title_to_qa


def _match_qa(title: str, title_to_qa: dict[str, int]) -> int | None:
    """Try several normalizations to match a perf entry title to a QA record."""
    if not title:
        return None
    t = title.strip().lower()
    if t in title_to_qa:
        return title_to_qa[t]
    # Substring match — perf title appears inside any QA key
    for key, qa in title_to_qa.items():
        if t and (t in key or key in t):
            return qa
    # Word-overlap match
    t_words = set(re.findall(r"[֐-׿A-Za-z]{4,}", t))
    if not t_words:
        return None
    best, best_overlap = None, 0
    for key, qa in title_to_qa.items():
        k_words = set(re.findall(r"[֐-׿A-Za-z]{4,}", key))
        if not k_words:
            continue
        overlap = len(t_words & k_words)
        if overlap >= 2 and overlap > best_overlap:
            best, best_overlap = qa, overlap
    return best


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def calibrate() -> dict:
    """
    Match QA scores to actual engagement, compute correlation.
    """
    perf = _load_json(PERF_FILE, [])
    if not isinstance(perf, list) or not perf:
        return {
            "samples": 0,
            "correlation": 0.0,
            "verdict": "no_signal",
            "qa_threshold_for_high_engagement": 0,
            "drift": 0.0,
        }

    title_to_qa = _build_title_to_qa()

    pairs: list[tuple[int, float]] = []  # (qa, engagement)
    for entry in perf:
        title = entry.get("title", "")
        qa = _match_qa(title, title_to_qa)
        if qa is None:
            continue
        eng = _engagement_score(entry)
        pairs.append((qa, eng))

    samples = len(pairs)
    if samples < MIN_SAMPLES:
        return {
            "samples": samples,
            "correlation": 0.0,
            "verdict": "no_signal",
            "qa_threshold_for_high_engagement": 0,
            "drift": 0.0,
        }

    qas = [p[0] for p in pairs]
    engs = [p[1] for p in pairs]
    correlation = _pearson([float(q) for q in qas], engs)

    # Drift: compare normalized predicted (QA/100) vs normalized engagement.
    # High drift = predictions don't match outcomes.
    qa_norm = [q / 100.0 for q in qas]
    eng_norm = _normalize(engs)
    drift = mean(abs(a - b) for a, b in zip(qa_norm, eng_norm))

    # Find QA threshold for "high engagement" (top tertile of engagement).
    sorted_by_eng = sorted(pairs, key=lambda p: p[1], reverse=True)
    top_tertile_size = max(1, samples // 3)
    top_tertile = sorted_by_eng[:top_tertile_size]
    qa_threshold = int(min(p[0] for p in top_tertile))

    # Verdict logic:
    #   - correlation reflects directional alignment
    #   - drift reflects magnitude mismatch
    #   - mean(qa) vs mean(eng_norm*100) reflects bias (over/under)
    mean_qa = mean(qa_norm)
    mean_eng = mean(eng_norm)
    bias = mean_qa - mean_eng  # positive = predicted higher than reality

    if drift <= DRIFT_GOOD and correlation >= 0.3:
        verdict = "well_calibrated"
    elif bias > 0.15 or (drift > DRIFT_WARN and correlation < 0.3):
        verdict = "overconfident"
    elif bias < -0.15:
        verdict = "underconfident"
    elif correlation < 0.0:
        verdict = "overconfident"  # negative correlation is a worse case
    else:
        # In-between: lean by drift
        verdict = "overconfident" if bias >= 0 else "underconfident"

    return {
        "samples": samples,
        "correlation": round(correlation, 3),
        "verdict": verdict,
        "qa_threshold_for_high_engagement": qa_threshold,
        "drift": round(drift, 3),
    }


def adjustment_recommendation() -> str:
    """
    Hebrew text — what to tell Moki to adjust based on calibration.
    """
    r = calibrate()
    n = r["samples"]
    drift = r["drift"]
    corr = r["correlation"]
    thr = r["qa_threshold_for_high_engagement"]
    verdict = r["verdict"]

    if verdict == "no_signal":
        return (
            f"אין מספיק מדגם לכיול ({n} זיווגים בלבד; דרושים לפחות {MIN_SAMPLES}). "
            f"המשך לרשום ביצועים ב-performance_log כדי שה-engagement יוכל להצטלב "
            f"עם ציוני ה-QA."
        )

    base = (
        f"כיול ({n} זיווגים, מתאם {corr:+.2f}, סטייה {drift:.2f}): "
    )

    if verdict == "well_calibrated":
        return base + (
            f"המודל מכויל היטב — ציוני QA תואמים את ה-engagement. "
            f"להמשיך כך. סף ה-QA לפוסטים מובילים: {thr}/100."
        )
    if verdict == "overconfident":
        return base + (
            f"המודל overconfident — נותן ציוני QA גבוהים לפוסטים שלא מהדהדים. "
            f"המלצות: (1) להחמיר ב-Voice QA — להוריד 10 נקודות מהציון, "
            f"(2) להעלות את סף ה-pass ל-{max(thr, 75)}, "
            f"(3) לשקול שימוש ב-anti_patterns כדי לפסול פתיחות חלשות."
        )
    if verdict == "underconfident":
        return base + (
            f"המודל underconfident — מעניק ציונים נמוכים לפוסטים שדווקא הצליחו. "
            f"המלצות: (1) להקל ב-Voice QA על פוסטים שעוברים את הסף הבסיסי, "
            f"(2) לא לפסול אוטומטית פוסטים עם ציון 60-75 — לתת להם הזדמנות. "
            f"סף QA ריאלי לעיון: {thr}/100."
        )
    return base + "לא ברור — בדוק ידנית את performance_log."


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] in ("--rec", "--recommend", "-r"):
        print(adjustment_recommendation())
    else:
        result = calibrate()
        print("\n📐 Calibration report:\n")
        for k, v in result.items():
            print(f"  {k:<40}: {v}")
        print()
        print(adjustment_recommendation())
        print()
