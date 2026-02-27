# VC Portfolio Manager

A minimal admin-style webapp for a US-based VC firm to manage portfolio companies with an **Entity-Canonical, Parking-Lot Ingestion** architecture.

**Live Demo:** http://localhost:3000 (after starting dev servers)

![Architecture](docs/images/architecture.png)

## Core Value Proposition

- **Upload/store/browse materials reliably**
- **Nothing gets lost** - every submission is persisted to Parking Lot immediately
- **Future-proof** - smarter matching and ingestion can be added without refactoring

## Features

✅ **Entity Management** - Create and manage portfolio companies  
✅ **Resource Management** - Upload files, text, and URLs per entity  
✅ **Artifact Tracking** - Store versioned markdown documents  
✅ **Parking Lot Ingestion** - Durable staging for all inbound content  
✅ **Tab State Persistence** - View state preserved when switching tabs  
✅ **Entity Resolution** - Smart matching or manual assignment  

## Quick Start

```powershell
# 1. Setup Python environment
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install fastapi uvicorn sqlalchemy aiosqlite python-multipart pydantic pydantic-settings aiofiles

# 2. Setup frontend
cd frontend
npm install
cd ..

# 3. Start backend (Terminal 1)
cd backend
..\venv\Scripts\python.exe run.py

# 4. Start frontend (Terminal 2)
cd frontend
npm run dev
```

Open http://localhost:3000

## Documentation

| Document | Description |
|----------|-------------|
| [API Reference](docs/API_REFERENCE.md) | Complete API documentation |
| [Architecture](docs/ARCHITECTURE.md) | System design and data flow |
| [Developer Guide](docs/DEVELOPER_GUIDE.md) | Setup, development, and deployment |
| [Gap Analysis](docs/GAP_ANALYSIS.md) | Comparison with PRD requirements |
| [Design Doc](docs/plans/2025-02-27-vc-portfolio-mvp-design.md) | Original design specification |

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Frontend      │────▶│   Backend API    │────▶│   Storage       │
│   (React)       │◄────│   (FastAPI)      │◄────│   (Local FS)    │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌──────────────────┐
                        │   SQLite DB      │
                        └──────────────────┘
```

### Key Components

- **ParkingLotManager** - Durable staging for all uploads
- **EntityResolver** - Matches uploads to existing entities or requests resolution
- **ResourceMaterializer** - Safely moves files to entity folders (Copy→Verify→Write DB→Delete)
- **StorageAdapter** - Abstract interface for storage (local FS now, cloud later)

## Data Flow

### Creating an Entity

1. User uploads files/text/URLs with entity name
2. Content saved to Parking Lot immediately
3. EntityResolver attempts to match by name
4. If no match → auto-creates new entity
5. ResourceMaterializer copies files to entity folder
6. User sees new entity with all resources

### Uploading to Existing Entity

1. User clicks "Upload" in entity detail
2. Content saved to Parking Lot
3. EntityResolver matches by provided entity_id
4. Files immediately attached to entity

## File Storage

```
data/entities/
├── 00000/                          # Parking lot pseudo-entity
│   └── parkinglot/{ingest_id}/
│       ├── files/                  # Raw uploads
│       └── payload/                # metadata, text, urls
│
└── {entity_uuid}/                  # Real entities
    ├── resources/{resource_id}/    # Canonical resources
    └── artifacts/{artifact_id}/    # Versioned markdown
```

## Tech Stack

**Backend:**
- Python 3.11+
- FastAPI
- SQLAlchemy (async) + SQLite
- Local filesystem storage

**Frontend:**
- React 18
- TypeScript
- Vite
- SWR (data fetching)

## API Highlights

```bash
# Ingest content
POST /ingest/resources
  - files, text, urls
  - optional: entity_id, entity_hint_name

# Returns:
# - resolved: auto-attached to entity
# - resolution_required: user must choose
# - failed: error occurred

# Manage parking lot
GET /parkinglot
POST /parkinglot/{id}/resolve
  - { entity_id } or { create_entity: { name } }

# Browse portfolio
GET /entities
GET /entities/{id}/resources
GET /entities/{id}/artifacts
```

See [API Reference](docs/API_REFERENCE.md) for complete documentation.

## MVP Acceptance Criteria

| Criteria | Status |
|----------|--------|
| Tab state preserved | ✅ |
| No upload loss | ✅ |
| Canonical resources only | ✅ |
| Resolution handshake works | ✅ |
| Filesystem correctness | ✅ |
| Entity detail separation | ✅ |
| Local-only MVP | ✅ |

**Result: 7/7 Pass** ✅

## Known Limitations

1. **No File Viewer** - Files can be uploaded but not viewed in-app (stored on disk)
2. **No Artifact Viewer** - Markdown artifacts listed but not rendered
3. **No Search** - Out of scope for MVP
4. **No Authentication** - Single-user local deployment

See [Gap Analysis](docs/GAP_ANALYSIS.md) for details.

## Future Extensions

The architecture supports:

- **Email/IM ingestion** - Add new `source` values
- **Smarter matching** - Update `EntityResolver` only
- **Cloud storage** - Swap `StorageAdapter` implementation
- **Artifact generation** - Write markdown directly
- **Multi-tenancy** - Add `tenant_id` to tables

## Project Structure

```
vc-agent/
├── backend/              # FastAPI application
│   ├── app/
│   │   ├── routers/      # API endpoints
│   │   └── services/     # Business logic
│   └── run.py
├── frontend/             # React application
│   └── src/
│       ├── components/   # UI components
│       ├── hooks/        # Data hooks
│       ├── services/     # API client
│       └── store/        # State management
├── data/                 # Runtime data (gitignored)
└── docs/                 # Documentation
```

## Contributing

1. Read the [Developer Guide](docs/DEVELOPER_GUIDE.md)
2. Check [Architecture](docs/ARCHITECTURE.md) for design patterns
3. Run tests and verify acceptance criteria
4. Update documentation

## License

MIT
