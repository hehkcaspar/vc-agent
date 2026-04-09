# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## First-Principles Thinking

Reason from raw requirements and the true nature of the problem — never from convention or templates.

1. **Challenge unclear goals.** Don't assume I know what I want. When intention, motivation or objectives are ambiguous, stop and discuss before proceeding.
2. **Suggest the optimal path.** If the goal is clear but the approach isn't the shortest or best, say so directly and propose a better one.
3. **Fix root causes, not symptoms.** When something breaks, trace it to the root cause — no band-aids. Every decision must be able to answer "why."
4. **Say only what matters.** Cut everything that doesn't change a decision.

## Project Overview

VC Portfolio Manager — a full-stack app for managing portfolio companies as canonical entities with a parking-lot ingestion workflow (no data loss). Backend is FastAPI + SQLAlchemy (async SQLite), frontend is React 18 + TypeScript + Vite + SWR.

## Development Commands

### Backend (from `backend/`)
```bash
# Install dependencies (use venv — never install globally)
python -m venv venv
source venv/bin/activate  # or .\venv\Scripts\Activate.ps1 on Windows
pip install -r requirements.txt

# Run dev server (localhost:8000)
python run.py

# Tests
pytest                                          # all tests
pytest tests/test_entities.py                   # single file
pytest --cov=app tests/                         # with coverage
RUN_E2E_LLM=1 pytest tests/test_chat_e2e_llm.py -v --tb=short  # real Gemini E2E
```

### Frontend (from `frontend/`)
```bash
npm install
npm run dev      # dev server on localhost:3000 (proxies /api/* to backend)
npm run build    # tsc + vite build → dist/
npm run preview  # preview production build
```

No frontend test runner is configured yet.

## Architecture

### Core Pattern: Entity-Canonical + Parking-Lot Ingestion
1. All inbound content persists to parking lot (`/00000/parkinglot/{ingest_id}/`) immediately
2. `EntityResolver` matches content to entities; `WorkspaceMaterializer` promotes parking lot items to workspace nodes (write to Inbox/ → write DB → delete parking)
3. Normal APIs only operate on canonical records (never raw parking lot)

### Workspace (Hierarchical File System per Entity)
Each entity has a single workspace tree that replaces the old dual Resource/Artifact model. Design doc: `doc/ENTITY_WORKSPACE_DESIGN.md`.

- **`workspace_nodes`** table — files, folders, bookmarks in a tree (path-based, with `parent_id`)
- **`workspace_ops`** table — audit log for all mutations (create, overwrite, move, delete, etc.)
- **Physical storage** — blobs at `{entity_id}/workspace/blobs/{node_id}/{filename}` (decoupled from path; moves are DB-only)
- **Versioning** — every file overwrite snapshots old content to `.versions/{node_id}/`
- **Provenance** — `origin_type` (upload|agent|ingest|shared|user) enforces write zones: agents cannot overwrite user uploads
- **Lazy scaffolding** — new entities get only `Inbox/` + `WORKSPACE_NOTES.md`. Taxonomy folders (`Data Room/`, `Data Room/Financials/`, `Data Room/Legal/`, `Technical/`, `Deliverables/`, `Deliverables/{Memos,Reports,Factsheets}/`) materialize lazily via `_ensure_parents` when files actually land in them. Full taxonomy lives as `WORKSPACE_TAXONOMY` constant in `services/workspace.py` and is consumed by Process Inbox for routing decisions — never pre-created.
- **WORKSPACE_NOTES.md** — shared file at root for cross-file context (both user and agent can edit)
- **Process Inbox** (`POST /workspace/inbox/process`) — batch intake. Walks `Inbox/` direct children:
  - **Path A** (loose files): per-file Gemini extraction (Pass 1, reuses single-file metadata_preprocess) → one synoptic Gemini call (Pass 2, `prompts/inbox_grouping.md`) sees all summaries + live destination state → groups files into named subfolders, joins existing subfolders, or marks `needs_triage`. Filename collisions inside a group auto-disambiguated (`name (1).ext`).
  - **Path B** (user-uploaded folders): one fast Gemini call from structure alone (`prompts/inbox_folder_routing.md`, no file bytes read) → `place_whole | join_existing | needs_sampling | unpack | needs_triage`. `needs_sampling` extracts up to `WORKSPACE_INTAKE_SAMPLE_SIZE` files and re-runs B1. After placement, per-file extraction is enqueued in the background via the existing single-file queue.
  - Every processed file gets an `intake_routing` metadata block (`status: routed|needs_triage|error`, `run_id`, `batch_name`, `destination`, `confidence`, `reason`) — stable contract for future triage UIs / agents.
  - Routing destinations are validated against `WORKSPACE_TAXONOMY`; LLM cannot route into `Inbox/` or arbitrary paths.
  - Service: `services/inbox_processing_jobs.py`. In-memory job registry, one active job per entity, status lost on restart (mirrors `metadata_preprocess_jobs` pattern).
