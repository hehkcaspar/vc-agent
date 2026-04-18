# VC Portfolio Manager - Developer Guide

For architecture context, see `ARCHITECTURE.md`.
For endpoint and model contracts, see `API_REFERENCE.md`.
For documentation map, see `README.md`.

## Quick Start

### Prerequisites

- **Python 3.13** (recommended; 3.11+ generally ok — see root `README.md` for pin notes)
- Node.js 18+
- Windows 11 (or any OS with PowerShell support)

### 1. Clone and Setup

```powershell
# Navigate to project directory
cd vc-agent

# Create Python virtual environment
python -m venv venv

# Activate virtual environment
.\venv\Scripts\Activate.ps1

# Install backend dependencies
pip install -r backend/requirements.txt

# Install frontend dependencies
cd frontend
npm install
cd ..
```

### 2. Configuration

Default configuration is in `backend/app/config.py`:

```python
DATA_ROOT = PROJECT_ROOT / "data" / "entities"
DATABASE_URL = "sqlite+aiosqlite:///./data/vc_portfolio.db"
```

Override with environment variables:

```powershell
$env:DATA_ROOT = "D:\\vc-data\\entities"
$env:DATABASE_URL = "sqlite+aiosqlite:///D:/vc-data/vc_portfolio.db"
```

A commented template listing all backend variables is in [`backend/.env_sample`](backend/.env_sample); copy it to `backend/.env` and adjust.

**Portfolio chat (Gemini):** set an API key (either name is accepted by the backend client):

```powershell
$env:GEMINI_API_KEY = "your-key"
# or
$env:GOOGLE_API_KEY = "your-key"
```

Optional: `GEMINI_MODEL` (default `gemini-3.1-pro-preview`), `GEMINI_METADATA_EXTRACTION_MODEL` (default `gemini-3.1-flash-lite-preview`, used for structured JSON extraction presets such as `extract_info` + `legal_review`, and for **per-row metadata pre-process** / file-lookup enrichment), `CHAT_ENABLE_GOOGLE_SEARCH` (default true), `CHAT_MAX_ATTACHMENT_BYTES`, `CHAT_MAX_HISTORY_MESSAGES`.

**Deep Agent harness (optional):** `CHAT_USE_DEEP_AGENT` (server default when the client omits `use_deep_agent`), `CHAT_DEFAULT_MODEL_PROFILE`, per-message `model_profile_id` / **`use_deep_agent`** body field, `CHAT_AGENT_RECURSION_LIMIT`, Moonshot / Kimi Code keys and URLs (`MOONSHOT_*`, `KIMI_CODE_*`, see `config.py`).

**Workspace policy:** `WORKSPACE_MAX_FILE_BYTES` (default 50 MB per file), `WORKSPACE_VERSION_RETENTION_DAYS` (default 30 days). These govern upload size limits and how long old file versions are kept before cleanup.

**Optional real-LLM E2E:** set `RUN_E2E_LLM=1` and a valid Gemini key, then from `backend` run `pytest tests/test_chat_e2e_llm.py` (isolated temp DB + `DATA_ROOT`; does not touch `data/vc_portfolio.db`). Documented in `tests/test_chat_e2e_llm.py` module docstring; marker `e2e_llm` registered in `backend/pytest.ini`.

**Tracing (LangSmith):** see `docs/TRACING.md` for the canonical setup, scope, env vars, and verification flow.

**SQLite dev reset:** with the API stopped, from `backend`:  
`..\venv\Scripts\python.exe scripts\reset_sqlite_db.py --yes`  
recreates `data/vc_portfolio.db` from current models (does not delete files under `data/entities/`).

### 3. Run Development Servers

**Terminal 1 - Backend:**
```powershell
.\venv\Scripts\Activate.ps1
cd backend
..\venv\Scripts\python.exe run.py
```
Backend: http://localhost:8000

**Terminal 2 - Frontend:**
```powershell
cd frontend
npm run dev
```
Frontend: http://localhost:3000

**API proxy:** Dev server forwards `/api/*` to FastAPI (strip `/api`). Default target is `http://127.0.0.1:8000`.

If the browser shows 404s for `/api/entities` and a JSON body like `{"message":"no Route matched with those values"}`, something other than Uvicorn is usually listening on port 8000 (for example **Kong**). Fix by either:

