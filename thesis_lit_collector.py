"""
thesis_lit_collector.py — Massive lit collection for thesis-level work.

לא 12-15 — מטרה 300+ מקורות ייחודיים. מדלג על Claude curation בשלב האיסוף.

Pipeline:
  1. Generate 25-30 query variations (English + Hebrew) via Claude — one cheap call
  2. Run each query × 7 sources (Semantic Scholar, OpenAlex, Crossref, ERIC, CORE, DOAJ, PubMed)
  3. Dedupe by DOI / normalized title
  4. Score by relevance (keyword overlap with topic) × citations
  5. Save top 300 to output/thesis/<stamp>/papers_full.json + lit_review_skeleton.md

Usage:
  python3 thesis_lit_collector.py "בדידות של מנהל פנימייה" \\
      --target 300 --en "loneliness boarding school principal"
"""

import sys
import json
import re
import argparse
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

from config import OUTPUT_DIR
from claude_cli import ask_claude_json
from agent1_researcher import (
    search_semantic_scholar,
    search_openalex,
    search_crossref,
    search_eric,
    search_core,
    search_doaj,
    search_pubmed,
    _clean_ss_papers,
    _clean_openalex_papers,
    _clean_crossref_papers,
    _clean_eric_papers,
    _clean_core_papers,
    _clean_doaj_papers,
    _clean_pubmed_papers,
)

try:
    from hebrew_lemma import lemmatize as _he_lemmatize
except Exception:
    def _he_lemmatize(w: str) -> str:
        return w


THESIS_DIR = OUTPUT_DIR / "thesis"


# ─────────────────────────────────────────────
# Query expansion
# ─────────────────────────────────────────────

def expand_queries(topic_he: str, topic_en: str, n_en: int = 22, n_he: int = 8) -> list[str]:
    """Generate diverse query variations across languages and conceptual angles."""
    prompt = f"""Topic for academic thesis lit search:
Hebrew: {topic_he}
English: {topic_en}

Generate {n_en + n_he} diverse search queries to find papers across many angles:
- {n_en} in English
- {n_he} in Hebrew

Cover these angles:
1. Direct topic terms
2. Synonyms and alternative phrasings
3. Related theoretical frameworks (e.g. for loneliness: social isolation, alienation, perceived isolation)
4. Population variations (principals, headteachers, school leaders, residential care directors)
5. Institutional context (boarding school, residential, youth village, kfar noar, kibbutz school)
6. Adjacent constructs (burnout, support, mental health, psychological wellbeing, leadership stress)
7. Methodological angles (qualitative phenomenology, mixed methods)
8. Specific Hebrew academic phrasing (e.g. "בדידות במנהיגות חינוכית", "מנהלי פנימיות בישראל")

Each query: 3-7 words, search-engine-ready (no quotes, no boolean operators).
NO duplicates, NO trivial variations.

Return JSON: {{"queries_en": [...], "queries_he": [...]}}"""

    try:
        result = ask_claude_json(prompt, max_budget=0.4)
        en = result.get("queries_en", []) or []
        he = result.get("queries_he", []) or []
        all_q = [q.strip() for q in (en + he) if isinstance(q, str) and q.strip()]
        seen = set()
        out = []
        for q in all_q:
            key = q.lower()
            if key not in seen:
                seen.add(key)
                out.append(q)
        # Truncate to requested total — Claude often returns more than asked
        max_total = n_en + n_he
        return out[:max_total]
    except Exception as e:
        print(f"  ⚠️ Query expansion failed: {e} — fallback")
        return [topic_en, topic_he]


# ─────────────────────────────────────────────
# Dedupe
# ─────────────────────────────────────────────

def _coerce_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return " ".join(str(x) for x in v if x is not None)
    return str(v)


def _norm_title(t) -> str:
    s = _coerce_str(t)
    if not s:
        return ""
    s = re.sub(r"[^\w\s]", " ", s.lower())
    s = re.sub(r"\s+", " ", s).strip()
    return s[:100]


def _paper_key(p: dict) -> str:
    doi = _coerce_str(p.get("doi")).lower().strip()
    if doi:
        return f"doi:{doi}"
    url = _coerce_str(p.get("url") or p.get("pdf_url")).lower()
    m = re.search(r"10\.\d{4,9}/\S+", url)
    if m:
        return f"doi:{m.group(0).rstrip('/').rstrip('.')}"
    return f"title:{_norm_title(p.get('title', ''))}"


