# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repo.

## First-Principles Thinking

Reason from raw requirements and the true nature of the problem — never from convention or templates.

1. **Challenge unclear goals.** When intention or motivation is ambiguous, stop and discuss before proceeding.
2. **Suggest the optimal path.** If the goal is clear but the approach isn't the shortest or best, say so directly.
3. **Fix root causes, not symptoms.** Trace breakage to the root — no band-aids. Every decision must answer "why."
4. **Say only what matters.** Cut everything that doesn't change a decision.

## Project Overview

VC Portfolio Manager — full-stack app managing portfolio companies as canonical entities with parking-lot ingestion (no data loss). FastAPI + SQLAlchemy (async SQLite) backend; React 18 + TypeScript + Vite + SWR frontend. A separate Academic Tracking module monitors scholars on the same backend with its own DB.

## Development

### System dependencies
- **LibreOffice** (`soffice` on PATH) — legacy office (doc/ppt/xls). `brew install --cask libreoffice` / `apt install libreoffice-core`.
- **Ghostscript** (`gs` on PATH) — PDF compression. `brew install ghostscript` / `apt install ghostscript`.

### Backend (`backend/`)
```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python run.py                                                  # localhost:8000
pytest                                                         # all tests
RUN_E2E_LLM=1 pytest tests/test_chat_e2e_llm.py -v --tb=short  # real Gemini E2E
```

### Frontend (`frontend/`)
```bash
npm install
npm run dev      # localhost:3000, proxies /api/* → backend
npm run build    # tsc + vite → dist/
```
No frontend test runner yet.

## Architecture

### Core pattern: entity-canonical + parking-lot ingestion
1. Inbound content lands in parking lot (`/00000/parkinglot/{ingest_id}/`).
2. `EntityResolver` matches to entities; `WorkspaceMaterializer` promotes to workspace nodes (write Inbox/ → write DB → delete parking).
3. Normal APIs only see canonical records.

### Facts vs Opinions (see `docs/design/FACTS_VS_OPINIONS.md`)
- **Facts** live canonically on `Entity.metadata_json` (mirrored to `Company Profile.json` at workspace root). Mutated only by user edits, ingestion, or user-accepted reconciliations.
- **Opinions** live in per-preset workspace files (`Legal Review.json`, `Deliverables/Analysis/extract_info_signals.json`, `Deliverables/Reports/risk_analyze.md`). Overwritten per run; history via workspace `.versions/`.
- **Fact discrepancy protocol**: when an opinion run reads source material that contradicts canonical state, the agent calls `propose_fact_update(...)` → row appended to `metadata._fact_discrepancies[]` → UI banner → user Accept/Reject. Agents never silently mutate facts.

### Workspace (per-entity hierarchical FS)
Replaces old Resource/Artifact split. Design: `docs/design/ENTITY_WORKSPACE_DESIGN.md`.

- `workspace_nodes` — files/folders/bookmarks tree (path + `parent_id`)
- `workspace_ops` — audit log
- Blobs at `{entity_id}/workspace/blobs/{node_id}/{filename}` — decoupled from logical path; moves are DB-only
- Versioning: every overwrite snapshots to `.versions/{node_id}/`
- `origin_type` (upload|agent|ingest|shared|user) gates writes — agents cannot overwrite user uploads
- **Lazy scaffolding** — new entity gets only `Inbox/` + `WORKSPACE_NOTES.md`. Taxonomy folders (`Data Room/{Financials,Legal}`, `Technical/`, `Deliverables/{Memos,Reports,Factsheets}`) materialize on demand via `_ensure_parents`. Full taxonomy lives as `WORKSPACE_TAXONOMY` in `services/workspace.py`.
- `WORKSPACE_NOTES.md` — root file for cross-file context, both user+agent writable.
- **Process Inbox** (`POST /workspace/inbox/process`, `services/inbox_processing_jobs.py`):
  - Path A (loose files): per-file Gemini extraction → synoptic Gemini grouping (`prompts/inbox_grouping.md`) → routes into named subfolders or `needs_triage`.
  - Path B (uploaded folders): structure-only Gemini routing (`prompts/inbox_folder_routing.md`) → `place_whole | join_existing | needs_sampling | unpack | needs_triage`.
  - Every file tagged with `intake_routing` metadata. Routing validated against `WORKSPACE_TAXONOMY` (no writes to `Inbox/` or arbitrary paths).
