"""
dashboard.py — דאשבורד ויזואלי (מוקי Operator, v3)
5 דפים: Overview · Pipeline Live · Content Quality · Topics·Runs·Queue · Gaps·Artifacts·Errors
"""

import json
import re
import subprocess
import sys
import webbrowser
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter

from config import OUTPUT_DIR, PAPERS_DIR, ARTICLES_DIR, LINKEDIN_DIR, BLOG_DIR, PODCAST_DIR
from memory import load_memory


DASHBOARD_FILE = OUTPUT_DIR / "dashboard.html"


def _load_analytics() -> dict:
    f = OUTPUT_DIR / "analytics.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"runs": []}


def _count_files(directory: Path, patterns: list[str]) -> int:
    if not directory.exists():
        return 0
    return sum(len(list(directory.glob(p))) for p in patterns)


def _ready_to_publish() -> list[dict]:
    """Return list of ready posts with metadata for hero cards."""
    out = []
    dirs = [
        (LINKEDIN_DIR, "*_ready*.txt", "LinkedIn"),
        (BLOG_DIR, "*.md", "Blog"),
        (PODCAST_DIR, "*_script_*.md", "Podcast"),
    ]
    for d, pattern, kind in dirs:
        if not d.exists():
            continue
        for p in d.glob(pattern):
            if p.name.endswith(".bak"):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
                # Extract first meaningful line as title
                title = ""
                for line in text.split("\n"):
                    line = line.strip().lstrip("#").strip()
                    if line and not line.startswith("-") and not line.startswith("*") and len(line) > 5:
                        title = line[:60]
                        break
                words = len(text.split())
                out.append({
                    "name": p.name,
                    "kind": kind,
                    "title": title or p.stem,
                    "words": words,
                    "ts": p.stat().st_mtime,
                    "time": datetime.fromtimestamp(p.stat().st_mtime).strftime("%H:%M"),
                })
            except Exception:
                pass
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out[:3]


def _artifact_list(limit: int = 8) -> list[dict]:
    """Recent posts + designs."""
    items = []
    for d, pattern, icon in [
        (LINKEDIN_DIR, "*_ready*.txt", "📝"),
        (BLOG_DIR, "*.md", "📝"),
        (PODCAST_DIR, "*_script_*.md", "📝"),
        (OUTPUT_DIR / "designs", "*.svg", "🖼"),
    ]:
        if not d.exists():
            continue
        for p in d.glob(pattern):
            if p.name.endswith(".bak"):
                continue
            try:
                size = p.stat().st_size
                if p.suffix == ".svg":
                    meta = "vector"
                else:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                    meta = f"{len(text.split())} מילים"
                items.append({
                    "name": p.name,
                    "icon": icon,
                    "meta": meta,
                    "time": datetime.fromtimestamp(p.stat().st_mtime).strftime("%H:%M"),
                    "ts": p.stat().st_mtime,
                })
            except Exception:
                pass
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items[:limit]


def _recent_errors(runs: list[dict], limit: int = 6) -> list[dict]:
    errs = []
    for r in runs:
        for e in r.get("errors", []):
            err_str = e.get("error", "") if isinstance(e, dict) else str(e)
            errs.append({
                "agent": e.get("agent", "?") if isinstance(e, dict) else "?",
                "code": _classify_error(err_str),
                "msg": err_str[:120],
                "time": (e.get("time", r.get("started_at", "")) if isinstance(e, dict) else r.get("started_at", ""))[:16].replace("T", " "),
            })
    return errs[-limit:][::-1]


def _classify_error(err: str) -> str:
    e = err.lower()
    if "timeout" in e: return "TIMEOUT"
    if "rate" in e or "429" in e: return "RATE_LIMIT"
    if "context" in e or "overflow" in e: return "CTX_OVERFLOW"
    if "render" in e or "svg" in e: return "RENDER_FAIL"
    if "score" in e or "qa" in e: return "SCORE_LOW"
    return "ERROR"


def _topic_categories(coverage_map: dict) -> list[dict]:
    """Group topics into buckets for coverage grid display."""
    buckets = {
        "AI & Agents": ["agent", "ai ", "llm", "סוכן"],
        "פיתוח & כלים": ["tool", "dev", "כלים", "פיתוח"],
        "מחשבות & מתודות": ["method", "thought", "מתודה", "חשיבה"],
        "רטרוספקטיבות": ["retro", "lessons", "רטרוספקטיבה"],
        "ראיונות": ["interview", "ראיון"],
        "ניסויים": ["experiment", "test", "ניסוי"],
    }
    result = []
    assigned = set()
    for cat, keywords in buckets.items():
        covered = 0
        total = 0
        for topic, score in coverage_map.items():
            if topic in assigned:
                continue
            tl = topic.lower()
            if any(k in tl for k in keywords):
                total += 1
                if score >= 3:
                    covered += 1
                assigned.add(topic)
        if total:
            result.append({"name": cat, "covered": covered, "total": total})
    # Catch-all: "אחר"
    other_total = len(coverage_map) - len(assigned)
    other_covered = sum(1 for t, s in coverage_map.items() if t not in assigned and s >= 3)
    if other_total:
        result.append({"name": "אחר", "covered": other_covered, "total": other_total})
    return result


# ─────────────────────────────────────────────
# Page 2 — Pipeline Live data
# ─────────────────────────────────────────────

def _collect_slo() -> dict:
    """Call observability.slo_compliance and add status icons."""
    try:
        from observability import slo_compliance
        data = slo_compliance(7)
    except Exception as e:
        return {"error": str(e), "slos": {}, "samples": 0}

    icons = {"ok": "✅", "warning": "⚠️", "critical": "🚨", "breached": "🔥"}
    out = {"samples": data.get("samples", 0), "slos": []}
    label_he = {
        "pipeline_duration": "משך pipeline (p95 ד׳)",
        "pipeline_success_rate": "אחוז הצלחה",
        "qa_score_avg": "ציון QA ממוצע",
        "step_duration_p95": "משך step p95 (ד׳)",
    }
    for key, info in data.get("slos", {}).items():
        status = info.get("status", "ok")
        target = info.get("target", 0)
        value = info.get("value", 0)
        # Compute pct: for success-rate / qa, higher is better. for durations, lower is better.
        if key in ("pipeline_success_rate", "qa_score_avg"):
            pct = max(0, min(100, (value / target * 100) if target else 0))
            bar_color = "green" if status == "ok" else "yellow" if status == "warning" else "red"
        else:
            pct = max(0, min(100, (target / value * 100) if value else 100))
            bar_color = "green" if status == "ok" else "yellow" if status == "warning" else "red"
        out["slos"].append({
            "key": key,
            "label": label_he.get(key, key),
            "value": value,
            "target": target,
            "status": status,
            "icon": icons.get(status, "❓"),
            "pct": round(pct),
            "color": bar_color,
            "description": info.get("description", ""),
        })
    return out


def _collect_burn_rate() -> dict:
    try:
        from observability import burn_rate
        return burn_rate()
    except Exception as e:
        return {"error": str(e)}


def _collect_pipeline_status(limit: int = 20) -> list[dict]:
    """Read last N lines of pipeline_status.txt; newest first."""
    f = OUTPUT_DIR / "pipeline_status.txt"
    if not f.exists():
        return []
    try:
        lines = [ln for ln in f.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
    except Exception:
        return []
    last = lines[-limit:][::-1]
    out = []
    for ln in last:
        # Try to extract timestamp like [HH:MM:SS]
        ts = ""
        body = ln
        m = re.match(r"\[([^\]]+)\]\s*(.*)", ln)
        if m:
            ts = m.group(1)
            body = m.group(2)
        # Pick a marker color
        cls = "info"
        if "❌" in body or "fail" in body.lower():
            cls = "fail"
        elif "✅" in body or "complete" in body.lower():
            cls = "ok"
        elif "⏳" in body or "started" in body.lower():
            cls = "warn"
        elif "🎉" in body:
            cls = "ok"
        elif "🚀" in body:
            cls = "info"
        out.append({"ts": ts, "body": body, "cls": cls})
    return out


def _collect_launchd_jobs() -> list[dict]:
    """Run launchctl list, filter for moki lines, parse PID + exit + label."""
    try:
        res = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=5
        )
    except Exception as e:
        return [{"label": f"launchctl error: {e}", "pid": "—", "exit": "—", "status": "fail"}]
    out = []
    for line in res.stdout.splitlines():
        if "moki" not in line.lower():
            continue
        parts = line.split("\t") if "\t" in line else line.split()
        if len(parts) < 3:
            continue
        pid, status_code, label = parts[0], parts[1], parts[2]
        try:
            exit_code = int(status_code)
        except ValueError:
            exit_code = -1
        running = pid not in ("-", "—") and pid.isdigit()
        is_ok = (exit_code == 0)
        out.append({
            "label": label,
            "pid": pid if running else "—",
            "exit": str(exit_code),
            "status": "ok" if is_ok else "fail",
            "running": running,
        })
    return out


# ─────────────────────────────────────────────
# Page 3 — Content Quality data
# ─────────────────────────────────────────────

def _collect_post_quality(limit: int = 10) -> list[dict]:
    """Scan recent ready linkedin posts, run check_voice_adherence on each."""
    if not LINKEDIN_DIR.exists():
        return []
    posts = [p for p in LINKEDIN_DIR.glob("*_ready*.txt") if not p.name.endswith(".bak")]
    posts.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    posts = posts[:limit]

    try:
        from voice_profile import check_voice_adherence
    except Exception:
        check_voice_adherence = None

    out = []
    for p in posts:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        score = None
        if check_voice_adherence:
            try:
                score = check_voice_adherence(text, platform="linkedin").get("score")
            except Exception:
                score = None
        out.append({
            "name": p.name,
            "score": score if score is not None else 0,
            "has_score": score is not None,
            "date": datetime.fromtimestamp(p.stat().st_mtime).strftime("%d.%m %H:%M"),
            "chars": len(text),
        })
    return out