def dedupe(papers: list[dict]) -> list[dict]:
    by_key = {}
    for p in papers:
        k = _paper_key(p)
        if not k or k == "title:":
            continue
        if k in by_key:
            existing = by_key[k]
            # Keep version with more info
            if (p.get("citation_count") or 0) > (existing.get("citation_count") or 0):
                by_key[k] = p
            elif (p.get("abstract") or "") and not (existing.get("abstract") or ""):
                by_key[k] = p
        else:
            by_key[k] = p
    return list(by_key.values())


# ─────────────────────────────────────────────
# Relevance scoring (cheap — no Claude)
# ─────────────────────────────────────────────

def _tokenize(text: str) -> set[str]:
    if not text:
        return set()
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return {w for w in text.split() if len(w) >= 4}


_HEBREW_RE = re.compile(r"[\u0590-\u05FF]+")


def _he_lemma_set(words) -> set[str]:
    """Lemmatize each Hebrew word; keep stems only."""
    out = set()
    for w in words:
        try:
            out.add(_he_lemmatize(w))
        except Exception:
            out.add(w)
    return {w for w in out if len(w) >= 2}


def relevance_score(paper: dict, topic_terms_en: set[str], topic_terms_he: set[str]) -> float:
    title = _coerce_str(paper.get("title"))
    abstract = _coerce_str(paper.get("abstract"))
    text = f"{title} {abstract}"
    en_tokens = _tokenize(text)
    he_text = re.sub(r"[^\u0590-\u05FF\s]", " ", text)
    he_raw = {w for w in he_text.split() if len(w) >= 3}
    he_tokens = _he_lemma_set(he_raw)

    en_overlap = len(topic_terms_en & en_tokens) / max(len(topic_terms_en), 1)
    he_overlap = len(topic_terms_he & he_tokens) / max(len(topic_terms_he), 1) if topic_terms_he else 0

    base = (en_overlap * 0.7) + (he_overlap * 0.3 if topic_terms_he else en_overlap * 0.3)

    # Title match bonus
    title_tokens = _tokenize(title)
    title_overlap = len(topic_terms_en & title_tokens)
    if title_overlap >= 2:
        base += 0.2

    # Citation log scale
    cites = paper.get("citation_count") or 0
    if cites > 0:
        import math
        base += min(math.log10(cites + 1) / 10, 0.15)

    # Abstract presence
    if abstract:
        base += 0.05

    return round(base, 3)


# ─────────────────────────────────────────────
# Bulk collection
# ─────────────────────────────────────────────

def collect_for_query(query: str, is_hebrew: bool = False) -> tuple[str, list[dict]]:
    """Run a single query across all sources. Returns (query, papers)."""
    papers = []

    sources = [
        ("ss",   lambda: _clean_ss_papers(search_semantic_scholar(query, limit=30, retry=1))),
        ("oa",   lambda: _clean_openalex_papers(search_openalex(query, limit=50, language="he" if is_hebrew else None))),
        ("cr",   lambda: _clean_crossref_papers(search_crossref(query, limit=30))),
        ("er",   lambda: _clean_eric_papers(search_eric(query, limit=20))),
        ("core", lambda: _clean_core_papers(search_core(query, limit=15))),
        ("doaj", lambda: _clean_doaj_papers(search_doaj(query, limit=15, hebrew_only=is_hebrew))),
        ("pm",   lambda: _clean_pubmed_papers(search_pubmed(query, limit=20))),
    ]

    with ThreadPoolExecutor(max_workers=7) as ex:
        futures = {ex.submit(fn): name for name, fn in sources}
        for fut in as_completed(futures, timeout=120):
            try:
                results = fut.result(timeout=60)
                if results:
                    for r in results:
                        papers.append(_normalize_paper(r))
            except Exception:
                pass

    return (query, papers)


def bulk_collect(queries: list[str], target: int = 300) -> tuple[list[dict], dict]:
    """Run all queries in parallel. Stop early if target hit.

    Uses incremental dedupe (running dict of paper keys) — O(n) total
    instead of O(n²) over the loop.
    """
    all_papers = []
    seen_keys = set()
    per_query_stats = {}
    hebrew_pattern = re.compile(r"[\u0590-\u05FF]")

    print(f"\n  📥 Collecting from {len(queries)} queries × 7 sources (parallel)...\n")

    for i, q in enumerate(queries, 1):
        is_he = bool(hebrew_pattern.search(q))
        q, papers = collect_for_query(q, is_hebrew=is_he)
        per_query_stats[q] = len(papers)

        new_count = 0
        for p in papers:
            k = _paper_key(p)
            if not k or k == "title:":
                continue
            if k not in seen_keys:
                seen_keys.add(k)
                all_papers.append(p)
                new_count += 1

        print(f"  [{i:>2}/{len(queries)}] {q[:60]:<60} → +{len(papers):>3} "
              f"(new: {new_count}, unique: {len(seen_keys)})")

        if len(seen_keys) >= target * 1.5:
            print(f"  ✓ Reached {len(seen_keys)} unique — stopping early")
            break

    return all_papers, per_query_stats


