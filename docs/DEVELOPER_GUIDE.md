# VC Portfolio Manager - Developer Guide

For architecture context, see `ARCHITECTURE.md`.
For endpoint and model contracts, see `API_REFERENCE.md`.
For documentation map, see `README.md`.

## Quick Start

### Prerequisites

- Python 3.11+
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

Optional: `GEMINI_MODEL` (default `gemini-3.1-pro-preview`), `GEMINI_METADATA_EXTRACTION_MODEL` (default `gemini-3.1-flash-lite-preview`, used for structured JSON extraction presets such as `extract_info`), `CHAT_ENABLE_GOOGLE_SEARCH` (default true), `CHAT_MAX_ATTACHMENT_BYTES`, `CHAT_MAX_ARTIFACT_CHARS`, `CHAT_MAX_HISTORY_MESSAGES`.

**Deep Agent harness (optional):** `CHAT_USE_DEEP_AGENT` (default false), `CHAT_DEFAULT_MODEL_PROFILE` (e.g. `gemini_google`; override per message with JSON `model_profile_id`), `CHAT_AGENT_RECURSION_LIMIT`, Moonshot OpenAI-compatible chat: `MOONSHOT_API_KEY` or **`KIMI_CODE_API_KEY`** (same Open Platform Bearer key), plus `MOONSHOT_BASE_URL` / `MOONSHOT_MODEL` for `kimi_moonshot`. Edit-policy flags: `CHAT_ARTIFACT_OVERWRITE_ENABLED`, `CHAT_ARTIFACT_DEFAULT_EDIT_MODE`, `CHAT_ARTIFACT_RESOLVE_MIN_SCORE`. Enable **LangSmith** tracing via standard LangChain env vars if desired (`LANGCHAIN_TRACING_V2`, etc.). See `backend/app/config.py` and `backend/.env_sample`.

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
│   │   │   ├── entities.py          # Entity CRUD + resources/artifacts + artifact view/update
│   │   │   ├── chat.py              # Gemini chat sessions, messages, presets
│   │   │   ├── ingest.py            # Ingestion endpoint
│   │   │   └── parkinglot.py        # Parking lot management
│   │   └── services/
│   │       ├── storage.py           # Storage adapter
│   │       ├── parking.py           # Parking lot manager
│   │       ├── resolver.py          # Entity resolver
│   │       ├── materializer.py      # Resource materializer
│   │       ├── gemini_runner.py     # Gemini generate + JSON extraction helpers
│   │       ├── preset_registry.py   # Chat preset definitions (e.g. red_team, extract_info)
│   │       └── artifact_service.py  # Artifact file helpers / versioning
│   ├── requirements.txt
│   └── run.py                       # Development server
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Layout.tsx           # App layout with sidebar
│   │   │   ├── PortfolioTab.tsx     # Main portfolio view (list/grid segmented toggle)
│   │   │   ├── EntityDetail.tsx     # Entity workspace: resources, chat, artifacts + viewer modal
│   │   │   ├── EntityConversation.tsx  # Gemini chat UI, presets, artifact cards
│   │   │   ├── JsonArtifactFormEditor.tsx  # Structured JSON editor (form mode)
│   │   │   ├── CreateEntityModal.tsx
│   │   │   ├── EditEntityModal.tsx  # Edit entity metadata
│   │   │   ├── EntityMetadataForm.tsx
│   │   │   └── ParkingLotModal.tsx
│   │   ├── hooks/
│   │   │   ├── useEntities.ts       # Entity data hooks
│   │   │   └── useParkingLot.ts     # Parking lot hooks
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

### Entity chat, presets, and JSON artifacts

On **Entity detail**, the layout is a three-column **notebook** pattern: Resources | Chat | Artifacts. Chat calls `POST /entities/{id}/chat/...` (see `API_REFERENCE.md`). Presets (e.g. `red_team` → markdown artifact, `extract_info` → JSON artifact) are registered in `backend/app/services/preset_registry.py`.

**JSON artifact viewer** (`EntityDetail.tsx` → `ArtifactViewerModal`): valid JSON artifacts open a modal with **Form** (compact `JsonArtifactFormEditor`) and **Raw JSON** (textarea). Saving **Raw JSON** requires parseable JSON; soft line wraps in the textarea are **display only** and do not insert characters. Edits persist via `PUT /entities/{entity_id}/artifacts/{artifact_id}/content`. The textarea uses a flex-friendly width (`min-width: 0` on the control chain) so resize does not create phantom horizontal gaps.

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

**Tab State:**
- [ ] Switch view mode (list/grid)
- [ ] Navigate away and back
- [ ] View mode is preserved
- [ ] Selection is preserved

**Entity chat & artifacts:**
- [ ] Send a message with optional resource/artifact context
- [ ] Run a preset (e.g. red team / extract info) and see artifact card or update in Artifacts
- [ ] Open a JSON artifact: Form vs Raw JSON toggle matches portfolio toggle styling; save from either mode

**UI/UX:**
- [ ] Hover effects on cards
- [ ] Edit/Archive buttons appear on hover
- [ ] Modal animations smooth
- [ ] Archived entities visually distinct
- [ ] Responsive on different screen sizes

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
