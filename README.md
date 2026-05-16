# 🦊 Moki — Education Agents Pipeline

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/)
[![Claude API](https://img.shields.io/badge/Claude-Opus%204.7-orange.svg)](https://anthropic.com/)
[![Status](https://img.shields.io/badge/status-active-success.svg)](https://github.com/pazer30-jpg/education-agents)
[![Agents](https://img.shields.io/badge/agents-13-purple.svg)](https://github.com/pazer30-jpg/education-agents)
[![APA 7](https://img.shields.io/badge/citations-APA%207-informational.svg)](https://github.com/pazer30-jpg/education-agents)
[![License](https://img.shields.io/badge/license-personal-lightgrey.svg)](https://github.com/pazer30-jpg/education-agents)

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

```mermaid
flowchart TD
    Topic([📌 Topic]) --> A0

    A0["🧠 Agent 0 — Planner<br/><i>research plan + proposal + RQs</i>"] --> A1
    A1["🔍 Agent 1 — Researcher<br/><i>7 academic APIs: SS · OpenAlex · ERIC · CORE · CrossRef · DOAJ · PubMed</i>"]
    A1 --> A15["📄 Agent 1.5 — PDF Reader"]
    A1 --> A17["🧪 Agent 1.7 — Paper Analyzer<br/><i>methodology + evidence_strength</i>"]
    A15 --> A2
    A17 --> A2

    A2["✍️ Agent 2 — Writer<br/><i>APA 7 academic article (EN + HE)</i>"]
    A2 --> A25["📝 Agent 2.5 — Editor"]
    A2 --> A27["✓ Agent 2.7 — Fact Checker<br/><i>verify citations vs abstracts</i>"]
    A25 --> A3
    A27 --> A3

    A3["✨ Agent 3 — Content Creator<br/><i>LinkedIn · Blog · Podcast</i>"]
    A3 --> A35["👤 Agent 3.5 — Human Review"]
    A3 --> A36["📝 Agent 3.6 — Editor"]
    A35 --> A4
    A36 --> A4

    A4["🎨 Agent 4 — Designer<br/><i>SVG covers + banners</i>"]
    A4 --> A6
    A6["🎬 Agent 6 — Video Creator<br/><i>fal.ai: Seedance · Kling · Veo · Hailuo (5-10s, 9:16)</i>"]
    A6 --> A5

    A5["🎯 Agent 5 — Project Manager<br/><i>QA gates · Loop Detector · Orchestration</i>"] --> Output([📚 Hebrew content + EN paper + Video])

    %% Reciprocal feedback channels
    A27 -.->|"missing citations"| A1
    A2 -.->|"weak claims"| A2
    A5 -.->|"QA fail + retry hint"| A2
    A5 -.->|"QA fail + retry hint"| A3

    classDef agent fill:#4a90e2,stroke:#2c5aa0,color:#fff
    classDef sub fill:#7cb9e8,stroke:#4a90e2,color:#000
    classDef io fill:#ffd700,stroke:#cc9900,color:#000
    class A0,A1,A2,A3,A4,A5 agent
    class A15,A17,A25,A27,A35,A36 sub
    class Topic,Output io
```

> _Solid arrows: data flow.  Dotted arrows: reciprocal feedback (via scratchpad)._

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
| `FAL_KEY` | No (skip Agent 6 without it) | fal.ai key for video generation |

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

# Video generation (Agent 6 — requires FAL_KEY)
python agent6_video_creator.py --auto-latest linkedin --model=seedance_lite

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

## 🎬 Demo

To capture a demo of the system in action:

```bash
# Record terminal session (macOS — installs once)
brew install asciinema agg

# Record a typical run (small example)
asciinema rec demo.cast -c "python agent5_project_manager.py 'belonging research'"

# Convert to animated GIF
agg demo.cast demo.gif --speed 2

# Or use the dashboard for a static screenshot
moki-dash    # opens output/dashboard.html in browser
```

Then add `demo.gif` to the repo root and reference it here.

---

## 📊 Engineering Highlights

What makes this project technically interesting:

- **13-agent pipeline** with optional gates, retries, and loop detection
- **8 reciprocal feedback channels** between agents via shared scratchpad
- **Obsidian-as-memory** — agent prompts read user-editable markdown files
- **3-tier classification** for file organization (frontmatter → rules → Claude fallback)
- **Atomic checkpointing** with SHA256 hash validation + .bak rolling
- **Daily budget cap** with environment override + hard halt
- **Hebrew-aware** — RTL rendering, rule-based lemmatization, mixed-script regex
- **APA 7 generation** with proper author formatting + sorted bibliography
- **Pre-generation outline gate** — catches weak outputs before $$ is spent
- **Auto-organizer** — keeps Obsidian vault tidy by file type
- **Performance learner** — top 20% vs bottom 20% pattern extraction

---

## 📄 License

Personal research project. Not currently licensed for redistribution.