- **Structured upload**: `POST /workspace/upload` (multipart, preserves `webkitRelativePath` from frontend) and `POST /workspace/upload-zip` (server-side `zipfile` unpack with `WORKSPACE_MAX_ZIP_BYTES` cap, zip-slip rejection, single-root detection to avoid double-nesting). Frontend `FileUploadModal` (in `EntityDetail.tsx`) exposes four modes — **Files / Folder / Zip / Text**. Text mode wraps pasted free-form content (email, IM, notes) into a synthetic `File` and submits through the same `POST /workspace/upload` endpoint so it flows through Process Inbox identically to regular uploads; filename defaults to `note-<ISO timestamp>.md`.

### Backend Structure (`backend/app/`)
- **`main.py`** — FastAPI app, CORS, lifespan (LangSmith guard + DB init + stuck-evaluating reset)
- **`routers/`** — `entities`, `workspace`, `chat`, `ingest`, `parkinglot`, `academic`
- **`services/`** — Domain logic:
  - `workspace.py` — `WorkspaceService` (tree queries, write/overwrite/version, move/rename, delete, copy, undo, provenance enforcement, template scaffolding, agent context builder)
  - `workspace_tools.py` — `build_workspace_tools()` — 13 LangChain agent tools for workspace operations
  - `storage.py` — `StorageAdapter` abstraction (local FS, designed for future cloud swap)
  - `parking.py` / `resolver.py` / `materializer.py` — ingestion pipeline (materializes to workspace Inbox/)
  - `direct_llm.py` / `gemini_context.py` — Gemini Interactions API + Kimi dispatch, workspace node context building (replaced legacy `gemini_runner.py`)
  - `portfolio_deep_agent.py` — LangChain Deep Agent harness (workspace tools, job polling)
  - `model_profiles.py` — model wiring (Gemini, Kimi/Moonshot)
  - `metadata_extraction.py` / `metadata_preprocess_jobs.py` — async single-file metadata enrichment for workspace nodes
  - `inbox_processing_jobs.py` — Process Inbox batch intake (Path A loose files, Path B user-uploaded folders); reuses metadata_preprocess for per-file extraction
  - `academic/` — Academic Tracking v2 module (separate from portfolio):
    - `file_utils.py` — shared dossier path, JSON/JSONL read/write helpers
    - `evaluation_service.py` — evaluation normalisation, delta computation, score extraction, background eval/refresh/comparative tasks, `running_agents` registry
    - `chat_service.py` — background chat job execution
    - `digest_service.py` — weekly portfolio digest generation via Gemini
    - `scholar_agent.py` — goal-driven Deep Agents harness (invoke_scholar_agent, invoke_scholar_chat); `_extract_text()` normalises Gemini content blocks to plain strings
    - `scholar_prompts.py` — goal prompt templates (initial eval, refresh, chat, comparative, upload processing). `build_scholar_system_prompt()` interpolates dimensions from `dimensions.py` at every invocation
    - `dimensions.py` — file-backed evaluation dimensions at `data/config/dimensions.json` (`DEFAULT_DIMENSIONS` seed, `read/write_dimensions()`, `render_dimensions_schema_block()`, `render_dimensions_rubric()`)
    - `domain_tools.py` — 12 scholar-scoped tools built via `build_scholar_tools(scholar_id)` closure pattern
    - `tool_utils.py` — pure utility functions (URL classification, name matching, title normalisation)
    - `semantic_scholar.py` — Semantic Scholar API client (rate-limited, optional key)
    - `heartbeat.py` — background scheduler for stale scholar refresh, channel polling, digest generation
    - `channel_pollers.py` — Google Scholar / Semantic Scholar change detection
