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


def _ready_to_publish() -> dict:
    """כמה תוצרים מוכנים לפרסום (לא .bak, לא template)."""
    def _count_real(d: Path, pattern: str) -> int:
        if not d.exists():
            return 0
        return sum(1 for p in d.glob(pattern) if not p.name.endswith(".bak"))
    return {
        "linkedin": _count_real(LINKEDIN_DIR, "*_ready.txt"),
        "blog":     _count_real(BLOG_DIR, "*.md"),
        "podcast":  _count_real(PODCAST_DIR, "*_script_*.md"),
    }


def generate_dashboard() -> str:
    analytics = _load_analytics()
    runs = analytics.get("runs", [])
    mem = load_memory()

    # ── All data as JSON for JS filtering ─────
    runs_json = json.dumps(runs, ensure_ascii=False, default=str)

    # ── File counts ───────────────────────────
    ready = _ready_to_publish()
    n_ready_total = ready["linkedin"] + ready["blog"] + ready["podcast"]
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
  --bg:#0a0b10;--surface:#12141c;--card:#171a24;--border:#222636;
  --t:#e4e4e7;--td:#71717a;--tb:#fafafa;--tdim:#52525b;
  --red:#E94560;--green:#4ade80;--blue:#60a5fa;--orange:#F5A623;--purple:#a78bfa;
  --r:12px;
}}
*{{margin:0;padding:0;box-sizing:border-box}}
html{{scroll-behavior:smooth}}
body{{font-family:-apple-system,'Inter','Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--t);padding:16px 20px 40px;max-width:1200px;margin:0 auto;line-height:1.5}}

/* ─── Topbar ─── */
.top{{display:flex;justify-content:space-between;align-items:center;padding:12px 0 20px;border-bottom:1px solid var(--border);margin-bottom:24px}}
.top .brand{{display:flex;align-items:center;gap:10px}}
.top .brand .logo{{font-size:22px}}
.top .brand h1{{font-size:18px;color:var(--tb);font-weight:700;letter-spacing:-.3px}}
.top .brand .tag{{font-size:10px;color:var(--td);padding:2px 8px;background:var(--card);border-radius:10px;margin-right:4px}}
.top .filters{{display:flex;gap:4px}}
.top .filters .btn{{background:transparent;border:1px solid var(--border);color:var(--td);padding:6px 14px;border-radius:8px;font-size:11px;cursor:pointer;transition:all .15s}}
.top .filters .btn:hover{{color:var(--tb);border-color:#333}}
.top .filters .btn.active{{background:var(--tb);border-color:var(--tb);color:#000;font-weight:600}}

/* ─── Hero ─── */
.hero{{background:linear-gradient(135deg,#151a29 0%,#1a1f30 100%);border:1px solid var(--border);border-radius:var(--r);padding:28px 32px;margin-bottom:20px;position:relative;overflow:hidden}}
.hero::before{{content:'';position:absolute;top:-40%;right:-10%;width:400px;height:400px;background:radial-gradient(circle,rgba(74,222,128,.08) 0%,transparent 70%);pointer-events:none}}
.hero-grid{{display:grid;grid-template-columns:1fr auto;gap:20px;align-items:center;position:relative}}
.hero-num{{font-size:72px;font-weight:800;color:var(--tb);line-height:1;letter-spacing:-2px;background:linear-gradient(135deg,#fff 0%,#a78bfa 100%);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.hero-label{{font-size:13px;color:var(--td);margin-top:6px;font-weight:500}}
.hero-breakdown{{display:flex;gap:18px;margin-top:14px;font-size:12px}}
.hero-breakdown span{{color:var(--td)}}
.hero-breakdown span b{{color:var(--tb);font-weight:700;margin-left:2px}}
.hero-cta{{display:flex;flex-direction:column;gap:8px;align-items:flex-end}}
.btn-primary{{background:var(--green);color:#0a0b10;border:none;padding:12px 24px;border-radius:10px;font-size:13px;font-weight:700;cursor:pointer;transition:all .15s;white-space:nowrap}}
.btn-primary:hover{{background:#6ef095;transform:translateY(-1px)}}
.btn-secondary{{background:transparent;color:var(--td);border:1px solid var(--border);padding:8px 16px;border-radius:8px;font-size:11px;cursor:pointer}}
.btn-secondary:hover{{color:var(--tb);border-color:#333}}

/* ─── Compact stat strip ─── */
.strip{{display:grid;grid-template-columns:repeat(5,1fr);gap:1px;background:var(--border);border:1px solid var(--border);border-radius:var(--r);overflow:hidden;margin-bottom:20px}}
.strip .c{{background:var(--card);padding:16px 18px}}
.strip .c .l{{font-size:10px;color:var(--td);text-transform:uppercase;letter-spacing:.8px;font-weight:600}}
.strip .c .v{{font-size:24px;font-weight:700;color:var(--tb);margin-top:4px;letter-spacing:-.5px}}
.strip .c .d{{font-size:11px;color:var(--td);margin-top:2px}}
@media(max-width:900px){{.strip{{grid-template-columns:repeat(2,1fr)}}}}

.refresh-dot{{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--green);margin-left:6px;animation:pulse 2s infinite;vertical-align:middle}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}

/* ─── Sections ─── */
.sec{{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:18px 20px;margin-bottom:16px}}
.sec h2{{font-size:12px;font-weight:700;color:var(--td);text-transform:uppercase;letter-spacing:.8px;margin-bottom:14px;display:flex;align-items:center;gap:8px}}
.sec h2 .count{{background:var(--border);color:var(--tb);padding:1px 7px;border-radius:8px;font-size:10px;font-weight:600}}
.g2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.g3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}}
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

<!-- Topbar -->
<div class="top">
  <div class="brand">
    <span class="logo">🤖</span>
    <h1>מוקי</h1>
    <span class="tag">Education Agents</span>
    <span class="refresh-dot"></span>
  </div>
  <div class="filters">
    <button class="btn active" onclick="setPeriod(7, event)">שבוע</button>
    <button class="btn" onclick="setPeriod(30, event)">חודש</button>
    <button class="btn" onclick="setPeriod(0, event)">הכל</button>
  </div>
</div>

<!-- Hero: Ready to publish -->
<div class="hero">
  <div class="hero-grid">
    <div>
      <div class="hero-num">{n_ready_total}</div>
      <div class="hero-label">תוצרים מוכנים לפרסום</div>
      <div class="hero-breakdown">
        <span>💼 LinkedIn <b>{ready['linkedin']}</b></span>
        <span>📰 Blog <b>{ready['blog']}</b></span>
        <span>🎙️ Podcast <b>{ready['podcast']}</b></span>
      </div>
    </div>
    <div class="hero-cta">
      <button class="btn-primary" onclick="runNow()">▶ הרץ pipeline חדש</button>
      <button class="btn-secondary" onclick="location.reload()">↻ רענן</button>
    </div>
  </div>
</div>

<!-- Compact stat strip -->
<div class="strip" id="statStrip"></div>

<!-- Charts -->
<div class="g2">
  <div class="sec"><h2>📈 QA לאורך זמן</h2><div class="chart" id="qaChart"></div></div>
  <div class="sec"><h2>⏱️ זמן ריצה (דקות)</h2><div class="chart" id="durChart"></div></div>
</div>

<!-- Agent perf + Coverage -->
<div class="g2">
  <div class="sec"><h2>🤖 ביצועי סוכנים</h2><div id="agentPerf"></div></div>
  <div class="sec">
    <h2>🗺️ כיסוי נושאים <span class="count">{len(coverage_map)}</span></h2>
    <div style="font-size:10px;color:var(--td);margin-bottom:10px;display:flex;gap:8px">
      <span class="tag low" style="font-size:10px">🟢 פנוי ({n_available})</span>
      <span class="tag med" style="font-size:10px">🟡 זהירות ({n_caution})</span>
      <span class="tag high" style="font-size:10px">🔴 חסום ({n_blocked})</span>
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
  <div class="sec"><h2>📁 תוצרים אחרונים</h2>{files_html if files_html else '<div class="empty">אין קבצים</div>'}</div>
</div>

<!-- Errors -->
<div class="sec"><h2>🔴 שגיאות אחרונות</h2><div id="errorsDiv"></div></div>

<div class="ft">מוקי · {datetime.now().strftime('%d/%m/%Y %H:%M')}</div>

<script>
const ALL_RUNS = {runs_json};
const COVERAGE = {coverage_json};
const FILE_COUNTS = {{papers:{n_papers},articles:{n_articles},linkedin:{n_linkedin},blog:{n_blog},podcast:{n_podcast},designs:{n_designs},total:{n_total}}};
const MEM = {{topics:{total_topics_mem},papers:{total_papers_mem},iterations:{iterations}}};

let currentPeriod = 7;

function setPeriod(days, ev) {{
  currentPeriod = days;
  document.querySelectorAll('.filters .btn').forEach(b => b.classList.remove('active'));
  (ev && ev.target || document.querySelector('.filters .btn')).classList.add('active');
  render();
}}

function filterRuns(days) {{
  if (days === 0) return ALL_RUNS;
  const cutoff = new Date(Date.now() - days*86400000).toISOString();
  return ALL_RUNS.filter(r => (r.started_at||'') >= cutoff);
}}

function render() {{
  const runs = filterRuns(currentPeriod);
  renderStrip(runs);
  renderQAChart(runs);
  renderDurChart(runs);
  renderAgentPerf(runs);
  renderCoverage();
  renderTable(runs);
  renderErrors(runs);
}}

function renderStrip(runs) {{
  const succ = runs.filter(r=>r.success).length;
  const total = runs.length;
  const rate = total ? Math.round(succ/total*100) : 0;
  const qas = runs.map(r=>r.avg_qa).filter(Boolean);
  const avgQa = qas.length ? Math.round(qas.reduce((a,b)=>a+b,0)/qas.length) : 0;
  const durs = runs.map(r=>r.duration_s).filter(Boolean);
  const avgDur = durs.length ? (durs.reduce((a,b)=>a+b,0)/durs.length/60).toFixed(1) : '0';
  const costs = runs.map(r=>r.est_cost||0);
  const totalCost = costs.reduce((a,b)=>a+b,0).toFixed(2);
  const rateColor = rate>=80?'var(--green)':rate>=50?'var(--orange)':'var(--red)';
  const qaColor = avgQa>=80?'var(--green)':avgQa>=60?'var(--orange)':'var(--red)';

  document.getElementById('statStrip').innerHTML = `
    <div class="c"><div class="l">ריצות</div><div class="v">${{total}}</div><div class="d">${{succ}}/${{total}} הצלחות</div></div>
    <div class="c"><div class="l">הצלחה</div><div class="v" style="color:${{rateColor}}">${{rate}}%</div><div class="d">${{currentPeriod?currentPeriod+' ימים':'הכל'}}</div></div>
    <div class="c"><div class="l">QA ממוצע</div><div class="v" style="color:${{qaColor}}">${{avgQa||'—'}}</div><div class="d">מתוך 100</div></div>
    <div class="c"><div class="l">זמן ממוצע</div><div class="v">${{avgDur}}</div><div class="d">דקות לריצה</div></div>
    <div class="c"><div class="l">עלות</div><div class="v" style="color:var(--orange)">${{totalCost}}$</div><div class="d">סה"כ בתקופה</div></div>
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
