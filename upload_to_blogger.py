"""
upload_to_blogger.py
--------------------
מעלה פוסטי בלוג שנוצרו על ידי agent3_content_creator.py כטיוטות ל-Blogger.

דרישות:
    pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client markdown2

הכנה ראשונה (חד-פעמית):
    1. Google Cloud Console → צור project → הפעל את Blogger API v3
    2. צור OAuth 2.0 Client ID מסוג "Desktop app"
    3. הורד credentials.json לאותה תיקייה כמו הסקריפט
    4. הרץ: python upload_to_blogger.py
       (בפעם הראשונה ייפתח חלון דפדפן לאישור)

שימוש:
    python upload_to_blogger.py              # מעלה רק קבצים חדשים
    python upload_to_blogger.py --all        # מעלה הכל גם אם כבר עלה
    python upload_to_blogger.py --dry-run    # מציג מה היה עולה, בלי להעלות
"""

import os
import sys
import json
import argparse
import hashlib
import time
from pathlib import Path
from datetime import datetime

# ─── הגדרות ───────────────────────────────────────────────
BLOG_ID       = "3137138380120778851"
SCRIPT_DIR    = Path(__file__).parent
POSTS_FOLDER  = SCRIPT_DIR / "output" / "posts" / "blog"
FILE_TYPES    = [".md"]
CREDENTIALS   = SCRIPT_DIR / "credentials.json"
TOKEN_FILE    = SCRIPT_DIR / ".blogger_token.json"
STATE_FILE    = SCRIPT_DIR / ".blogger_uploaded.json"
SCOPES        = ["https://www.googleapis.com/auth/blogger"]

SKIP_PATTERNS = (".bak", "_human.md", "_20260510_002709.md")
SKIP_NAMES    = {"blog.md"}
# ──────────────────────────────────────────────────────────


def authenticate():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS.exists():
                sys.exit(f"❌ חסר credentials.json בנתיב: {CREDENTIALS}\n"
                         "   ראה הוראות בראש הקובץ.")
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return build("blogger", "v3", credentials=creds)


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def file_signature(path: Path) -> str:
    """חתימה לזיהוי שינויים בקובץ (גודל + 8K ראשונים)."""
    h = hashlib.sha1()
    h.update(str(path.stat().st_size).encode())
    h.update(path.read_bytes()[:8192])
    return h.hexdigest()


def extract_title_and_body(filepath: Path) -> tuple[str, str]:
    text = filepath.read_text(encoding="utf-8").strip()
    lines = text.splitlines()

    # דלג על YAML frontmatter אם קיים
    yaml_title = None
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                # שלוף title מתוך ה-frontmatter אם יש
                for fm in lines[1:i]:
                    if fm.startswith("title:"):
                        yaml_title = fm.split(":", 1)[1].strip().strip('"').strip("'")
                        break
                lines = lines[i + 1:]
                break

    body_start = 0
    title = yaml_title or filepath.stem
    # אם השורה הראשונה היא # heading, השתמש בו ככותרת והשמט מהגוף
    while body_start < len(lines) and not lines[body_start].strip():
        body_start += 1
    if body_start < len(lines) and lines[body_start].startswith("# "):
        title = lines[body_start].lstrip("# ").strip()
        body_start += 1

    body_text = "\n".join(lines[body_start:]).strip()
    return title, body_text


def file_to_post(filepath: Path) -> dict:
    title, body_text = extract_title_and_body(filepath)

    if filepath.suffix == ".md":
        import markdown2
        content = markdown2.markdown(
            body_text, extras=["tables", "fenced-code-blocks", "break-on-newline"]
        )
    else:
        content = "".join(f"<p>{line}</p>\n" for line in body_text.splitlines() if line.strip())

    return {"title": title, "content": content}


def should_skip(filepath: Path) -> bool:
    name = filepath.name
    if name in SKIP_NAMES:
        return True
    return any(name.endswith(p) for p in SKIP_PATTERNS)


def upload_drafts(service, *, force_all: bool = False, dry_run: bool = False) -> None:
    if not POSTS_FOLDER.exists():
        sys.exit(f"❌ התיקייה לא נמצאה: {POSTS_FOLDER}")

    files = sorted(
        f for f in POSTS_FOLDER.iterdir()
        if f.is_file() and f.suffix in FILE_TYPES and not should_skip(f)
    )
    if not files:
        print(f"❌ לא נמצאו פוסטים להעלאה ב-{POSTS_FOLDER}")
        return

    state = load_state()
    new_files = []
    for f in files:
        sig = file_signature(f)
        recorded = state.get(f.name, {})
        if not force_all and recorded.get("signature") == sig:
            continue
        new_files.append((f, sig))

    if not new_files:
        print(f"✅ אין קבצים חדשים. ({len(files)} קבצים כבר עלו)")
        return

    mode = "DRY RUN" if dry_run else "DRAFT"
    print(f"📤 [{mode}] מעלה {len(new_files)} פוסטים (מתוך {len(files)} בתיקייה)\n")

    for filepath, sig in new_files:
        if dry_run:
            title, _ = extract_title_and_body(filepath)
            print(f"  • {filepath.name}  →  כותרת: {title!r}")
            continue

        post_data = file_to_post(filepath)
        for attempt in range(1, 6):
            try:
                result = service.posts().insert(
                    blogId=BLOG_ID,
                    body={
                        "title":   post_data["title"],
                        "content": post_data["content"],
                        "status":  "DRAFT",
                    },
                    isDraft=True,
                ).execute()

                state[filepath.name] = {
                    "signature":  sig,
                    "post_id":    result["id"],
                    "title":      result["title"],
                    "uploaded_at": datetime.now().isoformat(timespec="seconds"),
                }
                save_state(state)

                print(f"✔ [{filepath.name}]")
                print(f"  כותרת : {result['title']}")
                print(f"  ID    : {result['id']}\n")
                time.sleep(2)   # השהייה בין פוסטים למניעת rate limit
                break

            except Exception as e:
                if "429" in str(e) and attempt < 5:
                    wait = 30 * attempt
                    print(f"  ⏳ rate limit — ממתין {wait} שניות (ניסיון {attempt}/5)...")
                    time.sleep(wait)
                else:
                    print(f"✘ שגיאה ב-[{filepath.name}]: {e}\n")
                    break


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload education-agents blog posts as Blogger drafts.")
    parser.add_argument("--all", action="store_true",
                        help="העלה הכל גם אם כבר עלה בעבר")
    parser.add_argument("--dry-run", action="store_true",
                        help="הצג מה היה עולה, בלי להתחבר ל-Google")
    args = parser.parse_args()

    if args.dry_run:
        upload_drafts(service=None, force_all=args.all, dry_run=True)
        return

    print("🔐 מתחבר ל-Google...\n")
    service = authenticate()
    upload_drafts(service, force_all=args.all)
    print("🏁 סיום.")


if __name__ == "__main__":
    main()
