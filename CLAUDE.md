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

### System Dependencies
- **LibreOffice** — required for legacy office format support (doc/ppt/xls → text extraction). Install: `brew install --cask libreoffice` (macOS) / `apt install libreoffice-core` (Debian/Ubuntu). The `soffice` binary must be on PATH.
- **Ghostscript** — required for PDF compression (reduces token cost, enables oversized PDFs). Install: `brew install ghostscript` (macOS) / `apt install ghostscript` (Linux). The `gs` binary must be on PATH.

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
Each entity has a single workspace tree that replaces the old dual Resource/Artifact model. Design doc: `docs/design/ENTITY_WORKSPACE_DESIGN.md`.

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
- **`main.py`** — FastAPI app, CORS, lifespan (LangSmith guard + DB init + stuck-evaluating reset + secret redactor for httpx logs + v2 migration)
- **`routers/`** — `entities` (CRUD + `deal_stage` + derived `last_content_at` on detail endpoint), `workspace`, `chat`, `ingest`, `parkinglot`, `settings` (fund registry CRUD + legal-template catalog GET + per-template text GET + legal-review checklist GET/PUT), `academic` (scholar CRUD, evaluate/refresh/stop, papers, evaluations, events, channels, chat sessions/jobs, signal feed, ranking, digests, custom dimensions, identity source CRUD, continuous-tasks catalog/patch/run-now, eval-log)
- **`services/`** — Domain logic:
  - `workspace.py` — `WorkspaceService` (tree queries, write/overwrite/version, move/rename, delete, copy, undo, provenance enforcement, template scaffolding, agent context builder)
  - `workspace_tools.py` — `build_workspace_tools()` — 13 LangChain agent tools for workspace operations
  - `storage.py` — `StorageAdapter` abstraction (local FS, designed for future cloud swap)
  - `parking.py` / `resolver.py` / `materializer.py` — ingestion pipeline (materializes to workspace Inbox/)
  - `direct_llm.py` / `gemini_context.py` — Gemini Interactions API + Kimi dispatch; `build_context_parts` (one-shot Gemini, with PDF compression via ghostscript), `build_harness_user_attachment_text` (one-shot Kimi, pypdf text extraction), `build_selected_files_pointer_list` (agent mode, metadata-only pointer table — no file content inlined)
  - `office_extractors.py` — text extraction for OOXML (docx/pptx/xlsx via zipfile XML) + legacy office (doc/ppt/xls via LibreOffice headless conversion)
  - `document_text.py` — `extract_pdf_text` (pypdf page-by-page) + `compress_pdf` (ghostscript /ebook→/screen)
  - `agent_harness.py` — ReAct agent harness (`langchain.agents.create_agent` with SummarizationMiddleware + PatchToolCallsMiddleware, workspace tools only); shared utilities (`history_to_lc_messages`, `_build_agent_core`). Passes `model_profile_id` to tools for format-aware file handling
  - `deep_agent_compat.py` — Legacy Deep Agent compat (removable). Uses `deepagents.create_deep_agent` which adds SDK built-in tools. Delete this file + `deepagents` from requirements.txt to fully remove
  - `model_profiles.py` — model wiring (Gemini, Kimi/Moonshot)
  - `metadata_extraction.py` / `metadata_preprocess_jobs.py` — async single-file metadata enrichment for workspace nodes; also provides Tier 1-3 entity-level `validate_entity_metadata` + `merge_entity_metadata` used by the `extract_info` post-processing path
  - `inbox_processing_jobs.py` — Process Inbox batch intake (Path A loose files, Path B user-uploaded folders); reuses metadata_preprocess for per-file extraction
  - `funds_config.py` — Pydantic loader/writer for `data/config/funds.json` (Taihill fund registry, atomic tmp → os.replace). Consumed by `routers/settings.py`, referenced by `entities.metadata_json._positions[].fund_id`, AND injected into every portfolio chat/agent system prompt as a "GP identity" block via `prompt_assembly._format_gp_identity_block()` — lets the LLM map cap-table signatories to "us" and populate `our_position.investor_entity` / `fund_id` fields (e.g., in the `legal_review` preset). Mirrors `services/academic/continuous_config.py` pattern
  - `legal_templates_config.py` / `legal_template_tools.py` — **Legal Review preset, Tier R1.** Raw reference corpus (YC SAFE + NVCA templates) ships as files under `backend/app/legal_templates/{yc_safe,nvca}/` (source `.docx`/`.doc`/`.pdf` + extracted `.txt`). Catalogued in `data/config/legal_templates.json` (metadata only — id, label, category, paths). Dedicated agent tool `legal_template_read(template_id)` fetches raw text on demand; the prompt gets only the catalog pointer. Regenerate the `.txt` files via `backend/scripts/build_legal_templates.py`. Seeded on startup by `ensure_legal_templates_seed()`
  - `legal_review_checklist_config.py` — **Legal Review preset, Tier R2.** Distilled, structured review checklist at `data/config/legal_review_checklist.json` (categories → items → standard_value + red_flag_patterns + scenario_focus). Synthesised from internal investment-checklist xlsx + 2025-2026 VC term-sheet research. Injected in full into the legal_review prompt via `render_legal_review` (placeholder `{{review_checklist}}`). User-tunable post-launch without code deploy. Seeded on startup by `ensure_legal_review_checklist_seed()`
  - `academic/` — Academic Tracking v2 module (separate from portfolio). Uses the Scholar Evaluation Framework (`docs/design/SCHOLAR_EVALUATION_FRAMEWORK.md`): 4 MECE dimensions, 3-layer continuous monitoring, `google-genai` SDK (no langchain in this module):
    - `file_utils.py` — dossier path, JSON/JSONL read/write, four JSONL primitives (`append_record`, `read_records`, `fold_records`, `latest_record`) with per-scholar asyncio write lock
    - `locks.py` — per-scholar `asyncio.Lock` registry (single-process; file-lock needed for multi-worker)
    - `continuous_config.py` — Pydantic-validated loader + atomic writer for `data/config/continuous_tasks.json` (Layer 2 sources + Layer 3 tasks + cadences). `load_continuous_tasks()` for the Pydantic model, `load_raw_continuous_tasks()` for mutation, `write_continuous_tasks()` validates then writes atomically via `tmp → os.replace`
    - `identity_resolver.py` — multi-pass identity resolution for new scholars: homepage crawl → grounded Gemini discovery → deterministic URL parsing → per-source LLM verification (`identity_verifier.py`) → GS verification (SerpAPI + direct-scrape + `search_gs_by_papers` fallback, each candidate LLM-verified) → SS resolution (Tier 1 top-K name search + Tier 2 paper search, each candidate passes cheap pre-filter `verify_ss_metrics` then LLM verifier) → ORCID verification (`orcid_client.py` enrichment + LLM) → homepage text verification. Rejected candidates are persisted in `profile.json.rejected_identity` so future runs skip them. Writes 10+ identity sources to profile.json
    - `identity_verifier.py` — LLM-based per-source identity verification gate. `IdentityVerifier` class with per-run cache, `ScholarContext` context builder, `IdentityVerdict` structured output. Every high-signal source (GS, SS, ORCID, homepage) must pass this gate before being committed. Low-confidence matches (`<0.6`) are committed but flagged `llm_low_confidence` for UI review. Rejections append to `rejected_identity` with reason + timestamp
    - `orcid_client.py` — minimal public-ORCID fetcher (`pub.orcid.org` API, no auth): name, biography, employments, work titles. Used only by the identity verifier for LLM enrichment
    - `evaluation_service.py` — thin façade: `bootstrap_scholar()` runs identity → Layer 2 sources (parallel) → phase classifier → Layer 3 dim evals (parallel) → narrative synthesizer. All setup (config load, claim, name lookup) runs inside the outer try/finally so the evaluating lock is always released even if bootstrap crashes before reaching the pipeline steps. Atomic `claim_evaluating` / `release_evaluating` cross-process lock via scholar status column
    - `dim_runner.py` — generic Layer 3 dim eval: triage (cold-start skip) → scoring via `generate_structured` → red-flag caps → append to `evaluations/{dim_id}.jsonl`
    - `phase_classifier.py` — two-pass R1-R4 classification: grounded search discovery → structured synthesis into `peer_group.jsonl`
    - `narrative_synthesizer.py` — cross-dim narrative + open questions → `narrative.jsonl`
    - `scholar_chat.py` — Gemini Interactions API agentic chat: function-calling tools (read_fact_store, read_dim_history, trigger_refresh, log_event) + google_search, client-driven tool loop, `previous_interaction_id` chain persistence
    - `chat_service.py` — background job wrapper for scholar_chat, stores `last_interaction_id` on session
    - `llm_client.py` — self-contained `google-genai` wrapper (no portfolio imports): `generate_structured` (Path 1), `grounded_generate_text` / `grounded_search_json` (Path 2), `interactions_create` (Path 3)
    - `schemas.py` — Pydantic response schemas: `DimEvalResult`, `TriageResult`, `PhaseClassificationResult`, `NarrativeReport`, `RedFlagDetection`, etc.
    - `fact_store.py` — read-only aggregate: `current_state()` bundles profile/papers/grants/patents/startups/attributed_metrics/peer_group/red_flags. Red-flag projection via `fold_records`. Severity caps per Concept 7
    - `attributed_metrics.py` — author-position-weighted citation metrics (Concept 4): per-paper weights, first/last h-index, inflation flags. Returns `missing_data` when SS id is absent
    - `refresh_dispatcher.py` — `trigger_refresh(source, scholar_id)` with in-flight per-(scholar, source) dedupe
    - `events_sync.py` — `log_event()` creates `ScholarEvent` SQL rows (single source of truth, no JSONL). Dual-date contract: `created_at` (auto, when collected) vs `event_date` (caller-supplied, when it actually happened — NULL if unknown). Sources must parse real dates from upstream data (e.g. `published_date` from news, `year` from papers) and pass as `event_date`. All event writers (sources, heartbeat, upload_processor, scholar_chat) route through `log_event()`
    - `upload_processor.py` — structured-output upload analysis → profile patch + timeline events (via `log_event`)
    - `sources/` — Layer 2 source fetchers (one module per external API): `semantic_scholar_papers` (passes paper year as `event_date`), `google_scholar_stats` (SerpAPI + direct-scrape fallback + `search_gs_by_papers`), `patents_lens` (scaffold), `news_web` (grounded search + URL dedupe, parses `published_date` as `event_date`), `crunchbase_startups` (scaffold, disabled), `red_flags_watch` (grounded search with VC-calibrated severity). **Snapshot contract**: every `run()` must call `record_snapshot()` on ALL exit paths (including early returns for missing ids or fetch errors) so the heartbeat cadence timer resets — otherwise `_is_source_due` returns True every tick
    - `dimensions.py` — file-backed 4-dim config at `data/config/dimensions.json` (Academic Excellence, Tech-transfer Experience, Founder Potential, Growth Trajectory)
    - `tool_utils.py` — pure utility functions: `classify_urls` (10 source shapes: GS/SS/ORCID/DBLP/arXiv/OpenReview/LinkedIn/GitHub/Twitter/homepage), `names_match` (unicode + initial + prefix aware), `verify_ss_metrics` (cheap pre-filter: rejects zero-metric candidates against strong anchors; the LLM verifier is the final gate), `KNOWN_IDENTITY_SOURCES` + `HIGH_SIGNAL_IDENTITY_SOURCES` frozen sets. Note: `KNOWN_IDENTITY_SOURCES` is used by the identity resolver for automated discovery — the identity CRUD endpoints accept arbitrary source_ids (user-defined custom sources)
    - `semantic_scholar.py` — Semantic Scholar API client (rate-limited, optional key)
    - `heartbeat.py` — unified dispatcher: reads `continuous_tasks.json` per tick, claims evaluating lock, dispatches Layer 2 + Layer 3 per scholar cadence; also runs legacy channel polling (events routed through `log_event`). Module-level `get_heartbeat_status()` exposes liveness (last tick timestamp) for the Tasks view
    - `eval_log.py` — structured append-only JSONL evaluation log at `data/logs/evaluation.jsonl`. Every pipeline step emits `start` + terminal (`done|error|cancelled`). `log_step` async context manager for automatic timing. `read_tail_jsonl` for bounded reads. Auto-rotation on startup
    - `channel_pollers.py` — Google Scholar / Semantic Scholar change detection (legacy, kept for signal feed)
