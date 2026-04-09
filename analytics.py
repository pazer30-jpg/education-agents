"""
analytics.py — מעקב ביצועים
עוקב אחרי כל הרצה: זמנים, QA scores, החלטות ביקורת, נושאים.

שימוש:
  from analytics import tracker
  tracker.start_run("non-formal education")
  tracker.record_step("researcher", duration=45.2, qa_score=88)
  tracker.end_run(success=True)

  # דוח
  python analytics.py
"""

import json
import time
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from config import OUTPUT_DIR

ANALYTICS_FILE = OUTPUT_DIR / "analytics.json"


# ─────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────

def _empty_db() -> dict:
    return {
        "runs":          [],
        "total_runs":    0,
        "total_success": 0,
        "created_at":    datetime.now().isoformat(),
    }


def _load() -> dict:
    if ANALYTICS_FILE.exists():
        try:
            return json.loads(ANALYTICS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return _empty_db()


def _save(db: dict):
    ANALYTICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ANALYTICS_FILE.write_text(
        json.dumps(db, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


# ─────────────────────────────────────────────
# Tracker
# ─────────────────────────────────────────────

class PipelineTracker:
    def __init__(self):
        self._current: dict | None = None
        self._run_start: float     = 0

    def start_run(self, topic: str, content_types: list = None):
        self._run_start = time.time()
        self._current   = {
            "id":            datetime.now().strftime("%Y%m%d_%H%M%S"),
            "started_at":    datetime.now().isoformat(),
            "topic":         topic,
            "content_types": content_types or [],
            "steps":         [],
            "qa_scores":     {},
            "review":        {},
            "success":       None,
            "duration_s":    None,
            "outputs":       [],
            "errors":        [],
        }

    def record_step(self, agent: str, duration_s: float,
                    qa_score: int = None, status: str = "ok", note: str = ""):
        if not self._current:
            return
        step = {"agent": agent, "duration_s": round(duration_s, 1),
                "status": status, "note": note}
        if qa_score is not None:
            step["qa_score"] = qa_score
            self._current["qa_scores"][agent] = qa_score
        self._current["steps"].append(step)

    def record_review(self, platform: str, decision: str):
        if self._current:
            self._current["review"][platform] = decision

    def record_output(self, output_type: str, file_path: str):
        if self._current:
            self._current["outputs"].append({"type": output_type, "file": str(file_path)})

    def record_error(self, agent: str, error: str):
        if self._current:
            self._current["errors"].append({
                "agent": agent,
                "error": str(error)[:200],
                "time":  datetime.now().isoformat(),
            })

    def end_run(self, success: bool):
        if not self._current:
            return
        self._current["success"]    = success
        self._current["duration_s"] = round(time.time() - self._run_start, 1)
        self._current["ended_at"]   = datetime.now().isoformat()

        scores = list(self._current["qa_scores"].values())
        self._current["avg_qa"] = round(sum(scores) / len(scores)) if scores else None

        # Estimate cost based on steps
        self._current["est_cost"] = _estimate_cost(self._current["steps"])

        db = _load()
        db["runs"].append(self._current)
        db["runs"] = db["runs"][-200:]
        db["total_runs"]    = len(db["runs"])
        db["total_success"] = sum(1 for r in db["runs"] if r.get("success"))
        _save(db)
        self._current = None


# ─────────────────────────────────────────────
# Cost estimation
# ─────────────────────────────────────────────

# Average cost per agent step (based on max_budget * ~40% usage)
COST_PER_AGENT = {
    "planner":    0.35,
    "researcher": 2.50,  # 3 queries + curation
    "writer":     2.80,  # EN + HE
    "content":    2.00,  # per platform
    "designer":   0.25,
    "editor":     1.50,
}

def _estimate_cost(steps: list[dict]) -> float:
    total = 0
    for step in steps:
        agent = step.get("agent", "")
        total += COST_PER_AGENT.get(agent, 0.5)
    return round(total, 2)


# singleton
tracker = PipelineTracker()


# ─────────────────────────────────────────────
# Reports
# ─────────────────────────────────────────────

def print_report(last_n: int = 20):
    db   = _load()
    runs = db.get("runs", [])

    if not runs:
        print("אין נתוני analytics עדיין.")
        return

    recent = runs[-last_n:]

    print(f"""
{'='*60}
📊 Analytics Report — {datetime.now().strftime('%d/%m/%Y %H:%M')}
{'='*60}
  סה״כ הרצות:   {db['total_runs']}
  הצלחות:        {db['total_success']}  ({db['total_success']/max(1,db['total_runs'])*100:.0f}%)
  כשלים:         {db['total_runs'] - db['total_success']}
""")

    durations = [r["duration_s"] for r in recent if r.get("duration_s")]
    if durations:
        avg_d = sum(durations) / len(durations)
        print(f"  ⏱  זמן ממוצע:    {avg_d/60:.1f} דק'  "
              f"(מינ: {min(durations)/60:.1f}  מקס: {max(durations)/60:.1f})")

    qa_by_agent = defaultdict(list)
    for r in recent:
        for agent, score in r.get("qa_scores", {}).items():
            qa_by_agent[agent].append(score)
    if qa_by_agent:
        print("\n  🔍 QA ממוצע לפי סוכן:")
        for agent, scores in sorted(qa_by_agent.items()):
            avg = sum(scores) / len(scores)
            bar = "█" * int(avg / 10) + "░" * (10 - int(avg / 10))
            print(f"    {agent:<14} {bar}  {avg:.0f}/100  {_trend(scores)}")

    review_counts = defaultdict(lambda: defaultdict(int))
    for r in recent:
        for platform, decision in r.get("review", {}).items():
            review_counts[platform][decision] += 1
    if review_counts:
        print("\n  👁️  תוצאות ביקורת:")
        for platform, decisions in review_counts.items():
            total = sum(decisions.values())
            parts = " | ".join(f"{d}: {n}" for d, n in sorted(decisions.items()))
            print(f"    {platform:<10} ({total}x) → {parts}")

    topics = [r.get("topic","") for r in recent if r.get("topic")]
    if topics:
        from collections import Counter
        top = Counter(topics).most_common(5)
        print(f"\n  📚 נושאים נפוצים: "
              + " | ".join(f"{t} ({n}x)" for t, n in top))

    all_errors = []
    for r in recent:
        all_errors.extend(r.get("errors", []))
    if all_errors:
        from collections import Counter
        err_agents = Counter(e["agent"] for e in all_errors)
        print(f"\n  ⚠️  שגיאות לפי סוכן: "
              + " | ".join(f"{a}: {n}" for a, n in err_agents.most_common()))

    print(f"\n  הרצות אחרונות:")
    for r in reversed(recent[-10:]):
        icon = "✅" if r.get("success") else "❌"
        t    = r.get("started_at", "")[:16].replace("T", " ")
        dur  = f"{r.get('duration_s',0)/60:.1f}m"
        qa   = f"QA:{r.get('avg_qa','?')}" if r.get("avg_qa") else ""
        errs = f"⚠️ {len(r.get('errors',[]))}" if r.get("errors") else ""
        print(f"    {icon} {t}  {dur:>6}  {qa:>8}  {r.get('topic','')[:28]}  {errs}")
    print()


def _trend(scores: list) -> str:
    if len(scores) < 3:
        return "  —"
    if scores[-1] > scores[0] + 3:
        return "↗ עולה"
    elif scores[-1] < scores[0] - 3:
        return "↘ יורד"
    return "→ יציב"


def export_csv(output_path: Path = None) -> Path:
    import csv
    db   = _load()
    runs = db.get("runs", [])
    if not runs:
        print("אין נתונים לייצוא.")
        return None

    if not output_path:
        output_path = OUTPUT_DIR / f"analytics_{datetime.now().strftime('%Y%m%d')}.csv"

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "topic", "duration_min", "success",
                         "avg_qa", "content_types", "outputs_count", "errors_count"])
        for r in runs:
            writer.writerow([
                r.get("started_at","")[:10],
                r.get("topic",""),
                f"{r.get('duration_s',0)/60:.1f}",
                r.get("success",""),
                r.get("avg_qa",""),
                ",".join(r.get("content_types",[])),
                len(r.get("outputs",[])),
                len(r.get("errors",[])),
            ])

    print(f"✅ יוצא ל: {output_path}")
    return output_path


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if "--csv" in sys.argv:
        export_csv()
    elif "--last" in sys.argv:
        idx = sys.argv.index("--last")
        n   = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 20
        print_report(last_n=n)
    else:
        print_report()