- **Upload modes** (`FileUploadModal` in `EntityDetail.tsx`): Files / Folder / Zip / Text. Endpoints: `POST /workspace/upload` (multipart, preserves `webkitRelativePath`), `POST /workspace/upload-zip` (server unpack with size cap + zip-slip rejection + single-root flatten). Text mode wraps pasted content as `note-<ISO>.md`.

### Backend (`backend/app/`)
- `main.py` — FastAPI app + lifespan (DB init, idempotent migrations, stuck-evaluating reset, secret redaction)
- `routers/` — `entities` (CRUD + `deal_stage` + derived `last_content_at`), `workspace`, `chat`, `ingest`, `parkinglot`, `settings` (funds CRUD + legal templates + checklist GET/PUT), `academic` (full Academic Tracking surface), `discrepancies` (list/accept/reject for `_fact_discrepancies[]`)
- `services/` — domain logic (see below)
- `prompts/` — markdown templates (extract_info, red_team, legal_review, file_lookup_preprocess, inbox_grouping, inbox_folder_routing)
- `models.py` — SQLAlchemy ORM. `entities.metadata_json` holds **facts only** (Tier 1-3 identity/team/deal) + user-edited `_positions[]` + founder `status` + `prior_rounds[]` per-round fact bag + `_fact_discrepancies[]`. Opinions live in workspace files (see Facts vs Opinions). `entities.deal_stage` ∈ `{prospect, diligence, portfolio, passed, exited}` (lifecycle, distinct from archival `status`). Timestamp columns all use the `UtcDateTime` TypeDecorator (`datetime_support.py`) — declares `DateTime(timezone=True)` at the dialect level, round-trips aware UTC on both SQLite and Postgres, with `utc_now()` returning aware UTC. Foreign keys that need cascading deletes declare `ondelete="CASCADE"` (Postgres enforces strictly; SQLite ignores). Migrations are idempotent ALTER TABLE checks in lifespan, plus a Postgres-only timestamp-column rewrite and FK cascade re-issue for existing DBs that predate the schema switches.
- `academic_database.py` / `academic_models.py` / `academic_schemas.py` — separate `AcademicBase` engine + `data/academic.db`
- `config.py` — Pydantic Settings from `.env`

Key services:
- `workspace.py` (`WorkspaceService`) + `workspace_tools.py` (14 LangChain agent tools including `propose_fact_update` for fact discrepancies)
- `direct_llm.py` / `gemini_context.py` — Gemini Interactions API + Kimi dispatch. `build_context_parts` (one-shot Gemini, ghostscript PDF compression), `build_harness_user_attachment_text` (one-shot Kimi, pypdf), `build_selected_files_pointer_list` (agent mode, metadata-only)
- `office_extractors.py` — OOXML + legacy office (LibreOffice headless)
- `document_text.py` — pypdf + ghostscript
- `agent_harness.py` — `langchain.agents.create_agent` ReAct harness with workspace tools only + SummarizationMiddleware + PatchToolCallsMiddleware
- `deep_agent_compat.py` — legacy `deepagents.create_deep_agent` shim (removable)
- `metadata_extraction.py` / `metadata_preprocess_jobs.py` — single-file enrichment + entity-level metadata validate/merge for `extract_info`. Facts-only validator; legacy `prior_rounds[]` short shape auto-migrated on read. `legal_review_facts.py` splits agent output into fact-block (→ `prior_rounds[]`) + opinion-block (→ `Legal Review.json`). `fact_discrepancies.py` handles the append/accept/reject lifecycle with dotted `field_path` grammar (`[key=value]` or shorthand `[value]`). `extract_info_signals.py` splits the combined agent output into facts + signals for the Deliverables/Analysis/ sidecar.
- `funds_config.py` — Taihill fund registry at `data/config/funds.json`. Injected into every portfolio chat as a "GP identity" block via `prompt_assembly._format_gp_identity_block()`.
- `legal_templates_config.py` + `legal_template_tools.py` — **Tier R1**: raw YC SAFE + NVCA corpus under `backend/app/legal_templates/{yc_safe,nvca}/`, catalog at `data/config/legal_templates.json`, agent tool `legal_template_read(template_id)` fetches on demand. Regenerate `.txt` via `backend/scripts/build_legal_templates.py`.
- `legal_review_checklist_config.py` — **Tier R2**: distilled checklist at `data/config/legal_review_checklist.json`, injected wholesale into the `legal_review` prompt.
- `academic/` — Academic Tracking v2 (see Academic section).

