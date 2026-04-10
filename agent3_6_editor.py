"""
agent3_6_editor.py — Content Editor (Agent 3.6)
מגיה תוכן: קול פז, ביטויים בוטיים, כללי פלטפורמה.
נקרא אוטומטית אחרי Agent 3.
batch: קריאה אחת ל-3 פלטפורמות.
"""
from agent_editor import edit_all_content, edit_content

__all__ = ["edit_all_content", "edit_content"]

if __name__ == "__main__":
    import sys
    platforms = sys.argv[1:] or ["linkedin", "blog", "podcast"]
    note = ""
    if "--note" in platforms:
        idx = platforms.index("--note")
        note = platforms[idx + 1] if idx + 1 < len(platforms) else ""
        platforms = platforms[:idx]
    edit_all_content(platforms, extra=note)
