"""
fix_titles.py
-------------
מתקן כותרות שגויות בפוסטים שעלו ל-Blogger.
הרץ: python3 fix_titles.py
"""

import json
import sys
from pathlib import Path

BLOG_ID    = "3137138380120778851"
SCRIPT_DIR = Path(__file__).parent
TOKEN_FILE = SCRIPT_DIR / ".blogger_token.json"
SCOPES     = ["https://www.googleapis.com/auth/blogger"]

FIXES = [
    # CHANGES: prefix posts — real titles extracted from body
    {
        "post_id": "557732145344413431",
        "new_title": "הטקס לא שייך לך — על זיכרון שמועבר בלי לעבור",
    },
    {
        "post_id": "8440619366515044910",
        "new_title": "הטקס שאנחנו לא בחרנו — ומה עושים איתו",
    },
    {
        "post_id": "6950650019534304025",
        "new_title": "אתה מתכנן את הפעילות — לא את הקבוצה שעומדת מולך",
    },
    {
        "post_id": "2555089635730378535",
        "new_title": "הטקס שאנחנו לא בחרנו — ומה עושים איתו",
    },
    # Filename-as-title posts — real titles extracted from H1
    {
        "post_id": "2845797365650719948",
        "new_title": "המדורה עובדת. אנחנו לא מוכנים.",
    },
    {
        "post_id": "8667448917154272546",
        "new_title": "המדורה עובדת. אנחנו לא מוכנים.",
    },
    {
        "post_id": "4117586803457204146",
        "new_title": "הסף הוא לא שלב — הוא מקום",
    },
    {
        "post_id": "8916186834682046414",
        "new_title": "הסף הוא לא שלב — הוא מקום",
    },
    {
        "post_id": "820023171892128646",
        "new_title": "החינוך הבלתי פורמלי לא בנוי לפצות — הוא בנוי להכיל",
    },
    {
        "post_id": "305558041812282429",
        "new_title": "החינוך הבלתי פורמלי לא בנוי לפצות — הוא בנוי להכיל",
    },
    {
        "post_id": "3523020805130854070",
        "new_title": "לא הייתי המחנך הטוב — הייתי רק שם כשהמרחב עשה את העבודה",
    },
    {
        "post_id": "2573211738323685453",
        "new_title": "לא הייתי המחנך הטוב — הייתי רק שם כשהמרחב עשה את העבודה",
    },
]


def authenticate():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    if not TOKEN_FILE.exists():
        sys.exit("❌ אין token — הרץ קודם את upload_to_blogger.py")

    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    return build("blogger", "v3", credentials=creds)


def main():
    print("🔐 מתחבר ל-Google...\n")
    service = authenticate()

    for fix in FIXES:
        post_id   = fix["post_id"]
        new_title = fix["new_title"]
        try:
            service.posts().patch(
                blogId=BLOG_ID,
                postId=post_id,
                body={"title": new_title},
            ).execute()
            print(f"✔ תוקן → {new_title}")
        except Exception as e:
            print(f"✘ שגיאה ב-{post_id}: {e}")

    print(f"\n🏁 סיום — {len(FIXES)} כותרות עודכנו.")


if __name__ == "__main__":
    main()
