# VC Portfolio Manager MVP Design

**Date:** 2025-02-27  
**Project:** VC Portfolio Manager (Entity-Canonical, Parking-Lot Ingestion)  
**Architecture:** Option A - Monolithic Full-Stack

---

## 1. Overview

Build a minimal admin-style webapp for a US-based VC firm to manage portfolio companies as **Entities**, with two kinds of content per Entity:
- **Resources**: user-provided (PDF, images, text/markdown, URLs)
- **Artifacts**: system-generated outputs (MVP: markdown files)

**Core MVP value:** upload/store/browse materials reliably; nothing gets lost; future ingestion + smarter matching can be added without refactoring.

---

## 2. Architecture

### 2.1 Project Structure

```
vc-agent/
├── backend/                    # FastAPI + SQLite
│   ├── app/
│   │   ├── main.py            # FastAPI entry point
│   │   ├── config.py          # Settings (DATA_ROOT, etc.)
│   │   ├── models.py          # SQLAlchemy models
│   │   ├── schemas.py         # Pydantic schemas
│   │   ├── database.py        # DB connection/session
│   │   ├── routers/
│   │   │   ├── entities.py    # CRUD for entities
│   │   │   ├── resources.py   # Entity resources
│   │   │   ├── artifacts.py   # Entity artifacts
│   │   │   ├── ingest.py      # Ingestion endpoint
│   │   │   └── parkinglot.py  # Parking lot management
│   │   └── services/
│   │       ├── storage.py     # StorageAdapter interface + Local impl
│   │       ├── parking.py     # ParkingLotManager
│   │       ├── resolver.py    # EntityResolver
│   │       └── materializer.py # ResourceMaterializer
│   ├── requirements.txt
│   └── run.py
├── frontend/                   # React (Vite)
│   ├── src/
│   │   ├── components/        # Reusable UI components
│   │   ├── pages/             # Page components
│   │   ├── hooks/             # Custom React hooks
│   │   ├── services/          # API client
│   │   ├── store/             # State management (Context + useReducer)
│   │   ├── types/             # TypeScript types
│   │   └── App.tsx
│   ├── package.json
│   └── index.html
└── data/                       # Runtime data folder (gitignored)
```

### 2.2 Technology Stack

**Backend:**
- Python 3.11+
- FastAPI
- SQLAlchemy (async)
- aiosqlite
- python-multipart (file uploads)
- uvicorn

**Frontend:**
- React 18+
- TypeScript
- Vite
- SWR (data fetching)
- CSS Modules (or plain CSS)

---

## 3. Data Models

### 3.1 Database Schema (SQLite)

```sql
-- Entities Table
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    type TEXT DEFAULT 'company',
    name TEXT NOT NULL,
    website TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- IngestItems Table (Parking Lot)
CREATE TABLE ingest_items (
    ingest_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    status TEXT DEFAULT 'parked',
    parkinglot_path TEXT NOT NULL,
    entity_hint_name TEXT,
    entity_hint_domain TEXT,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Resources Table (Canonical)
CREATE TABLE resources (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    title TEXT NOT NULL,
    mime_type TEXT,
    original_filename TEXT,
    relative_path TEXT NOT NULL,
    url TEXT,
    origin_ingest_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (entity_id) REFERENCES entities(id),
    FOREIGN KEY (origin_ingest_id) REFERENCES ingest_items(ingest_id)
);

-- Artifacts Table
CREATE TABLE artifacts (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    version INTEGER DEFAULT 1,
    status TEXT DEFAULT 'draft',
    relative_path TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (entity_id) REFERENCES entities(id)
);
```

### 3.2 Pydantic Schemas

See `backend/app/schemas.py` for full schema definitions.

---

## 4. Backend Services

### 4.1 StorageAdapter (Abstract Interface)

```python
class StorageAdapter(ABC):
    @abstractmethod
    async def write_file(self, relative_path: str, content: bytes) -> str: ...
    @abstractmethod
    async def read_file(self, relative_path: str) -> bytes: ...
    @abstractmethod
    async def copy_file(self, source: str, dest: str) -> None: ...
    @abstractmethod
    async def delete_file(self, relative_path: str) -> None: ...
    @abstractmethod
    async def ensure_dir(self, relative_path: str) -> None: ...
```

**LocalFilesystemAdapter**: Implementation for MVP using local filesystem.

### 4.2 ParkingLotManager

- `create_ingest_item(files, text, urls, hints) -> IngestItem`
- Writes files to `/data/entities/00000/parkinglot/{ingest_id}/`
- Creates `meta.json`, `text.md`, `urls.json`

### 4.3 EntityResolver

- `resolve(ingest_id, hints) -> ResolutionResult`
- MVP: Exact name match (case-insensitive) against existing entities
- Returns: `resolved`, `resolution_required` (with candidates), or `failed`

**Resolution Logic:**
1. If `entity_id` provided → validate exists → materialize
2. If `entity_hint_name` provided → case-insensitive exact match
   - Single match → auto-resolve
   - Multiple/no match → `resolution_required` with candidates
3. If no hints → `resolution_required`

### 4.4 ResourceMaterializer