- Running FastAPI on a free port and setting **`frontend/.env`**: `VITE_PROXY_TARGET=http://127.0.0.1:<port>`, then restart `npm run dev`, or
- Bypassing the proxy: `VITE_API_URL=http://127.0.0.1:<port>` in `frontend/.env` (calls the API directly; backend CORS already allows all origins in dev).

See `frontend/.env.example`.

---

## Project Structure

```
vc-agent/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI entry point
│   │   ├── config.py                # Settings
│   │   ├── models.py                # SQLAlchemy models (Entity, WorkspaceNode, WorkspaceOp, …)
│   │   ├── schemas.py               # Pydantic schemas
│   │   ├── database.py              # DB connection
│   │   ├── routers/
│   │   │   ├── entities.py          # Entity CRUD (workspace scaffold on create)
│   │   │   ├── workspace.py         # Workspace tree: upload (file/folder/zip), browse, move, rename, versions, trash, ops, metadata-preprocess, Process Inbox
│   │   │   ├── chat.py              # Gemini chat sessions, messages, presets (includes legal_review post-processing)
│   │   │   ├── ingest.py            # Ingestion endpoint
│   │   │   ├── parkinglot.py        # Parking lot management
│   │   │   └── settings.py          # Portfolio settings — funds CRUD, legal templates catalog, legal-review checklist GET/PUT
│   │   ├── prompts/                 # Markdown prompts (extract_info, red_team, legal_review, file_lookup_preprocess, inbox_grouping, inbox_folder_routing)
│   │   ├── legal_templates/         # Raw reference corpus (YC SAFE + NVCA, source docx + extracted txt); rebuilt via `scripts/build_legal_templates.py`
│   │   └── services/
│   │       ├── storage.py             # Storage adapter
│   │       ├── workspace.py           # WorkspaceService — unified hierarchical file system per entity; WORKSPACE_TAXONOMY constant
│   │       ├── workspace_tools.py     # 13 agent tools for workspace operations
│   │       ├── legal_template_tools.py # 1 reference tool `legal_template_read` — always loaded alongside workspace tools
│   │       ├── parking.py             # Parking lot manager
│   │       ├── resolver.py            # Entity resolver
│   │       ├── materializer.py        # Workspace materializer (ingest → Inbox/)
│   │       ├── office_extractors.py    # OOXML (docx/pptx/xlsx) + legacy (doc/ppt/xls via LibreOffice) text extraction
│   │       ├── document_text.py       # PDF text extraction (pypdf) + compression (ghostscript)
│   │       ├── direct_llm.py          # Stateful Gemini Interactions API + Kimi dispatch (replaces gemini_runner)
│   │       ├── gemini_context.py      # One-shot context (Gemini native binary / Kimi text) + agent pointer list
│   │       ├── preset_registry.py     # Chat presets (red_team, extract_info, legal_review) + renderers with incremental/GP-identity injection
│   │       ├── prompt_assembly.py     # System prompts (portfolio + deep agent) + GP-identity block from funds.json
│   │       ├── metadata_extraction.py # Tier 1-3 entity metadata + legal_reviews: validate/merge helpers used by extract_info + legal_review sync paths
│   │       ├── funds_config.py        # data/config/funds.json loader/writer (fund registry — seeds the GP-identity prompt block)
│   │       ├── legal_templates_config.py  # data/config/legal_templates.json loader/writer + read_template_text — Tier R1 catalog
│   │       ├── legal_review_checklist_config.py  # data/config/legal_review_checklist.json loader/writer — Tier R2 rubric
│   │       ├── file_lookup_normalize.py  # Normalize Gemini file-lookup JSON (pre-process)
│   │       ├── metadata_preprocess_jobs.py # In-memory single-file async jobs; merge native + gemini + description into metadata_json
│   │       ├── inbox_processing_jobs.py    # Process Inbox: Path A (per-file extract + synoptic grouping) + Path B (folder routing); in-memory job registry
│   │       ├── native_file_metadata.py    # Programmatic hints (mime, size, etc.)
│   │       ├── json_loose.py          # Tolerant JSON decode for model output
│   │       ├── model_profiles.py      # Gemini / Kimi ChatModel wiring for harness
│   │       ├── agent_harness.py        # ReAct agent harness (langchain.agents.create_agent + middleware); wires workspace + legal_template tools
│   │       └── deep_agent_compat.py   # Legacy Deep Agent compat (removable)
│   ├── scripts/
│   │   └── build_legal_templates.py # Regenerates extracted .txt alongside each source file under legal_templates/
│   ├── requirements.txt
│   └── run.py                       # Development server
│
├── frontend/
│   ├── src/
│   │   ├── App.tsx                  # Root shell; mounts ToastHost for global toasts
│   │   ├── components/
│   │   │   ├── Layout.tsx           # App layout; sidebar with Portfolio + Academic + Settings tabs, theme toggle
│   │   │   ├── PortfolioTab.tsx     # Portfolio list/grid view; dual-dim filter (Stage + Status) in toolbar
│   │   │   ├── EntityDetail.tsx     # Entity workspace: workspace tree, chat, file viewer
│   │   │   ├── EntityConversation.tsx  # Chat UI: presets, Agent toggle, async job polling, composer shell
│   │   │   ├── EntityEditModal.tsx   # Edit entity (deal stage, positions, founders) — inline +Add fund redirects to registry
│   │   │   ├── EntityHeader.tsx     # Entity header chips (deal stage, Invested, MOIC, Last update)
│   │   │   ├── CreateEntityModal.tsx
│   │   │   ├── EditEntityModal.tsx  # Legacy entity metadata editor
│   │   │   ├── EntityMetadataForm.tsx
│   │   │   ├── ParkingLotModal.tsx
│   │   │   ├── Settings/            # Unified Settings page (sidebar-mounted)
│   │   │   │   ├── SettingsPage.tsx    # Two-column layout: left nav + right content, active-section state
│   │   │   │   ├── SettingsNav.tsx     # Grouped nav (Portfolio / Academic / Application)
│   │   │   │   ├── Settings.css        # Layout + table + checklist viewer + templates grid + JSON editor styles
│   │   │   │   └── sections/
│   │   │   │       ├── FundsSettings.tsx       # Fund registry CRUD table + add/edit/delete modals
│   │   │   │       ├── ChecklistSettings.tsx   # Tier R2 rubric viewer + Edit-as-JSON modal
│   │   │   │       ├── TemplatesSettings.tsx   # Tier R1 catalog grid + click-to-preview text modal
│   │   │   │       ├── TasksSettings.tsx       # Embeds existing academic/TasksView
│   │   │   │       ├── DimensionsSettings.tsx  # Deep-link to Academic tab
│   │   │   │       ├── RankingSettings.tsx     # Deep-link to Academic tab
│   │   │   │       ├── AppearanceSettings.tsx  # Light / Dark radio group (fieldset/legend)
│   │   │   │       └── AboutSettings.tsx       # Version + repo/docs links
│   │   │   └── ui/
│   │   │       └── Modal.tsx        # Shared modal primitive — every popup uses this
│   │   ├── context/
│   │   │   └── ChatModelProfileContext.tsx  # Persisted harness profile id
│   │   ├── hooks/
│   │   │   ├── useEntities.ts       # Entity + workspace data hooks
│   │   │   └── useParkingLot.ts     # Parking lot hooks
│   │   ├── lib/
│   │   │   ├── appToast.ts          # showToast helper (used by EntityDetail, pre-process)
│   │   │   └── metadataPreprocess.ts # (stub) reserved for legacy single-file pre-process polling — Process Inbox is the live entry point
│   │   ├── services/
│   │   │   └── api.ts               # API client
│   │   ├── store/
│   │   │   └── TabContext.tsx       # Tab state persistence
│   │   ├── styles/
│   │   │   ├── variables.css        # Design tokens (colors, spacing, radii, modal widths, semantic bg subtle/strong tokens)
│   │   │   ├── primitives.css       # Shared .modal*, .btn-* (primary/secondary/text/icon/danger/sm), .form-* (group/input/label/error/label-hint) rules (single source of truth)
│   │   │   ├── segmented-toggle.css # Shared list/grid and Form/Raw JSON toggle styling
│   │   │   └── global.css           # Global baseline (imports segmented-toggle)
│   │   └── types/
│   │       └── index.ts             # TypeScript types + field config
│   ├── index.html                   # Google Fonts loaded here
│   └── vite.config.ts
│
├── data/                            # Runtime data (gitignored)
│   ├── entities/                    # File storage
│   └── vc_portfolio.db              # SQLite database
│
└── docs/                            # Documentation
```

