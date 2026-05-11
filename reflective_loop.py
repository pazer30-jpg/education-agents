"""
reflective_loop.py — Moki's reflection layer.

After every ~10 published posts, Moki analyzes its own performance + voice
patterns + recurring issues, then proposes specific improvements (which it
can apply with user approval).

Pure Python (no LLM call). Reuses:
  - voice_drift.analyze_voice_drift / format_report
  - performance_log entries (output/performance_log.json)
  - analytics.json QA history
  - voice_profile.FIELD_EXAMPLES / _FORBIDDEN_PATTERNS / check_voice_adherence

Public API:
    run_reflection(min_posts: int = 10) -> dict
    format_reflection_report(report: dict) -> str
    apply_recommendation(rec: dict, dry_run: bool = True) -> dict
    should_run_reflection() -> bool
    save_reflection(report: dict) -> Path
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from config import OUTPUT_DIR, LINKEDIN_DIR


# ─────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────

REFLECTIONS_DIR = OUTPUT_DIR / "reflections"
LAST_RUN_FILE = REFLECTIONS_DIR / "last_run.json"
PERF_FILE = OUTPUT_DIR / "performance_log.json"
ANALYTICS_FILE = OUTPUT_DIR / "analytics.json"


# ─────────────────────────────────────────────
# Tiny IO helpers
# ─────────────────────────────────────────────

def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ─────────────────────────────────────────────
# Performance helpers
# ─────────────────────────────────────────────

def _engagement_score(entry: dict) -> float:
    """Same scoring as performance_log._engagement_score."""
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
    return entry.get("personal_score", 5) * 10


# ─────────────────────────────────────────────
# QA scores from analytics
# ─────────────────────────────────────────────

def _recent_qa_scores(limit: int = 10) -> dict:
    """
    Pull recent average QA from analytics.json. Returns:
      {"avg_qa": float|None, "samples": int, "by_agent": {agent: avg}}
    """
    data = _load_json(ANALYTICS_FILE, {})
    runs = data.get("runs", []) if isinstance(data, dict) else []
    runs = [r for r in runs if isinstance(r, dict)]
    if not runs:
        return {"avg_qa": None, "samples": 0, "by_agent": {}}

    recent = runs[-limit:]
    qas: list[float] = []
    by_agent: dict[str, list[float]] = defaultdict(list)
    for r in recent:
        if isinstance(r.get("avg_qa"), (int, float)):
            qas.append(float(r["avg_qa"]))
        for ag, sc in (r.get("qa_scores") or {}).items():
            if isinstance(sc, (int, float)):
                by_agent[ag].append(float(sc))

    avg_qa = round(sum(qas) / len(qas), 1) if qas else None
    by_agent_avg = {
        ag: round(sum(vals) / len(vals), 1)
        for ag, vals in by_agent.items()
        if vals
    }
    return {
        "avg_qa": avg_qa,
        "samples": len(recent),
        "by_agent": by_agent_avg,
    }


# ─────────────────────────────────────────────
# Match a post file to a perf-log entry (best effort)
# ─────────────────────────────────────────────

_FILENAME_CLEAN_RE = re.compile(r"[\W_]+")


def _norm(s: str) -> str:
    return _FILENAME_CLEAN_RE.sub("", s.lower())


def _match_perf_for_file(path: Path, perf_data: list) -> dict | None:
    """Best-effort: find perf entry whose title overlaps the filename stem."""
    stem_norm = _norm(path.stem)
    if not stem_norm:
        return None
    best = None
    best_score = 0
    for e in perf_data:
        title = e.get("title", "")
        if not title:
            continue
        t_norm = _norm(title)
        if not t_norm:
            continue
        # Compare on shared character n-grams of length 6
        score = 0
        for i in range(0, len(t_norm) - 5):
            if t_norm[i:i + 6] in stem_norm:
                score += 1
        if score > best_score:
            best_score = score
            best = e
    return best if best_score >= 2 else None


# ─────────────────────────────────────────────
# Voice QA per file
# ─────────────────────────────────────────────

def _voice_qa(text: str) -> dict:
    """Wrap voice_profile.check_voice_adherence; degrade gracefully."""
    try:
        from voice_profile import check_voice_adherence
        return check_voice_adherence(text, platform="linkedin")
    except Exception:
        return {"score": None, "issues": [], "strengths": []}


# ─────────────────────────────────────────────
# Main reflection
# ─────────────────────────────────────────────

def run_reflection(min_posts: int = 10) -> dict:
    """
    Analyze the last N posts and identify patterns + improvements.

    Returns a dict suitable for printing, saving, or driving auto-apply.
    """
    REFLECTIONS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Gather posts
    if LINKEDIN_DIR.exists():
        post_files = [
            p for p in LINKEDIN_DIR.glob("*_ready*.txt")
            if not p.name.endswith(".bak") and p.is_file()
        ]
        post_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    else:
        post_files = []
    post_files = post_files[:30]
    samples = len(post_files)

    if samples < min_posts:
        return {
            "samples": samples,
            "min_required": min_posts,
            "skipped": True,
            "reason": (
                f"רק {samples} פוסטים זמינים, נדרשים לפחות {min_posts} "
                f"לרפלקציה אמינה."
            ),
            "performance_summary": {},
            "voice_drift": {},
            "common_issues": [],
            "best_performers": [],
            "weak_performers": [],
            "recommendations": [],
            "generated_at": datetime.now().isoformat(),
        }

    # 2. Load perf log + analytics
    perf_data = _load_json(PERF_FILE, [])
    if not isinstance(perf_data, list):
        perf_data = []
    qa_summary = _recent_qa_scores(limit=samples)

    # 3. Voice drift (uses its own discovery; that's fine — it scans the same
    # directory).
    try:
        from voice_drift import analyze_voice_drift
        voice = analyze_voice_drift(top_n=samples)
    except Exception as e:
        voice = {"error": str(e), "samples": 0,
                 "patterns": [], "recommendations": []}

    # 4. Per-post stats
    per_post: list[dict] = []
    issue_counter: Counter[str] = Counter()
    theme_engagement: dict[str, list[float]] = defaultdict(list)
    voice_scores: list[int] = []

    for fp in post_files:
        try:
            txt = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        vqa = _voice_qa(txt)
        v_score = vqa.get("score")
        if isinstance(v_score, (int, float)):
            voice_scores.append(int(v_score))
        for iss in vqa.get("issues", []):
            issue_counter[iss] += 1

        perf = _match_perf_for_file(fp, perf_data)
        eng = _engagement_score(perf) if perf else None
        topic = (perf or {}).get("topic_area", "") if perf else ""
        if topic and eng is not None:
            theme_engagement[topic].append(eng)

        per_post.append({
            "file": str(fp),
            "filename": fp.name,
            "voice_score": v_score,
            "voice_issues": vqa.get("issues", []),
            "engagement": eng,
            "topic": topic,
            "matched_perf": bool(perf),
        })

    # 5. Best / weak performers
    def _rank_key(p: dict):
        # Higher is better. Combine voice_score + engagement (normalized).
        v = p["voice_score"] if p["voice_score"] is not None else 50
        e = p["engagement"] if p["engagement"] is not None else 0
        return v + min(e, 200)  # cap engagement so one viral post doesn't dominate

    ranked = sorted(per_post, key=_rank_key, reverse=True)
    best = ranked[:3]
    weak = ranked[-3:][::-1] if len(ranked) >= 3 else []

    best_performers = [
        {
            "file": p["filename"],
            "qa": p["voice_score"],
            "engagement": p["engagement"],
            "topic": p["topic"],
        }
        for p in best
    ]
    weak_performers = [
        {
            "file": p["filename"],
            "qa": p["voice_score"],
            "engagement": p["engagement"],
            "issues": p["voice_issues"][:3],
        }
        for p in weak
    ]

    # 6. Common issues across the corpus
    common_issues = [
        f"{iss} ({count}/{samples})"
        for iss, count in issue_counter.most_common(5)
        if count >= max(2, samples // 5)  # appears in >=20% of posts
    ]

    # 7. Theme engagement insight
    theme_avg = {
        t: round(sum(v) / len(v), 1)
        for t, v in theme_engagement.items()
        if v
    }
    theme_ranked = sorted(theme_avg.items(), key=lambda x: -x[1])

    # 8. Performance summary
    perf_summary = {
        "qa_avg_recent": qa_summary.get("avg_qa"),
        "qa_samples": qa_summary.get("samples"),
        "qa_by_agent": qa_summary.get("by_agent", {}),
        "voice_score_avg": (
            round(sum(voice_scores) / len(voice_scores), 1)
            if voice_scores else None
        ),
        "voice_score_samples": len(voice_scores),
        "perf_log_entries": len(perf_data),
        "perf_log_matched": sum(1 for p in per_post if p["matched_perf"]),
        "theme_engagement": theme_avg,
    }

    # 9. Recommendations
    recommendations = _build_recommendations(
        samples=samples,
        voice=voice,
        issue_counter=issue_counter,
        theme_ranked=theme_ranked,
        voice_score_avg=perf_summary["voice_score_avg"],
    )

    report = {
        "samples": samples,
        "performance_summary": perf_summary,
        "voice_drift": voice,
        "common_issues": common_issues,
        "best_performers": best_performers,
        "weak_performers": weak_performers,
        "recommendations": recommendations,
        "generated_at": datetime.now().isoformat(),
    }
    return report


# ─────────────────────────────────────────────
# Recommendation builder (heuristic)
# ─────────────────────────────────────────────

def _build_recommendations(
    samples: int,
    voice: dict,
    issue_counter: Counter,
    theme_ranked: list[tuple[str, float]],
    voice_score_avg: float | None,
) -> list[dict]:
    recs: list[dict] = []

    # 1. Phrase overuse → add to forbidden (auto-safe)
    for pat in (voice.get("patterns") or []):
        if pat.get("type") == "phrase_overuse":
            phrase = pat.get("phrase", "").strip()
            count = pat.get("count", 0)
            if not phrase or count < max(2, samples * 0.4):
                continue
            recs.append({
                "area": "forbidden_phrase",
                "suggestion": (
                    f"הביטוי '{phrase}' מופיע ב-{count}/{samples} פוסטים. "
                    f"להוסיף לרשימת ה-FORBIDDEN_PATTERNS?"
                ),
                "auto_apply_safe": True,
                "payload": {"phrase": phrase, "count": count},
            })

    # 2. Opening repetition → suggest variety (NOT auto-safe — content level)
    for pat in (voice.get("patterns") or []):
        if pat.get("type") == "opening_repetition":
            count = pat.get("count", 0)
            ex = (pat.get("examples") or [""])[0]
            recs.append({
                "area": "opening_variety",
                "suggestion": (
                    f"פתיחה זהה חוזרת {count} פעמים (\"{ex[:50]}...\") — "
                    f"לגוון על ידי הוספת דוגמה חדשה ל-FIELD_EXAMPLES."
                ),
                "auto_apply_safe": False,
                "payload": {"example_opening": ex, "count": count},
            })

    # 3. Theme that performs well → push more
    if len(theme_ranked) >= 2:
        top_theme, top_eng = theme_ranked[0]
        avg_other = (
            sum(e for _, e in theme_ranked[1:]) / max(1, len(theme_ranked) - 1)
        )
        if top_eng > avg_other * 1.5 and top_eng > 0:
            recs.append({
                "area": "topic_priority",
                "suggestion": (
                    f"נושא '{top_theme}' מקבל engagement של {top_eng:.0f} "
                    f"מול ממוצע {avg_other:.0f} בנושאים אחרים — "
                    f"להגדיל משקל בתור הנושאים?"
                ),
                "auto_apply_safe": False,
                "payload": {"topic": top_theme, "engagement": top_eng},
            })

    # 4. Theme that performs poorly → reduce
    if len(theme_ranked) >= 2:
        bot_theme, bot_eng = theme_ranked[-1]
        avg_others = (
            sum(e for _, e in theme_ranked[:-1])
            / max(1, len(theme_ranked) - 1)
        )
        if avg_others > bot_eng * 2 and avg_others > 0:
            recs.append({
                "area": "topic_demote",
                "suggestion": (
                    f"נושא '{bot_theme}' מתפקד מתחת לממוצע "
                    f"({bot_eng:.0f} מול {avg_others:.0f}) — "
                    f"לשקול לוותר עליו או לזווית אחרת."
                ),
                "auto_apply_safe": False,
                "payload": {"topic": bot_theme, "engagement": bot_eng},
            })

    # 5. Voice drift verdict
    verdict = voice.get("verdict")
    if verdict == "stuck":
        recs.append({
            "area": "voice_freshness",
            "suggestion": (
                "Voice drift: 'תקוע'. להוסיף דוגמת שטח חדשה ל-FIELD_EXAMPLES "
                "כדי לרענן את מאגר הפתיחות."
            ),
            "auto_apply_safe": False,
            "payload": {"verdict": verdict},
        })
    elif verdict == "drifting":
        recs.append({
            "area": "voice_freshness",
            "suggestion": (
                "Voice drift: 'סוחף לרוטינה'. לשקול להוסיף דוגמה חדשה "
                "ל-FIELD_EXAMPLES בנושא שעוד לא מכוסה."
            ),
            "auto_apply_safe": False,
            "payload": {"verdict": verdict},
        })

    # 6. Recurring voice-QA issues → add as forbidden / surface
    for iss, count in issue_counter.most_common(3):
        if count < max(2, samples // 4):
            continue
        # Look for "ביטוי אסור: 'X'" form — those are content but already
        # tracked. The interesting ones are recurring style issues.
        m = re.search(r"ביטוי אסור: '([^']+)'", iss)
        if m:
            phrase = m.group(1)
            recs.append({
                "area": "forbidden_phrase",
                "suggestion": (
                    f"'{phrase}' זוהה ב-{count}/{samples} פוסטים "
                    f"(כבר ב-FORBIDDEN). לבדוק למה ה-writer ממשיך להשתמש בו."
                ),
                "auto_apply_safe": False,
                "payload": {"phrase": phrase, "count": count},
            })
        else:
            recs.append({
                "area": "voice_issue",
                "suggestion": (
                    f"בעיה חוזרת ({count}/{samples}): {iss}"
                ),
                "auto_apply_safe": False,
                "payload": {"issue": iss, "count": count},
            })

    # 7. Voice score too low overall
    if voice_score_avg is not None and voice_score_avg < 70:
        recs.append({
            "area": "voice_score_low",
            "suggestion": (
                f"ממוצע ציון voice הוא {voice_score_avg}/100 — "
                f"נמוך. לבחון את ה-system prompt של agent3."
            ),
            "auto_apply_safe": False,
            "payload": {"voice_avg": voice_score_avg},
        })

    if not recs:
        recs.append({
            "area": "healthy",
            "suggestion": "אין המלצות — Moki נראית בריאה. המשך כך.",
            "auto_apply_safe": False,
            "payload": {},
        })

    return recs


# ─────────────────────────────────────────────
# Apply recommendation
# ─────────────────────────────────────────────

def apply_recommendation(rec: dict, dry_run: bool = True) -> dict:
    """
    Apply a recommendation. If dry_run, just describe what would change.
    Returns: {"applied": bool, "dry_run": bool, "description": str,
              "changes": [...]}
    """
    area = rec.get("area")
    payload = rec.get("payload", {}) or {}

    if not rec.get("auto_apply_safe", False):
        return {
            "applied": False,
            "dry_run": dry_run,
            "description": (
                f"המלצה בתחום '{area}' אינה מסומנת כבטוחה ל-auto-apply. "
                f"דורשת אישור ידני."
            ),
            "changes": [],
        }

    if area == "forbidden_phrase":
        phrase = payload.get("phrase", "").strip()
        if not phrase:
            return {
                "applied": False,
                "dry_run": dry_run,
                "description": "אין ביטוי בפיילוד — לא ניתן להחיל.",
                "changes": [],
            }
        return _add_to_forbidden(phrase, dry_run=dry_run)

    if area == "field_example":
        example = payload.get("example", {})
        return _add_field_example(example, dry_run=dry_run)

    return {
        "applied": False,
        "dry_run": dry_run,
        "description": f"אזור '{area}' לא נתמך עדיין ל-auto-apply.",
        "changes": [],
    }


def _add_to_forbidden(phrase: str, dry_run: bool) -> dict:
    """Edit voice_profile.py to append `phrase` into _FORBIDDEN_PATTERNS."""
    vp_path = Path(__file__).parent / "voice_profile.py"
    if not vp_path.exists():
        return {
            "applied": False,
            "dry_run": dry_run,
            "description": f"voice_profile.py לא נמצא ({vp_path}).",
            "changes": [],
        }
    src = vp_path.read_text(encoding="utf-8")
    if phrase in src:
        return {
            "applied": False,
            "dry_run": dry_run,
            "description": f"'{phrase}' כבר נמצא ב-voice_profile.py.",
            "changes": [],
        }

    # Insert before the closing bracket of _FORBIDDEN_PATTERNS list. We look
    # for the exact list-opening line and the first ']' that follows.
    open_marker = "_FORBIDDEN_PATTERNS = ["
    open_idx = src.find(open_marker)
    if open_idx < 0:
        return {
            "applied": False,
            "dry_run": dry_run,
            "description": "לא מצאתי את _FORBIDDEN_PATTERNS ב-voice_profile.py.",
            "changes": [],
        }
    close_idx = src.find("]", open_idx)
    if close_idx < 0:
        return {
            "applied": False,
            "dry_run": dry_run,
            "description": "לא מצאתי את סוגרי הרשימה _FORBIDDEN_PATTERNS.",
            "changes": [],
        }

    insertion = f'    "{phrase}",\n'
    new_src = src[:close_idx] + insertion + src[close_idx:]
    description = f"מוסיף '{phrase}' ל-_FORBIDDEN_PATTERNS ב-voice_profile.py"

    if dry_run:
        return {
            "applied": False,
            "dry_run": True,
            "description": f"[DRY-RUN] {description}",
            "changes": [{
                "file": str(vp_path),
                "type": "append_to_list",
                "list": "_FORBIDDEN_PATTERNS",
                "value": phrase,
            }],
        }

    vp_path.write_text(new_src, encoding="utf-8")
    return {
        "applied": True,
        "dry_run": False,
        "description": description,
        "changes": [{
            "file": str(vp_path),
            "type": "append_to_list",
            "list": "_FORBIDDEN_PATTERNS",
            "value": phrase,
        }],
    }


def _add_field_example(example: dict, dry_run: bool) -> dict:
    """Append a new entry to FIELD_EXAMPLES in voice_profile.py."""
    themes = example.get("themes")
    moment = example.get("moment", "").strip()
    if not (isinstance(themes, list) and themes and moment):
        return {
            "applied": False,
            "dry_run": dry_run,
            "description": "דוגמה לא תקינה (חסרים themes/moment).",
            "changes": [],
        }

    vp_path = Path(__file__).parent / "voice_profile.py"
    if not vp_path.exists():
        return {
            "applied": False,
            "dry_run": dry_run,
            "description": "voice_profile.py לא נמצא.",
            "changes": [],
        }
    src = vp_path.read_text(encoding="utf-8")
    open_marker = "FIELD_EXAMPLES = ["
    open_idx = src.find(open_marker)
    if open_idx < 0:
        return {
            "applied": False,
            "dry_run": dry_run,
            "description": "לא מצאתי את FIELD_EXAMPLES.",
            "changes": [],
        }
    # Find the matching closing ']' — naive: last ']' before the next top
    # level def/var. Since FIELD_EXAMPLES is a list of dicts, find the line
    # `]\n\n\n` that follows the last "}," entry of the list.
    # Strategy: walk forward, tracking bracket depth.
    depth = 0
    i = open_idx + len(open_marker) - 1  # position of '['
    close_idx = -1
    while i < len(src):
        ch = src[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                close_idx = i
                break
        i += 1
    if close_idx < 0:
        return {
            "applied": False,
            "dry_run": dry_run,
            "description": "לא מצאתי את סוגרי הרשימה FIELD_EXAMPLES.",
            "changes": [],
        }

    themes_repr = ", ".join(f'"{t}"' for t in themes)
    moment_escaped = moment.replace('"', '\\"')
    new_entry = (
        "    {\n"
        f'        "themes": [{themes_repr}],\n'
        f'        "moment": "{moment_escaped}",\n'
        "    },\n"
    )
    new_src = src[:close_idx] + new_entry + src[close_idx:]
    description = (
        f"מוסיף דוגמת שטח חדשה ל-FIELD_EXAMPLES "
        f"(themes={themes}, length={len(moment)})"
    )

    if dry_run:
        return {
            "applied": False,
            "dry_run": True,
            "description": f"[DRY-RUN] {description}",
            "changes": [{
                "file": str(vp_path),
                "type": "append_to_list",
                "list": "FIELD_EXAMPLES",
                "value": example,
            }],
        }

    vp_path.write_text(new_src, encoding="utf-8")
    return {
        "applied": True,
        "dry_run": False,
        "description": description,
        "changes": [{
            "file": str(vp_path),
            "type": "append_to_list",
            "list": "FIELD_EXAMPLES",
            "value": example,
        }],
    }


# ─────────────────────────────────────────────
# Schedule helpers
# ─────────────────────────────────────────────

def _read_last_run() -> dict:
    return _load_json(LAST_RUN_FILE, {}) or {}


def _write_last_run(report: dict) -> None:
    _save_json(LAST_RUN_FILE, {
        "ran_at": datetime.now().isoformat(),
        "samples": report.get("samples", 0),
        "post_count_at_run": _count_published_posts(),
    })


def _count_published_posts() -> int:
    if not LINKEDIN_DIR.exists():
        return 0
    return sum(
        1 for p in LINKEDIN_DIR.glob("*_ready*.txt")
        if not p.name.endswith(".bak")
    )


def should_run_reflection() -> bool:
    """
    True if:
      - >10 new posts since last reflection, OR
      - >30 days since last reflection, OR
      - never ran AND we have at least 10 posts.
    """
    posts_now = _count_published_posts()
    last = _read_last_run()
    if not last:
        return posts_now >= 10

    last_count = int(last.get("post_count_at_run", 0))
    if posts_now - last_count >= 10:
        return True

    ran_at = last.get("ran_at")
    if ran_at:
        try:
            dt = datetime.fromisoformat(ran_at)
            if datetime.now() - dt > timedelta(days=30):
                return True
        except ValueError:
            return True

    return False


# ─────────────────────────────────────────────
# Persist + report
# ─────────────────────────────────────────────

def save_reflection(report: dict) -> Path:
    """Write the report to output/reflections/reflection_<ts>.md and update
    last_run.json. Returns path to the markdown file."""
    REFLECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = REFLECTIONS_DIR / f"reflection_{ts}.md"
    md_path.write_text(format_reflection_report(report), encoding="utf-8")

    # Also store the raw JSON next to it for programmatic re-use.
    json_path = REFLECTIONS_DIR / f"reflection_{ts}.json"
    _save_json(json_path, report)

    _write_last_run(report)
    return md_path


def format_reflection_report(report: dict) -> str:
    """Markdown summary of a reflection report."""
    n = report.get("samples", 0)
    if report.get("skipped"):
        return (
            f"# Moki Reflection — Skipped\n\n"
            f"{report.get('reason', '')}\n"
        )

    lines: list[str] = []
    lines.append(f"# Moki Reflection — {report.get('generated_at','')[:19]}")
    lines.append("")
    lines.append(f"**Samples analyzed:** {n} פוסטים אחרונים")
    lines.append("")

    perf = report.get("performance_summary", {}) or {}
    lines.append("## Performance")
    if perf.get("qa_avg_recent") is not None:
        lines.append(
            f"- QA ממוצע ב-{perf.get('qa_samples',0)} ריצות אחרונות: "
            f"**{perf['qa_avg_recent']}/100**"
        )
    if perf.get("voice_score_avg") is not None:
        lines.append(
            f"- Voice score ממוצע על {perf.get('voice_score_samples',0)} "
            f"פוסטים: **{perf['voice_score_avg']}/100**"
        )
    by_agent = perf.get("qa_by_agent") or {}
    if by_agent:
        lines.append("- QA לפי agent:")
        for ag, sc in sorted(by_agent.items(), key=lambda x: -x[1]):
            lines.append(f"  - {ag}: {sc}")
    if perf.get("perf_log_entries"):
        lines.append(
            f"- רשומות ב-performance_log: {perf['perf_log_entries']} "
            f"(התאמה ל-{perf.get('perf_log_matched',0)} מתוך {n} פוסטים)"
        )
    lines.append("")

    voice = report.get("voice_drift") or {}
    lines.append("## Voice Drift")
    if voice.get("samples"):
        lines.append(
            f"- ציון מגוון: **{voice.get('diversity_score','?')}/100** "
            f"(verdict: {voice.get('verdict','?')})"
        )
        for pat in (voice.get("patterns") or [])[:5]:
            t = pat.get("type")
            if t == "opening_repetition":
                ex = (pat.get("examples") or [""])[0]
                lines.append(
                    f"- פתיחה חוזרת ({pat.get('count',0)}): \"{ex[:60]}\""
                )
            elif t == "phrase_overuse":
                lines.append(
                    f"- ביטוי בשימוש-יתר ({pat.get('count',0)}/{n}): "
                    f"'{pat.get('phrase','')}'"
                )
            elif t == "structure_monotone":
                lines.append(
                    f"- מבנה מונוטוני: {pat.get('structure','')}"
                )
    else:
        lines.append("- (אין נתוני drift)")
    lines.append("")

    issues = report.get("common_issues") or []
    if issues:
        lines.append("## Common voice issues")
        for iss in issues:
            lines.append(f"- {iss}")
        lines.append("")

    best = report.get("best_performers") or []
    if best:
        lines.append("## Best performers")
        for p in best:
            lines.append(
                f"- {p.get('file','')} "
                f"(qa={p.get('qa')}, eng={p.get('engagement')}, "
                f"topic={p.get('topic') or '-'})"
            )
        lines.append("")

    weak = report.get("weak_performers") or []
    if weak:
        lines.append("## Weak performers")
        for p in weak:
            iss = "; ".join(p.get("issues") or []) or "-"
            lines.append(
                f"- {p.get('file','')} "
                f"(qa={p.get('qa')}, eng={p.get('engagement')})"
            )
            lines.append(f"  issues: {iss}")
        lines.append("")

    recs = report.get("recommendations") or []
    if recs:
        lines.append("## Recommendations")
        for i, r in enumerate(recs, 1):
            mark = "auto-safe" if r.get("auto_apply_safe") else "needs approval"
            lines.append(
                f"{i}. **[{r.get('area','?')}]** ({mark})\n"
                f"   {r.get('suggestion','')}"
            )
        lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# CLI smoke-test
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if "--should-run" in args:
        print("yes" if should_run_reflection() else "no")
        sys.exit(0)

    min_posts = 10
    for a in args:
        if a.isdigit():
            min_posts = int(a)
            break

    report = run_reflection(min_posts=min_posts)
    md = format_reflection_report(report)
    print(md)
    if not report.get("skipped"):
        path = save_reflection(report)
        print(f"\n[נשמר ב: {path}]")
