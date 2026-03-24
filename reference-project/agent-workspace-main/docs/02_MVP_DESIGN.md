# Agentic Workspace Processor — MVP Design

> Ship the thinnest end-to-end slice. File-first storage. Late-binding structure. Let the agent improvise within boundaries.

---

## 1. What This Is

A general-purpose agent that operates on a workspace of files: accepts text instructions (one-shot or conversational), scans and reasons about resources, then produces persistent artifacts (markdown, JSON) that accumulate value over time.

Domain-agnostic. Instructions define the domain, not the code.

---

## 2. Lessons from Prior Work

Three sources inform this design: two prior evaluation projects and OpenClaw's Pi orchestration layer.

### Carry Forward

| Pattern | Source | Adaptation |
|---------|--------|------------|
| ReAct agent flexibility | agent_evaluator | General tasks can't be pre-sequenced; LLM decides tool order |
| LangGraph state threading | evo-1 | Clean TypedDict, no global mutation |
| Trace/audit logs | evo-1 | Every run → inspectable thought/action/observation JSON |
| Task prompt refinement via LLM | evo-1 | Naive user input → professional instructions via meta-prompt |
| Multi-format extraction with fallbacks | Both | Real-world files are messy; encoding chains matter |
| Structured output: JSON + Markdown | evo-1 | Machine-readable + human-readable artifacts |
| Progress callbacks | agent_evaluator | Enables real-time UI later without agent changes |
| Explicit context budgeting | Pi | Token limits per section, not "dump everything" — debuggable, tunable |
| Skills as markdown artifacts | Pi | Agent-created reusable knowledge that influences future prompts |
| Reusable instruction templates | Pi (.pi/prompts/) | Users build a library of task patterns |
| JSONL for sessions | Pi | Append-only, cheap, branchable — validated at production scale |
| Layered resolution | Pi | System < workspace < user overrides for instructions and skills |

### Drop

| Anti-pattern | Why |
|--------------|-----|
| Global mutable state | Not thread-safe, limits concurrency |
| Hardcoded Chinese / domain-specific prompts | Must be language and domain agnostic |
| Regex-based output parsing | Fragile; use structured JSON or function calling |
| Rigid fixed-step pipeline | Too inflexible for general tasks |
| Arbitrary content truncation | Context budgeting should be explicit, not a hard char limit |
| Pi's full plugin/extension system | Over-engineering for MVP; add when triggered |
| Vector embeddings for memory | File-based keyword search first; add when it hurts |

---

## 3. Core Concepts — Four Pillars

```
RESOURCES ──▶ INSTRUCTIONS ──▶ AGENT ──▶ ARTIFACTS
   ▲                                        │
   └──────────── feedback loop ─────────────┘
```

**Resources** — Files and folders that change over time. Agent sees the current snapshot + diff from last run.

**Instructions** — What the agent should do. One-shot task, conversational thread, or reusable template. Can include a criteria document, a single sentence, or an evolving conversation with long-term memory.

**Agent** — ReAct loop: scan resources → extract content → reason → produce output. Tool selection is dynamic, not pre-scripted.

**Artifacts** — Persistent outputs: reports, memory notes, learned skills, execution traces, settings. These feed back into future runs as context. The agent grows smarter over time.

---

## 4. Architecture

### Workspace Structure

```
workspace/
├── resources/                  ← User-managed input (files, folders)
├── instructions/               ← Task definitions + conversation history
│   ├── task.md                 ← Active instruction
│   ├── templates/              ← Reusable prompt templates
│   └── conversation.jsonl      ← Append-only conversation history
├── artifacts/                  ← Agent-generated outputs
│   ├── reports/                ← Reports (md, json)
│   ├── memory/                 ← Persistent observations, decisions (md)
│   ├── skills/                 ← Agent-learned reusable knowledge (md)
│   ├── traces/                 ← Execution traces (json)
│   └── settings/               ← Agent/user settings (json)
├── .snapshots/manifest.json    ← Hash-based file manifest for diff
└── config.yaml                 ← Workspace configuration
```

### Module Layout