- **`prompts/`** — Markdown prompt templates (extract_info, red_team, file_lookup_preprocess, inbox_grouping, inbox_folder_routing)
- **`models.py`** — SQLAlchemy ORM (entities, workspace_nodes, workspace_ops, ingest_items, conversation_sessions/messages, chat_completion_jobs)
- **`academic_database.py`** — Academic DB engine, sessions, `AcademicBase` (separate `data/academic.db`)
- **`academic_models.py`** — Academic Tracking ORM using `AcademicBase` (scholars, scholar_events, channels, chat_sessions/messages/jobs)
- **`academic_schemas.py`** — Pydantic schemas for academic endpoints (scholar CRUD, evaluations, papers, reports, events, chat, ranking, digest, custom dimensions)
- **`config.py`** — Pydantic Settings loaded from `.env`

### Frontend Structure (`frontend/src/`)
- **`App.tsx`** — Root with `TabProvider` + `ToastHost`
- **`components/Layout.tsx`** — App shell with sidebar (Portfolio + Academic tabs)
- **`components/EntityDetail.tsx`** — Entity workspace (hierarchical file tree + chat panel)
- **`components/EntityConversation.tsx`** — Chat UI with presets, agent toggle, job polling, workspace node selection
- **`components/academic/`** — Academic Tracking v2 workspace:
  - `AcademicTab.tsx` — Scholar list/ranking views, signal feed, stale alerts, digest viewer, custom dimensions modal
  - `ScholarDetail.tsx` — Scholar detail with report sidebar, content tab router, auto-refresh during evaluation
  - `EvaluationTab.tsx` — Radar chart, dimension scores with delta indicators, computed metrics, commercialisation signals
  - `PublicationsTab.tsx` — Papers table with sort (citations/year) and author position filter
  - `ProfilesTab.tsx` — Profile link cards with channel monitoring controls (pause/resume)
  - `TimelineTab.tsx` — Event timeline with significance filter; shows event date vs discovery date when they differ
  - `ScholarConversation.tsx` — Per-scholar chat with session management and async job polling
  - `RankingView.tsx` — Sortable ranking table with weight presets and comparative evaluation
  - `AddScholarModal.tsx` — Create/edit scholar modal
