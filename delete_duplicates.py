"""
delete_duplicates.py
--------------------
מוחק מבלוגר 12 פוסטים כפולים (גרסאות משנה שהכותרת שלהן זהה לפרק_XX הראשי).
הגרסאות הראשיות (פרק_XX_BLOG.md) נשמרות.

הרץ: python3 delete_duplicates.py
"""

import json, sys, time
from pathlib import Path

BLOG_ID    = "3137138380120778851"
SCRIPT_DIR = Path(__file__).parent
TOKEN_FILE = SCRIPT_DIR / ".blogger_token.json"
STATE_FILE = SCRIPT_DIR / ".blogger_uploaded.json"
SCOPES     = ["https://www.googleapis.com/auth/blogger"]

# post_id → שם הקובץ שיוסר מה-state אחרי מחיקה
TO_DELETE = {
    "8405454448145086683": "civil_religion__x_secondary_traum_x_existential_me_en_blog_20260416_1129.md",
    "8440619366515044910": "civil_religion_and_s_x_secular_ritual_and_c_x_yom_hazikaron__en_blog_20260416_1424.md",
    "85928630084078624":   "digital_youth_w_x_digital_youth_w_x_arts-based_non_en_blog_20260407_2359.md",
    "5212361250269009482": "education_durin_x_education_for_h_x_non-formal_edu_en_blog_20260407_1148.md",
    "2645063209185870354": "education_durin_x_education_for_h_x_non-formal_edu_he_blog_20260407_1307.md",
    "3830898913537452577": "militarism_and_educa_x_military_socializati_x_civic_and_mili_en_blog_20260416_2021.md",
    "4408526999852750380": "non-formal_educ_x_social_entrepre_x_non-formal_edu_en_blog_20260409_2007.md",
    "5029011976106730397": "reuven_kahana_n_x_reuven_kahana_y_x_non-formal_edu_en_blog_20260409_2053.md",
    "6109833631709202971": "youth_movements_x_peer_mentorship_x_assessment_and_en_blog_20260407_1317.md",
    "5085938487243747842": "כוחו_של_הדיאלוג_he_blog_20260414_1019.md",
    "609997410771067854":  "מנהיגות_חינוכית_en_blog_20260411_1516.md",
    "481621300787641460":  "מתוך_תוצאת_plan_en_blog_20260413_1037.md",
}


def authenticate():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    if not TOKEN_FILE.exists():
        sys.exit("❌ אין token — הרץ קודם upload_to_blogger.py")
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("blogger", "v3", credentials=creds)


def main():
    print("🔐 מתחבר ל-Google...\n")
    service = authenticate()

    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    deleted = 0

    for post_id, fname in TO_DELETE.items():
        try:
            service.posts().delete(blogId=BLOG_ID, postId=post_id).execute()
            # הסר מה-state
            if fname in state:
                del state[fname]
            STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"✔ נמחק: {fname[:65]}")
            deleted += 1
            time.sleep(1)
        except Exception as e:
            print(f"✘ שגיאה [{post_id}]: {e}")

    print(f"\n🏁 נמחקו {deleted}/{len(TO_DELETE)} פוסטים כפולים.")
    print(f"   נותרו בבלוגר: {len(state)} פוסטים.")


if __name__ == "__main__":
    main()
