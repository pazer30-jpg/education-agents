"""
dashboard.py — דאשבורד ויזואלי אינטראקטיבי
מייצר דף HTML עם:
  - רענון אוטומטי כל 60 שניות
  - פילטר תקופה (שבוע / חודש / הכל)
  - כרטיס ריצה אחרונה
  - עלות מוערכת
  - מפת כיסוי נושאים
  - כפתור "הרץ עכשיו"

שימוש:
  python dashboard.py           # בנה ופתח בדפדפן
  python dashboard.py --serve   # שרת חי עם "הרץ עכשיו" (port 8787)
  python dashboard.py --no-open # בנה בלבד

פלט: output/dashboard.html
"""

import json
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


def _get_recent_files(directory: Path, patterns: list[str], limit: int = 5) -> list[dict]:
    files = []
    if not directory.exists():
        return files
    for p in patterns:
        for f in directory.glob(p):
            files.append({
                "name": f.name,
                "date": datetime.fromtimestamp(f.stat().st_mtime).strftime("%d/%m %H:%M"),
                "ts": f.stat().st_mtime,
            })
    files.sort(key=lambda x: x["ts"], reverse=True)
    return files[:limit]


def generate_dashboard() -> str:
    analytics = _load_analytics()
    runs = analytics.get("runs", [])
    mem = load_memory()

    # ── All data as JSON for JS filtering ─────
    runs_json = json.dumps(runs, ensure_ascii=False, default=str)

    # ── Static counts ─────────────────────────
    n_papers = _count_files(PAPERS_DIR, ["*.json"])
    n_articles = _count_files(ARTICLES_DIR, ["*.md"])
    n_linkedin = _count_files(LINKEDIN_DIR, ["*.txt"])
    n_blog = _count_files(BLOG_DIR, ["*.md"])
    n_podcast = _count_files(PODCAST_DIR, ["*.md"])
    n_designs = _count_files(OUTPUT_DIR / "designs", ["*.svg"])
    n_total = n_papers + n_articles + n_linkedin + n_blog + n_podcast + n_designs

    # ── Memory ────────────────────────────────
    total_papers_mem = len(mem.get("papers", {}))
    total_topics_mem = len(mem.get("researched_topics", []))
    researched_topics = mem.get("researched_topics", [])
    coverage_map = mem.get("coverage_map", {})
    # Merge: ensure all researched topics appear in coverage
    for t in researched_topics:
        if t not in coverage_map:
            coverage_map[t] = 1
    # Coverage counts for legend
    n_blocked = sum(1 for s in coverage_map.values() if s >= 7)
    n_caution = sum(1 for s in coverage_map.values() if 4 <= s < 7)
    n_available = sum(1 for s in coverage_map.values() if s < 4)
    queue = mem.get("topic_queue", [])[:6]
    gaps = mem.get("gaps", [])[:5]
    iterations = mem.get("iterations", 0)

    # ── Coverage map data ─────────────────────
    coverage_json = json.dumps(coverage_map, ensure_ascii=False)

    # ── Recent files ──────────────────────────
    recent_files = (
        _get_recent_files(ARTICLES_DIR, ["*.md"], 3) +
        _get_recent_files(LINKEDIN_DIR, ["*_ready.txt"], 2) +
        _get_recent_files(BLOG_DIR, ["*.md"], 2) +
        _get_recent_files(PODCAST_DIR, ["*_script_*.md"], 2)
    )
    recent_files.sort(key=lambda x: x["ts"], reverse=True)
    recent_files = recent_files[:6]

    files_html = ""
    for f in recent_files:
        icon = "📝" if "article" in f["name"] or f["name"].endswith("_en.md") or f["name"].endswith("_he.md") else \
               "💼" if "linkedin" in f["name"] else "📰" if "blog" in f["name"] else "🎙️"
        files_html += f'<div class="file-item"><span class="fi">{icon}</span><span class="fn">{f["name"][:32]}</span><span class="fd">{f["date"]}</span></div>'

    queue_html = "".join(f'<div class="qi"><span class="qn">{i}</span>{t}</div>' for i, t in enumerate(queue, 1))
    if not queue:
        queue_html = '<div class="empty">אין נושאים בתור</div>'

    gaps_html = "".join(f'<div class="gi">⚠️ {g}</div>' for g in gaps)
    if not gaps:
        gaps_html = '<div class="empty ok">לא זוהו פערים</div>'

    html = f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>מוקי — Dashboard</title>