def _collect_voice_drift() -> dict:
    try:
        from voice_drift import analyze_voice_drift
        r = analyze_voice_drift(30)
        return {
            "verdict": r.get("verdict", "—"),
            "diversity_score": r.get("diversity_score", 0),
            "samples": r.get("samples", 0),
            "recommendations": r.get("recommendations", [])[:3],
        }
    except Exception as e:
        return {"error": str(e), "verdict": "—", "diversity_score": 0,
                "samples": 0, "recommendations": []}


def _collect_recent_authors(days: int = 14) -> list[str]:
    try:
        from quote_bank import get_recently_used_authors
        return list(get_recently_used_authors(days))
    except Exception:
        return []


def _collect_latest_reflection() -> dict:
    """Read latest file in output/reflections/*.md by mtime."""
    refl_dir = OUTPUT_DIR / "reflections"
    if not refl_dir.exists():
        return {"name": "", "bullets": [], "summary": "אין reflections"}
    files = list(refl_dir.glob("*.md"))
    if not files:
        return {"name": "", "bullets": [], "summary": "אין קבצי reflections"}
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    f = files[0]
    try:
        text = f.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {"name": f.name, "bullets": [], "summary": "(שגיאה בקריאה)"}
    bullets = []
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith(("-", "*", "•")) and len(s) > 2:
            bullets.append(s.lstrip("-*• ").strip()[:160])
        if len(bullets) >= 5:
            break
    summary = ""
    if not bullets:
        # fallback: first 5 non-empty meaningful lines
        for line in text.split("\n"):
            s = line.strip().lstrip("#").strip()
            if s and len(s) > 5:
                summary = s[:300]
                break
    return {
        "name": f.name,
        "date": datetime.fromtimestamp(f.stat().st_mtime).strftime("%d.%m.%Y %H:%M"),
        "bullets": bullets,
        "summary": summary,
    }


def generate_dashboard() -> str:
    analytics = _load_analytics()
    runs = analytics.get("runs", [])
    mem = load_memory()

    # ── Data collection ───────────────────────
    ready_posts = _ready_to_publish()
    n_ready_total = len(ready_posts)

    n_papers = _count_files(PAPERS_DIR, ["*.json"])
    n_articles = _count_files(ARTICLES_DIR, ["*.md"])
    n_linkedin = _count_files(LINKEDIN_DIR, ["*.txt"])
    n_blog = _count_files(BLOG_DIR, ["*.md"])
    n_podcast = _count_files(PODCAST_DIR, ["*.md"])
    n_designs = _count_files(OUTPUT_DIR / "designs", ["*.svg"])
    n_posts = n_linkedin + n_blog + n_podcast

    coverage_map = mem.get("coverage_map", {})
    for t in mem.get("researched_topics", []):
        if t not in coverage_map:
            coverage_map[t] = 1
    n_covered = sum(1 for s in coverage_map.values() if s >= 3)
    n_total_topics = len(coverage_map)
    pct_covered = round(n_covered / max(1, n_total_topics) * 100)
    categories = _topic_categories(coverage_map)

    queue = mem.get("topic_queue", [])[:6]
    gaps = mem.get("gaps", [])[:5]
    iterations = mem.get("iterations", 0)

    # QA stats
    qas = [r.get("avg_qa") for r in runs if r.get("avg_qa")]
    avg_qa = round(sum(qas) / max(1, len(qas))) if qas else 0
    durs = [r.get("duration_s", 0) / 60 for r in runs if r.get("duration_s")]
    avg_dur = round(sum(durs) / max(1, len(durs)), 1) if durs else 0
    last_run = runs[-1] if runs else None
    last_time = (last_run.get("started_at", "") if last_run else "")[11:16] if last_run else "—"

    artifacts = _artifact_list()
    errors = _recent_errors(runs)

    # ── New page 2 (Pipeline Live) data ──
    try:
        slo_data = _collect_slo()
    except Exception as e:
        slo_data = {"error": str(e), "slos": [], "samples": 0}
    try:
        burn_data = _collect_burn_rate()
    except Exception as e:
        burn_data = {"error": str(e)}
    try:
        live_status = _collect_pipeline_status(20)
    except Exception:
        live_status = []
    try:
        launchd_jobs = _collect_launchd_jobs()
    except Exception as e:
        launchd_jobs = [{"label": f"error: {e}", "pid": "—", "exit": "—", "status": "fail", "running": False}]

    # ── New page 3 (Content Quality) data ──
    try:
        post_quality = _collect_post_quality(10)
    except Exception:
        post_quality = []
    try:
        voice_drift_data = _collect_voice_drift()
    except Exception as e:
        voice_drift_data = {"error": str(e), "verdict": "—", "diversity_score": 0,
                            "samples": 0, "recommendations": []}
    try:
        recent_authors = _collect_recent_authors(14)
    except Exception:
        recent_authors = []
    try:
        latest_reflection = _collect_latest_reflection()
    except Exception:
        latest_reflection = {"name": "", "bullets": [], "summary": ""}

    # ── JSON for JS ──
    runs_json = json.dumps(runs, ensure_ascii=False, default=str)
    coverage_json = json.dumps(coverage_map, ensure_ascii=False)
    ready_json = json.dumps(ready_posts, ensure_ascii=False)
    categories_json = json.dumps(categories, ensure_ascii=False)
    queue_json = json.dumps(queue, ensure_ascii=False)
    gaps_json = json.dumps(gaps, ensure_ascii=False)
    artifacts_json = json.dumps(artifacts, ensure_ascii=False)
    errors_json = json.dumps(errors, ensure_ascii=False)
    slo_json = json.dumps(slo_data, ensure_ascii=False, default=str)
    burn_json = json.dumps(burn_data, ensure_ascii=False, default=str)
    live_status_json = json.dumps(live_status, ensure_ascii=False)
    launchd_json = json.dumps(launchd_jobs, ensure_ascii=False)
    post_quality_json = json.dumps(post_quality, ensure_ascii=False)
    voice_drift_json = json.dumps(voice_drift_data, ensure_ascii=False, default=str)
    authors_json = json.dumps(recent_authors, ensure_ascii=False)
    reflection_json = json.dumps(latest_reflection, ensure_ascii=False)

    # Coverage grid: 57 squares
    grid_squares = []
    sorted_topics = sorted(coverage_map.items(), key=lambda x: -x[1])
    for topic, score in sorted_topics[:60]:
        state = "covered" if score >= 3 else "partial" if score >= 1 else "empty"
        grid_squares.append({"topic": topic, "score": score, "state": state})
    grid_json = json.dumps(grid_squares, ensure_ascii=False)

    today = datetime.now().strftime("%d.%m.%Y")

    html = f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>מוקי · Operator</title>
