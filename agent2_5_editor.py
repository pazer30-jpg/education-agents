"""
agent2_5_editor.py — Article Editor (Agent 2.5)
עורך מאמר אקדמי: מבנה, ציטוטים APA 7, קוהרנטיות.
נקרא אוטומטית אחרי Agent 2.
"""
from agent_editor import edit_article

__all__ = ["edit_article"]

if __name__ == "__main__":
    import sys
    from pathlib import Path
    from config import ARTICLES_DIR
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
    else:
        mds = sorted(ARTICLES_DIR.glob("*.md"), key=lambda x: x.stat().st_mtime)
        p = mds[-1] if mds else None
    if p and p.exists():
        edit_article({"md": p, "docx": p.with_suffix(".docx")},
                     extra=sys.argv[2] if len(sys.argv) > 2 else "")
