# VC Portfolio Manager - Architecture Documentation

For setup and development workflow, see `DEVELOPER_GUIDE.md`.
For API contract details, see `API_REFERENCE.md`.
For documentation map, see `README.md`.

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

#### Resource and artifact `metadata_json` (plus async pre-process)

- **`resources.metadata_json`** and **`artifacts.metadata_json`** store one JSON object as SQLite **TEXT** (nullable). Migrations / `create_all` add the column on older DBs (`database.py`).
- **API surface:** Responses use a parsed **`metadata`** field (`dict` or `null`). Invalid or non-object JSON in the DB is exposed as `null` (`schemas.metadata_json_to_dict`).
- **Row pre-process:** `POST /entities/{id}/metadata-preprocess` enqueues a FastAPI **`BackgroundTasks`** job registered in **`metadata_preprocess_jobs.py`** (in-memory **`_jobs` / `_inflight`**). Successful runs **merge** into existing JSON:
  - **`native_file_metadata`** — size/MIME-oriented hints from **`native_file_metadata.py`**
  - **`gemini_preprocessed`** — Gemini JSON output for a single attached file, using **`file_lookup_preprocess.md`** (via **`preset_registry.load_file_lookup_preprocess_instruction`**), tolerant parsing **`json_loose.parse_json_loose`**, normalization **`file_lookup_normalize.normalize_file_lookup_result`**, model id from **`GEMINI_METADATA_EXTRACTION_MODEL`** (fallback `generate_json_with_context` wiring in **`metadata_preprocess_jobs`**).
- **Caveats (MVP):** No SQL-backed job table — status is **lost on API restart**; scaling to multiple workers would need a shared queue. Idempotency: starting pre-process for the same entity + resource/artifact while **pending/running** returns the existing **`job_id`**.
- **Preset `extract_info`:** Separate path — **`metadata_extraction.py`** + **`extract_info.md`** produce VC-shaped JSON artifacts; not the same as file-lookup pre-process (though both may share **`GEMINI_METADATA_EXTRACTION_MODEL`**).
- **Deep Agent:** **`artifact_editing.resolve_snapshot`** includes **`metadata`** for resolved artifact rows so tool payloads stay JSON-safe.

#### Portfolio chat (one-shot + optional Deep Agent)

- **Effective mode** for `POST .../messages`: `use_deep_agent` in the JSON body if provided, otherwise `CHAT_USE_DEEP_AGENT` in settings (`schemas.ChatMessageCreate`).
- **One-shot path:** synchronous model call (`generate_with_context`); response **`200`** with `ChatMessageResult` (no tools; no artifact writes from chat in this path).
- **Deep Agent path:** user message is saved immediately; a **`chat_completion_jobs`** row is created; response **`202`** with `job_id` and `user_message`. **`run_chat_agent_job`** runs after the response (FastAPI `BackgroundTasks`), executes the graph in **`asyncio.to_thread`**, updates **`step_detail`** for polling (tool hooks → `portfolio_deep_agent.on_status`). Client calls **`GET .../chat/sessions/{id}/jobs/{job_id}`** until `succeeded` / `failed`, then loads messages.
- **Presets follow the same mode switch:** `PresetRunRequest.use_deep_agent` (falling back to `CHAT_USE_DEEP_AGENT`). The UI sends Agent On/Off into preset runs. For `extract_info`, the preset prioritizes attached materials and avoids prior session history contamination.
- **Tools** (`portfolio_deep_agent.build_portfolio_tools`): list/read artifacts and resources; resolve artifact target; validate / **`portfolio_apply_artifact_edit`** (Option B); **`portfolio_create_artifact`** for new lineage (markdown / JSON / text). Optional **`resource_ids` / `artifact_ids`** seed the turn and populate **`session_artifact_ids`** hints for edit resolution.
- **Create vs edit (guardrail):** When **`CHAT_ARTIFACT_AMBIGUOUS_INTENT_POLICY=create_new`** (default), user turns that heuristically sound like “save / take a note / 记下来 …” **without** an artifact selected for that turn cannot call **`portfolio_apply_artifact_edit`** alone; the tool returns `create_intent_requires_create_tool` so the model should use **`portfolio_create_artifact`**. Explicit update wording or a selected artifact clears the block. See `portfolio_deep_agent._looks_like_create_intent` / `_looks_like_explicit_edit_intent` (covered by `tests/test_natural_artifact_intent.py` when `data/vc_portfolio.db` exists).
- **Option B edits:** `artifact_editing.py` (resolve → validate → apply); audit **`artifact_edit_events`**; versioning vs overwrite via `CHAT_ARTIFACT_*` settings.
- **Profiles:** `model_profiles.py` — `gemini_google`, `kimi_moonshot`. **`CHAT_DEFAULT_MODEL_PROFILE`** when `model_profile_id` omitted.
- **Attachment materialization:** `gemini_context.py` + `deep_agent_office_extractors.py`. PDFs and Office formats become text for preamble / one-shot; multimodal parts where the profile supports it.
- **Frontend:** `EntityDetail` passes **`onArtifactsChanged`** (`mutate` from `useEntityArtifacts`) into `EntityConversation`. After a **successful** deep-agent job, the chat panel calls **`onArtifactsChanged()`** so new or updated artifacts appear without a full page reload (presets already called this after `runPreset`).
- **Storage:** `artifact_service` + `app.services.storage.storage`.

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

