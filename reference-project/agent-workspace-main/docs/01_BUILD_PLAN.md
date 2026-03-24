# Agentic Workspace Processor — Build Plan

> Based on [MVP_DESIGN.md](MVP_DESIGN.md). Thinnest end-to-end slice first. Each phase ends with a testable deliverable.

---

## Package: `agent_workspace/`

### Files to Create

| File | Purpose | Reuse From |
|------|---------|------------|
| `__init__.py`, `__main__.py` | Package entry | New |
| `config.py` | Env vars + YAML workspace config | Adapt evo-1 `config.py` (dataclass + `_load_env()`) |
| `workspace.py` | Resource scanning, hash-based diff, manifest | Adapt agent_evaluator `file_scanner.py` + evo-1 `classify_file()` |
| `extractor.py` | Multi-format file extraction | Adapt evo-1 `extractors.py` + agent_evaluator byte limits |
| `context.py` | Context assembly + token budgeting | New (Pi ContextEngine-inspired) |
| `agent.py` | LangGraph ReAct agent | New |
| `memory.py` | Load/save memory files (markdown) | New |
| `prompts.py` | System prompts, template resolution | New |
| `tools/scan_resources.py` | Enumerate + classify files | New (wraps workspace.py) |
| `tools/extract_content.py` | Extract text/images from files | New (wraps extractor.py) |
| `tools/search_resources.py` | Keyword search across resources + artifacts | New |
| `tools/read_artifact.py` | Read prior agent outputs | New |
| `tools/write_artifact.py` | Create/update artifacts | New |
| `cli.py` | CLI entry point | Adapt evo-1 `main.py` (argparse + path resolution) |
| `requirements.txt` | Dependencies | New |

### Key Reference Files

- `mcp-agent-evaluation-evo-1/config.py` — Settings dataclass, `_load_env()` with multi-path dotenv
- `mcp-agent-evaluation-evo-1/extractors.py` — `extract_file_for_llm()`, `classify_file()`, encoding fallback
- `agent_evaluator/tools/file_scanner.py` — `FileScanner` class, recursive scan + classify
- `agent_evaluator/tools/docling_extractor.py` — `_extract_docx_limited()`, `_extract_excel_limited()`, byte budgeting
- `agent_evaluator/utils/llm_client.py` — `@with_retry` decorator, exponential backoff, multimodal invoke
- `agent_evaluator/agents/rating_agent.py` — `create_react_agent` usage pattern

---

## Phase 1 — Walking Skeleton

**Goal:** `python -m agent_workspace run --task "Summarize these documents"` works end-to-end.

| # | Step | Depends On | Notes |
|---|------|-----------|-------|
| 1 | Package structure: `__init__.py`, `__main__.py` | — | `__main__.py`: `from .cli import main; main()` |
| 2 | `config.py` — Settings dataclass + YAML loader | — | Env: `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_TEMPERATURE`. YAML: extraction limits, iteration cap, dir names |
| 3 | `workspace.py` — `Workspace.scan() → ResourceManifest` | — | Walk resources/, SHA-256 hash, classify by extension. No diff yet |
| 4 | `extractor.py` — `extract(path, config) → ExtractionResult` | — | .txt/.md/.json/.csv/.yaml (direct read), .docx (python-docx), .pdf (PyMuPDF). Encoding fallback: utf-8 → gbk → latin-1. Configurable char limits. *Parallel with 3* |
| 5 | `tools/` — `scan_resources`, `extract_content`, `write_artifact` | 3, 4 | `@tool`-decorated functions for LangGraph. write_artifact creates dirs + writes to `artifacts/{type}/{name}` |
| 6 | `prompts.py` — System prompt builder (minimal) | — | Capabilities list + workspace path + task injection. No templates yet. *Parallel with 3–5* |
| 7 | `agent.py` — LangGraph ReAct agent | 5, 6 | `create_react_agent` with 3 tools + ChatOpenAI from config |
| 8 | `cli.py` — `run` command | 7 | `--task "..."`, `--task-file path`, `--workspace .` (default: cwd). Resolve paths, load config, invoke agent, print result |
| 9 | `requirements.txt` | — | langgraph, langchain-openai, langchain-core, python-dotenv, python-docx, pymupdf, pyyaml |
| 10 | Manual test | 8 | Workspace with .txt + .docx + .pdf → run summarize → verify report in `artifacts/reports/` |

**Deliverable:** Agent scans folder, extracts content, reasons about it, writes markdown report.

**Decision:** Use `create_react_agent` (prebuilt) for speed. Refactor to custom StateGraph only if finer control is needed.

---

## Phase 2 — Persistence

**Goal:** Agent is context-aware across runs. Diff detection + memory + traces.

| # | Step | Depends On | Notes |
|---|------|-----------|-------|
| 11 | `workspace.py` — `save_snapshot()`, `load_snapshot()`, `diff()` | Phase 1 | Manifest → `.snapshots/manifest.json`. Diff → `{added, modified, removed, unchanged}` |
| 12 | `memory.py` — `load_memory() → dict[str, str]` | — | Read all `.md` from `artifacts/memory/`. Agent writes via write_artifact. *Parallel with 11* |
| 13 | `tools/read_artifact.py` — Read prior outputs | — | Read file from `artifacts/` by relative path. *Parallel with 12* |
| 14 | Trace logging | — | Hook LangGraph callbacks. Write `artifacts/traces/{timestamp}.json`. Format: `[{type, tool, input, output, timestamp}]`. *Parallel with 12* |
| 15 | `context.py` — Context assembly + budgeting | 11, 12 | `assemble(instructions, memory, diff, skills, settings) → messages[]`. Priority ordering. Token budget via `len(text) // 4`. MVP compaction: truncate oldest |
| 16 | Update `agent.py` + `prompts.py` | 15 | Inject diff summary + memory + skills into system prompt. Add read_artifact tool |
| 17 | Update `cli.py` | 14, 16 | Save snapshot + trace after each run |

