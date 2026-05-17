"""
Agent 6 — Video Creator (fal.ai)

Generates short videos (5-10s) for LinkedIn / blog posts using fal.ai's video
models — Veo, Kling, Seedance, Hailuo.

Flow:
  1. Read a post (LinkedIn / blog text)
  2. Ask Claude to write a cinematic prompt that fits the post's mood
  3. Submit to fal.ai (async queue), poll until done
  4. Download MP4 to output/videos/
  5. Update Obsidian wikilink in the source post

Cost: ~$0.20-0.80 per video depending on model + duration.
Counts against daily budget cap.

Requires:
  - FAL_KEY in .env (get from https://fal.ai/dashboard/keys)
  - requests library (already a dependency)

Usage:
  python3 agent6_video_creator.py output/posts/linkedin/<post>.txt
  python3 agent6_video_creator.py --auto-latest linkedin
  python3 agent6_video_creator.py --skip-on-missing-key  # graceful skip
"""

import os
import re
import sys
import time
import json
import requests
from pathlib import Path
from datetime import datetime

from config import OUTPUT_DIR

try:
    from claude_cli import ask_claude_json
except Exception:
    ask_claude_json = None


# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────

VIDEOS_DIR = OUTPUT_DIR / "videos"
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

FAL_QUEUE_BASE = "https://queue.fal.run"
FAL_KEY_ENV = "FAL_KEY"

# Model registry: fal.ai endpoint → (duration_s, est_cost_usd, label)
VIDEO_MODELS = {
    "seedance_lite":  ("fal-ai/seedance/v1/lite/text-to-video",  5, 0.20, "Seedance Lite (fast, cheap)"),
    "kling_25":       ("fal-ai/kling-video/v2.5-turbo/text-to-video", 5, 0.35, "Kling 2.5 Turbo"),
    "hailuo":         ("fal-ai/minimax/hailuo-02/standard/text-to-video", 6, 0.40, "Hailuo 02"),
    "veo3":           ("fal-ai/veo3",                                    8, 0.80, "Google Veo 3 (premium)"),
}

DEFAULT_MODEL = "seedance_lite"
HARD_TIMEOUT_S = 300  # 5 min max per video
POLL_INTERVAL_S = 5


# ─────────────────────────────────────────────
# Prompt generation — Claude writes the cinematic prompt
# ─────────────────────────────────────────────

PROMPT_TEMPLATE = """Read this Hebrew post and write a SHORT cinematic video prompt (in English) that captures its mood.

Post:
{post_text}

Rules for the video prompt:
- 1-2 sentences only (max 40 words)
- Visual, concrete, cinematic — NOT abstract
- Specify camera motion if relevant (slow zoom, handheld, tracking shot)
- Specify lighting (golden hour, soft window light, harsh fluorescent)
- NO text-on-screen — pure visual
- Match the post's emotional register (intimate / urgent / contemplative)
- For education topics: focus on hands, faces (no full identifiable shots), spaces (classrooms, dorms, paths)

Return JSON:
{{
  "video_prompt": "<the cinematic prompt>",
  "mood": "<one word: intimate|tense|contemplative|hopeful|stark>",
  "reasoning": "<one sentence — why this fits the post>"
}}

JSON only."""


def generate_video_prompt(post_text: str) -> dict | None:
    """Use Claude to convert a post into a cinematic video prompt."""
    if ask_claude_json is None:
        return None
    snippet = post_text.strip()[:1500]
    try:
        result = ask_claude_json(
            PROMPT_TEMPLATE.format(post_text=snippet),
            max_budget=0.10,
            timeout=60,
        )
        if isinstance(result, dict) and result.get("video_prompt"):
            return result
    except Exception as e:
        print(f"  [Agent6] Prompt gen failed: {e}")
    return None


# ─────────────────────────────────────────────
# fal.ai client
# ─────────────────────────────────────────────

class FalError(Exception):
    pass


