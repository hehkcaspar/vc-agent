# VC Portfolio Manager - API Reference

For architecture/data-flow context, see `ARCHITECTURE.md`.
For setup and local workflow, see `DEVELOPER_GUIDE.md`.
For documentation map, see `README.md`.

## Base URL
```
http://localhost:8000
```

## Endpoints

### Ingestion

#### POST /ingest/resources
Main ingestion endpoint for all incoming content.

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| files | File[] | No | Files to upload (PDF, images, text) |
| text | string | No | Free text content |
| urls | string | No | JSON array of URLs |
| entity_id | string | No | Target entity ID (if known) |
| entity_hint_name | string | No | Entity name hint for matching |
| entity_hint_domain | string | No | Domain hint for matching |

**Responses:**

**200 OK - Resolved**
```json
{
  "status": "resolved",
  "entity_id": "uuid",
  "resources": [...]
}
```

**200 OK - Resolution Required**
```json
{
  "status": "resolution_required",
  "ingest_id": "uuid",
  "candidates": [...]
}
```

**200 OK - Failed**
```json
{
  "status": "failed",
  "ingest_id": "uuid",
  "error": "error message"
}
```

---

### Entities

#### GET /entities
List all entities (sorted by updated_at desc).

**Query Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| skip | int | 0 | Pagination offset |
| limit | int | 100 | Max items to return |

**Response:**
```json
[
  {
    "id": "uuid",
    "type": "company",
    "name": "Company Name",
    "website": "https://example.com",
    "status": "active",
    "created_at": "2024-01-01T00:00:00",
    "updated_at": "2024-01-01T00:00:00"
  }
]
```

#### POST /entities
Create a new entity directly.

**Request Body:**
```json
{
  "name": "Company Name",
  "website": "https://example.com"
}
```

#### GET /entities/{id}
Get entity details.

#### PATCH /entities/{id}
Update entity.

**Request Body:**
```json
{
  "name": "New Name",
  "website": "https://new-website.com",
  "status": "archived"
}
```

#### DELETE /entities/{id}
Delete entity and all associated workspace nodes.

---

### Workspace

All workspace endpoints are scoped to an entity: `/entities/{entity_id}/workspace/...`

The workspace replaces the former Resource + Artifact model with a unified hierarchical file system. Each entity has a tree of **nodes** (files, folders, bookmarks). Files support versioning, soft-delete (trash), and metadata enrichment.

#### Tree

##### GET /entities/{entity_id}/workspace/tree
Return the full workspace tree (recursive).

**Query Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| path | string | "" | Subtree root path (empty = entire tree) |
| depth | int | 10 | Max depth (1-20) |

**Response:** Array of `WorkspaceTreeNode` (recursive `children`).

```json
[
  {
    "id": "uuid",
    "name": "Data Room",
    "node_type": "folder",
    "path": "Data Room",
    "size_bytes": null,
    "mime_type": null,
    "description": "Due diligence documents",
    "version": null,
    "children": [
      {
        "id": "uuid",
        "name": "Q4.xlsx",
        "node_type": "file",
        "path": "Data Room/Q4.xlsx",
        "size_bytes": 12345,
        "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "description": null,
        "version": 2,
        "children": []
      }
    ]
  }
]
```

##### GET /entities/{entity_id}/workspace/ls
List direct children of a directory.

**Query Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| path | string | "" | Directory path (empty = root) |

**Response:** Array of `WorkspaceNodeResponse`.

##### GET /entities/{entity_id}/workspace/node/{node_id}
Get a single node by ID.

**Response:** `WorkspaceNodeResponse`.

##### GET /entities/{entity_id}/workspace/search
Search workspace files by name/path substring.

**Query Parameters:**
| Param | Type | Description |
|-------|------|-------------|
| q | string | Search query (matched against name and path, case-insensitive) |

**Response:** Array of `WorkspaceNodeResponse`.

#### Files

##### GET /entities/{entity_id}/workspace/file/{node_id}
Download a file's content. Returns `FileResponse` for files, JSON `{"url", "type"}` for bookmarks, 400 for folders.

##### GET /entities/{entity_id}/workspace/file?path=...
Download a file by its workspace path.

