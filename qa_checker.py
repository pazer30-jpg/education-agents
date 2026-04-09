"""
qa_checker.py — בקרת איכות
נקרא על ידי project_manager לפני ואחרי כל שלב.

בודק:
  Agent 1 (Research)  → כמות מאמרים, שנות פרסום, open access, כיסוי נושאים
  Agent 2 (Writer)    → אורך מאמר, מבנה סעיפים, ציטוטים, קוהרנטיות
  Agent 3 (Content)   → קול של פז, אורך לפלטפורמה, hooks, hashtags
  Loop detection      → זיהוי תקיעות, חזרות, אינסוף

מחזיר:
  QAResult(passed, score, issues, warnings, recommendation)
"""

import json
import re
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime


# ─────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────

@dataclass
class QAResult:
    passed: bool
    score: int          # 0-100
    issues: list[str]   # חסימות — חייבים לתקן לפני המשך
    warnings: list[str] # אזהרות — כדאי לתקן
    recommendation: str # מה לעשות הלאה
    details: dict = field(default_factory=dict)

    def summary(self) -> str:
        icon = "✅" if self.passed else "❌"
        lines = [f"{icon} QA Score: {self.score}/100"]
        if self.issues:
            lines.append("  🚫 בעיות קריטיות:")
            for i in self.issues:
                lines.append(f"     • {i}")
        if self.warnings:
            lines.append("  ⚠️  אזהרות:")
            for w in self.warnings:
                lines.append(f"     • {w}")
        lines.append(f"  💡 המלצה: {self.recommendation}")
        return "\n".join(lines)


# ─────────────────────────────────────────────
# Loop detector
# ─────────────────────────────────────────────

class LoopDetector:
    """עוקב אחרי מצב הסוכנים ומזהה תקיעות"""

    def __init__(self):
        self._history: list[dict] = []
        self._agent_attempts: dict[str, int] = {}
        self._last_output_hashes: dict[str, str] = {}

    def record(self, agent: str, output_summary: str, success: bool):
        self._history.append({
            "agent": agent,
            "time": datetime.now().isoformat(),
            "success": success,
            "hash": hash(output_summary),
        })
        self._agent_attempts[agent] = self._agent_attempts.get(agent, 0) + 1

        # Check for repeated identical output
        prev_hash = self._last_output_hashes.get(agent)
        current_hash = str(hash(output_summary))
        self._last_output_hashes[agent] = current_hash

        return self._detect(agent, prev_hash, current_hash)

    def _detect(self, agent: str, prev_hash, current_hash) -> dict:
        issues = []
        attempts = self._agent_attempts.get(agent, 0)

        if attempts >= 3:
            issues.append(f"{agent} רץ {attempts} פעמים — ייתכן לולאה")

        if prev_hash and prev_hash == current_hash and attempts > 1:
            issues.append(f"{agent} מייצר פלט זהה בכל הרצה — תקוע")

        # Check for rapid consecutive failures
        recent = [h for h in self._history[-6:] if h["agent"] == agent]
        if len(recent) >= 3 and not any(h["success"] for h in recent[-3:]):
            issues.append(f"{agent} נכשל 3 פעמים ברצף — צריך התערבות")

        return {"loop_detected": bool(issues), "issues": issues, "attempts": attempts}

    def reset_agent(self, agent: str):
        self._agent_attempts[agent] = 0
        self._last_output_hashes.pop(agent, None)


# ─────────────────────────────────────────────
# QA checks per agent
# ─────────────────────────────────────────────

