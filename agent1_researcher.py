"""
Agent 1 - Researcher
מחפש מאמרים אקדמיים מ-מספר מקורות:
  1. Semantic Scholar  — ציטוטים, peer-reviewed
  2. OpenAlex          — ללא rate limit, open access
  3. Crossref          — DOI metadata, citation counts
  4. ERIC              — ספציפי לחינוך
  5. CORE              — full-text open access
  6. DOAJ              — open access, supports Hebrew
  7. Hebrew (OpenAlex he-filter + translated queries)
  8. PubMed            — בריאות, טראומה, פסיכולוגיה (NCBI E-utilities)
  + Unpaywall          — מוצא PDFs לפי DOI
  + Claude Knowledge כ-fallback אחרון
"""

import requests
import json
import time
import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def search_semantic_scholar(query: str, limit: int = 10, retry: int = 2) -> list[dict]:
    """Reduced retry: 2 attempts max with 10s + 20s wait = 30s budget per query.
    Other sources (OpenAlex, CORE, ERIC) cover the gap when SS is rate-limited."""
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
                if attempt < retry - 1:
                    wait = 10 * (attempt + 1)
                    print(f"  [SS] Rate limited, waiting {wait}s ({attempt+1}/{retry})...")
                    time.sleep(wait)
                else:
                    print(f"  [SS] Rate limited — giving up, falling back to OpenAlex")
                    return []
            else:
                return []
        except Exception:
            if attempt < retry - 1:
                time.sleep(3)
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
            "citation_count": int(p.get("citationCount") or 0),
            "venue": p.get("venue", ""),
            "source": "Semantic Scholar",
        })
    return cleaned


# ─────────────────────────────────────────────
# OpenAlex (no rate limit, free, broad coverage)
# ─────────────────────────────────────────────

OPENALEX_BASE = "https://api.openalex.org"


def search_openalex(query: str, limit: int = 10, language: str = None) -> list[dict]:
    """Search OpenAlex — no API key needed, no rate limit.
    language: ISO code like 'he' to filter Hebrew-only results."""
    url = f"{OPENALEX_BASE}/works"
    params = {
        "search": query,
        "per_page": limit,
        "sort": "relevance_score:desc",
        "select": "id,title,abstract_inverted_index,authorships,publication_year,"
                  "cited_by_count,primary_location,doi,open_access,language",
    }
    if language:
        params["filter"] = f"language:{language}"
    try:
        r = requests.get(url, params=params, timeout=15,
                         headers={"User-Agent": "education-agents/1.0"})
        if r.status_code != 200:
            return []
        return r.json().get("results", [])
    except Exception:
        return []


# ─────────────────────────────────────────────
# DOAJ (Hebrew-friendly open access)
# ─────────────────────────────────────────────

DOAJ_BASE = "https://doaj.org/api/search/articles"


def search_doaj(query: str, limit: int = 10, hebrew_only: bool = False) -> list[dict]:
    """Search DOAJ — open access journals, supports Hebrew via language filter."""
    import urllib.parse
    q = urllib.parse.quote(query)
    url = f"{DOAJ_BASE}/{q}"
    params = {"pageSize": limit}
    try:
        r = requests.get(url, params=params, timeout=15,
                         headers={"User-Agent": "education-agents/1.0"})
        if r.status_code != 200:
            return []
        results = r.json().get("results", [])
        if hebrew_only:
            results = [p for p in results
                       if "he" in (p.get("bibjson", {}).get("language") or [])]
        return results
    except Exception:
        return []


def _clean_doaj_papers(raw: list[dict]) -> list[dict]:
    cleaned = []
    for p in raw:
        bj = p.get("bibjson", {}) if isinstance(p, dict) else {}
        authors = ", ".join(
            (a.get("name", "") if isinstance(a, dict) else str(a))
            for a in (bj.get("author") or [])[:4]
        )
        links = bj.get("link", []) or []
        pdf_url = next((l.get("url", "") for l in links
                        if isinstance(l, dict) and l.get("type") == "fulltext"), "")
        journal = bj.get("journal", {}) if isinstance(bj.get("journal"), dict) else {}
        cleaned.append({
            "title": bj.get("title", "")[:200],
            "authors": authors,
            "year": bj.get("year"),
            "abstract": (bj.get("abstract") or "")[:500],
            "url": (bj.get("identifier", [{}])[0].get("id", "")
                    if bj.get("identifier") else ""),
            "pdf_url": pdf_url,
            "citation_count": 0,  # DOAJ doesn't provide
            "venue": journal.get("title", "") if isinstance(journal, dict) else "",
            "source": "DOAJ",
            "language": ", ".join(bj.get("language", []) or []),
        })
    return cleaned


