"""
upload_articles.py
------------------
מעלה מאמרים אקדמיים אנגליים מתיקיית output/articles/ כטיוטות ל-Blogger.

קריטריוני ברירה (אוטומטיים):
  - קובץ .md עם סיומת _en.md
  - כותרת אנגלית (לא "Synthesized Article: מ..." / לא שם קובץ)
  - מעל 3,000 מילים
  - נמצאו ≥6 ציטוטים (20XX) או (19XX)
  - קיים פרק References

שימוש:
    python3 upload_articles.py              # מעלה מאמרים חדשים בלבד
    python3 upload_articles.py --all        # מחייב העלאה מחדש של הכל
    python3 upload_articles.py --dry-run    # מציג מה יועלה בלי להעלות
"""

import os, sys, json, argparse, hashlib, time, re
from pathlib import Path

# ─── הגדרות ────────────────────────────────────────────────
BLOG_ID        = "3137138380120778851"
SCRIPT_DIR     = Path(__file__).parent
ARTICLES_FOLDER = SCRIPT_DIR / "output" / "articles"
TOKEN_FILE     = SCRIPT_DIR / ".blogger_token.json"
STATE_FILE     = SCRIPT_DIR / ".blogger_articles_uploaded.json"
SCOPES         = ["https://www.googleapis.com/auth/blogger"]
LABEL_ACADEMIC = "Academic Article"
# ───────────────────────────────────────────────────────────

# קבצים לדלג עליהם תמיד
SKIP_NAMES = {
    "loneliness_residenti_x_בדידות_של_מנהלי_פנימ_x_role_isolation_en.md",
    "civil_religion_and_s_x_secular_ritual_and_c_x_yom_hazikaron__en.md",
    "non-formal_education_x_mental_health_and_ps_x_environmental__en.md",
    "organizational_knowl_x_knowledge_management_x_tacit_and_expl_en.md",
    # review/notes files
    "civil_religion_yom_hazikaron_review.md",
    "dialogue_in_education_review.md",
    "grit_selfregulation_review_edited.md",
    "instructional_planning_review.md",
    "memorial_narrative_genZ_review_part1.md",
    "spirituality_transmission_civic_review.md",
}


def authenticate():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    if not TOKEN_FILE.exists():
        sys.exit("❌ אין token. הרץ קודם: python3 upload_to_blogger.py --dry-run\n"
                 "   (כדי לבצע אימות OAuth)")

    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("blogger", "v3", credentials=creds)


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}

def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def file_signature(path: Path) -> str:
    h = hashlib.sha1()
    h.update(str(path.stat().st_size).encode())
    h.update(path.read_bytes()[:8192])
    return h.hexdigest()


