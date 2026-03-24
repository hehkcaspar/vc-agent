# Agentic Workspace Processor — Agent Guide

> This file is for AI coding agents working on this codebase. It complements the human-facing README.

---

## Project Overview

**Agentic Workspace Processor** is a domain-agnostic ReAct agent that operates on file workspaces. It accepts text instructions, scans and reasons about resources, then produces persistent artifacts that accumulate value over time.

**Key Principle**: Instructions define the domain, not the code. The same codebase powers investment monitoring, resume screening, and document analysis without modification.

**Status**: Production-ready MVP (v0.1.0). All 8 unit tests and 3 E2E tests passing.

---

## Core + Shell Architecture

This project follows a **core + shell** pattern:

- **Core** (`agent_workspace/`) — A stable, domain-agnostic engine. It handles workspace management, content extraction, ReAct agent orchestration, memory, and artifact persistence. The core should change infrequently.
- **Shells** (`apps/`) — Lightweight application layers that wrap the core for specific use cases. Each shell defines its own domain logic, UI, and workflow while calling `run_agent()` from the core.

**Current shells:**
- `apps/resume_screener/` — Real-time resume screening with FastAPI + WebSocket UI
  - ⚠️ **Known Gap**: Only evaluates against the first position in `positions.json`. See [docs/07_MULTI_JD_DESIGN_PLAN.md](docs/07_MULTI_JD_DESIGN_PLAN.md).

**When modifying code**, determine whether the change belongs in core or shell:
- Core changes: new file type support, new tools, agent improvements, config changes
- Shell changes: UI, domain-specific parsing, workflow orchestration, app-specific APIs

**Integration pattern**: Shells import `run_agent()` from `agent_workspace.agent`, create temporary workspaces, and parse the agent's text output for domain-specific structure.

---

## Technology Stack

| Component | Choice | Version |
|-----------|--------|---------|
| Language | Python | 3.10+ |
| Agent Framework | LangGraph | >=0.2.0 |
| LLM Client | langchain-openai | >=0.3.0 |
| PDF Extraction | PyMuPDF | >=1.23.0 |
| DOCX Extraction | python-docx | >=1.1.0 |
| Excel Extraction | openpyxl | >=3.1.0 |
| Config | PyYAML + python-dotenv | >=6.0 / >=1.0.0 |

**No database, no web framework, no vector DB.** Everything is file-based.

---

## Project Structure

```
agent_workspace/               # CORE — domain-agnostic engine
├── __init__.py                # Version: 0.1.0
├── __main__.py                # Entry point: python -m agent_workspace
├── cli.py                     # CLI commands (init, run, scan, diff, artifacts, memory)
├── config.py                  # Environment vars + YAML workspace config
├── workspace.py               # Resource scanning, SHA-256 hashes, diff detection
├── extractor.py               # Multi-format file extraction
├── agent.py                   # ReAct agent with LangGraph + retry logic
├── prompts.py                 # System prompts and template resolution
├── utils.py                   # Retry decorator, progress callbacks
├── requirements.txt           # Dependencies
└── tools/                     # LangGraph tool wrappers
    ├── __init__.py
    ├── scan_resources.py      # Enumerate files
    ├── extract_content.py     # Extract text/images
    ├── search_resources.py    # Keyword search
    ├── read_artifact.py       # Read prior outputs
    └── write_artifact.py      # Create reports/memory

apps/                          # SHELLS — use-case-specific applications
└── resume_screener/           # Resume screening shell
    ├── backend/
    │   ├── main.py            # FastAPI app, WebSocket, API routes
    │   ├── config.py          # Screener config (imports core LLM settings)
    │   ├── watcher.py         # File watcher + resume queue
    │   ├── screener.py        # AI screening (creates temp workspace, calls core)
    │   └── requirements.txt   # Shell-specific deps (FastAPI, uvicorn)
    ├── frontend/              # SPA with WebSocket real-time updates
    │   ├── index.html
    │   ├── styles.css
    │   └── app.js
    └── sample_data/
        ├── incoming_candidate/
        ├── processed/
        ├── evaluations/
        └── jds/positions.json

tests/
├── test_phase1.py             # 8 basic functionality tests
├── test_all.py                # Comprehensive tests (Phases 1-4)
└── e2e/                       # End-to-end test data
    ├── test_set_1/            # Investment monitoring (6 PDFs)
    ├── test_set_2/            # Investment updates (2 PDFs)
    ├── test_set_3/            # Resume screening (9 files)
    ├── test_set_4/            # Job platform (129 files)
    └── workspaces/            # E2E test outputs

docs/                          # Documentation
├── 01_BUILD_PLAN.md
├── 02_MVP_DESIGN.md
├── 03_USER_GUIDE.md
├── 04_ARCHITECTURE.md
├── 05_TESTING.md
└── 06_E2E_RESULTS.md
```

---

## Build and Run Commands

### Installation