- **`prompts/`** — Markdown prompt templates (extract_info, red_team, legal_review, file_lookup_preprocess, inbox_grouping, inbox_folder_routing)
- **`models.py`** — SQLAlchemy ORM (entities, workspace_nodes, workspace_ops, ingest_items, conversation_sessions/messages, chat_completion_jobs). `entities.metadata_json` TEXT column holds extracted VC metadata (populated by `extract_info` preset, synced from `Company Profile.json`; also carries user-edited `_positions[]` and founder `status` flags, plus `legal_reviews[]` populated by the `legal_review` preset and synced from `Legal Review.json`). `entities.deal_stage` TEXT column (default `diligence`): `prospect | diligence | portfolio | passed | exited` — lifecycle, distinct from `status` (archival visibility). Both migrations run in `main.py` lifespan as idempotent ALTER TABLE checks.
- **`academic_database.py`** — Academic DB engine, sessions, `AcademicBase` (separate `data/academic.db`)
- **`academic_models.py`** — Academic Tracking ORM using `AcademicBase` (scholars, scholar_events, channels, chat_sessions/messages/jobs)
- **`academic_schemas.py`** — Pydantic schemas for academic endpoints (scholar CRUD, papers, events, chat, ranking, digest, custom dimensions, identity source upsert/delete with arbitrary source_ids, continuous-task patch). Legacy evaluation/report schemas removed in v2 — the `/evaluations` endpoint returns an untyped dict; typed shapes live in `services/academic/schemas.py`
- **`config.py`** — Pydantic Settings loaded from `.env`

