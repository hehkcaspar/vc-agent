# Architecture Documentation

## System Overview

### Core + Shell Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  SHELL LAYER: Application-specific wrappers                     │
│  e.g. apps/resume_screener/ (FastAPI + WebSocket + Watcher)     │
├─────────────────────────────────────────────────────────────────┤
│  CORE LAYER: agent_workspace/                                   │
│                                                                 │
│  ┌───────────────────────────────────────────────┐              │
│  │             User Interface (CLI)              │              │
│  ├───────────────────────────────────────────────┤              │
│  │          Agent Controller (ReAct + LangGraph) │              │
│  ├───────────────────────────────────────────────┤              │
│  │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐│              │
│  │  │ Scan │ │Extr. │ │Search│ │Write │ │ Read ││              │
│  │  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘│              │
│  ├───────────────────────────────────────────────┤              │
│  │  Workspace · Extractor · Memory · Config      │              │
│  └───────────────────────────────────────────────┘              │
├─────────────────────────────────────────────────────────────────┤
│  STORAGE LAYER: File-based (Markdown, JSON, YAML)               │
└─────────────────────────────────────────────────────────────────┘
```

The **core** is domain-agnostic and stable. **Shells** wrap the core for specific use cases by calling `run_agent()` and parsing its output.

### Core Internal Flow

```
User Task → CLI → Agent.build_agent() → LangGraph ReAct → Tools → Artifacts
                                              ↓
                                        Snapshot saved
                                        Trace logged
```

### Shell Integration Flow

```
Shell App → Create temp workspace → Copy files to resources/
    ↓
Write domain instructions → Call run_agent()
    ↓
Parse agent text output → Domain-specific structured result
    ↓
