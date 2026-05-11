"""
canvas_builder.py — Generate Obsidian Canvas (.canvas) files.

Canvas הוא mind map דיגיטלי של Obsidian. JSON format נתמך מובנה.
מייצר 2 קנבסים:
  1. _thesis-canvas.canvas — קשתות + הוגים מרכזיים + חיבורים
  2. _theme-canvas.canvas   — themes × posts mapping

Usage:
  python3 canvas_builder.py
  python3 canvas_builder.py --thesis
  python3 canvas_builder.py --theme
"""

import sys
import json
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR, ARTICLES_DIR


# ─────────────────────────────────────────────
# Canvas JSON spec helpers
# ─────────────────────────────────────────────

def _node(id, x, y, width, height, **kwargs):
    """Create a canvas node."""
    base = {"id": id, "x": x, "y": y, "width": width, "height": height}
    base.update(kwargs)
    return base


def _text_node(id, x, y, w, h, text, color=None):
    n = _node(id, x, y, w, h, type="text", text=text)
    if color:
        n["color"] = str(color)  # 1=red, 2=orange, 3=yellow, 4=green, 5=cyan, 6=purple
    return n


def _file_node(id, x, y, w, h, file_path, color=None):
    n = _node(id, x, y, w, h, type="file", file=file_path)
    if color:
        n["color"] = str(color)
    return n


def _edge(id, from_id, to_id, label=None):
    e = {"id": id, "fromNode": from_id, "fromSide": "right",
         "toNode": to_id, "toSide": "left"}
    if label:
        e["label"] = label
    return e


# ─────────────────────────────────────────────
# Thesis canvas — arcs + central authors
# ─────────────────────────────────────────────

def build_thesis_canvas() -> Path:
    """Build canvas showing arcs as columns + key authors."""
    canvas = {"nodes": [], "edges": []}

    # Read arc manifests
    arcs_dir = OUTPUT_DIR / "_arcs"
    arcs = []
    if arcs_dir.exists():
        for f in sorted(arcs_dir.glob("arc_*.json")):
            try:
                arcs.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass

    # Layout: each arc as a vertical column
    col_x = 0
    arc_widths = 320
    arc_spacing = 360

    for i, arc in enumerate(arcs[:7]):
        # Arc header
        header_id = f"arc_header_{i}"
        canvas["nodes"].append(_text_node(
            header_id, col_x, 0, arc_widths, 100,
            f"# 📐 {arc.get('arc_id', '?')}\n\n{arc.get('thesis', '')[:120]}\n\n_{arc.get('post_count', 0)} posts_",
            color=str(((i % 6) + 1)),
        ))

        # Posts in arc (vertical stack)
        y = 130
        prev_id = header_id
        for j, post in enumerate(arc.get("posts", [])[:8]):
            post_id = f"arc_{i}_post_{j}"
            file_path = post.get("path", "")
            stage = post.get("stage", "")
            stage_emoji = {"opening": "🌱", "building": "🌳", "complication": "⚡", "conclusion": "🎯"}.get(stage, "")
            label = f"{stage_emoji} {post.get('title', '?')[:50]}"

            if file_path:
                canvas["nodes"].append(_file_node(
                    post_id, col_x, y, arc_widths, 80, file_path,
                ))
            else:
                canvas["nodes"].append(_text_node(
                    post_id, col_x, y, arc_widths, 80, label,
                ))

            # Connect previous to current
            canvas["edges"].append({
                "id": f"e_{i}_{j}",
                "fromNode": prev_id,
                "fromSide": "bottom",
                "toNode": post_id,
                "toSide": "top",
            })
            prev_id = post_id
            y += 100

        col_x += arc_spacing

    # Add hub nodes (top central authors) on the right side
    try:
        from corpus_graph import build_graph, centrality_analysis
        G = build_graph()
        cent = centrality_analysis(G, top_n=8)
        hub_x = col_x + 100
        hub_y = 100
        for i, entry in enumerate(cent.get("top", [])[:6]):
            author = entry["author"]
            citations = entry["citations"]
            canvas["nodes"].append(_text_node(
                f"hub_{i}",
                hub_x, hub_y + i * 90,
                240, 70,
                f"## {author}\n_מצוטט {citations}×_",
                color="6",
            ))
    except Exception as e:
        print(f"  ⚠️ Could not add hub nodes: {e}")

    # Save
    out = OUTPUT_DIR / "_thesis-canvas.canvas"
    out.write_text(json.dumps(canvas, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ─────────────────────────────────────────────
# Theme canvas — themes × posts grid
# ─────────────────────────────────────────────

def build_theme_canvas() -> Path:
    """Themes as columns, posts as nodes underneath."""
    canvas = {"nodes": [], "edges": []}

    THEME_KEYWORDS = {
        "שייכות": ("belonging", "1"),
        "חוסן": ("resilience", "4"),
        "מנהיגות": ("leadership", "6"),
        "טראומה": ("trauma", "1"),
        "דיאלוג": ("dialogue", "5"),
        "פדגוגיה": ("pedagogy", "3"),
        "זיכרון": ("memory", "2"),
    }

    col_x = 0
    width = 280

    for theme, (en, color) in THEME_KEYWORDS.items():
        # Theme header
        canvas["nodes"].append(_text_node(
            f"theme_{theme}", col_x, 0, width, 80,
            f"# {theme}\n_{en}_",
            color=color,
        ))

        # Find posts with this theme tag
        posts_with_theme = []
        for d in [OUTPUT_DIR / "posts" / "linkedin", OUTPUT_DIR / "posts" / "blog"]:
            if not d.exists():
                continue
            for f in sorted(d.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
                if f.is_file() and not f.name.endswith(".bak"):
                    try:
                        text = f.read_text(encoding="utf-8", errors="ignore")[:1000]
                        if theme in text:
                            posts_with_theme.append(f)
                            if len(posts_with_theme) >= 5:
                                break
                    except Exception:
                        pass

        # Add post nodes
        y = 100
        for j, p in enumerate(posts_with_theme):
            try:
                rel_path = str(p.relative_to(OUTPUT_DIR))
                canvas["nodes"].append(_file_node(
                    f"theme_{theme}_post_{j}",
                    col_x, y, width, 100,
                    rel_path,
                ))
                y += 110
            except Exception:
                pass

        col_x += width + 60

    out = OUTPUT_DIR / "_theme-canvas.canvas"
    out.write_text(json.dumps(canvas, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    if "--thesis" in sys.argv:
        path = build_thesis_canvas()
        print(f"✅ {path.name}")
    elif "--theme" in sys.argv:
        path = build_theme_canvas()
        print(f"✅ {path.name}")
    else:
        # Build both
        p1 = build_thesis_canvas()
        p2 = build_theme_canvas()
        print(f"✅ {p1.name}")
        print(f"✅ {p2.name}")
        print("\nפתח ב-Obsidian:")
        print(f"  - _thesis-canvas.canvas → קשתות + הוגים מרכזיים")
        print(f"  - _theme-canvas.canvas  → themes × posts grid")


if __name__ == "__main__":
    main()