def check_research(papers_file: Path) -> QAResult:
    """בדיקת איכות פלט Agent 1"""
    issues, warnings = [], []
    details = {}
    score = 100

    if not papers_file or not papers_file.exists():
        return QAResult(False, 0, ["קובץ מאמרים לא נמצא"], [], "הרץ Agent 1 מחדש")

    try:
        with open(papers_file, encoding="utf-8") as f:
            data = json.load(f)
        papers = data.get("papers", data) if isinstance(data, dict) else data
    except Exception as e:
        return QAResult(False, 0, [f"שגיאה בקריאת קובץ: {e}"], [], "בדוק את קובץ ה-JSON")

    n = len(papers)
    details["count"] = n

    # כמות
    if n < 5:
        issues.append(f"רק {n} מאמרים — מינימום 5 נדרש")
        score -= 40
    elif n < 8:
        warnings.append(f"רק {n} מאמרים — עדיף 10+")
        score -= 15

    # שנות פרסום
    years = [p.get("year") for p in papers if p.get("year")]
    if years:
        recent = sum(1 for y in years if y and y >= 2015)
        details["recent_pct"] = round(recent / len(years) * 100)
        if recent / len(years) < 0.4:
            warnings.append(f"רק {details['recent_pct']}% מהמאמרים מ-2015+ — שקול חיפוש עדכני יותר")
            score -= 10

    # אבסטרקטים
    no_abstract = sum(1 for p in papers if not p.get("abstract"))
    if no_abstract > n * 0.5:
        warnings.append(f"{no_abstract} מאמרים ללא תקציר — Agent 2 יתקשה לסנתז")
        score -= 10

    # כיסוי נושאים (בדיקה גסה על פי מגוון כותרות)
    titles = " ".join(p.get("title", "") for p in papers).lower()
    topic = data.get("topic", "") if isinstance(data, dict) else ""
    topic_words = [w for w in topic.lower().split() if len(w) > 4]
    covered = sum(1 for w in topic_words if w in titles)
    if topic_words and covered / len(topic_words) < 0.5:
        warnings.append("נושאי המחקר לא מכוסים מספיק במאמרים שנשלפו")
        score -= 10

    passed = not bool(issues)
    rec = "המשך ל-Agent 2" if passed else "הרץ Agent 1 מחדש עם נושאי משנה ספציפיים יותר"
    return QAResult(passed, max(0, score), issues, warnings, rec, details)


def check_article(article_path: Path) -> QAResult:
    """בדיקת איכות פלט Agent 2"""
    issues, warnings = [], []
    details = {}
    score = 100

    if not article_path or not article_path.exists():
        return QAResult(False, 0, ["קובץ מאמר לא נמצא"], [], "הרץ Agent 2 מחדש")

    text = article_path.read_text(encoding="utf-8", errors="replace")
    words = len(text.split())
    details["words"] = words

    # אורך
    if words < 800:
        issues.append(f"מאמר קצר מדי — {words} מילים (מינימום 800)")
        score -= 35
    elif words < 1500:
        warnings.append(f"מאמר קצר יחסית — {words} מילים (עדיף 2000+)")
        score -= 10

    # מבנה סעיפים
    sections = re.findall(r'^#{1,3}\s+.+', text, re.MULTILINE)
    details["sections"] = len(sections)
    required = ["abstract", "תקציר", "introduction", "מבוא",
                "conclusion", "מסקנות", "references", "ביבליוגרפיה"]
    found = sum(1 for r in required if r.lower() in text.lower())
    if found < 3:
        issues.append(f"חסרים סעיפים בסיסיים (נמצאו {found}/8) — מבוא, תקציר, מסקנות, ביבליוגרפיה")
        score -= 30
    elif len(sections) < 4:
        warnings.append(f"רק {len(sections)} כותרות — מבנה דל")
        score -= 10

    # ציטוטים
    citations = re.findall(r'\([A-Z][a-z]+.{1,30}\d{4}\)', text)
    details["citations"] = len(citations)
    if len(citations) < 3:
        warnings.append(f"מעט ציטוטים ({len(citations)}) — מאמר אקדמי צריך לפחות 8-10")
        score -= 15

    # בדיקת שפה (לא ריק, לא HTML)
    if "<html" in text.lower() or "<!doctype" in text.lower():
        issues.append("הקובץ מכיל HTML במקום Markdown")
        score -= 40

    if len(text.strip()) < 100:
        issues.append("הקובץ כמעט ריק")
        score -= 60

    # ── Smart QA: Claude content review ────────
    if not issues and words > 500:
        try:
            smart = _smart_review_article(text[:3000])
            if smart.get("score"):
                smart_score = smart["score"]
                if smart_score < 60:
                    score -= 20
                    warnings.append(f"ביקורת תוכן: {smart.get('issue', 'איכות נמוכה')} (ציון: {smart_score})")
                elif smart_score < 80:
                    score -= 10
                    warnings.append(f"ביקורת תוכן: {smart.get('suggestion', '')} (ציון: {smart_score})")
                details["smart_qa"] = smart
        except Exception:
            pass  # don't block pipeline on smart QA failure

    passed = not bool(issues)
    rec = "המשך ל-Agent 3" if passed else "הרץ Agent 2 מחדש — שלח לו הנחיה לשפר את הסעיפים החסרים"
    return QAResult(passed, max(0, score), issues, warnings, rec, details)


