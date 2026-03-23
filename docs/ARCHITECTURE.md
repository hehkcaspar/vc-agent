# VC Portfolio Manager - Architecture Documentation

## Overview

The VC Portfolio Manager follows an **Entity-Canonical, Parking-Lot Ingestion** architecture designed for reliability and future extensibility.

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

## Core Principles

### 1. No Loss
Every inbound submission is persisted to the Parking Lot immediately before any processing.

### 2. Downstream Simplicity
All normal portfolio/resource APIs operate only on **canonical** records (never missing entity_id).

### 3. Resolver Isolation
All entity-matching complexity lives behind `EntityResolver`; other modules never implement matching logic.

### 4. Storage Abstraction
Business logic uses a `StorageAdapter` interface so local FS can be swapped for cloud storage later.

## Backend Architecture

### Service Layer

```
┌─────────────────────────────────────────────────────────────┐
│                        API Routers                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐  │
│  │ /ingest  │  │/entities │  │/parking  │  │ /artifacts │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └─────┬──────┘  │
└───────┼─────────────┼─────────────┼──────────────┼─────────┘
        │             │             │              │
        ▼             ▼             ▼              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Service Layer                           │
│  ┌──────────────────┐  ┌──────────────────┐                │
│  │ ParkingLotManager│  │EntityResolver    │                │
│  │ - create_item    │  │ - resolve()      │                │
│  │ - list_items     │  │                  │                │
│  └──────────────────┘  └──────────────────┘                │
│  ┌──────────────────┐  ┌──────────────────┐                │
│  │ResourceMaterializer│ │  StorageAdapter  │                │
│  │ - materialize()  │  │ - write_file()   │                │
│  │                  │  │ - copy_file()    │                │
│  └──────────────────┘  │ - delete_recursive│                │
│                        └──────────────────┘                │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│                      Data Layer                              │
│  ┌──────────────────┐  ┌──────────────────┐                │
│  │   SQLAlchemy     │  │   Local FS       │                │
│  │   (SQLite)       │  │   (DATA_ROOT)    │                │
│  └──────────────────┘  └──────────────────┘                │
└─────────────────────────────────────────────────────────────┘
```

### Key Components

#### ParkingLotManager
- Persists inbound content immediately
- Creates `IngestItem` record
- Writes files to `/00000/parkinglot/{ingest_id}/`
- Stores metadata, text, and URLs in payload folder

#### EntityResolver
- Input: `ingest_id` + extracted hints
- Output: `resolved`, `resolution_required` (+ candidates), or `failed`
- MVP logic:
  - If `entity_id` provided → validate exists → resolved
  - If `entity_hint_name` provided → case-insensitive exact match
    - Single match → auto-resolved
    - Multiple/no match → resolution_required
  - No hints → resolution_required

#### ResourceMaterializer
- Converts `IngestItem` to canonical Resources
- Follows safety rule: **Copy → Verify → Write DB → Delete parking**
- Creates entity folder structure
- Marks ingest item as `materialized`

#### StorageAdapter (Abstract)
```python
class StorageAdapter(ABC):
    async def write_file(self, relative_path: str, content: bytes) -> str
    async def read_file(self, relative_path: str) -> bytes
    async def copy_file(self, source: str, dest: str) -> None
    async def delete_file(self, relative_path: str) -> None
    async def delete_recursive(self, relative_path: str) -> None
    async def ensure_dir(self, relative_path: str) -> None
    async def exists(self, relative_path: str) -> bool
```

## Data Flow

### 1. Create Entity (with files)

```
User Upload
    │
    ▼
┌─────────────────┐
│  Create Modal   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────┐
│  POST /ingest   │────▶│  ParkingLot     │
│  (with hint)    │     │  (save files)   │
└────────┬────────┘     └─────────────────┘
         │
         ▼
┌─────────────────┐
│ EntityResolver  │──Match?──┬──Yes──▶ Materialize
│                 │          │
└─────────────────┘          └──No───▶ Return candidates
         │                                    │
         │ (auto-create)                       │ (user selects)
         ▼                                    ▼
┌─────────────────┐                 ┌─────────────────┐
│ POST /parking   │                 │ POST /parking   │
│ /{id}/resolve   │                 │ /{id}/resolve   │
│ (create_entity) │                 │ (entity_id)     │
└────────┬────────┘                 └────────┬────────┘
         │                                  │
         └──────────────┬───────────────────┘
                        ▼
              ┌─────────────────┐
              │  Materializer   │
              │  - Copy files   │
              │  - Write DB     │
              │  - Delete parking│
              └────────┬────────┘
                       ▼
              ┌─────────────────┐
              │  Entity Detail  │
              │  (show resources)│
              └─────────────────┘
```

### 2. Upload to Existing Entity

```
Entity Detail
    │
    ▼
┌─────────────────┐
│  + Upload Button │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────┐
│  POST /ingest   │────▶│  ParkingLot     │
│  (entity_id)    │     │  (save files)   │
└────────┬────────┘     └─────────────────┘
         │
         ▼
┌─────────────────┐
│ EntityResolver  │──Entity exists?──▶ Materialize directly
│  (entity_id)    │
└─────────────────┘
         │
         ▼
┌─────────────────┐
│  Materializer   │
│  (auto-resolve) │
└─────────────────┘
```

