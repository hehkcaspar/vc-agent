# VC Portfolio Manager - Developer Guide

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
pip install fastapi uvicorn sqlalchemy aiosqlite python-multipart pydantic pydantic-settings aiofiles

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
│   │   ├── main.py              # FastAPI entry point
│   │   ├── config.py            # Settings
│   │   ├── models.py            # SQLAlchemy models
│   │   ├── schemas.py           # Pydantic schemas
│   │   ├── database.py          # DB connection
│   │   ├── routers/
│   │   │   ├── entities.py      # Entity CRUD
│   │   │   ├── ingest.py        # Ingestion endpoint
│   │   │   └── parkinglot.py    # Parking lot management
│   │   └── services/
│   │       ├── storage.py       # Storage adapter
│   │       ├── parking.py       # Parking lot manager
│   │       ├── resolver.py      # Entity resolver
│   │       └── materializer.py  # Resource materializer
│   ├── run.py                   # Development server
│   └── cleanup_parkinglot.py    # Cleanup utility
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Layout.tsx       # App layout with sidebar
│   │   │   ├── PortfolioTab.tsx # Main portfolio view
│   │   │   ├── EntityDetail.tsx # Entity detail view
│   │   │   ├── CreateEntityModal.tsx
│   │   │   └── ParkingLotModal.tsx
│   │   ├── hooks/
│   │   │   ├── useEntities.ts   # Entity data hooks
│   │   │   └── useParkingLot.ts # Parking lot hooks
│   │   ├── services/
│   │   │   └── api.ts           # API client
│   │   ├── store/
│   │   │   └── TabContext.tsx   # Tab state persistence
│   │   └── types/
│   │       └── index.ts         # TypeScript types
│   ├── package.json
│   └── vite.config.ts
│
├── data/                        # Runtime data (gitignored)
│   ├── entities/                # File storage
│   └── vc_portfolio.db          # SQLite database
│
└── docs/                        # Documentation
    ├── API_REFERENCE.md
    ├── ARCHITECTURE.md
    ├── GAP_ANALYSIS.md
    └── plans/
```

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

**Adding a New Component:**

1. Create `.tsx` and `.css` files in `frontend/src/components/`
2. Add to parent component
3. Build: `npm run build`

**Adding a New API Call:**

1. Add method to `frontend/src/services/api.ts`
2. Create hook in `frontend/src/hooks/` if needed
3. Use in component

**State Management:**

- Server state: Use SWR hooks (e.g., `useEntities()`)
- Tab state: Use `useTabContext()` for persistence
- Local state: Use `useState()` for UI-only state

---

## Testing

### Manual Testing Checklist

**Entity CRUD:**
- [ ] Create entity with name only
- [ ] Create entity with files
- [ ] Create entity with text
- [ ] Create entity with URLs
- [ ] View entity detail
- [ ] Edit entity name/website
- [ ] Delete entity

**Ingestion Pipeline:**
- [ ] Upload creates parking lot record
- [ ] Exact name match auto-resolves
- [ ] No match shows resolution_required
- [ ] Can resolve to existing entity
- [ ] Can resolve to new entity
- [ ] Files appear in entity after resolution

**Tab State:**
- [ ] Switch view mode (list/grid)
- [ ] Navigate away and back
- [ ] View mode is preserved
- [ ] Selection is preserved

**Upload to Existing:**
- [ ] Open entity detail
- [ ] Click Upload button
- [ ] Select files
- [ ] Files appear in resources list

### API Testing with curl

```bash
# List entities
curl http://localhost:8000/entities

# Create entity
curl -X POST http://localhost:8000/entities \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Company", "website": "https://test.com"}'

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

## Utilities

### Parking Lot Cleanup

```powershell
# List all parking lot items
cd backend
..\venv\Scripts\python.exe cleanup_parkinglot.py

# Clean up materialized items (keeps DB records, deletes files)
..\venv\Scripts\python.exe cleanup_parkinglot.py --clean-materialized

# Delete materialized items (DB + files)
..\venv\Scripts\python.exe cleanup_parkinglot.py --delete

# Delete ALL parking lot items (careful!)
..\venv\Scripts\python.exe cleanup_parkinglot.py --delete-all

# Clean orphaned folders (no DB record)
..\venv\Scripts\python.exe cleanup_parkinglot.py --orphans
```

### Database Inspection

```powershell
cd backend
..\venv\Scripts\python.exe -c "
import asyncio
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import Entity, IngestItem, Resource

async def inspect():
    async with AsyncSessionLocal() as db:
        # Count entities
        result = await db.execute(select(Entity))
        entities = result.scalars().all()
        print(f'Entities: {len(entities)}')
        for e in entities:
            print(f'  - {e.name}')
        
        # Count parking lot items
        result = await db.execute(select(IngestItem))
        items = result.scalars().all()
        print(f'\\nParking Lot Items: {len(items)}')
        
        # Count resources
        result = await db.execute(select(Resource))
        resources = result.scalars().all()
        print(f'\\nTotal Resources: {len(resources)}')

asyncio.run(inspect())
"
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
2. Make changes
3. Test thoroughly
4. Update documentation
5. Submit PR

## Resources

- FastAPI Docs: https://fastapi.tiangolo.com/
- React Docs: https://react.dev/
- SWR Docs: https://swr.vercel.app/
- SQLAlchemy Docs: https://docs.sqlalchemy.org/