### Frontend Structure (`frontend/src/`)
- **`App.tsx`** — Root with `TabProvider` + `ToastHost`
- **`components/Layout.tsx`** — App shell with sidebar (Portfolio + Academic + Settings tabs; theme toggle in footer)
- **`components/Settings/`** — Unified Settings page (`SettingsPage` + `SettingsNav` + `sections/*`). Two-column Claude-style layout: left categorical nav (PORTFOLIO / ACADEMIC / APPLICATION groups), right section content. Categories: Funds (CRUD table + add/edit modal), Legal Review Checklist (structured viewer + Edit-as-JSON modal), Legal Templates (catalog grid + click-to-preview extracted text), Continuous Tasks (embeds `TasksView`), Custom Dimensions / Ranking Presets (link out to Academic tab for now), Appearance (Light / Dark / Match system), About. Uses `components/ui/Modal.tsx` + `styles/primitives.css` + `styles/variables.css` — no hand-rolled styles.
- **`components/EntityDetail.tsx`** — Entity workspace (rich header via `<EntityHeader>` + `<EntityEditModal>`, hierarchical file tree, chat panel). Loads fresh entity detail via `useEntity` for `last_content_at`; `handleDealStageChange` PATCHes the entity directly
- **`components/EntityHeader.tsx`** — Rich header: deal-stage `TagMenu` badge, website link, edit button (right-aligned), one-liner, founder chips (strike-through for `status: "departed"`), metric-badge row (Round / Invested / MOIC / Last update). Invested + MOIC chips only render for `deal_stage ∈ {portfolio, exited}`. Aggregates `metadata._positions[]` for the Invested total and MOIC ratio; uses `lib/moicColor.ts` for the colour band
- **`components/EntityEditModal.tsx`** — Single modal with three sections: deal-stage radios, positions list (fund dropdown with inline `+ Add new fund…` that POSTs to `/settings/funds`, invested amount + currency + current value + round + instrument + date + notes), founder active/departed toggles. On save, PATCHes `deal_stage` + the full `metadata_json` (clones `entity.metadata` before overriding `_positions` + founder `status`, so extract_info fields are preserved)
- **`components/EntityConversation.tsx`** — Chat UI with presets, segmented Chat/Agent toggle, job polling, workspace node selection, optimistic user messages, inline thinking bubble
- **`components/academic/`** — Academic Tracking v2 workspace:
  - `AcademicTab.tsx` — Scholar list/ranking/tasks views (tri-toggle), signal feed (shows event date + "discovered X ago" when dates differ), stale alerts, digest viewer, custom dimensions modal, activity log modal
  - `ScholarDetail.tsx` — Scholar detail header with evaluation controls (Run/Stop/Confirmation modal), content tab router, auto-refresh during evaluation
  - `EvaluationTab.tsx` — Radar chart (4 dims), stable dimension cards grid with expandable detail panel (evidence/uncertainty/questions), peer group block, red flags banner
  - `ReportTab.tsx` — Full-width synthesized narrative report with an inline version-history picker dropdown
  - `PublicationsTab.tsx` — Full-width papers table with sort (citations/year) and author position filter
  - `ProfilesTab.tsx` — Full-width profile table (all identity sources: GS/SS/ORCID/DBLP/LinkedIn/GitHub/Twitter/homepage + user-defined custom sources) with monitoring status dots, pause/play controls, inline edit/add/delete via `EditProfileModal`, low-confidence amber badges for LLM-unverified sources. Delete supports optional blacklisting (adds to `rejected_identity`). Profile links only appear here (not duplicated in the header). Custom sources get title-cased labels derived from their snake_case key
  - `EditProfileModal.tsx` — Add/edit identity source modal (source picker with "Other..." option for arbitrary custom sources, URL + optional id inputs, GS/SS warning banner). Custom sources prompt for a free-text name (normalized to snake_case `source_id`). User edits bypass LLM verification and set `verified_by: user_edit`
  - `TimelineTab.tsx` — Event timeline with significance filter and sort toggle (Discovered vs Event date). Shows event date vs discovery date when they differ; sort by event_date pushes NULL dates last
  - `ScholarConversation.tsx` — Per-scholar chat with session management and async job polling
  - `RankingView.tsx` — Sortable ranking table with weight presets and comparative evaluation
  - `TasksView.tsx` — Continuous-tasks management page. Three sections (Layer 2 Sources / Layer 3 Dimensions / Layer 3 System) with heartbeat liveness strip. Per-task: inline cadence edit (Enter/Esc), enable toggle, 7d run count, health dot (green/amber/red), run-now button. Expandable detail panel with description, required sources, models, last run, last error. All class names use `ct-*` prefix to avoid collisions with the scholar-list `.task-*` classes
  - `AddScholarModal.tsx` — Create/edit scholar modal
