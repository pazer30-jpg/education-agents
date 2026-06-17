"""
agent_chat_view.py — Visual timeline of inter-agent communication.

Moki's agents talk to each other through scratchpad.py (transient per-run
hints) and through checkpoint/journal records. This renders all of that as
a chat-style HTML timeline — who said what to whom, when — so you can SEE
the conversation instead of reading raw JSON.

Sources mined:
  output/_scratchpad.json          — live inter-agent notes + Q&A (ask/answer)
  output/_journal/*.md             — narrative run journals (Agent 7)
  output/_state/autonomy_log.json  — what the daily routines did
  output/checkpoints/*.json        — defer/resume handoffs between runs

Output:
  output/agent_chat.html  — chat timeline, opens in browser

CLI:
  python3 agent_chat_view.py            # build + open
  python3 agent_chat_view.py --no-open  # build only
"""

import argparse
import html
import json
import webbrowser
from datetime import datetime
from pathlib import Path

from config import OUTPUT_DIR

SCRATCHPAD = OUTPUT_DIR / "_scratchpad.json"
AUTONOMY   = OUTPUT_DIR / "_state" / "autonomy_log.json"
JOURNAL    = OUTPUT_DIR / "_journal"
CHECKPOINTS = OUTPUT_DIR / "checkpoints"
OUT = OUTPUT_DIR / "agent_chat.html"

# Each agent gets a stable color + emoji so the eye can track speakers.
AGENT_STYLE = {
    "planner":          ("🧠", "#6940A5"),
    "researcher":       ("🔍", "#0F7B6C"),
    "writer":           ("✍️", "#2383E2"),
    "fact_checker":     ("🔬", "#B91C1C"),
    "editor":           ("✏️", "#CB912F"),
    "content":          ("✨", "#0F7B6C"),
    "content_creator":  ("✨", "#0F7B6C"),
    "designer":         ("🎨", "#6940A5"),
    "journal":          ("📓", "#787774"),
    "curator":          ("🏆", "#CB912F"),
    "failure_analyzer": ("🚨", "#B91C1C"),
    "survey_designer":  ("📋", "#6940A5"),
    "response_analyzer":("📊", "#2383E2"),
}


def _style(agent: str) -> tuple[str, str]:
    key = agent.lower().replace("agent", "").strip("_ 0123456789.")
    for k, v in AGENT_STYLE.items():
        if k in key or key in k:
            return v
    return ("🤖", "#787774")


def _load_json(p: Path, default):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _collect_messages() -> list[dict]:
    """Build a flat, time-sorted list of {ts, frm, to, kind, text}."""
    msgs = []

    # 1) Scratchpad — notes + ask/answer pairs
    sp = _load_json(SCRATCHPAD, {})
    for agent, keys in (sp.items() if isinstance(sp, dict) else []):
        for key, entry in (keys.items() if isinstance(keys, dict) else []):
            ts = entry.get("ts", "")
            val = entry.get("value", {})
            # ask/answer convention: q_<target>_<id> / a_<asker>_<id>
            to = ""
            kind = "note"
            if key.startswith("q_"):
                parts = key.split("_", 2)
                to = parts[1] if len(parts) > 1 else ""
                kind = "question"
            elif key.startswith("a_"):
                parts = key.split("_", 2)
                to = parts[1] if len(parts) > 1 else ""
                kind = "answer"
            # Human-readable text
            if isinstance(val, dict):
                text = (val.get("summary") or val.get("issue")
                        or val.get("question") or val.get("answer")
                        or json.dumps(val, ensure_ascii=False)[:300])
            else:
                text = str(val)[:300]
            if not text:
                continue
            msgs.append({"ts": ts, "frm": agent, "to": to,
                         "kind": kind, "text": text, "channel": "scratchpad"})

    # 2) Autonomy log — daily routine outcomes (system → operator)
    al = _load_json(AUTONOMY, [])
    for ev in (al[-40:] if isinstance(al, list) else []):
        text = ev.get("message") or ev.get("summary") or ""
        if not text:
            continue
        msgs.append({"ts": ev.get("at", ""), "frm": ev.get("routine", "autonomy"),
                     "to": "", "kind": ev.get("status", "note"),
                     "text": text, "channel": "autonomy"})

    # 3) Checkpoints — defer/resume handoffs
    if CHECKPOINTS.exists():
        for cp in sorted(CHECKPOINTS.glob("run_*.json"),
                         key=lambda p: p.stat().st_mtime, reverse=True)[:6]:
            data = _load_json(cp, {})
            for step, entry in (data.get("steps", {}) or {}).items():
                if "_deferred" in step or "_resumed" in step:
                    agent = step.replace("_deferred", "").replace("_resumed", "")
                    kind = "deferred" if "_deferred" in step else "resumed"
                    val = entry.get("value", {})
                    text = (val.get("reason", "") if isinstance(val, dict) else "")[:200] \
                           or f"step {kind}"
                    msgs.append({"ts": entry.get("saved_at", ""), "frm": agent,
                                 "to": "", "kind": kind, "text": text,
                                 "channel": "checkpoint"})

    # Sort by timestamp (best-effort)
    def _key(m):
        try:
            return datetime.fromisoformat(m["ts"].replace("Z", ""))
        except Exception:
            return datetime.min
    msgs.sort(key=_key)
    return msgs