**Query Parameters:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| path | string | yes | Full workspace path to the file |

**Response:** `FileResponse` with inferred MIME type.

##### POST /entities/{entity_id}/workspace/file?path=...
Upload a single file to a specific path. Creates intermediate folders automatically.

**Query Parameters:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| path | string | yes | Target workspace path |

**Request:** `multipart/form-data` with a single `file` field.

**Response:** `WorkspaceNodeResponse` for the created/overwritten file.

##### POST /entities/{entity_id}/workspace/upload
Bulk upload multiple files, preserving relative paths.

**Query Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| base_path | string | "Inbox" | Base folder for uploads |

**Request:** `multipart/form-data` with `files` field (multiple).

**Response:**
```json
{
  "uploaded": 3,
  "results": [
    { "id": "uuid", "path": "Inbox/report.pdf", "size": 12345 },
    { "path": "Inbox/bad.bin", "error": "File too large" }
  ]
}
```

##### POST /entities/{entity_id}/workspace/folder?path=...
Create a new folder.

**Query Parameters:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| path | string | yes | Folder path to create |

**Response:** `WorkspaceNodeResponse` for the created folder.

#### Versioning

##### GET /entities/{entity_id}/workspace/file/{node_id}/versions
List all versions of a file.

**Response:**
```json
{
  "versions": [
    { "version": 1, "timestamp": "...", "checksum": "sha256", "size": 1234 },
    { "version": 2, "timestamp": "...", "checksum": "sha256", "size": 5678, "current": true }
  ]
}
```

##### GET /entities/{entity_id}/workspace/file/{node_id}/versions/{version}
Download a specific old version of a file.

**Response:** `FileResponse` with filename `v{version}_{name}`.

##### POST /entities/{entity_id}/workspace/file/{node_id}/restore/{version}
Restore a previous version as the new current version (creates a new version entry).

**Response:** `WorkspaceNodeResponse` for the updated file.

##### GET /entities/{entity_id}/workspace/file/{node_id}/diff?v1=...&v2=...
Unified text diff between two versions of a file. Returns 400 for binary files.

**Query Parameters:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| v1 | int | yes | First version number |
| v2 | int | yes | Second version number |

**Response:**
```json
{
  "node_id": "uuid",
  "v1": 1,
  "v2": 2,
  "diff": "--- v1/report.md\n+++ v2/report.md\n...",
  "has_changes": true
}
```

#### Mutations

##### POST /entities/{entity_id}/workspace/move
Move a node to a new path.

**Request Body:**
```json
{
  "from_path": "Inbox/report.pdf",
  "to_path": "Data Room/report.pdf"
}
```

**Response:** `WorkspaceNodeResponse` for the moved node.

##### POST /entities/{entity_id}/workspace/rename
Rename a node (same parent, new name).

**Request Body:**
```json
{
  "path": "Data Room/old-name.pdf",
  "new_name": "new-name.pdf"
}
```

**Response:** `WorkspaceNodeResponse` for the renamed node.

##### POST /entities/{entity_id}/workspace/copy
Copy a node to a new path.

**Request Body:**
```json
{
  "from_path": "Data Room/report.pdf",
  "to_path": "Archive/report.pdf"
}
```

**Response:** `WorkspaceNodeResponse` for the new copy.

##### DELETE /entities/{entity_id}/workspace/node?path=...
Soft-delete a node (moves to trash).

**Query Parameters:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| path | string | yes | Path of node to delete |

**Response:**
```json
{
  "message": "Deleted 'Data Room/report.pdf'",
  "node_id": "uuid"
}
```

#### Trash

##### GET /entities/{entity_id}/workspace/trash
List all soft-deleted nodes.

**Response:** Array of `WorkspaceNodeResponse` (with `deleted_at` set).

##### POST /entities/{entity_id}/workspace/trash/{node_id}/restore
Restore a soft-deleted node.

**Response:** `WorkspaceNodeResponse` for the restored node.

##### DELETE /entities/{entity_id}/workspace/trash/{node_id}
Permanently delete a trashed node and its storage.

**Response:**
```json
{
  "message": "Permanently deleted",
  "node_id": "uuid"
}
```