```bash
# Install dependencies
pip install -r agent_workspace/requirements.txt

# Configure environment
cp .env.example .env  # Edit and add LLM_API_KEY
```

### Running the Application

```bash
# Initialize workspace
python -m agent_workspace init --dir ./my_workspace

# Run agent task
python -m agent_workspace run --workspace ./my_workspace \
    --task "Summarize these documents"

# Run with task file
python -m agent_workspace run --task-file instructions/task.md

# Run with template
python -m agent_workspace run --template evaluate --var criteria=criteria.docx

# Other commands
python -m agent_workspace scan [--workspace .]
python -m agent_workspace diff [--workspace .]
python -m agent_workspace artifacts [--workspace .]
python -m agent_workspace memory [--workspace .]
```

### Testing

```bash
# Phase 1 tests (basic functionality - 8 tests)
python -m tests.test_phase1

# Comprehensive tests (Phases 1-4)
python -m tests.test_all

# Run from project root
python -m tests.test_phase1
```

**Expected test output**: `Passed: 8/8` or `SUMMARY: 5/5 test groups passed`

---

## Architecture Overview

### Core Flow

```
User Task → CLI → Agent.build_agent() → LangGraph ReAct → Tools → Artifacts
                                              ↓
                                        Snapshot saved
                                        Trace logged
```

### Key Components

#### 1. Workspace (`workspace.py`)
- **Scan**: Build manifest with SHA-256 hashes of all resources
- **Diff**: Compare manifests → added/modified/removed/unchanged
- **Snapshot**: Persist manifest to `.snapshots/manifest.json`

#### 2. Extractor (`extractor.py`)
File type support with configurable limits:

| Type | Extension | Method | Limit |
|------|-----------|--------|-------|
| Text | .txt, .md, .json, etc. | UTF-8 → GBK → Latin-1 fallback | 15K chars |
| PDF | .pdf | PyMuPDF | 15K chars |
| Word | .docx | python-docx | 10K chars |
| Excel | .xlsx | openpyxl | 10K chars, 5 sheets, 100 rows |
| Image | .jpg, .png, etc. | Base64 data URL | 10 per run |

#### 3. Agent (`agent.py`)
- Uses `create_react_agent` from LangGraph (prebuilt ReAct)
- 5 tools: scan_resources, extract_content, search_resources, read_artifact, write_artifact
- Retry logic: 3 attempts with exponential backoff (1s, 2s, 4s) + jitter
- Callbacks for progress output

#### 4. Configuration (`config.py`)
- **Environment**: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TEMPERATURE
- **YAML**: workspace directories, extraction limits, agent settings

---

## Code Style Guidelines

### Python Style
- Use `from __future__ import annotations` for forward references
- Type hints required for function signatures
- Prefer `pathlib.Path` over string paths
- Docstrings for all public functions

### File Organization
- One logical concern per file
- Tools in `tools/` subdirectory with `@tool` decorator
- Keep CLI commands in `cli.py`, business logic in modules

### Error Handling
- Use `@with_retry` decorator for LLM calls
- Graceful degradation for file extraction failures
- Exit codes: 0 (success), 1 (error), 130 (interrupt)

### Logging/Output
- Use `print()` for user-facing output
- Use `logging` for diagnostics
- Progress format: `[step N] Tool: name` / `Input: ...` / `Result: ...`

---

## Testing Strategy

### Unit Tests (`tests/test_phase1.py`)
1. `test_workspace_init` - Directory structure creation
2. `test_scan_empty` - Empty resource handling
3. `test_scan_with_files` - File type detection
4. `test_extract_content` - Content extraction
5. `test_write_artifact` - Artifact creation
6. `test_read_artifact` - Artifact reading
7. `test_diff_detection` - Change detection
8. `test_artifacts_and_memory_commands` - CLI commands

### E2E Tests (`tests/e2e/`)
- **Test 1**: Investment monitoring (6 PDFs) - track company over time
- **Test 2**: Resume screening (9 files) - evaluate candidates
- **Test 3**: Job platform (129 files) - operational assessment

### Adding Tests
- Use `tempfile.TemporaryDirectory()` for isolated tests
- Import from package: `from agent_workspace.cli import main`
- Assert on file existence and content

---

## Development Conventions

### Core vs Shell Boundary

**Changes to `agent_workspace/` (core)** should be:
- Domain-agnostic (no resume, investment, etc. specific logic)
- Backward-compatible (shells must not break)
- Minimal (prefer shell-side solutions when possible)

**Changes to `apps/` (shells)** can be:
- Domain-specific, opinionated, user-facing
- Free to add UI, APIs, and workflow
- Responsible for parsing agent output into structured domain objects

### Agentic vs Deterministic Boundaries

**Deterministic (code enforces)**:
- Workspace folder layout
- File extraction dispatch by extension
- Manifest format and hashing
- Token budget enforcement

