# Agentic Workspace Processor

A general-purpose agent that operates on a workspace of files: accepts text instructions, scans and reasons about resources, then produces persistent artifacts (markdown, JSON) that accumulate value over time.

**Domain-agnostic.** Instructions define the domain, not the code.

---

## Features

- **ReAct Agent**: Dynamic tool selection — scan → extract → search → write
- **File-first Storage**: All state in files (no database needed)
- **Persistence**: Hash-based diff detects changes across runs
- **Memory**: Agent observations persist across sessions
- **Multi-format Extraction**: Text, Markdown, JSON, DOCX, PDF, Excel, images
- **Search**: Keyword search across all resources and artifacts
- **Templates**: Reusable instruction patterns with variable substitution
- **Traces**: Every run produces an inspectable JSON trace

---

## Installation

### Requirements

- Python 3.10+
- OpenAI-compatible API key (OpenAI, DashScope, etc.)

### Install Dependencies

```bash
cd agent_workspace
pip install -r requirements.txt
```

### Configure Environment

Create a `.env` file in the project root:

```bash
# Required: API key
LLM_API_KEY=your-api-key-here

# Optional: Custom base URL (default: DashScope)
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen3.5-flash
LLM_TEMPERATURE=0.1
```

Or set environment variables directly.

---

## Quick Start

### 1. Initialize a Workspace

```bash
python -m agent_workspace init --dir ./my_workspace
```

This creates the folder structure:

```
my_workspace/
├── resources/           ← Put your input files here
├── instructions/        ← Task definitions and templates
│   └── templates/
├── artifacts/           ← Agent outputs
│   ├── reports/         ← Generated reports
│   ├── memory/          ← Persistent observations
│   ├── skills/          ← Reusable knowledge
│   ├── traces/          ← Execution traces
│   └── settings/        ← Configuration
├── .snapshots/          ← File change tracking
└── config.yaml          ← Workspace settings
```

### 2. Add Resources

Copy files to the `resources/` directory:

```bash
cp ~/Documents/*.pdf my_workspace/resources/
cp ~/Documents/*.docx my_workspace/resources/
```

### 3. Run a Task

```bash
python -m agent_workspace run --workspace ./my_workspace \
    --task "Summarize these documents and identify key action items"
```

The agent will:
1. Scan the resources directory
2. Extract content from relevant files
3. Analyze and reason about the content
4. Write a report to `artifacts/reports/`

---

## CLI Commands

### `init` — Initialize Workspace

```bash
python -m agent_workspace init [--dir ./workspace]
```

Creates the workspace folder structure with default `config.yaml`.

### `run` — Run Agent Task

```bash
# Inline task
python -m agent_workspace run --task "Summarize these documents"

# Task from file
python -m agent_workspace run --task-file instructions/task.md

# Using a template
python -m agent_workspace run --template evaluate --var criteria_file=criteria.docx

# Quiet mode (no progress output)
python -m agent_workspace run --task "Analyze files" --quiet
```

### `scan` — List Resources

```bash
python -m agent_workspace scan [--workspace .]
```

Shows all files in the resources directory with their types and sizes.

### `diff` — Show Changes

```bash
python -m agent_workspace diff [--workspace .]
```

Displays what changed since the last run (added/modified/removed files).

### `artifacts` — List Artifacts

```bash
python -m agent_workspace artifacts [--workspace .]
```

Lists all generated artifacts organized by type.

### `memory` — View Memory

```bash
python -m agent_workspace memory [--workspace .]
```

Displays the agent's persistent memory notes.

---

## Configuration

### Workspace Config (`config.yaml`)