- `materialize(ingest_id, entity_id | new_entity_name) -> Entity`
- Copy files from parking lot → entity folder
- Create resource records
- Mark ingest_item as `materialized`

**Safety Rule:** Copy → Verify → Write DB → Delete parking

---

## 5. API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/ingest/resources` | Main ingestion endpoint |
| GET | `/parkinglot` | List parking lot items |
| GET | `/parkinglot/{id}` | Get single item |
| POST | `/parkinglot/{id}/resolve` | Resolve to entity |
| GET | `/entities` | List all entities |
| POST | `/entities` | Create entity directly |
| GET | `/entities/{id}` | Get entity details |
| GET | `/entities/{id}/resources` | List entity resources |
| GET | `/entities/{id}/artifacts` | List entity artifacts |

### 5.1 Ingestion Endpoint

**Request:** `POST /ingest/resources` (multipart/form-data)
- `files`: 0..N file uploads
- `text`: Optional text content
- `urls`: Optional JSON array of URLs
- `entity_id`: Optional existing entity ID
- `entity_hint_name`: Optional entity name hint
- `entity_hint_domain`: Optional domain hint

**Responses:**
- `200` `{ status: "resolved", entity_id, resources: [...] }`
- `200` `{ status: "resolution_required", ingest_id, candidates: [...] }`
- `200` `{ status: "failed", ingest_id, error }`

---

## 6. Frontend Architecture

### 6.1 State Management

1. **Server State**: SWR for caching API responses
2. **Tab State**: Context + `sessionStorage` to persist view state
3. **UI State**: Local React state for component-level interactions

### 6.2 Component Structure

```
src/
├── components/
│   ├── Layout.tsx           # Sidebar + content area
│   ├── TabContainer.tsx     # Manages tab state persistence
│   ├── EntityList.tsx       # List/grid toggle view
│   ├── EntityCard.tsx       # Grid item
│   ├── EntityRow.tsx        # List item
│   ├── EntityDetail.tsx     # Resources + Artifacts zones
│   ├── CreateEntityModal.tsx
│   ├── ParkingLotBadge.tsx  # Shows count in sidebar
│   └── ParkingLotModal.tsx  # Resolve pending items
├── hooks/
│   ├── useTabState.ts       # Persist/restore tab state
│   ├── useEntities.ts       # SWR fetch hooks
│   └── useParkingLot.ts
├── services/
│   └── api.ts               # Axios/Fetch wrapper
├── store/
│   └── TabContext.tsx       # Global tab state provider
└── types/
    └── index.ts
```

### 6.3 Tab State Persistence

**Flow:**
1. User switches away from Portfolio tab
2. `useTabState` hook saves current state to `sessionStorage` with tab key
3. User returns to Portfolio tab
4. State restored from `sessionStorage`, scroll position reset

**Persisted State:**
- View mode (list/grid)
- Scroll position
- Selected items
- Draft inputs in modals
- Search/filter values

---

## 7. File System Layout

```
/data/entities/
├── 00000/                          # Parking lot pseudo-entity
│   └── parkinglot/
│       └── {ingest_id}/
│           ├── files/
│           │   ├── pitch_deck.pdf
│           │   └── logo.png
│           └── payload/
│               ├── meta.json       # {source, hints, timestamps}
│               ├── text.md         # Optional pasted text
│               └── urls.json       # Optional URLs array
│
└── {entity_uuid}/                  # Real entities
    ├── resources/
    │   └── {resource_uuid}/
    │       └── pitch_deck.pdf
    └── artifacts/
        └── {artifact_uuid}/
            ├── v1.md
            └── v2.md
```

---

## 8. Acceptance Criteria

1. **Tab state preserved:** switching away and back restores view mode + selection + scroll.
2. **No upload loss:** every submission creates a Parking Lot folder + ingest record immediately.
3. **Canonical resources only:** entity resource lists never include parking lot items.
4. **Resolution handshake works:** unresolved items prompt user choice; resolution materializes into the selected/created entity.
5. **Filesystem correctness:** resources/artifacts are stored under the specified folder structure; metadata matches what's on disk.
6. **Entity detail separation:** Resources and Artifacts are distinct zones, both recency-sorted.
7. **Local-only MVP:** no external storage required; storage adapter boundary exists for later swap.

---

## 9. Future Extension Points

- **New ingestion sources** (email/IM): Add to `/ingest/resources` endpoint
- **Smarter matching**: Update `EntityResolver` only
- **Cloud storage**: Swap `StorageAdapter` implementation
- **Artifact generation engine**: Write through `ArtifactStore` adapter

---

## 10. Implementation Phases

### Phase 1: Backend Foundation
- Project setup, dependencies
- Database models and connection
- Storage adapter interface + local impl

### Phase 2: Core Services
- ParkingLotManager
- EntityResolver
- ResourceMaterializer

### Phase 3: API Layer
- Ingestion endpoint
- Parking lot management
- Entity CRUD endpoints

### Phase 4: Frontend Foundation
- React + Vite setup
- Type definitions
- API client

### Phase 5: UI Implementation
- Layout and navigation
- Entity list/grid views
- Create entity modal
- Entity detail view (Resources + Artifacts)

### Phase 6: Tab State & Polish
- Tab state persistence
- Parking lot UI
- Testing and bug fixes