### Frontend (`frontend/src/`)
- `App.tsx` — root + `TabProvider` + `ToastHost`
- `components/Layout.tsx` — sidebar (Portfolio / Academic / Settings; theme toggle in footer)
- `components/PortfolioTab.tsx` — portfolio list/grid view. Toolbar splits into two independent filter dims + view toggle. **Stage** (workflow, default `funnel` = prospect+diligence+portfolio): `Funnel | Prospect | Diligence | Portfolio | Passed | Exited`. No "All stages" button — it was visually redundant with Funnel on small portfolios. **Status** (archival, default `active`): `Active | Archived | All`. Filters combine via AND; counts on each segment reflect the other dim's full pool. Sessions that stored the legacy `stageFilter='active'` or `'all'` migrate to `'funnel'` on load (`normaliseStageFilter`).
- `components/CreateEntityModal.tsx` — one combined **Website / URLs** textarea at the old website position (first URL seeds `entity_hint_domain`, all non-empty lines normalised with `https://` and queued for ingestion via `urls`). No status selector on create — new entities default to `active`. Uses `EntityMetadataForm` with `hiddenFields={['website', 'status']}` so the shared form's `name` field is the only auto-rendered piece.
- `components/Settings/` — two-column unified Settings page. Sections: Funds, Legal Review Checklist (viewer + Edit-as-JSON), Legal Templates (catalog + preview), Continuous Tasks (embeds `TasksView`), Custom Dimensions / Ranking Presets (link out to Academic), Appearance, About.
- `components/EntityDetail.tsx` — shell with horizontal tab row (`role="tablist"`): **Workroom** | **Facts** | **Initial Screening** (appears when `Deliverables/Memos/initial_screening.md` exists) | **Initial Screening v2** (appears when `..._v2.md` exists). When both exist the labels disambiguate as **Screening v1** / **Screening v2**; when only one exists its label collapses to **Initial Screening**. Active screening tab falls back to Workroom if its memo disappears from the tree. Workroom (default) is the existing workspace tree + chat split. Header (`<EntityHeader>`) + discrepancy panel + edit modal sit above the tab row. `useEntity` for fresh `last_content_at`.
- `components/EntityFactsTab.tsx` — read-only canonical fact view. Sections: Identity (Tier 1), Team (Tier 2 with founder chips + key team), Deal & rounds (Tier 3 + `prior_rounds[]` table with expandable term blocks rendered by in-file `TermBlockList` — labeled KV, booleans as pills, arrays-of-objects as sub-tables; no raw JSON), Our positions (positions table with MOIC, pencil button opens `EntityEditModal`), pending-discrepancy banner (toggles the same `FactDiscrepancyPanel` that the header badge uses), extraction-metadata footer. Helpers from `lib/entityFormat.ts` (`formatMoney`, `coerceMoney`, `readPositions`, `readFounders`, `fundLabel`) are shared with `EntityHeader` + `EntityEditModal`.
- `components/EntityInitialScreeningTab.tsx` — path-agnostic read-only view of a Taihill Monday-Screening memo. Props: `memoPath`, optional `reviewPath`. Splits the md on h2 boundaries and renders each section as a `.facts-section` card using `ReactMarkdown` + `remark-gfm` inside `.markdown-viewer` (chrome parity with Facts tab). Meta bar shows generated-at (from `GET /workspace/node/{id}` → `updated_at`) + a **Source** button (routes `onOpenPreview` back to Workroom so the file preview panel is visible) + a **Review notes** disclosure that lazy-loads the sibling `*_review_notes.md` on first open. Follow-up-questions sections get a `.facts-section--followup` gold left-accent; the review card gets a muted `.facts-section--review` wash. Exports `hasScreeningMemo(tree, path)` so the parent can gate tab visibility. Works with either v1's older schema (`Why it matters / Team (facts only) / …`) or the current Taihill template (`Intro / [1] Team / … / [6] Source / Follow-up`) — the parser is purely structural.
- `components/EntityHeader.tsx` — deal-stage `TagMenu`, website, edit button, one-liner, founder chips (strike-through for `status: "departed"`), metric badges (Round / Invested / MOIC / Last update), discrepancy badge (red alert-triangle with count + `aria-label`, toggles the panel) when `_fact_discrepancies[]` has pending rows. Invested + MOIC only for `deal_stage ∈ {portfolio, exited}`.
- `components/FactDiscrepancyPanel.tsx` — collapsible panel below the header, lists pending discrepancies with humanized `field_path`, current/proposed values, confidence pill, rationale, source-doc link (opens FilePreview), Accept/Reject. Hardened against malformed legacy entries (filters non-object / missing-status rows).
- `components/EntityEditModal.tsx` — single modal: deal-stage radios + positions list (fund dropdown with inline `+ Add new fund…` POSTing `/settings/funds`) + founder active/departed toggles. Clones `entity.metadata` before overrides so extract_info fields are preserved.
- `components/EntityConversation.tsx` — chat UI with presets, segmented Chat/Agent toggle, job polling, optimistic user messages, inline thinking bubble.
- `components/academic/` — Academic Tracking v2 UI (AcademicTab list/ranking/tasks tri-toggle, ScholarDetail with Narrative/Timeline/Evaluation/Publications/Profiles/Chat tabs, AddScholarModal, EditProfileModal, TasksView with `ct-*` class prefix, etc.)
- `services/api.ts` / `services/academicApi.ts` — API clients
- `hooks/useEntities.ts` / `hooks/useAcademic.ts` — SWR hooks (10s poll for Tasks view; 5s while scholar `evaluating`)
- `lib/moicColor.ts`, `lib/relativeTime.ts`, `lib/eventIcons.tsx`, `lib/academicRanking.ts` — display helpers
- **Icons**: all UI chrome uses `lucide-react`. No emoji or HTML-entity glyphs (▶ ✏ 🗑 ▼ ×) in JSX — emoji only in user/LLM content.