# ─────────────────────────────────────────────
# Render outputs
# ─────────────────────────────────────────────

def _author_str(p: dict) -> str:
    a = p.get("authors")
    if isinstance(a, list):
        return ", ".join(str(x) for x in a[:4])[:120]
    return _coerce_str(a)[:120]


def _normalize_paper(p: dict) -> dict:
    """Coerce title/abstract/doi to strings — many APIs return inconsistent types."""
    if not isinstance(p, dict):
        return {}
    p["title"] = _coerce_str(p.get("title"))
    p["abstract"] = _coerce_str(p.get("abstract"))
    p["doi"] = _coerce_str(p.get("doi"))
    p["url"] = _coerce_str(p.get("url"))
    p["pdf_url"] = _coerce_str(p.get("pdf_url"))
    p["venue"] = _coerce_str(p.get("venue"))
    return p


def render_lit_review(papers: list[dict], topic_he: str, work_dir: Path) -> Path:
    """Big lit review skeleton — grouped by relevance tier."""
    parts = [
        f"# סקירת ספרות — {topic_he}",
        "",
        f"_Generated: {datetime.now().strftime('%d/%m/%Y %H:%M')}_",
        f"_Total: {len(papers)} מקורות ייחודיים_",
        "",
        "---",
        "",
    ]

    # Tier by relevance score
    tier1 = [p for p in papers if (p.get("relevance") or 0) >= 0.5]
    tier2 = [p for p in papers if 0.3 <= (p.get("relevance") or 0) < 0.5]
    tier3 = [p for p in papers if (p.get("relevance") or 0) < 0.3]

    parts.extend([
        f"## סיכום",
        "",
        f"- 🌟 רלוונטיות גבוהה (≥ 0.5): **{len(tier1)} מקורות**",
        f"- ✅ רלוונטיות בינונית (0.3-0.5): **{len(tier2)} מקורות**",
        f"- 📚 רלוונטיות נמוכה (< 0.3): **{len(tier3)} מקורות** _(לסקירה מורחבת)_",
        "",
        "_מקורות ממויינים לפי relevance × citations._",
        "",
        "---",
        "",
    ])

    def render_paper(p, idx):
        title = p.get("title", "?")
        authors = _author_str(p)
        year = p.get("year") or "n.d."
        cites = p.get("citation_count") or 0
        rel = p.get("relevance", 0)
        url = p.get("url") or p.get("pdf_url", "")
        source = p.get("source", "?")
        abstract = (p.get("abstract") or "")[:500]
        venue = p.get("venue", "")

        out = [
            f"### {idx}. {title}",
            "",
            f"**{authors}** ({year}) · {cites} citations · rel={rel:.2f} · _{source}_",
        ]
        if venue:
            out.append(f"  ")
            out.append(f"_{venue}_")
        if url:
            out.append("")
            out.append(f"🔗 {url}")
        if abstract:
            out.append("")
            out.append(f"> {abstract}")
        out.extend(["", "---", ""])
        return out

    if tier1:
        parts.extend([f"## 🌟 שכבה 1 — רלוונטיות גבוהה ({len(tier1)})", ""])
        for i, p in enumerate(tier1, 1):
            parts.extend(render_paper(p, i))

    if tier2:
        parts.extend([f"## ✅ שכבה 2 — רלוונטיות בינונית ({len(tier2)})", ""])
        for i, p in enumerate(tier2, 1):
            parts.extend(render_paper(p, i))

    if tier3:
        parts.extend([f"## 📚 שכבה 3 — רלוונטיות נמוכה ({len(tier3)})", ""])
        for i, p in enumerate(tier3, 1):
            parts.extend(render_paper(p, i))

    out_path = work_dir / "lit_review_full.md"
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path


