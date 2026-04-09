import os
from pathlib import Path

# ─────────────────────────────────────────────
# Claude — claude_cli.py handles binary discovery + API fallback
# ─────────────────────────────────────────────
MODEL = "claude-sonnet-4-6"  # for compatibility with older code

# ─────────────────────────────────────────────
# Semantic Scholar
# ─────────────────────────────────────────────
SEMANTIC_SCHOLAR_BASE    = "https://api.semanticscholar.org/graph/v1"
SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")  # optional

# ─────────────────────────────────────────────
# Output directories — absolute paths
# ─────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent.resolve()
OUTPUT_DIR   = BASE_DIR / "output"
PAPERS_DIR   = OUTPUT_DIR / "papers"
ARTICLES_DIR = OUTPUT_DIR / "articles"
POSTS_DIR    = OUTPUT_DIR / "posts"
LINKEDIN_DIR = OUTPUT_DIR / "posts" / "linkedin"
BLOG_DIR     = OUTPUT_DIR / "posts" / "blog"
PODCAST_DIR  = OUTPUT_DIR / "posts" / "podcast"

# Create all dirs on import
for _d in [PAPERS_DIR, ARTICLES_DIR, LINKEDIN_DIR, BLOG_DIR, PODCAST_DIR]:
    _d.mkdir(parents=True, exist_ok=True)