```
agent_workspace/
├── config.py           ← Env vars + YAML workspace config
├── workspace.py        ← Resource scanning, hash-based diff, manifest
├── extractor.py        ← Multi-format file extraction
├── context.py          ← Context assembly + token budgeting
├── agent.py            ← ReAct agent (LangGraph)
├── memory.py           ← Load/save memory files (markdown)
├── prompts.py          ← System prompts, task refinement, template resolution
├── tools/              ← LangGraph tool wrappers
│   ├── scan_resources.py
│   ├── extract_content.py
│   ├── search_resources.py
│   ├── read_artifact.py
│   └── write_artifact.py
└── cli.py              ← Entry point
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| ReAct agent, not fixed pipeline | General tasks can't be pre-sequenced |
| File-backed everything | Inspectable, hackable, no infrastructure |
| Context budgeting (context.py) | Explicit token allocation per section — debuggable, tunable |
| Artifacts are first-class (including skills) | Agent can read its own prior outputs; knowledge accumulates |
| Instruction templates | Reusable prompt patterns, user-defined or agent-created |
| JSONL for conversation | Append-only, proven at scale (Pi) |
| Hash-based diff | Lightweight change detection, no git dependency |
| No hardcoded domain | Instructions define the domain, not the code |
| YAML + env vars for config | YAML for workspace settings, env vars for secrets |

---

## 5. Agentic vs Deterministic Boundaries

The AI-native MVP doctrine: *be deterministic by necessity, not by default.*

### Keep Deterministic (enforce structure)

| Concern | Why |
|---------|-----|
| Workspace folder layout | Predictable file locations for both agent and user |
| Config + secrets handling | Env vars for secrets, YAML for workspace config — boring, reliable |
| Manifest snapshots + diff | Hash-based, mechanical, no LLM needed |
| File extraction dispatch | Deterministic: file type → extractor function |
| Trace logging | Every run produces a trace — non-negotiable |
| Token budget enforcement | Context assembler enforces limits mechanically |
| Artifact path conventions | `artifacts/{type}/{name}.{ext}` — predictable, scannable |

### Keep Agentic (let the LLM decide)

| Concern | Why |
|---------|-----|
| Which files to extract | Agent scans first, then selectively extracts what matters |
| Tool call sequence | ReAct loop — agent decides scan → extract → search → write order |
| What artifacts to create | Agent decides report structure, memory notes, skill definitions |
| Memory management | Agent decides what observations are worth remembering |
| Task decomposition | Agent breaks instructions into sub-steps |
| When to ask for clarification | Agent judges if instructions are sufficient |
| Output format and structure | Agent chooses how to organize reports for the task |
| Skill creation | Agent decides when a reusable pattern is worth saving |

---

## 6. Component Contracts

Interfaces the code must satisfy. Implementation details are left to the builder.

### Workspace (workspace.py)

- `scan() → ResourceManifest` — Walk resources/, return `{path: {hash, size, type}}`
- `diff(previous) → ResourceDiff` — Compare manifests → `{added, modified, removed, unchanged}`
- `save_snapshot()` / `load_snapshot()` — Persist/load manifest to .snapshots/

### Extractor (extractor.py)

| Type | Method | Default limit |
|------|--------|---------------|
| .docx | python-docx (paragraphs + tables) | 10K chars |
| .xlsx | openpyxl (first 5 sheets, 100 rows) | 10K chars |
| .pdf | PyMuPDF (text per page) | 15K chars |
| .txt/.md/.json/.csv/.yaml | Direct read (utf-8 → gbk → latin-1 fallback) | 15K chars |
| images | Base64 for multimodal LLM | 10 per run |

Limits configurable via config.yaml. Cached per run.

### Context Assembler (context.py)

- `assemble(instructions, memory, diff, conversation_tail, skills, settings) → messages[]`
- Enforces token budget. Priority: instructions > diff > memory > skills > conversation tail > settings.
- MVP compaction: truncate oldest. Post-MVP: LLM-generated summary.

### Agent (agent.py)

LangGraph ReAct agent with tools: `scan_resources`, `extract_content`, `search_resources`, `read_artifact`, `write_artifact`.

Context injected via ContextAssembler (budget-aware): instructions + diff + memory + skills + settings.

Output: artifacts via write_artifact tool + trace log.

### Memory (memory.py)

- `load() → dict[str, str]` — Read all markdown files from artifacts/memory/
- Agent writes memory via write_artifact tool. Plain markdown, human-readable.
- Conversation history: JSONL, sliding window (default 20 turns).

### Templates (prompts.py)

- `resolve_template(name, vars) → str` — Load `instructions/templates/{name}.md`, substitute `{variable}` placeholders.
- Agent can also create templates as artifacts.

---

## 7. Build Plan

Thinnest end-to-end slice first. Each phase has a clear deliverable.

### Phase 1 — Walking Skeleton

config.py → workspace.py (scan) → extractor.py → tools (scan, extract, write) → agent.py → cli.py

**Deliverable:** `python -m agent_workspace run --task "Summarize these documents"` scans a folder, extracts content, and writes a markdown report to artifacts/reports/.

### Phase 2 — Persistence

Hash-based diff → memory load/save → read_artifact tool → trace logging → inject diff + memory into context.

**Deliverable:** Agent knows what changed since last run. Reads/writes memory. Traces are inspectable.

### Phase 3 — Conversation

JSONL history → chat CLI → conversation context injection.

**Deliverable:** Multi-turn conversation with workspace awareness and memory.

### Phase 4 — Richer Extraction + Search

Excel extraction → image support → search_resources tool.

**Deliverable:** Full file type coverage. Agent can keyword-search across resources and artifacts.

### Phase 5 — Polish

Error handling/retry → progress output → `init` command → README.

**Deliverable:** Production-ready CLI.

---

## 8. CLI

```bash
python -m agent_workspace init [--dir ./workspace]
python -m agent_workspace run --task "Analyze client updates"
python -m agent_workspace run --task-file instructions/task.md
python -m agent_workspace run --template evaluate --var criteria_file=criteria.docx
python -m agent_workspace chat "What changed since last time?"
python -m agent_workspace diff
python -m agent_workspace artifacts
python -m agent_workspace memory
```

---

## 9. Technology

| Component | Choice |
|-----------|--------|
| Orchestration | LangGraph (ReAct) |
| LLM Client | langchain-openai (ChatOpenAI) — any OpenAI-compatible API |
| Extraction | python-docx, openpyxl, PyMuPDF |
| State | LangGraph TypedDict |
| Storage | Files (md, json, jsonl, yaml) |
| CLI | argparse or click |
| Config | PyYAML + python-dotenv |

```
# requirements.txt
langgraph>=1.0.0
langchain-openai>=0.3.0
langchain-core>=0.3.0
python-dotenv>=1.0.0
python-docx>=1.1.0
openpyxl>=3.1.0
pymupdf>=1.23.0
pyyaml>=6.0
```

No Docling, no vector DB, no web framework, no database.

---

## 10. Post-MVP — Triggers & Upgrade Paths

| Feature | Trigger | Upgrade Path |
|---------|---------|-------------|
| Vector search / embeddings | Keyword search insufficient for large workspaces | Add embedding index alongside file-based memory; memory.py gets a search backend adapter |
| Multi-agent orchestration | Single ReAct agent hits complexity ceiling | Agent config already workspace-scoped; add agent registry + subagent spawning (Pi pattern) |
| Web UI / API server | Multiple users or remote access needed | Wrap cli.py handlers in FastAPI; workspace stays file-backed |
| Database | File-based state becomes a query bottleneck | Migrate manifests + settings to SQLite; artifacts stay as files |
| Streaming responses | Real-time output matters for UI | LangGraph supports streaming natively; add SSE endpoint |
| Plugin system | Custom extractors / tools needed by different users | Tool catalog with profiles (Pi pattern); extractor registry |
| Parallel extraction | Single-threaded too slow for large workspaces | asyncio or thread pool in extractor.py; agent loop stays serial |
| Context compaction via LLM | Simple truncation loses important context | Replace truncation in context.py with LLM-summarized compaction |

Don't build until the trigger fires.

---

## 11. Validation Cases

The architecture is valid if all four cases work without code changes — only instructions differ.

| Case | Resources | Instructions | Key Artifacts |
|------|-----------|-------------|---------------|
| **Event tracking** | Single .docx updated over weeks | "Track changes, summarize deltas, flag missing logistics" | `reports/update_YYYY-MM-DD.md`, memory |
| **Client updates** | Folder where new files appear | "Running summary, action items, deadlines" | `reports/client_summary.md`, `reports/action_items.md` |
| **Evaluation** | Submission docs organized by criteria | Criteria document | `reports/evaluation.json`, `reports/evaluation_summary.md` |
| **Research synthesis** | PDFs + notes | "Literature review, group by methodology, identify gaps" | `reports/lit_review.md`, memory with references |

---

## 12. Risks

| Risk | Mitigation |
|------|------------|
| Token overflow (too many files) | Context budgeting + selective extraction (scan first, extract targeted) |
| LLM output variance | Low temperature, structured prompts, trace for debugging |
| Memory grows unbounded | Agent instructed to summarize/compact; post-MVP: LLM compaction |
| Conversation history too long | Sliding window + memory summarization |
| Multimodal LLM unavailable | Graceful fallback: skip images, log path only |

---

## 13. Done Criteria

The MVP is complete when:

1. `init` creates the workspace folder structure
2. `run` scans files, extracts content, reasons, and writes at least one artifact
3. `diff` detects resource changes between runs
4. `chat` supports multi-turn conversation with workspace + memory context
5. Memory persists across runs
6. Every run produces an inspectable trace
7. No database, no web server, no heavy dependencies
