"""
Agent 1 - Researcher
מחפש מאמרים אקדמיים מ-6 מקורות:
  1. Semantic Scholar  — ציטוטים, peer-reviewed
  2. OpenAlex          — ללא rate limit, open access
  3. Crossref          — DOI metadata, citation counts
  4. ERIC              — ספציפי לחינוך
  5. CORE              — full-text open access
  6. Unpaywall         — מוצא PDFs לפי DOI
  + Claude Knowledge כ-fallback אחרון
"""

import requests
import json
import time
from pathlib import Path
from config import SEMANTIC_SCHOLAR_BASE, SEMANTIC_SCHOLAR_API_KEY, PAPERS_DIR
from claude_cli import ask_claude_json


# ─────────────────────────────────────────────
# Source health tracking
# ─────────────────────────────────────────────

_source_health: dict[str, dict] = {}

def _track(source: str, results: list, error: str = ""):
    _source_health[source] = {"count": len(results), "ok": len(results) > 0, "error": error}

def _health_report() -> str:
    if not _source_health:
        return ""
    working = [s for s, h in _source_health.items() if h["ok"]]
    failed  = [s for s, h in _source_health.items() if not h["ok"]]
    total   = sum(h["count"] for h in _source_health.values())
    lines   = [f"  📡 מקורות: {len(working)} עבדו, {len(failed)} נכשלו | {total} תוצאות סה\"כ"]
    if failed:
        lines.append(f"  ⚠️  נכשלו: {', '.join(failed)}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Semantic Scholar
# ─────────────────────────────────────────────

def _ss_headers() -> dict:
    h = {}
    if SEMANTIC_SCHOLAR_API_KEY:
        h["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY
    return h


def search_semantic_scholar(query: str, limit: int = 10, retry: int = 4) -> list[dict]:
    url = f"{SEMANTIC_SCHOLAR_BASE}/paper/search"
    params = {
        "query": query,
        "limit": limit,
        "fields": "paperId,title,abstract,authors,year,url,openAccessPdf,citationCount,venue",
    }
    for attempt in range(retry):
        try:
            r = requests.get(url, params=params, headers=_ss_headers(), timeout=15)
            if r.status_code == 200:
                return r.json().get("data", [])
            elif r.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"  [SS] Rate limited, waiting {wait}s (attempt {attempt+1}/{retry})...")
                time.sleep(wait)
            else:
                print(f"  [SS] status {r.status_code}")
                return []
        except Exception as e:
            print(f"  [SS] Error: {e}")
            if attempt < retry - 1:
                time.sleep(5)
            else:
                return []
    return []


def _clean_ss_papers(raw: list[dict]) -> list[dict]:
    cleaned = []
    for p in raw:
        pdf = p.get("openAccessPdf") or {}
        authors = [a.get("name", "") for a in (p.get("authors") or [])[:4]]
        cleaned.append({
            "title": p.get("title", ""),
            "authors": ", ".join(authors),
            "year": p.get("year"),
            "abstract": (p.get("abstract") or "")[:500],
            "url": p.get("url", ""),
            "pdf_url": pdf.get("url", "") if pdf else "",
            "citation_count": p.get("citationCount", 0),
            "venue": p.get("venue", ""),
            "source": "Semantic Scholar",
        })
    return cleaned


# ─────────────────────────────────────────────
# OpenAlex (no rate limit, free, broad coverage)
# ─────────────────────────────────────────────

OPENALEX_BASE = "https://api.openalex.org"


def search_openalex(query: str, limit: int = 10) -> list[dict]:
    """Search OpenAlex — no API key needed, no rate limit."""
    url = f"{OPENALEX_BASE}/works"
    params = {
        "search": query,
        "per_page": limit,
        "sort": "relevance_score:desc",
        "select": "id,title,abstract_inverted_index,authorships,publication_year,"
                  "cited_by_count,primary_location,doi,open_access",
    }
    try:
        r = requests.get(url, params=params, timeout=15,
                         headers={"User-Agent": "education-agents/1.0"})
        if r.status_code != 200:
            print(f"  [OpenAlex] status {r.status_code}")
            return []
        return r.json().get("results", [])
    except Exception as e:
        print(f"  [OpenAlex] Error: {e}")
        return []


def _rebuild_abstract(inverted_index: dict | None) -> str:
    """OpenAlex stores abstracts as inverted index — rebuild to text."""
    if not inverted_index:
        return ""
    word_positions = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort()
    return " ".join(w for _, w in word_positions)[:500]


def _clean_openalex_papers(raw: list[dict]) -> list[dict]:
    cleaned = []
    for p in raw:
        # Authors
        authors = []
        for a in (p.get("authorships") or [])[:4]:
            name = a.get("author", {}).get("display_name", "")
            if name:
                authors.append(name)

        # PDF URL
        pdf_url = ""
        oa = p.get("open_access") or {}
        if oa.get("oa_url"):
            pdf_url = oa["oa_url"]
        elif p.get("primary_location", {}).get("pdf_url"):
            pdf_url = p["primary_location"]["pdf_url"]

        # DOI
        doi = p.get("doi") or ""
        if doi and not doi.startswith("http"):
            doi = f"https://doi.org/{doi}"

        cleaned.append({
            "title": p.get("title", ""),
            "authors": ", ".join(authors),
            "year": p.get("publication_year"),
            "abstract": _rebuild_abstract(p.get("abstract_inverted_index")),
            "url": doi,
            "pdf_url": pdf_url,
            "citation_count": p.get("cited_by_count", 0),
            "venue": ((p.get("primary_location") or {}).get("source") or {}).get("display_name", ""),
            "source": "OpenAlex",
        })
    return cleaned


# ─────────────────────────────────────────────
# CORE (full-text open access)
# ─────────────────────────────────────────────

CORE_BASE = "https://api.core.ac.uk/v3"


def search_core(query: str, limit: int = 10) -> list[dict]:
    """Search CORE — free, full-text open access papers."""
    url = f"{CORE_BASE}/search/works"
    params = {"q": query, "limit": limit}
    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code != 200:
            print(f"  [CORE] status {r.status_code}")
            return []
        return r.json().get("results", [])
    except Exception as e:
        print(f"  [CORE] Error: {e}")
        return []


def _clean_core_papers(raw: list[dict]) -> list[dict]:
    cleaned = []
    for p in raw:
        # Authors
        authors_list = p.get("authors") or []
        authors = ", ".join(
            a.get("name", "") if isinstance(a, dict) else str(a)
            for a in authors_list[:4]
        )

        # PDF / download URL
        pdf_url = ""
        if p.get("downloadUrl"):
            pdf_url = p["downloadUrl"]
        elif p.get("sourceFulltextUrls"):
            pdf_url = p["sourceFulltextUrls"][0] if p["sourceFulltextUrls"] else ""

        # DOI
        doi = p.get("doi") or ""
        if doi and not doi.startswith("http"):
            doi = f"https://doi.org/{doi}"

        # Year
        year = None
        if p.get("yearPublished"):
            year = p["yearPublished"]
        elif p.get("publishedDate"):
            try:
                year = int(p["publishedDate"][:4])
            except (ValueError, TypeError):
                pass

        cleaned.append({
            "title": p.get("title", ""),
            "authors": authors,
            "year": year,
            "abstract": (p.get("abstract") or "")[:500],
            "url": doi or p.get("sourceFulltextUrls", [""])[0] if p.get("sourceFulltextUrls") else doi,
            "pdf_url": pdf_url,
            "citation_count": p.get("citationCount", 0),
            "venue": p.get("publisher", "") or p.get("journals", [{}])[0].get("title", "") if p.get("journals") else "",
            "source": "CORE",
        })
    return cleaned


# ─────────────────────────────────────────────
# Crossref (DOI metadata + citation counts)
# ─────────────────────────────────────────────

def search_crossref(query: str, limit: int = 10) -> list[dict]:
    """Search Crossref — DOI metadata, citation counts, broad coverage."""
    url = "https://api.crossref.org/works"
    params = {
        "query": query,
        "rows": limit,
        "select": "DOI,title,author,published-print,published-online,"
                  "is-referenced-by-count,container-title,link",
    }
    try:
        r = requests.get(url, params=params, timeout=15,
                         headers={"User-Agent": "education-agents/1.0 (mailto:pazer30@gmail.com)"})
        if r.status_code != 200:
            print(f"  [Crossref] status {r.status_code}")
            return []
        return r.json().get("message", {}).get("items", [])
    except Exception as e:
        print(f"  [Crossref] Error: {e}")
        return []


def _clean_crossref_papers(raw: list[dict]) -> list[dict]:
    cleaned = []
    for p in raw:
        # Title
        titles = p.get("title", [])
        title = titles[0] if titles else ""

        # Authors
        authors_raw = p.get("author", [])
        authors = ", ".join(
            f"{a.get('family', '')} {a.get('given', '')}".strip()
            for a in authors_raw[:4]
        )

        # Year
        year = None
        for date_field in ("published-print", "published-online"):
            parts = (p.get(date_field) or {}).get("date-parts", [[]])
            if parts and parts[0]:
                year = parts[0][0]
                break

        # DOI + PDF
        doi = p.get("DOI", "")
        url = f"https://doi.org/{doi}" if doi else ""

        # Try to find PDF link
        pdf_url = ""
        for link in (p.get("link") or []):
            if link.get("content-type") == "application/pdf":
                pdf_url = link.get("URL", "")
                break

        # Venue
        venue = (p.get("container-title") or [""])[0]

        cleaned.append({
            "title": title,
            "authors": authors,
            "year": year,
            "abstract": "",  # Crossref rarely has abstracts
            "url": url,
            "pdf_url": pdf_url,
            "citation_count": p.get("is-referenced-by-count", 0),
            "venue": venue,
            "source": "Crossref",
        })
    return cleaned


# ─────────────────────────────────────────────
# ERIC (education-specific database)
# ─────────────────────────────────────────────

ERIC_BASE = "https://api.ies.ed.gov/eric/"


def search_eric(query: str, limit: int = 10) -> list[dict]:
    """Search ERIC — education-specific, free, no API key."""
    params = {
        "search": query,
        "rows": limit,
        "format": "json",
    }
    try:
        r = requests.get(ERIC_BASE, params=params, timeout=15)
        if r.status_code != 200:
            print(f"  [ERIC] status {r.status_code}")
            return []
        return r.json().get("response", {}).get("docs", [])
    except Exception as e:
        print(f"  [ERIC] Error: {e}")
        return []


def _clean_eric_papers(raw: list[dict]) -> list[dict]:
    cleaned = []
    for p in raw:
        authors = p.get("author", [])
        if isinstance(authors, list):
            authors = ", ".join(authors[:4])

        # ERIC ID → URL
        eric_id = p.get("id", "")
        url = f"https://eric.ed.gov/?id={eric_id}" if eric_id else ""

        # Some ERIC entries have direct PDF links
        pdf_url = p.get("url", "") or ""
        if pdf_url and not pdf_url.endswith(".pdf"):
            pdf_url = ""

        # Year from publicationdateyear
        year = p.get("publicationdateyear")
        if not year and p.get("publicationdate"):
            try:
                year = int(str(p["publicationdate"])[:4])
            except (ValueError, TypeError):
                pass

        cleaned.append({
            "title": p.get("title", ""),
            "authors": authors,
            "year": year,
            "abstract": (p.get("description", "") or "")[:500],
            "url": url,
            "pdf_url": pdf_url,
            "citation_count": 0,
            "venue": p.get("source", "") or p.get("publisher", ""),
            "source": "ERIC",
        })
    return cleaned


# ─────────────────────────────────────────────
# Unpaywall (find PDFs by DOI)
# ─────────────────────────────────────────────

UNPAYWALL_EMAIL = "pazer30@gmail.com"


def find_pdf_via_unpaywall(doi: str) -> str:
    """Given a DOI, try to find an open access PDF URL via Unpaywall."""
    if not doi:
        return ""
    # Extract DOI from URL if needed
    doi_id = doi
    if "doi.org/" in doi:
        doi_id = doi.split("doi.org/")[-1]
    if not doi_id:
        return ""

    try:
        r = requests.get(
            f"https://api.unpaywall.org/v2/{doi_id}",
            params={"email": UNPAYWALL_EMAIL},
            timeout=8,
        )
        if r.status_code != 200:
            return ""
        data = r.json()
        oa = data.get("best_oa_location") or {}
        return oa.get("url_for_pdf") or oa.get("url") or ""
    except Exception:
        return ""


def enrich_pdfs_via_unpaywall(papers: list[dict]) -> int:
    """Try to find PDF URLs for papers that have DOI but no pdf_url."""
    found = 0
    for p in papers:
        if p.get("pdf_url"):
            continue
        doi = p.get("url", "")
        if "doi.org" not in doi:
            continue
        pdf = find_pdf_via_unpaywall(doi)
        if pdf:
            p["pdf_url"] = pdf
            p["pdf_source"] = "Unpaywall"
            found += 1
    return found


# ─────────────────────────────────────────────
# Fallback: Claude generates papers from knowledge
# ─────────────────────────────────────────────

def _generate_papers_from_claude(topic: str, subtopics: list[str]) -> list[dict]:
    print("  [Agent1] All APIs unavailable — generating from Claude's knowledge...")

    prompt = f"""You are an academic researcher in education.

Generate a list of 12-15 real, published academic papers about: "{topic}"
Subtopics to cover: {json.dumps(subtopics)}

For each paper include:
- title: exact paper title
- authors: author names (Last, First format)
- year: publication year (prefer 2015-2024, at least 50% should be from 2015+)
- abstract: 2-3 sentence summary of the paper
- url: DOI URL (https://doi.org/...) — you MUST provide this for every paper
- pdf_url: open access PDF URL if you know one. Empty string only if truly unavailable.
- citation_count: approximate number of citations (integer)
- venue: journal or conference name
- source: "Claude Knowledge"
- relevance_note: 1-2 sentences on why this paper is relevant

IMPORTANT:
- Only include papers you are confident are real and published
- Always provide DOI URLs when possible
- Prefer recent papers (2015+) but include seminal older works too
- Return a JSON array."""

    return ask_claude_json(prompt, max_budget=1.5)


# ─────────────────────────────────────────────
# Multi-source search
# ─────────────────────────────────────────────

def _search_all_sources(queries: list[str]) -> tuple[list[dict], dict]:
    """
    Search all 3 sources, merge and deduplicate.
    Returns: (all_papers_cleaned, stats)
    """
    all_papers = []
    seen_titles = set()
    stats = {"semantic_scholar": 0, "openalex": 0, "crossref": 0, "eric": 0, "core": 0}

    def _add_unique(papers: list[dict], source: str):
        for p in papers:
            title_key = (p.get("title") or "").lower().strip()[:80]
            if title_key and title_key not in seen_titles:
                seen_titles.add(title_key)
                all_papers.append(p)
                stats[source] += 1

    # ── Semantic Scholar ──────────────────────
    for i, q in enumerate(queries):
        if i > 0:
            time.sleep(5)
        print(f"  [Agent1] 🔍 SS: '{q[:50]}'...")
        results = search_semantic_scholar(q, limit=10)
        _track("semantic_scholar", results)
        if not results and i == 0:
            print("  [Agent1] ⚠️  SS rate limited")
            break
        _add_unique(_clean_ss_papers(results), "semantic_scholar")

    # ── OpenAlex (always — no rate limit) ─────
    for q in queries:
        print(f"  [Agent1] 🌐 OpenAlex: '{q[:50]}'...")
        results = search_openalex(q, limit=10)
        _track("openalex", results)
        _add_unique(_clean_openalex_papers(results), "openalex")

    # ── Crossref (DOI + citations) ────────────
    for q in queries[:2]:
        print(f"  [Agent1] 🔗 Crossref: '{q[:50]}'...")
        results = search_crossref(q, limit=8)
        _track("crossref", results)
        _add_unique(_clean_crossref_papers(results), "crossref")

    # ── ERIC (education-specific) ─────────────
    for q in queries[:3]:
        print(f"  [Agent1] 🎓 ERIC: '{q[:50]}'...")
        results = search_eric(q, limit=8)
        _track("eric", results)
        _add_unique(_clean_eric_papers(results), "eric")

    # ── CORE (for full-text PDFs) ─────────────
    for q in queries[:2]:
        print(f"  [Agent1] 📄 CORE: '{q[:50]}'...")
        results = search_core(q, limit=8)
        _track("core", results)
        _add_unique(_clean_core_papers(results), "core")

    # ── Unpaywall: find PDFs for papers with DOI ──
    papers_with_doi = [p for p in all_papers if p.get("url") and "doi.org" in p.get("url", "") and not p.get("pdf_url")]
    if papers_with_doi:
        print(f"  [Agent1] 🔓 Unpaywall: searching PDFs for {len(papers_with_doi)} papers...")
        found = enrich_pdfs_via_unpaywall(papers_with_doi[:15])  # limit API calls
        if found:
            print(f"  [Agent1] 🔓 Unpaywall: found {found} PDFs")
            stats["unpaywall_pdfs"] = found

    # ── Citation chain: follow top-cited papers ──
    top_cited = sorted(
        [p for p in all_papers if p.get("citation_count", 0) > 20],
        key=lambda p: p.get("citation_count", 0), reverse=True,
    )[:3]
    if top_cited:
        print(f"  [Agent1] 🔗 Citation chain: following {len(top_cited)} top-cited papers...")
        for p in top_cited:
            paper_id = p.get("paperId") or ""
            if not paper_id or paper_id.startswith("http"):
                continue
            try:
                url = f"{SEMANTIC_SCHOLAR_BASE}/paper/{paper_id}/citations"
                r = requests.get(url, params={"fields": "title,authors,year,url,citationCount", "limit": 5},
                                 headers=_ss_headers(), timeout=12)
                if r.status_code == 200:
                    citing = r.json().get("data", [])
                    for c in citing:
                        cp = c.get("citingPaper", {})
                        if cp.get("title"):
                            _add_unique([{
                                "title": cp.get("title", ""),
                                "authors": ", ".join(a.get("name","") for a in (cp.get("authors") or [])[:3]),
                                "year": cp.get("year"),
                                "url": cp.get("url", ""),
                                "citation_count": cp.get("citationCount", 0),
                                "source": "citation_chain",
                                "abstract": "", "pdf_url": "", "venue": "",
                            }], "citation_chain")
                    stats["citation_chain"] = stats.get("citation_chain", 0) + len(citing)
                time.sleep(2)
            except Exception:
                pass
        if stats.get("citation_chain"):
            print(f"  [Agent1] 🔗 Citation chain: {stats['citation_chain']} citing papers found")

    return all_papers, stats


# ─────────────────────────────────────────────
# Dedup check
# ─────────────────────────────────────────────

def _check_existing(topic: str) -> Path | None:
    """Check if we already have recent research for this topic."""
    topic_slug = topic.replace(" ", "_").lower()[:40]
    candidates = list(PAPERS_DIR.glob(f"*{topic_slug}*enriched*.json")) + \
                 list(PAPERS_DIR.glob(f"*{topic_slug}*papers*.json"))
    if not candidates:
        return None
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
        papers = data.get("papers", data) if isinstance(data, dict) else data
        if len(papers) >= 5:
            return latest
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────
# Main agent function
# ─────────────────────────────────────────────

def run_researcher(topic: str, subtopics: list[str], force: bool = False) -> Path:
    # Check for existing research (skip if force=True)
    if not force:
        existing = _check_existing(topic)
        if existing:
            print(f"\n  ♻️  נמצא מחקר קיים עבור '{topic}' → {existing.name} (דולג)")
            return existing

    print(f"\n{'='*60}")
    print(f"🔍 Agent 1 - Researcher | Topic: {topic}")
    print(f"   Subtopics: {', '.join(subtopics)}")
    print(f"{'='*60}\n")

    # Step 1: Generate search queries
    print("  [Agent1] Generating search queries...")
    queries_prompt = f"""Topic: "{topic}", Subtopics: {json.dumps(subtopics)}
Generate 5 diverse search queries for finding education research papers.
Rules:
  - 4 queries in English (academic terms)
  - 1 query in Hebrew (for Israeli research: e.g. "חינוך בלתי פורמלי נוער")
  - Each query uses different terms/angles
Return ONLY a JSON array of strings."""

    try:
        queries = ask_claude_json(queries_prompt, max_budget=0.3)
        if not isinstance(queries, list):
            raise ValueError("not a list")
    except Exception:
        queries = [topic] + [s for s in subtopics[:3]]

    print(f"  [Agent1] Queries ({len(queries)}): {queries}")

    # Step 2: Search all sources
    all_papers, stats = _search_all_sources(queries)

    total = sum(v for k, v in stats.items() if k != "unpaywall_pdfs")
    print(f"\n  [Agent1] 📊 נמצאו {total} מאמרים:")
    icons = {"semantic_scholar": "🔍", "openalex": "🌐", "crossref": "🔗", "eric": "🎓", "core": "📄", "unpaywall_pdfs": "🔓"}
    for source, count in stats.items():
        if count > 0:
            print(f"     {icons.get(source, '•')} {source}: {count}")

    # Step 3: Curate or fallback
    if not all_papers:
        curated = _generate_papers_from_claude(topic, subtopics)
    else:
        print(f"  [Agent1] Curating {len(all_papers)} papers with Claude...")

        curate_prompt = f"""Research topic: "{topic}"
Subtopics: {json.dumps(subtopics)}

Papers found ({len(all_papers)} total, from multiple sources):
{json.dumps(all_papers, ensure_ascii=False, indent=1)}

Task:
1. Select the 12-15 most relevant and high-quality papers
2. Prefer: papers with pdf_url, highly cited, diverse years (2010-2024), varied sources
3. Add "relevance_note" (1-2 sentences) for each selected paper
4. Keep the original "source" field for each paper

Return a JSON array of paper objects with:
title, authors, year, abstract, url, pdf_url, citation_count, venue, source, relevance_note"""

        try:
            curated = ask_claude_json(curate_prompt, max_budget=1.0)
            if not isinstance(curated, list):
                curated = all_papers[:15]
        except Exception as e:
            print(f"  [Agent1] Curation error: {e}, using raw results")
            curated = all_papers[:15]

    # Count papers with PDF URLs
    with_pdf = sum(1 for p in curated if p.get("pdf_url"))
    print(f"  [Agent1] Final: {len(curated)} papers ({with_pdf} with PDF URL)")

    # Save
    topic_slug = topic.replace(" ", "_").lower()[:40]
    filepath = PAPERS_DIR / f"{topic_slug}_papers.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump({
            "topic": topic,
            "subtopics": subtopics,
            "sources": stats,
            "papers": curated,
        }, f, ensure_ascii=False, indent=2)

    # Health report
    report = _health_report()
    if report:
        print(report)

    print(f"\n✅ Agent 1 complete → {filepath} ({len(curated)} papers)\n")
    return filepath


# ─────────────────────────────────────────────
# Standalone
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    topic = sys.argv[1] if len(sys.argv) > 1 else "non-formal education"
    subtopics = sys.argv[2:] or ["values education", "experiential learning", "belonging", "identity"]
    path = run_researcher(topic, subtopics)
    print(f"Papers saved to: {path}")
