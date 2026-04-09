"""
Memory System
מעקב אחרי כל מה שנחקר, נכתב ופורסם
קובץ: output/memory.json
"""

import json
from pathlib import Path
from datetime import datetime
from config import OUTPUT_DIR

MEMORY_FILE = OUTPUT_DIR / "memory.json"


def _empty_memory() -> dict:
    return {
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "main_field": "",
        "researched_topics": [],
        "papers": {},
        "articles": [],
        "content_created": [],
        "topic_queue": [],
        "coverage_map": {},
        "gaps": [],
        "iterations": 0,
    }


def load_memory() -> dict:
    if MEMORY_FILE.exists():
        with open(MEMORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    return _empty_memory()


def save_memory(mem: dict):
    mem["updated_at"] = datetime.now().isoformat()
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(mem, f, ensure_ascii=False, indent=2)


def record_research(topic: str, subtopics: list[str], papers_file: Path):
    mem = load_memory()
    if topic not in mem["researched_topics"]:
        mem["researched_topics"].append(topic)
    for s in subtopics:
        if s not in mem["researched_topics"]:
            mem["researched_topics"].append(s)

    if papers_file.exists():
        with open(papers_file, encoding="utf-8") as f:
            data = json.load(f)
        papers = data.get("papers", data) if isinstance(data, dict) else data
        for p in papers:
            pid = p.get("paperId") or p.get("title", "")[:60]
            if pid and pid not in mem["papers"]:
                mem["papers"][pid] = {
                    "title": p.get("title"),
                    "year": p.get("year"),
                    "topic": topic,
                    "citation_count": p.get("citation_count") or p.get("citations", 0),
                }

    mem["coverage_map"][topic] = mem["coverage_map"].get(topic, 0) + 3
    for s in subtopics:
        mem["coverage_map"][s] = mem["coverage_map"].get(s, 0) + 1
    mem["topic_queue"] = [t for t in mem["topic_queue"] if t != topic]
    mem["iterations"] += 1
    save_memory(mem)
    return mem


def record_article(article_paths: dict, topic: str):
    mem = load_memory()
    mem["articles"].append({
        "topic": topic,
        "paths": {k: str(v) for k, v in article_paths.items()},
        "created_at": datetime.now().isoformat(),
    })
    save_memory(mem)


def record_content(content_type: str, topic: str, file_path: str):
    mem = load_memory()
    mem["content_created"].append({
        "type": content_type,
        "topic": topic,
        "path": file_path,
        "created_at": datetime.now().isoformat(),
    })
    save_memory(mem)


def add_to_queue(topics: list[str]):
    mem = load_memory()
    for t in topics:
        if t not in mem["topic_queue"] and t not in mem["researched_topics"]:
            mem["topic_queue"].append(t)
    save_memory(mem)


def set_gaps(gaps: list[str]):
    mem = load_memory()
    mem["gaps"] = gaps
    save_memory(mem)


def get_summary() -> str:
    mem = load_memory()
    lines = [
        f"📊 מצב הזיכרון ({mem['updated_at'][:10]})",
        f"   נושאים שנחקרו:  {len(mem['researched_topics'])}",
        f"   מאמרים שנאספו:  {len(mem['papers'])}",
        f"   מאמרים שנכתבו:  {len(mem['articles'])}",
        f"   תוכן שנוצר:     {len(mem['content_created'])}",
        f"   בתור לחקירה:    {len(mem['topic_queue'])} נושאים",
    ]
    if mem["topic_queue"]:
        lines.append(f"   הבא בתור:       {mem['topic_queue'][0]}")
    if mem["gaps"]:
        lines.append(f"   פערים:          {', '.join(mem['gaps'][:3])}")
    return "\n".join(lines)
