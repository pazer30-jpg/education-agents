"""
podcast_audio.py — Turn a podcast script into actual audio.

The podcast agent (Agent 3) produces a SCRIPT — text only. This module
converts it to a real MP3 via fal.ai's ElevenLabs multilingual TTS,
which handles Hebrew.

Flow:
  1. Read a podcast script (.md)
  2. Strip stage directions ([הפסקה], headers, markdown)
  3. Chunk long scripts (TTS has per-call length limits)
  4. Submit each chunk to fal.ai, download, concatenate
  5. Save MP3 to output/podcasts_audio/

Requires:
  - FAL_KEY in .env
  - Optional: ffmpeg for chunk concatenation (falls back to first chunk only)

Usage:
  python3 podcast_audio.py output/posts/podcast/<script>.md
  python3 podcast_audio.py --auto-latest
"""

import os
import re
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR

# Reuse fal.ai plumbing from Agent 6
try:
    from agent6_video_creator import (
        _fal_headers, fal_poll, fal_download, FalError,
        FAL_QUEUE_BASE, _budget_allows, _record_spend,
    )
    import requests
    HAS_FAL = True
except Exception:
    HAS_FAL = False


AUDIO_DIR = OUTPUT_DIR / "podcasts_audio"

# fal.ai TTS endpoint — ElevenLabs multilingual handles Hebrew
TTS_ENDPOINT = "fal-ai/elevenlabs/tts/multilingual-v2"
TTS_COST_PER_1K_CHARS = 0.03   # rough estimate
CHUNK_CHARS = 2500             # per-call character ceiling


# ─────────────────────────────────────────────
# Script cleaning
# ─────────────────────────────────────────────

