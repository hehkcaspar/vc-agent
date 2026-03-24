# Agentic Workspace Processor

A production-ready, domain-agnostic agent that operates on file workspaces. Accepts text instructions, scans and reasons about resources, then produces persistent artifacts that accumulate value over time.

[![Tests](https://img.shields.io/badge/tests-8%2F8%20passing-brightgreen)]()
[![E2E](https://img.shields.io/badge/e2e-3%2F3%20passing-brightgreen)]()
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)]()
[![License](https://img.shields.io/badge/license-MIT-green)]()

---

## 🏗️ Architecture: Core + Shell

This project follows a **core + shell** architecture:

- **Core** (`agent_workspace/`) — A capable, flexible, domain-agnostic engine that rarely needs to change. It handles workspace scanning, content extraction, agent orchestration, memory, and artifact persistence.
- **Shells** (`apps/`) — Lightweight application layers that wrap the core for specific use cases. Each shell defines its own UI, workflow, and domain logic while delegating heavy lifting to the core.

```
┌────────────────────────────────────────────────────────┐
│  Shell: Resume Screener (apps/resume_screener/)        │
│    FastAPI + WebSocket + File Watcher + Screener UI    │
├────────────────────────────────────────────────────────┤
│  Core: agent_workspace/                                │
│    ReAct Agent · Extractor · Workspace · Tools · CLI   │
├────────────────────────────────────────────────────────┤
│  Storage: File-based (Markdown, JSON, YAML)            │
└────────────────────────────────────────────────────────┘
```

**Principle**: Instructions define the domain, not the code. The same core powers investment monitoring, resume screening, and document analysis without modification.

---

## ✨ Core Features

- **🤖 ReAct Agent**: Dynamic tool selection — scan → extract → search → write
- **📁 File-first Storage**: All state in files, no database needed
- **🔄 Change Detection**: Hash-based diff tracks resource changes across runs
- **🧠 Memory System**: Persistent observations across sessions
- **📄 Multi-format Support**: PDF, DOCX, XLSX, TXT, MD, JSON, images
- **🔍 Search**: Keyword search across all resources and artifacts
- **📋 Templates**: Reusable instruction patterns with variables
- **📊 Traces**: Every run produces inspectable JSON execution logs

---

## 🚀 Quick Start (Core CLI)

### Installation

```bash
# Clone repository
git clone <repo-url>
cd agent-workspace

# (venv is REQUIRED) create and activate a Python virtual environment
python -m venv .venv
# Windows
.\.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# Install core dependencies
pip install -r agent_workspace/requirements.txt

# Configure API key
cp .env.example .env
# Edit .env and add your LLM_API_KEY
```

### Initialize Workspace

```bash
python -m agent_workspace init --dir ./my_workspace
```

### Run First Task

```bash
# Add files to resources/
cp ~/Documents/*.pdf my_workspace/resources/

# Run agent
python -m agent_workspace run --workspace ./my_workspace \
    --task "Summarize these documents and identify key insights"
```

### Check Results

```bash
# List generated artifacts
python -m agent_workspace artifacts --workspace ./my_workspace

# View reports
cat my_workspace/artifacts/reports/*.md
```

---

## 📱 Shell Apps

### Resume Screener (`apps/resume_screener/`)

A real-time resume screening web application. Monitors an incoming folder for new resumes, screens them against configured job descriptions using AI, and displays results in a professional web interface.

**Key features:**
- Real-time file monitoring with WebSocket updates
- Three-verdict system: Invite / Waitlist / Not a Match
- Multi-format resume support (PDF, DOCX, images)
- Chinese language support for JDs and resumes

```bash
# Install additional dependencies
pip install -r apps/resume_screener/backend/requirements.txt

# Run the app
python apps/resume_screener/backend/main.py

# Open http://localhost:8000
```

See [apps/resume_screener/README.md](apps/resume_screener/README.md) for full documentation.

**⚠️ Known Limitation**: Currently only evaluates against the first position in `positions.json`. [Design plan available](docs/07_MULTI_JD_DESIGN_PLAN.md) for multi-position matching.

---

## 📚 Documentation

| Document | Description |
|----------|-------------|
| [BUILD_PLAN](docs/01_BUILD_PLAN.md) | Original build plan with phases |
| [MVP_DESIGN](docs/02_MVP_DESIGN.md) | Architecture and design decisions |
| [USER_GUIDE](docs/03_USER_GUIDE.md) | Comprehensive usage guide |
| [ARCHITECTURE](docs/04_ARCHITECTURE.md) | System architecture details |
| [TESTING](docs/05_TESTING.md) | Testing documentation |
| [E2E_RESULTS](docs/06_E2E_RESULTS.md) | End-to-end test results |
| [MULTI_JD_DESIGN](docs/07_MULTI_JD_DESIGN_PLAN.md) | Multi-position JD matching design (resume_screener) |
| [DATA_ORG_DESIGN](docs/08_DATA_ORGANIZATION_DESIGN_PLAN.md) | Data organization design (resume_screener) |
| [TODO](docs/TODO.md) | Project TODOs and known gaps |

---

## 🧪 Testing

### Run Test Suite

```bash
# Phase 1 tests (basic functionality)
python -m tests.test_phase1

# Comprehensive tests (all phases)
python -m tests.test_all
```

### E2E Test Scenarios

| Scenario | Files | Description |
|----------|-------|-------------|
| [Investment Monitoring](tests/e2e/test_set_1) | 6 PDFs | Track company updates over time |
| [Resume Screening](tests/e2e/test_set_3) | 9 files | Evaluate and rank job candidates |
| [Job Platform](tests/e2e/test_set_4) | 129 files | Assess operational platform |

**Test Results**: ✅ All 3 E2E tests passed

---

## 🏗️ Project Structure

```
agent-workspace/
├── agent_workspace/           # CORE — domain-agnostic engine
│   ├── __init__.py
│   ├── __main__.py            # Entry point: python -m agent_workspace
│   ├── cli.py                 # CLI commands (init, run, scan, diff, artifacts, memory)
│   ├── config.py              # Environment vars + YAML workspace config
│   ├── workspace.py           # Resource scanning, SHA-256 diff, snapshots
│   ├── extractor.py           # Multi-format file extraction
│   ├── agent.py               # ReAct agent with LangGraph + retry
│   ├── prompts.py             # System prompts & template resolution
│   ├── utils.py               # Retry logic, progress callbacks
│   ├── requirements.txt
│   └── tools/                 # LangGraph tool wrappers
│       ├── scan_resources.py
│       ├── extract_content.py
│       ├── search_resources.py
│       ├── read_artifact.py
│       └── write_artifact.py
│
├── apps/                      # SHELLS — use-case-specific applications
│   └── resume_screener/       # Resume screening shell
│       ├── backend/           # FastAPI + screening logic
│       │   ├── main.py        # FastAPI app, WebSocket, API routes
│       │   ├── config.py      # Screener config (paths, polling)
│       │   ├── watcher.py     # File watcher + resume queue
│       │   ├── screener.py    # AI screening logic (calls core)
│       │   └── requirements.txt
│       ├── frontend/          # Single-page app
│       │   ├── index.html
│       │   ├── styles.css
│       │   └── app.js
│       └── sample_data/       # JDs, incoming folder, evaluations
│
├── docs/                      # Documentation
│   ├── 01_BUILD_PLAN.md
│   ├── 02_MVP_DESIGN.md
│   ├── 03_USER_GUIDE.md
│   ├── 04_ARCHITECTURE.md
│   ├── 05_TESTING.md
│   └── 06_E2E_RESULTS.md
│
├── tests/                     # Test suite
│   ├── test_phase1.py         # 8 unit tests
│   ├── test_all.py            # Comprehensive tests (Phases 1-4)
│   └── e2e/                   # E2E test data & results
│
├── .env                       # API keys (not committed)
├── AGENTS.md                  # Agent coding guide
└── README.md                  # This file
```

---

## 💡 Use Cases

### 1. Investment Monitoring
Track portfolio company updates over time:

```bash
python -m agent_workspace run \
  --task "Monitor invested company, summarize quarterly updates"
```

### 2. Resume Screening (via Shell App)
Real-time screening with WebSocket UI — see [Resume Screener](apps/resume_screener/README.md).

Or via core CLI:

```bash
python -m agent_workspace run \
  --task "Evaluate candidates against JD.txt, rank top 3, provide hire/no-hire"
```

### 3. Document Analysis
Analyze large document collections:

```bash
python -m agent_workspace run \
  --task "Analyze operational documents, identify strengths and improvements"
```

---

## ⚙️ Configuration

### Environment Variables

```bash
# Required
LLM_API_KEY=your-api-key

# Optional
LLM_BASE_URL=https://api.example.com/v1
LLM_MODEL=gpt-4o-mini
LLM_TEMPERATURE=0.1
```

Also accepts `DASHSCOPE_API_KEY` and `QWEN_API_KEY` as aliases for `LLM_API_KEY`.

### Workspace Config (config.yaml)

```yaml
workspace:
  resources_dir: resources
  artifacts_dir: artifacts

extraction:
  max_text_chars: 15000
  max_images: 10

agent:
  max_iterations: 20
  trace_enabled: true
```

---

## 🛠️ CLI Commands

```bash
# Initialize workspace
python -m agent_workspace init [--dir ./workspace]

# Run agent task
python -m agent_workspace run --task "Analyze documents"
python -m agent_workspace run --task-file instructions/task.md
python -m agent_workspace run --template evaluate --var criteria=criteria.docx

# Scan resources
python -m agent_workspace scan [--workspace .]

# Show changes
python -m agent_workspace diff [--workspace .]

# List artifacts
python -m agent_workspace artifacts [--workspace .]

# View memory
python -m agent_workspace memory [--workspace .]
```

---

## 🐛 Troubleshooting

**No API key found**
```bash
export LLM_API_KEY="your-key"
# or create .env file
```

**File not found** — Ensure files are in `resources/` directory. Use `scan` command to verify.

**Out of memory** — Reduce `max_text_chars` in `config.yaml` or process files in smaller batches.

---

## 📊 Performance

| Operation | Time |
|-----------|------|
| Initialize workspace | <1s |
| Scan 100 files | <1s |
| Extract PDF (1MB) | ~200ms |
| Simple analysis | 10-30s |
| Complex batch (100+ files) | 60-120s |

---

## 🏆 Validation

### Unit Tests: 8/8 Passing

- ✅ Workspace initialization
- ✅ Resource scanning
- ✅ Content extraction (PDF, DOCX, TXT, XLSX)
- ✅ Artifact read/write
- ✅ Diff detection
- ✅ Memory management
- ✅ Template resolution
- ✅ Search functionality

### E2E Tests: 3/3 Passing

- ✅ Investment monitoring (6 PDFs)
- ✅ Resume screening (9 files, Chinese)
- ✅ Job platform assessment (129 files)

---

## 🤝 Building a New Shell App

To build a new shell application on top of the core:

1. Create a new directory under `apps/your_app_name/`
2. Import and call `run_agent()` from `agent_workspace.agent`
3. Create temporary workspaces for each task (see `screener.py` for pattern)
4. Parse agent output for your domain-specific structure
5. Add your own UI, API, or workflow layer

The core provides: scanning, extraction, agent orchestration, memory, artifacts.
The shell provides: domain logic, UI, file watching, result parsing, storage.

---

## 🙏 Acknowledgments

- [LangGraph](https://github.com/langchain-ai/langgraph) — Agent orchestration
- [LangChain](https://github.com/langchain-ai/langchain) — LLM abstractions
- [python-docx](https://github.com/python-openxml/python-docx) — Word extraction
- [PyMuPDF](https://github.com/pymupdf/PyMuPDF) — PDF extraction
- [openpyxl](https://openpyxl.readthedocs.io/) — Excel extraction

---

**Status**: ✅ Production Ready | **Version**: 0.1.0 | **Last Updated**: 2026-03-11