### UI primitives
- `styles/variables.css` — design tokens (colors, typography, spacing, radii, shadows, modal widths: narrow 480 / standard 720 / wide min(92vw, 1152))
- `styles/primitives.css` — single source of truth for `.modal*`, `.btn-*`, `.form-*`, `.radio-group`, `.tag-menu*`. Imported once from `main.tsx`. **Do not redefine these in component CSS** — add component-scoped classes instead.
- `components/ui/Modal.tsx` — the only modal primitive. Handles overlay + Esc + scroll-lock + header. `size` prop: `narrow` | `standard` | `wide`. Children render directly inside `.modal` so `<form>` can wrap body+footer.
- Adding popups: use `<Modal>` + `<div className="modal-body">…</div>` + `<div className="modal-footer">…</div>`. Never hand-roll overlays or set inline `maxWidth`.

### Chat modes (`agent_mode` field)
- **Chat** (`one_shot`): synchronous Gemini → 200. Files inlined natively (PDFs ghostscript-compressed). Capped at `MAX_ATTACHMENTS=10` and `MAX_TEXT_CHARS=200_000` (`gemini_context.py`). Frontend enforces the limit (blocks >10, trims on mode switch). Status line shows `"N/10 sources"`.
- **Agent** (`react`, default when toggle on): 202 + `job_id`, background `langchain.agents.create_agent` with 13 workspace tools + SummarizationMiddleware + PatchToolCallsMiddleware. **No SDK built-in tools.** Agent gets a metadata-only pointer list of selected files plus full tree context; uses `workspace_read_file` on demand (Gemini = native PDF binary, Kimi = pypdf text). No file count limit.
- **Deep Agent** (`deep_agent`, legacy): same async pattern via `deepagents.create_deep_agent` (adds 9 SDK tools alongside workspace tools). Removable.
- **Preset shortcuts** (`POST /chat/presets/{id}/run`) → 202 + `PresetRunJobAccepted`, persists synthetic `▶ Run preset: <label>` user message, runs `run_preset_agent_job` background. Poll the same chat-job endpoint.
  - `extract_info` — **force-`react`**. Agent picks files itself, extracts ~26 Tier 1-3 VC fields + signals. Post-processing splits the combined output: facts go to `Company Profile.json` + `Entity.metadata_json`; signals (priority_indicators, red_flags, competitors) go to `Deliverables/Analysis/extract_info_signals.json`. Server overrides `_extracted_at` + `_files_examined` from `status_trace`. Re-runs inject prior extraction + `_fact_discrepancies[]` as incremental context. Salvage path + plain-text fallback message if write skipped.
  - `red_team` — honors client toggle. Pure opinion artifact at `Deliverables/Reports/risk_analyze.md`; never touches `metadata_json`.
  - `legal_review` — **force-`react`**. Reviews user-selected legal docs for one round against Tier R1 (template corpus, fetched on demand) + Tier R2 (checklist, injected in full). Auto-detects scenario from `metadata._positions[]` + prior `prior_rounds[]`. Agent emits per-round entries with `proposed_facts` block + opinion fields. Post-processing splits each entry: fact block (term blocks + our_position) → deep-merged into `metadata.prior_rounds[]` by `round_name`; opinion block → merged into `Legal Review.json`. Server overrides `review_date` + `documents_reviewed` + `checklist_version`. Discrepancies (e.g. SAFE amount vs `_positions[]`) surface via `propose_fact_update` for user adjudication.
  - `initial_screening` — **force-`react`**. Three-stage Taihill Monday-Screening pipeline: (1) ReAct research agent with workspace tools + `web_search` + `propose_fact_update` writes 5 section JSONs (`team.json`, `market.json`, `product_tech.json`, `business_model.json`, `funding_traction.json`) to `Deliverables/Analysis/initial_screening/`; (2) one-shot composer reads ONLY the 5 JSONs + entity `referral_source`, writes `Deliverables/Memos/initial_screening.md` in Taihill's Monday format (`Intro`, `[1] Team`, `[2] Market & Industry Pain Point`, `[3] Product/Tech` with 4 sub-parts, `[4] Business Model`, `[5] Funding & Traction`, `[6] Source`, optional `Follow-up questions`); (3) one-shot reviewer verifies draft against sources, writes CLEAN revised memo + audit-trail `initial_screening_review_notes.md`. Recursion override 120 for phase 1 (default 50 is too tight for rich workspaces).
  - `initial_screening_v2` — **force-`react`**. Same Taihill Monday-Screening deliverable as `initial_screening`, but phase 1 is split: survey agent (≤25 recursion) → **5 section agents in parallel** via `asyncio.gather` (`team`, `market`, `product_tech`, `business_model`, `funding_traction`; each ≤45 recursion) → compose + review reused. Reliability patterns (all in `services/initial_screening_v2_job.py`): **pre-delete target files** (`_delete_if_exists`) so failures produce honest gaps not stale data; **dual delivery path** — section agents may call `workspace_write_file` OR emit JSON as final reply text, orchestrator accepts either (`_parse_section_json` fallback); **invoke-error-tolerant verification** — recursion after a successful write still counts as delivered. Writes to `Deliverables/Analysis/initial_screening_v2/` + `initial_screening_v2.md` + `initial_screening_v2_review_notes.md` so v1/v2 outputs coexist. Failure-isolated: one section failing still ships 4-5/5 + memo that degrades via cross-reference. Template format reference: `reference-project/Initial Screening & DD Samples/` (real Taihill samples — Agent Arena, GGWP, InnerCosmos, Lynq, Quest).
