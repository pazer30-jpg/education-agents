"""
corpus_graph.py — Graph analysis of the citation network.

לוקח את 1,056 ההוגים + 184 הפוסטים ובונה graph:
  - Nodes:  הוגים + פוסטים
  - Edges:  כל פוסט מחובר להוגים שהוא מצטט

ניתוחים:
  - Centrality: מי ההוגה הכי מרכזי בקורפוס שלך?
  - Communities: קלסטרים של הוגים שמופיעים יחד
  - Bridges: הוגים שמחברים בין תחומים
  - Underutilized: הוגים שצוטטו פעם ולא חזרו (שווה להחיות?)

Usage:
  python3 corpus_graph.py                  # full report
  python3 corpus_graph.py --centrality     # רק centrality
  python3 corpus_graph.py --communities    # רק communities
  python3 corpus_graph.py --export gexf    # ייצא graph לGephi
"""

import sys
import re
import json
from pathlib import Path
from datetime import datetime
from collections import Counter, defaultdict

try:
    import networkx as nx
except ImportError:
    print("  ❌ networkx not installed. Run: pip install networkx")
    sys.exit(1)

from config import OUTPUT_DIR, ARTICLES_DIR, LINKEDIN_DIR, BLOG_DIR, PODCAST_DIR

GRAPH_DIR = OUTPUT_DIR / "_graph"
GRAPH_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
# Build graph from posts + citations
# ─────────────────────────────────────────────

# Match (Author Year) and Author (Year) patterns
CITATION_PATTERNS = [
    re.compile(r"\(([A-Z][a-zA-Z\-']+(?:\s+&\s+[A-Z][a-zA-Z\-']+)?(?:\s+et\s+al\.?)?),?\s+(\d{4})\)"),
    re.compile(r"\b([A-Z][a-zA-Z\-']+(?:\s+et\s+al\.?)?)\s+\((\d{4})\)"),
    # Already-converted wikilinks
    re.compile(r"\[\[([A-Z][a-zA-Z\-' ]+?)\s+(\d{4})\]\]"),
    # Hebrew (Author year)
    re.compile(r"\(([֐-׿']+(?:[\s־-][֐-׿']+)?),?\s+(\d{4})\)"),
]


def _extract_citations(text: str) -> set[tuple[str, str]]:
    """Return set of (author_normalized, year) pairs."""
    cites = set()
    for pattern in CITATION_PATTERNS:
        for match in pattern.finditer(text):
            author = match.group(1).strip()
            year = match.group(2).strip()
            # Normalize: remove "et al.", "&", extra spaces
            author = re.sub(r"\s+et\s+al\.?", "", author)
            author = re.sub(r"\s+&\s+.*", "", author).strip()
            if author and len(author) > 1:
                cites.add((author, year))
    return cites


def build_graph() -> nx.Graph:
    """Build a graph: nodes=posts+authors, edges=cited_by."""
    G = nx.Graph()

    sources = [
        (ARTICLES_DIR.glob("*.md"), "article"),
        (LINKEDIN_DIR.glob("*ready*.txt"), "linkedin"),
        (BLOG_DIR.glob("*.md"), "blog"),
        (PODCAST_DIR.glob("*script*.md"), "podcast"),
    ]
    n_posts = 0

    for it, kind in sources:
        for f in it:
            if f.name.endswith(".bak"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            post_id = f"post::{f.stem[:60]}"
            G.add_node(post_id, kind=kind, type="post", file=str(f.relative_to(OUTPUT_DIR)))
            n_posts += 1

            for author, year in _extract_citations(text):
                author_id = f"author::{author}"
                if not G.has_node(author_id):
                    G.add_node(author_id, type="author", name=author)
                G.add_edge(post_id, author_id, year=year)

    return G


# ─────────────────────────────────────────────
# Analyses
# ─────────────────────────────────────────────

def centrality_analysis(G: nx.Graph, top_n: int = 15) -> dict:
    """Find most central authors — degree centrality."""
    author_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == "author"]
    if not author_nodes:
        return {"top": []}

    degree = {n: G.degree(n) for n in author_nodes}
    sorted_authors = sorted(degree.items(), key=lambda x: -x[1])

    return {
        "total_authors": len(author_nodes),
        "top": [
            {"author": n.replace("author::", ""), "citations": d}
            for n, d in sorted_authors[:top_n]
        ],
    }


def underutilized_authors(G: nx.Graph, threshold: int = 1) -> list[str]:
    """Authors cited only `threshold` times — candidates to revisit."""
    underused = []
    for n, d in G.nodes(data=True):
        if d.get("type") == "author" and G.degree(n) <= threshold:
            underused.append(n.replace("author::", ""))
    return sorted(underused)


def community_detection(G: nx.Graph) -> dict:
    """Greedy modularity communities."""
    try:
        from networkx.algorithms.community import greedy_modularity_communities
    except ImportError:
        return {"error": "community detection not available"}

    # Project to author-only graph: authors connected if they appear in same post
    author_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == "author"]
    A = nx.Graph()
    A.add_nodes_from(author_nodes)
    for n, d in G.nodes(data=True):
        if d.get("type") != "post":
            continue
        cited_authors = [m for m in G.neighbors(n) if G.nodes[m].get("type") == "author"]
        for i, a1 in enumerate(cited_authors):
            for a2 in cited_authors[i + 1:]:
                if A.has_edge(a1, a2):
                    A[a1][a2]["weight"] += 1
                else:
                    A.add_edge(a1, a2, weight=1)

    if A.number_of_edges() == 0:
        return {"error": "no co-citation edges"}

    communities = list(greedy_modularity_communities(A, weight="weight"))
    return {
        "n_communities": len(communities),
        "communities": [
            sorted([n.replace("author::", "") for n in c])[:10]
            for c in communities[:8]
        ],
    }


