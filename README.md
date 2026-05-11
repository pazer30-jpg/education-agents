# 🦊 Moki — Education Agents Pipeline

מערכת סוכנים אוטונומית לכתיבה, מחקר ויצירת תוכן בתחום החינוך.  
מ-**נושא** → **מחקר אקדמי** → **מאמר** → **פוסט LinkedIn + בלוג + פודקאסט**.

---

## ⚡ התחלה מהירה

```bash
# 1. התקנת תלויות
pip install -r requirements.txt

# 2. בדיקת תקינות
bash scripts/health_check.sh

# 3. הרצה
python agent5_project_manager.py
# או ישירות:
python orchestrator.py "חינוך בלתי פורמלי" --content linkedin blog
```

---

## 🏗 ארכיטקטורה

```
נושא
  │
  ▼
Agent 0 — Planner        (תכנון מחקר + הצעת גישה)
  │
  ▼
Agent 1 — Researcher     (Semantic Scholar · OpenAlex · CrossRef · ERIC · PubMed)
  │  └── Agent 1.5 — PDF Reader   (חילוץ טקסט ממאמרים)
  │  └── Agent 1.7 — Paper Analyzer
  │
  ▼
Agent 2 — Writer         (מאמר אקדמי EN + HE)
  │  └── Agent 2.5 — Editor
  │  └── Agent 2.7 — Fact Checker
  │
  ▼
Agent 3 — Content Creator  (LinkedIn · Blog · Podcast)
  │  └── Agent 3.5 — Human Review
  │  └── Agent 3.6 — Editor
  │
  ▼
Agent 4 — Designer       (SVG illustrations)
  │
  ▼
Agent 5 — Project Manager  (QA · Loop Detector · Orchestration)
```

**שכבות תומכות:**
- 🧠 **זיכרון** — `memory.py`, `obsidian_memory.py`, `scratchpad.py`
- 🛡 **איכות** — `qa_checker.py`, `causal_validator.py`, `conflict_resolver.py`
- 📊 **Observability** — `analytics.py`, `observability.py`, `dashboard.py`
- 🎓 **אקדמי** — `seminar_writer.py`, `thesis_prep.py`, `bibliography.py`

---

## ⚙️ הגדרות

```bash
cp .env.example .env
```

| משתנה | חובה | תיאור |
|---|---|---|
| `ANTHROPIC_API_KEY` | רק ללא Claude CLI | מפתח API ישיר |
| `TELEGRAM_BOT_TOKEN` | לא | התראות על כשלות/הצלחות |
| `TELEGRAM_CHAT_ID` | לא | צ'אט לשליחת התראות |
| `SEMANTIC_SCHOLAR_API_KEY` | לא | שיפור rate limits |
| `UNPAYWALL_EMAIL` | כן | גישה ל-PDFs פתוחים |

> **Claude CLI vs API Key:** המערכת מתועדפת לרוץ דרך `claude` CLI (מנוי Claude).  
> אם ה-CLI לא זמין — דורשת `ANTHROPIC_API_KEY`.

---

## 🚀 מצבי הרצה

```bash
# פייפליין מלא (דרך Agent 5 — מומלץ)
python agent5_project_manager.py

# פייפליין ישיר
python orchestrator.py "נושא" --content linkedin blog podcast

# רק מחקר
python agent1_researcher.py

# רק תוכן ממאמר קיים
python agent3_content_creator.py --from-article output/articles/my_article.md

# כתיבה אקדמית
python seminar_writer.py "נושא" --length 10000
python thesis_prep.py "נושא"

# Autopilot (ריצה אוטונומית)
python autopilot.py
```

---

## 📁 מבנה תיקיות

```
education-agents/
├── agent0_planner.py          # Agent 0 — תכנון
├── agent1_researcher.py       # Agent 1 — מחקר
├── agent2_writer.py           # Agent 2 — כתיבה
├── agent3_content_creator.py  # Agent 3 — יצירת תוכן
├── agent4_designer.py         # Agent 4 — עיצוב
├── agent5_project_manager.py  # Agent 5 — מנהל + QA
├── orchestrator.py            # Pipeline runner
├── claude_cli.py              # Wrapper ל-Claude
│
├── scripts/
│   ├── health_check.sh        # בדיקת תקינות לפני הרצה
│   ├── clean_cache.sh         # ניקוי pycache
│   └── pipeline_stats.sh      # סטטיסטיקות ביצועים
│
├── moki/                      # Obsidian Vault — מחקר
│   ├── _arcs/                 # קשתות נרטיב
│   ├── _daily/                # יומן יומי
│   └── *.md                   # מאמרים ותיאוריות
│
├── output/                    # Obsidian Vault — תוצרים
│   ├── ready/
│   │   ├── blog/              # ✅ בלוגים מוכנים לפרסום
│   │   ├── linkedin/          # ✅ פוסטים מוכנים
│   │   └── podcast/           # ✅ פרקי פודקאסט
│   ├── articles/              # מאמרים אקדמיים
│   └── papers/                # PDFs שנאספו
│
├── requirements.txt
├── .env.example
└── README.md
```

---

## 🔧 סקריפטים שימושיים

```bash
# בדיקת תקינות לפני הרצה
bash scripts/health_check.sh

# סטטיסטיקות על הפייפליין
bash scripts/pipeline_stats.sh

# ניקוי pycache + DS_Store
bash scripts/clean_cache.sh
```

---

## 🐛 בעיות נפוצות

| שגיאה | פתרון |
|---|---|
| `Claude CLI not available and no ANTHROPIC_API_KEY` | הגדר `ANTHROPIC_API_KEY` ב-`.env` |
| `writer hard timeout — exceeded 60 min` | הנושא מורכב מדי — נסה לצמצם את מספר ה-subtopics |
| `'>=' not supported between instances of 'str' and 'int'` | תוקן — עדכן לגרסה עדכנית |
| פייפליין תקוע מעל שעה | הרץ `bash scripts/health_check.sh` ובדוק את ה-CLI |

---

## 📊 מצב נוכחי

```bash
bash scripts/pipeline_stats.sh
```

---

## 🗺 מפת הקוד המלאה

ראה [`output/_INDEX.md`](output/_INDEX.md) — נוצרת אוטומטית ע"י `regenerate_index.py`.