- **Send UX**: optimistic user message (rolled back on error) + inline "Assistant" spinner bubble (`activeAgentStatusText` in agent mode, "Thinking…" otherwise). Textarea placeholder shows `"Working hard…"` while disabled. Auto-scrolls to thinking bubble.

### File preview
`FilePreview` in `EntityDetail.tsx` — side panel + expand-to-popup modal (lucide `Maximize2`). Markdown via `react-markdown` + `remark-gfm`. Popup: filename + version-picker dropdown (text/markdown only, lazy `GET /workspace/file/{id}/versions`, blob via `/{version}`) + copy-to-clipboard + close. Versions are view-only; side panel always shows current.

### Workspace agent tools (14 + 1 reference)
- Browse/organize: `workspace_get_tree`, `workspace_list_files`, `workspace_read_file`, `workspace_search_files`, `workspace_create_folder`, `workspace_move`, `workspace_rename`
- Write/manage: `workspace_write_file`, `workspace_annotate`, `workspace_delete`, `workspace_file_versions`, `workspace_restore_version`, `workspace_history`
- Fact discrepancy: `propose_fact_update(field_path, current_value, proposed_value, source_doc_path, confidence, rationale, round_name?)` — surfaces a claim to the user; never silently mutates facts.
- Reference (entity-agnostic): `legal_template_read(template_id)` — YC SAFE / NVCA / side-letter raw text from catalog.

