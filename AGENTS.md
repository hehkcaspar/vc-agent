# VC Portfolio Manager

## Project Overview

VC Portfolio Manager — a full-stack app for managing portfolio companies as canonical entities with a parking-lot ingestion workflow (no data loss). Backend is FastAPI + SQLAlchemy (async SQLite), frontend is React 18 + TypeScript + Vite + SWR.

### Core Architecture

1. **No Loss**: Every inbound submission persists to Parking Lot immediately before processing
2. **Downstream Simplicity**: All normal APIs operate only on canonical records
3. **Resolver Isolation**: Entity-matching complexity lives behind `EntityResolver`
4. **Storage Abstraction**: `StorageAdapter` interface for local FS → future cloud swap
5. **Unified Workspace**: Each entity has one hierarchical file tree (replaces old dual Resource/Artifact model)

---

## Technology Stack

- **Backend**: Python, FastAPI, SQLAlchemy (async), SQLite
- **Frontend**: React 18, TypeScript, Vite, SWR
- **Storage**: Local filesystem with `StorageAdapter` abstraction
- **AI**: Gemini (google-genai + LangChain), optional Kimi/Moonshot
- **Academic Module**: Separate DB + document store for scholar tracking

---

## Project Structure

```
vc-agent/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI app, CORS, lifespan
│   │   ├── models.py                  # ORM: entities, workspace_nodes, workspace_ops, chat, ingest
│   │   ├── schemas.py                 # Pydantic request/response models
│   │   ├── config.py                  # Settings from .env
│   │   ├── database.py                # Async + sync SQLAlchemy engines
│   │   ├── routers/
│   │   │   ├── entities.py            # Entity CRUD + workspace scaffolding
│   │   │   ├── workspace.py           # 26 workspace endpoints (tree, files, versions, trash, ops)
│   │   │   ├── chat.py                # Chat sessions, messages, presets, deep agent jobs
│   │   │   ├── ingest.py              # File/text/URL ingestion
│   │   │   ├── parkinglot.py          # Parking lot resolution
│   │   │   └── academic.py            # Academic tracking
│   │   └── services/
│   │       ├── workspace.py           # WorkspaceService (tree, write, move, version, provenance)
│   │       ├── workspace_tools.py     # 13 LangChain agent tools
│   │       ├── storage.py             # StorageAdapter (local FS)
│   │       ├── materializer.py        # WorkspaceMaterializer (ingest → Inbox/)
│   │       ├── parking.py             # ParkingLotManager
│   │       ├── resolver.py            # EntityResolver
│   │       ├── portfolio_deep_agent.py # Deep Agent harness
│   │       ├── gemini_context.py      # Workspace node → Gemini context
│   │       ├── gemini_runner.py       # Gemini API calls
│   │       └── academic/              # Academic tracking services
│   └── tests/
├── frontend/
│   └── src/
│       ├── components/
│       │   ├── EntityDetail.tsx        # Entity workspace tree + chat
│       │   ├── EntityConversation.tsx  # Chat UI with node selection
│       │   └── academic/              # Academic tracking UI
│       ├── services/api.ts            # API client (entities, workspace, chat)
│       ├── hooks/useEntities.ts       # SWR hooks (useWorkspaceTree)
│       └── types/index.ts             # TypeScript interfaces
└── doc/
    ├── ENTITY_WORKSPACE_DESIGN.md     # Workspace design spec (implemented)
    └── MVP-prd.md                     # Original MVP requirements
```

## Data Storage

```
data/entities/
  /00000/parkinglot/{ingest_id}/       # Parking lot (temporary)
  /{entity_id}/workspace/
    /blobs/{node_id}/{filename}        # File content (path-independent)
    /.versions/{node_id}/              # Version snapshots + manifest.json

data/vc_portfolio.db                   # Portfolio SQLite (workspace_nodes, workspace_ops, entities, chat)
data/academic.db                       # Academic SQLite (scholars, events, channels)
data/scholars/{scholar_id}/            # Academic dossier files
```

## Workspace Model

Each entity has a single workspace tree:
- **`workspace_nodes`** — files, folders, bookmarks with materialized paths and parent_id
- **`workspace_ops`** — audit log for all mutations
- **Storage keys** decoupled from paths → moves/renames are DB-only
- **Versioning** — every overwrite snapshots old content
- **Provenance** — `origin_type` (upload|agent|ingest|shared) enforces write zones
- **Template** — new entities get: Inbox/, Data Room/, Technical/, Deliverables/ + WORKSPACE_NOTES.md

### Agent Tools (13)

Browse (7): `workspace_get_tree`, `workspace_list_files`, `workspace_read_file`, `workspace_search_files`, `workspace_create_folder`, `workspace_move`, `workspace_rename`

Write (6): `workspace_write_file`, `workspace_annotate`, `workspace_delete`, `workspace_file_versions`, `workspace_restore_version`, `workspace_history`

## Key Configuration

See `backend/.env_sample` for all options. Key settings:
- `GEMINI_API_KEY` — required for AI features
- `CHAT_USE_DEEP_AGENT` — enable deep agent mode (default false)
- `WORKSPACE_MAX_FILE_BYTES` — max file size (default 50MB)
- `LANGSMITH_TRACING` — optional LangSmith tracing

## Documentation

- `CLAUDE.md` — Claude Code instructions (authoritative)
- `docs/ARCHITECTURE.md` — Backend/frontend architecture
- `docs/API_REFERENCE.md` — Endpoint contracts and data models
- `docs/DEVELOPER_GUIDE.md` — Setup, workflow, testing
- `doc/ENTITY_WORKSPACE_DESIGN.md` — Workspace design spec
