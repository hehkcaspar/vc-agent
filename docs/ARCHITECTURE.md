# VC Portfolio Manager - Architecture Documentation

For setup and development workflow, see `DEVELOPER_GUIDE.md`.
For API contract details, see `API_REFERENCE.md`.
For documentation map, see `README.md`.

## Overview

The VC Portfolio Manager follows an **Entity-Canonical, Parking-Lot Ingestion** architecture designed for reliability and future extensibility. Each entity has a unified **hierarchical workspace** (replacing the old dual Resource/Artifact model) where all files live in a single tree with folder structure, versioning, and provenance tracking.

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
All normal portfolio/workspace APIs operate only on **canonical** records (never missing entity_id).

### 3. Resolver Isolation
All entity-matching complexity lives behind `EntityResolver`; other modules never implement matching logic.

### 4. Storage Abstraction
Business logic uses a `StorageAdapter` interface so local FS can be swapped for cloud storage later.

### 5. Unified Workspace
Each entity has one hierarchical workspace tree. No separate "resources" and "artifacts" — everything is a file or folder in the tree. Provenance metadata distinguishes uploads from agent-created deliverables.

## Backend Architecture

### Service Layer

```
┌─────────────────────────────────────────────────────────────┐
│                        API Routers                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐  │
│  │ /ingest  │  │/entities │  │/parking  │  │ /workspace │  │
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
│  │WorkspaceMaterializer│ │ WorkspaceService │                │
│  │ - materialize()  │  │ - write_file()   │                │
│  │ (→ Inbox/)       │  │ - move/rename()  │                │
│  └──────────────────┘  │ - get_tree()     │                │
│  ┌──────────────────┐  │ - annotate()     │                │
│  │  StorageAdapter  │  │ - versioning     │                │
│  │ - write_file()   │  └──────────────────┘                │
│  │ - copy_file()    │                                      │
│  │ - delete_recursive│                                      │
│  └──────────────────┘                                      │
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

#### WorkspaceMaterializer
- Converts `IngestItem` into `WorkspaceNode` entries under the entity's **Inbox/** folder
- Follows safety rule: **Copy → Verify → Write DB → Delete parking**
- Creates workspace node records with `origin_type="ingest"`
- Marks ingest item as `materialized`

#### WorkspaceService
- Manages the hierarchical workspace tree per entity
- Operations: create folder, write file, move, rename, annotate, delete (soft), version history, restore
- Scaffolds new workspaces with a default folder template (`Inbox`, `Data Room`, `Technical`, `Deliverables`, etc.)
- Creates a shared `WORKSPACE_NOTES.md` file for cross-file context
- Enforces provenance-based write protection (see below)
- Content-addressed blob storage with path-independent keys
- Moves and renames are DB-only operations (no file system changes)

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

#### Workspace provenance enforcement

Every `WorkspaceNode` has an `origin_type` field (`upload`, `agent`, `ingest`, `shared`, `user`) that records how the file entered the workspace. The write-protection rule is:

- **Agents cannot overwrite or delete user-uploaded files** (`origin_type="upload"` or `"ingest"`). If an agent tool attempts to write to an upload path, `WorkspaceService` raises `ProtectedFileError` with guidance to create a derivative file instead (e.g., `Data Room/pitch-deck.pdf` → `Deliverables/pitch-deck-analysis.md`).
- **Shared files** (e.g., `WORKSPACE_NOTES.md`, `origin_type="shared"`) are writable by agents.
- **Agent-created files** (`origin_type="agent"`) are freely editable by agents; old content is auto-versioned on overwrite.

This prevents agents from accidentally destroying original materials while allowing full creative freedom in the `Deliverables/` subtree and elsewhere.

#### 3-layer agent context

The Deep Agent system prompt is assembled from three layers so the agent understands the workspace without needing to browse on every turn:

1. **Auto-generated tree** — `WorkspaceService.build_annotated_tree_text()` renders the full workspace tree as indented text (folders, files, sizes). Injected into the system prompt so the agent can reference file paths immediately.
2. **Node descriptions** — Each file/folder can have a one-line description (set via `workspace_annotate`). These appear inline in the tree text, giving the agent semantic context about what each file contains.
3. **WORKSPACE_NOTES.md** — A shared markdown file for cross-file context, data quality issues, and information gaps. Appended to the tree context under a `--- Workspace Notes ---` separator. The agent is instructed to update this file after learning non-obvious context.

#### Workspace node metadata and async pre-process

- **`workspace_nodes.metadata_json`** stores one JSON object as SQLite **TEXT** (nullable). Contains descriptions, native file metadata, and Gemini-preprocessed summaries.
- **API surface:** Responses use a parsed **`metadata`** field (`dict` or `null`).
- **Row pre-process:** `POST /entities/{id}/metadata-preprocess` enqueues a background job for metadata extraction. Successful runs **merge** into existing JSON:
  - **`native_file_metadata`** — size/MIME-oriented hints
  - **`gemini_preprocessed`** — Gemini JSON output for a single attached file
- **Caveats (MVP):** No SQL-backed job table — status is **lost on API restart**. Idempotency: starting pre-process for the same entity + node while **pending/running** returns the existing **`job_id`**.

#### Portfolio chat (one-shot + optional Deep Agent)

- **Effective mode** for `POST .../messages`: `use_deep_agent` in the JSON body if provided, otherwise `CHAT_USE_DEEP_AGENT` in settings.
- **One-shot path:** synchronous model call (`generate_with_context`); response **`200`** with `ChatMessageResult` (no tools; no file writes from chat in this path).
- **Deep Agent path:** user message is saved immediately; a **`chat_completion_jobs`** row is created; response **`202`** with `job_id` and `user_message`. **`run_chat_agent_job`** runs after the response (FastAPI `BackgroundTasks`), executes the graph in **`asyncio.to_thread`**, updates **`step_detail`** for polling (tool hooks → status callback). Client calls **`GET .../chat/sessions/{id}/jobs/{job_id}`** until `succeeded` / `failed`, then loads messages.
- **Presets follow the same mode switch:** `PresetRunRequest.use_deep_agent` (falling back to `CHAT_USE_DEEP_AGENT`). The UI sends Agent On/Off into preset runs.
- **Tools** (`workspace_tools.build_workspace_tools`): 13 workspace tools:

| Tool | Purpose |
|------|---------|
| `workspace_get_tree` | Browse the workspace tree structure |
| `workspace_list_files` | List files and folders at a specific path |
| `workspace_read_file` | Read text content of a file (with PDF/Office extraction) |
| `workspace_search_files` | Search for files by name and path |
| `workspace_create_folder` | Create a folder (with parent auto-creation) |
| `workspace_move` | Move a file or folder to a new location |
| `workspace_rename` | Rename a file or folder in place |
| `workspace_write_file` | Write or overwrite a file (auto-versions old content) |
| `workspace_annotate` | Set a description on a file or folder |
| `workspace_delete` | Soft-delete a file or folder |
| `workspace_file_versions` | List version history for a file |
| `workspace_restore_version` | Revert a file to a previous version |
| `workspace_history` | View recent workspace operations |

- **Write zones (guardrail):** Agent tools enforce provenance-based write protection. User uploads are read-only to agents; agents create derivative files instead. See "Workspace provenance enforcement" above.
- **Profiles:** `model_profiles.py` — `gemini_google`, `kimi_moonshot`. **`CHAT_DEFAULT_MODEL_PROFILE`** when `model_profile_id` omitted.
- **Attachment materialization:** `gemini_context.py` + `deep_agent_office_extractors.py`. PDFs and Office formats become text for preamble / one-shot; multimodal parts where the profile supports it.
- **Frontend:** `EntityDetail` passes workspace mutation callbacks into `EntityConversation`. After a **successful** deep-agent job, the chat panel triggers workspace revalidation so new or updated files appear without a full page reload.

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
              ┌─────────────────────────┐
              │  WorkspaceMaterializer  │
              │  - Copy files to blob   │
              │  - Create workspace     │
              │    nodes under Inbox/   │
              │  - Write DB             │
              │  - Delete parking       │
              └────────┬────────────────┘
                       ▼
              ┌─────────────────┐
              │  Entity Detail  │
              │  (show workspace│
              │   file tree)    │
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
┌──────────────────────────┐
│  WorkspaceMaterializer   │
│  (files → Inbox/ nodes)  │
└──────────────────────────┘
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
    └── workspace/
        ├── blobs/
        │   └── {node_id}/         # Content-addressed blob storage
        │       └── pitch_deck.pdf  # Path-independent file content
        └── .versions/
            └── {node_id}/         # Version history per file
                ├── v1              # Previous version snapshots
                └── v2
```