KIND_BADGE = {
    "question": ("שאלה", "#2383E2"),
    "answer":   ("תשובה", "#0F7B6C"),
    "note":     ("הערה", "#787774"),
    "deferred": ("נדחה", "#CB912F"),
    "resumed":  ("שוחזר", "#0F7B6C"),
    "ok":       ("✓", "#0F7B6C"),
    "error":    ("שגיאה", "#B91C1C"),
    "warn":     ("אזהרה", "#CB912F"),
    "skipped":  ("דולג", "#9B9A97"),
}


def build_html(msgs: list[dict]) -> str:
    rows = []
    for m in msgs:
        emoji, color = _style(m["frm"])
        ts = (m["ts"] or "")[:16].replace("T", " ")
        to_html = (f'<span class="arrow">→ {html.escape(m["to"])}</span>'
                   if m.get("to") else "")
        badge_txt, badge_col = KIND_BADGE.get(m["kind"], (m["kind"], "#787774"))
        rows.append(f"""
      <div class="msg" style="--c:{color}">
        <div class="avatar" style="background:{color}">{emoji}</div>
        <div class="bubble">
          <div class="meta">
            <span class="frm">{html.escape(m['frm'])}</span>
            {to_html}
            <span class="badge" style="background:{badge_col}1a;color:{badge_col}">{badge_txt}</span>
            <span class="ch">{m['channel']}</span>
            <span class="ts">{ts}</span>
          </div>
          <div class="text">{html.escape(m['text'])}</div>
        </div>
      </div>""")

    body = "\n".join(rows) if rows else '<p class="empty">אין הודעות בין-סוכן עדיין.</p>'
    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>מוקי · שיחת הסוכנים</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Newsreader:wght@400;500;600&family=Inter:wght@400;500;600&display=swap');
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  font-family:'Inter',system-ui,sans-serif;
  background:#FBFAF7; color:#37352F;
  padding:32px 16px; line-height:1.55; font-size:14px;
}}
.wrap {{ max-width:780px; margin:0 auto; }}
h1 {{ font-family:'Newsreader',serif; font-weight:500; font-size:30px;
      color:#1F1E1B; margin-bottom:6px; }}
.sub {{ color:#787774; font-size:13px; margin-bottom:28px; }}
.msg {{ display:flex; gap:12px; margin-bottom:16px; align-items:flex-start; }}
.avatar {{ width:34px; height:34px; border-radius:50%; flex-shrink:0;
           display:flex; align-items:center; justify-content:center;
           font-size:17px; color:#fff; }}
.bubble {{ background:#fff; border:1px solid rgba(55,53,47,0.1);
           border-radius:10px; border-top-right-radius:2px;
           padding:10px 14px; flex:1;
           box-shadow:0 1px 2px rgba(55,53,47,0.04); border-right:3px solid var(--c); }}
.meta {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap;
         margin-bottom:5px; font-size:12px; }}
.frm {{ font-weight:600; color:#1F1E1B; }}
.arrow {{ color:#9B9A97; }}
.badge {{ padding:1px 8px; border-radius:10px; font-size:11px; font-weight:500; }}
.ch {{ color:#9B9A97; font-size:11px; background:#F0EFEC; padding:1px 7px; border-radius:8px; }}
.ts {{ color:#B3B2AE; font-size:11px; margin-right:auto; }}
.text {{ color:#37352F; white-space:pre-wrap; word-break:break-word; }}
.empty {{ color:#9B9A97; text-align:center; padding:60px 0; }}
.legend {{ display:flex; gap:14px; flex-wrap:wrap; margin-bottom:24px;
           padding:14px; background:#fff; border:1px solid rgba(55,53,47,0.08);
           border-radius:10px; font-size:12px; }}
.legend span {{ display:flex; align-items:center; gap:5px; }}
.dot {{ width:11px; height:11px; border-radius:50%; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>🦊 שיחת הסוכנים</h1>
  <div class="sub">תקשורת בין-סוכן דרך scratchpad · autonomy · checkpoints — {len(msgs)} הודעות · {datetime.now().strftime('%d/%m/%Y %H:%M')}</div>
  <div class="legend">
    <span><span class="dot" style="background:#6940A5"></span>Planner</span>
    <span><span class="dot" style="background:#0F7B6C"></span>Researcher/Content</span>
    <span><span class="dot" style="background:#2383E2"></span>Writer</span>
    <span><span class="dot" style="background:#B91C1C"></span>FactCheck/Alerts</span>
    <span><span class="dot" style="background:#CB912F"></span>Editor/Curator</span>
  </div>
{body}
</div>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(description="Visual inter-agent chat timeline")
    ap.add_argument("--no-open", action="store_true", help="build but don't open browser")
    args = ap.parse_args()

    msgs = _collect_messages()
    html_doc = build_html(msgs)
    tmp = OUT.with_suffix(".html.tmp")
    tmp.write_text(html_doc, encoding="utf-8")
    tmp.replace(OUT)
    print(f"💬 {OUT.relative_to(OUTPUT_DIR.parent)} ({len(msgs)} messages)")
    if not args.no_open:
        webbrowser.open(f"file://{OUT}")


if __name__ == "__main__":
    main()