- **`services/api.ts`** — API client (entities, workspace, chat, ingest, parking lot)
- **`services/academicApi.ts`** — API client (scholars, chat, ranking, digests, uploads, custom dimensions)
- **`hooks/useEntities.ts`** — SWR hooks: `useEntities`, `useEntity`, `useWorkspaceTree`
- **`hooks/useAcademic.ts`** — SWR hooks for all academic data
- **`types/index.ts`** — TypeScript interfaces (Entity, WorkspaceNode, WorkspaceTreeNode, chat types)
- **`types/academic.ts`** — TypeScript interfaces + display constants (labels, colours, score helpers)
- **`lib/academicRanking.ts`** — `computeWeightedRank()` for client-side ranking
- **`lib/eventIcons.tsx`** — unified `EVENT_ICONS` map + `<EventIcon>` component (lucide) shared by `AcademicTab` signal feed and `TimelineTab`
- **Icons** — all UI chrome uses [`lucide-react`](https://lucide.dev). No emoji or HTML-entity glyphs (▶ ✏ 🗑 ▼ ×) in JSX; emoji is reserved for content (user text, LLM output, persisted event payloads). When adding an icon, import from `lucide-react` and pick a size matching nearby usage (12–20px)

### UI primitives (styles + modal wrapper)
- **`styles/variables.css`** — design tokens: colors, typography, spacing, radii, shadows, z-index, and modal widths (`--modal-w-narrow: 480px`, `--modal-w-standard: 720px`, `--modal-w-wide: min(92vw, 1152px)`)
- **`styles/primitives.css`** — single source of truth for `.modal*`, `.btn-primary|secondary|text|icon|icon-danger|sm`, `.form-group`, `.form-input`, `.form-label`, `.radio-group`. Imported once from `main.tsx`. **Do not redefine these classes in component CSS files** — add component-scoped classes instead
- **`components/ui/Modal.tsx`** — the only modal primitive. Handles overlay, Esc key, body scroll lock, header (title + `X` close), `size` prop (`narrow` | `standard` | `wide`). Every popup in the app flows through it. Children render directly inside `.modal` so `<form>` callers can wrap body+footer together
- **Adding a new popup**: import `Modal`, pass `isOpen` / `onClose` / `title` / optional `size`, and put `<div className="modal-body">…</div>` + `<div className="modal-footer">…</div>` as children. Never hand-roll `<div className="modal-overlay">`, never set inline `maxWidth`, never duplicate `.modal*` rules in component CSS
- **`context/TabContext.tsx`** — Tab state management
- Data fetching via SWR with automatic revalidation

### Chat Modes
- **One-shot** (default): synchronous Gemini call → 200 response
- **Deep Agent** (`use_deep_agent=true`): 202 response with `job_id`, background LangChain agent with 13 workspace tools, client polls for completion. Agent receives workspace tree context (file structure + descriptions + workspace notes) on every turn.
- **Preset shortcuts** (`POST /chat/presets/{id}/run`) share the same two modes. In deep-agent mode, the endpoint returns **202 + `PresetRunJobAccepted`**, persists a synthetic `▶ Run preset: <label>` user message, creates a `chat_completion_jobs` row with `preset_payload_json` populated, and runs `run_preset_agent_job` as a background task. **Poll the same chat-job endpoint** — no separate preset job endpoint. The frontend's `agentJob` polling loop + spinner status line are reused verbatim, so Red Team / Extract Info runs get the same live tool-step progress as a chat send. `extract_info` is force-pinned to one-shot; `red_team` honors the toggle.

### File preview
`FilePreview` in `components/EntityDetail.tsx` renders files in the side panel and supports an **expand-to-popup** modal (lucide `Maximize2`). Markdown files (`.md`, `text/markdown`) render via `react-markdown` + `remark-gfm`, not as raw text. The popup header has filename + **version picker dropdown** on the left (text/markdown only, fetched lazily from `GET /workspace/file/{id}/versions`; historical blob via `GET /workspace/file/{id}/versions/{version}`) and **copy-to-clipboard** + close on the right. Versions in the modal are view-only — no re-fetch on expand, modalContent overrides content when a historical version is selected. The inline side panel always shows the current version.

### Workspace Agent Tools (13 total)
Browse + organize (7): `workspace_get_tree`, `workspace_list_files`, `workspace_read_file`, `workspace_search_files`, `workspace_create_folder`, `workspace_move`, `workspace_rename`
Write + manage (6): `workspace_write_file`, `workspace_annotate`, `workspace_delete`, `workspace_file_versions`, `workspace_restore_version`, `workspace_history`

### Data Storage
- Portfolio SQLite DB at `data/vc_portfolio.db`
- Academic SQLite DB at `data/academic.db` (separate from portfolio)
- Entity workspace files at `data/entities/{entity_id}/workspace/blobs/{node_id}/`
- Version history at `data/entities/{entity_id}/workspace/.versions/{node_id}/`
- Parking lot at `data/entities/00000/parkinglot/`
- Scholar dossiers at `data/scholars/{scholar_id}/` (profile.json, papers.json, events.jsonl, channels.json, evaluations/, reports/, uploads/, agent_runs/)
- Academic config at `data/config/` (heartbeat.json, field_archetypes.json, ranking_presets/, digests/, dimensions.json)
- `data/` is gitignored

## Key Configuration

All backend config via environment variables (see `backend/.env_sample`):
- `GEMINI_API_KEY` / `GOOGLE_API_KEY` — required for AI features
- `CHAT_USE_DEEP_AGENT` — enable deep agent mode (default false)
- `CHAT_DEFAULT_MODEL_PROFILE` — `gemini_google` or `kimi_moonshot`
- `WORKSPACE_MAX_FILE_BYTES` — max file size (default 50 MB)
- `WORKSPACE_MAX_ZIP_BYTES` — max zip upload size (default 500 MB)
- `WORKSPACE_VERSION_RETENTION_DAYS` — version history retention (default 30 days)
- `WORKSPACE_INTAKE_SAMPLE_SIZE` — Path B sampling budget for ambiguous folder uploads (default 5)
- `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` — optional tracing
- `ACADEMIC_DATABASE_URL` — academic SQLite DB (default `data/academic.db`, separate from portfolio)
- `ACADEMIC_GEMINI_MODEL` — model for academic tracking (default `gemini-3-flash-preview`)
- `SEMANTIC_SCHOLAR_API_KEY` — optional; free tier works without key (1 req/sec rate limit)
- `SERPAPI_KEY` — optional; for Google Scholar metrics via SerpAPI

Frontend proxy target configurable via `VITE_PROXY_TARGET` (default `http://127.0.0.1:8000`).

## Academic Tracking Module (v2)

Separate from portfolio — scholar-centric tracking with goal-driven Deep Agents. Uses its own SQLite DB (`data/academic.db`) with `AcademicBase` declared in `app/academic_database.py`. Full design in `doc/ACADEMIC_TRACKING_V2_DESIGN.md`.

### Two-layer storage
- **Document store** (source of truth): JSON/JSONL/markdown files per scholar in `data/scholars/{id}/` — profile.json, papers.json, events.jsonl, channels.json, evaluations/*.json, reports/*.md
- **SQL index** (queryable): 3 core tables (scholars, scholar_events, channels) + 3 chat tables — rebuildable from documents via `sync_sql_index` tool

### Agent architecture
One agent factory (`invoke_scholar_agent`), one toolkit (`build_scholar_tools(scholar_id)` — 12 closure-bound tools), different goals. Initial evaluation, refresh, signal investigation, chat, comparative evaluation, and upload processing are all the same agent with different system prompts.

**Goals (background tasks via FastAPI BackgroundTasks):**
- **Initial evaluation** — identity extraction (URL-first, deterministic pre-classification), paper fetching (SS API), bibliometrics, N-dimension scoring (list lives in `data/config/dimensions.json`, editable at runtime — see "Dimensions config" below), report generation
- **Refresh** — re-fetch papers, update metrics, rescore, compute delta vs previous evaluation
- **Chat** — multi-turn conversation with scholar context and tools
- **Comparative** — side-by-side evaluation of two scholars
- **Upload processing** — agent analyses user-uploaded documents
- **Digest** — weekly portfolio summary via direct Gemini call (no agent)

### Service modules (under `services/academic/`)
- `file_utils.py` — shared `dossier_path()`, `read_json()`, `write_json()`, `append_jsonl()`
- `evaluation_service.py` — normalisation, delta, scoring, background task orchestration, `running_agents` registry
- `chat_service.py` — background chat job execution
- `digest_service.py` — weekly digest generation
- `heartbeat.py` — stale scholar refresh, channel polling, scheduled digest

### Key design decisions
- **Minimal SQL, rich documents**: SQL for cross-scholar queries/scheduling only; all agent-readable state lives in dossier files
- **URL-first identity**: Homepage links are ground truth; pre-classification extracts GS/SS/LinkedIn IDs deterministically before Gemini
- Google Scholar stats are authoritative; Semantic Scholar only fills gaps
- `@tool` decorator requires docstring as FIRST statement in function body (no logger calls before it)
- Startup hook resets stuck "evaluating" scholars to "active" (handles server restart mid-evaluation)
- **Event date vs discovery date**: `append_event` tool accepts optional `event_date` for when the event actually occurred (e.g., a company founding); `created_at` records when the system discovered it. Timeline UI shows both when they differ
- **Gemini content block handling**: `_extract_text()` in `scholar_agent.py` normalises list-of-blocks content (`[{"type": "text", "text": "..."}]`) from Gemini models into plain strings before storing in DB
- **Dimensions config (dynamic, file-backed)**: evaluation dimensions are NOT hardcoded in prompts. `services/academic/dimensions.py` owns `data/config/dimensions.json`, auto-seeded on first read with seven defaults. `scholar_prompts.build_scholar_system_prompt()` calls `read_dimensions()` every invocation and interpolates `{dimensions_schema_block}` (the JSON schema example) and `{dimensions_rubric}` (the scoring guidance) into `_BASE_PROMPT`, plus `{n_dimensions}` into goal text. CRUD exposed via `GET/POST/PUT/DELETE /academic/custom-dimensions` (route name kept for backward compatibility; the endpoint now manages the full list — there is no distinction between "built-in" and "custom"). Changes take effect on the next agent run; no restart. Caveat: ranking-preset weights in `routers/academic.py` still reference default keys by name, so deleting a default leaves that preset partially dead
- Frontend auto-refreshes (5s polling) while scholar status is "evaluating"
- Frontend is an independent tab/workspace with 6 content tabs (Report, Timeline, Evaluation, Publications, Profiles, Chat) plus list/ranking views, signal feed, and digest

## Code Style

- Python: PEP 8, type hints, async/await for I/O
- Frontend: functional React components with hooks, TypeScript strict mode
- Key abstractions to maintain: `StorageAdapter`, `WorkspaceService`, `ParkingLotManager`, `EntityResolver`, `WorkspaceMaterializer`

## Documentation

Detailed docs in `docs/`: `ARCHITECTURE.md`, `DEVELOPER_GUIDE.md`, `API_REFERENCE.md`, `TRACING.md`. Workspace design in `doc/ENTITY_WORKSPACE_DESIGN.md`. Product requirements in `doc/MVP-prd.md`.