def _clean_script(text: str) -> str:
    """Strip frontmatter, markdown, stage directions — keep spoken words only."""
    # Frontmatter
    if text.startswith("---"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5:]

    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Skip markdown headers
        if line.startswith("#"):
            continue
        # Skip pure stage directions like [הפסקה] or [מוזיקה]
        if re.fullmatch(r"\[[^\]]+\]", line):
            continue
        # Remove inline stage directions
        line = re.sub(r"\[[^\]]{1,40}\]", "", line)
        # Remove markdown emphasis
        line = re.sub(r"[*_`>]+", "", line)
        line = line.strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _chunk_text(text: str, max_chars: int = CHUNK_CHARS) -> list[str]:
    """Split into chunks at sentence boundaries, under max_chars each."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, current = [], ""
    for s in sentences:
        if len(current) + len(s) + 1 > max_chars and current:
            chunks.append(current.strip())
            current = s
        else:
            current = f"{current} {s}".strip()
    if current.strip():
        chunks.append(current.strip())
    return chunks


# ─────────────────────────────────────────────
# TTS via fal.ai
# ─────────────────────────────────────────────

def _tts_chunk(text: str, voice: str = "Rachel") -> str | None:
    """Submit one chunk to fal.ai TTS, return audio URL."""
    url = f"{FAL_QUEUE_BASE}/{TTS_ENDPOINT}"
    payload = {"text": text, "voice": voice}
    try:
        r = requests.post(url, json=payload, headers=_fal_headers(), timeout=30)
        if r.status_code not in (200, 202):
            print(f"  [TTS] submit {r.status_code}: {r.text[:120]}")
            return None
        request_id = r.json().get("request_id") or \
            r.json().get("response_url", "").rsplit("/", 1)[-1]
        result = fal_poll(TTS_ENDPOINT, request_id, max_wait_s=180)
    except (FalError, requests.RequestException) as e:
        print(f"  [TTS] error: {e}")
        return None

    if isinstance(result.get("audio"), dict):
        return result["audio"].get("url")
    if isinstance(result.get("audio_url"), str):
        return result["audio_url"]
    return None


def _concat_audio(chunk_files: list[Path], dest: Path) -> bool:
    """Concatenate MP3 chunks with ffmpeg. Returns success."""
    if len(chunk_files) == 1:
        chunk_files[0].rename(dest)
        return True
    # Build ffmpeg concat list
    list_file = dest.with_suffix(".txt")
    list_file.write_text(
        "\n".join(f"file '{f}'" for f in chunk_files), encoding="utf-8")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(list_file), "-c", "copy", str(dest)],
            capture_output=True, timeout=120, check=True,
        )
        list_file.unlink(missing_ok=True)
        for f in chunk_files:
            f.unlink(missing_ok=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"  [TTS] ⚠️ ffmpeg concat failed ({e}) — keeping first chunk only")
        if chunk_files:
            chunk_files[0].rename(dest)
        return False


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def create_audio(script_path: Path, voice: str = "Rachel") -> dict:
    """Convert a podcast script to audio."""
    if not HAS_FAL:
        return {"status": "error", "error": "fal.ai plumbing unavailable (agent6 import failed)"}
    if not script_path.exists():
        return {"status": "error", "error": f"script not found: {script_path}"}

    print(f"\n  🎙️  Podcast Audio — {script_path.name}")
    raw = script_path.read_text(encoding="utf-8", errors="replace")
    spoken = _clean_script(raw)
    if len(spoken) < 100:
        return {"status": "error", "error": "script too short after cleaning"}

    est_cost = round(len(spoken) / 1000 * TTS_COST_PER_1K_CHARS, 2)
    print(f"  [TTS] {len(spoken):,} chars, est ${est_cost}")
    if not _budget_allows(est_cost):
        return {"status": "skipped_budget"}

    chunks = _chunk_text(spoken)
    print(f"  [TTS] {len(chunks)} chunks")

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    chunk_files = []
    for i, chunk in enumerate(chunks, 1):
        print(f"  [TTS] chunk {i}/{len(chunks)} ({len(chunk)} chars)...")
        audio_url = _tts_chunk(chunk, voice=voice)
        if not audio_url:
            return {"status": "error", "error": f"TTS failed on chunk {i}"}
        cf = AUDIO_DIR / f"{script_path.stem}_{stamp}_part{i}.mp3"
        try:
            fal_download(audio_url, cf)
            chunk_files.append(cf)
        except Exception as e:
            return {"status": "error", "error": f"download chunk {i}: {e}"}

    dest = AUDIO_DIR / f"{script_path.stem}_{stamp}.mp3"
    concat_ok = _concat_audio(chunk_files, dest)
    _record_spend(est_cost)

    # Link audio into the script (Obsidian)
    try:
        if "🎙️ **Audio:**" not in raw:
            script_path.write_text(
                raw + f"\n\n🎙️ **Audio:** [[{dest.stem}]]\n", encoding="utf-8")
    except Exception:
        pass

    return {
        "status": "ok",
        "audio_path": str(dest),
        "chunks": len(chunks),
        "concatenated": concat_ok,
        "cost_usd": est_cost,
    }


def _latest_script() -> Path | None:
    d = OUTPUT_DIR / "posts" / "podcast"
    if not d.exists():
        return None
    scripts = [p for p in d.glob("*.md")
               if not p.name.startswith("_") and not p.name.endswith(".bak")]
    return max(scripts, key=lambda p: p.stat().st_mtime) if scripts else None


def main():
    args = sys.argv[1:]
    if "-h" in args or "--help" in args:
        print(__doc__)
        return

    voice = "Rachel"
    for a in list(args):
        if a.startswith("--voice="):
            voice = a.split("=", 1)[1]
            args.remove(a)

    if "--auto-latest" in args:
        script = _latest_script()
        if not script:
            print("❌ No podcast script found")
            return
    elif args:
        script = Path(args[0])
    else:
        print("Usage: python3 podcast_audio.py <script.md> [--voice=Rachel]")
        print("   or: python3 podcast_audio.py --auto-latest")
        return

    try:
        result = create_audio(script, voice=voice)
    except FalError as e:
        print(f"  ❌ {e}")
        return

    print(f"\n  Status: {result['status']}")
    if result["status"] == "ok":
        print(f"  ✅ Audio: {result['audio_path']}")
        print(f"  Chunks: {result['chunks']} · Cost: ${result['cost_usd']}")
        if not result["concatenated"]:
            print(f"  ⚠️ ffmpeg missing — only first chunk saved. Install: brew install ffmpeg")


if __name__ == "__main__":
    main()