# ─────────────────────────────────────────────
# Hebrew query translation (minimal static map)
# ─────────────────────────────────────────────

_EN_HE_MAP = {
    "youth movements": "תנועות נוער",
    "non-formal education": "חינוך בלתי-פורמלי",
    "informal education": "חינוך לא-פורמלי",
    "education": "חינוך",
    "belonging": "שייכות",
    "identity": "זהות",
    "resilience": "חוסן",
    "leadership": "מנהיגות",
    "mentoring": "הנחיה",
    "group": "קבוצה",
    "memorial": "זיכרון",
    "trauma": "טראומה",
    "israel": "ישראל",
    "israeli": "ישראלי",
    "civic education": "חינוך אזרחי",
    "civil religion": "דת אזרחית",
    "boarding school": "פנימייה",
    "youth village": "כפר נוער",
}


def _translate_to_hebrew(query: str) -> str:
    """Minimal English→Hebrew translation for common education terms."""
    q = query.lower()
    hebrew_parts = []
    for en, he in _EN_HE_MAP.items():
        if en in q:
            hebrew_parts.append(he)
    return " ".join(hebrew_parts) if hebrew_parts else ""


def _is_israel_related(query: str) -> bool:
    """Check if query is about Israel/Israeli context — triggers Hebrew search."""
    indicators = ["israel", "israeli", "ישראל", "ישראלי", "jewish", "zionist",
                  "kibbutz", "aliya", "moshav", "kfar", "mechina"]
    q = query.lower()
    return any(ind in q for ind in indicators)


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
            if not isinstance(a, dict):
                continue
            author_obj = a.get("author") or {}
            name = author_obj.get("display_name", "") if isinstance(author_obj, dict) else ""
            if name:
                authors.append(name)

        # PDF URL
        pdf_url = ""
        oa = p.get("open_access") or {}
        if oa.get("oa_url"):
            pdf_url = oa.get("oa_url", "")
        elif (p.get("primary_location") or {}).get("pdf_url"):
            pdf_url = (p.get("primary_location") or {}).get("pdf_url", "")

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
            "citation_count": int(p.get("cited_by_count") or 0),
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
            "url": (p.get("sourceFulltextUrls") or [""])[0] if p.get("sourceFulltextUrls") else (doi or ""),
            "pdf_url": pdf_url,
            "citation_count": int(p.get("citationCount") or 0),
            "venue": p.get("publisher", "") or ((p.get("journals") or [{}])[0].get("title", "") if p.get("journals") else ""),
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
            "citation_count": int(p.get("is-referenced-by-count") or 0),
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
# PubMed (NCBI E-utilities) — health/trauma/mental/psychology
# ─────────────────────────────────────────────

PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

_PUBMED_KEYWORDS = (
    "health", "trauma", "mental", "wellbeing", "well-being", "well being",
    "psychology", "psychological", "psychiatric", "depression", "anxiety",
    "ptsd", "resilience", "therapy", "therapeutic", "child", "adolescent",
    "youth", "טראומה", "בריאות", "נפש", "פסיכולוג", "רווחה",
)


def _is_pubmed_relevant(query: str) -> bool:
    q = (query or "").lower()
    return any(kw in q for kw in _PUBMED_KEYWORDS)