def bridge_authors(G: nx.Graph, top_n: int = 10) -> list[dict]:
    """Authors with high betweenness — connect distinct topic clusters."""
    author_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == "author"]
    if len(author_nodes) < 5:
        return []

    A = nx.Graph()
    A.add_nodes_from(author_nodes)
    for n, d in G.nodes(data=True):
        if d.get("type") != "post":
            continue
        cited = [m for m in G.neighbors(n) if G.nodes[m].get("type") == "author"]
        for i, a1 in enumerate(cited):
            for a2 in cited[i + 1:]:
                A.add_edge(a1, a2)

    if A.number_of_edges() == 0:
        return []

    bc = nx.betweenness_centrality(A, k=min(50, A.number_of_nodes()))
    sorted_b = sorted(bc.items(), key=lambda x: -x[1])
    return [
        {"author": n.replace("author::", ""), "betweenness": round(b, 3)}
        for n, b in sorted_b[:top_n] if b > 0
    ]


# ─────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────

def export_gexf(G: nx.Graph) -> Path:
    """Export to GEXF for Gephi."""
    out = GRAPH_DIR / f"corpus_{datetime.now().strftime('%Y%m%d_%H%M')}.gexf"
    nx.write_gexf(G, str(out))
    return out


def export_obsidian_dataview(centrality: dict, communities: dict, bridges: list) -> Path:
    """Export to Obsidian-friendly markdown."""
    out = OUTPUT_DIR / "_graph_analysis.md"
    lines = [
        "---",
        "title: 🕸 ניתוח graph של הקורפוס",
        "tags: [moki/index, moki/graph]",
        "---",
        "",
        f"# 🕸 ניתוח Graph — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
        "## 🎯 הוגים מרכזיים (לפי כמות ציטוטים)",
        "",
        "| הוגה | ציטוטים |",
        "|---|---|",
    ]
    for entry in centrality.get("top", [])[:15]:
        lines.append(f"| [[{entry['author']}]] | {entry['citations']}x |")
    lines.append("")

    lines.append("## 🌉 הוגי-גשר (מקשרים בין תחומים)")
    lines.append("")
    for entry in bridges[:10]:
        lines.append(f"- [[{entry['author']}]] — betweenness {entry['betweenness']}")
    lines.append("")

    if communities.get("communities"):
        lines.append("## 🧬 קהילות הוגים (קלסטרים שמופיעים יחד)")
        lines.append("")
        for i, com in enumerate(communities["communities"], 1):
            lines.append(f"### קלסטר {i}")
            for author in com[:8]:
                lines.append(f"- [[{author}]]")
            lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


# ─────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────

def full_report() -> str:
    G = build_graph()
    n_posts = sum(1 for n, d in G.nodes(data=True) if d.get("type") == "post")
    n_authors = sum(1 for n, d in G.nodes(data=True) if d.get("type") == "author")

    lines = [
        f"\n🕸 Corpus Graph Analysis — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"   Posts:        {n_posts}",
        f"   Authors:      {n_authors}",
        f"   Connections:  {G.number_of_edges()}",
        "",
    ]

    # Centrality
    cent = centrality_analysis(G, top_n=10)
    lines.append("🎯 הוגים מרכזיים (top 10):")
    for entry in cent.get("top", []):
        lines.append(f"   {entry['citations']:>4}x  {entry['author']}")
    lines.append("")

    # Communities
    com = community_detection(G)
    if "communities" in com:
        lines.append(f"🧬 קהילות הוגים: {com['n_communities']}")
        for i, c in enumerate(com["communities"][:5], 1):
            lines.append(f"   קלסטר {i}: {', '.join(c[:5])}{'...' if len(c) > 5 else ''}")
        lines.append("")

    # Bridges
    bridges = bridge_authors(G, top_n=5)
    if bridges:
        lines.append("🌉 הוגי-גשר (מקשרים תחומים):")
        for b in bridges:
            lines.append(f"   {b['betweenness']:.3f}  {b['author']}")
        lines.append("")

    # Save Obsidian summary
    try:
        export_obsidian_dataview(cent, com, bridges)
        lines.append(f"📁 נשמר: output/_graph_analysis.md")
    except Exception as e:
        lines.append(f"⚠️ Export failed: {e}")

    return "\n".join(lines)


def main():
    if "--export" in sys.argv:
        idx = sys.argv.index("--export")
        fmt = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "gexf"
        G = build_graph()
        if fmt == "gexf":
            path = export_gexf(G)
            print(f"✅ Exported: {path}")
        return

    if "--centrality" in sys.argv:
        G = build_graph()
        c = centrality_analysis(G, top_n=20)
        print(json.dumps(c, ensure_ascii=False, indent=2))
        return

    if "--communities" in sys.argv:
        G = build_graph()
        c = community_detection(G)
        print(json.dumps(c, ensure_ascii=False, indent=2))
        return

    print(full_report())


if __name__ == "__main__":
    main()
