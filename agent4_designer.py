"""
Agent 4 — Graphic Designer
יוצר תמונה אחת לכל פיס תוכן שנוצר ב-Agent 3:
  - LinkedIn post  → cover image  (1200×627)
  - Blog post      → header image (1600×500)
  - Podcast ep.    → cover art    (3000×3000 square)

מופעל אוטומטית אחרי Agent 3.
פלט: SVG בתיקיית output/designs/ + image_prompts.txt לשימוש ב-DALL-E
"""

import json
import sys
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR
from claude_cli import ask_claude_json

DESIGNS_DIR = OUTPUT_DIR / "designs"
DESIGNS_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
# Brand system
# ─────────────────────────────────────────────

BRAND = {
    "primary":   "#1A1A2E",
    "secondary": "#16213E",
    "accent":    "#E94560",
    "warm":      "#F5A623",
    "light":     "#EAEAEA",
    "white":     "#FFFFFF",
    "font_he":   "Assistant, Arial, sans-serif",
    "font_body": "Heebo, Arial, sans-serif",
}


# ─────────────────────────────────────────────
# SVG generators — one per platform
# ─────────────────────────────────────────────

def _svg_linkedin(headline: str, subline: str, topic_tag: str) -> str:
    """1200×627 — LinkedIn recommended cover size"""
    h  = headline[:52] + ("…" if len(headline) > 52 else "")
    s  = subline[:72]  + ("…" if len(subline)   > 72 else "")
    tl = topic_tag[:26]
    tw = min(len(tl) * 15 + 44, 360)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 627">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{BRAND['primary']}"/>
      <stop offset="100%" stop-color="{BRAND['secondary']}"/>
    </linearGradient>
    <linearGradient id="bar" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="{BRAND['accent']}"/>
      <stop offset="100%" stop-color="{BRAND['warm']}"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="627" fill="url(#bg)"/>
  <circle cx="980" cy="110" r="200" fill="{BRAND['accent']}" opacity="0.07"/>
  <circle cx="1080" cy="520" r="130" fill="{BRAND['warm']}"  opacity="0.06"/>
  <circle cx="60"   cy="560" r="100" fill="{BRAND['accent']}" opacity="0.05"/>
  <rect x="80" y="195" width="6" height="230" fill="url(#bar)" rx="3"/>
  <rect x="80" y="128" width="{tw}" height="44" rx="22" fill="{BRAND['accent']}"/>
  <text x="102" y="157" font-family="{BRAND['font_he']}" font-size="19"
        fill="{BRAND['white']}" font-weight="600">{tl}</text>
  <text x="108" y="268" font-family="{BRAND['font_he']}" font-size="50"
        fill="{BRAND['white']}" font-weight="800">{h}</text>
  <text x="108" y="320" font-family="{BRAND['font_body']}" font-size="25"
        fill="{BRAND['light']}" opacity="0.85">{s}</text>
  <circle cx="120" cy="536" r="30" fill="{BRAND['accent']}" opacity="0.85"/>
  <text x="120" y="544" font-family="{BRAND['font_he']}" font-size="22"
        fill="{BRAND['white']}" font-weight="700" text-anchor="middle">פז</text>
  <text x="164" y="544" font-family="{BRAND['font_he']}" font-size="19"
        fill="{BRAND['light']}" opacity="0.9">פז שלמה | חינוך בלתי פורמלי</text>
</svg>"""


def _svg_blog(headline: str, subline: str) -> str:
    """1600×500 — blog header banner"""
    h = headline[:55] + ("…" if len(headline) > 55 else "")
    s = subline[:80]  + ("…" if len(subline)   > 80 else "")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 500">
  <defs>
    <linearGradient id="bg2" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%"   stop-color="{BRAND['primary']}"/>
      <stop offset="65%"  stop-color="{BRAND['secondary']}"/>
      <stop offset="100%" stop-color="#0F3460"/>
    </linearGradient>
  </defs>
  <rect width="1600" height="500" fill="url(#bg2)"/>
  <polygon points="1250,0 1600,0 1600,500 1400,500"
           fill="{BRAND['accent']}" opacity="0.07"/>
  <polygon points="1400,0 1600,0 1600,280"
           fill="{BRAND['warm']}"  opacity="0.06"/>
  <rect x="0" y="0" width="1600" height="5" fill="{BRAND['accent']}"/>
  <text x="800" y="205" font-family="{BRAND['font_he']}" font-size="62"
        fill="{BRAND['white']}" font-weight="800" text-anchor="middle">{h}</text>
  <rect x="690" y="228" width="220" height="3"
        fill="{BRAND['accent']}" rx="2"/>
  <text x="800" y="285" font-family="{BRAND['font_body']}" font-size="28"
        fill="{BRAND['light']}" opacity="0.82" text-anchor="middle">{s}</text>
  <rect x="0" y="455" width="1600" height="45"
        fill="{BRAND['accent']}" opacity="0.12"/>
  <text x="50" y="484" font-family="{BRAND['font_body']}" font-size="18"
        fill="{BRAND['light']}" opacity="0.7">פז שלמה | חינוך בלתי פורמלי ומנהיגות</text>
</svg>"""


