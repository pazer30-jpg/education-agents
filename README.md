# 🦊 Moki — Education Agents Pipeline

Autonomous multi-agent system for academic research and content creation in education.
From **topic** → **academic research** → **article** → **LinkedIn post + blog + podcast**.

Generates Hebrew content with English academic sources. Built for non-formal education research.

---

## ⚡ Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment + voice
cp .env.example .env                       # add your API keys
cp voice_profile.example.py voice_profile.py   # customize for your voice

# 3. Health check
bash scripts/health_check.sh

# 4. Run
python agent5_project_manager.py
# or directly:
python orchestrator.py "informal education" --content linkedin blog
```

> **Voice profile is required.** The system reads `voice_profile.py` to shape
> generated content. A template is provided in `voice_profile.example.py` —
> copy and customize before running. The real `voice_profile.py` is gitignored
> to keep personal style/bio out of the repo.

---

## 🏗 Architecture

```text
Topic
  │
  ▼
Agent 0 — Planner          (research planning + proposal)
  │
  ▼
Agent 1 — Researcher       (Semantic Scholar · OpenAlex · CrossRef · ERIC · PubMed · CORE · DOAJ)
  │  └── Agent 1.5 — PDF Reader      (text extraction from papers)
  │  └── Agent 1.7 — Paper Analyzer  (methodology + evidence_strength classification)
  │
  ▼
Agent 2 — Writer           (academic article EN + HE)
  │  └── Agent 2.5 — Editor          (APA 7 polish)
  │  └── Agent 2.7 — Fact Checker    (citation validation against abstracts)
  │
  ▼
Agent 3 — Content Creator  (LinkedIn · Blog · Podcast)
  │  └── Agent 3.5 — Human Review    (optional approval gate)
  │  └── Agent 3.6 — Editor          (per-platform polish)
  │
  ▼
Agent 4 — Designer         (SVG covers and banners)
  │
  ▼
Agent 5 — Project Manager  (QA gates · Loop Detector · Orchestration)
```

**Supporting layers:**
- 🧠 **Memory** — `memory.py`, `obsidian_memory.py`, `scratchpad.py`
- 🛡 **Quality** — `qa_checker.py`, `causal_validator.py`, `conflict_resolver.py`, `fact_checker.py`
- 📊 **Observability** — `analytics.py`, `observability.py`, `dashboard.py`, `agent_health.py`
- 🎓 **Academic** — `seminar_writer.py`, `thesis_prep.py`, `thesis_lit_collector.py`, `bibliography.py`
- 🔁 **Learning** — `performance_learner.py`, `failure_analyzer.py`, `voice_match.py`, `reflective_loop.py`

---

## 🤝 Inter-Agent Communication

Agents talk to each other through `scratchpad.py` (transient, per-run) and Obsidian memory notes (persistent).

```text
fact_checker        → researcher        (missing citations → next searches)
causal_validator    → writer            (strong claims → soften)
conflict_resolver   → writer            (surface contradictions)
qa_checker          → next agent        (failure reason in retry prompt)
analytics           → agent0_planner    (strong/weak topics)
edit_tracker        → agent2_5_editor   (learned correction patterns from user edits)
active_response     → agent3            (observability alerts → behavior change)
```

---

## ⚙️ Configuration

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Only without Claude CLI | Direct API access |
| `TELEGRAM_BOT_TOKEN` | No | Failure/success notifications |
| `TELEGRAM_CHAT_ID` | No | Notification destination |
| `SEMANTIC_SCHOLAR_API_KEY` | No | Improved rate limits |
| `UNPAYWALL_EMAIL` | Yes | Access to open-access PDFs |
| `MOKI_AUTONOMY_LEVEL` | No (default: 1) | 0=ask all gates, 1=trust me, 2=full |
| `MOKI_DAILY_BUDGET` | No (default: 30) | Daily Claude budget cap in USD |

> **Claude CLI vs API Key:** The system prefers `claude` CLI (subscription).
> Falls back to `ANTHROPIC_API_KEY` if CLI is unavailable.

---

## 🚀 Running Modes

```bash
# Full pipeline (recommended — via Agent 5)
python agent5_project_manager.py

# Force full pipeline with all 5 agents (MAX mode)
python agent5_project_manager.py "full pipeline — topic X"

# Direct pipeline
python orchestrator.py "topic" --content linkedin blog podcast

# Research only
python agent1_researcher.py

# Content from existing article
python agent3_content_creator.py --from-article output/articles/my_article.md

# Academic writing (long form)
python seminar_writer.py --topic "X" --papers output/thesis/<stamp>/papers_full.json --target-words 12000
python thesis_prep.py "topic"
python thesis_lit_collector.py "topic" --target 300

# Autopilot (autonomous)
python autopilot.py
```

---

## 📁 Folder Structure

```text
education-agents/
├── agent0_planner.py            # Agent 0 — research planning
├── agent1_researcher.py         # Agent 1 — academic search
├── agent2_writer.py             # Agent 2 — article writing
├── agent3_content_creator.py    # Agent 3 — multi-platform content
├── agent4_designer.py           # Agent 4 — visual design
├── agent5_project_manager.py    # Agent 5 — orchestrator + QA
├── orchestrator.py              # Direct pipeline runner
├── claude_cli.py                # Claude wrapper (CLI + API fallback)
│
├── scripts/
│   ├── health_check.sh          # Pre-run health gate
│   ├── clean_cache.sh           # Cache cleanup
│   └── pipeline_stats.sh        # Performance stats
│
├── output/                      # Obsidian Vault — generated content
│   ├── ready/{linkedin,blog,podcast}/   # Approved for publishing
│   ├── articles/                # Academic articles
│   ├── papers/                  # Collected PDFs (gitignored)
│   ├── _memory/                 # Moki's active memory (gitignored)
│   └── thesis/<stamp>/          # Thesis preparation outputs
│
├── requirements.txt
├── .env.example
└── README.md
```

---

## 🔧 Useful Scripts

```bash
# Pre-run health check
bash scripts/health_check.sh

# Pipeline performance stats
bash scripts/pipeline_stats.sh

# Cache cleanup
bash scripts/clean_cache.sh
```

---

## 🐛 Common Issues

| Error | Solution |
|---|---|
| `Claude CLI not available and no ANTHROPIC_API_KEY` | Set `ANTHROPIC_API_KEY` in `.env` |
| `writer hard timeout — exceeded N min` | Topic too complex — reduce subtopics or use `--smart` mode |
| `'>=' not supported between instances of 'str' and 'int'` | Fixed in current version |
| Pipeline stuck for over an hour | Run `bash scripts/health_check.sh` to verify CLI |
| `DailyBudgetExceeded` | Raise with `export MOKI_DAILY_BUDGET=50` |

---

## 📊 Current State

```bash
bash scripts/pipeline_stats.sh
python agent_health.py            # Per-agent health card
python failure_analyzer.py        # Failure pattern detection
python performance_learner.py     # Top vs bottom content patterns
```

---

## 🗺 Complete Code Map

See [`output/_INDEX.md`](output/_INDEX.md) — auto-regenerated by `regenerate_index.py`.

---

## 🌍 Note on Language

The system generates content **in Hebrew** (the user's primary language), citing **English academic sources**.
This README, code comments, and docstrings are in English for accessibility.
The voice profile, system prompts, and Obsidian memory remain in Hebrew — they encode user preferences.

---

## 📄 License

Personal research project. Not currently licensed for redistribution.