Clean up temp workspace → Return to shell
```

## Component Details

### 1. CLI Layer (`cli.py`)

**Purpose**: Entry point for all user interactions

**Commands**:
- `init` - Initialize workspace structure
- `run` - Execute agent task
- `scan` - List resources
- `diff` - Show changes
- `artifacts` - List outputs
- `memory` - View observations

**Design Principles**:
- Single entry point with subcommands
- Consistent `--workspace` flag
- Error messages to stderr
- Exit codes for automation

### 2. Agent Layer (`agent.py`)

**Purpose**: ReAct agent with LangGraph orchestration

**Key Classes**:
- `ToolCallbackHandler` - Progress tracking during execution
- `run_agent()` - Main entry point with retry logic

**Flow**:
1. Load configuration
2. Build system prompt with context
3. Create ReAct agent with tools
4. Execute with progress callbacks
5. Save snapshot and trace

### 3. Tools Layer (`tools/`)

| Tool | Purpose | File Types | Security |
|------|---------|------------|----------|
| `scan_resources` | Enumerate files | All | — |
| `extract_content` | Extract text | PDF, DOCX, TXT, MD, XLSX, CSV, Images | — |
| `search_resources` | Keyword search | Text files | — |
| `read_artifact` | Read prior outputs | All | Path traversal protected |
| `write_artifact` | Save outputs | All | Path traversal protected |

### 4. Core Services

#### Workspace (`workspace.py`)
- **Scan**: Build manifest with SHA-256 hashes
- **Diff**: Compare manifests for changes
- **Snapshot**: Persist manifest to `.snapshots/`

#### Extractor (`extractor.py`)
- **Text files**: UTF-8 → GBK → Latin-1 fallback
- **PDF**: PyMuPDF with page limits
- **DOCX**: python-docx (paragraphs + tables)
- **DOC**: Returns informative message (python-docx cannot read legacy `.doc`)
- **Excel**: openpyxl (sheets + rows limits)
- **CSV**: stdlib csv module with truncation
- **Images**: Base64 for multimodal LLM

#### Memory (loaded via `agent.py` `_load_memory()`)
- Load from `artifacts/memory/*.md`
- Agent writes via `write_artifact` tool
- Markdown format for readability
- Note: there is no separate `memory.py` — memory loading is a function in `agent.py`

#### Config (`config.py`)
- Environment variables for secrets (API keys)
- YAML for workspace settings
- Dataclass-based with defaults

### 5. Storage Layer

**File Organization**:
```
workspace/
├── resources/           # Input files
├── instructions/        # Task definitions
├── artifacts/
│   ├── reports/         # Analysis outputs
│   ├── memory/          # Persistent observations
│   ├── skills/          # Reusable knowledge
│   └── traces/          # Execution logs
└── .snapshots/          # Change tracking
```

**Formats**:
- **Markdown** - Reports, memory, skills (human-readable)
- **JSON** - Traces, structured data (machine-readable)
- **YAML** - Configuration (human-editable)
- **JSONL** - Conversation history (append-only)

## Data Flow

### Normal Operation Flow

```
User Task
    ↓
[CLI] Parse arguments
    ↓
[Agent] Load config → Build prompt
    ↓
[Tools] scan_resources → extract_content → write_artifact
    ↓
[Workspace] Save snapshot
    ↓
[Trace] Persist execution log
    ↓
User sees result + artifact paths
```

### Over-Time Monitoring Flow

```
Initial Run
    ↓
[Workspace] Save snapshot (manifest v1)
    ↓
Generate report v1

New Files Added
    ↓
[Workspace] diff(v2, v1) → "2 files added"
    ↓
[Agent] Load prior memory → Context
    ↓
Generate report v2 (with comparison)
    ↓
Save snapshot (manifest v2)
```

## Design Decisions

### 1. File-First Storage

**Decision**: All state in files, no database

**Rationale**:
- Inspectable and hackable
- No infrastructure dependencies
- Version control friendly
- Easy backup/restore

### 2. ReAct Agent Pattern

**Decision**: Use LangGraph's `create_react_agent` (prebuilt)

**Rationale**:
- Faster to implement than custom StateGraph
- Proven pattern for tool-using agents
- Can refactor to custom if needed later

### 3. Hash-Based Diff

**Decision**: SHA-256 for change detection

**Rationale**:
- Reliable change detection
- No dependency on file modification times
- Git-like semantics without git complexity

### 4. Context Budgeting

**Decision**: Priority-ordered context assembly

**Priority**:
1. Instructions (task)
2. Diff summary (changes)
3. Memory (observations)
4. Skills (templates)
5. Conversation tail
6. Settings

**Compaction**: Truncate oldest (MVP), summarize with LLM (future)

### 5. Deterministic Boundaries

**Agentic (LLM decides)**:
- Which files to extract
- Tool call sequence
- Report structure
- Memory content

**Deterministic (code enforces)**:
- Workspace folder layout
- File extraction dispatch
- Manifest format
- Token budget enforcement

## Error Handling

### Retry Logic (`utils.py`)

```python
@with_retry(max_attempts=3, initial_delay=1.0)
def call_llm(...)
```

**Retryable**: 429, 502, 503, 504, connection errors
**Backoff**: Exponential (1s, 2s, 4s) + jitter

### Progress Output

- `[step N] Tool: name` - Tool start
- `Input: summary` - Tool input preview
- `Result: summary` - Tool output preview

### Error Categories

| Type | Handling |
|------|----------|
| Config error | Exit early with message |
| File not found | Log warning, continue |
| Extraction fail | Return error marker |
| LLM error | Retry 3x, then fail |
| User interrupt | Graceful shutdown |

## Extension Points

### Adding New File Types

1. Add extension to `_TYPE_MAP` in `workspace.py`
2. Add extractor function in `extractor.py`
3. Add dispatch case in `extract_file()`

### Adding New Tools

1. Create tool file in `tools/`
2. Add `@tool` decorator
3. Import in `agent.py`
4. Add to tools list

### Custom Agent Behavior

Replace `create_react_agent` with custom `StateGraph`:
- Define custom `State` TypedDict
- Add nodes for pre/post processing
- Implement conditional edges

## Performance Considerations

### Extraction Limits

| Type | Default Limit |
|------|--------------|
| Text files | 15,000 chars |
| PDF | 15,000 chars |
| DOCX | 10,000 chars |
| Excel | 10,000 chars, 5 sheets, 100 rows |
| Images | 10 per run |

### Context Window

- System prompt: ~2,000 tokens
- Per extracted file: ~500-2,000 tokens
- Memory: ~1,000 tokens
- **Total budget**: Configurable (default: aggressive truncation)

### Caching Strategy

- No built-in caching (MVP)
- File extraction: Re-extract each run
- Manifest: Load from disk each scan
- Future: Add LRU cache for extraction

## Security Considerations

### API Keys
- Stored in environment variables or `.env`
- Never logged or written to artifacts
- Validated at startup

### File Access
- Restricted to workspace directory
- No path traversal (`../` blocked)
- Symbolic links not followed

### Content Safety
- No content filtering (relies on LLM provider)
- Binary files: Only images supported
- Large files: Truncated to limits

---

*Last updated: 2026-03-11*