The workspace tree structure is stored in the **`workspace_nodes`** table, not in the file system. Moves and renames update the DB path only — blob storage keys are stable. This decouples logical organization from physical storage.

SQLite **`create_all`** runs on startup. Optional offline reset: `backend/scripts/reset_sqlite_db.py --yes` (stop the API first).

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

-- Workspace nodes (unified file tree per entity)
workspace_nodes (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    node_type TEXT NOT NULL,        -- file | folder | bookmark
    name TEXT NOT NULL,             -- display name
    path TEXT NOT NULL,             -- materialized: "Data Room/Financials/Q4.xlsx"
    parent_id TEXT,                 -- FK to workspace_nodes.id
    mime_type TEXT,                 -- file-specific
    size_bytes INTEGER,
    checksum TEXT,                  -- SHA-256 of current content
    storage_key TEXT,               -- path-independent blob key
    url TEXT,                       -- bookmark nodes only
    version INTEGER DEFAULT 1,
    origin_type TEXT,               -- upload | agent | ingest | shared | user
    origin_ref TEXT,                -- e.g., ingest_id, agent_run_id
    metadata_json TEXT,             -- descriptions, preprocessed data
    deleted_at TIMESTAMP,           -- soft delete
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    FOREIGN KEY (entity_id) REFERENCES entities(id),
    FOREIGN KEY (parent_id) REFERENCES workspace_nodes(id)
)