### Data storage
- Portfolio DB: `data/vc_portfolio.db`
- Academic DB: `data/academic.db`
- Entity blobs: `data/entities/{entity_id}/workspace/blobs/{node_id}/`
- Versions: `data/entities/{entity_id}/workspace/.versions/{node_id}/`
- Parking lot: `data/entities/00000/parkinglot/`
- Scholar dossiers: `data/scholars/{scholar_id}/` — profile.json, papers.json, attributed_metrics.json, grants.json, patents.json, startups.json, channels.json, news.jsonl, peer_group.jsonl, red_flags.jsonl, snapshot_log.jsonl, narrative.jsonl, evaluations/{dim_id}.jsonl, uploads/. Events are SQL-only.
- Config: `data/config/{funds.json, legal_templates.json, legal_review_checklist.json, continuous_tasks.json, dimensions.json, heartbeat.json, field_archetypes.json, ranking_presets/, digests/}`
- Fact discrepancies: `metadata.metadata_json._fact_discrepancies[]` — agent-surfaced claims awaiting user Accept/Reject via the `discrepancies` router. See `docs/design/FACTS_VS_OPINIONS.md`.
- `data/` is gitignored.

## Configuration

`.env` (see `backend/.env_sample`). Essentials:
- `GEMINI_API_KEY` / `GOOGLE_API_KEY` — required
- `CHAT_DEFAULT_AGENT_MODE` — `one_shot` | `react` | `deep_agent` (default `one_shot`; frontend defaults to `react` when toggle on)
- `CHAT_DEFAULT_MODEL_PROFILE` — `gemini_google` | `kimi_moonshot`
- `CHAT_AGENT_RECURSION_LIMIT` — LangGraph recursion cap (default 100)
- `WORKSPACE_MAX_FILE_BYTES` (50 MB) / `WORKSPACE_MAX_ZIP_BYTES` (500 MB) / `WORKSPACE_VERSION_RETENTION_DAYS` (30) / `WORKSPACE_INTAKE_SAMPLE_SIZE` (5)
- `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` — optional tracing
- `ACADEMIC_DATABASE_URL` (default `data/academic.db`) / `ACADEMIC_GEMINI_MODEL` (default `gemini-3-flash-preview`)
- `SEMANTIC_SCHOLAR_API_KEY` — optional (1 req/sec without)
- `SERPAPI_KEY` — optional, GS metrics

Frontend: `VITE_PROXY_TARGET` (default `http://127.0.0.1:8000`).

## Academic Tracking v2 (Scholar Evaluation Framework)

