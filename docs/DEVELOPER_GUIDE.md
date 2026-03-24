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
│   │   │   ├── entities.py          # Entity CRUD + resources/artifacts
│   │   │   ├── ingest.py            # Ingestion endpoint
│   │   │   └── parkinglot.py        # Parking lot management
│   │   └── services/
│   │       ├── storage.py           # Storage adapter
│   │       ├── parking.py           # Parking lot manager
│   │       ├── resolver.py          # Entity resolver
│   │       └── materializer.py      # Resource materializer
│   ├── requirements.txt
│   └── run.py                       # Development server
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Layout.tsx           # App layout with sidebar
│   │   │   ├── PortfolioTab.tsx     # Main portfolio view
│   │   │   ├── EntityDetail.tsx     # Entity detail with resource viewer
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
│   │   │   └── global.css           # Global styles
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

**variables.css** - CSS custom properties:
```css
--color-bg-primary: #0a0a0f;      /* Deep navy background */
--color-brand-primary: #6366f1;    /* Indigo accent */
--color-accent-gold: #fbbf24;      /* Gold accent */
--font-display: 'Playfair Display', serif;
--font-body: 'Plus Jakarta Sans', sans-serif;
```

**Typography:**
- **Playfair Display** - Headings, entity names (elegant serif)
- **Plus Jakarta Sans** - Body text, UI elements (geometric sans)
- **JetBrains Mono** - Code blocks, text previews

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

- Use CSS variables: `var(--color-bg-primary)`
- Use spacing scale: `var(--space-4)` (1rem)
- Use radius scale: `var(--radius-md)` (10px)
- Add transitions: `transition: all var(--transition-fast)`
- Use glassmorphism: `backdrop-filter: blur(20px)`

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