def search_pubmed(query: str, limit: int = 10) -> list[dict]:
    """Search PubMed via NCBI E-utilities. No API key needed for low volume.
    Returns list of dicts with raw fields ready for _clean_pubmed_papers."""
    try:
        r = requests.get(PUBMED_ESEARCH, params={
            "db": "pubmed", "term": query, "retmax": limit, "retmode": "json",
        }, timeout=15, headers={"User-Agent": "education-agents/1.0"})
        if r.status_code != 200:
            return []
        ids = r.json().get("esearchresult", {}).get("idlist", []) or []
        if not ids:
            return []
        time.sleep(0.4)  # be polite to NCBI
        r2 = requests.get(PUBMED_EFETCH, params={
            "db": "pubmed", "id": ",".join(ids), "rettype": "abstract", "retmode": "xml",
        }, timeout=20, headers={"User-Agent": "education-agents/1.0"})
        if r2.status_code != 200:
            return []
        import xml.etree.ElementTree as ET
        root = ET.fromstring(r2.content)
        papers = []
        for art in root.findall(".//PubmedArticle"):
            pmid_el = art.find(".//PMID")
            pmid = pmid_el.text if pmid_el is not None else ""
            title_el = art.find(".//ArticleTitle")
            title = "".join(title_el.itertext()).strip() if title_el is not None else ""
            abstract_parts = [
                "".join(ab.itertext()).strip()
                for ab in art.findall(".//Abstract/AbstractText")
            ]
            abstract = " ".join(p for p in abstract_parts if p)
            year_el = art.find(".//PubDate/Year") or art.find(".//PubDate/MedlineDate")
            year = None
            if year_el is not None and year_el.text:
                try:
                    year = int(year_el.text[:4])
                except (ValueError, TypeError):
                    pass
            authors = []
            for au in art.findall(".//Author")[:4]:
                ln = au.find("LastName")
                fn = au.find("ForeName") or au.find("Initials")
                name = " ".join(x.text for x in (fn, ln) if x is not None and x.text).strip()
                if name:
                    authors.append(name)
            venue_el = art.find(".//Journal/Title") or art.find(".//Journal/ISOAbbreviation")
            venue = venue_el.text if venue_el is not None else ""
            doi = ""
            for aid in art.findall(".//ArticleId"):
                if aid.get("IdType") == "doi" and aid.text:
                    doi = aid.text
                    break
            papers.append({
                "pmid": pmid, "title": title, "abstract": abstract, "authors": authors,
                "year": year, "venue": venue, "doi": doi,
            })
        return papers
    except Exception as e:
        print(f"  [PubMed] Error: {e}")
        return []