def check_content(platform: str, content_file: Path) -> QAResult:
    """בדיקת איכות פלט Agent 3 — לפי פלטפורמה"""
    issues, warnings = [], []
    details = {}
    score = 100

    if not content_file or not content_file.exists():
        return QAResult(False, 0, [f"קובץ {platform} לא נמצא"], [],
                        f"הרץ Agent 3 מחדש עם platform={platform}")

    text = content_file.read_text(encoding="utf-8", errors="replace")
    chars = len(text)
    words = len(text.split())
    details.update({"chars": chars, "words": words, "platform": platform})

    # ── LinkedIn ──────────────────────────────
    if platform == "linkedin":
        # אורך
        if chars < 600:
            issues.append(f"פוסט קצר מדי — {chars} תווים (מינימום 800)")
            score -= 30
        elif chars < 900:
            warnings.append(f"פוסט קצר — {chars} תווים (עדיף 1200+)")
            score -= 10
        elif chars > 2800:
            warnings.append(f"פוסט ארוך מדי — {chars} תווים (LinkedIn חותך ב-3000)")
            score -= 10

        # hooks
        first_line = text.strip().split("\n")[0]
        if len(first_line) > 120:
            warnings.append("שורה פותחת ארוכה מדי — hook חייב להיות חד וקצר")
            score -= 5

        # hashtags
        hashtags = re.findall(r'#\w+', text)
        details["hashtags"] = len(hashtags)
        if len(hashtags) < 3:
            warnings.append(f"רק {len(hashtags)} hashtags — עדיף 8-12")
            score -= 5
        elif len(hashtags) > 20:
            warnings.append(f"{len(hashtags)} hashtags — יותר מדי, זה נראה spam")
            score -= 5

        # שאלה בסוף
        last_para = text.strip().split("\n\n")[-1]
        if "?" not in last_para:
            warnings.append("הפוסט לא מסתיים בשאלה — LinkedIn אוהב engagement")
            score -= 5

        # בוטי סימנים
        bot_phrases = ["חשוב לציין", "מעניין לראות", "נראה כי", "לסיכום,"]
        found_bot = [p for p in bot_phrases if p in text]
        if found_bot:
            warnings.append(f"ביטויים שנשמעים בוטיים: {', '.join(found_bot)}")
            score -= 8

    # ── Blog ──────────────────────────────────
    elif platform == "blog":
        if words < 400:
            issues.append(f"מאמר בלוג קצר מדי — {words} מילים (מינימום 500)")
            score -= 30
        elif words < 700:
            warnings.append(f"בלוג קצר — {words} מילים (עדיף 900+)")
            score -= 10

        headings = re.findall(r'^#{1,3}\s+.+', text, re.MULTILINE)
        details["headings"] = len(headings)
        if len(headings) < 2:
            warnings.append(f"רק {len(headings)} כותרות — בלוג צריך כותרות משנה")
            score -= 10

        # כותרות כשאלות (סגנון פז)
        q_headings = [h for h in headings if "?" in h]
        if headings and not q_headings:
            warnings.append("אין כותרות בצורת שאלה — זה חלק מהקול של פז")
            score -= 5

    # ── Podcast ───────────────────────────────
    elif platform == "podcast":
        if words < 500:
            issues.append(f"סקריפט קצר מדי — {words} מילים (פרק של 20 דק' צריך 2500+)")
            score -= 30
        elif words < 1500:
            warnings.append(f"סקריפט קצר — {words} מילים (עדיף 2500+)")
            score -= 15

        # סימוני הפקה
        production_marks = re.findall(r'\[.+?\]', text)
        details["production_marks"] = len(production_marks)
        if len(production_marks) < 3:
            warnings.append(f"מעט סימוני הפקה [{len(production_marks)}] — הוסף [הפסקה], [דוגמה מהשטח]")
            score -= 8

        # show notes
        has_notes = "show notes" in text.lower() or "נושאים" in text or "קישורים" in text
        if not has_notes:
            warnings.append("חסרים show notes — נדרשים לפרסום בפודקאסט")
            score -= 5

    # ── קול של פז (כללי לכל הפלטפורמות) ──────
    paz_markers = ["אני", "הרגשתי", "הצלחתי", "לא הצלחתי", "בשנה", "קבוצת", "חניכים"]
    found_paz = sum(1 for m in paz_markers if m in text)
    details["paz_voice_markers"] = found_paz
    if found_paz < 2:
        warnings.append("הטקסט לא נשמע מספיק כמו פז — חסרים גוף ראשון וסיפורים מהשטח")
        score -= 12

    # ── Smart QA: Claude voice review ──────────
    if not issues and words > 100:
        try:
            smart = _smart_review_content(text[:2000], platform)
            if smart.get("score"):
                smart_score = smart["score"]
                if smart_score < 60:
                    score -= 15
                    warnings.append(f"ביקורת קול: {smart.get('issue', 'לא נשמע כמו פז')} (ציון: {smart_score})")
                elif smart_score < 80:
                    score -= 5
                    warnings.append(f"ביקורת קול: {smart.get('suggestion', '')} (ציון: {smart_score})")
                details["smart_qa"] = smart
        except Exception:
            pass

    passed = not bool(issues)
    rec = ("תוכן תקין — המשך לעיצוב" if passed
           else f"הרץ Agent 3 מחדש ל-{platform} עם הנחיה לתקן: {'; '.join(issues)}")
    return QAResult(passed, max(0, score), issues, warnings, rec, details)