<style>
:root {{
  --bg:#0c0e14;--card:#151821;--border:#1e2233;--hover:#1a1f2e;
  --t:#d4d4d8;--td:#71717a;--tb:#fafafa;
  --red:#E94560;--green:#4ade80;--blue:#60a5fa;--orange:#F5A623;--purple:#a78bfa;
  --r:14px;
}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Inter',-apple-system,sans-serif;background:var(--bg);color:var(--t);padding:20px;max-width:1240px;margin:0 auto}}

.hdr{{text-align:center;padding:24px 0 8px}}
.hdr h1{{font-size:30px;color:var(--tb);font-weight:800}}
.hdr .sub{{color:var(--td);font-size:12px;margin:6px 0}}
.pipe{{display:flex;justify-content:center;gap:4px;margin:12px 0;flex-wrap:wrap}}
.pipe .s{{background:var(--card);border:1px solid var(--border);padding:5px 12px;border-radius:18px;font-size:11px;color:var(--td)}}
.pipe .a{{color:#444;padding-top:5px;font-size:10px}}

/* Controls */
.controls{{display:flex;justify-content:center;gap:8px;margin:16px 0}}
.btn{{background:var(--card);border:1px solid var(--border);color:var(--t);padding:8px 18px;border-radius:20px;font-size:12px;cursor:pointer;transition:all .2s}}
.btn:hover{{border-color:#444;color:var(--tb)}}
.btn.active{{background:var(--red);border-color:var(--red);color:#fff}}
.btn.run{{background:#1a2e1a;border-color:#2d5a2d;color:var(--green)}}
.btn.run:hover{{background:#2d5a2d}}
.refresh-dot{{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--green);margin-left:6px;animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}

/* Stats */
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:12px;margin:18px 0}}
.st{{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:18px;transition:border .2s}}
.st:hover{{border-color:#333}}
.st .l{{font-size:10px;color:var(--td);text-transform:uppercase;letter-spacing:1px}}
.st .v{{font-size:34px;font-weight:800;margin:4px 0}}
.st .d{{font-size:11px;color:var(--td)}}

/* Last run card */
.last-run{{background:linear-gradient(135deg,#1a1d27,#1e2435);border:1px solid var(--border);border-radius:var(--r);padding:22px;margin:14px 0;position:relative;overflow:hidden}}
.last-run::before{{content:'';position:absolute;top:0;right:0;width:4px;height:100%;border-radius:0 var(--r) var(--r) 0}}
.last-run.success::before{{background:var(--green)}}
.last-run.fail::before{{background:var(--red)}}
.last-run h3{{font-size:14px;color:var(--tb);margin-bottom:10px}}
.last-run .lr-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px}}
.last-run .lr-item .lr-label{{font-size:10px;color:var(--td);text-transform:uppercase}}
.last-run .lr-item .lr-val{{font-size:18px;font-weight:700;color:var(--tb);margin-top:2px}}

/* Sections */
.sec{{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:20px;margin:12px 0}}
.sec h2{{font-size:15px;font-weight:700;color:var(--tb);margin-bottom:12px;display:flex;align-items:center;gap:8px}}
.g2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.g3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}}
@media(max-width:768px){{.g2,.g3{{grid-template-columns:1fr}}}}

/* Chart */
.chart{{display:flex;gap:3px;align-items:flex-end;height:130px;padding:8px 0}}
.cb{{flex:1;min-width:12px;border-radius:3px 3px 0 0;position:relative;cursor:pointer;transition:opacity .2s}}
.cb:hover{{opacity:.7}}
.cb .bv{{position:absolute;top:-20px;left:50%;transform:translateX(-50%);font-size:10px;color:var(--td);opacity:0;transition:opacity .2s}}
.cb:hover .bv{{opacity:1}}

/* Table */
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{text-align:right;padding:8px 6px;color:var(--td);font-size:10px;text-transform:uppercase;letter-spacing:.5px;border-bottom:2px solid var(--border)}}
td{{padding:8px 6px;border-bottom:1px solid #1a1d28}}
tr:hover td{{background:var(--hover)}}
.qg{{color:var(--green);font-weight:600}}
.qw{{color:var(--orange);font-weight:600}}
.bok{{background:#0f2a1a;color:var(--green);padding:2px 8px;border-radius:8px;font-size:10px}}
.ber{{background:#2a0f14;color:var(--red);padding:2px 8px;border-radius:8px;font-size:10px}}

/* Agent bars */
.ag{{display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid #1a1d28}}
.ag:last-child{{border:none}}
.ag-i{{font-size:16px;width:22px;text-align:center}}
.ag-n{{min-width:72px;font-size:12px;font-weight:600;color:var(--tb)}}
.ag-c{{font-size:11px;color:var(--td);min-width:32px}}
.ag-t{{font-size:11px;color:var(--td);min-width:36px}}
.ag-bar{{flex:1;height:7px;background:#1e2233;border-radius:4px;overflow:hidden}}
.ag-fill{{height:100%;border-radius:4px;transition:width .3s}}
.ag-v{{font-size:11px;font-weight:600;min-width:26px;text-align:left}}

/* Topics horizontal bar */
.hb{{display:flex;align-items:center;margin:5px 0;gap:8px}}
.hb-l{{min-width:130px;font-size:11px;color:var(--td);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left}}
.hb-b{{background:var(--red);height:22px;border-radius:3px;color:#fff;font-size:10px;font-weight:600;display:flex;align-items:center;padding:0 8px;min-width:24px;transition:width .3s}}

/* Coverage cloud */
.cloud{{display:flex;flex-wrap:wrap;gap:6px;padding:8px 0}}
.tag{{padding:5px 12px;border-radius:16px;font-size:11px;font-weight:500;border:1px solid;transition:transform .2s}}
.tag:hover{{transform:scale(1.05)}}
.tag.high{{background:#1a2e1a;border-color:#2d5a2d;color:var(--green)}}
.tag.med{{background:#2a2510;border-color:#5a4a1a;color:var(--orange)}}
.tag.low{{background:#2a1520;border-color:#5a2a3a;color:var(--red)}}

/* Queue, gaps, files */
.qi{{padding:6px 8px;border-bottom:1px solid #1a1d28;font-size:12px;display:flex;gap:8px;align-items:center}}
.qi:last-child{{border:none}}
.qn{{background:var(--red);color:#fff;width:20px;height:20px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;flex-shrink:0}}
.gi{{padding:6px 8px;border-bottom:1px solid #1a1d28;font-size:12px}}
.gi:last-child{{border:none}}
.file-item{{display:flex;align-items:center;gap:6px;padding:5px 0;border-bottom:1px solid #1a1d28;font-size:11px}}
.file-item:last-child{{border:none}}
.fi{{font-size:14px}}
.fn{{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.fd{{color:var(--td);font-size:10px}}

/* Errors */
.ei{{display:flex;gap:8px;padding:7px 0;border-bottom:1px solid #1a1d28;font-size:11px;align-items:center}}
.ei:last-child{{border:none}}
.ea{{background:#2a0f14;color:var(--red);padding:2px 7px;border-radius:6px;font-size:10px;font-weight:600;flex-shrink:0}}
.em{{color:var(--td);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.et{{color:#555;font-size:10px;flex-shrink:0}}
.empty{{color:var(--td);font-size:12px;padding:8px 0;text-align:center}}
.empty.ok{{color:var(--green)}}
.cost{{color:var(--orange);font-weight:600}}
.ft{{text-align:center;color:var(--td);font-size:10px;padding:18px 0}}
</style>
</head>
<body>

<div class="hdr">
  <h1>🤖 מוקי</h1>
  <div class="sub">לוח בקרה <span class="refresh-dot"></span> רענון אוטומטי כל 60 שניות</div>
  <div class="pipe">
    <span class="s">🧠 Planner</span><span class="a">→</span>
    <span class="s">🔍 Research×3</span><span class="a">→</span>
    <span class="s">📄 PDF</span><span class="a">→</span>
    <span class="s">✍️ Writer</span><span class="a">→</span>
    <span class="s">✏️ Editor</span><span class="a">→</span>
    <span class="s">✨ Content</span><span class="a">→</span>
    <span class="s">🎨 Design</span>
  </div>
</div>

<!-- Controls: period filter + run button -->
<div class="controls">
  <button class="btn active" onclick="setPeriod(7)">שבוע</button>
  <button class="btn" onclick="setPeriod(30)">חודש</button>
  <button class="btn" onclick="setPeriod(0)">הכל</button>
  <button class="btn run" onclick="runNow()">▶ הרץ עכשיו</button>
</div>

<!-- Last run card -->
<div id="lastRun"></div>

<!-- Stat cards -->
<div class="stats" id="statCards"></div>

<!-- Charts -->
<div class="g2">
  <div class="sec"><h2>📈 QA לאורך זמן</h2><div class="chart" id="qaChart"></div></div>
  <div class="sec"><h2>⏱️ זמן ריצה (דקות)</h2><div class="chart" id="durChart"></div></div>
</div>

<!-- Agent perf + Coverage -->
<div class="g2">
  <div class="sec"><h2>🤖 ביצועי סוכנים</h2><div id="agentPerf"></div></div>
  <div class="sec">
    <h2>🗺️ כיסוי נושאים ({len(coverage_map)})</h2>
    <div style="font-size:11px;color:var(--td);margin-bottom:8px">
      <span class="tag high" style="font-size:10px">🔴 BLOCKED ({n_blocked})</span>
      <span class="tag med" style="font-size:10px">🟡 CAUTION ({n_caution})</span>
      <span class="tag low" style="font-size:10px">🟢 AVAILABLE ({n_available})</span>
    </div>
    <div class="cloud" id="coverageMap"></div>
  </div>
</div>

<!-- Runs table -->
<div class="sec">
  <h2>📊 ריצות אחרונות</h2>
  <table><thead><tr><th></th><th>תאריך</th><th>נושא</th><th>שלבים</th><th>זמן</th><th>QA</th><th>עלות</th><th>סטטוס</th></tr></thead>
  <tbody id="runsTable"></tbody></table>
</div>

<!-- Queue + Gaps + Files -->
<div class="g3">
  <div class="sec"><h2>🔮 בתור</h2>{queue_html}</div>
  <div class="sec"><h2>⚠️ פערים</h2>{gaps_html}</div>
  <div class="sec"><h2>📁 אחרונים</h2>{files_html if files_html else '<div class="empty">אין קבצים</div>'}</div>
</div>

<!-- Errors -->
<div class="sec"><h2>🔴 שגיאות</h2><div id="errorsDiv"></div></div>

<!-- Quick access -->
<div class="sec" style="background:linear-gradient(135deg,#151821,#1a2030)">
  <h2>🚀 גישה מהירה למוקי</h2>
  <div style="font-size:13px;line-height:2.2;color:var(--td)">
    <div><span style="color:var(--tb);font-weight:600">צ'אט אינטראקטיבי:</span> <code style="background:#1e2233;padding:3px 10px;border-radius:6px;color:var(--green);font-size:12px">python3 agent5_project_manager.py --chat</code></div>
    <div><span style="color:var(--tb);font-weight:600">הרצה אוטומטית:</span> <code style="background:#1e2233;padding:3px 10px;border-radius:6px;color:var(--blue);font-size:12px">python3 agent5_project_manager.py "הרץ הכל" --auto</code></div>
    <div><span style="color:var(--tb);font-weight:600">Pipeline ישיר:</span> <code style="background:#1e2233;padding:3px 10px;border-radius:6px;color:var(--orange);font-size:12px">python3 orchestrator.py "non-formal education" --parallel</code></div>
    <div><span style="color:var(--tb);font-weight:600">סיכום שבועי:</span> <code style="background:#1e2233;padding:3px 10px;border-radius:6px;color:var(--purple);font-size:12px">python3 weekly_summary.py --save</code></div>
    <div><span style="color:var(--tb);font-weight:600">ביבליוגרפיה:</span> <code style="background:#1e2233;padding:3px 10px;border-radius:6px;color:var(--red);font-size:12px">python3 bibliography.py --stats</code></div>
    <div><span style="color:var(--tb);font-weight:600">דאשבורד חי:</span> <code style="background:#1e2233;padding:3px 10px;border-radius:6px;color:var(--green);font-size:12px">python3 dashboard.py --serve</code></div>
    <div style="margin-top:6px;color:#555;font-size:11px">תיקייה: <code style="color:#666">~/Desktop/education-agents/</code></div>
  </div>
</div>

<div class="ft">מוקי — Education Agents Pipeline · {datetime.now().strftime('%Y')}</div>

<script>
const ALL_RUNS = {runs_json};
const COVERAGE = {coverage_json};
const FILE_COUNTS = {{papers:{n_papers},articles:{n_articles},linkedin:{n_linkedin},blog:{n_blog},podcast:{n_podcast},designs:{n_designs},total:{n_total}}};
const MEM = {{topics:{total_topics_mem},papers:{total_papers_mem},iterations:{iterations}}};

let currentPeriod = 7;

function setPeriod(days) {{
  currentPeriod = days;
  document.querySelectorAll('.controls .btn:not(.run)').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');
  render();
}}

function filterRuns(days) {{
  if (days === 0) return ALL_RUNS;
  const cutoff = new Date(Date.now() - days*86400000).toISOString();
  return ALL_RUNS.filter(r => (r.started_at||'') >= cutoff);
}}

function render() {{
  const runs = filterRuns(currentPeriod);
  renderLastRun(runs);
  renderStats(runs);
  renderQAChart(runs);
  renderDurChart(runs);
  renderAgentPerf(runs);
  renderCoverage();
  renderTable(runs);
  renderErrors(runs);
}}

function renderLastRun(runs) {{
  const el = document.getElementById('lastRun');
  if (!runs.length) {{ el.innerHTML = ''; return; }}
  const r = runs[runs.length-1];
  const ok = r.success;
  const dur = r.duration_s ? (r.duration_s/60).toFixed(1) : '?';
  const date = (r.started_at||'').slice(0,16).replace('T',' ');
  const steps = (r.steps||[]).length;
  const cost = r.est_cost ? '$'+r.est_cost : '~$8';
  const errs = (r.errors||[]).length;
  el.innerHTML = `
    <div class="last-run ${{ok?'success':'fail'}}">
      <h3>${{ok?'✅':'❌'}} ריצה אחרונה — ${{date}}</h3>
      <div class="lr-grid">
        <div class="lr-item"><div class="lr-label">נושא</div><div class="lr-val" style="font-size:14px">${{(r.topic||'').slice(0,40)}}</div></div>
        <div class="lr-item"><div class="lr-label">זמן</div><div class="lr-val">${{dur}} דק'</div></div>
        <div class="lr-item"><div class="lr-label">QA</div><div class="lr-val" style="color:${{(r.avg_qa||0)>=80?'var(--green)':'var(--orange)'}}">${{r.avg_qa||'—'}}/100</div></div>
        <div class="lr-item"><div class="lr-label">שלבים</div><div class="lr-val">${{steps}}</div></div>
        <div class="lr-item"><div class="lr-label">עלות</div><div class="lr-val cost">${{cost}}</div></div>
        <div class="lr-item"><div class="lr-label">שגיאות</div><div class="lr-val" style="color:${{errs?'var(--red)':'var(--green)'}}">${{errs||'0'}}</div></div>
      </div>
    </div>`;
}}

function renderStats(runs) {{
  const succ = runs.filter(r=>r.success).length;
  const total = runs.length;
  const rate = total ? Math.round(succ/total*100) : 0;
  const qas = runs.map(r=>r.avg_qa).filter(Boolean);
  const avgQa = qas.length ? Math.round(qas.reduce((a,b)=>a+b,0)/qas.length) : 0;
  const durs = runs.map(r=>r.duration_s).filter(Boolean);
  const avgDur = durs.length ? (durs.reduce((a,b)=>a+b,0)/durs.length/60).toFixed(1) : '0';
  const totalH = durs.length ? (durs.reduce((a,b)=>a+b,0)/3600).toFixed(1) : '0';
  const costs = runs.map(r=>r.est_cost||0);
  const totalCost = costs.reduce((a,b)=>a+b,0).toFixed(2);
  const avgCost = costs.length ? (totalCost/costs.length).toFixed(2) : '0';

  document.getElementById('statCards').innerHTML = `
    <div class="st"><div class="l">ריצות</div><div class="v" style="color:var(--red)">${{total}}</div><div class="d">${{succ}} הצלחות · ${{total-succ}} כשלים</div></div>
    <div class="st"><div class="l">הצלחה</div><div class="v" style="color:var(--green)">${{rate}}%</div><div class="d">${{currentPeriod?currentPeriod+' ימים':'הכל'}}</div></div>
    <div class="st"><div class="l">QA ממוצע</div><div class="v" style="color:var(--blue)">${{avgQa}}</div><div class="d">מתוך 100</div></div>
    <div class="st"><div class="l">זמן ממוצע</div><div class="v" style="color:var(--orange)">${{avgDur}}</div><div class="d">דקות · סה"כ ${{totalH}}h</div></div>
    <div class="st"><div class="l">עלות</div><div class="v" style="color:var(--purple)">${{totalCost}}$</div><div class="d">ממוצע ${{avgCost}}$ לריצה</div></div>
    <div class="st"><div class="l">תוכן</div><div class="v" style="color:var(--tb)">${{FILE_COUNTS.total}}</div><div class="d">📝${{FILE_COUNTS.articles}} 💼${{FILE_COUNTS.linkedin}} 📰${{FILE_COUNTS.blog}} 🎙️${{FILE_COUNTS.podcast}}</div></div>
  `;
}}

function renderQAChart(runs) {{
  const data = runs.slice(-20).filter(r=>r.avg_qa);
  const el = document.getElementById('qaChart');
  if (!data.length) {{ el.innerHTML='<div class="empty">אין נתונים</div>'; return; }}
  const max = Math.max(...data.map(d=>d.avg_qa));
  el.innerHTML = data.map(d => {{
    const h = Math.max(5, d.avg_qa/max*120);
    const c = d.avg_qa>=80?'var(--green)':d.avg_qa>=60?'var(--orange)':'var(--red)';
    return `<div class="cb" style="height:${{h}}px;background:${{c}}" title="${{(d.started_at||'').slice(0,10)}}: ${{d.avg_qa}}"><span class="bv">${{d.avg_qa}}</span></div>`;
  }}).join('');
}}

function renderDurChart(runs) {{
  const data = runs.slice(-20).filter(r=>r.duration_s);
  const el = document.getElementById('durChart');
  if (!data.length) {{ el.innerHTML='<div class="empty">אין נתונים</div>'; return; }}
  const max = Math.max(...data.map(d=>d.duration_s));
  el.innerHTML = data.map(d => {{
    const m = (d.duration_s/60).toFixed(1);
    const h = Math.max(5, d.duration_s/max*120);
    return `<div class="cb" style="height:${{h}}px;background:var(--blue)" title="${{m}}m"><span class="bv">${{m}}</span></div>`;
  }}).join('');
}}

function renderAgentPerf(runs) {{
  const stats = {{}};
  const order = ['planner','researcher','writer','content','designer'];
  const icons = {{planner:'🧠',researcher:'🔍',writer:'✍️',content:'✨',designer:'🎨'}};
  runs.forEach(r => (r.steps||[]).forEach(s => {{
    if (!stats[s.agent]) stats[s.agent] = {{count:0,time:0,qa:[]}};
    stats[s.agent].count++;
    stats[s.agent].time += s.duration_s||0;
    if (s.qa_score) stats[s.agent].qa.push(s.qa_score);
  }}));
  const el = document.getElementById('agentPerf');
  if (!Object.keys(stats).length) {{ el.innerHTML='<div class="empty">אין נתונים</div>'; return; }}
  el.innerHTML = order.filter(a=>stats[a]).map(a => {{
    const s = stats[a];
    const avgT = (s.time/Math.max(1,s.count)).toFixed(0);
    const avgQ = s.qa.length ? Math.round(s.qa.reduce((a,b)=>a+b,0)/s.qa.length) : 0;
    const c = avgQ>=80?'var(--green)':avgQ>=60?'var(--orange)':'var(--red)';
    return `<div class="ag"><span class="ag-i">${{icons[a]||''}}</span><span class="ag-n">${{a}}</span><span class="ag-c">${{s.count}}x</span><span class="ag-t">${{avgT}}s</span><div class="ag-bar"><div class="ag-fill" style="width:${{Math.max(5,avgQ)}}%;background:${{c}}"></div></div><span class="ag-v">${{avgQ}}</span></div>`;
  }}).join('');
}}

function renderCoverage() {{
  const el = document.getElementById('coverageMap');
  const entries = Object.entries(COVERAGE).sort((a,b)=>b[1]-a[1]);
  if (!entries.length) {{ el.innerHTML='<div class="empty">אין נתוני כיסוי</div>'; return; }}
  const max = entries[0][1];
  el.innerHTML = entries.map(([topic, score]) => {{
    const cls = score >= 7 ? 'high' : score >= 4 ? 'med' : 'low';
    const label = score >= 7 ? 'BLOCKED' : score >= 4 ? 'CAUTION' : 'AVAILABLE';
    const size = 11 + Math.min(5, Math.round(score));
    return `<span class="tag ${{cls}}" style="font-size:${{size}}px" title="${{topic}}: ${{score}} pts — ${{label}}">${{topic}}</span>`;
  }}).join('');
}}

function renderTable(runs) {{
  const el = document.getElementById('runsTable');
  const recent = runs.slice(-12).reverse();
  if (!recent.length) {{ el.innerHTML='<tr><td colspan="8" style="text-align:center;color:var(--td)">אין ריצות</td></tr>'; return; }}
  el.innerHTML = recent.map(r => {{
    const icon = r.success ? '✅' : '❌';
    const date = (r.started_at||'').slice(0,16).replace('T',' ');
    const dur = r.duration_s ? (r.duration_s/60).toFixed(1) : '?';
    const qa = r.avg_qa||'—';
    const qaCls = typeof qa==='number' && qa>=80 ? 'qg' : typeof qa==='number' ? 'qw' : '';
    const cost = r.est_cost ? '$'+r.est_cost : '—';
    const errs = (r.errors||[]).length;
    const badge = errs ? `<span class="ber">${{errs}} שגיאות</span>` : '<span class="bok">תקין</span>';
    return `<tr><td>${{icon}}</td><td style="color:var(--td);font-size:11px">${{date}}</td><td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${{r.topic||''}}">${{(r.topic||'').slice(0,35)}}</td><td>${{(r.steps||[]).length}}</td><td>${{dur}} דק'</td><td class="${{qaCls}}">${{qa}}</td><td class="cost">${{cost}}</td><td>${{badge}}</td></tr>`;
  }}).join('');
}}

function renderErrors(runs) {{
  const el = document.getElementById('errorsDiv');
  const errs = [];
  runs.forEach(r => (r.errors||[]).forEach(e => errs.push(e)));
  const recent = errs.slice(-5).reverse();
  if (!recent.length) {{ el.innerHTML='<div class="empty ok">אין שגיאות</div>'; return; }}
  el.innerHTML = recent.map(e => `<div class="ei"><span class="ea">${{e.agent||'?'}}</span><span class="em">${{(e.error||'').slice(0,80)}}</span><span class="et">${{(e.time||'').slice(0,16).replace('T',' ')}}</span></div>`).join('');
}}

function runNow() {{
  const btn = event.target;
  btn.textContent = '⏳ מריץ...';
  btn.disabled = true;
  fetch('/run', {{method:'POST'}})
    .then(r => r.text())
    .then(t => {{ btn.textContent = '✅ הסתיים'; setTimeout(()=>{{btn.textContent='▶ הרץ עכשיו';btn.disabled=false}}, 3000); }})
    .catch(() => {{ btn.textContent = '⚠️ השתמש בטרמינל'; setTimeout(()=>{{btn.textContent='▶ הרץ עכשיו';btn.disabled=false}}, 3000); }});
}}

// Auto-refresh every 60 seconds
setInterval(() => location.reload(), 60000);

// Initial render
render();
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
                # Regenerate on each request
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
                # Run in background
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
            pass  # suppress logs

    build_dashboard(open_browser=False)
    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    print(f"  🌐 Dashboard server: http://localhost:{port}")
    webbrowser.open(f"http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  ⛔ Server stopped")


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve_dashboard()
    else:
        build_dashboard(open_browser="--no-open" not in sys.argv)
