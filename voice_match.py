"""
voice_match.py — Voice Match Score.

נותן ציון 0-100 לפוסט חדש לפי דמיון סמנטי ל-top 10 פוסטים שעבדו היסטורית.
לא בודק structure — בודק האם זה **באמת נשמע כמו פז**.

אלגוריתם:
  1. בונה reference set — 10-20 פוסטים שעבדו (QA גבוה + לא ישנים)
  2. embed את ה-reference (sentence-transformers, cached)
  3. embed פוסט חדש
  4. ציון = ממוצע cosine similarity ל-top 5 הכי דומים
  5. <0.55 → התראה: "זה לא נשמע כמוך"
  6. >0.85 → התראה: "אולי קרוב מדי למה שכבר כתבת"

ה-balanced sweet spot: 0.60-0.80.

Usage:
  python3 voice_match.py <file>              # ציון לקובץ ספציפי
  python3 voice_match.py --rebuild-ref        # בונה מחדש את ה-reference set
  python3 voice_match.py --scan posts/linkedin  # סורק תיקייה
"""

import sys
import re
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from config import OUTPUT_DIR

try:
    from embeddings import embed, embed_batch, cosine_similarity
    HAS_EMBEDDINGS = True
except Exception:
    HAS_EMBEDDINGS = False


# ─────────────────────────────────────────────
# Build reference set
# ─────────────────────────────────────────────

REF_FILE = OUTPUT_DIR / "_state" / "voice_reference.json"


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:]
    return text


def _load_qa_scores() -> dict[str, float]:
    f = OUTPUT_DIR / "analytics.json"
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        scores = {}
        for r in data.get("runs", []):
            qa = r.get("avg_qa")
            if isinstance(qa, (int, float)):
                for o in r.get("outputs", []):
                    fp = o.get("file")
                    if fp:
                        scores[fp] = float(qa)
        return scores
    except Exception:
        return {}


def _all_posts() -> list[Path]:
    posts = []
    for sub in ("posts/linkedin", "posts/blog", "posts/podcast"):
        d = OUTPUT_DIR / sub
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file() and p.suffix in (".md", ".txt"):
                if p.name.startswith("_") or p.name.endswith(".bak"):
                    continue
                posts.append(p)
    return posts