## File System Layout

```
DATA_ROOT/
├── 00000/                          # Parking lot pseudo-entity
│   └── parkinglot/
│       └── {ingest_id}/
│           ├── files/              # Raw uploaded files
│           │   ├── pitch_deck.pdf
│           │   └── logo.png
│           └── payload/
│               ├── meta.json       # source, hints, timestamps
│               ├── text.md         # Optional text
│               └── urls.json       # Optional URLs
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

## Database Schema

```sql
-- Entities table
entities (
    id TEXT PRIMARY KEY,
    type TEXT DEFAULT 'company',
    name TEXT NOT NULL,
    website TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP,
    updated_at TIMESTAMP
)

-- Parking lot items
ingest_items (
    ingest_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    status TEXT DEFAULT 'parked',
    parkinglot_path TEXT NOT NULL,
    entity_hint_name TEXT,
    entity_hint_domain TEXT,
    error TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
)

-- Canonical resources (never parking lot)
resources (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,  -- file, text, url
    title TEXT NOT NULL,
    mime_type TEXT,
    original_filename TEXT,
    relative_path TEXT NOT NULL,
    url TEXT,
    origin_ingest_id TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    FOREIGN KEY (entity_id) REFERENCES entities(id),
    FOREIGN KEY (origin_ingest_id) REFERENCES ingest_items(ingest_id)
)

-- Artifacts (system-generated)
artifacts (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,  -- memo, factsheet, report, other
    version INTEGER DEFAULT 1,
    status TEXT DEFAULT 'draft',
    relative_path TEXT NOT NULL,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    FOREIGN KEY (entity_id) REFERENCES entities(id)
)
```

## Frontend Architecture

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

### Component Hierarchy

```
App
└── TabProvider
    └── Layout
        ├── Sidebar (Portfolio tab)
        └── PortfolioTab
            ├── Header
            │   ├── ParkingLotBadge
            │   └── CreateButton
            ├── ViewToggle (list/grid)
            ├── EntityList/EntityGrid
            │   └── EntityCard/EntityRow (with Edit & Archive buttons)
            ├── CreateEntityModal
            │   └── EntityMetadataForm (schema-driven)
            ├── EditEntityModal
            │   └── EntityMetadataForm (shared config)
            ├── ParkingLotModal
            └── EntityDetail (when selected)
                ├── Header (Back button)
                ├── ResourcesZoneWithHeader
                │   ├── ZoneHeader (dynamic: title or preview controls)
                │   ├── AddResourceMenu (dropdown: File/Text/URL)
                │   ├── ResourceList
                │   └── ResourcePreview (PDF/Image/Text viewer)
                └── ArtifactsZone
                    └── ArtifactList
```

## Security Considerations

1. **Path Traversal Prevention**: StorageAdapter validates all paths are within DATA_ROOT
2. **No Authentication**: MVP has no auth (single-user local deployment)
3. **CORS**: Configured for development (`*`)
4. **File Uploads**: No size limits in MVP (add for production)

## Design System Architecture

### Schema-Driven Forms

Entity metadata fields are defined once and used everywhere:

**Configuration** (`frontend/src/types/index.ts`):
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
    type: 'text',
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

**Benefits:**
- Single source of truth for form fields
- Create and Edit modals automatically stay in sync
- Adding new fields updates both modals automatically
- Type-safe with TypeScript

### CSS Architecture

**Design Tokens** (`frontend/src/styles/variables.css`):
- Colors: Background, brand, accent, text, semantic
- Typography: Font families, sizes
- Spacing: Consistent scale (0.25rem to 3rem)
- Radii: Border radius scale
- Shadows: Elevation system
- Transitions: Timing functions

**Global Styles** (`frontend/src/styles/global.css`):
- CSS reset
- Base element styles
- Utility animations
- Scrollbar styling

**Component Styles:**
- Each component has its own CSS file
- Uses CSS variables from design system
- No inline styles (maintainability)

## Extension Points

The architecture supports these future extensions:

1. **New Ingestion Sources** (email/IM)
   - Add `source` field values: "email", "im"
   - Same `/ingest/resources` endpoint
   - No other changes needed

2. **Smarter Matching**
   - Update `EntityResolver.resolve()` method only
   - Add fuzzy matching, domain matching, ML-based matching

3. **Cloud Storage**
   - Implement `S3StorageAdapter` or `GCSStorageAdapter`
   - Swap in config
   - No business logic changes

4. **Artifact Generation**
   - Write markdown files directly to entity folders
   - Create `Artifact` records via `ArtifactStore`
   - Portfolio UI shows them automatically

5. **Search/Filtering**
   - Add search endpoints
   - Frontend already has UI structure

6. **Multi-tenancy**
   - Add `tenant_id` to all tables
   - Filter all queries by tenant
   - Add row-level security