- **`services/api.ts`** — API client (entities, workspace, chat, ingest, parking lot, **settings** — `getFunds`, `upsertFund`, `deleteFund`)
- **`services/academicApi.ts`** — API client (scholars, chat, ranking, digests, uploads, custom dimensions, identity source CRUD, continuous-tasks catalog + patch + run-now)
- **`hooks/useEntities.ts`** — SWR hooks: `useEntities`, `useEntity`, `useWorkspaceTree`, `useFunds`
- **`hooks/useAcademic.ts`** — SWR hooks for all academic data including `useContinuousTasks` (10s poll for the Tasks view)
- **`types/index.ts`** — TypeScript interfaces (Entity with `deal_stage` + `last_content_at`, WorkspaceNode, WorkspaceTreeNode, chat types, `Fund`, `FundsConfig`, `FounderEntry`, `EntityPosition`, `DealStage` enum)
- **`lib/moicColor.ts`** — `getMoicColor`/`formatMoic` for MOIC chip colour bands (≥2× green / ≥1× neutral / <1× red)
- **`lib/relativeTime.ts`** — `formatRelativeTime` for "3d ago" labels (Last update chip)
- **`types/academic.ts`** — TypeScript interfaces + display constants (labels, colours, score helpers)
- **`lib/academicRanking.ts`** — `computeWeightedRank()` for client-side ranking
- **`lib/eventIcons.tsx`** — unified `EVENT_ICONS` map + `<EventIcon>` component (lucide) shared by `AcademicTab` signal feed and `TimelineTab`
- **Icons** — all UI chrome uses [`lucide-react`](https://lucide.dev). No emoji or HTML-entity glyphs (▶ ✏ 🗑 ▼ ×) in JSX; emoji is reserved for content (user text, LLM output, persisted event payloads). When adding an icon, import from `lucide-react` and pick a size matching nearby usage (12–20px)

### UI primitives (styles + modal wrapper)
- **`styles/variables.css`** — design tokens: colors, typography, spacing, radii, shadows, z-index, and modal widths (`--modal-w-narrow: 480px`, `--modal-w-standard: 720px`, `--modal-w-wide: min(92vw, 1152px)`)
- **`styles/primitives.css`** — single source of truth for `.modal*`, `.btn-primary|secondary|text|icon|icon-danger|sm`, `.form-group`, `.form-input`, `.form-label`, `.radio-group`, and `.tag-menu*` (base dropdown pill — tone classes like `.status-*`, `.priority-*`, `.deal-stage-*` live with each feature tab's CSS). Imported once from `main.tsx`. **Do not redefine these classes in component CSS files** — add component-scoped classes instead
- **`components/ui/Modal.tsx`** — the only modal primitive. Handles overlay, Esc key, body scroll lock, header (title + `X` close), `size` prop (`narrow` | `standard` | `wide`). Every popup in the app flows through it. Children render directly inside `.modal` so `<form>` callers can wrap body+footer together
- **Adding a new popup**: import `Modal`, pass `isOpen` / `onClose` / `title` / optional `size`, and put `<div className="modal-body">…</div>` + `<div className="modal-footer">…</div>` as children. Never hand-roll `<div className="modal-overlay">`, never set inline `maxWidth`, never duplicate `.modal*` rules in component CSS
- **`context/TabContext.tsx`** — Tab state management
- Data fetching via SWR with automatic revalidation

### Chat Modes (tri-state: `agent_mode` field)
- **Chat** (`agent_mode: "one_shot"`): synchronous Gemini call → 200 response. Files are inlined into the prompt: Gemini receives native binary (PDFs compressed via ghostscript, images as-is). Capped at `MAX_ATTACHMENTS = 10` files, `MAX_TEXT_CHARS = 200_000` per file (constants in `gemini_context.py`). Frontend enforces the file-count limit: blocks selection beyond 10, trims excess on mode switch with a toast. Context line shows `"N/10 sources"`.
- **Agent** (`agent_mode: "react"`, default when agent enabled): 202 response with `job_id`, background agent via `langchain.agents.create_agent` with 13 workspace tools + SummarizationMiddleware + PatchToolCallsMiddleware. **No SDK built-in tools** — only workspace tools. Agent receives a pointer list (path, type, size, description) of user-selected files plus the full workspace tree context. Uses `workspace_read_file` on demand: Gemini gets compressed native PDF binary (base64 content blocks) and native images; Kimi gets pypdf text. No file count limit.
- **Deep Agent** (`agent_mode: "deep_agent"`, legacy compat): Same async pattern but uses `deepagents.create_deep_agent` which adds 9 SDK built-in tools (read_file, write_file, ls, etc.) alongside workspace tools. **Removable** — delete `deep_agent_compat.py` + `deepagents` from requirements.txt. Falls back to ReAct automatically via `DEEP_AGENT_AVAILABLE` guard.
- **Preset shortcuts** (`POST /chat/presets/{id}/run`) share all three modes. The endpoint returns **202 + `PresetRunJobAccepted`** (for agent modes), persists a synthetic `▶ Run preset: <label>` user message, creates a `chat_completion_jobs` row with `preset_payload_json` + `agent_mode` populated, and runs `run_preset_agent_job` as a background task. **Poll the same chat-job endpoint** — no separate preset job endpoint.
  - `extract_info` — **force-`react`** server-side. Agent browses the workspace, picks files autonomously (no user selection needed), extracts Tier 1-3 VC metadata (~26 fields: identity + team + deal/funding + signals), writes `Company Profile.json` at the workspace root, and post-processing syncs the JSON into `Entity.metadata_json` + auto-updates `Entity.name` / `Entity.website` when extraction finds better values. On re-runs, `render_extract_info` injects the previous extraction as incremental context so the agent focuses on new/changed files. Workspace versioning = extraction history. Post-processing overrides `_extracted_at` with server time and rebuilds `_files_examined` from the `status_trace` (LLMs hallucinate timestamps and confuse checksums with node_ids). Includes a salvage path (`parse_json_loose` on the agent's final text) if the agent skips `workspace_write_file`, and a plain-text chat message when extraction fully fails so the UI doesn't render raw JSON from a `null` node_id card.
  - `red_team` — honors the client toggle (one-shot or react).
  - `legal_review` — **force-`react`** server-side. Reviews user-selected legal docs (term sheet / SAFE / SPA / COI / voting / investors-rights / side letters) for one funding round against a two-tier reference system: **Tier R1** — raw template corpus (YC SAFE + NVCA files under `backend/app/legal_templates/`, catalogued at `data/config/legal_templates.json`, fetched on demand via `legal_template_read(template_id)` — the prompt gets only the catalog pointer); **Tier R2** — distilled structured checklist at `data/config/legal_review_checklist.json` (categories → items → standard_value + red_flag_patterns + scenario_focus), injected in full into the prompt. Auto-detects scenario per round (`new_investment` / `follow_on` / `retrospective`) from `metadata._positions[]` + prior `legal_reviews[]`. Writes `Legal Review.json` at the workspace root; post-processing validates, overrides `review_date` + `documents_reviewed` (rebuilt from `status_trace`, resolved to node_ids) + `checklist_version`, merges by `round_name` into `entity.metadata_json.legal_reviews[]`, and re-persists the corrected JSON to the workspace file. Same belt-and-suspenders as extract_info: salvage `parse_json_loose(agent_text)` if the agent skipped the write; plain-text assistant message (not an `artifact_card`) when the run fully fails.
- **Unified send UX** (both modes): user message appears optimistically before the API call (rolled back on error). An inline "Assistant" thinking bubble with animated spinner shows in the message area — displaying `activeAgentStatusText` (tool/step detail) in agent mode, or "Thinking…" in chat mode. The textarea placeholder shows a generic `"Working hard…"` spinner while disabled. Auto-scrolls to the thinking bubble.

### File preview
`FilePreview` in `components/EntityDetail.tsx` renders files in the side panel and supports an **expand-to-popup** modal (lucide `Maximize2`). Markdown files (`.md`, `text/markdown`) render via `react-markdown` + `remark-gfm`, not as raw text. The popup header has filename + **version picker dropdown** on the left (text/markdown only, fetched lazily from `GET /workspace/file/{id}/versions`; historical blob via `GET /workspace/file/{id}/versions/{version}`) and **copy-to-clipboard** + close on the right. Versions in the modal are view-only — no re-fetch on expand, modalContent overrides content when a historical version is selected. The inline side panel always shows the current version.

### Workspace Agent Tools (13 workspace + 1 reference)
Browse + organize (7): `workspace_get_tree`, `workspace_list_files`, `workspace_read_file`, `workspace_search_files`, `workspace_create_folder`, `workspace_move`, `workspace_rename`
Write + manage (6): `workspace_write_file`, `workspace_annotate`, `workspace_delete`, `workspace_file_versions`, `workspace_restore_version`, `workspace_history`

Reference (1, entity-agnostic): `legal_template_read(template_id)` — fetches raw text of a YC SAFE / NVCA / side-letter template from the `data/config/legal_templates.json` catalog. Used by the `legal_review` preset for precise wording comparison when a deal term looks unusual.

### Data Storage
- Portfolio SQLite DB at `data/vc_portfolio.db`
- Academic SQLite DB at `data/academic.db` (separate from portfolio)
- Entity workspace files at `data/entities/{entity_id}/workspace/blobs/{node_id}/`
- Version history at `data/entities/{entity_id}/workspace/.versions/{node_id}/`
- Parking lot at `data/entities/00000/parkinglot/`
- Scholar dossiers at `data/scholars/{scholar_id}/` — profile.json, papers.json, attributed_metrics.json, grants.json, patents.json, startups.json, channels.json, news.jsonl, peer_group.jsonl, red_flags.jsonl, snapshot_log.jsonl, narrative.jsonl, evaluations/{dim_id}.jsonl, uploads/. Events are SQL-only (`scholar_events` table), not in dossier files
- Portfolio config at `data/config/funds.json` — Taihill fund registry referenced by `entities.metadata_json._positions[].fund_id`
- Academic config at `data/config/` (continuous_tasks.json, dimensions.json, heartbeat.json, field_archetypes.json, ranking_presets/, digests/)
- `data/` is gitignored

## Key Configuration

All backend config via environment variables (see `backend/.env_sample`):
- `GEMINI_API_KEY` / `GOOGLE_API_KEY` — required for AI features
- `CHAT_USE_DEEP_AGENT` — legacy boolean (default false); overridden by `agent_mode` when present
- `CHAT_DEFAULT_AGENT_MODE` — `one_shot`, `react`, or `deep_agent` (default `one_shot`; frontend defaults to `react` when agent toggle is on)
- `CHAT_AGENT_RECURSION_LIMIT` — LangGraph recursion limit for agent modes (default 100)
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

## Academic Tracking Module (v2 — Scholar Evaluation Framework)

Separate from portfolio — scholar-centric tracking with 3-layer continuous monitoring. Uses its own SQLite DB (`data/academic.db`) with `AcademicBase` declared in `app/academic_database.py`. All LLM calls use the `google-genai` SDK (`services/academic/llm_client.py`), not `langchain-google-genai` or `deepagents` — those are portfolio-only.

Full design: `docs/design/SCHOLAR_EVALUATION_FRAMEWORK.md` (8 shared concepts + 4 per-dim prompts).

### 4 MECE dimensions
- **D1 Academic Excellence** — scientific contribution + peer standing (authorship-weighted citations, field recognition, collaboration quality)
- **D2 Tech-transfer Experience** — historical commercial track record (ventures, IP, market validation). Clean venture failures are positive evidence, not red flags
- **D3 Founder Potential** — future commercial success probability (founder-market fit, determination, commitment, team-attracting ability)
- **D4 Growth Trajectory** — slope across scientific + commercial + operator axes (multi-axis acceleration, phase-sensitive)

Dimension prompts live in `data/config/dimensions.json` (file-backed, editable at runtime, auto-seeded with 4 defaults).

### 3-layer architecture
- **Layer 1 — Fact store**: per-scholar JSON + JSONL files under `data/scholars/{id}/`. Current-state files (profile.json, papers.json, attributed_metrics.json, etc.) are rewritten wholesale; append-only logs (peer_group.jsonl, red_flags.jsonl, evaluations/{dim}.jsonl) use the four JSONL primitives in `file_utils.py`. Events are SQL-only (`scholar_events` table) — no dossier file
- **Layer 2 — Source fetchers**: `sources/` modules (semantic_scholar_papers, google_scholar_stats, patents_lens, news_web, crunchbase_startups, red_flags_watch). Each owns one external API; dim agents in Layer 3 can only call `trigger_refresh`, never external APIs directly
- **Layer 3 — Dim evaluation + synthesis**: `dim_runner.py` (per-dim triage → scoring), `phase_classifier.py` (R1-R4 classification), `narrative_synthesizer.py` (cross-dim report), `scholar_chat.py` (Interactions API with function tools)

All tasks are configured in `data/config/continuous_tasks.json` (cadences, models, required_sources); heartbeat dispatches them.

### Identity resolution
`identity_resolver.py` runs before Layer 2 on fresh scholars. Pipeline: homepage crawl → grounded LLM discovery → deterministic `classify_urls` → **per-source LLM verification** (every high-signal source independently verified via `identity_verifier.py`):
- **GS**: fetch profile → LLM verify (name + affiliation + metrics) → if rejected, try SerpAPI paper-search fallback with LLM verify on each candidate
- **SS**: cheap pre-filter (`verify_ss_metrics` — rejects zero-metric candidates against strong anchors) → LLM verify → iterate top-K candidates in Tier 1 (name search) and Tier 2 (paper search). Each rejection is appended to `profile.json.rejected_identity` so future runs skip it
- **ORCID**: fetch public API via `orcid_client.py` (name, bio, employments, works) → LLM verify
- **Homepage**: fetch + strip to plain text → LLM verify
- Low-signal sources (LinkedIn, GitHub, Twitter, DBLP, arXiv, OpenReview) committed with heuristic confidence, no LLM gate
- **Persistent rejections**: `rejected_identity` in profile.json stores known-bad candidates per source type. The resolver skips them before reaching the LLM. User edits (via Profiles tab) bypass LLM verification and clear rejections for that id
- Metrics seeded from GS; falls back to SS when no GS profile exists. Each source is verified independently — no GS-as-hard-anchor requirement

### Continuous-tasks management
- `continuous_tasks.json` is file-backed, mutated in place by the API, re-read by heartbeat every tick. No DB table, no migration
- `GET /academic/continuous-tasks` — full catalog + per-task health (7d runs, success rate, avg duration, last error) computed from the eval log + heartbeat liveness probe
- `PATCH /academic/continuous-tasks/{kind}/{task_id}` — edit `enabled` / `default_cadence_days` / `priority_overrides`. Validates the full config via Pydantic before atomic write. Emits audit entry to eval log
- `POST /academic/continuous-tasks/{kind}/{task_id}/run-now` — force-execute one task across all active scholars (or one scholar). Uses existing runners + evaluating lock. Observable in the Activity Log
- Frontend Tasks view (accessible via List / Ranking / **Tasks** toggle) shows all sources + dims + system tasks with inline cadence edit, enable toggle, run-now, expandable detail panel

### Cross-process coordination
- `claim_evaluating(scholar_id)` / `release_evaluating(scholar_id)` — atomic SQL status transition that prevents heartbeat + manual `/evaluate` + run-now from racing on the same scholar
- `bootstrap_scholar` wraps all setup (config load, claim, name lookup) inside the outer try/finally so the evaluating lock is always released even on early crashes
- Heartbeat only dispatches to scholars in `active` status; bootstrap, heartbeat, and run-now all claim/release
- Startup lifespan resets any stuck `evaluating` scholars to `active`

### Key design decisions
- **Minimal SQL, rich documents**: SQL for cross-scholar queries, scheduling, signal feed, and events; all Layer 3-readable state (evaluations, narratives, peer group, red flags) lives in dossier files
- **Identity-first with LLM verification**: every high-signal source (GS, SS, ORCID, homepage) passes a lightweight LLM gate (`identity_verifier.py`) before being committed to profile.json. The LLM checks name + affiliation + research area + top papers; rejects on contradiction, accepts on consistency. The old heuristic `verify_ss_metrics` is kept as a cheap pre-filter (catches zero-metric mismatches before wasting an LLM call) but is no longer the final word. Rejected candidates are persistently blacklisted in `profile.json.rejected_identity` so they can't sneak back on subsequent runs
- **Percentile scoring** (Concept 1): scores represent "percent of comparable peers beaten". Band anchors: <50 (unremarkable), 50-74 (solid), 75-89 (top quartile), 90-94 (top decile), 95-98 (top 5%), 99 (singular)
- **Peer group** (Concept 2): R1-R4 phases via two-axis classification (academic age + achievement gates G1-G4). Gates dominate age; age caps upward mobility
- **Evidence contract** (Concept 3): every score ≥50 must emit structured evidence with primary/supporting weights and `missing_data` / `uncertainty`
- **Red flags** (Concept 7): append-only event log in `red_flags.jsonl` with per-severity caps. Severity calibrated for VC: clean venture failures are `low` (note only), fraud/misconduct is `critical`
- **LLM execution model** (Concept 8): Path 1 (single-shot `generate_structured` for scoring/triage/synthesis/identity-verification), Path 2 (grounded Google Search for discovery), Path 3 (Interactions API for chat with function tools)
- **Events are SQL-only**: `scholar_events` is the single source of truth (no `events.jsonl` duplication). All event creation routes through `events_sync.log_event()`. Dual-date: `created_at` (auto, collection time) vs `event_date` (nullable, when it actually happened). Sources parse real dates; `event_date=None` means unknown. Timeline supports sorting by either date; signal feed shows both when they differ
- Frontend auto-refreshes (5s polling) while scholar status is "evaluating"
- Frontend tab workspace: Narrative (sidebar), Timeline, Evaluation, Publications, Profiles, Chat — plus list/ranking/tasks views, signal feed, and digest

## Code Style

- Python: PEP 8, type hints, async/await for I/O
- Frontend: functional React components with hooks, TypeScript strict mode
- Key abstractions to maintain: `StorageAdapter`, `WorkspaceService`, `ParkingLotManager`, `EntityResolver`, `WorkspaceMaterializer`

## Documentation

Detailed docs in `docs/`: `ARCHITECTURE.md`, `DEVELOPER_GUIDE.md`, `API_REFERENCE.md`, `TRACING.md`. Design rationale in `docs/design/`: `MVP-prd.md` (original PRD), `ENTITY_WORKSPACE_DESIGN.md` (workspace design), `SCHOLAR_EVALUATION_FRAMEWORK.md` (canonical evaluation design — 8 shared concepts + 4 per-dim prompts).
