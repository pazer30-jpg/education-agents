"""
Agent 4 — Graphic Designer
יוצר גרפיקה ויזואלית (לא רק טקסט על רקע) לכל פיס תוכן:
  - LinkedIn post  → cover graphic  (1200×627)
  - Blog post      → header graphic (1600×500)
  - Podcast ep.    → cover art      (3000×3000 square)

הגרפיקה כוללת: אלמנטים ויזואליים, איקונים, צורות אבסטרקטיות,
מטאפורות חזותיות — מינימום טקסט (רק שם + תגית קטנה).

פלט: SVG בתיקיית output/designs/ + image_prompts.txt לשימוש ב-DALL-E
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR
from claude_cli import ask_claude

DESIGNS_DIR = OUTPUT_DIR / "designs"
DESIGNS_DIR.mkdir(parents=True, exist_ok=True)

IMAGES_DIR = OUTPUT_DIR / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
# SVG → PNG conversion
# ─────────────────────────────────────────────

def _svg_to_png(svg_path: Path, png_path: Path) -> bool:
    """ממיר SVG ל-PNG. מנסה cairosvg → inkscape → rsvg-convert."""
    import os, sys
    try:
        # Suppress cairosvg's native-lib errors to stderr
        devnull = open(os.devnull, "w")
        old_stderr = sys.stderr
        sys.stderr = devnull
        try:
            import cairosvg
            cairosvg.svg2png(url=str(svg_path), write_to=str(png_path), dpi=150)
            return True
        finally:
            sys.stderr = old_stderr
            devnull.close()
    except ImportError:
        pass
    except Exception:
        pass

    import subprocess
    for cmd in [
        ["inkscape", "--export-type=png", f"--export-filename={png_path}", str(svg_path)],
        ["rsvg-convert", "-f", "png", "-o", str(png_path), str(svg_path)],
    ]:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=30)
            if r.returncode == 0 and png_path.exists():
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return False


# ─────────────────────────────────────────────
# Brand system
# ─────────────────────────────────────────────

BRAND = {
    "primary":   "#1A1A2E",
    "secondary": "#16213E",
    "accent":    "#E94560",
    "warm":      "#F5A623",
    "teal":      "#0F9B8E",
    "light":     "#EAEAEA",
    "white":     "#FFFFFF",
}


# ─────────────────────────────────────────────
# Topic → mood mapping
# ─────────────────────────────────────────────

def _infer_mood(text: str) -> str:
    """Map content to a visual mood description for Claude."""
    t = text.lower()
    if any(w in t for w in ["belong", "שייכות", "community", "קהילה", "connection"]):
        return "warmth, circles of people, interconnected shapes, gentle embrace"
    if any(w in t for w in ["leader", "מנהיגות", "leading", "management"]):
        return "upward movement, single figure above horizon, light breaking through"
    if any(w in t for w in ["resilience", "חוסן", "strength", "grit"]):
        return "bending but not breaking, tree in wind, roots deep, storm with clearing"
    if any(w in t for w in ["identity", "זהות", "self", "formation"]):
        return "mirror reflections, layers of translucent shapes, kaleidoscope"
    if any(w in t for w in ["youth", "נוער", "young", "adolescent"]):
        return "energy, upward paths, open horizon, dawn light, motion trails"
    if any(w in t for w in ["dialog", "דיאלוג", "conversation", "buber", "בובר"]):
        return "two forms facing each other, bridge between, shared space"
    if any(w in t for w in ["threshold", "סף", "transition", "liminal", "מעבר"]):
        return "doorway shapes, gradient transitions, between-space, emerging form"
    if any(w in t for w in ["hope", "תקווה", "motivation", "מוטיבציה"]):
        return "rising light, ascending paths, seeds sprouting, warm dawn colors"
    if any(w in t for w in ["outdoor", "adventure", "הרפתקה", "טבע"]):
        return "mountain paths, open sky, campfire glow, vast landscape"
    if any(w in t for w in ["digital", "דיגיטל", "online", "technology"]):
        return "connected nodes, screen glow, data streams, digital constellation"
    if any(w in t for w in ["trust", "אמון", "safe", "בטוח", "secure"]):
        return "shelter, protected space, warm enclosed circle, soft boundary"
    return "depth, layered meaning, quiet strength, abstract educational landscape"


# ─────────────────────────────────────────────
# Text overlays — minimal, on top of illustration
# ─────────────────────────────────────────────

def _overlay_linkedin(svg: str, topic_tag: str) -> str:
    """Add minimal author strip + topic pill on top of illustration."""
    tag = topic_tag[:20]
    tag_w = len(tag) * 13 + 30
    overlay = f"""
  <!-- topic pill -->
  <rect x="60" y="30" width="{tag_w}" height="36" rx="18"
        fill="{BRAND['accent']}" opacity="0.85"/>
  <text x="{60 + tag_w//2}" y="54" font-family="Assistant,Arial,sans-serif"
        font-size="16" fill="{BRAND['white']}" text-anchor="middle"
        font-weight="600">{tag}</text>
  <!-- author strip -->
  <rect x="0" y="572" width="1200" height="55"
        fill="{BRAND['primary']}" opacity="0.75"/>
  <circle cx="40" cy="599" r="20" fill="{BRAND['accent']}" opacity="0.9"/>
  <text x="40" y="605" font-family="Assistant,Arial,sans-serif" font-size="15"
        fill="{BRAND['white']}" font-weight="700" text-anchor="middle">פז</text>
  <text x="70" y="605" font-family="Assistant,Arial,sans-serif" font-size="14"
        fill="{BRAND['light']}" opacity="0.9">פז שלמה | חינוך בלתי פורמלי</text>