def _svg_podcast(show_name: str, episode_title: str, episode_num: int) -> str:
    """3000×3000 — podcast cover (Spotify/Apple minimum 3000px)"""
    sn  = show_name[:22]
    et1 = episode_title[:28]
    et2 = episode_title[28:56] if len(episode_title) > 28 else ""
    et2_tag = (
        f'<text x="1500" y="1610" font-family="{BRAND["font_body"]}" font-size="100"'
        f' fill="{BRAND["light"]}" text-anchor="middle">{et2}</text>'
        if et2 else ""
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3000 3000">
  <defs>
    <radialGradient id="bg3" cx="50%" cy="40%">
      <stop offset="0%"   stop-color="#1E3A5F"/>
      <stop offset="100%" stop-color="{BRAND['primary']}"/>
    </radialGradient>
  </defs>
  <rect width="3000" height="3000" fill="url(#bg3)"/>
  <g opacity="0.13" fill="{BRAND['accent']}">
    <rect x="180" y="1320" width="45" height="360" rx="22"/>
    <rect x="265" y="1210" width="45" height="580" rx="22"/>
    <rect x="350" y="1090" width="45" height="820" rx="22"/>
    <rect x="435" y="1270" width="45" height="460" rx="22"/>
    <rect x="520" y="1160" width="45" height="680" rx="22"/>
    <rect x="2435" y="1320" width="45" height="360" rx="22"/>
    <rect x="2520" y="1160" width="45" height="680" rx="22"/>
    <rect x="2605" y="1060" width="45" height="880" rx="22"/>
    <rect x="2690" y="1210" width="45" height="580" rx="22"/>
    <rect x="2775" y="1320" width="45" height="360" rx="22"/>
  </g>
  <circle cx="1500" cy="720" r="260" fill="{BRAND['accent']}" opacity="0.88"/>
  <text x="1500" y="810" font-size="260" text-anchor="middle"
        font-family="Apple Color Emoji,Segoe UI Emoji,sans-serif">&#x1F399;</text>
  <text x="1500" y="1085" font-family="{BRAND['font_he']}" font-size="82"
        fill="{BRAND['accent']}" font-weight="300" text-anchor="middle"
        letter-spacing="6">פרק {episode_num}</text>
  <text x="1500" y="1290" font-family="{BRAND['font_he']}" font-size="138"
        fill="{BRAND['white']}" font-weight="800" text-anchor="middle">{sn}</text>
  <rect x="850" y="1340" width="1300" height="5" fill="{BRAND['accent']}" rx="3"/>
  <text x="1500" y="1490" font-family="{BRAND['font_body']}" font-size="100"
        fill="{BRAND['light']}" text-anchor="middle">{et1}</text>
  {et2_tag}
  <text x="1500" y="2860" font-family="{BRAND['font_body']}" font-size="72"
        fill="{BRAND['light']}" opacity="0.45" text-anchor="middle">פז שלמה</text>
</svg>"""


# ─────────────────────────────────────────────
# Save helper
# ─────────────────────────────────────────────

def _save(svg: str, slug: str, dalle_prompt: str, label: str) -> Path:
    ts   = datetime.now().strftime("%Y%m%d_%H%M")
    path = DESIGNS_DIR / f"{slug}_{ts}.svg"
    path.write_text(svg, encoding="utf-8")

    prompts_file = DESIGNS_DIR / "image_prompts.txt"
    with open(prompts_file, "a", encoding="utf-8") as f:
        f.write(f"\n{'─'*60}\n[{label}] {path.name}\n")
        f.write(f"DALL-E / Midjourney:\n{dalle_prompt}\n")

    return path


# ─────────────────────────────────────────────
# Text extractor (Claude)
# ─────────────────────────────────────────────

def _extract_design_text(content: str, platform: str) -> dict:
    """Ask Claude to extract headline/subline/topic_tag/dalle_prompt from content."""
    platform_hints = {
        "linkedin": "זהו פוסט LinkedIn. חלץ את המסר המרכזי.",
        "blog":     "זהו מאמר בלוג. חלץ כותרת + תת-כותרת.",
        "podcast":  "זהו סקריפט פודקאסט. חלץ שם פרק + תיאור.",
    }
    hint = platform_hints.get(platform, "")

    prompt = f"""{hint}

מהתוכן הבא, חלץ:
- headline: כותרת ראשית קצרה וחדה (עד 52 תווים, עברית)
- subline: תת-כותרת מרחיבה (עד 72 תווים, עברית)
- topic_tag: מילה-שתיים שמתארת את הנושא (עד 26 תווים, עברית, לדוגמה: שייכות / חוסן / מנהיגות)
- dalle_prompt: תיאור סצנה באנגלית לתמונה ב-DALL-E (ללא טקסט בתמונה, אווירה חינוכית ישראלית, תאורה קולנועית, גוונים כחולים עמוקים)

התוכן:
{content[:3000]}

החזר JSON בלבד עם ארבעת השדות."""

    return ask_claude_json(prompt, max_budget=0.3)


# ─────────────────────────────────────────────
# Per-platform designers
# ─────────────────────────────────────────────

def _design_linkedin(content_path: Path) -> Path:
    print("  [Agent4] מעצב תמונת LinkedIn...")
    content = content_path.read_text(encoding="utf-8", errors="replace")
    data = _extract_design_text(content, "linkedin")

    headline  = data.get("headline",  "חינוך שמשנה")
    subline   = data.get("subline",   "מחשבות מהשטח")
    topic_tag = data.get("topic_tag", "חינוך")
    dalle     = data.get("dalle_prompt", "Israeli youth educator speaking to a group, cinematic blue tones, no text")

    slug = content_path.stem[:30]
    svg  = _svg_linkedin(headline, subline, topic_tag)
    path = _save(svg, slug + "_li", dalle, "LinkedIn")
    print(f"  [Agent4] LinkedIn → {path.name}")
    return path


def _design_blog(content_path: Path) -> Path:
    print("  [Agent4] מעצב header לבלוג...")
    content = content_path.read_text(encoding="utf-8", errors="replace")
    data = _extract_design_text(content, "blog")

    headline = data.get("headline", "חינוך בלתי פורמלי")
    subline  = data.get("subline",  "מחשבות מהשטח")
    dalle    = data.get("dalle_prompt", "Young people learning together outdoors, Israel, deep blue cinematic light, no text")

    slug = content_path.stem[:30]
    svg  = _svg_blog(headline, subline)
    path = _save(svg, slug + "_blog", dalle, "Blog")
    print(f"  [Agent4] Blog → {path.name}")
    return path


def _design_podcast(content_path: Path, show_name: str, episode_num: int) -> Path:
    print("  [Agent4] מעצב כריכת פודקאסט...")
    content = content_path.read_text(encoding="utf-8", errors="replace")
    data = _extract_design_text(content, "podcast")

    episode_title = data.get("headline", "שיחה על חינוך")
    dalle         = data.get("dalle_prompt", "Microphone in a studio, blue mood, educational context, cinematic, no text")

    slug = content_path.stem[:30]
    svg  = _svg_podcast(show_name, episode_title, episode_num)
    path = _save(svg, slug + "_pod", dalle, "Podcast")
    print(f"  [Agent4] Podcast → {path.name}")
    return path


# ─────────────────────────────────────────────
# Main agent function
# ─────────────────────────────────────────────

def run_designer(
    article_paths: dict[str, Path],
    post_paths: dict[str, list[Path]],
    design_types: list[str],
    topic: str = "",
    show_name: str = "חינוך בלתי פורמלי",
    episode_num: int = 1,
) -> dict[str, Path]:
    """
    article_paths: {"md": Path, ...} — from Agent 2
    post_paths:    {"linkedin": [Path,...], "blog": [Path,...], "podcast": [Path,...]} — from Agent 3
    design_types:  ["linkedin_cover", "blog_banner", "podcast_cover", "quote_card"]
    Returns: {"linkedin": Path(svg), "blog": Path(svg), "podcast": Path(svg)}
    """
    print(f"\n{'='*60}")
    print(f"🎨 Agent 4 — Designer | {', '.join(design_types)}")
    print(f"{'='*60}\n")

    saved = {}

    def _latest(paths: list) -> Path | None:
        valid = [p for p in paths if Path(p).exists()]
        return max(valid, key=lambda p: Path(p).stat().st_mtime) if valid else None

    if "linkedin_cover" in design_types:
        li_paths = post_paths.get("linkedin", [])
        li_file  = _latest(li_paths)
        if li_file:
            saved["linkedin"] = _design_linkedin(Path(li_file))
        else:
            print("  [Agent4] ⚠️  לא נמצא קובץ LinkedIn — מדלג")

    if "blog_banner" in design_types:
        blog_paths = post_paths.get("blog", [])
        blog_file  = _latest(blog_paths)
        if blog_file:
            saved["blog"] = _design_blog(Path(blog_file))
        else:
            print("  [Agent4] ⚠️  לא נמצא קובץ בלוג — מדלג")

    if "podcast_cover" in design_types:
        pod_paths = post_paths.get("podcast", [])
        pod_file  = _latest(pod_paths)
        if pod_file:
            saved["podcast"] = _design_podcast(Path(pod_file), show_name, episode_num)
        else:
            print("  [Agent4] ⚠️  לא נמצא קובץ פודקאסט — מדלג")

    print(f"\n✅ Agent 4 complete → {list(saved.keys())}\n")
    return saved


# ─────────────────────────────────────────────
# Standalone CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    from config import POSTS_DIR

    args = sys.argv[1:]

    # Auto-detect latest posts
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
    valid_types = {"linkedin_cover", "blog_banner", "podcast_cover", "quote_card"}
    types = [t for t in types if t in valid_types]
    if not types:
        types = ["linkedin_cover", "blog_banner", "podcast_cover"]

    results = run_designer(
        article_paths={},
        post_paths=post_paths,
        design_types=types,
    )
    for platform, path in results.items():
        print(f"  {platform}: {path}")