def extract_title_and_body(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8").strip()
    lines = text.splitlines()

    yaml_title = None
    body_start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                for fm in lines[1:i]:
                    if fm.startswith("title:"):
                        yaml_title = fm.split(":", 1)[1].strip().strip('"').strip("'")
                        break
                body_start = i + 1
                break

    body_lines = lines[body_start:]

    # דלג על בלוק CHANGES אם יש
    if body_lines and body_lines[0].startswith("CHANGES:"):
        for j, bl in enumerate(body_lines):
            if bl.strip() == "---":
                body_lines = body_lines[j + 1:]
                break

    # H1 כותרת
    h1_title = None
    h1_index = 0
    for idx, line in enumerate(body_lines):
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            h1_title = s.lstrip("# ").strip()
            h1_index = idx
            break

    title = h1_title or yaml_title or path.stem
    # נקה "Synthesized Article: " prefix אם יש
    title = re.sub(r"^Synthesized Article:\s*", "", title, flags=re.IGNORECASE).strip()

    # גוף המאמר ללא כותרת H1
    if h1_title:
        body_text = "\n".join(body_lines[h1_index + 1:]).strip()
    else:
        body_text = "\n".join(body_lines).strip()

    return title, body_text


def is_quality_article(path: Path) -> bool:
    """בדוק אם המאמר עומד בסטנדרט אקדמי."""
    if path.name in SKIP_NAMES:
        return False

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    word_count = len(text.split())

    try:
        title, _ = extract_title_and_body(path)
    except Exception:
        return False

    is_english_title = (
        bool(title)
        and not any(ord(c) > 0x590 for c in title[:40])
        and not title.lower().startswith("synthesized article: מ")
        and not title.startswith("I've reviewed")
        and title != path.stem
    )
    has_references = any(
        l.strip().lower() in ("references", "## references", "### references")
        for l in lines
    )
    has_citations = text.count("(20") + text.count("(19") > 5
    long_enough = word_count >= 3000

    return is_english_title and has_references and has_citations and long_enough


def file_to_post(path: Path) -> dict:
    import markdown2
    title, body_text = extract_title_and_body(path)
    content = markdown2.markdown(
        body_text, extras=["tables", "fenced-code-blocks", "break-on-newline"]
    )
    return {"title": title, "content": content}


def upload_articles(service, *, force_all: bool = False, dry_run: bool = False) -> None:
    if not ARTICLES_FOLDER.exists():
        sys.exit(f"❌ תיקייה לא נמצאה: {ARTICLES_FOLDER}")

    candidates = sorted(
        f for f in ARTICLES_FOLDER.iterdir()
        if f.is_file() and f.suffix == ".md" and is_quality_article(f)
    )

    if not candidates:
        print("❌ לא נמצאו מאמרים כשירים")
        return

    state = load_state()
    to_upload = []
    for f in candidates:
        sig = file_signature(f)
        if not force_all and state.get(f.name, {}).get("signature") == sig:
            continue
        to_upload.append((f, sig))

    already_done = len(candidates) - len(to_upload)
    if not to_upload:
        print(f"✅ אין מאמרים חדשים. ({already_done} מאמרים כבר עלו)")
        return

    mode = "DRY RUN" if dry_run else "DRAFT"
    print(f"📤 [{mode}] מעלה {len(to_upload)} מאמרים (מתוך {len(candidates)} כשירים)\n")

    for filepath, sig in to_upload:
        if dry_run:
            title, _ = extract_title_and_body(filepath)
            print(f"  • {filepath.name}")
            print(f"    כותרת: {title!r}")
            continue

        post_data = file_to_post(filepath)
        from datetime import datetime

        for attempt in range(1, 6):
            try:
                result = service.posts().insert(
                    blogId=BLOG_ID,
                    body={
                        "title":   post_data["title"],
                        "content": post_data["content"],
                        "status":  "DRAFT",
                        "labels":  [LABEL_ACADEMIC],
                    },
                    isDraft=True,
                ).execute()

                state[filepath.name] = {
                    "signature":   sig,
                    "post_id":     result["id"],
                    "title":       result["title"],
                    "uploaded_at": datetime.now().isoformat(timespec="seconds"),
                }
                save_state(state)

                print(f"✔ {result['title'][:70]}")
                print(f"  ID: {result['id']}\n")
                time.sleep(2)
                break

            except Exception as e:
                if "429" in str(e) and attempt < 5:
                    wait = 30 * attempt
                    print(f"  ⏳ rate limit — ממתין {wait}s (ניסיון {attempt}/5)...")
                    time.sleep(wait)
                else:
                    print(f"✘ שגיאה [{filepath.name}]: {e}\n")
                    break


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload academic articles as Blogger drafts.")
    parser.add_argument("--all",     action="store_true", help="העלה הכל מחדש")
    parser.add_argument("--dry-run", action="store_true", help="הצג בלי להעלות")
    args = parser.parse_args()

    if args.dry_run:
        upload_articles(service=None, force_all=args.all, dry_run=True)
        return

    print("🔐 מתחבר ל-Google...\n")
    service = authenticate()
    upload_articles(service, force_all=args.all)
    print("🏁 סיום.")


if __name__ == "__main__":
    main()