```yaml
workspace:
  resources_dir: resources
  instructions_dir: instructions
  artifacts_dir: artifacts
  snapshots_dir: .snapshots

extraction:
  max_text_chars: 15000      # Character limit for text extraction
  max_images: 10             # Max images per run
  max_excel_rows: 100        # Max rows per Excel sheet
  max_excel_sheets: 5        # Max sheets to extract

agent:
  max_iterations: 20         # Max tool calls per run
  memory_turns: 20           # Conversation history window
  trace_enabled: true        # Save execution traces
```

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LLM_API_KEY` | Yes | — | API key for LLM service |
| `LLM_BASE_URL` | No | DashScope | OpenAI-compatible endpoint |
| `LLM_MODEL` | No | qwen3.5-flash | Model name |
| `LLM_TEMPERATURE` | No | 0.1 | Sampling temperature |

---

## Agent Tools

The agent has access to these tools:

| Tool | Purpose |
|------|---------|
| `scan_resources` | List all files in resources/ with types and sizes |
| `extract_content` | Extract text from specific files |
| `search_resources` | Keyword search across all text files |
| `read_artifact` | Read previously generated artifacts |
| `write_artifact` | Create reports, memory notes, data files |

---

## Use Cases

### Event Tracking

Track changes to a single document over time:

```bash
# Initial analysis
python -m agent_workspace run --task "Analyze event planning doc and flag missing logistics"

# Follow-up after updates
python -m agent_workspace run --task "What changed? Update logistics tracking."
```

**Artifacts**: `reports/update_YYYY-MM-DD.md`, `memory/logistics.md`

### Client Updates

Monitor a folder where new files appear:

```bash
python -m agent_workspace run --task "Review new client files. Update running summary and action items."
```

**Artifacts**: `reports/client_summary.md`, `reports/action_items.md`

### Evaluation

Evaluate submissions against criteria:

```bash
# Create template
echo "Evaluate submission against: {criteria_file}" > instructions/templates/evaluate.md

# Run evaluation
python -m agent_workspace run --template evaluate --var criteria_file=rubric.docx
```

**Artifacts**: `reports/evaluation.json`, `reports/evaluation_summary.md`

### Research Synthesis

Analyze multiple PDFs and notes:

```bash
python -m agent_workspace run --task "Literature review: group papers by methodology, identify gaps"
```

**Artifacts**: `reports/lit_review.md`, `memory/references.md`

---

## Troubleshooting

### LLM API Errors

If you see API errors:

1. Check your `LLM_API_KEY` is set correctly
2. Verify `LLM_BASE_URL` matches your provider
3. The agent will retry transient errors (429, 5xx) up to 3 times

### No Files Found

Ensure files are in the `resources/` directory:

```bash
python -m agent_workspace scan
```

### Memory Not Loading

Memory files must be in `artifacts/memory/` with `.md` extension.

### Token Budget Exceeded

If you have many large files:
- Increase `max_text_chars` in `config.yaml`
- The agent selectively extracts — ensure it scans first
- Use `search_resources` to find specific content

---

## Architecture

```
RESOURCES ──▶ INSTRUCTIONS ──▶ AGENT ──▶ ARTIFACTS
   ▲                                        │
   └──────────── feedback loop ─────────────┘
```

**Resources** — Files and folders that change over time. Agent sees snapshot + diff.

**Instructions** — Task definitions, templates, conversation history.

**Agent** — ReAct loop: scan → extract → reason → produce output.

**Artifacts** — Persistent outputs: reports, memory, skills, traces.

---

## Development

### Running Tests

```bash
# Phase 1 tests (basic functionality)
python -m agent_workspace.test_phase1

# Comprehensive tests (all phases)
python -m agent_workspace.test_all
```

### Project Structure

```
agent_workspace/
├── __init__.py
├── __main__.py           # Entry point: python -m agent_workspace
├── cli.py                # CLI commands
├── config.py             # Environment + YAML config
├── workspace.py          # Resource scanning, diff, snapshots
├── extractor.py          # Multi-format file extraction
├── agent.py              # ReAct agent with LangGraph
├── prompts.py            # System prompts, templates
├── utils.py              # Retry logic, progress callbacks
├── memory.py             # Memory management (loaded via agent)
└── tools/
    ├── scan_resources.py
    ├── extract_content.py
    ├── search_resources.py
    ├── read_artifact.py
    └── write_artifact.py
```

---

## License

MIT License — See LICENSE file for details.

---

## Acknowledgments

Built with:
- [LangGraph](https://github.com/langchain-ai/langgraph) — Agent orchestration
- [LangChain](https://github.com/langchain-ai/langchain) — LLM abstractions
- [python-docx](https://github.com/python-openxml/python-docx) — Word extraction
- [PyMuPDF](https://github.com/pymupdf/PyMuPDF) — PDF extraction
- [openpyxl](https://openpyxl.readthedocs.io/) — Excel extraction