# ─────────────────────────────────────────────
# Smart QA: Claude-based content review
# ─────────────────────────────────────────────

def _smart_review_article(text_sample: str) -> dict:
    """Ask Claude to review article quality. Returns {score, issue, suggestion}."""
    from claude_cli import ask_claude_json

    prompt = f"""Rate this academic article excerpt (0-100) on:
- synthesis quality (not just summaries of papers?)
- coherent argument (clear thesis from intro to conclusion?)
- proper citations (APA format, real-looking references?)
- depth (beyond surface-level observations?)

Excerpt:
{text_sample}

Return JSON: {{"score": int, "issue": "main problem or empty", "suggestion": "how to improve or empty"}}"""

    return ask_claude_json(prompt, max_budget=0.2)


def _smart_review_content(text: str, platform: str) -> dict:
    """Ask Claude to review content for Paz's voice."""
    from claude_cli import ask_claude_json

    prompt = f"""Rate this {platform} post (0-100) for:
- authentic personal voice (not generic AI)
- specific examples from the field (not abstract)
- ends with genuine question (not rhetorical)
- no bot phrases ("חשוב לציין", "מעניין לראות", etc.)

Text:
{text[:2000]}

Return JSON: {{"score": int, "issue": "main problem or empty", "suggestion": "how to improve or empty"}}"""

    return ask_claude_json(prompt, max_budget=0.2)


# ─────────────────────────────────────────────
# Main QA runner
# ─────────────────────────────────────────────

def run_qa(stage: str, **kwargs) -> QAResult:
    """
    stage: "research" | "article" | "linkedin" | "blog" | "podcast"
    kwargs: paths to check
    """
    if stage == "research":
        return check_research(kwargs.get("papers_file"))
    elif stage == "article":
        return check_article(kwargs.get("article_path"))
    elif stage in ("linkedin", "blog", "podcast"):
        return check_content(stage, kwargs.get("content_file"))
    else:
        return QAResult(True, 100, [], [], f"אין בדיקת QA ל-{stage}")