Separate from portfolio. Own SQLite (`data/academic.db`, `AcademicBase`). All LLM calls via `google-genai` SDK in `services/academic/llm_client.py` — **no langchain/deepagents in this module**. Full design: `docs/design/SCHOLAR_EVALUATION_FRAMEWORK.md` (8 shared concepts + 4 per-dim prompts).

### 4 MECE dimensions
- **D1 Academic Excellence** — scientific contribution + peer standing (authorship-weighted citations)
- **D2 Tech-transfer Experience** — historical commercial track record. Clean venture failures = positive evidence, not red flag.
- **D3 Founder Potential** — future commercial success probability (founder-market fit, determination, team-attracting)
- **D4 Growth Trajectory** — slope across scientific/commercial/operator axes

Dimension prompts at `data/config/dimensions.json` (file-backed, runtime-editable, auto-seeded).

### 3-layer architecture
- **Layer 1 — Fact store**: per-scholar files at `data/scholars/{id}/`. Wholesale-rewritten current state (profile.json, etc.); append-only logs use the four JSONL primitives in `file_utils.py`. Events are SQL-only (`scholar_events`).
- **Layer 2 — Source fetchers** (`sources/`): semantic_scholar_papers, google_scholar_stats, patents_web, news_web, startups_web, red_flags_watch. Each owns one external API (or grounded-search target). `news_web`, `patents_web`, and `startups_web` share the Gemini grounded-search pattern via `llm_client.grounded_search_json` + a structured relevance filter; `startups_web` populates `startups.json`, which `news_web._collect_known_ventures` then reads to enrich the news query. Layer 3 dim agents call `trigger_refresh` only — never external APIs directly.
  - **Mode contract**: `run(scholar_id, *, mode, reason)` branches on `mode ∈ {"bootstrap", "incremental"}`. Bootstrap = full-career sweep (no cutoff, no prior-items exclusion). Incremental = time-windowed query since `last_snapshot.created_at − 24h` with known-items fed in as prompt context. The mode flag is advisory — if no prior snapshot exists, `should_use_bootstrap` forces the bootstrap prompt regardless. Shared helpers in `sources/_incremental.py`.
  - **Cross-batch dedup**: `startups_web` and `news_web` run `canonicalize_candidates` (`sources/_canonicalize.py`) — an LLM pass that matches new candidates against the existing ledger using scholar research areas + one-liners + URLs as semantic context. Catches "Rivet AI" vs "Rivet" (ventures) and reworded-headline reposts (news) that rule-based keys miss. `patents_web` keeps rule-based `_patent_key` — patent_number is a strong enough identifier. Canon is skipped when either side is empty.
  - **Prompt shape rule**: grounded-search prompts must use flowing prose, NOT markdown section headers like "**Part 1**" — Gemini mirrors headers back as markdown-formatted prose and breaks JSON parsing. End every JSON-output prompt with an explicit "Return ONLY a JSON array — no prose, no markdown, no section headers."
  - **Snapshot contract**: every `run()` must call `record_snapshot()` on ALL exit paths (incl. early returns), or heartbeat fires the source every tick.
- **Layer 3 — Dim eval + synthesis**: `dim_runner.py` (per-dim triage→scoring), `phase_classifier.py` (R1-R4), `narrative_synthesizer.py` (cross-dim report), `scholar_chat.py` (Interactions API + function tools).
  - **Data-gaps enforcement**: `_compose_data_gaps_context(scholar_id, dim_cfg, cfg)` in `dim_runner.py` classifies every entry in `dim_cfg.required_sources` from `cfg.sources[src_id]` + the source's last `snapshot.detail` as one of `OK / disabled / scaffold / errored / skipped / never-ran / undeclared`. The gap list is rendered as a `DATA GAPS` prompt block fed to the scoring LLM ("do NOT score as zero evidence when the truth is couldn't check") AND merged back into the eval record's `missing_data` so the gap is preserved even if the LLM forgets to echo it. Applies to both the score-0 and normal paths; triage skipped.

All cadences/models/required_sources in `data/config/continuous_tasks.json`; `heartbeat.py` dispatches per tick. Module-level `get_heartbeat_status()` exposes liveness.