---

## Frontend Architecture

### Design System

Located in `frontend/src/styles/`:

**variables.css** - CSS custom properties (light theme defaults; dark theme overrides exist under `[data-theme="dark"]`). Examples:

```css
--color-bg-primary: #F9F9F7;
--color-brand-primary: #1E293B;
--color-accent-gold: #C89A58;
--font-display: 'Cormorant Garamond', Georgia, serif;
--font-body: 'Manrope', -apple-system, BlinkMacSystemFont, sans-serif;
--font-mono: 'JetBrains Mono', 'Fira Code', monospace;
```

**Typography:**
- **Cormorant Garamond** (`--font-display`) — Headings and prominent titles
- **Manrope** (`--font-body`) — Body and UI
- **JetBrains Mono** (`--font-mono`) — Code, JSON, metadata lines

### UI Primitives

Single source of truth for modal chrome, buttons, and form controls lives in `frontend/src/styles/primitives.css`, imported once from `main.tsx` after `global.css`. **Never redeclare these classes in component CSS files.** Add component-scoped classes instead.

- **Modal widths** are driven by three tokens in `variables.css`: `--modal-w-narrow: 480px`, `--modal-w-standard: 720px`, `--modal-w-wide: min(92vw, 1152px)`. Pick by content: confirms → narrow, forms → standard (default), previews/viewers → wide. No inline `maxWidth` anywhere.
- **Modal component** — `frontend/src/components/ui/Modal.tsx` is the only modal wrapper. It renders the overlay + outer shell and handles click-to-close, Escape key, body scroll lock, and `aria-labelledby` linkage when a string title is passed. Children are responsible for `.modal-body` / `.modal-footer` structure so `<form>` callers can wrap body + footer together. API:
  ```tsx
  <Modal
    isOpen={showX}
    onClose={() => setShowX(false)}
    title="Create New Entity"      // string → auto-rendered h3 + aria-labelledby
    size="standard"                 // "narrow" | "standard" | "wide"
  >
    <form onSubmit={…}>
      <div className="modal-body">…fields…</div>
      <div className="modal-footer">
        <button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="submit" className="btn-primary">Save</button>
      </div>
    </form>
  </Modal>
  ```
  Does **not** implement a focus trap. If you need one, wrap children with a focus-trap library.