-- Workspace operations (audit log for all mutations)
workspace_ops (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    batch_id TEXT,                  -- group for atomic undo
    op_type TEXT NOT NULL,          -- create_file | create_folder | overwrite |
                                   -- move | rename | copy | delete | restore |
                                   -- upload_tree | extract_zip
    actor_type TEXT NOT NULL,       -- user | agent | system
    actor_ref TEXT,
    node_id TEXT,
    payload_json TEXT NOT NULL,     -- op-specific data
    inverse_json TEXT,              -- for undo
    before_checksum TEXT,
    after_checksum TEXT,
    undone_at TIMESTAMP,
    created_at TIMESTAMP,
    FOREIGN KEY (entity_id) REFERENCES entities(id)
)

-- Chat (sessions + messages + async deep-agent jobs)
-- conversation_sessions, conversation_messages: see SQLAlchemy models
-- chat_completion_jobs: pending/running deep-agent work; links user_message_id → assistant_message_id when done
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
                ├── entity-zones--notebook (three columns on desktop: Workspace | Chat | Workspace)
                ├── WorkspaceZone (.zone)
                │   ├── ZoneHeader + workspace tree browser
                │   └── .zone-content (scrolls)
                │       ├── File tree with folders
                │       └── FilePreview (PDF/Image/Text/HTML viewer)
                ├── EntityConversation (.zone--chat-main): sessions, transcript, **Run preset** dashed shortcuts (mode follows Agent On/Off), **Agent** pill (persistent mode, On/Off + `use_deep_agent`), composer shell (+ / send), **async polling** on `202` + status line in textarea; optional `ChatModelProfileContext` / sidebar model selector (Layout)
                └── Workspace details/viewer panel
