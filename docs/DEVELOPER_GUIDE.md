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

Optional: `GEMINI_MODEL` (default `gemini-3.1-pro-preview`), `GEMINI_METADATA_EXTRACTION_MODEL` (default `gemini-3.1-flash-lite-preview`, used for structured JSON extraction presets such as `extract_info` and for **per-row metadata pre-process** / file-lookup enrichment), `CHAT_ENABLE_GOOGLE_SEARCH` (default true), `CHAT_MAX_ATTACHMENT_BYTES`, `CHAT_MAX_ARTIFACT_CHARS`, `CHAT_MAX_HISTORY_MESSAGES`.

**Deep Agent harness (optional):** `CHAT_USE_DEEP_AGENT` (server default when the client omits `use_deep_agent`), `CHAT_DEFAULT_MODEL_PROFILE`, per-message `model_profile_id` / **`use_deep_agent`** body field, `CHAT_AGENT_RECURSION_LIMIT`, Moonshot / Kimi Code keys and URLs (`MOONSHOT_*`, `KIMI_CODE_*`, see `config.py`).

**Artifact policy:** `CHAT_ARTIFACT_DEFAULT_EDIT_MODE`, `CHAT_ARTIFACT_OVERWRITE_ENABLED`, `CHAT_ARTIFACT_RESOLVE_MIN_SCORE`, and **`CHAT_ARTIFACT_AMBIGUOUS_INTENT_POLICY`** (`create_new` \| `allow_edit`). See `backend/.env_sample` and `docs/API_REFERENCE.md` (Entity chat env table).

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
│   │   ├── models.py                # SQLAlchemy models
│   │   ├── schemas.py               # Pydantic schemas
│   │   ├── database.py              # DB connection
│   │   ├── routers/
│   │   │   ├── entities.py          # Entity CRUD + resources/artifacts + metadata-preprocess jobs
│   │   │   ├── chat.py              # Gemini chat sessions, messages, presets
│   │   │   ├── ingest.py            # Ingestion endpoint
│   │   │   └── parkinglot.py        # Parking lot management
│   │   ├── prompts/                 # Markdown prompts (extract_info, file_lookup_preprocess, …)
│   │   └── services/
│   │       ├── storage.py             # Storage adapter
│   │       ├── parking.py             # Parking lot manager
│   │       ├── resolver.py            # Entity resolver
│   │       ├── materializer.py        # Resource materializer
│   │       ├── gemini_runner.py       # Legacy Gemini generate + JSON extraction
│   │       ├── gemini_context.py      # Multimodal parts + harness attachment text
│   │       ├── preset_registry.py     # Chat presets (red_team, extract_info); loads file_lookup_preprocess text
│   │       ├── prompt_assembly.py     # System prompts (portfolio + deep agent)
│   │       ├── artifact_service.py    # Artifact create/version/overwrite helpers
│   │       ├── artifact_editing.py    # Option B resolve/validate/apply + edit events (+ resolve_snapshot metadata)
│   │       ├── metadata_extraction.py # VC-normalized JSON for extract_info preset
│   │       ├── file_lookup_normalize.py  # Normalize Gemini file-lookup JSON (pre-process)
│   │       ├── metadata_preprocess_jobs.py # In-memory async jobs; merge into metadata_json
│   │       ├── native_file_metadata.py    # Programmatic hints (mime, size, etc.)
│   │       ├── json_loose.py          # Tolerant JSON decode for model output
│   │       ├── model_profiles.py      # Gemini / Kimi ChatModel wiring for harness
│   │       └── portfolio_deep_agent.py # Deep Agents tools + invoke wrapper
│   ├── requirements.txt
│   └── run.py                       # Development server
│
├── frontend/
│   ├── src/
│   │   ├── App.tsx                  # Root shell; mounts ToastHost for global toasts
│   │   ├── components/
│   │   │   ├── Layout.tsx           # App layout; sidebar may include chat model selector
│   │   │   ├── PortfolioTab.tsx     # Main portfolio view (list/grid segmented toggle)
│   │   │   ├── EntityDetail.tsx     # Entity workspace: resources, chat, artifacts + viewer modal
│   │   │   ├── EntityConversation.tsx  # Chat UI: presets, Agent toggle, async job polling, composer shell
│   │   │   ├── JsonArtifactFormEditor.tsx  # Structured JSON editor (form mode)
│   │   │   ├── CreateEntityModal.tsx
│   │   │   ├── EditEntityModal.tsx  # Edit entity metadata
│   │   │   ├── EntityMetadataForm.tsx
│   │   │   └── ParkingLotModal.tsx
│   │   ├── context/
│   │   │   └── ChatModelProfileContext.tsx  # Persisted harness profile id
│   │   ├── hooks/
│   │   │   ├── useEntities.ts       # Entity data hooks
│   │   │   └── useParkingLot.ts     # Parking lot hooks
│   │   ├── lib/
│   │   │   ├── appToast.ts          # showToast helper (used by EntityDetail, pre-process)
│   │   │   └── metadataPreprocess.ts # POST + poll metadata-preprocess jobs
│   │   ├── services/
│   │   │   └── api.ts               # API client
│   │   ├── store/
│   │   │   └── TabContext.tsx       # Tab state persistence
│   │   ├── styles/
│   │   │   ├── variables.css        # Design system tokens
│   │   │   ├── segmented-toggle.css # Shared list/grid and Form/Raw JSON toggle styling
│   │   │   └── global.css           # Global styles (imports segmented-toggle)
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

