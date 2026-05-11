"""
book_outliner.py — Find chapter ideas from accumulated posts.
פעם בחודש — סורק 30+ פוסטים אחרונים, מציע מבנה לספר.

Logic:
  1. קלסטרינג של פוסטים לפי נושא (semantic embeddings)
  2. כל קלסטר = פוטנציאל לפרק
  3. לכל קלסטר — מציע thesis + 5-7 פוסטים שיכולים לעוף לפרק

Usage:
  python3 book_outliner.py                    # full outline
  python3 book_outliner.py --min-cluster 5    # only clusters with 5+ posts
"""

import sys
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from config import OUTPUT_DIR, LINKEDIN_DIR, BLOG_DIR

OUTLINES_DIR = OUTPUT_DIR / "book_outlines"
OUTLINES_DIR.mkdir(parents=True, exist_ok=True)


def _collect_posts() -> list[dict]:
    """Gather all posts (LinkedIn ready + Blog) with text + date."""
    posts = []
    for d, pattern, kind in [
        (LINKEDIN_DIR, "*ready*.txt", "LinkedIn"),
        (BLOG_DIR, "*.md", "Blog"),
    ]:
        if not d.exists():
            continue
        for f in d.glob(pattern):
            if f.name.endswith(".bak"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            # Extract title (first non-empty line, stripped of markdown)
            title = ""
            for line in text.split("\n"):
                line = line.strip().lstrip("#").strip()
                if line and len(line) > 10:
                    title = line[:100]
                    break
            posts.append({
                "title": title or f.stem,
                "kind": kind,
                "file": f.name,
                "text": text[:1500],  # first 1500 chars for clustering
                "date": datetime.fromtimestamp(f.stat().st_mtime).strftime("%d/%m/%Y"),
                "ts": f.stat().st_mtime,
            })
    return sorted(posts, key=lambda p: p["ts"], reverse=True)


def cluster_posts(posts: list[dict], threshold: float = 0.5) -> list[dict]:
    """Cluster posts using semantic embeddings."""
    try:
        from embeddings import embed_batch, cosine_similarity
    except ImportError:
        print("  ⚠️  embeddings not available — falling back to keyword overlap")
        return _keyword_cluster(posts)

    if not posts:
        return []

    # Embed each post (text + title)
    texts = [f"{p['title']} {p['text'][:500]}" for p in posts]
    print(f"  [book_outliner] Embedding {len(texts)} posts...")
    vecs = embed_batch(texts)

    # Greedy clustering
    clusters = []
    assigned = [False] * len(posts)
    for i in range(len(posts)):
        if assigned[i]:
            continue
        cluster = [i]
        assigned[i] = True
        for j in range(i + 1, len(posts)):
            if assigned[j]:
                continue
            if cosine_similarity(vecs[i], vecs[j]) >= threshold:
                cluster.append(j)
                assigned[j] = True
        if len(cluster) >= 2:  # ignore singletons
            clusters.append({
                "anchor_idx": i,
                "anchor_title": posts[i]["title"],
                "post_indices": cluster,
                "size": len(cluster),
            })

    # Sort by cluster size
    clusters.sort(key=lambda c: c["size"], reverse=True)
    return clusters


def _keyword_cluster(posts: list[dict]) -> list[dict]:
    """Fallback: keyword-based clustering."""
    by_keyword = defaultdict(list)
    keywords = ["שייכות", "חוסן", "טראומה", "מנהיגות", "דיאלוג", "זיכרון",
                "מתבגרים", "מדריכים", "תקווה", "חינוך", "קבוצה"]
    for i, p in enumerate(posts):
        text = (p["title"] + " " + p["text"]).lower()
        for kw in keywords:
            if kw in text:
                by_keyword[kw].append(i)
                break
    return [
        {"anchor_idx": indices[0], "anchor_title": kw, "post_indices": indices, "size": len(indices)}
        for kw, indices in by_keyword.items() if len(indices) >= 2
    ]


def generate_outline(min_cluster: int = 3) -> Path | None:
    posts = _collect_posts()
    if len(posts) < 5:
        print(f"  ℹ️  אין מספיק פוסטים ({len(posts)}) — צריך 5+")
        return None

    print(f"  [book_outliner] {len(posts)} posts — clustering...")
    clusters = cluster_posts(posts)
    valid = [c for c in clusters if c["size"] >= min_cluster]

    if not valid:
        print(f"  ⚠️  אין קלסטרים עם {min_cluster}+ פוסטים")
        return None

    # Try to use Claude for chapter naming/synthesis
    chapter_briefs = []
    try:
        from claude_cli import ask_claude_json
        for c in valid[:8]:
            anchor_post = posts[c["anchor_idx"]]
            related_titles = [posts[i]["title"][:80] for i in c["post_indices"]]
            try:
                brief = ask_claude_json(
                    f"""נושא משותף לפוסטים האלה:
{chr(10).join('- ' + t for t in related_titles)}

החזר JSON:
{{
  "chapter_title": "שם פרק חד וברור (עברית)",
  "chapter_thesis": "טענה מרכזית של הפרק (משפט אחד)",
  "what_paz_argues": "מה פז טוען בפרק הזה (2-3 משפטים)"
}}
JSON בלבד.""",
                    max_budget=0.3, timeout=60,
                )
                chapter_briefs.append({**c, "brief": brief})
            except Exception:
                chapter_briefs.append({**c, "brief": None})
    except Exception:
        chapter_briefs = [{**c, "brief": None} for c in valid]

    # Build outline markdown
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = OUTLINES_DIR / f"book_outline_{stamp}.md"

    lines = [
        f"# Book Outline — מוקי 🦊",
        f"",
        f"_Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')}_",
        f"_Based on {len(posts)} posts, {len(valid)} chapter candidates_",
        f"",
        f"---",
        f"",
    ]

    for i, c in enumerate(chapter_briefs[:8], 1):
        brief = c.get("brief") or {}
        title = brief.get("chapter_title") or f"פרק {i}: {c['anchor_title'][:60]}"
        lines.append(f"## פרק {i}: {title}")
        lines.append("")
        if brief.get("chapter_thesis"):
            lines.append(f"**Thesis:** {brief['chapter_thesis']}")
            lines.append("")
        if brief.get("what_paz_argues"):
            lines.append(f"**טיעון:** {brief['what_paz_argues']}")
            lines.append("")
        lines.append(f"**מבוסס על {c['size']} פוסטים:**")
        lines.append("")
        for idx in c["post_indices"][:7]:
            p = posts[idx]
            lines.append(f"- [{p['kind']} · {p['date']}] {p['title']}")
        lines.append("")
        lines.append("---")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ Book outline: {out_path}")
    print(f"   {len(valid)} chapter candidates from {len(posts)} posts")
    return out_path


def main():
    min_cluster = 3
    if "--min-cluster" in sys.argv:
        idx = sys.argv.index("--min-cluster")
        if idx + 1 < len(sys.argv):
            try:
                min_cluster = int(sys.argv[idx + 1])
            except ValueError:
                pass
    generate_outline(min_cluster=min_cluster)


if __name__ == "__main__":
    main()