</svg>"""
    return svg.replace("</svg>", overlay)


def _overlay_blog(svg: str) -> str:
    """Thin bottom strip for blog."""
    overlay = f"""
  <rect x="0" y="462" width="1600" height="38"
        fill="{BRAND['primary']}" opacity="0.65"/>
  <text x="28" y="487" font-family="Heebo,Arial,sans-serif" font-size="15"
        fill="{BRAND['light']}" opacity="0.8">פז שלמה | חינוך בלתי פורמלי</text>
</svg>"""
    return svg.replace("</svg>", overlay)


def _overlay_podcast(svg: str, show_name: str, episode_num: int) -> str:
    """Top + bottom strips for podcast."""
    overlay = f"""
  <rect x="0" y="0" width="3000" height="180"
        fill="{BRAND['primary']}" opacity="0.7"/>
  <text x="140" y="120" font-family="Assistant,Arial,sans-serif" font-size="90"
        fill="{BRAND['white']}" font-weight="800">{show_name[:20]}</text>
  <rect x="0" y="2820" width="3000" height="180"
        fill="{BRAND['primary']}" opacity="0.7"/>
  <text x="140" y="2930" font-family="Heebo,Arial,sans-serif" font-size="72"
        fill="{BRAND['warm']}">פרק {episode_num}</text>
  <text x="2860" y="2930" font-family="Heebo,Arial,sans-serif" font-size="64"
        fill="{BRAND['light']}" opacity="0.6" text-anchor="end">פז שלמה</text>
</svg>"""
    return svg.replace("</svg>", overlay)


# ─────────────────────────────────────────────
# Claude SVG generator
# ─────────────────────────────────────────────

def _generate_svg(content: str, platform: str, dimensions: dict) -> tuple[str, str]:
    """
    Ask Claude to generate a graphic SVG based on content meaning.
    Returns: (svg_string, dalle_prompt)
    """
    w, h = dimensions["w"], dimensions["h"]

    platform_guide = {
        "linkedin": f"""LinkedIn cover ({w}×{h}).
Layout: graphic takes 65% left side, small branding bottom-right.
Small text allowed: topic tag (2-3 words max) in a pill badge, "פז שלמה" signature.""",

        "blog": f"""Blog header banner ({w}×{h}).
Layout: wide panoramic graphic, no text except small "פז שלמה" bottom-left.
Use horizontal flow — elements should guide the eye left to right.""",

        "podcast": f"""Podcast cover art ({w}×{h} square).
Layout: central focal graphic element, show name "חינוך בלתי פורמלי" small at bottom.
Bold, iconic, recognizable at small sizes (podcast thumbnail).""",
    }

    mood = _infer_mood(content)

    prompt = f"""You are a graphic designer creating an SVG illustration.

