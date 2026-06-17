"""
email_sender.py — Email finished articles to the operator.

Beyond the .md/.docx files saved to disk, each finished article is emailed
so Paz gets it in his inbox — readable on phone, forwardable, archivable.

Config (.env):
  EMAIL_TO            = pazer30@gmail.com        (where to send)
  EMAIL_FROM          = your_gmail@gmail.com     (sender — a Gmail account)
  EMAIL_APP_PASSWORD  = xxxx xxxx xxxx xxxx       (Gmail App Password, NOT
                          your login password — create at
                          myaccount.google.com/apppasswords)

If any of the three is missing, this is a graceful no-op (logs a hint,
doesn't crash the pipeline).

Why Gmail App Password (not OAuth): zero extra dependencies, works headless
in cron, no browser flow. Tradeoff: requires 2FA + one-time app-password setup.

CLI:
  python3 email_sender.py --test
  python3 email_sender.py --article output/articles/foo_en.md
"""

import argparse
import os
import smtplib
import ssl
import subprocess
import sys
import tempfile
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

from config import OUTPUT_DIR

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # SSL

# macOS Mail.app fallback — used when SMTP isn't configured. Sends through
# whatever account is already signed into Mail.app (no App Password needed).
MAIL_APP_TO = os.environ.get("EMAIL_TO", "") or "pazer30@gmail.com"