def build_reference(top_n: int = 15) -> dict:
    """Build/refresh reference set of top-performing posts."""
    posts = _all_posts()
    qa_scores = _load_qa_scores()

    scored = []
    cutoff = (datetime.now() - timedelta(days=180)).timestamp()
    for p in posts:
        try:
            mtime = p.stat().st_mtime
            if mtime < cutoff:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            body = _strip_frontmatter(text).strip()
            if len(body) < 200:
                continue
        except Exception:
            continue

        qa = qa_scores.get(str(p), 50.0)
        scored.append({"path": str(p), "qa": qa, "text": body[:3000], "mtime": mtime})

    scored.sort(key=lambda x: (x["qa"], x["mtime"]), reverse=True)
    top = scored[:top_n]

    # Embed each
    if HAS_EMBEDDINGS and top:
        texts = [t["text"] for t in top]
        vecs = embed_batch(texts)
        for entry, vec in zip(top, vecs):
            entry["embedding"] = vec

    REF_FILE.parent.mkdir(parents=True, exist_ok=True)
    REF_FILE.write_text(json.dumps({
        "built_at": datetime.now().isoformat(),
        "n": len(top),
        "posts": [{"path": t["path"], "qa": t["qa"],
                   "embedding": t.get("embedding", [])} for t in top],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"n": len(top), "file": REF_FILE}


def load_reference() -> list[dict]:
    if not REF_FILE.exists():
        return []
    try:
        return json.loads(REF_FILE.read_text(encoding="utf-8")).get("posts", [])
    except Exception:
        return []


# ─────────────────────────────────────────────
# Score a single post
# ─────────────────────────────────────────────

def score_text(text: str, ref: list[dict] = None) -> dict:
    """
    Return:
      {
        "score": 0-100,
        "max_similarity": 0-1,
        "avg_top5": 0-1,
        "verdict": "ok"|"too_generic"|"too_similar",
        "closest": [{"path": str, "similarity": float}, ...]
      }
    """
    if not HAS_EMBEDDINGS:
        return {"score": 50, "verdict": "no_embeddings", "max_similarity": 0,
                "avg_top5": 0, "closest": []}

    body = _strip_frontmatter(text).strip()[:3000]
    if len(body) < 100:
        return {"score": 0, "verdict": "too_short", "max_similarity": 0,
                "avg_top5": 0, "closest": []}

    if ref is None:
        ref = load_reference()
    if not ref:
        return {"score": 50, "verdict": "no_reference", "max_similarity": 0,
                "avg_top5": 0, "closest": []}

    vec = embed(body)
    sims = []
    for r in ref:
        r_vec = r.get("embedding") or []
        if not r_vec:
            continue
        sim = cosine_similarity(vec, r_vec)
        sims.append({"path": r["path"], "similarity": sim})

    if not sims:
        return {"score": 50, "verdict": "no_embeddings", "max_similarity": 0,
                "avg_top5": 0, "closest": []}

    sims.sort(key=lambda x: x["similarity"], reverse=True)
    top5 = sims[:5]
    max_sim = top5[0]["similarity"]
    avg_top5 = sum(s["similarity"] for s in top5) / len(top5)

    # Score: 0-100. Sweet spot is 0.60-0.80.
    if avg_top5 < 0.55:
        verdict = "too_generic"
        score = int(avg_top5 / 0.55 * 50)  # 0-50
    elif avg_top5 > 0.85:
        verdict = "too_similar"
        # Penalize over-similarity
        score = int(85 - (avg_top5 - 0.85) * 100)  # drops below 85
    else:
        verdict = "ok"
        # Linear interpolation in sweet spot
        score = int(50 + (avg_top5 - 0.55) / 0.30 * 50)  # 50-100

    return {
        "score": max(0, min(100, score)),
        "verdict": verdict,
        "max_similarity": round(max_sim, 3),
        "avg_top5": round(avg_top5, 3),
        "closest": [{"path": Path(s["path"]).name, "similarity": round(s["similarity"], 3)}
                    for s in top5],
    }


def score_file(file_path: Path) -> dict:
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"score": 0, "verdict": "read_error", "error": str(e)}
    return score_text(text)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def _print_score(s: dict, label: str = ""):
    if label:
        print(f"\n📊 {label}")
    icon = {"ok": "🟢", "too_generic": "🟡", "too_similar": "🟠",
            "too_short": "⚪", "no_reference": "⚪", "no_embeddings": "⚪",
            "read_error": "🔴"}.get(s.get("verdict"), "❓")
    msg = {"ok": "תקין — נשמע כמו פז",
           "too_generic": "גנרי מדי — לא נשמע כמוך",
           "too_similar": "דומה מדי לפוסט קיים — אולי חזרה?",
           "too_short": "קצר מדי לניתוח",
           "no_reference": "אין reference set — הרץ --rebuild-ref",
           "no_embeddings": "ספריית embeddings לא זמינה",
           "read_error": "שגיאת קריאה"}.get(s.get("verdict"), s.get("verdict", "?"))
    print(f"   {icon} Score: {s.get('score', 0)}/100 — {msg}")
    if s.get("avg_top5"):
        print(f"   avg_top5_similarity = {s['avg_top5']}")
    if s.get("closest"):
        print(f"   Closest matches:")
        for c in s["closest"][:3]:
            print(f"      • {c['similarity']:.3f}  {c['path']}")


def main():
    if "--rebuild-ref" in sys.argv:
        print("🔨 Building reference set from top-performing posts...")
        if not HAS_EMBEDDINGS:
            print("❌ embeddings module not available. Install: pip install sentence-transformers")
            return
        result = build_reference()
        print(f"✅ Reference set: {result['n']} posts → {result['file'].relative_to(OUTPUT_DIR.parent)}")
        return

    if "--scan" in sys.argv:
        idx = sys.argv.index("--scan")
        target = Path(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else None
        if not target or not target.exists():
            print(f"❌ Path not found: {target}")
            return
        files = list(target.rglob("*.md")) + list(target.rglob("*.txt"))
        ref = load_reference()
        if not ref:
            print("❌ No reference set. Run: --rebuild-ref")
            return
        for f in files[:30]:
            if f.name.startswith("_"):
                continue
            s = score_file(f)
            _print_score(s, label=f.name)
        return

    # Default: score a single file
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("Usage:")
        print("  python3 voice_match.py <file>")
        print("  python3 voice_match.py --rebuild-ref")
        print("  python3 voice_match.py --scan <directory>")
        return

    p = Path(args[0])
    if not p.exists():
        print(f"❌ File not found: {p}")
        return

    s = score_file(p)
    _print_score(s, label=p.name)


if __name__ == "__main__":
    main()
