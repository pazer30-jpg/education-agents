"""
active_response.py — מקשר בין observability לAgent 3.

מה שעשינו עד היום: observability.py → active_alerts.json (פסיבי)
מה שחסר: סוכנים לא מגיבים להתראות. מוקי רואה התראה, ממשיך כרגיל.

עכשיו: לפני שAgent 3 כותב, הוא קורא את ה-active alerts ומתאים את הprompt.

Use cases:
  - Alert: "voice_score_avg 65 < target 85" → Agent 3 prompt: "פז, התקרבת לרמת AI generic. שמור על voice ספציפי יותר."
  - Alert: "step_p95 too high" → skip optional checks
  - Anti-patterns piling up → inject explicit "AVOID" block
"""

import json
from pathlib import Path
from datetime import datetime, timedelta

from config import OUTPUT_DIR

ALERTS_FILE = OUTPUT_DIR / "active_alerts.json"
ANTI_PATTERNS_FILE = OUTPUT_DIR / "anti_patterns.json"


def _load_alerts() -> list[dict]:
    if not ALERTS_FILE.exists():
        return []
    try:
        return json.loads(ALERTS_FILE.read_text(encoding="utf-8")).get("alerts", [])
    except Exception:
        return []


def _load_anti_patterns(min_failures: int = 2) -> list[dict]:
    if not ANTI_PATTERNS_FILE.exists():
        return []
    try:
        data = json.loads(ANTI_PATTERNS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("patterns", [])
        return [p for p in data if p.get("failures", 0) >= min_failures]
    except Exception:
        return []


def adjustments_for_agent3() -> dict:
    """
    Generate prompt adjustments based on current alerts + anti-patterns.
    Returns: {"prompt_inject": str, "skip_checks": [list], "warnings_for_user": [list]}
    """
    alerts = _load_alerts()
    anti = _load_anti_patterns()

    inject_lines = []
    skip_checks = []
    user_warnings = []

    # Voice score breach → tighten voice
    voice_alerts = [a for a in alerts if "voice" in (a.get("slo", "") or "").lower()]
    if voice_alerts:
        inject_lines.append(
            "⚠️  התראה: voice score ירד מתחת ליעד. הקפד על שפה אישית, לא גנרית. "
            "פרטים מהשטח (שמות, מקומות, שנים) חובה."
        )

    # QA score breach
    qa_alerts = [a for a in alerts if "qa" in (a.get("slo", "") or "").lower()]
    if qa_alerts:
        inject_lines.append(
            "⚠️  התראה: ציון QA ירד. בדוק שיש מקורות, ציטוטים, ומבנה ברור."
        )

    # Step duration breach → skip non-critical
    duration_alerts = [a for a in alerts if "duration" in (a.get("slo", "") or "").lower()]
    if duration_alerts:
        skip_checks.append("voice_drift")  # heuristic — can run later
        user_warnings.append(
            f"⏱  Pipeline runs slow ({duration_alerts[0].get('value','?')} min) — "
            "skipping voice_drift check this run"
        )

    # Anti-patterns inject
    if anti:
        anti_block = "\n".join(
            f"  ✗ הימנע מ: {p.get('pattern', '')[:80]} (כשל {p.get('failures', 0)}×)"
            for p in anti[:5]
        )
        inject_lines.append(
            f"━━━ AVOID THESE PATTERNS (failed before) ━━━\n{anti_block}"
        )

    return {
        "prompt_inject": "\n\n".join(inject_lines) if inject_lines else "",
        "skip_checks": skip_checks,
        "warnings_for_user": user_warnings,
        "alerts_active": len(alerts),
        "anti_patterns_active": len(anti),
    }


def report() -> str:
    """Human-readable status."""
    adj = adjustments_for_agent3()
    lines = [f"\n🚨 Active Response — {datetime.now().strftime('%d/%m/%Y %H:%M')}"]
    lines.append(f"   Active alerts:    {adj['alerts_active']}")
    lines.append(f"   Anti-patterns:    {adj['anti_patterns_active']}")
    if adj['prompt_inject']:
        lines.append(f"\n📝 ETA Agent 3 prompt:")
        lines.append(adj['prompt_inject'][:500])
    if adj['skip_checks']:
        lines.append(f"\n⏭  Skipping: {', '.join(adj['skip_checks'])}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