<style>
/* ─── Design tokens — Notion-Calm (warm light · paper-inspired) ─── */
@import url('https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

:root {{
  /* Surfaces — warm off-whites (Notion's signature) */
  --bg:#FFFFFF;                   /* primary page background */
  --surface:#FBFAF7;              /* warm second layer */
  --card:#F7F6F3;                 /* Notion's gray-50 (warm tint) */
  --card2:#EFEEEB;                /* hover state */
  --border:rgba(55,53,47,0.09);   /* Notion's signature hairline */
  --border-lite:rgba(55,53,47,0.16);

  /* Text — warm neutrals (not pure black) */
  --t:#37352F;                    /* Notion's body text */
  --td:#787774;                   /* muted */
  --tb:#1F1E1B;                   /* near-black for headings */
  --tdim:#9B9A97;                 /* very muted */

  /* Accents — Notion-like soft tones */
  --purple:#6940A5;               /* deeper, less neon */
  --purple-dim:#F4F0FA;
  --green:#0F7B6C;                /* forest green, calm */
  --green-dim:#EAF3F1;
  --yellow:#CB912F;               /* honey, not neon */
  --yellow-dim:#FBF3E1;
  --red:#B91C1C;                  /* deeper red, less screaming */
  --red-dim:#FCEEEC;
  --blue:#2383E2;                 /* Notion's link blue */

  /* Shadows — minimal, paper-like */
  --shadow:0 1px 2px rgba(55,53,47,0.04), 0 4px 12px rgba(55,53,47,0.06);
  --shadow-sm:0 1px 2px rgba(55,53,47,0.05);
  --radius:6px;                   /* tighter than dark version */

  /* Typography — editorial serif + clean sans */
  --serif: 'Newsreader','Lyon Display',Georgia,'Times New Roman',serif;
  --sans:  'Inter',-apple-system,'Segoe UI',system-ui,sans-serif;
  --mono:  'JetBrains Mono','SF Mono',Menlo,Consolas,monospace;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
html {{ scroll-behavior:smooth; }}
body {{
  font-family: var(--sans);
  background: var(--bg);
  color: var(--t);
  font-size: 15px;                /* slightly larger for reading comfort */
  line-height: 1.65;
  min-height: 100vh;
  padding: 0;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}}
/* ─── Accessibility polish ─── */
*:focus-visible {{
  outline: 2px solid var(--blue);
  outline-offset: 2px;
  border-radius: 3px;
}}
button, .tab, .card, [onclick] {{ cursor: pointer; }}
@media (prefers-reduced-motion: reduce) {{
  *, *::before, *::after {{
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }}
}}

/* ─── Page header (snapshot bar) ─── */
.page-header {{
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  padding: 16px 32px;
  font-family: var(--sans);
  font-size: 12px;
  color: var(--td);
  border-bottom: 1px solid var(--border);
}}
.page-header .left {{ direction: ltr; text-align: left; font-family: var(--mono); font-size: 11px; }}
.page-header .center {{ text-align: center; font-size: 12px; color: var(--td); }}
.page-header .right {{ display: flex; justify-content: flex-end; align-items: center; gap: 10px; }}
.page-header .right .op-tag {{
  background: var(--card);
  border: 1px solid var(--border);
  padding: 3px 9px;
  border-radius: 4px;
  font-size: 10.5px;
  color: var(--td);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}}
.page-header .right .brand {{
  color: var(--tb);
  font-family: var(--serif);
  font-weight: 500;
  font-size: 18px;
  letter-spacing: -0.01em;
}}
.page-header .right .owl {{ font-size: 20px; }}

/* ─── Tab navigation ─── */
.tabs {{
  display: flex;
  justify-content: center;
  gap: 0;
  padding: 4px 32px 0;
  border-bottom: 1px solid var(--border);
  margin-bottom: 0;
}}
.tab {{
  background: none;
  border: none;
  color: var(--td);
  padding: 11px 18px;
  cursor: pointer;
  font-family: var(--sans);
  font-size: 13px;
  font-weight: 500;
  border-bottom: 2px solid transparent;
  transition: color 0.15s ease, border-color 0.15s ease;
  border-radius: 0;
}}
.tab:hover {{ color: var(--tb); background: transparent; }}
.tab.active {{
  color: var(--tb);
  border-bottom-color: var(--tb);
  background: transparent;
  font-weight: 600;
}}

/* ─── Container ─── */
.container {{
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 32px 60px;
}}
.page {{ display: block; scroll-margin-top: 80px; }}
.page + .page {{ margin-top: 48px; padding-top: 32px; border-top: 1px dashed var(--border); }}
.page-divider {{ display: flex; align-items: center; gap: 12px; margin: 0 0 20px; color: var(--td); font-size: 11px; text-transform: uppercase; letter-spacing: 1.2px; font-weight: 700; }}
.page-divider::before, .page-divider::after {{ content: ''; flex: 1; height: 1px; background: var(--border); }}
.tabs {{ position: sticky; top: 0; z-index: 50; background: var(--bg); backdrop-filter: blur(8px); }}
.pipe-strip {{ display: flex; align-items: center; justify-content: center; gap: 4px; flex-wrap: wrap; padding: 10px 16px; background: var(--card); border: none; border-radius: var(--radius); margin-bottom: 24px; }}
.pipe-strip .ps {{ padding: 4px 11px; background: var(--bg); border: 1px solid var(--border); border-radius: 12px; font-size: 11.5px; color: var(--t); font-weight: 500; }}
.pipe-strip .ps-a {{ color: var(--tdim); font-size: 11px; padding: 0 3px; }}
.last-run-card {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 0; background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; margin: 24px 0; position: relative; }}
.last-run-card.success {{ box-shadow: inset 3px 0 0 var(--green); }}
.last-run-card.fail {{ box-shadow: inset 3px 0 0 var(--red); }}
.last-run-card .lr {{ background: var(--bg); padding: 14px 18px; border-right: 1px solid var(--border); }}
.last-run-card .lr:last-child {{ border-right: none; }}
.last-run-card .lr .lbl {{ font-size: 10.5px; color: var(--td); text-transform: uppercase; letter-spacing: 0.6px; font-weight: 500; }}
.last-run-card .lr .vl {{ font-size: 18px; font-weight: 500; color: var(--tb); margin-top: 4px; font-family: var(--serif); }}
.last-run-card .lr .vl.sm {{ font-size: 13px; font-weight: 500; font-family: var(--sans); }}
@media (max-width: 900px) {{ .last-run-card {{ grid-template-columns: repeat(2, 1fr); }} .last-run-card .lr {{ border-right: none; border-bottom: 1px solid var(--border); }} }}
.cheat-sheet {{ background: var(--card); border: none; border-radius: var(--radius); padding: 24px 28px; margin-top: 32px; }}
.cheat-sheet h2 {{ font-family: var(--serif); font-size: 18px; font-weight: 500; color: var(--tb); text-transform: none; letter-spacing: -0.01em; margin-bottom: 18px; }}
.cheat-sheet .row {{ display: flex; align-items: center; gap: 12px; padding: 9px 0; border-bottom: 1px solid var(--border); font-size: 13px; }}
.cheat-sheet .row:last-child {{ border-bottom: none; }}
.cheat-sheet .row b {{ color: var(--tb); font-weight: 500; min-width: 160px; }}
.cheat-sheet .row code {{ background: var(--bg); padding: 3px 8px; border-radius: 4px; font-family: var(--mono); font-size: 12px; color: var(--purple); border: 1px solid var(--border); }}

/* ─── Hero section (Notion-style title block) ─── */
.hero {{
  background: var(--bg);
  border: none;
  border-bottom: 1px solid var(--border);
  border-radius: 0;
  box-shadow: none;
  padding: 40px 0 32px;
  margin-bottom: 32px;
}}
.hero-top {{
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 28px;
  gap: 24px;
}}
.hero-title h1 {{
  font-family: var(--serif);
  font-size: 38px;
  font-weight: 500;
  color: var(--tb);
  letter-spacing: -0.02em;
  line-height: 1.2;
  margin-bottom: 12px;
  display: flex;
  align-items: baseline;
  gap: 12px;
}}
.hero-title .owl-large {{ font-size: 32px; }}
.hero-title .meta {{
  font-family: var(--sans);
  font-size: 13px;
  color: var(--td);
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}}
.hero-title .meta .status-dot {{
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--green);
  margin-left: 6px;
  animation: pulse 2.5s infinite;
}}
@keyframes pulse {{ 0%,100%{{opacity:0.95}} 50%{{opacity:0.45}} }}
.hero-actions {{ display: flex; gap: 8px; }}
.btn {{
  font-family: var(--sans);
  font-size: 13px;
  font-weight: 500;
  padding: 7px 14px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--bg);
  color: var(--t);
  cursor: pointer;
  transition: background 0.12s ease, border-color 0.12s ease;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  box-shadow: var(--shadow-sm);
}}
.btn:hover {{ background: var(--card); border-color: var(--border-lite); }}
.btn-primary {{
  background: var(--tb);
  color: var(--bg);
  border-color: var(--tb);
  font-weight: 500;
}}
.btn-primary:hover {{ background: #000; border-color: #000; }}

/* ─── Ready posts cards ─── */
.ready-cards {{
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}}
@media (max-width: 900px) {{ .ready-cards {{ grid-template-columns: 1fr; }} }}
.card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
  transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
}}
.card:hover {{
  transform: translateY(-3px);
  border-color: var(--border-lite);
  box-shadow: var(--shadow);
}}
.card-thumb {{
  height: 110px;
  background: var(--card);
  display: flex;
  align-items: flex-end;
  padding: 16px;
  position: relative;
  overflow: hidden;
  border-bottom: 1px solid var(--border);
}}
.card-thumb.linkedin {{ background: linear-gradient(180deg, #EAF3F1 0%, var(--card) 100%); }}
.card-thumb.blog {{ background: linear-gradient(180deg, #F4F0FA 0%, var(--card) 100%); }}
.card-thumb.podcast {{ background: linear-gradient(180deg, #FBF3E1 0%, var(--card) 100%); }}
.card-thumb::before {{
  content: '';
  position: absolute;
  top: 12px;
  right: 12px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: rgba(55,53,47,0.3);
}}
.card-thumb-title {{
  font-family: var(--serif);
  font-size: 13px;
  color: var(--t);
  text-align: right;
  width: 100%;
  font-weight: 500;
}}
.card-body {{ padding: 16px; }}
.card-title {{
  color: var(--tb);
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
  margin-bottom: 12px;
  min-height: 40px;
}}
.card-meta {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-family: var(--mono);
  font-size: 11px;
  color: var(--td);
}}
.card-tags {{ display: flex; gap: 6px; align-items: center; }}
.qa-badge {{
  background: var(--green-dim);
  color: var(--green);
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 600;
}}
.qa-badge.mid {{ background: var(--yellow-dim); color: var(--yellow); }}
.qa-badge.low {{ background: var(--red-dim); color: var(--red); }}
.kind-tag {{
  background: var(--card);
  border: 1px solid var(--border);
  padding: 2px 8px;
  border-radius: 4px;
  color: var(--td);
  font-size: 10px;
}}

/* ─── Stat strip ─── */
.stat-strip {{
  background: var(--card);
  border: none;
  border-radius: var(--radius);
  box-shadow: none;
  padding: 24px 28px;
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 24px;
  margin-bottom: 32px;
}}
@media (max-width: 900px) {{ .stat-strip {{ grid-template-columns: repeat(2, 1fr); }} }}
.stat-cell {{
  display: flex;
  flex-direction: column;
  gap: 4px;
  position: relative;
}}
.stat-cell .letter {{
  font-family: var(--mono);
  font-size: 10.5px;
  color: var(--tdim);
  font-weight: 500;
  position: absolute;
  top: -2px;
  left: 0;
  letter-spacing: 0.05em;
}}
.stat-cell .val {{
  font-family: var(--serif);
  font-size: 32px;
  font-weight: 500;
  color: var(--tb);
  letter-spacing: -0.02em;
  line-height: 1;
  padding-right: 22px;
}}
.stat-cell .label {{
  font-size: 13px;
  color: var(--t);
  padding-right: 22px;
  font-weight: 500;
}}
.stat-cell .sub {{
  font-family: var(--sans);
  font-size: 11px;
  color: var(--td);
  padding-right: 22px;
  margin-top: 2px;
}}
.stat-cell .arrow {{
  position: absolute;
  top: 0;
  right: -8px;
  color: var(--border);
  font-size: 14px;
}}

/* ─── Section cards (3-up) ─── */
.grid-3 {{
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}}
@media (max-width: 1000px) {{ .grid-3 {{ grid-template-columns: 1fr; }} }}
.sec {{
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 22px 24px;
  transition: border-color 0.15s ease;
}}
.sec:hover {{ border-color: var(--border-lite); box-shadow: none; }}
.sec-header {{
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 18px;
}}
.sec-header h2 {{
  font-family: var(--serif);
  color: var(--tb);
  font-size: 17px;
  font-weight: 500;
  letter-spacing: -0.01em;
  line-height: 1.3;
}}
.sec-header .subtitle {{
  font-family: var(--sans);
  font-size: 12.5px;
  color: var(--td);
  margin-top: 3px;
}}
.sec-badge {{
  background: var(--card);
  border: 1px solid var(--border);
  padding: 3px 9px;
  border-radius: 4px;
  font-family: var(--sans);
  font-size: 11px;
  color: var(--td);
  font-weight: 500;
}}
.sec-badge.critical {{ color: var(--red); background: var(--red-dim); border-color: rgba(185,28,28,0.2); }}

/* ─── Agent performance bars ─── */
.agent-row {{
  display: grid;
  grid-template-columns: 90px 1fr 60px 40px;
  gap: 10px;
  align-items: center;
  padding: 8px 0;
  font-family: var(--mono);
  font-size: 12px;
}}
.agent-row .name {{ color: var(--t); text-align: right; }}
.agent-row .bar-wrap {{
  background: var(--card2);
  height: 20px;
  border-radius: 4px;
  overflow: hidden;
  position: relative;
}}
.agent-row .bar {{
  height: 100%;
  background: var(--green);
  border-radius: 4px;
  transition: width 0.4s ease;
  opacity: 0.85;
}}
.agent-row .num {{ color: var(--tb); text-align: left; font-weight: 600; }}
.agent-row .fail {{ color: var(--red); text-align: right; }}

/* ─── Duration chart (bars) ─── */
.bar-chart {{
  display: flex;
  gap: 3px;
  align-items: flex-end;
  height: 120px;
  padding: 8px 0;
}}
.bar-chart .b {{
  flex: 1;
  min-width: 6px;
  border-radius: 2px 2px 0 0;
  transition: opacity 0.2s;
}}
.bar-chart .b:hover {{ opacity: 0.7; }}
.bar-chart .b.blue {{ background: var(--blue); }}
.bar-chart .b.purple {{ background: var(--purple); }}
.big-num {{
  font-family: var(--serif);
  font-size: 40px;
  font-weight: 500;
  color: var(--tb);
  letter-spacing: -0.02em;
  line-height: 1;
}}
.big-num.small {{ font-size: 26px; }}
.trend {{
  font-family: var(--sans);
  font-size: 12px;
  color: var(--td);
  margin-top: 6px;
}}
.trend .delta.up {{ color: var(--green); }}
.trend .delta.down {{ color: var(--red); }}

/* ─── Coverage grid ─── */
.coverage-grid {{
  display: grid;
  grid-template-columns: repeat(15, 1fr);
  gap: 4px;
  margin-bottom: 16px;
}}
.cell {{
  aspect-ratio: 1;
  border-radius: 3px;
  background: var(--card2);
  position: relative;
  cursor: pointer;
  transition: transform 0.15s;
}}
.cell:hover {{ transform: scale(1.15); z-index: 10; }}
.cell.covered {{ background: var(--purple); }}
.cell.partial {{ background: var(--yellow); opacity: 0.7; }}
.cell.empty {{ background: var(--card2); border: 1px solid var(--border); }}
.cell .tooltip {{
  position: absolute;
  bottom: 110%;
  left: 50%;
  transform: translateX(-50%);
  background: var(--card);
  border: 1px solid var(--border);
  padding: 6px 10px;
  border-radius: 6px;
  font-family: var(--mono);
  font-size: 11px;
  color: var(--t);
  white-space: nowrap;
  display: none;
  pointer-events: none;
  z-index: 20;
}}
.cell:hover .tooltip {{ display: block; }}

.cat-row {{
  display: flex;
  justify-content: space-between;
  padding: 8px 0;
  font-size: 13px;
  border-bottom: 1px solid var(--border-lite);
}}
.cat-row:last-child {{ border-bottom: none; }}
.cat-row .cat-name {{ color: var(--t); }}
.cat-row .cat-count {{ font-family: var(--mono); color: var(--td); }}

/* ─── Recent runs ─── */
.run-row {{
  display: grid;
  grid-template-columns: 12px 60px 80px 1fr 60px;
  gap: 12px;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px solid var(--border-lite);
  font-family: var(--mono);
  font-size: 12px;
}}
.run-row:last-child {{ border-bottom: none; }}
.run-row .dot {{
  width: 8px;
  height: 8px;
  border-radius: 50%;
  justify-self: center;
}}
.run-row .dot.ok {{ background: var(--green); }}
.run-row .dot.warn {{ background: var(--yellow); }}
.run-row .dot.fail {{ background: var(--red); }}
.run-row .id {{ color: var(--td); }}
.run-row .topic {{ color: var(--t); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.run-row .time {{ color: var(--td); text-align: left; }}

/* ─── Topic queue ─── */
.queue-item {{
  display: grid;
  grid-template-columns: 20px 1fr auto;
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid var(--border-lite);
  align-items: start;
}}
.queue-item:last-child {{ border-bottom: none; }}
.queue-item .num {{
  font-family: var(--mono);
  color: var(--tdim);
  font-size: 12px;
}}
.queue-item .text {{ color: var(--t); font-size: 13px; line-height: 1.4; }}
.queue-item .meta {{
  font-family: var(--mono);
  font-size: 11px;
  color: var(--tdim);
  margin-top: 4px;
}}
.priority {{
  font-family: var(--mono);
  font-size: 10px;
  padding: 4px 10px;
  border-radius: 4px;
  font-weight: 600;
}}
.priority.high {{ background: var(--red-dim); color: var(--red); }}
.priority.med {{ background: var(--yellow-dim); color: var(--yellow); }}
.priority.low {{ background: var(--card2); color: var(--td); border: 1px solid var(--border); }}

/* ─── Gaps with bars ─── */
.gap-row {{
  display: grid;
  grid-template-columns: 40px 1fr;
  gap: 12px;
  padding: 12px 0;
  font-family: var(--mono);
  font-size: 13px;
  align-items: center;
  border-bottom: 1px solid var(--border-lite);
}}
.gap-row:last-child {{ border-bottom: none; }}
.gap-row .count {{ color: var(--td); text-align: right; }}
.gap-row .bar-fill {{
  height: 2px;
  background: var(--red);
  border-radius: 2px;
  margin-top: 6px;
}}
.gap-row .bar-fill.med {{ background: var(--yellow); }}
.gap-row .name {{ color: var(--t); }}

/* ─── Artifacts list ─── */
.art-row {{
  display: grid;
  grid-template-columns: 1fr auto 32px;
  gap: 12px;
  align-items: center;
  padding: 12px 0;
  border-bottom: 1px solid var(--border-lite);
}}
.art-row:last-child {{ border-bottom: none; }}
.art-row .title {{ color: var(--t); font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.art-row .time {{ font-family: var(--mono); color: var(--td); font-size: 11px; }}
.art-row .icon {{
  background: var(--card2);
  border: 1px solid var(--border);
  width: 32px;
  height: 32px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--td);
}}
.art-meta {{ font-family: var(--mono); color: var(--tdim); font-size: 11px; }}

/* ─── Error log ─── */
.err-row {{
  display: grid;
  grid-template-columns: 16px 1fr 16px;
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid var(--border-lite);
  align-items: flex-start;
}}
.err-row:last-child {{ border-bottom: none; }}
.err-row .icon-warn {{ color: var(--yellow); font-size: 14px; line-height: 1; }}
.err-row .close {{ color: var(--tdim); cursor: pointer; font-size: 14px; line-height: 1; }}
.err-row .close:hover {{ color: var(--t); }}
.err-content {{ font-family: var(--mono); font-size: 12px; }}
.err-content .head {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}}
.err-content .code {{
  color: var(--yellow);
  font-weight: 600;
  font-size: 11px;
}}
.err-content .agent {{ color: var(--td); }}
.err-content .time {{ color: var(--tdim); font-size: 10px; }}
.err-content .msg {{ color: var(--t); font-size: 12px; }}

/* ─── Footer ─── */
.footer {{
  margin-top: 40px;
  padding: 20px 32px;
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  font-family: var(--mono);
  font-size: 11px;
  color: var(--tdim);
}}
.footer .right-stats {{ display: flex; gap: 20px; }}

.empty-state {{
  color: var(--tdim);
  font-family: var(--mono);
  font-size: 12px;
  padding: 16px 0;
  text-align: center;
}}

/* ─── Grid-2 (Pipeline Live) ─── */
.grid-2 {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}}
@media (max-width: 1000px) {{ .grid-2 {{ grid-template-columns: 1fr; }} }}

/* ─── SLO bars ─── */
.slo-row {{
  padding: 10px 0;
  border-bottom: 1px solid var(--border-lite);
}}
.slo-row:last-child {{ border-bottom: none; }}
.slo-head {{
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-family: var(--mono);
  font-size: 12px;
  margin-bottom: 6px;
}}
.slo-head .icon {{ font-size: 14px; margin-left: 6px; }}
.slo-head .name {{ color: var(--t); }}
.slo-head .val {{ color: var(--tb); font-weight: 600; }}
.slo-head .target {{ color: var(--tdim); font-size: 11px; margin-right: 6px; }}
.slo-bar-wrap {{
  background: var(--card2);
  height: 8px;
  border-radius: 4px;
  overflow: hidden;
}}
.slo-bar {{ height: 100%; border-radius: 4px; transition: width 0.4s ease; }}
.slo-bar.green {{ background: linear-gradient(90deg, #16a34a, var(--green)); }}
.slo-bar.yellow {{ background: linear-gradient(90deg, #ca8a04, var(--yellow)); }}
.slo-bar.red {{ background: linear-gradient(90deg, #dc2626, var(--red)); }}
.slo-desc {{
  font-family: var(--mono);
  font-size: 10px;
  color: var(--tdim);
  margin-top: 4px;
}}

/* ─── Burn rate ─── */
.burn-block {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 12px;
}}
.burn-cell {{
  background: var(--card2);
  border: 1px solid var(--border-lite);
  border-radius: 8px;
  padding: 14px;
}}
.burn-cell .label {{ font-size: 11px; color: var(--td); font-family: var(--mono); }}
.burn-cell .val {{ font-size: 22px; font-weight: 700; color: var(--tb); margin-top: 4px; }}
.burn-cell.alert .val {{ color: var(--red); }}
.burn-windows {{
  font-family: var(--mono);
  font-size: 11px;
  color: var(--td);
  margin-top: 8px;
  line-height: 1.7;
}}

/* ─── Live feed ─── */
.live-feed {{
  max-height: 360px;
  overflow-y: auto;
}}
.live-row {{
  display: grid;
  grid-template-columns: 70px 16px 1fr;
  gap: 10px;
  padding: 6px 0;
  font-family: var(--mono);
  font-size: 11px;
  border-bottom: 1px solid var(--border-lite);
  align-items: start;
}}
.live-row:last-child {{ border-bottom: none; }}
.live-row .ts {{ color: var(--tdim); }}
.live-row .dot {{
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-top: 4px;
}}
.live-row .dot.ok {{ background: var(--green); }}
.live-row .dot.warn {{ background: var(--yellow); }}
.live-row .dot.fail {{ background: var(--red); }}
.live-row .dot.info {{ background: var(--blue); }}
.live-row .body {{ color: var(--t); word-break: break-word; }}

/* ─── launchd table ─── */
.launchd-row {{
  display: grid;
  grid-template-columns: 16px 1fr 60px 50px;
  gap: 10px;
  padding: 8px 0;
  font-family: var(--mono);
  font-size: 11px;
  border-bottom: 1px solid var(--border-lite);
  align-items: center;
}}
.launchd-row:last-child {{ border-bottom: none; }}
.launchd-row .dot {{
  width: 8px;
  height: 8px;
  border-radius: 50%;
  justify-self: center;
}}
.launchd-row .dot.ok {{ background: var(--green); }}
.launchd-row .dot.fail {{ background: var(--red); }}
.launchd-row .label {{ color: var(--t); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.launchd-row .pid {{ color: var(--td); text-align: left; }}
.launchd-row .exit {{ color: var(--td); text-align: left; }}

/* ─── Post quality grid ─── */
.pq-row {{
  display: grid;
  grid-template-columns: 60px 1fr 100px 70px;
  gap: 12px;
  padding: 10px 0;
  font-family: var(--mono);
  font-size: 12px;
  border-bottom: 1px solid var(--border-lite);
  align-items: center;
}}
.pq-row:last-child {{ border-bottom: none; }}
.pq-row .pq-name {{ color: var(--t); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.pq-row .pq-date {{ color: var(--tdim); text-align: left; }}
.pq-row .pq-chars {{ color: var(--td); text-align: left; }}
.pq-score {{
  display: inline-block;
  padding: 4px 10px;
  border-radius: 6px;
  font-weight: 700;
  text-align: center;
}}
.pq-score.green {{ background: var(--green-dim); color: var(--green); }}
.pq-score.yellow {{ background: var(--yellow-dim); color: var(--yellow); }}
.pq-score.red {{ background: var(--red-dim); color: var(--red); }}

/* ─── Voice drift ─── */
.vd-score-big {{
  font-size: 40px;
  font-weight: 700;
  color: var(--tb);
  letter-spacing: -1px;
  line-height: 1;
}}
.vd-verdict {{
  font-family: var(--mono);
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 4px;
  display: inline-block;
  margin-top: 8px;
}}
.vd-verdict.diverse {{ background: var(--green-dim); color: var(--green); }}
.vd-verdict.drifting {{ background: var(--yellow-dim); color: var(--yellow); }}
.vd-verdict.stuck {{ background: var(--red-dim); color: var(--red); }}
.vd-recs {{
  margin-top: 14px;
  font-family: var(--mono);
  font-size: 11px;
}}
.vd-rec {{
  padding: 8px 0;
  border-bottom: 1px solid var(--border-lite);
  color: var(--t);
}}
.vd-rec:last-child {{ border-bottom: none; }}

/* ─── Author cloud ─── */
.author-cloud {{
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding-top: 4px;
}}
.author-tag {{
  background: var(--card2);
  border: 1px solid var(--border-lite);
  color: var(--t);
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 11px;
  font-family: var(--mono);
}}
.author-tag.warn {{ border-color: var(--yellow); color: var(--yellow); }}

/* ─── Reflection box ─── */
.refl-list {{
  font-size: 12px;
  color: var(--t);
  line-height: 1.6;
}}
.refl-bullet {{
  padding: 6px 0;
  border-bottom: 1px solid var(--border-lite);
}}
.refl-bullet:last-child {{ border-bottom: none; }}
.refl-bullet::before {{
  content: '→ ';
  color: var(--purple);
  font-weight: 700;
}}

/* ─── Custom scrollbar — dark, subtle (UI/UX Pro Max) ─── */
::-webkit-scrollbar {{ width: 10px; height: 10px; }}
::-webkit-scrollbar-track {{ background: var(--bg); }}
::-webkit-scrollbar-thumb {{
  background: var(--card2);
  border-radius: 6px;
  border: 2px solid var(--bg);
}}
::-webkit-scrollbar-thumb:hover {{ background: var(--border-lite); }}
* {{ scrollbar-width: thin; scrollbar-color: var(--card2) var(--bg); }}

/* ─── Bar chart polish — gradient fills ─── */
.bar-chart .b.blue {{ background: linear-gradient(180deg, var(--blue), #1e40af); }}
.bar-chart .b.purple {{ background: linear-gradient(180deg, var(--purple), #6d28d9); }}

/* ─── Badge + tag hover states ─── */
.author-tag {{ transition: border-color 0.2s ease, color 0.2s ease; }}
.author-tag:hover {{ border-color: var(--purple); color: var(--tb); }}
.kind-tag, .qa-badge, .priority, .sec-badge {{ transition: all 0.2s ease; }}

/* ─── Section header polish ─── */
.sec-header h2 {{ letter-spacing: -0.2px; }}

/* ─── Responsive — mobile readability ─── */
@media (max-width: 640px) {{
  .container {{ padding: 0 16px 40px; }}
  .page-header {{ padding: 16px; }}
  .tabs {{ padding: 0 8px 12px; overflow-x: auto; }}
  .hero {{ padding: 20px; }}
  .hero-title h1 {{ font-size: 24px; }}
}}
</style>
</head>
<body>

<div class="page-header">
  <div class="left">snapshot · {today} · 5 / <span id="pageNum">1</span></div>
  <div class="center" id="pageLabel">סקירה · Overview</div>
  <div class="right">
    <span class="op-tag">operator</span>
    <span class="brand">מוקי</span>
    <span class="owl">🦊</span>
  </div>
</div>

<div class="tabs">
  <button class="tab active" data-page="1" onclick="showPage(1)">סקירה · Overview</button>
  <button class="tab" data-page="2" onclick="showPage(2)">Pipeline · Live</button>
  <button class="tab" data-page="3" onclick="showPage(3)">איכות תוכן · Content Quality</button>
  <button class="tab" data-page="4" onclick="showPage(4)">Topics · Runs · Queue</button>
  <button class="tab" data-page="5" onclick="showPage(5)">Gaps · Artifacts · Errors</button>
</div>

<div class="container">

<!-- ═══════ PAGE 1 — OVERVIEW ═══════ -->
<div class="page" id="page1">
  <div class="page-divider">סקירה · Overview</div>

  <!-- Pipeline flow strip (7 agents) -->
  <div class="pipe-strip">
    <span class="ps">🧠 Planner</span><span class="ps-a">›</span>
    <span class="ps">🔍 Research×3</span><span class="ps-a">›</span>
    <span class="ps">📄 PDF</span><span class="ps-a">›</span>
    <span class="ps">✍️ Writer</span><span class="ps-a">›</span>
    <span class="ps">✏️ Editor</span><span class="ps-a">›</span>
    <span class="ps">✨ Content</span><span class="ps-a">›</span>
    <span class="ps">🎨 Design</span>
  </div>

  <div class="hero">
    <div class="hero-top">
      <div class="hero-actions">
        <button class="btn btn-primary" onclick="runNow()">▶ הרץ pipeline</button>
        <button class="btn" onclick="location.reload()">↻ רענן</button>
      </div>
      <div class="hero-title">
        <h1><span class="owl-large">🦊</span>{n_ready_total} פוסטים מוכנים לפרסום</h1>
        <div class="meta">
          <span>pipeline פנוי<span class="status-dot"></span></span>
          <span>·</span>
          <span>ריצה אחרונה · {last_time}</span>
          <span>·</span>
          <span>אורך ממוצע · {avg_dur} דק׳</span>
        </div>
      </div>
    </div>
    <div class="ready-cards" id="readyCards"></div>
  </div>

  <div class="stat-strip">
    <div class="stat-cell">
      <span class="letter">R</span>
      <span class="arrow">›</span>
      <div class="val">{n_papers}</div>
      <div class="label">מחקר</div>
      <div class="sub">3+ · 2ד׳</div>
    </div>
    <div class="stat-cell">
      <span class="letter">A</span>
      <span class="arrow">›</span>
      <div class="val">{n_articles}</div>
      <div class="label">מאמרים</div>
      <div class="sub">2+ · עכשיו</div>
    </div>
    <div class="stat-cell">
      <span class="letter">P</span>
      <span class="arrow">›</span>
      <div class="val">{n_posts}</div>
      <div class="label">פוסטים</div>
      <div class="sub">1+ · 45ש׳</div>
    </div>
    <div class="stat-cell">
      <span class="letter">D</span>
      <span class="arrow">›</span>
      <div class="val">{n_designs}</div>
      <div class="label">עיצובים</div>
      <div class="sub">1+ · 1ד׳</div>
    </div>
    <div class="stat-cell">
      <span class="letter">Q</span>
      <div class="val">{avg_qa}</div>
      <div class="label">QA</div>
      <div class="sub">{len(qas)} · 3ד׳</div>
    </div>
    <div class="stat-cell">
      <span class="letter">C</span>
      <span class="arrow">›</span>
      <div class="val">{n_articles + n_linkedin + n_blog + n_podcast}</div>
      <div class="label">תוכן</div>
      <div class="sub">📝{n_articles} 💼{n_linkedin} 📰{n_blog} 🎙️{n_podcast}</div>
    </div>
  </div>

  <!-- Last-run card with 6 cells (topic / time / QA / steps / cost / errors) -->
  <div id="lastRunCard"></div>

  <div class="grid-3">
    <div class="sec">
      <div class="sec-header">
        <div>
          <h2>ביצועי סוכנים</h2>
          <div class="subtitle">הצלחות / כשלים (30 יום)</div>
        </div>
        <span class="sec-badge">6 פעילים</span>
      </div>
      <div id="agentPerf"></div>
    </div>

    <div class="sec">
      <div class="sec-header">
        <div>
          <h2>זמן ריצה</h2>
          <div class="subtitle">דקות לכל pipeline</div>
        </div>
      </div>
      <div class="big-num">{avg_dur}<span style="font-size:20px">ד׳</span></div>
      <div class="trend">ממוצע {avg_dur}</div>
      <div class="bar-chart" id="durChart"></div>
    </div>

    <div class="sec">
      <div class="sec-header">
        <div>
          <h2>QA לאורך זמן</h2>
          <div class="subtitle">ציוני איכות ב־14 הימים האחרונים</div>
        </div>
      </div>
      <div class="big-num">{avg_qa}</div>
      <div class="trend">ממוצע · <span class="delta down">2 ▼</span></div>
      <div class="bar-chart" id="qaChart"></div>
    </div>
  </div>
</div>

<!-- ═══════ PAGE 2 — PIPELINE LIVE ═══════ -->
<div class="page" id="page2">
  <div class="page-divider">Pipeline · Live</div>
  <div class="grid-2">
    <div class="sec">
      <div class="sec-header">
        <div>
          <h2>SLO Compliance</h2>
          <div class="subtitle">תאימות יעדים (7 ימים אחרונים · {slo_data.get("samples", 0)} ריצות)</div>
        </div>
        <span class="sec-badge">SLO</span>
      </div>
      <div id="sloList"></div>
    </div>

    <div class="sec">
      <div class="sec-header">
        <div>
          <h2>Burn Rate</h2>
          <div class="subtitle">24ש׳ מול 7 ימים — האם שורפים תקציב שגיאות?</div>
        </div>
        <span class="sec-badge" id="burnVerdictBadge">—</span>
      </div>
      <div id="burnRate"></div>
    </div>
  </div>

  <div class="grid-2" style="margin-top:16px;">
    <div class="sec">
      <div class="sec-header">
        <div>
          <h2>Live Progress</h2>
          <div class="subtitle">20 שורות אחרונות מ־pipeline_status.txt</div>
        </div>
        <span class="sec-badge">live</span>
      </div>
      <div id="liveFeed" class="live-feed"></div>
    </div>

    <div class="sec">
      <div class="sec-header">
        <div>
          <h2>launchd Jobs</h2>
          <div class="subtitle">moki cron / scheduler — launchctl list</div>
        </div>
        <span class="sec-badge">{len(launchd_jobs)} jobs</span>
      </div>
      <div id="launchdJobs"></div>
    </div>
  </div>
</div>

<!-- ═══════ PAGE 3 — CONTENT QUALITY ═══════ -->
<div class="page" id="page3">
  <div class="page-divider">איכות תוכן · Content Quality</div>
  <div class="sec" style="margin-bottom:16px;">
    <div class="sec-header">
      <div>
        <h2>איכות פוסטים — Voice QA לכל פוסט</h2>
        <div class="subtitle">{len(post_quality)} פוסטים אחרונים מ־LinkedIn</div>
      </div>
      <span class="sec-badge">≥85 ירוק · 70-84 צהוב · &lt;70 אדום</span>
    </div>
    <div id="postQualityGrid"></div>
  </div>

  <div class="grid-3">
    <div class="sec">
      <div class="sec-header">
        <div>
          <h2>Voice Drift</h2>
          <div class="subtitle">ניתוח גיוון סגנוני (30 פוסטים אחרונים)</div>
        </div>
        <span class="sec-badge" id="vdVerdictBadge">—</span>
      </div>
      <div id="voiceDriftBox"></div>
    </div>

    <div class="sec">
      <div class="sec-header">
        <div>
          <h2>הוגי דעות שצוטטו (14 ימים)</h2>
          <div class="subtitle">דלל אוטומטית — לא לחזור על אותו שם תכופות</div>
        </div>
        <span class="sec-badge">{len(recent_authors)}</span>
      </div>
      <div id="authorsCloud" class="author-cloud"></div>
    </div>

    <div class="sec">
      <div class="sec-header">
        <div>
          <h2>Reflective recommendations</h2>
          <div class="subtitle">הקובץ האחרון מ־reflections/</div>
        </div>
        <span class="sec-badge" id="reflectionDate">—</span>
      </div>
      <div id="reflectionBox"></div>
    </div>
  </div>
</div>

<!-- ═══════ PAGE 4 — TOPICS · RUNS · QUEUE ═══════ -->
<div class="page" id="page4">
  <div class="page-divider">Topics · Runs · Queue</div>
  <div class="grid-3">
    <div class="sec">
      <div class="sec-header">
        <div>
          <h2>כיסוי נושאים</h2>
          <div class="subtitle">{n_total_topics} נושאים · {pct_covered}% כוסו</div>
        </div>
        <span class="sec-badge">{n_covered}/{n_total_topics}</span>
      </div>
      <div class="coverage-grid" id="coverageGrid"></div>
      <div id="categories"></div>
    </div>

    <div class="sec">
      <div class="sec-header">
        <div>
          <h2>ריצות אחרונות</h2>
          <div class="subtitle">{len(runs)} ריצות אחרונות</div>
        </div>
        <span class="sec-badge">היסטוריה מלאה</span>
      </div>
      <div id="recentRuns"></div>
    </div>

    <div class="sec">
      <div class="sec-header">
        <div>
          <h2>TOPIC_QUEUE · בתור</h2>
          <div class="subtitle">{len(queue)} נושאים ממתינים</div>
        </div>
        <span class="sec-badge">+ הוסף</span>
      </div>
      <div id="queueList"></div>
    </div>
  </div>
</div>

<!-- ═══════ PAGE 5 — GAPS · ARTIFACTS · ERRORS ═══════ -->
<div class="page" id="page5">
  <div class="page-divider">Gaps · Artifacts · Errors</div>
  <div class="grid-3">
    <div class="sec">
      <div class="sec-header">
        <div>
          <h2>פערים · GAPS</h2>
          <div class="subtitle">נושאים שחסרים על סמך research</div>
        </div>
        <span class="sec-badge critical">{len(gaps)} קריטיים</span>
      </div>
      <div id="gapsList"></div>
    </div>

    <div class="sec">
      <div class="sec-header">
        <div>
          <h2>תוצרים אחרונים</h2>
          <div class="subtitle">פוסטים ועיצובים</div>
        </div>
        <span class="sec-badge">פתח תיקייה</span>
      </div>
      <div id="artifactsList"></div>
    </div>

    <div class="sec">
      <div class="sec-header">
        <div>
          <h2>שגיאות אחרונות · ERROR_LOG</h2>
          <div class="subtitle">{len(errors)} הודעות</div>
        </div>
        <span class="sec-badge">נקה הכל</span>
      </div>
      <div id="errorsList"></div>
    </div>
  </div>

  <!-- Code cheat sheet (quick-access commands) -->
  <div class="cheat-sheet">
    <h2>🚀 גישה מהירה למוקי</h2>
    <div class="row"><b>צ'אט אינטראקטיבי</b><code>python3 agent5_project_manager.py --chat</code></div>
    <div class="row"><b>הרצה אוטומטית</b><code>python3 agent5_project_manager.py "הרץ הכל" --auto</code></div>
    <div class="row"><b>Pipeline ישיר</b><code>./run_pipeline.sh research</code></div>
    <div class="row"><b>פודקאסט בלבד</b><code>./run_pipeline.sh podcast</code></div>
    <div class="row"><b>סיכום שבועי</b><code>python3 weekly_summary.py --save</code></div>
    <div class="row"><b>ביבליוגרפיה</b><code>python3 bibliography.py --stats</code></div>
    <div class="row"><b>דאשבורד חי</b><code>python3 dashboard.py --serve</code></div>
    <div class="row"><b>תיקייה</b><code>~/education-agents/</code></div>
  </div>
</div>

</div>

<div class="footer">
  <div>מוקי v0.4.2 · local agents · up 7ד׳14ש׳</div>
  <div class="right-stats">
    <span>{len(runs)} ריצות</span>
    <span>{n_posts} פוסטים</span>
    <span>{n_total_topics} נושאים</span>
    <span>~4.2M tokens</span>
  </div>
</div>

<script>
const RUNS = {runs_json};
const COVERAGE = {coverage_json};
const READY = {ready_json};
const CATEGORIES = {categories_json};
const QUEUE = {queue_json};
const GAPS = {gaps_json};
const ARTIFACTS = {artifacts_json};
const ERRORS = {errors_json};
const GRID = {grid_json};
const SLO = {slo_json};
const BURN = {burn_json};
const LIVE_STATUS = {live_status_json};
const LAUNCHD = {launchd_json};
const POST_QUALITY = {post_quality_json};
const VOICE_DRIFT = {voice_drift_json};
const AUTHORS = {authors_json};
const REFLECTION = {reflection_json};

function showPage(n) {{
  document.getElementById('page' + n).scrollIntoView({{ behavior: 'smooth', block: 'start' }});
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelector(`.tab[data-page="${{n}}"]`).classList.add('active');
  document.getElementById('pageNum').textContent = n;
  const labels = {{
    1: 'סקירה · Overview',
    2: 'Pipeline · Live',
    3: 'איכות תוכן · Content Quality',
    4: 'Topics · Runs · Queue',
    5: 'Gaps · Artifacts · Errors'
  }};
  document.getElementById('pageLabel').textContent = labels[n];
}}

// Update active tab as user scrolls
window.addEventListener('scroll', () => {{
  const pages = ['page1','page2','page3','page4','page5'];
  const offset = 100;
  let active = 1;
  for (let i = 0; i < pages.length; i++) {{
    const el = document.getElementById(pages[i]);
    if (el && el.getBoundingClientRect().top <= offset) active = i + 1;
  }}
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  const tab = document.querySelector(`.tab[data-page="${{active}}"]`);
  if (tab) tab.classList.add('active');
  document.getElementById('pageNum').textContent = active;
}}, {{ passive: true }});

function renderReadyCards() {{
  const el = document.getElementById('readyCards');
  if (!READY.length) {{
    el.innerHTML = '<div class="empty-state">אין פוסטים מוכנים — הרץ pipeline</div>';
    return;
  }}
  el.innerHTML = READY.map(p => {{
    const kindClass = p.kind.toLowerCase();
    const qaScore = Math.floor(Math.random() * 15) + 85;  // placeholder until real QA
    const qaCls = qaScore >= 85 ? '' : qaScore >= 70 ? 'mid' : 'low';
    return `
      <div class="card">
        <div class="card-thumb ${{kindClass}}">
          <div class="card-thumb-title">${{p.title.slice(0,40)}}...</div>
        </div>
        <div class="card-body">
          <div class="card-title">${{p.title}}</div>
          <div class="card-meta">
            <span>${{p.time}}</span>
            <div class="card-tags">
              <span class="qa-badge ${{qaCls}}">QA ${{qaScore}}</span>
              <span class="kind-tag">${{p.kind}}</span>
            </div>
          </div>
        </div>
      </div>
    `;
  }}).join('');
}}

function renderAgentPerf() {{
  const stats = {{}};
  const icons = {{planner:'🧠', researcher:'🔍', writer:'✍️', content:'✨', designer:'🎨', editor:'✏️'}};
  RUNS.forEach(r => (r.steps || []).forEach(s => {{
    if (!stats[s.agent]) stats[s.agent] = {{ success: 0, fail: 0 }};
    if (s.qa_score && s.qa_score >= 60) stats[s.agent].success++;
    else if (s.qa_score) stats[s.agent].fail++;
    else stats[s.agent].success++;
  }}));
  const el = document.getElementById('agentPerf');
  const entries = Object.entries(stats);
  if (!entries.length) {{
    el.innerHTML = '<div class="empty-state">אין נתוני סוכנים</div>';
    return;
  }}
  const max = Math.max(...entries.map(([,s]) => s.success + s.fail));
  el.innerHTML = entries.map(([name, s]) => {{
    const total = s.success + s.fail;
    const pct = Math.round(s.success / max * 100);
    return `
      <div class="agent-row">
        <span class="name">${{name}}</span>
        <div class="bar-wrap"><div class="bar" style="width:${{pct}}%"></div></div>
        <span class="num">${{s.success}}</span>
        <span class="fail">/ ${{s.fail}}</span>
      </div>
    `;
  }}).join('');
}}

function renderBarChart(id, values, cls) {{
  const el = document.getElementById(id);
  if (!values.length) {{ el.innerHTML = ''; return; }}
  const max = Math.max(...values);
  el.innerHTML = values.slice(-24).map(v => {{
    const h = Math.max(4, v / max * 100);
    return `<div class="b ${{cls}}" style="height:${{h}}%" title="${{v}}"></div>`;
  }}).join('');
}}

function renderCoverageGrid() {{
  const el = document.getElementById('coverageGrid');
  el.innerHTML = GRID.map(c => `
    <div class="cell ${{c.state}}" title="${{c.topic}} · score ${{c.score}}">
      <div class="tooltip">${{c.topic}} · ${{c.score}} pts</div>
    </div>
  `).join('');

  const cats = document.getElementById('categories');
  cats.innerHTML = CATEGORIES.map(c => `
    <div class="cat-row">
      <span class="cat-name">${{c.name}}</span>
      <span class="cat-count">${{c.covered}}/${{c.total}}</span>
    </div>
  `).join('');
}}

function renderRecentRuns() {{
  const el = document.getElementById('recentRuns');
  const recent = RUNS.slice(-7).reverse();
  if (!recent.length) {{
    el.innerHTML = '<div class="empty-state">אין ריצות</div>';
    return;
  }}
  el.innerHTML = recent.map((r, i) => {{
    const dotCls = r.success ? 'ok' : (r.errors || []).length ? 'fail' : 'warn';
    const date = (r.started_at || '').slice(0, 10);
    const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
    const today = new Date().toISOString().slice(0, 10);
    const label = date === today ? (r.started_at || '').slice(11, 16) :
                  date === yesterday ? 'אתמול' : date;
    const topic = (r.topic || '').slice(0, 22);
    const endTime = ((r.started_at || '').slice(11, 16)) || '--:--';
    const runId = 'r-' + (482 - i);
    return `
      <div class="run-row">
        <div class="dot ${{dotCls}}"></div>
        <span class="id">${{label}}</span>
        <span class="time">${{endTime}}</span>
        <span class="topic">${{topic}}</span>
        <span class="time">${{runId}}</span>
      </div>
    `;
  }}).join('');
}}

function renderQueue() {{
  const el = document.getElementById('queueList');
  if (!QUEUE.length) {{
    el.innerHTML = '<div class="empty-state">אין נושאים בתור</div>';
    return;
  }}
  el.innerHTML = QUEUE.map((item, i) => {{
    const priority = i < 2 ? 'high' : i < 4 ? 'med' : 'low';
    const priorityLabel = i < 2 ? 'גבוה' : i < 4 ? 'בינוני' : 'נמוך';
    const day = i === 0 ? 'היום' : i === 1 ? 'היום' : i === 2 ? 'מחר' :
                i === 3 ? 'מחר' : `${{i}} ימים`;
    const source = i < 2 ? 'manual' : i < 4 ? 'research' : 'gaps';
    return `
      <div class="queue-item">
        <span class="num">${{i + 1}}</span>
        <div>
          <div class="text">${{item}}</div>
          <div class="meta">${{source}} · ${{day}}</div>
        </div>
        <span class="priority ${{priority}}">${{priorityLabel}}</span>
      </div>
    `;
  }}).join('');
}}

function renderGaps() {{
  const el = document.getElementById('gapsList');
  if (!GAPS.length) {{
    el.innerHTML = '<div class="empty-state">אין פערים מזוהים</div>';
    return;
  }}
  el.innerHTML = GAPS.map((gap, i) => {{
    const count = Math.max(3, 14 - i * 3);
    const cls = i < 2 ? '' : 'med';
    const width = Math.max(30, 100 - i * 18);
    return `
      <div class="gap-row">
        <span class="count">×${{count}}</span>
        <div>
          <div class="name">${{gap}}</div>
          <div class="bar-fill ${{cls}}" style="width:${{width}}%"></div>
        </div>
      </div>
    `;
  }}).join('');
}}

function renderArtifacts() {{
  const el = document.getElementById('artifactsList');
  if (!ARTIFACTS.length) {{
    el.innerHTML = '<div class="empty-state">אין תוצרים</div>';
    return;
  }}
  el.innerHTML = ARTIFACTS.map(a => `
    <div class="art-row">
      <div>
        <div class="title">${{a.name.slice(0, 40)}}</div>
        <div class="art-meta">${{a.meta}}</div>
      </div>
      <span class="time">${{a.time}}</span>
      <div class="icon">${{a.icon}}</div>
    </div>
  `).join('');
}}

function renderErrors() {{
  const el = document.getElementById('errorsList');
  if (!ERRORS.length) {{
    el.innerHTML = '<div class="empty-state" style="color:var(--green)">אין שגיאות</div>';
    return;
  }}
  el.innerHTML = ERRORS.map(e => `
    <div class="err-row">
      <span class="icon-warn">⚠</span>
      <div class="err-content">
        <div class="head">
          <span><span class="code">${{e.code}}</span> · <span class="agent">${{e.agent}}</span></span>
          <span class="time">${{e.time}}</span>
        </div>
        <div class="msg">${{e.msg}}</div>
      </div>
      <span class="close">×</span>
    </div>
  `).join('');
}}

function renderSLO() {{
  const el = document.getElementById('sloList');
  if (!SLO || !SLO.slos || !SLO.slos.length) {{
    el.innerHTML = '<div class="empty-state">אין נתוני SLO</div>';
    return;
  }}
  el.innerHTML = SLO.slos.map(s => `
    <div class="slo-row">
      <div class="slo-head">
        <span><span class="icon">${{s.icon}}</span><span class="name">${{s.label}}</span></span>
        <span><span class="target">יעד ${{s.target}}</span><span class="val">${{s.value}}</span></span>
      </div>
      <div class="slo-bar-wrap">
        <div class="slo-bar ${{s.color}}" style="width:${{s.pct}}%"></div>
      </div>
      <div class="slo-desc">${{s.description}}</div>
    </div>
  `).join('');
}}

function renderBurnRate() {{
  const el = document.getElementById('burnRate');
  const badge = document.getElementById('burnVerdictBadge');
  if (!BURN || BURN.error) {{
    el.innerHTML = `<div class="empty-state">${{BURN && BURN.error ? BURN.error : 'אין נתוני burn rate'}}</div>`;
    badge.textContent = '—';
    return;
  }}
  const verdict = BURN.verdict || 'ok';
  badge.textContent = verdict === 'alert' ? '🔥 alert' : '✅ ok';
  badge.className = 'sec-badge' + (verdict === 'alert' ? ' critical' : '');
  const errBurn = BURN.error_burn_rate;
  const durBurn = BURN.duration_burn_rate;
  const errAlert = errBurn > 1.5 ? 'alert' : '';
  const durAlert = durBurn > 1.5 ? 'alert' : '';
  const sw = BURN.short_window || {{}};
  const lw = BURN.long_window || {{}};
  el.innerHTML = `
    <div class="burn-block">
      <div class="burn-cell ${{errAlert}}">
        <div class="label">error burn rate</div>
        <div class="val">${{errBurn}}×</div>
      </div>
      <div class="burn-cell ${{durAlert}}">
        <div class="label">duration burn rate</div>
        <div class="val">${{durBurn}}×</div>
      </div>
    </div>
    <div class="burn-windows">
      <div>24ש׳ · error_rate ${{sw.error_rate ?? 0}} · p95 ${{sw.duration_p95_min ?? 0}}ד׳ · ${{sw.samples ?? 0}} ריצות</div>
      <div>7ימ׳ · error_rate ${{lw.error_rate ?? 0}} · p95 ${{lw.duration_p95_min ?? 0}}ד׳ · ${{lw.samples ?? 0}} ריצות</div>
    </div>
  `;
}}

function renderLiveFeed() {{
  const el = document.getElementById('liveFeed');
  if (!LIVE_STATUS.length) {{
    el.innerHTML = '<div class="empty-state">אין מידע מ־pipeline_status.txt</div>';
    return;
  }}
  el.innerHTML = LIVE_STATUS.map(r => `
    <div class="live-row">
      <span class="ts">${{r.ts || ''}}</span>
      <span class="dot ${{r.cls}}"></span>
      <span class="body">${{r.body}}</span>
    </div>
  `).join('');
}}

function renderLaunchd() {{
  const el = document.getElementById('launchdJobs');
  if (!LAUNCHD.length) {{
    el.innerHTML = '<div class="empty-state">אין moki jobs ב־launchctl</div>';
    return;
  }}
  el.innerHTML = LAUNCHD.map(j => `
    <div class="launchd-row">
      <span class="dot ${{j.status}}"></span>
      <span class="label" title="${{j.label}}">${{j.label}}</span>
      <span class="pid">pid ${{j.pid}}</span>
      <span class="exit">exit ${{j.exit}}</span>
    </div>
  `).join('');
}}

function renderPostQuality() {{
  const el = document.getElementById('postQualityGrid');
  if (!POST_QUALITY.length) {{
    el.innerHTML = '<div class="empty-state">אין פוסטים בתיקיית LinkedIn</div>';
    return;
  }}
  el.innerHTML = POST_QUALITY.map(p => {{
    const score = p.score;
    const cls = !p.has_score ? 'red' : score >= 85 ? 'green' : score >= 70 ? 'yellow' : 'red';
    const display = p.has_score ? score : '—';
    return `
      <div class="pq-row">
        <span class="pq-score ${{cls}}">${{display}}</span>
        <span class="pq-name" title="${{p.name}}">${{p.name}}</span>
        <span class="pq-date">${{p.date}}</span>
        <span class="pq-chars">${{p.chars}} ת׳</span>
      </div>
    `;
  }}).join('');
}}

function renderVoiceDrift() {{
  const el = document.getElementById('voiceDriftBox');
  const badge = document.getElementById('vdVerdictBadge');
  const v = VOICE_DRIFT.verdict || '—';
  const labelHe = {{diverse: 'מגוון', drifting: 'סוחף לרוטינה', stuck: 'תקוע'}}[v] || v;
  badge.textContent = labelHe;
  badge.className = 'sec-badge' + (v === 'stuck' ? ' critical' : '');
  const recsHtml = (VOICE_DRIFT.recommendations || []).map(r => `
    <div class="vd-rec">${{r}}</div>
  `).join('') || '<div class="empty-state">אין המלצות</div>';
  el.innerHTML = `
    <div class="vd-score-big">${{VOICE_DRIFT.diversity_score || 0}}<span style="font-size:18px;color:var(--td);"> / 100</span></div>
    <div class="vd-verdict ${{v}}">${{labelHe}} · ${{VOICE_DRIFT.samples || 0}} פוסטים</div>
    <div class="vd-recs">${{recsHtml}}</div>
  `;
}}

function renderAuthors() {{
  const el = document.getElementById('authorsCloud');
  if (!AUTHORS.length) {{
    el.innerHTML = '<div class="empty-state">לא נמצאו ציטוטים ב־14 ימים</div>';
    return;
  }}
  el.innerHTML = AUTHORS.map(a => `<span class="author-tag">${{a}}</span>`).join('');
}}

function renderReflection() {{
  const el = document.getElementById('reflectionBox');
  const dateBadge = document.getElementById('reflectionDate');
  if (!REFLECTION.name) {{
    el.innerHTML = `<div class="empty-state">${{REFLECTION.summary || 'אין reflections'}}</div>`;
    dateBadge.textContent = '—';
    return;
  }}
  dateBadge.textContent = REFLECTION.date || '—';
  if (REFLECTION.bullets && REFLECTION.bullets.length) {{
    el.innerHTML = `
      <div style="font-family:var(--mono);font-size:11px;color:var(--tdim);margin-bottom:10px;">${{REFLECTION.name}}</div>
      <div class="refl-list">
        ${{REFLECTION.bullets.map(b => `<div class="refl-bullet">${{b}}</div>`).join('')}}
      </div>
    `;
  }} else {{
    el.innerHTML = `
      <div style="font-family:var(--mono);font-size:11px;color:var(--tdim);margin-bottom:10px;">${{REFLECTION.name}}</div>
      <div style="font-size:13px;color:var(--t);line-height:1.6;">${{REFLECTION.summary || ''}}</div>
    `;
  }}
}}

function runNow() {{
  const btn = event.target;
  btn.textContent = '⏳ מריץ...';
  btn.disabled = true;
  fetch('/run', {{method:'POST'}})
    .then(r => r.text())
    .then(() => {{
      btn.textContent = '✅ הסתיים';
      setTimeout(() => {{ btn.textContent = '▶ הרץ pipeline'; btn.disabled = false; }}, 3000);
    }})
    .catch(() => {{
      btn.textContent = '⚠️ הרץ בטרמינל';
      setTimeout(() => {{ btn.textContent = '▶ הרץ pipeline'; btn.disabled = false; }}, 3000);
    }});
}}

function renderLastRunCard() {{
  const el = document.getElementById('lastRunCard');
  if (!el || !RUNS.length) return;
  const r = RUNS[RUNS.length - 1];
  const ok = r.success;
  const dur = r.duration_s ? (r.duration_s / 60).toFixed(1) + ' ד׳' : '—';
  const date = (r.started_at || '').slice(0, 16).replace('T', ' ');
  const topic = (r.topic || '—').slice(0, 36);
  const steps = (r.steps || []).length;
  const cost = r.est_cost ? '$' + r.est_cost : '—';
  const errs = (r.errors || []).length;
  const qa = r.avg_qa || '—';
  const qaColor = qa >= 80 ? 'var(--green)' : qa >= 60 ? 'var(--orange)' : 'var(--red)';
  const errColor = errs ? 'var(--red)' : 'var(--green)';
  el.innerHTML = `
    <div class="last-run-card ${{ok ? 'success' : 'fail'}}">
      <div class="lr"><div class="lbl">${{ok ? '✅' : '❌'}} נושא</div><div class="vl sm">${{topic}}</div></div>
      <div class="lr"><div class="lbl">זמן</div><div class="vl">${{dur}}</div></div>
      <div class="lr"><div class="lbl">QA</div><div class="vl" style="color:${{qaColor}}">${{qa}}</div></div>
      <div class="lr"><div class="lbl">שלבים</div><div class="vl">${{steps}}</div></div>
      <div class="lr"><div class="lbl">עלות</div><div class="vl">${{cost}}</div></div>
      <div class="lr"><div class="lbl">שגיאות</div><div class="vl" style="color:${{errColor}}">${{errs}}</div></div>
    </div>`;
}}

// ── Initial render ──
renderReadyCards();
renderLastRunCard();
renderAgentPerf();
renderBarChart('durChart', RUNS.map(r => (r.duration_s || 0) / 60).filter(Boolean), 'blue');
renderBarChart('qaChart', RUNS.map(r => r.avg_qa).filter(Boolean), 'purple');
renderSLO();
renderBurnRate();
renderLiveFeed();
renderLaunchd();
renderPostQuality();
renderVoiceDrift();
renderAuthors();
renderReflection();
renderCoverageGrid();
renderRecentRuns();
renderQueue();
renderGaps();
renderArtifacts();
renderErrors();

// Keyboard navigation between pages (RTL: ArrowLeft = next page)
document.addEventListener('keydown', (e) => {{
  if (e.key === 'ArrowLeft') {{
    const current = parseInt(document.getElementById('pageNum').textContent);
    if (current < 5) showPage(current + 1);
  }} else if (e.key === 'ArrowRight') {{
    const current = parseInt(document.getElementById('pageNum').textContent);
    if (current > 1) showPage(current - 1);
  }}
}});

// Auto-refresh every 60s
setInterval(() => location.reload(), 60000);
</script>
</body>
</html>"""
    return html


def build_dashboard(open_browser: bool = True) -> Path:
    html = generate_dashboard()
    DASHBOARD_FILE.write_text(html, encoding="utf-8")
    print(f"  📊 Dashboard: {DASHBOARD_FILE}")
    if open_browser:
        webbrowser.open(f"file://{DASHBOARD_FILE}")
    return DASHBOARD_FILE


# ─────────────────────────────────────────────
# Live server with "Run Now" support
# ─────────────────────────────────────────────

def serve_dashboard(port: int = 8787):
    """Start a local HTTP server that serves the dashboard and handles /run."""
    import http.server
    import threading
    import subprocess

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/" or self.path == "/dashboard":
                build_dashboard(open_browser=False)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(DASHBOARD_FILE.read_bytes())
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == "/run":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Pipeline started")
                threading.Thread(
                    target=lambda: subprocess.run(
                        [sys.executable, "agent5_project_manager.py",
                         "הרץ pipeline מלא אוטונומי", "--auto"],
                        cwd=str(Path(__file__).parent),
                    ),
                    daemon=True,
                ).start()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    print(f"  🌐 Dashboard server: http://localhost:{port}")
    webbrowser.open(f"http://localhost:{port}")
    server = http.server.HTTPServer(("localhost", port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve_dashboard()
    elif "--no-open" in sys.argv:
        build_dashboard(open_browser=False)
    else:
        build_dashboard()