EMAIL_TO   = os.environ.get("EMAIL_TO", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_PW   = os.environ.get("EMAIL_APP_PASSWORD", "")


def is_configured() -> bool:
    return bool(EMAIL_TO and EMAIL_FROM and EMAIL_PW)


# ─────────────────────────────────────────────
# macOS Mail.app sender (no App Password needed)
# ─────────────────────────────────────────────

def _applescript_escape(s: str) -> str:
    """Escape a Python string for embedding in an AppleScript double-quoted literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def send_via_mailapp(to: str, subject: str, body: str,
                     attachments: list[Path] | None = None) -> dict:
    """Send through Mail.app using the signed-in account. Plain-text body.
    Returns {ok, error?}. macOS only."""
    if sys.platform != "darwin":
        return {"ok": False, "error": "Mail.app sender only works on macOS"}

    att_lines = ""
    for p in (attachments or []):
        if p and Path(p).exists():
            ap = _applescript_escape(str(Path(p).resolve()))
            att_lines += (
                f'        make new attachment with properties '
                f'{{file name:POSIX file "{ap}"}} at after the last paragraph\n'
            )

    script = f'''
tell application "Mail"
    set newMsg to make new outgoing message with properties {{subject:"{_applescript_escape(subject)}", content:"{_applescript_escape(body)}", visible:false}}
    tell newMsg
        make new to recipient at end of to recipients with properties {{address:"{_applescript_escape(to)}"}}
{att_lines}    end tell
    send newMsg
end tell
'''
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return {"ok": True, "to": to, "via": "mailapp"}
        return {"ok": False, "error": (r.stderr or "osascript failed")[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _md_to_html(md_text: str) -> str:
    """Minimal Markdown → HTML (no extra deps). Handles headings, bold,
    paragraphs, and preserves RTL for Hebrew."""
    import re
    lines = md_text.split("\n")
    # Strip YAML frontmatter
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                lines = lines[i + 1:]
                break
    out = []
    in_para = False
    for raw in lines:
        line = raw.rstrip()
        if not line:
            if in_para:
                out.append("</p>")
                in_para = False
            continue
        # Headings
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            if in_para:
                out.append("</p>"); in_para = False
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            continue
        # Plain paragraph line
        if not in_para:
            out.append("<p>"); in_para = True
        out.append(_inline(line) + " ")
    if in_para:
        out.append("</p>")
    return "\n".join(out)


def _inline(text: str) -> str:
    import re
    text = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # [[wikilinks]] → plain text
    text = re.sub(r"\[\[(.+?)\]\]", r"\1", text)
    return text


def send_article(md_path: Path, docx_path: Path | None = None) -> dict:
    """Email one article. Uses SMTP if configured, else falls back to Mail.app."""
    md_path = Path(md_path)
    if not md_path.exists():
        return {"ok": False, "error": f"article not found: {md_path}"}

    md_text = md_path.read_text(encoding="utf-8", errors="replace")
    # Title = first H1 or filename
    title = md_path.stem
    for line in md_text.split("\n"):
        if line.startswith("# "):
            title = line[2:].strip()
            break
        if line.startswith("title:"):
            title = line.split(":", 1)[1].strip().strip('"').strip("'")

    # ── Fallback path: Mail.app (no SMTP config needed) ──
    if not is_configured():
        word_count = len(md_text.split())
        # Strip frontmatter for a clean plain-text body
        body_txt = md_text
        if body_txt.startswith("---"):
            parts = body_txt.split("---", 2)
            if len(parts) >= 3:
                body_txt = parts[2]
        body = (f"מאמר חדש ממוקי\n\n{title}\n"
                f"{word_count} מילים · {md_path.name}\n"
                f"{'─'*40}\n\n{body_txt.strip()}")
        atts = [p for p in (docx_path, md_path) if p and Path(p).exists()]
        return send_via_mailapp(MAIL_APP_TO, f"📄 מוקי — {title[:120]}", body, atts)

    msg = EmailMessage()
    msg["Subject"] = f"📄 מוקי — {title[:120]}"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Date"] = formatdate(localtime=True)

    word_count = len(md_text.split())
    plain = (f"מאמר חדש ממוקי\n\n{title}\n\n"
             f"{word_count} מילים · {md_path.name}\n\n"
             f"{md_text[:2000]}...\n\n(המאמר המלא מצורף)")
    msg.set_content(plain)

    html_body = f"""<!DOCTYPE html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<style>body{{font-family:Georgia,serif;max-width:680px;margin:0 auto;padding:24px;
line-height:1.7;color:#222}} h1,h2,h3{{font-family:-apple-system,sans-serif;color:#111}}
.meta{{color:#888;font-size:13px;border-bottom:1px solid #eee;padding-bottom:12px;margin-bottom:20px}}
</style></head><body>
<div class="meta">🦊 מוקי · {word_count} מילים · {md_path.name}</div>
{_md_to_html(md_text)}
</body></html>"""
    msg.add_alternative(html_body, subtype="html")

    # Attach the .docx if present
    if docx_path and Path(docx_path).exists():
        data = Path(docx_path).read_bytes()
        msg.add_attachment(
            data,
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=Path(docx_path).name,
        )
    # Always attach the raw .md too
    msg.add_attachment(md_text.encode("utf-8"),
                       maintype="text", subtype="markdown",
                       filename=md_path.name)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=30) as s:
            s.login(EMAIL_FROM, EMAIL_PW)
            s.send_message(msg)
        return {"ok": True, "to": EMAIL_TO, "title": title}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def send_latest_articles(limit: int = 3) -> dict:
    """Email the most recent articles that haven't been emailed yet.
    Tracks sent files in output/_state/emailed_articles.json."""
    import json
    state_f = OUTPUT_DIR / "_state" / "emailed_articles.json"
    sent = set()
    if state_f.exists():
        try:
            sent = set(json.loads(state_f.read_text(encoding="utf-8")))
        except Exception:
            pass
    arts_dir = OUTPUT_DIR / "articles"
    if not arts_dir.exists():
        return {"sent": 0, "skipped": "no articles dir"}
    candidates = [p for p in arts_dir.glob("*_en.md")
                  if not p.name.endswith(".bak") and str(p) not in sent]
    candidates.sort(key=lambda p: -p.stat().st_mtime)
    n = 0
    for md in candidates[:limit]:
        docx = md.with_suffix(".docx")
        res = send_article(md, docx if docx.exists() else None)
        if res.get("ok"):
            sent.add(str(md))
            n += 1
            print(f"  ✉️  sent: {res['title'][:60]}")
        else:
            print(f"  ⚠️  {md.name}: {res.get('error')}")
            break  # stop on first failure (likely config/auth)
    state_f.parent.mkdir(parents=True, exist_ok=True)
    state_f.write_text(json.dumps(sorted(sent), ensure_ascii=False, indent=2),
                       encoding="utf-8")
    return {"sent": n}


def main():
    ap = argparse.ArgumentParser(description="Email finished articles")
    ap.add_argument("--test", action="store_true", help="send a tiny test email")
    ap.add_argument("--article", help="path to a specific .md to email")
    ap.add_argument("--latest", type=int, metavar="N", help="email N most recent un-sent articles")
    args = ap.parse_args()

    via = "SMTP" if is_configured() else "Mail.app"
    if not is_configured() and sys.platform != "darwin":
        print("⚠️  Email not configured and not on macOS. Add to .env:")
        print("   EMAIL_TO / EMAIL_FROM / EMAIL_APP_PASSWORD")
        sys.exit(1)

    if args.test:
        if is_configured():
            msg = EmailMessage()
            msg["Subject"] = "🦊 מוקי — מבחן מייל"
            msg["From"] = EMAIL_FROM
            msg["To"] = EMAIL_TO
            msg.set_content("אם קיבלת את זה — חיבור המייל של מוקי עובד (SMTP).")
            try:
                ctx = ssl.create_default_context()
                with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=30) as s:
                    s.login(EMAIL_FROM, EMAIL_PW)
                    s.send_message(msg)
                print(f"✅ test email sent to {EMAIL_TO} (SMTP)")
            except Exception as e:
                print(f"❌ {e}")
        else:
            res = send_via_mailapp(
                MAIL_APP_TO, "🦊 מוקי — מבחן מייל",
                "אם קיבלת את זה — חיבור המייל של מוקי דרך Mail.app עובד.")
            print(f"✅ test email sent to {MAIL_APP_TO} (Mail.app)"
                  if res.get("ok") else f"❌ {res.get('error')}")
        return

    if args.article:
        md = Path(args.article)
        res = send_article(md, md.with_suffix(".docx"))
        print(json.dumps(res, ensure_ascii=False) if False else
              (f"✅ sent: {res['title']}" if res.get("ok") else f"❌ {res.get('error')}"))
        return

    n = args.latest or 3
    res = send_latest_articles(n)
    print(f"✉️  emailed {res['sent']} article(s)")


if __name__ == "__main__":
    import json
    main()