CONTENT (understand the theme, don't write the text):
{content[:2000]}

VISUAL MOOD: {mood}

PLATFORM: {platform_guide.get(platform, '')}

BRAND COLORS:
  Primary (dark bg): {BRAND['primary']}
  Secondary: {BRAND['secondary']}
  Accent (red): {BRAND['accent']}
  Warm (orange): {BRAND['warm']}
  Teal: {BRAND['teal']}
  Light: {BRAND['light']}

DESIGN RULES — CRITICAL:
1. THIS IS A GRAPHIC, NOT A TEXT SLIDE. The visual elements ARE the design.
2. Create visual metaphors for the content's theme using:
   - Abstract geometric shapes (circles, paths, organic curves)
   - Symbolic icons drawn with SVG paths (people, connections, growth, light, paths)
   - Flowing lines, networks, constellations
   - Layered translucent shapes creating depth
3. MINIMAL TEXT — only:
   - A small topic tag (2-3 Hebrew words) in a pill/badge shape
   - "פז שלמה" signature (small, corner)
   - NO headlines, NO paragraphs, NO sentences
4. Use gradients, opacity layers, and subtle patterns for richness
5. At least 15-20 visual SVG elements (shapes, paths, circles, lines)
6. Create visual hierarchy with size, color, and opacity variation

VISUAL METAPHOR IDEAS (choose what fits the content):
- Education/growth: ascending circles, sprouting branches, upward paths
- Connection/belonging: interconnected nodes, overlapping circles, bridges
- Identity/self: mirror shapes, layered silhouettes, nested forms
- Resilience: wave patterns, bending-not-breaking lines, anchors
- Youth/energy: dynamic angles, radiating lines, spark patterns
- Community: clustered elements, rings, gathering formations
- Transition/threshold: doorway shapes, gradient transitions, bridges

Return ONLY the SVG code. Start with <svg and end with </svg>.
No explanation, no markdown, no code blocks.

Also include a comment at the very end: <!-- DALLE: your DALL-E prompt here -->"""

    system = f"""You are an expert SVG graphic designer. You create beautiful,
modern, abstract graphic illustrations — NOT text slides.
Your designs use geometric shapes, organic curves, gradients, and visual metaphors.
Minimal text. Maximum visual impact. Clean, professional, editorial style.
viewBox must be "0 0 {w} {h}". All coordinates within bounds."""

    raw = ask_claude(prompt, system=system, max_budget=0.8)

    # Extract SVG
    svg = raw.strip()
    # Remove markdown wrapping if present
    if "```" in svg:
        match = re.search(r'<svg[\s\S]*?</svg>', svg)
        svg = match.group(0) if match else svg

    # Make sure it starts with <svg
    if not svg.startswith("<svg"):
        match = re.search(r'<svg[\s\S]*?</svg>', svg)
        if match:
            svg = match.group(0)

    # Extract DALL-E prompt from comment
    dalle = ""
    dalle_match = re.search(r'<!--\s*DALLE:\s*(.+?)\s*-->', svg)
    if dalle_match:
        dalle = dalle_match.group(1)
        svg = svg.replace(dalle_match.group(0), "")  # remove from SVG

    if not dalle:
        dalle = f"Abstract graphic illustration about education, {platform} style, deep blue and red tones, geometric shapes, no text, editorial design"

    return svg.strip(), dalle


# ─────────────────────────────────────────────
# Fallback: template-based graphic (if Claude fails)
# ─────────────────────────────────────────────

def _fallback_svg(w: int, h: int, topic_tag: str, platform: str) -> str:
    """Generate a decent graphic SVG without Claude."""
    import random
    random.seed(hash(topic_tag))

    elements = []

    # Background
    elements.append(f'''<defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{BRAND['primary']}"/>
      <stop offset="100%" stop-color="{BRAND['secondary']}"/>
    </linearGradient>
    <radialGradient id="glow" cx="30%" cy="40%">
      <stop offset="0%" stop-color="{BRAND['accent']}" stop-opacity="0.15"/>
      <stop offset="100%" stop-color="{BRAND['primary']}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="{w}" height="{h}" fill="url(#bg)"/>
  <rect width="{w}" height="{h}" fill="url(#glow)"/>''')

    # Random geometric elements
    for _ in range(12):
        x = random.randint(50, w - 100)
        y = random.randint(50, h - 100)
        r = random.randint(15, 80)
        op = round(random.uniform(0.03, 0.12), 2)
        color = random.choice([BRAND['accent'], BRAND['warm'], BRAND['teal']])
        elements.append(f'  <circle cx="{x}" cy="{y}" r="{r}" fill="{color}" opacity="{op}"/>')

    # Flowing lines
    for i in range(5):
        y_start = random.randint(100, h - 100)
        y_end = y_start + random.randint(-80, 80)
        cp1 = random.randint(200, w // 2)
        cp2 = random.randint(w // 2, w - 100)
        op = round(random.uniform(0.08, 0.2), 2)
        color = random.choice([BRAND['accent'], BRAND['warm'], BRAND['teal']])
        elements.append(
            f'  <path d="M 0 {y_start} C {cp1} {y_start-60} {cp2} {y_end+60} {w} {y_end}" '
            f'fill="none" stroke="{color}" stroke-width="2" opacity="{op}"/>'
        )

    # Constellation dots
    for _ in range(8):
        x = random.randint(100, w - 100)
        y = random.randint(80, h - 80)
        elements.append(f'  <circle cx="{x}" cy="{y}" r="4" fill="{BRAND["light"]}" opacity="0.25"/>')

    # Accent shape cluster
    cx, cy = w * 0.35, h * 0.45
    for i in range(5):
        angle_offset = i * 72
        rx = 40 + i * 15
        ry = 30 + i * 12
        elements.append(
            f'  <ellipse cx="{cx + i*20}" cy="{cy + i*10}" rx="{rx}" ry="{ry}" '
            f'fill="{BRAND["accent"]}" opacity="{0.06 + i*0.02}" '
            f'transform="rotate({angle_offset} {cx + i*20} {cy + i*10})"/>'
        )

    # Small topic tag pill
    tag = topic_tag[:20]
    tag_w = len(tag) * 12 + 30
    if platform == "podcast":
        tx, ty = w // 2 - tag_w // 2, h - 200
    else:
        tx, ty = 60, h - 70
    elements.append(
        f'  <rect x="{tx}" y="{ty}" width="{tag_w}" height="32" rx="16" '
        f'fill="{BRAND["accent"]}" opacity="0.85"/>\n'
        f'  <text x="{tx + tag_w//2}" y="{ty + 22}" font-family="Assistant,Arial,sans-serif" '
        f'font-size="15" fill="{BRAND["white"]}" text-anchor="middle" font-weight="600">{tag}</text>'
    )

    # Signature
    sx = w - 180 if platform != "podcast" else w // 2
    sy = h - 30 if platform != "podcast" else h - 80
    anchor = "end" if platform != "podcast" else "middle"
    elements.append(
        f'  <text x="{sx}" y="{sy}" font-family="Assistant,Arial,sans-serif" font-size="14" '
        f'fill="{BRAND["light"]}" opacity="0.5" text-anchor="{anchor}">פז שלמה</text>'
    )

    body = "\n".join(elements)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}">\n{body}\n</svg>'


# ─────────────────────────────────────────────
# Save helper
# ─────────────────────────────────────────────

def _save(svg: str, slug: str, dalle_prompt: str, label: str) -> Path:
    ts       = datetime.now().strftime("%Y%m%d_%H%M")
    svg_path = DESIGNS_DIR / f"{slug}_{ts}.svg"
    svg_path.write_text(svg, encoding="utf-8")

    png_path = IMAGES_DIR / f"{slug}_{ts}.png"
    png_ok = _svg_to_png(svg_path, png_path)
    if png_ok:
        print(f"  [Agent4] 🖼  PNG: {png_path.name}")
    else:
        print(f"  [Agent4] ℹ️  SVG only (pip install cairosvg for PNG)")

    prompts_file = DESIGNS_DIR / "image_prompts.txt"
    with open(prompts_file, "a", encoding="utf-8") as f:
        f.write(f"\n{'─'*60}\n[{label}] {svg_path.name}\n")
        f.write(f"DALL-E / Midjourney:\n{dalle_prompt}\n")

    return png_path if png_ok else svg_path


# ─────────────────────────────────────────────
# Extract topic from content (lightweight)
# ─────────────────────────────────────────────

def _extract_topic_tag(content: str) -> str:
    """Extract a short topic tag from content — first heading or first bold text."""
    # Try first heading
    match = re.search(r'^#\s+(.+)', content, re.MULTILINE)
    if match:
        title = match.group(1).strip()
        # Take first 2-3 meaningful words
        words = [w for w in title.split() if len(w) > 2][:3]
        return " ".join(words)[:20]

    # Try first line
    first = content.strip().split("\n")[0][:30]
    words = [w for w in first.split() if len(w) > 2][:3]
    return " ".join(words)[:20] or "חינוך"


# ─────────────────────────────────────────────
# Per-platform designers
# ─────────────────────────────────────────────

DIMENSIONS = {
    "linkedin": {"w": 1200, "h": 627},
    "blog":     {"w": 1600, "h": 500},
    "podcast":  {"w": 3000, "h": 3000},
}


def _design_platform(content_path: Path, platform: str,
                     show_name: str = "חינוך בלתי פורמלי",
                     episode_num: int = 1) -> Path:
    label = {"linkedin": "LinkedIn", "blog": "Blog", "podcast": "Podcast"}.get(platform, platform)
    print(f"  [Agent4] מעצב גרפיקה ל-{label}...")

    content = content_path.read_text(encoding="utf-8", errors="replace")
    topic_tag = _extract_topic_tag(content)
    mood = _infer_mood(content)
    dims = DIMENSIONS[platform]

    try:
        svg, dalle = _generate_svg(content, platform, dims)

        # Validate SVG
        if "<svg" not in svg or "</svg>" not in svg or len(svg) < 200:
            raise ValueError("Invalid SVG output")

    except Exception as e:
        print(f"  [Agent4] ⚠️  Claude SVG failed ({e}) — using template")
        svg = _fallback_svg(dims["w"], dims["h"], topic_tag, platform)
        dalle = ""

    # Apply text overlays (minimal — on top of graphic)
    if platform == "linkedin":
        svg = _overlay_linkedin(svg, topic_tag)
    elif platform == "blog":
        svg = _overlay_blog(svg)
    elif platform == "podcast":
        svg = _overlay_podcast(svg, show_name, episode_num)

    # DALL-E prompt based on mood
    if not dalle:
        dalle = (f"Editorial illustration, no text, {mood}, "
                 f"deep navy and coral palette, conceptual minimal art, "
                 f"{platform} format, cinematic quality")

    slug = content_path.stem[:30]
    path = _save(svg, f"{slug}_{platform}", dalle, label)
    print(f"  [Agent4] {label} → {path.name}")
    return path


# ─────────────────────────────────────────────
# Main agent function
# ─────────────────────────────────────────────

def run_designer(
    article_paths: dict[str, Path] = None,
    post_paths: dict[str, list] = None,
    design_types: list[str] = None,
    topic: str = "",
    show_name: str = "חינוך בלתי פורמלי",
    episode_num: int = 1,
) -> dict[str, Path]:
    article_paths = article_paths or {}
    post_paths = post_paths or {}
    design_types = design_types or ["linkedin_cover", "blog_banner"]

    print(f"\n{'='*60}")
    print(f"🎨 Agent 4 — Designer (graphic) | {', '.join(design_types)}")
    print(f"{'='*60}\n")

    saved = {}

    def _latest(paths: list) -> Path | None:
        valid = [p for p in paths if Path(p).exists()]
        return max(valid, key=lambda p: Path(p).stat().st_mtime) if valid else None

    platform_map = {
        "linkedin_cover": ("linkedin", post_paths.get("linkedin", [])),
        "blog_banner":    ("blog",     post_paths.get("blog", [])),
        "podcast_cover":  ("podcast",  post_paths.get("podcast", [])),
    }

    for design_type in design_types:
        if design_type not in platform_map:
            continue
        platform, paths = platform_map[design_type]
        content_file = _latest(paths)
        if content_file:
            saved[platform] = _design_platform(
                Path(content_file), platform,
                show_name=show_name, episode_num=episode_num,
            )
        else:
            print(f"  [Agent4] ⚠️  לא נמצא קובץ ל-{platform} — מדלג")

    print(f"\n✅ Agent 4 complete → {list(saved.keys())}\n")
    return saved


# ─────────────────────────────────────────────
# Standalone CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    from config import POSTS_DIR

    args = sys.argv[1:]

    post_paths = {}
    for platform in ("linkedin", "blog", "podcast"):
        if platform == "linkedin":
            files = sorted(POSTS_DIR.glob("*_linkedin_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        elif platform == "blog":
            files = sorted(POSTS_DIR.glob("*_blog_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        else:
            files = sorted(POSTS_DIR.glob("*_podcast_script_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            post_paths[platform] = [files[0]]

    if not post_paths:
        print("לא נמצאו קבצי תוכן ב-output/posts/. הרץ Agent 3 קודם.")
        sys.exit(1)

    types = args if args else ["linkedin_cover", "blog_banner", "podcast_cover"]
    valid_types = {"linkedin_cover", "blog_banner", "podcast_cover"}
    types = [t for t in types if t in valid_types] or ["linkedin_cover"]

    results = run_designer(post_paths=post_paths, design_types=types)
    for platform, path in results.items():
        print(f"  {platform}: {path}")