def _fal_headers() -> dict:
    key = os.environ.get(FAL_KEY_ENV)
    if not key:
        # Try .env file
        env_path = Path(__file__).parent / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith(f"{FAL_KEY_ENV}="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not key:
        raise FalError(f"No {FAL_KEY_ENV} found. Get one at https://fal.ai/dashboard/keys")
    return {
        "Authorization": f"Key {key}",
        "Content-Type": "application/json",
    }


def fal_submit(model_endpoint: str, prompt: str, duration: int = 5,
               aspect_ratio: str = "9:16") -> str:
    """Submit a video job. Returns request_id."""
    url = f"{FAL_QUEUE_BASE}/{model_endpoint}"
    payload = {
        "prompt": prompt,
        "duration": duration,
        "aspect_ratio": aspect_ratio,  # 9:16 = vertical (LinkedIn), 16:9 = horizontal
    }
    r = requests.post(url, json=payload, headers=_fal_headers(), timeout=30)
    if r.status_code not in (200, 202):
        raise FalError(f"fal submit {r.status_code}: {r.text[:200]}")
    return r.json().get("request_id") or r.json().get("response_url", "").rsplit("/", 1)[-1]


def fal_poll(model_endpoint: str, request_id: str, max_wait_s: int = HARD_TIMEOUT_S) -> dict:
    """Poll until done or timeout. Returns final response dict."""
    status_url = f"{FAL_QUEUE_BASE}/{model_endpoint}/requests/{request_id}/status"
    result_url = f"{FAL_QUEUE_BASE}/{model_endpoint}/requests/{request_id}"
    deadline = time.time() + max_wait_s

    while time.time() < deadline:
        try:
            r = requests.get(status_url, headers=_fal_headers(), timeout=15)
            if r.status_code == 200:
                status = r.json().get("status", "")
                if status == "COMPLETED":
                    rr = requests.get(result_url, headers=_fal_headers(), timeout=30)
                    rr.raise_for_status()
                    return rr.json()
                if status in ("ERROR", "FAILED"):
                    raise FalError(f"job {request_id} failed: {r.json()}")
                print(f"     ⏳ status: {status}")
        except requests.RequestException as e:
            print(f"     ⚠️ poll error: {e}")
        time.sleep(POLL_INTERVAL_S)

    raise FalError(f"Timeout after {max_wait_s}s polling {request_id}")


def fal_download(video_url: str, dest: Path) -> Path:
    """Download MP4 to disk."""
    r = requests.get(video_url, stream=True, timeout=120)
    r.raise_for_status()
    with dest.open("wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            f.write(chunk)
    return dest


# ─────────────────────────────────────────────
# Budget guard
# ─────────────────────────────────────────────

def _budget_allows(est_cost: float) -> bool:
    """Check if this video fits within daily budget cap. Does NOT record spend."""
    try:
        from claude_cli import daily_budget_status
        status = daily_budget_status()
        if status["spent_usd"] + est_cost > status["cap_usd"]:
            print(f"  [Agent6] ❌ Skipping — would exceed daily cap "
                  f"(${status['spent_usd']:.2f} + ${est_cost} > ${status['cap_usd']})")
            return False
        return True
    except Exception:
        return True  # Fail open if budget tracking unavailable


def _record_spend(cost: float):
    """Record actual spend AFTER a video is successfully produced."""
    try:
        from claude_cli import _limiter
        _limiter._record_daily(cost)
    except Exception:
        pass


# ─────────────────────────────────────────────
# Main video creation
# ─────────────────────────────────────────────

def create_video_for_post(post_path: Path, model_key: str = DEFAULT_MODEL,
                          aspect: str = "9:16") -> dict:
    """End-to-end: read post → generate prompt → submit → poll → download."""
    if model_key not in VIDEO_MODELS:
        raise ValueError(f"Unknown model: {model_key}. Options: {list(VIDEO_MODELS.keys())}")
    endpoint, duration, cost, label = VIDEO_MODELS[model_key]

    print(f"\n  🎬 Agent 6 — Video Creator")
    print(f"     Post: {post_path.name}")
    print(f"     Model: {label}")
    print(f"     Aspect: {aspect}, duration: {duration}s, est: ${cost}")

    if not post_path.exists():
        return {"status": "error", "error": f"post not found: {post_path}"}

    # Budget check — only CHECKS here, spend recorded after success
    if not _budget_allows(cost):
        return {"status": "skipped_budget"}

    # 1. Read post + generate prompt
    text = post_path.read_text(encoding="utf-8", errors="replace")
    print(f"  [Agent6] Generating cinematic prompt from post...")
    prompt_data = generate_video_prompt(text)
    if not prompt_data:
        return {"status": "error", "error": "could not generate video prompt"}

    print(f"  [Agent6] Prompt: {prompt_data['video_prompt'][:120]}")
    print(f"     Mood: {prompt_data.get('mood', '?')}")

    # 2. Submit + poll
    try:
        print(f"  [Agent6] Submitting to fal.ai...")
        request_id = fal_submit(endpoint, prompt_data["video_prompt"],
                                duration=duration, aspect_ratio=aspect)
        print(f"  [Agent6] Job: {request_id} — polling...")
        result = fal_poll(endpoint, request_id)
    except FalError as e:
        return {"status": "error", "error": str(e), "prompt": prompt_data["video_prompt"]}

    # 3. Extract video URL from response
    video_url = None
    if isinstance(result.get("video"), dict):
        video_url = result["video"].get("url")
    elif isinstance(result.get("video_url"), str):
        video_url = result["video_url"]
    elif isinstance(result.get("output"), str):
        video_url = result["output"]
    if not video_url:
        return {"status": "error", "error": f"no video URL in response: {result}"}

    # 4. Download
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = VIDEOS_DIR / f"{post_path.stem}_{model_key}_{stamp}.mp4"
    print(f"  [Agent6] Downloading → {dest.name}")
    try:
        fal_download(video_url, dest)
    except Exception as e:
        return {"status": "error", "error": f"download failed: {e}"}

    # Video succeeded — NOW record the spend against daily budget
    _record_spend(cost)

    # 5. Add wikilink to source post (Obsidian-friendly)
    try:
        text_with_link = text
        link_line = f"\n\n🎬 **Video:** [[{dest.stem}]]\n"
        if "🎬 **Video:**" not in text_with_link:
            text_with_link += link_line
            post_path.write_text(text_with_link, encoding="utf-8")
    except Exception:
        pass

    return {
        "status": "ok",
        "video_path": str(dest),
        "prompt": prompt_data["video_prompt"],
        "mood": prompt_data.get("mood"),
        "model": label,
        "cost_usd": cost,
    }


# ─────────────────────────────────────────────
# Latest-post helpers
# ─────────────────────────────────────────────

def _latest_post(platform: str) -> Path | None:
    d = {"linkedin": OUTPUT_DIR / "posts" / "linkedin",
         "blog":     OUTPUT_DIR / "posts" / "blog"}.get(platform)
    if not d or not d.exists():
        return None
    candidates = []
    for ext in (".md", ".txt"):
        candidates.extend(p for p in d.glob(f"*{ext}")
                          if not p.name.startswith("_") and not p.name.endswith(".bak"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args or "-h" in args or "--help" in args:
        print(__doc__)
        return

    skip_on_missing = "--skip-on-missing-key" in args
    args = [a for a in args if a != "--skip-on-missing-key"]

    # Pick model
    model = DEFAULT_MODEL
    for a in list(args):
        if a.startswith("--model="):
            model = a.split("=", 1)[1]
            args.remove(a)

    # Resolve post path
    post_path = None
    if "--auto-latest" in args:
        idx = args.index("--auto-latest")
        platform = args[idx + 1] if idx + 1 < len(args) else "linkedin"
        post_path = _latest_post(platform)
        if not post_path:
            print(f"❌ No latest post found in {platform}")
            return
    elif args:
        post_path = Path(args[0])

    if not post_path:
        print("Usage: python3 agent6_video_creator.py <post-file> [--model=seedance_lite]")
        print("   or: python3 agent6_video_creator.py --auto-latest linkedin")
        return

    try:
        result = create_video_for_post(post_path, model_key=model)
    except FalError as e:
        if skip_on_missing and "No FAL_KEY" in str(e):
            print(f"  [Agent6] ⏭  Skipped — {e}")
            return
        print(f"  [Agent6] ❌ {e}")
        return

    print()
    print(f"  Status: {result['status']}")
    if result["status"] == "ok":
        print(f"  ✅ Video: {result['video_path']}")
        print(f"  Cost: ${result['cost_usd']}")


if __name__ == "__main__":
    main()