**Deliverable:** Second run reports what changed. Memory persists across runs. Every run produces a JSON trace.

**Verification:**
1. Run with 3 files → snapshot saved
2. Add a new file → run again → agent mentions the new file
3. Check `artifacts/memory/`, `artifacts/traces/`, `.snapshots/manifest.json`

---

## Phase 3 — Conversation

**Goal:** Multi-turn chat with workspace awareness and memory.

| # | Step | Depends On | Notes |
|---|------|-----------|-------|
| 18 | JSONL conversation persistence | — | Append `{role, content, timestamp}` to `instructions/conversation.jsonl`. Load last N turns (default 20) |
| 19 | Update `context.py` | 18 | Inject conversation tail into assembled context (after memory, before settings) |
| 20 | `cli.py` — `chat` command | 19 | `python -m agent_workspace chat "message"`. Loads conversation + memory + diff, runs agent, appends response |
| 21 | `prompts.py` — Template resolution | — | `resolve_template(name, vars) → str`. Load `instructions/templates/{name}.md`, substitute `{variable}` placeholders. *Parallel with 20* |
| 22 | `cli.py` — `--template` / `--var` flags | 21 | `run --template evaluate --var criteria_file=criteria.docx` |

**Deliverable:** Chat works with sliding-window history. Templates resolve with variable substitution.

**Verification:**
1. `chat "What files are here?"` → `chat "Summarize the PDF"` → second message references first
2. `instructions/conversation.jsonl` has both exchanges
3. Create `instructions/templates/summarize.md` → `run --template summarize` works

---

## Phase 4 — Richer Extraction + Search

**Goal:** Excel, images, keyword search.

| # | Step | Depends On | Notes |
|---|------|-----------|-------|
| 23 | `extractor.py` — Excel via openpyxl | — | First 5 sheets, 100 rows, 10K char limit. Adapt agent_evaluator's `_extract_excel_limited()` |
| 24 | `extractor.py` — Image → base64 | — | MIME type detection, max 10 images per run. *Parallel with 23* |
| 25 | `tools/search_resources.py` — Keyword search | — | Substring/regex across text files in resources/ + artifacts/. Return matching paths + snippets. *Parallel with 23* |
| 26 | Register `search_resources` in agent | 25 | Add to tool list |

**Deliverable:** All file types supported. Agent can search without extracting everything.

---

## Phase 5 — Polish

**Goal:** Production-ready CLI.

| # | Step | Depends On | Notes |
|---|------|-----------|-------|
| 27 | Error handling + LLM retry | — | Adapt agent_evaluator's `@with_retry` (429/5xx, exponential backoff + jitter) |
| 28 | Progress output | — | Print agent tool calls as it works. *Parallel with 27* |
| 29 | `cli.py` — Inspection commands | — | `init` (create folder structure + default config.yaml), `diff`, `artifacts`, `memory`. *Parallel with 27* |
| 30 | `README.md` | 29 | Usage examples, installation, configuration |

**Deliverable:** All CLI commands work. Graceful error handling. README.

---

## Verification Matrix

| Phase | Test | Pass Criteria |
|-------|------|--------------|
| 1 | `run --task "Summarize"` on folder with .txt + .docx + .pdf | Report appears in `artifacts/reports/` |
| 2 | Add new file → run again | Agent mentions new file. `artifacts/traces/` + `artifacts/memory/` populated. `.snapshots/manifest.json` exists |
| 3 | Two sequential `chat` messages | Second has context of first. `instructions/conversation.jsonl` has both. Template with `{var}` resolves |
| 4 | Add .xlsx + .png to resources | Agent extracts both. `search_resources` finds keyword across files |
| 5 | `init`, `diff`, `artifacts`, `memory` commands | All produce correct output. LLM failure retries gracefully |

---

## Standing Decisions

| Decision | Rationale | Upgrade Trigger |
|----------|-----------|----------------|
| `create_react_agent` (prebuilt) | Ship walking skeleton fast | Need finer control over tool loop |
| Token counting: `len(text) // 4` | Good enough for budgeting | Accuracy matters for near-limit prompts → add tiktoken |
| Synchronous CLI | Simple, no async complexity | Streaming responses or UI integration needed |
| Workspace root = cwd | Convention over configuration | Multiple workspaces in one project |
| Flat `requirements.txt` | No packaging overhead | Distributing as installable package |
| English-only logging | Domain-agnostic | i18n demand |

---

## CLI Surface (all phases)

```bash
# Phase 1
python -m agent_workspace run --task "Summarize these documents"
python -m agent_workspace run --task-file instructions/task.md

# Phase 3
python -m agent_workspace run --template evaluate --var criteria_file=criteria.docx
python -m agent_workspace chat "What changed since last time?"

# Phase 5
python -m agent_workspace init [--dir ./workspace]
python -m agent_workspace diff
python -m agent_workspace artifacts
python -m agent_workspace memory
```

---

## Dependencies

```
langgraph>=1.0.0
langchain-openai>=0.3.0
langchain-core>=0.3.0
python-dotenv>=1.0.0
python-docx>=1.1.0
openpyxl>=3.1.0       # Phase 4
pymupdf>=1.23.0
pyyaml>=6.0
```

No Docling, no vector DB, no web framework, no database.