#### History

##### GET /entities/{entity_id}/workspace/ops
List recent workspace operations (audit log).

**Query Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| limit | int | 50 | Max items (1-200) |

**Response:** Array of `WorkspaceOpResponse`.

```json
[
  {
    "id": "uuid",
    "op_type": "move",
    "actor_type": "user",
    "actor_ref": null,
    "node_id": "uuid",
    "payload": { "from_path": "...", "to_path": "..." },
    "created_at": "2024-01-01T00:00:00",
    "undone_at": null
  }
]
```

##### POST /entities/{entity_id}/workspace/ops/{op_id}/undo
Undo a workspace operation.

**Response:** Operation-specific undo result.

#### Metadata

##### POST /entities/{entity_id}/workspace/annotate
Set a description on a node.

**Request Body:**
```json
{
  "path": "Data Room/report.pdf",
  "description": "Q4 2024 financial report"
}
```

**Response:** `WorkspaceNodeResponse` for the updated node.

##### PATCH /entities/{entity_id}/workspace/node/{node_id}
Update node metadata and/or name.

**Request Body (any combination):**
```json
{
  "name": "renamed.pdf",
  "metadata": { "custom_key": "value" }
}
```

`metadata` is merged with existing metadata. Set to `null` to clear.

**Response:** `WorkspaceNodeResponse` for the updated node.

##### POST /entities/{entity_id}/workspace/node/{node_id}/metadata-preprocess
Start async Gemini metadata extraction for a file node.

**Response:** `200 OK`
```json
{ "job_id": "uuid" }
```

If a job for the same node is already pending/running, the same `job_id` is returned.

##### GET /entities/{entity_id}/workspace/metadata-preprocess-jobs/{job_id}
Poll metadata extraction job status.

**Response:**
```json
{
  "job_id": "uuid",
  "status": "pending|running|succeeded|failed",
  "error_message": "optional when failed"
}
```

##### POST /entities/{entity_id}/workspace/upload-zip
Upload a zip file; backend unpacks under `Inbox/<root>/` preserving the internal tree.

**Multipart form**: `file=<zip>`

**Behavior**:
- If every zip entry shares a single top-level directory, that directory is used as the root verbatim (no double-nesting).
- Otherwise, entries land under `Inbox/<zip-basename>/`.
- Total zip size capped at `WORKSPACE_MAX_ZIP_BYTES` (default 500 MB) → `413` if exceeded.
- Per-entry size capped at `WORKSPACE_MAX_FILE_BYTES` → `413` per offending entry.
- Zip-slip protection: entries with `..` or absolute paths → `400`.

**Response:** `200 OK`
```json
{
  "uploaded": 12,
  "base_path": "Inbox/Series A Closing",
  "results": [
    { "id": "node-uuid", "path": "Inbox/Series A Closing/SPA.pdf", "size": 234500 }
  ]
}
```

##### POST /entities/{entity_id}/workspace/inbox/process
Start the **Process Inbox** batch intake job. Walks `Inbox/` direct children, extracts metadata for loose files (Path A), routes user-uploaded folders by structure (Path B), and moves everything to its destination in the workspace taxonomy.

Only one job may run per entity at a time. If one is already pending/running, the existing `job_id` is returned.

**Response:** `202 Accepted`
```json
{ "job_id": "uuid" }
```

See ARCHITECTURE.md → "Process Inbox" for the routing logic and the `intake_routing` metadata schema written on every processed file.

##### GET /entities/{entity_id}/workspace/inbox/process/{job_id}
Poll Process Inbox job status. Frontend polls at 1s intervals while running.

**Response:** `200 OK`
```json
{
  "job_id": "uuid",
  "status": "pending|running|succeeded|failed",
  "total_items": 12,
  "processed_items": 8,
  "current_item": "Inbox/some-folder",
  "moved": [
    {
      "from": "Inbox/spa.pdf",
      "to": "Data Room/Legal/Series A Closing/spa.pdf",
      "batch_name": "Series A Closing",
      "joined_existing": false
    }
  ],
  "needs_triage": [
    { "path": "Inbox/blank.png", "reason": "binary blob" }
  ],
  "errors": [
    { "path": "Inbox/corrupted.pdf", "error": "extract: invalid pdf" }
  ],
  "folder_decisions": [
    {
      "folder": "Inbox/Series A Closing Binder",
      "action": "place_whole",
      "destination": "Data Room/Legal",
      "join_existing": null,
      "rename_root_to": "Series A Closing",
      "reason": "structure indicates Series A binder"
    }
  ],
  "error_message": null
}
```