**Agentic (LLM decides)**:
- Which files to extract
- Tool call sequence
- Report structure and content
- Memory organization

### Adding a New File Type
1. Add extension → type mapping in `workspace.py` `_TYPE_MAP`
2. Add extractor function in `extractor.py`
3. Add dispatch case in `extract_file()`

### Adding a New Tool
1. Create file in `tools/{tool_name}.py`
2. Add `@tool` decorator from `langchain_core.tools`
3. Import and add to tools list in `agent.py`

### Building a New Shell App
1. Create `apps/{app_name}/` with `backend/` and `frontend/` (if needed)
2. In the backend, import `run_agent` from `agent_workspace.agent`
3. Create temporary workspaces: folders with `resources/`, `instructions/`, `artifacts/`
4. Run config: `from agent_workspace.config import llm_settings` — reuse root `.env`
5. Parse agent text output into domain-specific structured results
6. Clean up temp workspaces after use
7. Add shell-specific `requirements.txt` for extra dependencies

**Known Shell Limitations**:
- `resume_screener`: Only evaluates against the first position in `positions.json` (not all positions). See [docs/07_MULTI_JD_DESIGN_PLAN.md](docs/07_MULTI_JD_DESIGN_PLAN.md) for the planned fix.
- `resume_screener`: Poor data organization - `sample_data/` mixes inputs, outputs, and runtime data. See [docs/08_DATA_ORGANIZATION_DESIGN_PLAN.md](docs/08_DATA_ORGANIZATION_DESIGN_PLAN.md) for the proposed structure.

---

## Security Considerations

### API Keys
- Stored in environment variables or `.env` file
- Never logged or written to artifacts
- Validated at startup in `llm_settings.validate()`

### File Access
- Restricted to workspace directory
- No path traversal (`../` is blocked by Path resolution)
- Symbolic links not explicitly followed

### Content Safety
- Binary files: Only images supported for LLM consumption
- Large files: Truncated to configurable limits
- No content filtering (relies on LLM provider)

---

## Configuration Reference

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LLM_API_KEY` | Yes | — | API key (also accepts `DASHSCOPE_API_KEY`, `QWEN_API_KEY`) |
| `LLM_BASE_URL` | No | DashScope | OpenAI-compatible endpoint |
| `LLM_MODEL` | No | qwen3.5-flash | Model name |
| `LLM_TEMPERATURE` | No | 0.1 | Sampling temperature |

### Workspace Config (`config.yaml`)

```yaml
workspace:
  resources_dir: resources
  instructions_dir: instructions
  artifacts_dir: artifacts
  snapshots_dir: .snapshots

extraction:
  max_text_chars: 15000
  max_images: 10
  max_excel_rows: 100
  max_excel_sheets: 5

agent:
  max_iterations: 20
  memory_turns: 20
  trace_enabled: true
```

---

## Common Tasks

### Running a Quick Test

```bash
# Create temp workspace
python -m agent_workspace init --dir /tmp/test_ws
echo "Hello world" > /tmp/test_ws/resources/test.txt

# Run agent
python -m agent_workspace run --workspace /tmp/test_ws \
    --task "What files are here?" --quiet
```

### Debugging Failed Runs

1. Check trace: `artifacts/traces/trace_*.json`
2. Check diff: `python -m agent_workspace diff`
3. Check artifacts: `python -m agent_workspace artifacts`
4. Enable verbose logging in code: `logging.basicConfig(level=logging.DEBUG)`

### Adding Memory

The agent writes memory automatically via `write_artifact` tool. Memory files are:
- Location: `artifacts/memory/*.md`
- Format: Markdown
- Loaded on next run and injected into system prompt

---

## Dependencies to Know

- **LangGraph**: ReAct agent orchestration
- **LangChain**: LLM abstractions and tool decorators
- **PyMuPDF (fitz)**: PDF text extraction
- **python-docx**: Word document extraction
- **openpyxl**: Excel spreadsheet extraction
- **PyYAML**: Workspace configuration
- **python-dotenv**: Environment variable loading

---

## Upgrade Paths (Post-MVP)

| Feature | Trigger | Approach |
|---------|---------|----------|
| Vector search | Keyword search insufficient | Add embeddings alongside file-based memory |
| Database | File state becomes query bottleneck | Migrate manifests to SQLite; artifacts stay files |
| Web UI | Multiple users needed | Wrap CLI handlers in FastAPI |
| Streaming | Real-time UI needed | LangGraph supports streaming natively |
| Plugin system | Custom extractors needed | Tool catalog with profiles |

**Do not implement until triggered by actual need.**

---

## Project Standards

- **Python**: 3.10+
- **Test Coverage**: 8 unit tests + 3 E2E scenarios
- **Documentation**: Human docs in `docs/`, this guide for agents
- **Version**: 0.1.0 (semantic versioning planned)
- **License**: MIT

---

*Last updated: 2026-03-11*