def render_bibliography_csv(papers: list[dict], work_dir: Path) -> Path:
    """CSV for Zotero/Mendeley import."""
    import csv
    out_path = work_dir / "papers_full.csv"
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["title", "authors", "year", "venue", "citations",
                    "relevance", "source", "url", "doi", "abstract"])
        for p in papers:
            w.writerow([
                p.get("title", ""),
                _author_str(p),
                p.get("year", ""),
                p.get("venue", ""),
                p.get("citation_count", 0),
                p.get("relevance", 0),
                p.get("source", ""),
                p.get("url", "") or p.get("pdf_url", ""),
                p.get("doi", ""),
                (p.get("abstract") or "")[:1000],
            ])
    return out_path


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("topic", help="נושא התזה בעברית")
    ap.add_argument("--en", help="English topic for queries")
    ap.add_argument("--target", type=int, default=300, help="Target paper count")
    ap.add_argument("--n-queries", type=int, default=30, help="How many query variations")
    args = ap.parse_args()

    topic_he = args.topic
    if "בדידות" in topic_he and "פנימי" in topic_he:
        topic_en = args.en or "loneliness of boarding school principals"
    else:
        topic_en = args.en or topic_he

    THESIS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    work_dir = THESIS_DIR / stamp
    work_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"📚 Thesis Lit Collector")
    print(f"   Topic: {topic_he}")
    print(f"   EN:    {topic_en}")
    print(f"   Target: {args.target} unique papers")
    print(f"   Output: {work_dir}")
    print(f"{'='*60}")

    # 1. Expand queries
    print(f"\n  🧠 Expanding into {args.n_queries} query variations...")
    queries = expand_queries(topic_he, topic_en,
                             n_en=int(args.n_queries * 0.75),
                             n_he=int(args.n_queries * 0.25))
    print(f"  → {len(queries)} queries")
    for q in queries:
        print(f"     · {q}")

    # 2. Bulk collect
    raw, per_q = bulk_collect(queries, target=args.target)
    print(f"\n  📊 Raw: {len(raw)} papers across {len(queries)} queries")

    # 3. Dedupe
    unique = dedupe(raw)
    print(f"  📊 After dedupe: {len(unique)} unique papers")

    # 4. Score relevance — lemmatize both topic and paper terms for Hebrew morphology
    topic_terms_en = _tokenize(topic_en)
    topic_terms_he_raw = {w for w in topic_he.split() if len(w) >= 3}
    topic_terms_he = _he_lemma_set(topic_terms_he_raw)
    for p in unique:
        p["relevance"] = relevance_score(p, topic_terms_en, topic_terms_he)

    # 5. Sort & truncate
    unique.sort(key=lambda p: (p["relevance"], p.get("citation_count", 0) or 0), reverse=True)
    final = unique[: args.target * 2]  # keep ~600 for tier 3 if available
    top = final[: args.target]

    # 6. Save
    json_path = work_dir / "papers_full.json"
    json_path.write_text(json.dumps({
        "topic_he": topic_he,
        "topic_en": topic_en,
        "queries": queries,
        "stats": per_q,
        "total_raw": len(raw),
        "total_unique": len(unique),
        "papers": final,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = render_bibliography_csv(final, work_dir)
    md_path = render_lit_review(final, topic_he, work_dir)

    # Tier breakdown
    t1 = sum(1 for p in final if p["relevance"] >= 0.5)
    t2 = sum(1 for p in final if 0.3 <= p["relevance"] < 0.5)
    t3 = sum(1 for p in final if p["relevance"] < 0.3)

    print(f"\n{'='*60}")
    print(f"✅ Lit collection complete → {work_dir}")
    print(f"{'='*60}")
    print(f"  📚 {len(final)} מקורות שמורים (מתוכם top {min(args.target, len(final))} למסמך)")
    print(f"     🌟 שכבה 1 (rel≥0.5): {t1}")
    print(f"     ✅ שכבה 2 (rel 0.3-0.5): {t2}")
    print(f"     📚 שכבה 3 (rel<0.3): {t3}")
    print(f"\n  קבצים:")
    print(f"   • {json_path.name}  — JSON מלא")
    print(f"   • {csv_path.name}   — לייבוא ל-Zotero/Mendeley")
    print(f"   • {md_path.name}    — סקירת ספרות מסודרת")
    print(f"\n  📝 השלב הבא: python3 thesis_prep.py \"{topic_he}\" --skip-search")
    print(f"     (יבנה הצעת מחקר + RQs מעל ה-{len(final)} מקורות)")

    # Sync to Obsidian — wikilinks, frontmatter, indexes
    try:
        from obsidian_bridge import bridge_all
        print(f"\n  🌐 Syncing to Obsidian...")
        bridge_all()
    except Exception as e:
        print(f"  ⚠️ Obsidian sync skipped: {e}")


if __name__ == "__main__":
    main()