---

### Parking Lot

#### GET /parkinglot
List parking lot items.

**Query Parameters:**
| Param | Type | Description |
|-------|------|-------------|
| status | string | Filter by status: parked, resolution_required, failed, materialized |

#### GET /parkinglot/{ingest_id}
Get specific parking lot item.

#### POST /parkinglot/{ingest_id}/resolve
Resolve a parking lot item to an entity.

**Request Body (attach to existing):**
```json
{
  "entity_id": "uuid"
}
```

**Request Body (create new):**
```json
{
  "create_entity": {
    "name": "New Company Name"
  }
}
```

---

## Entity chat (one-shot + optional Deep Agent harness)

All routes are scoped to an existing entity.

### Which path runs for `POST .../messages`?

The server computes an **effective** deep-agent flag per request:

```text
use_deep_agent = body.use_deep_agent if body.use_deep_agent is not None else CHAT_USE_DEEP_AGENT
```

- **`use_deep_agent` true:** LangChain **Deep Agents** (`create_deep_agent`) with entity-scoped tools (workspace-aware tools in `portfolio_deep_agent.py`). The HTTP handler **persists the user message**, enqueues a **`chat_completion_jobs`** row, returns **`202 Accepted`**, and runs the agent in a **background task** so the client can keep using the API and **poll** job status for step text.
- **`use_deep_agent` false:** one-shot model call (`generate_with_context`). Returns **`200 OK`** with the assistant message in the body (no job).

The SPA persists an **Agent** on/off toggle (`use_deep_agent` on each send) in `localStorage`; that overrides the server default when set. **Presets** (`POST .../presets/.../run`) also accept `use_deep_agent` and `model_profile_id`, so shortcut runs can follow the same mode/profile selection.

**Context selection:** Selected `node_ids` inline excerpts from workspace files into the user turn and help edit resolution. They are **optional** in Agent mode: tools can **list/read** all entity workspace nodes without prior selection.

### Environment (summary)

| Variable | Purpose |
|----------|---------|
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Legacy chat + presets |
| `GEMINI_MODEL` | Main chat model id (default `gemini-3.1-pro-preview`) |
| `GEMINI_METADATA_EXTRACTION_MODEL` | Presets such as `extract_info` and **node metadata pre-process** (file lookup JSON); default `gemini-3.1-flash-lite-preview` |
| `CHAT_USE_DEEP_AGENT` | Server default for deep agent when request omits `use_deep_agent` |
| `CHAT_DEFAULT_MODEL_PROFILE` | Default profile id if client omits `model_profile_id` |
| `CHAT_AGENT_RECURSION_LIMIT` | LangGraph recursion limit (default 50) |
| `WORKSPACE_MAX_FILE_BYTES` | Max upload size per file (default 50 MB) |
| `WORKSPACE_VERSION_RETENTION_DAYS` | How long old file versions are kept (default 30 days) |
| `MOONSHOT_API_KEY`, `KIMI_CODE_API_KEY`, URLs, `KIMI_CODE_MODEL`, etc. | Moonshot Open Platform vs Kimi Code routing — see `backend/app/config.py`, `backend/.env_sample`, `model_profiles.py` |

Other: `CHAT_ENABLE_GOOGLE_SEARCH`, attachment/history limits. Deep-agent steps are pushed to `chat_completion_jobs.step_detail` for UI polling.

**Deep-agent workspace tools (summary):**

- **`portfolio_list_workspace` / `portfolio_search_workspace`** — discover workspace files and folders.
- **`portfolio_read_file`** — read file content (text payloads, parsed PDF/Office text).
- **`portfolio_create_file`** — create a new file in the workspace tree.
- **`portfolio_edit_file`** — overwrite/update an existing file (creates a new version).