- **Buttons** — `.btn-primary`, `.btn-secondary`, `.btn-text`, `.btn-icon`, `.btn-icon-danger`, plus `.btn-sm` size modifier. All in `primitives.css`. Primary and secondary share an uppercase, letter-spaced editorial style.
- **Form controls** — `primitives.css` supports both conventions side by side:
  - `.form-group > label` / `.form-group > input` (nested selector style) — used by `CreateEntityModal`, `EditEntityModal`, `FileUploadModal`
  - `.form-label` + `.form-input` classes — used by `AddScholarModal`, `AcademicTab` Custom Dimensions
  Use whichever fits; don't mix in a single form.
- **Icons** — all UI chrome uses [`lucide-react`](https://lucide.dev). No emoji or HTML-entity glyphs (▶ ✏ 🗑 ▼ × ← →) in JSX. Emoji is reserved for content (user text, LLM output, persisted event payloads). Sizes 12–20 px typical; match nearby usage.
- **Shared event icon map** — `frontend/src/lib/eventIcons.tsx` exports `EVENT_ICONS` and `<EventIcon type={…} />`, used by both the academic `AcademicTab` signal feed and `TimelineTab`.

### State Management

1. **Server State**: SWR (stale-while-revalidate)
   - Caches API responses
   - Auto-revalidation on focus
   - Deduplicates requests

2. **Tab State**: Context + sessionStorage
   - Persists view mode (list/grid)
   - Preserves scroll position
   - Saves selected entity
   - Survives tab switches

3. **UI State**: Local React state
   - Modal open/close
   - Form inputs
   - Loading states

### Segmented toggles (shared UI)

Multi-option switches (portfolio **List / Grid**, JSON artifact **Form / Raw JSON**) use the same pattern:

- Container: `className="segmented-toggle"` (imported globally via `styles/global.css` → `segmented-toggle.css`).
- Selected segment: add `active` to that `<button type="button">`.
- Local spacing only: add a second class (for example `portfolio-view-toggle` in `PortfolioTab.css`).

Inactive segments sit on the tertiary track; the active segment uses elevated background, border, and `--shadow-sm` so it reads clearly as selected (not as part of the content area below).

### Entity chat, presets, Agent mode, and the workspace

On **Entity detail**, the layout is a two-column **notebook**: Workspace Tree | Chat.

- The **Workspace Tree** replaces the old flat Resources and Artifacts columns with a unified hierarchical file browser. Users can expand folders, upload files to any folder (default Inbox), create deliverables, move/rename/copy nodes, and view version history.
- **Presets** (`EntityConversation`): labeled **Run preset** — **one-shot** actions (dashed pills); they call `POST .../chat/presets/{id}/run` and create workspace files via the **legacy** Gemini pipeline (`preset_registry.py`).
- **Chat / Agent toggle**: segmented toggle (persistent in `localStorage`). **Agent** → `agent_mode: "react"` → **`202`** + poll `GET .../jobs/{job_id}`; status text appears in the composer. **Chat** → `agent_mode: "one_shot"` → **`200`** synchronous reply. See `API_REFERENCE.md`.
- **Context:** workspace node selections send `node_ids` (optional; Agent tools can still list/read entity files via workspace tools). Chat mode enforces a 10-file selection limit (frontend blocks over-selection, trims on mode switch). Agent mode uses 13 workspace tools (`workspace_tools.py`) to browse, read, create, move, and annotate files on demand.
- **Workspace refresh:** when an Agent job finishes successfully, `EntityConversation` calls **`onWorkspaceChanged`** so SWR refetches the workspace tree (new files appear without reloading the page). Preset runs invoke the same callback.

### Entity detail workspace tree

The workspace tree replaces the old separate Resources and Artifacts side columns.

- **Hierarchical folder browser:** files are organised into a tree with default folders (Inbox, Deliverables, etc.). Users can create arbitrary sub-folders.
- **Upload to Inbox:** files dropped or selected land in `/Inbox` by default; they can be moved to any folder afterwards. The upload modal supports four modes — **Files**, **Folder** (preserves tree), **Zip** (server-side unpack), and **Text** (paste free-form content from email/IM; saved as a markdown file under `Inbox/`).
- **Versions:** each file tracks version history. Old versions are retained for `WORKSPACE_VERSION_RETENTION_DAYS` (default 30 days). Users can view diffs and restore previous versions.
- **Trash:** deleted nodes go to trash (soft delete) and can be restored or permanently purged.
- **Operations log and undo:** workspace mutations are logged as ops; recent ops can be undone.
- **Node selection for chat context:** clicking the checkbox on a file node includes it in the chat context, similar to the old resource/artifact selection.

### Schema-Driven Forms

Entity metadata fields are defined in `frontend/src/types/index.ts`:

```typescript
export const ENTITY_METADATA_FIELDS: EntityMetadataField[] = [
  {
    name: 'name',
    label: 'Entity Name',
    type: 'text',
    required: true,
    placeholder: 'e.g., Acme Corporation',
  },
  {
    name: 'website',
    label: 'Website',
    type: 'text',  // Not 'url' - allows flexible input
    required: false,
    placeholder: 'example.com or https://example.com',
  },
  {
    name: 'status',
    label: 'Status',
    type: 'select',
    required: false,
    options: [
      { value: 'active', label: 'Active' },
      { value: 'archived', label: 'Archived' },
    ],
  },
];
```

Both Create and Edit modals use `EntityMetadataForm.tsx` which renders fields from this config.

---

## Development Workflow

### Backend Development

**Add a new API endpoint:**

1. Add route to appropriate router in `backend/app/routers/`
2. Add Pydantic schema to `backend/app/schemas.py` if needed
3. Test with: `curl http://localhost:8000/your-endpoint`

**Database Migrations:**

MVP uses SQLite with auto-create tables. For schema changes:

```python
# In development: drop and recreate
# Stop server, delete data/vc_portfolio.db, restart

# In production: use Alembic (not included in MVP)
```

**Adding a New Service:**

1. Create file in `backend/app/services/`
2. Import in routers that need it
3. Keep business logic out of routers

### Frontend Development

**Entity detail layout and scrolling**

On desktop, the app shell is viewport-height-bounded and `main` scrolls only when needed (for example a long portfolio list). On the entity detail screen, the workspace tree and chat columns grow to fill the space below the entity header; overflowing content scrolls inside each zone, not the whole page. See **Viewport layout and scrolling** in `docs/ARCHITECTURE.md` for the flex/grid rules and file references.

**Adding a New Component:**

1. Create `.tsx` and `.css` files in `frontend/src/components/`
2. Import global styles in `main.tsx`: `import './styles/global.css'`
3. Use CSS variables from design system
4. Build: `npm run build`

**Adding a New Metadata Field:**

1. Update `ENTITY_METADATA_FIELDS` in `frontend/src/types/index.ts`
2. Update `getEntityMetadataFields()` in `EntityMetadataForm.tsx`
3. Both Create and Edit modals automatically reflect changes

**State Management:**

- Server state: Use SWR hooks (e.g., `useEntities()`)
- Tab state: Use `useTabContext()` for persistence
- Local state: Use `useState()` for UI-only state

**Styling Guidelines:**

- Use CSS variables: `var(--color-bg-primary)`, spacing `--space-*`, radii `--radius-*`, shadows `--shadow-*`
- Prefer existing component patterns (`EntityDetail.css`, `PortfolioTab.css`) over one-off control styling
- For new two-or-more-option switches, reuse `segmented-toggle` (see **Segmented toggles** above)

---

## Testing

### Automated (backend)

From `backend/` (venv activated or use `..\venv\Scripts\python.exe -m pytest`):

```powershell
# Default suite: mocked Gemini where applicable (e.g. test_chat_api.py)
..\venv\Scripts\python.exe -m pytest tests/ -v --tb=short
```

**Metadata pipeline** (mocked Gemini where needed): `tests/test_metadata_preprocess.py`, `tests/test_json_loose.py`, `tests/test_native_file_metadata.py`, `tests/test_entity_metadata.py`. The metadata tests use workspace APIs for file operations.

**Real LLM end-to-end** (optional, costs quota): `tests/test_chat_e2e_llm.py` — set `RUN_E2E_LLM=1`, ensure `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) is set (`backend/.env` loaded by the test module). Uses a **temporary** SQLite file and temp `DATA_ROOT` only.

```powershell
$env:RUN_E2E_LLM = "1"
cd backend
..\venv\Scripts\python.exe -m pytest tests/test_chat_e2e_llm.py -v -s --tb=short
```

**Academic Tracking v2 e2e** (real Gemini + Semantic Scholar API):

```bash
cd backend
pytest tests/test_academic_e2e.py -v      # 16 tests: CRUD, eval, papers, reports, chat, ranking, digests, uploads, custom dims
pytest tests/test_academic_feifei.py -v   # focused test for Fei-Fei Li (hard case)
pytest tests/test_academic_randomized.py -v  # randomized scholar tests
```

These tests start a standalone server on port 8877/8878 (no portfolio dependencies needed), create scholars, run evaluations, and verify the full agent pipeline.

### Manual Testing Checklist

**Entity CRUD:**
- [ ] Create entity with name only
- [ ] Create entity with website (test auto-https)
- [ ] Create entity with files, text, URLs
- [ ] View entity detail
- [ ] Edit entity name/website/status
- [ ] Archive entity (should show badge)
- [ ] Unarchive entity
- [ ] Delete entity

**Ingestion Pipeline:**
- [ ] Upload creates parking lot record
- [ ] Exact name match auto-resolves
- [ ] No match shows resolution_required
- [ ] Can resolve to existing entity
- [ ] Can resolve to new entity
- [ ] Files appear in entity workspace after resolution

**Workspace Operations:**
- [ ] Upload file to entity workspace (lands in Inbox)
- [ ] Browse workspace tree, expand/collapse folders
- [ ] Create a new folder
- [ ] Move a file from Inbox to another folder
- [ ] Rename a file or folder
- [ ] Copy a file to another folder
- [ ] Delete a file (goes to trash)
- [ ] View trash, restore a deleted file
- [ ] Permanently purge a trashed file
- [ ] View file version history
- [ ] Restore a previous file version
- [ ] View diff between file versions
- [ ] View PDF inline
- [ ] View image inline
- [ ] View text file inline
- [ ] Download file
- [ ] Toggle node checkbox and verify chat-context count changes
- [ ] Hover row and run Pre-process (poll to completion), confirm metadata indicator updates
- [ ] Annotate a workspace node with tags/notes

**Tab State:**
- [ ] Switch view mode (list/grid)
- [ ] Navigate away and back
- [ ] View mode is preserved
- [ ] Selection is preserved

**Entity chat & workspace:**
- [ ] Send with **Agent off**: `200` and immediate assistant reply
- [ ] Send with **Agent on** (if server supports harness): `202`, progress in composer, final message after poll
- [ ] **Agent on** + ask to save a note / memo: new file appears in the workspace tree when the job finishes (no manual full refresh)
- [ ] Optional: override server default using Agent toggle vs `CHAT_USE_DEEP_AGENT`
- [ ] Optional workspace node context; Agent without selection still reaches corpus via workspace tools
- [ ] Run a **preset** (one-shot) and see new file in workspace tree
- [ ] JSON file: Form / Raw JSON save
- [ ] Undo a recent workspace operation via the ops log

**UI/UX:**
- [ ] Header height is compact and consistent
- [ ] Workspace tree hover states are clear and non-jumpy
- [ ] Edit/Archive buttons appear on hover
- [ ] Modal animations smooth
- [ ] Archived entities visually distinct
- [ ] Responsive on different screen sizes
- [ ] Toast notifications for pre-process (and similar flows) are visible and non-blocking

### API Testing with curl

```bash
# List entities
curl http://localhost:8000/entities

# Create entity
curl -X POST http://localhost:8000/entities \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Company", "website": "https://test.com"}'

# Update entity status / deal stage / metadata
curl -X PATCH http://localhost:8000/entities/{id} \
  -H "Content-Type: application/json" \
  -d '{"status": "archived"}'

# Promote to portfolio + set positions (metadata_json is the full JSON string)
curl -X PATCH http://localhost:8000/entities/{id} \
  -H "Content-Type: application/json" \
  -d '{"deal_stage": "portfolio", "metadata_json": "{\"_positions\":[{\"fund_id\":\"taihill_v3_lp\",\"invested_amount\":500000,\"currency\":\"USD\"}]}"}'

# --- Portfolio settings (fund registry) ---

# List funds
curl http://localhost:8000/settings/funds

# Add / update a fund (id must match ^[a-z0-9_]+$)
curl -X POST http://localhost:8000/settings/funds \
  -H "Content-Type: application/json" \
  -d '{"id": "taihill_v3_lp", "name": "Taihill Venture Series III LP"}'

# Remove a fund (does not touch existing _positions references)
curl -X DELETE http://localhost:8000/settings/funds/taihill_v3_lp

# Ingest with hint
curl -X POST http://localhost:8000/ingest/resources \
  -F "entity_hint_name=Test Company" \
  -F "files=@document.pdf"

# List parking lot
curl http://localhost:8000/parkinglot

# Resolve parking lot item
curl -X POST http://localhost:8000/parkinglot/{ingest_id}/resolve \
  -H "Content-Type: application/json" \
  -d '{"entity_id": "uuid"}'

# --- Workspace API ---

# Get workspace tree
curl http://localhost:8000/entities/{id}/workspace/tree

# List files in a folder (by path)
curl "http://localhost:8000/entities/{id}/workspace/ls?path=/Inbox"

# Upload a file to workspace
curl -X POST http://localhost:8000/entities/{id}/workspace/upload \
  -F "files=@document.pdf" \
  -F "parent_path=/Inbox"

# Create a folder
curl -X POST http://localhost:8000/entities/{id}/workspace/folder \
  -H "Content-Type: application/json" \
  -d '{"path": "/Deliverables/Q1 Report"}'

# Move a node
curl -X POST http://localhost:8000/entities/{id}/workspace/move \
  -H "Content-Type: application/json" \
  -d '{"node_id": "uuid", "new_parent_path": "/Deliverables"}'

# Rename a node
curl -X POST http://localhost:8000/entities/{id}/workspace/rename \
  -H "Content-Type: application/json" \
  -d '{"node_id": "uuid", "new_name": "renamed-file.pdf"}'

# Get file content
curl http://localhost:8000/entities/{id}/workspace/file/{node_id}

# Get file version history
curl http://localhost:8000/entities/{id}/workspace/file/{node_id}/versions

# Delete a node (soft delete to trash)
curl -X DELETE "http://localhost:8000/entities/{id}/workspace/node?node_id={node_id}"

# List trash
curl http://localhost:8000/entities/{id}/workspace/trash

# Restore from trash
curl -X POST http://localhost:8000/entities/{id}/workspace/trash/{node_id}/restore
```

**Academic Tracking (v2):**
- [ ] Create scholar (name + homepage URL)
- [ ] Create scholar with Google Scholar URL directly (verify pre-classification extracts GS ID)
- [ ] Edit scholar name/priority/tags/notes
- [ ] Delete scholar (hard delete — dossier + SQL cascade)
- [ ] Delete scholar while evaluating (should stop agent, then delete)
- [ ] Run evaluation → status changes to "evaluating", auto-refresh in UI (5s polling)
- [ ] Evaluation completes → status "active", report appears in sidebar, first report auto-selected
- [ ] Evaluation tab: radar chart, dimension scores, delta indicators (if 2+ evals)
- [ ] Publications tab: papers sorted by citations, filter by author position
- [ ] Timeline tab: events with significance filter, mark-as-read, event date vs discovery date shown when different
- [ ] Profiles tab: discovered links as cards, channel controls (pause/resume)
- [ ] Chat tab: create session, send message, async response with polling
- [ ] Signal feed: unread events across all scholars
- [ ] Mark signal feed items as read (individual + bulk)
- [ ] Ranking view: toggle from list view, weight preset selector, sortable columns
- [ ] Comparative evaluation: select 2 scholars in ranking, run comparison
- [ ] Generate digest: weekly summary via Gemini
- [ ] Evaluation dimensions modal: add / edit / delete dimensions (defaults and custom treated uniformly — all editable; file-backed at `data/config/dimensions.json`; changes take effect on next evaluation)
- [ ] Upload files to scholar dossier
- [ ] Multiple reports in sidebar, switch between them, delete a report
- [ ] Stale alerts bar: scholars needing refresh
- [ ] Failed evaluation shows error via toast (not alert)
- [ ] Server restart resets stuck "evaluating" scholars to "active"

---

## Common Issues

### Port Already in Use

**Backend:**
```powershell
# Find process using port 8000
Get-NetTCPConnection -LocalPort 8000
# Kill process
Stop-Process -Id <PID>
```

**Frontend:**
```powershell
# Vite will auto-increment port if 3000 is taken
# Or specify port: npm run dev -- --port 3001
```

### Database Locked

SQLite doesn't support concurrent writes well. If you see "database is locked":

1. Stop all backend processes
2. Wait a few seconds
3. Restart

For production, migrate to PostgreSQL.

### CORS Errors

If frontend can't connect to backend:

1. Verify backend is running: `curl http://localhost:8000/health`
2. Check vite.config.ts proxy settings
3. Ensure CORS middleware is enabled in backend

### File Uploads Fail

Check:
1. DATA_ROOT directory exists and is writable
2. Disk space available
3. File size within `WORKSPACE_MAX_FILE_BYTES` limit (default 50 MB)

### TypeScript Errors

```powershell
cd frontend
npx tsc --noEmit
```

Fix any type errors before committing.

---

## Production Considerations

### Security

- [ ] Add authentication (OAuth2, JWT)
- [ ] Add authorization (role-based access)
- [ ] File upload size limits
- [ ] File type validation
- [ ] Rate limiting
- [ ] Input sanitization

### Performance

- [ ] Migrate to PostgreSQL
- [ ] Add Redis for caching
- [ ] Implement pagination for large lists
- [ ] Add CDN for file serving
- [ ] Compress images on upload

### Deployment

**Backend:**
```powershell
# Production server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

**Frontend:**
```powershell
# Build for production
npm run build
# Serve dist/ folder with nginx or similar
```

### Monitoring

- [ ] Add structured logging
- [ ] Add metrics (Prometheus)
- [ ] Add error tracking (Sentry)
- [ ] Add health checks

---

## Contributing

1. Branch from `main`
2. Make changes following code style
3. Test thoroughly (manual checklist)
4. Run TypeScript checks: `npx tsc --noEmit`
5. Update documentation
6. Submit PR

## Resources

- FastAPI Docs: https://fastapi.tiangolo.com/
- React Docs: https://react.dev/
- SWR Docs: https://swr.vercel.app/
- SQLAlchemy Docs: https://docs.sqlalchemy.org/
