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
2. `EntityResolver` matches content to entities; `ResourceMaterializer` promotes parking lot items to canonical resources (copy → verify → write DB → delete parking)
3. Normal APIs only operate on canonical records (never raw parking lot)

### Backend Structure (`backend/app/`)
- **`main.py`** — FastAPI app, CORS, lifespan (LangSmith guard + DB init + stuck-evaluating reset)
- **`routers/`** — `entities`, `chat`, `ingest`, `parkinglot`, `academic`
- **`services/`** — Domain logic:
  - `storage.py` — `StorageAdapter` abstraction (local FS, designed for future cloud swap)
  - `parking.py` / `resolver.py` / `materializer.py` — ingestion pipeline
  - `gemini_runner.py` / `gemini_context.py` — Gemini API calls
  - `portfolio_deep_agent.py` — LangChain Deep Agent harness (async tools, job polling)
  - `artifact_service.py` / `artifact_editing.py` — artifact CRUD + Option B edit pipeline with audit log
  - `model_profiles.py` — model wiring (Gemini, Kimi/Moonshot)
  - `metadata_extraction.py` / `metadata_preprocess_jobs.py` — async metadata enrichment
  - `academic/` — Academic Tracking v2 module (separate from portfolio):
    - `file_utils.py` — shared dossier path, JSON/JSONL read/write helpers
    - `evaluation_service.py` — evaluation normalisation, delta computation, score extraction, background eval/refresh/comparative tasks, `running_agents` registry
    - `chat_service.py` — background chat job execution
    - `digest_service.py` — weekly portfolio digest generation via Gemini
    - `scholar_agent.py` — goal-driven Deep Agents harness (invoke_scholar_agent, invoke_scholar_chat); `_extract_text()` normalises Gemini content blocks to plain strings
    - `scholar_prompts.py` — goal prompt templates (initial eval, refresh, chat, comparative, upload processing)
    - `domain_tools.py` — 12 scholar-scoped tools built via `build_scholar_tools(scholar_id)` closure pattern
    - `tool_utils.py` — pure utility functions (URL classification, name matching, title normalisation)
    - `semantic_scholar.py` — Semantic Scholar API client (rate-limited, optional key)
    - `heartbeat.py` — background scheduler for stale scholar refresh, channel polling, digest generation
    - `channel_pollers.py` — Google Scholar / Semantic Scholar change detection
- **`prompts/`** — Markdown prompt templates (extract_info, red_team, file_lookup_preprocess)
- **`models.py`** — SQLAlchemy ORM (entities, resources, artifacts, ingest_items, conversation_sessions/messages, chat_completion_jobs, artifact_edit_events)
- **`academic_database.py`** — Academic DB engine, sessions, `AcademicBase` (separate `data/academic.db`)
- **`academic_models.py`** — Academic Tracking ORM using `AcademicBase` (scholars, scholar_events, channels, chat_sessions/messages/jobs)
- **`academic_schemas.py`** — Pydantic schemas for academic endpoints (scholar CRUD, evaluations, papers, reports, events, chat, ranking, digest, custom dimensions)
- **`config.py`** — Pydantic Settings loaded from `.env`

### Frontend Structure (`frontend/src/`)
- **`App.tsx`** — Root with `TabProvider` + `ToastHost`
- **`components/Layout.tsx`** — App shell with sidebar (Portfolio + Academic tabs)
- **`components/EntityDetail.tsx`** — Entity workspace (resources + chat + artifacts)
- **`components/EntityConversation.tsx`** — Chat UI with presets, agent toggle, job polling
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
- **`services/academicApi.ts`** — API client (scholars, chat, ranking, digests, uploads, custom dimensions)
- **`hooks/useAcademic.ts`** — SWR hooks for all academic data (reports, papers, evaluations, events, channels, chat sessions, signal feed, ranking, weight presets, digests, custom dimensions)
- **`types/academic.ts`** — TypeScript interfaces + display constants (labels, colours, score helpers)
- **`lib/academicRanking.ts`** — `computeWeightedRank()` for client-side ranking
- **`context/TabContext.tsx`** — Tab state management
- Data fetching via SWR with automatic revalidation

### Chat Modes
- **One-shot** (default): synchronous Gemini call → 200 response
- **Deep Agent** (`use_deep_agent=true`): 202 response with `job_id`, background LangChain agent with tools (list/read resources/artifacts, create/edit artifacts), client polls for completion

### Data Storage
- Portfolio SQLite DB at `data/vc_portfolio.db`
- Academic SQLite DB at `data/academic.db` (separate from portfolio)
- Entity files at `data/entities/{entity_id}/`
- Parking lot at `data/entities/00000/parkinglot/`
- Scholar dossiers at `data/scholars/{scholar_id}/` (profile.json, papers.json, events.jsonl, channels.json, evaluations/, reports/, uploads/, agent_runs/)
- Academic config at `data/config/` (heartbeat.json, field_archetypes.json, ranking_presets/, digests/, custom_dimensions.json)
- `data/` is gitignored

## Key Configuration

All backend config via environment variables (see `backend/.env_sample`):
- `GEMINI_API_KEY` / `GOOGLE_API_KEY` — required for AI features
- `CHAT_USE_DEEP_AGENT` — enable deep agent mode (default false)
- `CHAT_DEFAULT_MODEL_PROFILE` — `gemini_google` or `kimi_moonshot`
- `CHAT_ARTIFACT_DEFAULT_EDIT_MODE` — `versioned` or `overwrite`
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
- **Initial evaluation** — identity extraction (URL-first, deterministic pre-classification), paper fetching (SS API), bibliometrics, 7-dimension scoring, report generation
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
- Frontend auto-refreshes (5s polling) while scholar status is "evaluating"
- Frontend is an independent tab/workspace with 6 content tabs (Report, Timeline, Evaluation, Publications, Profiles, Chat) plus list/ranking views, signal feed, and digest

## Code Style

- Python: PEP 8, type hints, async/await for I/O
- Frontend: functional React components with hooks, TypeScript strict mode
- Key abstractions to maintain: `StorageAdapter`, `ParkingLotManager`, `EntityResolver`, `ResourceMaterializer`

## Documentation

Detailed docs in `docs/`: `ARCHITECTURE.md`, `DEVELOPER_GUIDE.md`, `API_REFERENCE.md`, `TRACING.md`. Product requirements in `doc/MVP-prd.md`.