```

### Viewport layout and scrolling

The shell and entity detail view are wired so **long file previews** (for example DOCX rendered as HTML) scroll **inside the workspace column**, not by growing the whole document.

**Desktop (viewport width >= 769px)**

- `Layout.css`: `.layout` uses `height` / `max-height: 100vh` and `overflow: hidden` so the app chrome stays within the window.
- `Layout.css`: `.main-content` uses `min-height: 0`, `overflow-y: auto`, and a column flex container so it can shrink inside the row, scroll the portfolio list when needed, and pass a bounded height to its children.
- `EntityDetail.css`: `.entity-detail` is `flex: 1` / `min-height: 0`; `.entity-zones--notebook` is a three-column grid with `minmax(0, 1fr)` so columns shrink correctly; each `.zone` is a column flex card; `.zone-content` is `flex: 1` / `min-height: 0` / `overflow-y: auto` so lists and previews scroll inside the card.

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

4. **Workspace Automation**
   - Agents create deliverables directly in workspace tree
   - Version history and audit log track all changes
   - Provenance enforcement protects user uploads

5. **Search/Filtering**
   - Add search endpoints
   - Frontend already has UI structure

6. **Multi-tenancy**
   - Add `tenant_id` to all tables
   - Filter all queries by tenant
   - Add row-level security

## Academic Tracking Module (v2)

Scholar-centric tracking with goal-driven Deep Agents. Separate from portfolio — own SQLite DB (`data/academic.db`), own models/schemas/router/services. Full design spec: `doc/ACADEMIC_TRACKING_V2_DESIGN.md`.

### Architecture

```
┌──────────────────────┐     ┌───────────────────────────────────┐     ┌─────────────────┐
│  Frontend             │     │  Scholar Agent (BackgroundTask)   │     │  External APIs  │
│  AcademicTab          │     │  invoke_scholar_agent(id, goal)   │     │                 │
│   ├─ List / Ranking   │────▶│                                   │────▶│  Google Scholar  │
│   ├─ Signal Feed      │     │  12 closure-bound tools:          │     │  (via SerpAPI)  │
│   └─ Digest           │     │    compute_bibliometrics          │     │                 │
│  ScholarDetail        │◄────│    fetch_gs_metrics (SerpAPI)     │     │  Semantic       │
│   ├─ Report           │     │    crawl_url                      │     │  Scholar API    │
│   ├─ Timeline         │     │    search_semantic_scholar         │     │                 │
│   ├─ Evaluation       │     │    fetch_ss_papers                │     │  Gemini API     │
│   ├─ Publications     │     │    search_web / search_patents    │     │  (+ Google      │
│   ├─ Profiles         │     │    append_event / sync_sql_index  │     │   Search)       │
│   └─ Chat             │     │    read_file / write_file (vfs)   │     │                 │
│  RankingView          │     │                                   │     │                 │
└──────────────────────┘     └───────────────────────────────────┘     └─────────────────┘
         │                                    │
         │  REST API (38 endpoints)           │  Documents + SQL
         ▼                                    ▼