### Entity chat, presets, Agent mode, and JSON artifacts

On **Entity detail**, the layout is a three-column **notebook**: Resources | Chat | Artifacts.

- **Presets** (`EntityConversation`): labeled **Run preset** — **one-shot** actions (dashed pills); they call `POST .../chat/presets/{id}/run` and create artifacts via the **legacy** Gemini pipeline (`preset_registry.py`).
- **Agent** pill: **persistent mode** for ordinary messages (`use_deep_agent` in the POST body, preference in `localStorage`). **On** → deep agent path → typically **`202`** + poll `GET .../jobs/{job_id}`; status text can appear in the composer while the user switches sessions elsewhere. **Off** → **`200`** one-shot `generate_with_context`. See `API_REFERENCE.md`.
- **Context:** side-column selections send `resource_ids` / `artifact_ids` (optional; Agent tools can still list/read entity files). Hint ids also participate in ambiguous **edit vs create** gating (see `ARCHITECTURE.md` → Portfolio chat).
- **Artifacts list refresh:** when an Agent job finishes successfully, `EntityConversation` calls **`onArtifactsChanged`** so SWR refetches `GET /entities/{id}/artifacts` (new memos appear without reloading the page). Preset runs already invoked the same callback after `runPreset`.

**JSON artifact viewer** (`EntityDetail.tsx` → `ArtifactViewerModal`): Form vs Raw JSON; saves via `PUT .../artifacts/{id}/content`. See existing notes on soft-wrap and `min-width: 0`.

### Entity detail side columns (Resources / Artifacts)

The Resources and Artifacts zones intentionally share the same row interaction model.

- **Compact rows (space-saving):** item cards were replaced by dense rows with divider lines.
- **Select-all control:** each zone has a top row (`Select all sources` / `Select all artifacts`) with a master checkbox.
  - Checked: all rows selected for chat context.
  - Unchecked: none selected.
  - Indeterminate: partial selection.
- **Per-row checkbox:** right side checkbox toggles inclusion in chat context.
- **Hover actions menu:** hover the file/logo area to reveal a menu trigger, then open actions:
  - `Pre-process` (metadata enrichment: async job + toast + list refresh; row may show a check when **`metadata`** is non-empty)
  - `Rename`
  - `Download`
  - `Delete`
- **Row body click behavior:** clicking the row body still opens preview/view exactly as before.

API mapping for those actions is documented in `API_REFERENCE.md` under Entity resource/artifact endpoints.

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

On desktop, the app shell is viewport-height-bounded and `main` scrolls only when needed (for example a long portfolio list). On the entity detail screen, Resources and Artifacts columns grow to use the space below the entity header; overflowing lists and file previews scroll inside each zone’s `.zone-content`, not the whole page. See **Viewport layout and scrolling** in `docs/ARCHITECTURE.md` for the flex/grid rules and file references.

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

**Heuristic / gate tests** (no LLM): `tests/test_natural_artifact_intent.py` exercises natural-language create vs edit classification and the `portfolio_apply_artifact_edit` gate. Uses your repo `data/vc_portfolio.db` when present; skipped if the file is missing.

**Metadata pipeline** (mocked Gemini where needed): `tests/test_metadata_preprocess.py`, `tests/test_json_loose.py`, `tests/test_native_file_metadata.py`, `tests/test_entity_metadata.py`.

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
- [ ] Files appear in entity after resolution

**Resource Management:**
- [ ] Add file to existing entity
- [ ] Add text note to existing entity
- [ ] Add URL to existing entity
- [ ] View PDF inline
- [ ] View image inline
- [ ] View text file inline
- [ ] Download unsupported files
- [ ] Toggle row checkbox and verify chat-context count changes
- [ ] Use `Select all sources` and verify full/partial/none states
- [ ] Hover row logo and run Pre-process (poll to completion), confirm **`metadata`** / UI indicator updates
- [ ] Hover row logo and run Rename / Download / Delete actions

**Tab State:**
- [ ] Switch view mode (list/grid)
- [ ] Navigate away and back
- [ ] View mode is preserved
- [ ] Selection is preserved

**Entity chat & artifacts:**
- [ ] Send with **Agent off**: `200` and immediate assistant reply
- [ ] Send with **Agent on** (if server supports harness): `202`, progress in composer, final message after poll
- [ ] **Agent on** + ask to save a note / memo: new artifact appears in the **Artifacts** column when the job finishes (no manual full refresh)
- [ ] Optional: override server default using Agent toggle vs `CHAT_USE_DEEP_AGENT`
- [ ] Optional resource/artifact context; Agent without selection still reaches corpus via tools
- [ ] Run a **preset** (one-shot) and see artifact card / new artifact row
- [ ] JSON artifact: Form / Raw JSON save
- [ ] Use `Select all artifacts` and verify full/partial/none states
- [ ] Hover artifact row logo and run Pre-process / Rename / Download / Delete actions

**UI/UX:**
- [ ] Header height is compact and consistent across Resources, Chat, and Artifacts
- [ ] Compact row hover states are clear and non-jumpy
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

# Update entity status
curl -X PATCH http://localhost:8000/entities/{id} \
  -H "Content-Type: application/json" \
  -d '{"status": "archived"}'

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
- [ ] Custom dimensions: add/delete custom evaluation dimensions
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
3. File size (no limit in MVP, but very large files may timeout)

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