### Identity resolution
`identity_resolver.py` → homepage crawl → grounded LLM discovery → `classify_urls` → per-source LLM verification (`identity_verifier.py`):
- **GS / SS / ORCID / Homepage**: every high-signal source verified independently (no GS-as-anchor). SS uses `verify_ss_metrics` as a cheap pre-filter only — LLM is the final gate. SS iterates Tier 1 (name search top-K) + Tier 2 (paper search).
- **Persistent rejections**: stored in `profile.json.rejected_identity` so future runs skip them. User edits (Profiles tab) bypass LLM and clear rejections.
- Low-signal sources (LinkedIn, GitHub, Twitter, DBLP, arXiv, OpenReview): heuristic confidence, no LLM gate.
- Metrics seeded from GS, fall back to SS when no GS.

### Continuous-tasks management
- `continuous_tasks.json` — file-backed, mutated via API, re-read by heartbeat per tick (no DB table).
- `GET /academic/continuous-tasks` — catalog + per-task health (7d runs, success rate, avg duration, last error) + heartbeat liveness.
- `PATCH /academic/continuous-tasks/{kind}/{task_id}` — edit `enabled` / `default_cadence_days` / `priority_overrides`. Pydantic-validated, atomic write, audit entry to eval log.
- `POST /academic/continuous-tasks/{kind}/{task_id}/run-now` — force-execute one task across active scholars (or one). Uses existing runners + evaluating lock.
- Frontend Tasks view: List / Ranking / **Tasks** tri-toggle. Inline cadence edit, enable toggle, run-now, expandable detail.

### Cross-process coordination
- `claim_evaluating(scholar_id)` / `release_evaluating(scholar_id)` — atomic SQL status transition prevents heartbeat + manual `/evaluate` + run-now from racing.
- `bootstrap_scholar` wraps all setup in outer try/finally so the lock releases on early crash.
- Heartbeat only dispatches `active` scholars. Startup lifespan resets stuck `evaluating` → `active`.

### Key design decisions
- **Minimal SQL, rich documents** — SQL for cross-scholar queries, scheduling, signal feed, events. Layer 3-readable state lives in dossier files.
- **Identity-first with LLM verification** — `verify_ss_metrics` is a pre-filter, not the gate. `rejected_identity` persists blacklists.
- **Percentile scoring** (Concept 1): scores = "percent of comparable peers beaten". Bands: <50 unremarkable, 50-74 solid, 75-89 top quartile, 90-94 top decile, 95-98 top 5%, 99 singular.
- **Peer group** (Concept 2): R1-R4 via two-axis classification (academic age + achievement gates G1-G4). Gates dominate age; age caps upward mobility.
- **Evidence contract** (Concept 3): every score ≥50 emits structured evidence with primary/supporting weights + `missing_data` / `uncertainty`.
- **Red flags** (Concept 7): append-only `red_flags.jsonl` with per-severity caps. VC-calibrated: clean failures `low`, fraud/misconduct `critical`.
- **LLM execution model** (Concept 8): Path 1 single-shot `generate_structured`, Path 2 grounded Google Search, Path 3 Interactions API for chat.
- **Events SQL-only** — `scholar_events` is single source of truth. All writes route through `events_sync.log_event()`. Dual-date: `created_at` (collection) vs `event_date` (when it happened, nullable). Sources parse real dates; `event_date=None` = unknown.

## Code Style

- Python: PEP 8, type hints, async/await for I/O.
- Frontend: functional React + hooks, TS strict mode.
- Key abstractions to maintain: `StorageAdapter`, `WorkspaceService`, `ParkingLotManager`, `EntityResolver`, `WorkspaceMaterializer`.

## Documentation

`docs/`: `ARCHITECTURE.md`, `DEVELOPER_GUIDE.md`, `API_REFERENCE.md`, `TRACING.md`. Design rationale in `docs/design/`: `MVP-prd.md`, `ENTITY_WORKSPACE_DESIGN.md`, `SCHOLAR_EVALUATION_FRAMEWORK.md`.