### `GET /entities/{entity_id}/chat/presets`

List shortcut presets. Each preset has an `id` (for example `red_team`, `extract_info`), display fields, and output hints. **Preset run** behavior is defined in `backend/app/services/preset_registry.py`: markdown-style outputs are stored as `.md` files; `extract_info` produces a versioned **JSON** file (title `extract_info`) with structured company metadata.

### `GET /entities/{entity_id}/chat/sessions`

List conversation sessions for the entity (newest first by `updated_at`).

### `POST /entities/{entity_id}/chat/sessions`

Create a new empty session. Optional JSON body: `{ "title": "Q1 diligence" }`.

### `GET /entities/{entity_id}/chat/sessions/{session_id}`

Session metadata and all messages (ascending by time).

### `POST /entities/{entity_id}/chat/sessions/{session_id}/messages`

Send a user turn. JSON body:

```json
{
  "text": "What are the top risks?",
  "node_ids": ["uuid"],
  "model_profile_id": "gemini_google",
  "use_deep_agent": true
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `text` | yes | User message |
| `node_ids` | no | Workspace node IDs to emphasize this turn |
| `model_profile_id` | no | `gemini_google` \| `kimi_moonshot` (harness only) |
| `use_deep_agent` | no | If set, overrides server `CHAT_USE_DEEP_AGENT` for **this** message |

**Responses**

- **`202 Accepted`** — Deep agent path. Body (`ChatMessageJobAccepted`): `job_id`, `user_message` (already stored), `warnings`. Client should **poll** `GET .../jobs/{job_id}` until `status` is `succeeded` or `failed`; then load session messages or read `assistant_message` from the job payload.
- **`200 OK`** — Legacy path. Body (`ChatMessageResult`): `assistant_message`, `warnings`.

Legacy: multimodal context where supported. Harness: text-inline preamble from selections; tools can fetch the rest. Search behavior follows the active profile when enabled.

### `GET /entities/{entity_id}/chat/sessions/{session_id}/jobs/{job_id}`

Poll deep-agent progress after a `202` from `POST .../messages`. Returns `ChatMessageJobStatus`: `status` (`pending` \| `running` \| `succeeded` \| `failed`), `step_detail` (human-readable step for UI), optional `assistant_message` when succeeded, `error_message`, `warnings`, `run_id`, `tool_trace`.

### `POST /entities/{entity_id}/chat/presets/{preset_id}/run`

Run a preset and **create a new workspace file** (markdown or JSON, depending on preset). JSON body:

```json
{
  "node_ids": [],
  "session_id": "required when use_deep_agent=true",
  "industry": "optional",
  "stage": "optional",
  "deliverable_type": "optional override",
  "deliverable_status": "optional override",
  "model_profile_id": "gemini_google",
  "use_deep_agent": true
}
```

**Response varies with `use_deep_agent`:**

- **`use_deep_agent: false`** (synchronous one-shot) — returns **`200 OK`** with `PresetRunResponse`:
  ```json
  { "node_id": "uuid", "assistant_summary": "...", "warnings": [] }
  ```

- **`use_deep_agent: true`** (background deep-agent run) — returns **`202 Accepted`** with `PresetRunJobAccepted`:
  ```json
  {
    "job_id": "uuid",
    "session_id": "uuid",
    "user_message": { "...": "synthetic '▶ Run preset: <label>' message" },
    "warnings": [],
    "status": "pending"
  }
  ```
  The handler inserts a synthetic user message into the session, creates a `ChatCompletionJob` row (with `preset_payload_json` set so the worker knows to run the preset flow), and schedules `run_preset_agent_job(job_id)` as a FastAPI `BackgroundTasks`. The worker writes `step_detail` exactly like `run_chat_agent_job`, and on success writes the deliverable to the workspace and appends the deliverable-card assistant message.

  **Poll the existing chat-job endpoint** to track progress — no separate preset-job endpoint:

      GET /entities/{entity_id}/chat/sessions/{session_id}/jobs/{job_id}

  The frontend reuses its `agentJob` polling loop and spinner status line verbatim for preset runs.

Notes:
- `session_id` is **required** when `use_deep_agent=true` (the job + assistant card message attach to a session).
- `extract_info` is force-pinned to `use_deep_agent=false` server-side (always one-shot).
- `red_team` honors the client toggle exactly — it does NOT auto-promote to agent mode.
- `extract_info` applies tolerant JSON parsing (raw JSON, fenced JSON, or prose-wrapped JSON object) before normalization.

---

## Data Models

### Entity
| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| type | string | "company" (MVP only) |
| name | string | Entity name (required) |
| website | string | Optional website URL |
| status | string | "active" or "archived" |
| created_at | datetime | Creation timestamp |
| updated_at | datetime | Last update timestamp |

### WorkspaceNode
| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| entity_id | UUID | Parent entity |
| node_type | string | "file", "folder", or "bookmark" |
| name | string | Display name |
| path | string | Materialized path (e.g. "Data Room/Financials/Q4.xlsx") |
| parent_id | UUID | Parent folder node (null for root-level nodes) |
| mime_type | string | MIME type (files only) |
| size_bytes | int | File size in bytes (files only) |
| checksum | string | SHA-256 of current content (files only) |
| storage_key | string | Path-independent blob key (files only) |
| url | string | Target URL (bookmarks only) |
| version | int | Current version number (files; default 1) |
| origin_type | string | "upload", "ingest", "agent", "preset" |
| metadata | object \| null | Parsed from `metadata_json`; API never returns raw text |
| created_at | datetime | Creation timestamp |
| updated_at | datetime | Last update timestamp |
| deleted_at | datetime | Soft-delete timestamp (null if alive) |

Unique constraint on `(entity_id, path)` where `deleted_at IS NULL`.

After **metadata pre-process** succeeds, `metadata` often includes top-level keys such as **`native_file_metadata`** and **`gemini_preprocessed`** (merged with any existing object).

### WorkspaceOp (audit log)
| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| entity_id | UUID | Parent entity |
| batch_id | string | Group ID for atomic undo |
| op_type | string | create_file, create_folder, overwrite, move, rename, copy, delete, restore, upload_tree, extract_zip |
| actor_type | string | "user", "agent", or "system" |
| actor_ref | string | Optional actor reference |
| node_id | UUID | Affected node |
| payload_json | text | Operation-specific data |
| inverse_json | text | Data needed for undo |
| before_checksum | string | File checksum before mutation |
| after_checksum | string | File checksum after mutation |
| created_at | datetime | Operation timestamp |
| undone_at | datetime | When undone (null if not undone) |

### Chat completion job (Deep Agent only)

Persisted in **`chat_completion_jobs`** for async turns. Not exposed as a full CRUD resource; use the job GET above. Stores `status`, `step_detail`, FKs to `user_message_id` / `assistant_message_id`, serialized `node_ids_json`, `model_profile_id`, `harness_extras`, `warnings_json`, `tool_trace_json`, `agent_run_id`.

### IngestItem (Parking Lot)
| Field | Type | Description |
|-------|------|-------------|
| ingest_id | UUID | Primary key |
| source | string | "frontend", "email", "im", "api" |
| status | string | "parked", "resolution_required", "failed", "materialized" |
| parkinglot_path | string | Relative path to stored files |
| entity_hint_name | string | Optional name hint |
| entity_hint_domain | string | Optional domain hint |
| error | string | Error message if failed |
| created_at | datetime | Creation timestamp |
| updated_at | datetime | Last update timestamp |

---

## Academic Tracking (v2)

Scholar-centric module with goal-driven agent. All endpoints prefixed `/academic/`.

### Scholar CRUD

#### POST /academic/scholars
Create a new scholar and initialise dossier directory.

**Request body (JSON):**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| name | string | yes | Scholar name |
| urls | string[] | no | Homepage, Google Scholar, lab page URLs |
| tracking_priority | string | no | "high", "medium" (default), or "low" |
| tags | string[] | no | Freeform tags |
| entity_id | string | no | Link to portfolio entity |
| user_notes | string | no | Analyst notes |

URLs are pre-classified deterministically — GS/SS/LinkedIn IDs extracted without LLM. **Response:** `ScholarResponse`.

#### GET /academic/scholars?page=1&page_size=20&status=active&priority=high&search=name
List scholars (paginated, enriched from profile.json).

#### GET /academic/scholars/{scholar_id}
Get single scholar (SQL + profile.json merge).

#### PUT /academic/scholars/{scholar_id}
Update scholar fields. `user_notes` writes to profile.json, other fields to SQL.

#### DELETE /academic/scholars/{scholar_id}
Hard-delete. Removes dossier directory and cascades to all SQL rows. Allows deletion while evaluating (stops agent first).

### Agent — Evaluate / Stop / Refresh

#### POST /academic/scholars/{scholar_id}/evaluate
Run initial evaluation (background). Sets status to "evaluating". Returns `409` if already evaluating.

#### POST /academic/scholars/{scholar_id}/stop
Stop a running evaluation. Resets status to "active".

#### POST /academic/scholars/{scholar_id}/refresh
Re-evaluate an existing scholar (background). Fetches new papers, updates metrics, rescores, computes delta.

### Papers

#### GET /academic/scholars/{scholar_id}/papers?limit=50&sort_by=citations&author_position=first
Papers from dossier `papers.json`. Supports sort by `citations` or `year`, filter by author position (`first`, `last`, `middle`, `sole`).

### Evaluations

#### GET /academic/scholars/{scholar_id}/evaluations
List all evaluations (from `evaluations/*.json`), normalised and sorted newest-first. Includes delta vs previous evaluation when available.

### Reports

#### GET /academic/scholars/{scholar_id}/reports
List reports (from `reports/*.md`), sorted newest-first. Content not included in list view.

#### GET /academic/scholars/{scholar_id}/reports/{report_id}
Get single report with markdown content.

#### DELETE /academic/scholars/{scholar_id}/reports/{report_id}
Delete a report file.

### Events

#### GET /academic/scholars/{scholar_id}/events?limit=50&event_type=new_paper&significance=high
Scholar event timeline from SQL (event_type, significance filters). Each event has two temporal fields: `event_date` (when the event actually occurred — may be historical, e.g., a company founding in 2017) and `created_at` (when the system discovered/recorded it). The frontend timeline shows both dates when they differ.

#### PUT /academic/scholars/{scholar_id}/events/{event_id}
Update event fields (e.g., mark as read).

### Channels

#### GET /academic/scholars/{scholar_id}/channels
List monitoring channels for a scholar.

#### PUT /academic/scholars/{scholar_id}/channels/{channel_id}
Update channel (pause/resume via `is_active`, change `polling_interval_hours`).

### Signal Feed

#### GET /academic/signal-feed?limit=50
Cross-scholar unread events (high + medium significance), enriched with scholar names.

#### POST /academic/signal-feed/mark-read
Bulk mark events as read. `{ "event_ids": ["id1", "id2"] }` or `{ "event_ids": [] }` to mark all.

### Chat (per-scholar)

#### GET /academic/scholars/{scholar_id}/chat/sessions
List chat sessions for a scholar.

#### POST /academic/scholars/{scholar_id}/chat/sessions
Create a new chat session. `{ "title": "Discussion about recent papers" }`

#### GET /academic/scholars/{scholar_id}/chat/sessions/{session_id}
Get session with full message history.

#### DELETE /academic/scholars/{scholar_id}/chat/sessions/{session_id}
Delete session and all messages/jobs.

#### POST /academic/scholars/{scholar_id}/chat/sessions/{session_id}/messages
Send a message. Always async — returns `202` with `{ job_id, user_message, status: "pending" }`.

#### GET /academic/scholars/{scholar_id}/chat/sessions/{session_id}/jobs/{job_id}
Poll chat job status. Returns `{ status, assistant_message, error_message }`.

### Ranking

#### GET /academic/ranking?status=active&priority=high
All scholars with latest evaluation dimension scores for ranking.

#### GET /academic/ranking/presets
List weight presets (seeded with "Balanced", "Impact Focused", "VC Commercialization").

#### POST /academic/ranking/presets
Create custom weight preset. `{ "name": "My Preset", "weights": { "research_impact": 0.3, ... } }`

#### DELETE /academic/ranking/presets/{name}
Delete a weight preset.

### Comparative Evaluation

#### POST /academic/scholars/{scholar_id}/compare/{other_id}
Run comparative evaluation between two scholars (background). Sets scholar A to "evaluating".

### Digest

#### POST /academic/digest/generate
Generate a weekly portfolio digest (background Gemini call).

#### GET /academic/digests
List generated digests.

#### GET /academic/digests/{digest_id}
Get digest with markdown content.

### Uploads

#### POST /academic/scholars/{scholar_id}/uploads
Upload files to scholar's dossier. Triggers agent processing in background.

#### GET /academic/scholars/{scholar_id}/uploads
List uploaded files with size and modification time.

### Evaluation Dimensions

All evaluation dimensions — both the original defaults (`research_impact`, `commercialization`, `career_trajectory`, `collaboration_strength`, `field_position`, `founder_potential`, `public_profile`) and any user-added ones — live in a single file-backed config at `data/config/dimensions.json` and are treated uniformly by the CRUD endpoints below. The file is auto-seeded with the defaults on first read (see `backend/app/services/academic/dimensions.py`). The scholar agent prompt reads the list at runtime and interpolates it into the evaluation JSON schema and the scoring rubric, so adding, editing, or deleting a dimension takes effect on the next evaluation with no code changes.

> **Route name note**: the route path is still `/academic/custom-dimensions` for backwards compatibility, but the endpoint now manages the **full** dimension list, not just user-added ones. All dimensions are fully editable and deletable.

#### GET /academic/custom-dimensions
List all evaluation dimensions (defaults + user-added). Returns `[{ name, key, prompt }, ...]`.

#### POST /academic/custom-dimensions
Create a new dimension. Body: `{ "name": "Patent Quality", "key": "patent_quality", "prompt": "Assess patent filing activity, prosecution quality, and commercial licensing." }`. Returns `409` if `key` already exists.

#### PUT /academic/custom-dimensions/{key}
Update an existing dimension (including rename). Body matches POST. If `body.key != {key}` and the new key already exists, returns `409`.

#### DELETE /academic/custom-dimensions/{key}
Delete a dimension. Returns `404` if the key doesn't exist.

> **Operator warning**: ranking-preset weights in `backend/app/routers/academic.py` hardcode the default dimension keys. Deleting a default (e.g. `research_impact`) won't crash the API but will leave the built-in ranking presets referencing a missing key. Only delete defaults if you're aware of this.

### Academic Data Models

#### Scholar (SQL + profile.json)
| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| name | string | Scholar name |
| status | string | active, evaluating, paused, archived |
| tracking_priority | string | high, medium, low |
| tags | JSON | Freeform tags |
| entity_id | UUID | FK to portfolio entity (nullable) |
| dossier_path | string | Path to scholar's dossier directory |
| affiliation | string | From profile.json |
| h_index | integer | From profile.json metrics |
| identity | object | From profile.json (google_scholar, semantic_scholar, linkedin, homepage) |

#### Evaluation (from evaluations/*.json)
| Field | Type | Description |
|-------|------|-------------|
| id | string | Filename stem |
| type | string | full, comparative, refresh |
| dimensions | object | Map of `{dimension_key: { score, explanation, evidence }}` for every dimension defined in `data/config/dimensions.json`. Default keys: `research_impact`, `commercialization`, `career_trajectory`, `collaboration_strength`, `field_position`, `founder_potential`, `public_profile`. Users can add/remove/rename dimensions via the `/academic/custom-dimensions` endpoints, and the agent prompt updates accordingly on the next run. |
| computed_metrics | object | Bibliometric data |
| commercialization_signals | object | Patents, startups |
| delta | object | Changes vs previous evaluation |
| trigger | string | manual, scheduled, signal |

#### Paper (from papers.json)
| Field | Type | Description |
|-------|------|-------------|
| id | string | Paper ID |
| title | string | Paper title |
| authors | array | Author objects with name, id, position |
| year | integer | Publication year |
| citations | integer | Citation count |
| venue | string | Journal/conference |
| author_position | string | first, last, middle, sole |
| fields_of_study | string[] | Research areas |