def _clean_pubmed_papers(raw: list[dict]) -> list[dict]:
    cleaned = []
    for p in raw:
        pmid = p.get("pmid", "")
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""
        if p.get("doi"):
            url = f"https://doi.org/{p['doi']}"
        cleaned.append({
            "title": p.get("title", "")[:200],
            "authors": ", ".join(p.get("authors", []) or []),
            "year": p.get("year"),
            "abstract": (p.get("abstract") or "")[:500],
            "url": url,
            "pdf_url": "",  # PubMed itself doesn't host PDFs; Unpaywall may fill this
            "citation_count": 0,  # PubMed doesn't provide citation counts
            "venue": p.get("venue", "") or "",
            "source": "PubMed",
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
    Search all 5 sources in parallel, merge and deduplicate.
    Returns: (all_papers_cleaned, stats)
    """
    all_papers = []
    seen_titles = set()
    stats = {"semantic_scholar": 0, "openalex": 0, "crossref": 0, "eric": 0, "core": 0,
             "doaj": 0, "hebrew": 0, "pubmed": 0}
    _lock = threading.Lock()

    def _add_unique(papers: list[dict], source: str):
        with _lock:
            for p in papers:
                title_key = (p.get("title") or "").lower().strip()[:80]
                if title_key and title_key not in seen_titles:
                    seen_titles.add(title_key)
                    all_papers.append(p)
                    stats[source] += 1

    # ── Source search functions (each runs in its own thread) ──

    def _search_semantic_scholar():
        """Semantic Scholar — rate limited at 1 req/sec."""
        for i, q in enumerate(queries):
            if i > 0:
                time.sleep(5)  # SS rate limit: generous delay between queries
            print(f"  [Agent1/SS] 🔍 '{q[:50]}'...")
            results = search_semantic_scholar(q, limit=10)
            _track("semantic_scholar", results)
            if not results and i == 0:
                print("  [Agent1/SS] ⚠️  rate limited, skipping remaining queries")
                break
            _add_unique(_clean_ss_papers(results), "semantic_scholar")

    def _search_openalex():
        """OpenAlex — no rate limit."""
        for i, q in enumerate(queries):
            if i > 0:
                time.sleep(1)  # polite delay
            print(f"  [Agent1/OpenAlex] 🌐 '{q[:50]}'...")
            results = search_openalex(q, limit=10)
            _track("openalex", results)
            _add_unique(_clean_openalex_papers(results), "openalex")

    def _search_crossref():
        """Crossref — DOI + citations."""
        for i, q in enumerate(queries[:2]):
            if i > 0:
                time.sleep(1)  # polite delay
            print(f"  [Agent1/Crossref] 🔗 '{q[:50]}'...")
            results = search_crossref(q, limit=8)
            _track("crossref", results)
            _add_unique(_clean_crossref_papers(results), "crossref")

    def _search_eric():
        """ERIC — education-specific."""
        for i, q in enumerate(queries[:3]):
            if i > 0:
                time.sleep(1)  # polite delay
            print(f"  [Agent1/ERIC] 🎓 '{q[:50]}'...")
            results = search_eric(q, limit=8)
            _track("eric", results)
            _add_unique(_clean_eric_papers(results), "eric")

    def _search_core():
        """CORE — full-text open access."""
        for i, q in enumerate(queries[:2]):
            if i > 0:
                time.sleep(1)  # polite delay
            print(f"  [Agent1/CORE] 📄 '{q[:50]}'...")
            results = search_core(q, limit=8)
            _track("core", results)
            _add_unique(_clean_core_papers(results), "core")

    def _search_doaj():
        """DOAJ — open access, supports Hebrew."""
        for i, q in enumerate(queries[:2]):
            if i > 0:
                time.sleep(1)
            print(f"  [Agent1/DOAJ] 🇮🇱 '{q[:50]}'...")
            results = search_doaj(q, limit=8)
            _track("doaj", results)
            _add_unique(_clean_doaj_papers(results), "doaj")

    def _search_pubmed():
        """PubMed — only run for health/trauma/mental/wellbeing queries."""
        relevant = [q for q in queries if _is_pubmed_relevant(q)]
        if not relevant:
            print("  [Agent1/PubMed] ⏭️  no health-related queries — skipping")
            return
        for i, q in enumerate(relevant[:3]):
            if i > 0:
                time.sleep(0.5)
            print(f"  [Agent1/PubMed] 🧬 '{q[:50]}'...")
            results = search_pubmed(q, limit=8)
            _track("pubmed", results)
            _add_unique(_clean_pubmed_papers(results), "pubmed")

    def _search_hebrew():
        """Hebrew-specific: OpenAlex filter + translated query."""
        # Only activate if query mentions Israel/Israeli context
        israel_queries = [q for q in queries if _is_israel_related(q)]
        if not israel_queries:
            return

        # Hebrew-filtered OpenAlex
        for q in israel_queries[:2]:
            print(f"  [Agent1/Hebrew-OA] 📜 '{q[:50]}'...")
            results = search_openalex(q, limit=8, language="he")
            _track("hebrew", results)
            _add_unique(_clean_openalex_papers(results), "hebrew")

        # Translated queries to Hebrew
        for q in israel_queries[:2]:
            he_query = _translate_to_hebrew(q)
            if he_query:
                time.sleep(1)
                print(f"  [Agent1/Hebrew-translated] 📜 '{he_query[:50]}'...")
                results = search_openalex(he_query, limit=6)
                _add_unique(_clean_openalex_papers(results), "hebrew")
                time.sleep(1)
                results = search_doaj(he_query, limit=6, hebrew_only=True)
                _add_unique(_clean_doaj_papers(results), "hebrew")

    # ── Run all sources in parallel ─────────
    source_funcs = {
        "Semantic Scholar": _search_semantic_scholar,
        "OpenAlex": _search_openalex,
        "Crossref": _search_crossref,
        "ERIC": _search_eric,
        "CORE": _search_core,
        "DOAJ": _search_doaj,
        "Hebrew": _search_hebrew,
        "PubMed": _search_pubmed,
    }

    print(f"  [Agent1] Searching {len(source_funcs)} sources in parallel...")
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(fn): name
            for name, fn in source_funcs.items()
        }
        for future in as_completed(futures):
            source_name = futures[future]
            try:
                future.result()
                print(f"  [Agent1] ✅ {source_name} done")
            except Exception as e:
                print(f"  [Agent1] ⚠️ {source_name} failed: {e}")

    # ── Unpaywall: find PDFs for papers with DOI (sequential, after all sources) ──
    papers_with_doi = [p for p in all_papers if p.get("url") and "doi.org" in p.get("url", "") and not p.get("pdf_url")]
    if papers_with_doi:
        print(f"  [Agent1] 🔓 Unpaywall: searching PDFs for {min(8, len(papers_with_doi))} papers...")
        found = enrich_pdfs_via_unpaywall(papers_with_doi[:8])  # was 15 — reduced to cut sequential time
        if found:
            print(f"  [Agent1] 🔓 Unpaywall: found {found} PDFs")
            stats["unpaywall_pdfs"] = found

    # ── Citation chain: follow top-cited papers (only top 1 to cut time) ──
    top_cited = sorted(
        [p for p in all_papers if p.get("citation_count", 0) > 20],
        key=lambda p: p.get("citation_count", 0), reverse=True,
    )[:1]  # was 3 — reduced because each call can rate-limit SS
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
                                "citation_count": int(cp.get("citationCount") or 0),
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

    # Check scratchpad for missing-source hints from fact_checker (previous run)
    extra_hints = ""
    try:
        from scratchpad import read as _scratch_read
        missing = _scratch_read("fact_checker", "missing_sources")
        if missing and isinstance(missing, dict):
            sugg = missing.get("suggested_searches", [])
            if sugg:
                extra_hints = (
                    f"\n\nMandatory: Previous fact_checker found missing citations. "
                    f"Include searches for these specific sources: {', '.join(sugg[:5])}"
                )
                print(f"  [Agent1] 🔄 Got {len(sugg)} missing-source hints from fact_checker")
    except Exception:
        pass

    queries_prompt = f"""Topic: "{topic}", Subtopics: {json.dumps(subtopics)}{extra_hints}
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
    icons = {"semantic_scholar": "🔍", "openalex": "🌐", "crossref": "🔗", "eric": "🎓",
             "core": "📄", "doaj": "🇮🇱", "hebrew": "📜", "pubmed": "🧬",
             "unpaywall_pdfs": "🔓"}
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

    # ── Create research summary (structured Markdown for humans + Agent 2) ──
    try:
        summary_path = _create_research_summary(topic, subtopics, curated, topic_slug)
        if summary_path:
            print(f"  [Agent1] 📋 Summary: {summary_path.name}")
    except Exception as e:
        print(f"  [Agent1] Summary failed: {e}")

    print(f"\n✅ Agent 1 complete → {filepath} ({len(curated)} papers)\n")
    return filepath


def _create_research_summary(topic: str, subtopics: list[str],
                              papers: list[dict], topic_slug: str) -> Path | None:
    """
    Create structured research_summary.md per topic with:
    - top 3-5 papers (sorted by citation count)
    - synthesis paragraph (one paragraph)
    - identified gaps
    - theoretical frameworks

    Output: output/papers/<topic_slug>_summary.md
    Cost: ~$0.5/topic (one LLM call)
    """
    # Take top 5 most cited papers with abstracts
    top_papers = sorted(
        [p for p in papers if p.get("abstract") and len(p.get("abstract", "")) > 100],
        key=lambda p: p.get("citation_count", 0),
        reverse=True,
    )[:5]

    if len(top_papers) < 3:
        return None  # Not enough papers for meaningful synthesis

    # Build slim payload for LLM
    slim = []
    for p in top_papers:
        authors = p.get("authors", "")
        if isinstance(authors, list):
            authors = ", ".join(str(a) for a in authors[:3])
        slim.append({
            "title": (p.get("title") or "")[:140],
            "authors": authors[:80],
            "year": p.get("year"),
            "citations": p.get("citation_count") or 0,
            "abstract": (p.get("abstract") or "")[:400],
            "venue": (p.get("venue") or "")[:60],
        })

    prompt = f"""You are an academic research synthesizer. Topic: "{topic}"
Subtopics: {", ".join(subtopics) if subtopics else "(none)"}

Top {len(slim)} papers (by citation count):
{json.dumps(slim, ensure_ascii=False, indent=1)}

Return JSON with EXACTLY this structure (no extra fields):
{{
  "synthesis": "ONE paragraph (3-5 sentences) describing what this body of research collectively says. Connect the papers, don't list them.",
  "gaps": ["specific gap 1", "specific gap 2", "specific gap 3"],
  "theoretical_frameworks": ["framework 1 (e.g. Self-Determination Theory)", "framework 2"],
  "key_findings": [
    {{"paper": "Author Year", "finding": "one-sentence key finding"}},
    ...
  ]
}}

Rules:
- synthesis: 60-120 words, written in Hebrew
- gaps: 2-4 items, each in Hebrew, specific (not "more research needed")
- theoretical_frameworks: 2-4, English names but explain in Hebrew if needed
- key_findings: one per paper, English

Return JSON only."""

    try:
        result = ask_claude_json(prompt, max_budget=0.5, timeout=180)
    except Exception as e:
        print(f"  [Agent1] Summary LLM failed: {e}")
        return None

    if not isinstance(result, dict):
        return None

    # Build Markdown output
    summary_md = SUMMARY_DIR_MD = PAPERS_DIR / f"{topic_slug}_summary.md"
    lines = [
        f"# Research Summary — {topic}",
        f"",
        f"_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}_",
        f"_Topic: {topic}_",
        f"_Subtopics: {', '.join(subtopics)}_" if subtopics else "",
        f"",
        f"## Synthesis",
        f"",
        result.get("synthesis", "(synthesis unavailable)"),
        f"",
        f"## Top Papers",
        f"",
    ]

    for p in slim:
        # Format APA-ish citation
        apa = f"{p['authors']} ({p['year']}). {p['title']}"
        if p.get('venue'):
            apa += f". {p['venue']}"
        apa += "."
        # Find matching key finding from result
        key_finding = ""
        for kf in result.get("key_findings", []):
            if isinstance(kf, dict):
                paper_ref = kf.get("paper", "")
                if any(part in paper_ref for part in [str(p['year']), p['authors'].split(',')[0] if p['authors'] else ""]):
                    key_finding = kf.get("finding", "")
                    break
        lines.append(f"### {p['title']}")
        lines.append(f"- **Citation:** {apa}")
        lines.append(f"- **Citations:** {p['citations']}")
        if key_finding:
            lines.append(f"- **Key finding:** {key_finding}")
        lines.append("")

    lines.append("## Identified Gaps")
    lines.append("")
    for gap in result.get("gaps", [])[:5]:
        lines.append(f"- {gap}")
    lines.append("")

    lines.append("## Theoretical Frameworks")
    lines.append("")
    for fw in result.get("theoretical_frameworks", [])[:5]:
        lines.append(f"- {fw}")
    lines.append("")

    # Also save JSON for machine consumption
    summary_json = PAPERS_DIR / f"{topic_slug}_summary.json"
    summary_json.write_text(
        json.dumps({
            "topic": topic,
            "subtopics": subtopics,
            "papers": slim,
            "synthesis": result.get("synthesis", ""),
            "gaps": result.get("gaps", []),
            "theoretical_frameworks": result.get("theoretical_frameworks", []),
            "key_findings": result.get("key_findings", []),
            "generated_at": datetime.now().isoformat(),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    SUMMARY_DIR_MD.write_text("\n".join(lines), encoding="utf-8")
    return SUMMARY_DIR_MD


# ─────────────────────────────────────────────
# Standalone
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    topic = sys.argv[1] if len(sys.argv) > 1 else "non-formal education"
    subtopics = sys.argv[2:] or ["values education", "experiential learning", "belonging", "identity"]
    path = run_researcher(topic, subtopics)
    print(f"Papers saved to: {path}")
