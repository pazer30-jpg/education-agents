"""
publish_packager.py — Bundle final content into a paste-ready package.

True auto-publishing to LinkedIn needs a LinkedIn API app (OAuth, review —
weeks of approval). This is the realistic alternative: take the finished
content + its media + format it so publishing is one copy-paste.

For each ready post, builds output/_publish/<stamp>/:
  - linkedin.txt   — clean text, no markdown, ready to paste into LinkedIn
  - blog.md        — blog post with frontmatter for any CMS
  - assets/        — copies of the cover image / video / audio
  - PUBLISH.md     — checklist: where to post, what media to attach, hashtags

Usage:
  python3 publish_packager.py            # package all ready posts
  python3 publish_packager.py --latest   # just the most recent of each type
"""

import re
import sys
import shutil
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR


PUBLISH_DIR = OUTPUT_DIR / "_publish"
POSTS = {
    "linkedin": OUTPUT_DIR / "posts" / "linkedin",
    "blog":     OUTPUT_DIR / "posts" / "blog",
    "podcast":  OUTPUT_DIR / "posts" / "podcast",
}
MEDIA_DIRS = {
    "videos": OUTPUT_DIR / "videos",
    "images": OUTPUT_DIR / "images",
    "audio":  OUTPUT_DIR / "podcasts_audio",
}


# ─────────────────────────────────────────────
# Content cleaning
# ─────────────────────────────────────────────

def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:]
    return text


def _linkedin_clean(text: str) -> str:
    """LinkedIn doesn't render markdown — flatten to clean text."""
    t = _strip_frontmatter(text)
    # Remove the video/audio wikilink lines we injected
    t = re.sub(r"\n*🎬 \*\*Video:\*\*.*", "", t)
    t = re.sub(r"\n*🎙️ \*\*Audio:\*\*.*", "", t)
    # Headers → plain
    t = re.sub(r"^#+\s*", "", t, flags=re.MULTILINE)
    # Bold/italic markers → gone (LinkedIn shows them literally)
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", t)
    # Markdown links → just the text
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    # Collapse 3+ blank lines
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _extract_hashtags(text: str) -> list[str]:
    """Pull existing hashtags, or suggest topic-based ones."""
    tags = re.findall(r"#[\wא-ת]+", text)
    return list(dict.fromkeys(tags))[:8]


def _find_media(stem: str) -> dict:
    """Find media files whose name starts with the post stem."""
    found = {}
    for kind, d in MEDIA_DIRS.items():
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.is_file() and f.stem.startswith(stem[:30]):
                found.setdefault(kind, []).append(f)
    return found


# ─────────────────────────────────────────────
# Package one post
# ─────────────────────────────────────────────

def _latest(platform: str) -> Path | None:
    d = POSTS.get(platform)
    if not d or not d.exists():
        return None
    cands = [p for p in d.iterdir()
             if p.is_file() and p.suffix in (".md", ".txt")
             and not p.name.startswith("_") and not p.name.endswith(".bak")]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def package(latest_only: bool = False) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    work = PUBLISH_DIR / stamp
    assets = work / "assets"
    work.mkdir(parents=True, exist_ok=True)

    packaged = []
    checklist = [
        f"# 📤 Publish Package — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
        "_Paste-ready content. Each platform has clean text + media to attach._",
        "",
    ]

    for platform in ("linkedin", "blog", "podcast"):
        post = _latest(platform)
        if not post:
            continue

        try:
            raw = post.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        media = _find_media(post.stem)

        if platform == "linkedin":
            clean = _linkedin_clean(raw)
            (work / "linkedin.txt").write_text(clean, encoding="utf-8")
            hashtags = _extract_hashtags(raw)
            checklist.extend([
                "## 🔗 LinkedIn",
                "",
                "1. Open `linkedin.txt` — copy the whole thing",
                "2. New LinkedIn post → paste",
                f"3. Attach media: {', '.join(f.name for fs in media.values() for f in fs) or '(none found)'}",
                f"4. Hashtags: {' '.join(hashtags) if hashtags else '(add 3-5 relevant)'}",
                f"5. Char count: {len(clean)} (LinkedIn limit ~3000)",
                "",
            ])
            packaged.append("linkedin")

        elif platform == "blog":
            shutil.copy2(post, work / "blog.md")
            checklist.extend([
                "## 📝 Blog",
                "",
                "1. `blog.md` — has frontmatter, ready for most CMS",
                f"2. Cover image: {', '.join(f.name for f in media.get('images', [])) or '(none — run agent6 --image)'}",
                "3. Review the title + meta description before publishing",
                "",
            ])
            packaged.append("blog")

        elif platform == "podcast":
            shutil.copy2(post, work / "podcast_script.md")
            audio = media.get("audio", [])
            checklist.extend([
                "## 🎙️ Podcast",
                "",
                "1. `podcast_script.md` — the script",
                f"2. Audio: {', '.join(f.name for f in audio) if audio else '(none — run podcast_audio.py)'}",
                "3. Upload audio to your podcast host (Spotify/Anchor/etc.)",
                "",
            ])
            packaged.append("podcast")

        # Copy media into assets/
        for fs in media.values():
            for f in fs:
                try:
                    assets.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, assets / f.name)
                except Exception:
                    pass

    checklist.extend([
        "---",
        "",
        "## ⚠️ Note on auto-publishing",
        "",
        "True one-click publishing to LinkedIn needs a LinkedIn API app",
        "(OAuth + Marketing Developer Platform approval — weeks of review).",
        "This package is the realistic alternative: everything formatted,",
        "media gathered, one copy-paste per platform.",
    ])

    (work / "PUBLISH.md").write_text("\n".join(checklist), encoding="utf-8")
    return work, packaged


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    latest_only = "--latest" in sys.argv
    work, packaged = package(latest_only=latest_only)

    print(f"\n📤 Publish package ready → {work}")
    if packaged:
        print(f"   Platforms: {', '.join(packaged)}")
        print(f"   Open: {work / 'PUBLISH.md'}")
    else:
        print(f"   ⚠️ No ready posts found in output/posts/")


if __name__ == "__main__":
    main()