SQLite **`create_all`** runs on startup; incremental **SQLite** column adds for older DBs live in `database.py` (e.g. `artifacts.title`, `artifact_edit_events.*`). Optional offline reset: `backend/scripts/reset_sqlite_db.py --yes` (stop the API first).

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
    metadata_json TEXT,             -- single JSON object; API exposes as "metadata"
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
    title TEXT,                     -- optional: e.g. extract_info, used for display/versioning
    version INTEGER DEFAULT 1,
    status TEXT DEFAULT 'draft',
    relative_path TEXT NOT NULL,  -- vN.md or vN.json under artifact folder
    metadata_json TEXT,             -- single JSON object; API exposes as "metadata"
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    FOREIGN KEY (entity_id) REFERENCES entities(id)
)

-- Chat (sessions + messages + async deep-agent jobs)
-- conversation_sessions, conversation_messages: see SQLAlchemy models
-- chat_completion_jobs: pending/running deep-agent work; links user_message_id → assistant_message_id when done
-- artifact_edit_events: audit log for harness artifact edits (Option B)
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
App (ToastHost for global toasts, e.g. metadata pre-process)
└── TabProvider
    └── Layout
        ├── Sidebar (Portfolio tab)
        └── PortfolioTab
            ├── Header
            │   ├── ParkingLotBadge
            │   └── CreateButton
            ├── segmented-toggle (list/grid; shared styles in styles/segmented-toggle.css)
            ├── EntityList/EntityGrid
            │   └── EntityCard/EntityRow (with Edit & Archive buttons)
            ├── CreateEntityModal
            │   └── EntityMetadataForm (schema-driven)
            ├── EditEntityModal
            │   └── EntityMetadataForm (shared config)
            ├── ParkingLotModal
            └── EntityDetail (when selected)
                ├── Header (Back button)
                ├── entity-zones--notebook (three columns on desktop: Resources | Chat | Artifacts)
                ├── ResourcesZoneWithHeader (.zone)
                │   ├── ZoneHeader + chat context toggles per resource
                │   └── .zone-content (scrolls)
                │       ├── ResourceList
                │       └── ResourcePreview (PDF/Image/Text/HTML viewer)
                ├── EntityConversation (.zone--chat-main): sessions, transcript, **Run preset** dashed shortcuts (mode follows Agent On/Off), **Agent** pill (persistent mode, On/Off + `use_deep_agent`), composer shell (+ / send), **async polling** on `202` + status line in textarea; optional `ChatModelProfileContext` / sidebar model selector (Layout)
                ├── Artifacts .zone
                │   ├── ZoneHeader
                │   └── .zone-content (scrolls)
                │       └── ArtifactsZone → ArtifactList
                └── ArtifactViewerModal (markdown or JSON): segmented-toggle Form / Raw JSON; PUT …/content for JSON saves
```

### Viewport layout and scrolling

The shell and entity detail view are wired so **long resource previews** (for example DOCX rendered as HTML) scroll **inside the Resources column**, not by growing the whole document.

**Desktop (viewport width ≥ 769px)**

- `Layout.css`: `.layout` uses `height` / `max-height: 100vh` and `overflow: hidden` so the app chrome stays within the window.
- `Layout.css`: `.main-content` uses `min-height: 0`, `overflow-y: auto`, and a column flex container so it can shrink inside the row, scroll the portfolio list when needed, and pass a bounded height to its children.
- `EntityDetail.css`: `.entity-detail` is `flex: 1` / `min-height: 0`; `.entity-zones--notebook` is a three-column grid (Resources, Chat, Artifacts) with `minmax(0, 1fr)` so columns shrink correctly; each `.zone` is a column flex card; `.zone-content` is `flex: 1` / `min-height: 0` / `overflow-y: auto` so lists and previews scroll inside the card.

**Mobile (width < 769px)**

- The layout is not locked to `100vh` the same way, so drawer/header behavior is unchanged.

**Preview panels**

- PDF iframes (`.preview-pdf`) use `min-height: 0` so they respect the constrained preview stack instead of forcing a large minimum height.

Relevant files: `frontend/src/components/Layout.css`, `frontend/src/components/EntityDetail.css`, `frontend/src/components/EntityDetail.tsx`.

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