┌──────────────────────────────────────────────────────────────┐
│  Storage                                                      │
│  ┌─────────────────────────┐  ┌────────────────────────────┐ │
│  │  Document Store          │  │  SQL Index (academic.db)   │ │
│  │  data/scholars/{id}/     │  │  scholars                  │ │
│  │    profile.json          │  │  scholar_events            │ │
│  │    papers.json           │  │  channels                  │ │
│  │    events.jsonl          │  │  chat_sessions/messages    │ │
│  │    channels.json         │  │  chat_jobs                 │ │
│  │    evaluations/*.json    │  └────────────────────────────┘ │
│  │    reports/*.md          │                                  │
│  │  data/config/            │                                  │
│  │    ranking_presets/      │                                  │
│  │    digests/              │                                  │
│  └─────────────────────────┘                                  │
└──────────────────────────────────────────────────────────────┘
```

### Two-Layer Storage

| Layer | Purpose | Technology | Source of Truth |
|-------|---------|------------|-----------------|
| **Document Store** | Full scholar state — everything the agent reads/writes | JSON/JSONL/markdown files per scholar dossier | Yes |
| **SQL Index** | Cross-scholar queries, scheduling, signal feed | SQLite tables (scholars, scholar_events, channels) | No — rebuildable via `sync_sql_index` tool |

### Agent Goals

All goals use the same agent factory and toolkit. The goal prompt determines behaviour:

| Goal | Trigger | What It Does |
|------|---------|--------------|
| Initial evaluation | POST /scholars/{id}/evaluate | Identity extraction, paper fetch, bibliometrics, 7-dimension scoring, report |
| Refresh | POST /scholars/{id}/refresh | Re-fetch papers, update metrics, rescore, compute delta |
| Chat | POST /scholars/{id}/chat/sessions/{sid}/messages | Multi-turn conversation with scholar context |
| Comparative | POST /scholars/{id}/compare/{other_id} | Side-by-side evaluation of two scholars |
| Upload processing | POST /scholars/{id}/uploads | Analyse user-uploaded documents |
| Digest | POST /digest/generate | Weekly portfolio summary (direct Gemini, no agent) |

### Backend Service Modules

| Module | Responsibility |
|--------|---------------|
| `routers/academic.py` | 38 REST endpoints — thin handlers delegating to services |
| `services/academic/file_utils.py` | Shared `dossier_path()`, `read_json()`, `write_json()`, `append_jsonl()` |
| `services/academic/evaluation_service.py` | Eval normalisation, delta computation, score extraction, background eval/refresh/comparative tasks |
| `services/academic/chat_service.py` | Background chat job execution |
| `services/academic/digest_service.py` | Weekly digest generation |
| `services/academic/scholar_agent.py` | Deep Agents harness — `invoke_scholar_agent()`, `invoke_scholar_chat()`, `_extract_text()` for Gemini content normalisation |
| `services/academic/domain_tools.py` | 12 tools built via `build_scholar_tools(scholar_id)` closure |
| `services/academic/heartbeat.py` | Background scheduler (stale refresh, channel polling, scheduled digest) |
| `services/academic/channel_pollers.py` | Google Scholar / Semantic Scholar change detection |

### Key Design Decisions

1. **Minimal SQL, rich documents**: SQL for cross-scholar queries and scheduling only. All agent-readable state lives in JSON/JSONL/markdown files per scholar dossier. No migrations for new fields.

2. **URL-first identity extraction**: Input URLs are pre-classified deterministically (`classify_urls`) before the agent runs. Google Scholar `user=` parameter, SS author ID, LinkedIn URL etc. are extracted without LLM involvement. Agent output is overridden with pre-extracted IDs to prevent hallucination.

3. **Google Scholar stats are authoritative**: h-index, i10-index, and citations from GS are never overridden by Semantic Scholar.

4. **Closure-bound tools**: `build_scholar_tools(scholar_id)` returns 12 `@tool`-decorated functions with the scholar's dossier path pre-bound. The agent never sees or passes UUIDs.

5. **`@tool` docstring rule**: The `@tool` decorator requires the docstring as the FIRST statement in the function body. Logger calls or any other code before the docstring breaks the decorator.

6. **Stuck-evaluating recovery**: Server startup resets all scholars with status "evaluating" back to "active" (handles server restart mid-background-task).

7. **Hard delete**: Deleting a scholar removes its dossier directory and cascades to all SQL rows (events, channels, chat sessions/messages/jobs).

8. **Event date vs discovery date**: The `append_event` tool accepts an optional `event_date` parameter (ISO date string) for when the event actually occurred (e.g., `"2017-06-01"` for a company founding). If omitted, defaults to current time. Events in the document store record both `date` (when it happened) and `discovered_at` (when the agent found it). The Timeline UI shows both dates when they differ.

9. **Gemini content block handling**: Gemini models may return `.content` as a list of content blocks (`[{"type": "text", "text": "..."}]`) rather than a plain string. `_extract_text()` in `scholar_agent.py` normalises this before the reply is stored in the DB or dossier trace files.

### SQL Tables (academic.db)

| Table | Purpose |
|-------|---------|
| `scholars` | id, name, status, tracking_priority, tags, entity_id, dossier_path |
| `scholar_events` | id, scholar_id, event_type, significance, title, is_read, event_date (when it happened), created_at (when discovered) |
| `channels` | id, scholar_id, channel_type, url, is_active, polling_interval_hours, last_polled_at, poll_error_count |
| `academic_chat_sessions` | id, scholar_id, title, created_at, updated_at |
| `academic_chat_messages` | id, session_id, role, content, created_at |
| `academic_chat_jobs` | id, scholar_id, session_id, status, user_message_id, assistant_message_id, agent_run_id, step_detail, error_message |
