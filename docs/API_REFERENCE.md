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
    "deal_stage": "diligence",
    "metadata": { "...parsed entities.metadata_json (or null)..." },
    "last_content_at": null,
    "created_at": "2024-01-01T00:00:00",
    "updated_at": "2024-01-01T00:00:00"
  }
]
```

- `deal_stage` — lifecycle enum: `prospect | diligence | portfolio | passed | exited` (default `diligence`). Distinct from `status` (`active | archived`) which controls archival visibility.
- `metadata` — parsed Tier 1-3 extract_info payload (see ARCHITECTURE.md → "Entity-level metadata"). Includes `_positions[]` and founder `status` when populated by the edit modal.
- `last_content_at` — always `null` on list responses. Populated only by the detail endpoint.

#### POST /entities
Create a new entity directly.

**Request Body:**
```json
{
  "name": "Company Name",
  "website": "https://example.com",
  "deal_stage": "diligence"
}
```

`deal_stage` is optional; defaults to `diligence`.

#### GET /entities/{id}
Get entity details. Response includes `last_content_at` — MAX(`workspace_nodes.created_at`) filtered on `origin_type IN ('upload', 'ingest', 'user')` and excluding soft-deleted rows. Captures "last genuine new content" (skips agent writes and overwrites). `null` if the entity has no user-origin content yet.

#### PATCH /entities/{id}
Update entity. All fields optional; `metadata_json` is the raw JSON string written to the `entities.metadata_json` column (the edit modal serializes its merged payload here).

**Request Body:**
```json
{
  "name": "New Name",
  "website": "https://new-website.com",
  "status": "archived",
  "deal_stage": "portfolio",
  "metadata_json": "{\"...\": \"...\"}"
}
```

Detail + PATCH responses recompute `last_content_at` from the workspace.

#### DELETE /entities/{id}
Delete entity and all associated workspace nodes.

---

### Fact discrepancies

Agent-surfaced contradictions between canonical `Entity.metadata_json` and the documents an opinion-producing preset (`legal_review`, `extract_info`) just read. The agent calls `propose_fact_update` during the run → server appends a row to `metadata._fact_discrepancies[]` with `status="pending"`. The user adjudicates via these endpoints. Accept applies the `proposed_value` to the canonical path; reject dismisses with an optional reason. See `docs/design/FACTS_VS_OPINIONS.md`.

#### GET /entities/{entity_id}/fact-discrepancies

Query param: `status` ∈ `pending | accepted | rejected | all` (default `pending`).

Response: `FactDiscrepancy[]`. Each row:

```json
{
  "id": "uuid",
  "detected_at": "2026-04-15T04:29:55Z",
  "detected_by": "legal_review",
  "source_run": { "agent_run_id": "…", "preset_id": "legal_review" },
  "field_path": "_positions[fund_id=taihill_venture_seed_iii_lp].invested_amount",
  "round_name": "Series Angel-1",
  "current_value": 500000,
  "proposed_value": 300000,
  "source_doc_node_id": "workspace_node_id",
  "source_doc_quote": "173,571 × US$1.7284 = US$300,000",
  "confidence": "high",
  "rationale": "SPA Schedule II lists Taihill with 173,571 Angel-1 shares …",
  "status": "pending",
  "resolved_at": null,
  "resolved_by": null,
  "dismiss_reason": null
}
```

`field_path` grammar: dotted. Array selectors `[fund_id=X]` / `[round_name=X]` / `[name=X]`, or shorthand `[X]` when the array has a default selector key (`_positions → fund_id`, `prior_rounds → round_name`, `founders → name`).

#### POST /entities/{entity_id}/fact-discrepancies/{discrepancy_id}/accept

Applies the discrepancy's `proposed_value` to `field_path` in `metadata_json`, flips status to `accepted`, stamps `resolved_at` + `resolved_by: "user"`. Returns the updated `EntityResponse`.

**Cascade on selector-key accept**: when the accepted leaf IS the selector key of its array row (e.g. `_positions[fund_id=X].fund_id` changing X → Y), pending sibling discrepancies that pointed at `_positions[fund_id=X]` get rewritten to `_positions[fund_id=Y]` so their next accept targets the same (renamed) row instead of creating a new stub. Handles both `[key=value]` and shorthand `[value]` forms.

Idempotent on already-accepted rows. `400` when the discrepancy is already rejected (undo is out of scope). `404` on unknown id.

#### POST /entities/{entity_id}/fact-discrepancies/{discrepancy_id}/reject

Body:
```json
{ "reason": "optional short string" }
```

Flips status to `rejected`, stamps `dismiss_reason`, leaves canonical facts untouched. Returns updated `EntityResponse`. Idempotent on already-rejected rows. `400` when the discrepancy is already accepted.

---

### Portfolio settings

File-backed configuration under `data/config/`. Validated via Pydantic on read and write; atomic `tmp → os.replace` on write.

#### GET /settings/funds
Return the Taihill fund registry used by the entity edit modal.

**Response:**
```json
{
  "funds": [
    { "id": "taihill_v3_lp", "name": "Taihill Venture Series III LP" },
    { "id": "newlight_i_lp", "name": "Newlight Fund I LP" }
  ]
}
```

Fund ids must match `^[a-z0-9_]+$` and are unique. Returns `{"funds": []}` when the config file does not exist.

#### POST /settings/funds
Upsert a fund by id. Preserves list order; appends when id is new.

**Request Body:**
```json
{ "id": "taihill_v3_lp", "name": "Taihill Venture Series III LP" }
```

**Response:** the full updated `FundsConfig` (list after upsert).

Validation errors (non-snake_case id, empty name, etc.) return `400 Bad Request`.

#### DELETE /settings/funds/{fund_id}
Remove a fund. No-op when id is not present.

**Response:** the full updated `FundsConfig`.

> Note: deleting a fund does not touch `_positions` already recorded on entities. Orphaned `fund_id` references render in the UI as the raw id until cleaned up manually.

#### GET /settings/legal-templates
Return the Tier R1 legal-template catalog used by the `legal_review` preset.

**Response:**
```json
{
  "version": 1,
  "templates": [
    {
      "id": "nvca_term_sheet_2020",
      "label": "NVCA Model Term Sheet (2020)",
      "category": "priced_round",
      "round_type": "series_a_plus",
      "instrument_types": ["priced_round"],
      "description": "NVCA industry-standard term sheet summarising economic + governance terms for a Series A (or later) priced equity round.",
      "source_file": "nvca/term_sheet_2020.docx",
      "text_file": "nvca/term_sheet_2020.txt"
    }
  ]
}
```

Returns `{"version": 1, "templates": []}` when the config file does not exist. Seeded on startup via `ensure_legal_templates_seed()` with 14 templates (YC post-money SAFE variants + pro-rata side letter + NVCA priced-round suite).

#### GET /settings/legal-templates/{template_id}/text
Return the extracted text for one template — the same content the agent sees when it calls the `legal_template_read` tool.

**Response:**
```json
{
  "id": "nvca_term_sheet_2020",
  "label": "NVCA Model Term Sheet (2020)",
  "text": "NVCA MODEL TERM SHEET\n\nSeries A Preferred Stock Financing of [Company]...\n"
}
```

`404 Not Found` when the id is unknown; `404` when the catalog references a missing text file (out-of-sync catalog vs `backend/app/legal_templates/`).

#### GET /settings/legal-review-checklist
Return the Tier R2 distilled review checklist. The payload is injected in full into the `legal_review` preset prompt.

**Response (abbreviated):**
```json
{
  "version": 1,
  "updated_at": null,
  "categories": [
    {
      "id": "economic_terms",
      "label": "Economic terms",
      "description": "Financial rights attaching to the preferred stock — liquidation, anti-dilution, dividends, pay-to-play.",
      "items": [
        {
          "id": "liquidation_preference_multiple",
          "label": "Liquidation preference multiple",
          "applies_to_instruments": ["priced_round"],
          "standard_value": "1x",
          "red_flag_patterns": [
            { "pattern": "multiple > 1x", "severity": "high",     "note": "…" },
            { "pattern": "multiple > 2x", "severity": "critical", "note": "…" }
          ],
          "why_matters": "Most impactful term in downside / moderate-outcome scenarios.",
          "scenario_focus": {
            "new_investment": "Primary negotiation item — push back on anything above 1x",
            "follow_on":     "Check if this round's pref stacks senior to ours",
            "retrospective": "Review cap-table stack; confirm our seniority"
          }
        }
      ]
    }
  ]
}
```

Seeded on startup via `ensure_legal_review_checklist_seed()` with 6 categories / 27 items.

#### PUT /settings/legal-review-checklist
Replace the checklist. The body must be the full config object (same shape as GET). Validated via Pydantic with `extra="forbid"` — unknown keys are rejected.

**Response:** the updated checklist (same shape as GET).

| Status | When |
|---|---|
| `200 OK` | Saved and re-validated |
| `400 Bad Request` | Pydantic `ValidationError` — `detail` is the `e.errors()` array (field locations + reasons). Also fires for id-format errors (`detail` is a string). |

> The Settings UI editor surfaces the error inline in the JSON editor so the user can fix and retry without losing their draft.

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

##### POST /entities/{entity_id}/workspace/upload-init
Issue an upload plan for a new file. On prod (Cloud Run + GCS) returns a pre-signed PUT URL the client uploads directly to `storage.googleapis.com`, bypassing Cloud Run's 32 MB request-body ceiling. On local dev (LocalFilesystemAdapter) returns `use_direct_upload=true` so the client falls back to `POST /workspace/file`.

**Request:**
```json
{
  "path": "Inbox/deck.pdf",
  "size": 91956224,
  "mime_type": "application/pdf",
  "metadata": { "description": "…" }
}
```

**Response:**
```json
{
  "upload_id": "uuid",
  "storage_key": "{entity_id}/workspace/blobs/{upload_id}/deck.pdf",
  "method": "PUT",
  "upload_url": "https://storage.googleapis.com/.../deck.pdf?X-Goog-Signature=…",
  "upload_headers": { "Content-Type": "application/pdf" },
  "max_bytes": 1073741824,
  "ttl_seconds": 1800,
  "use_direct_upload": false
}
```

**Errors:**
- `413` — `size` exceeds `WORKSPACE_MAX_FILE_BYTES`
- Pre-existing file at `path` — response has `use_direct_upload=true` (overwrite flows through `POST /workspace/file` to preserve version snapshots)

##### POST /entities/{entity_id}/workspace/upload-commit
Register a blob the client has already PUT to the signed URL. Reads the file's size + sha256 from storage, then calls `WorkspaceService.register_uploaded_blob` to create the `WorkspaceNode` + audit log entry.

**Request:**
```json
{
  "upload_id": "uuid-from-init",
  "path": "Inbox/deck.pdf",
  "mime_type": "application/pdf",
  "metadata": null
}
```

**Response:** `WorkspaceNodeResponse` for the registered file.

**Errors:**
- `404` — no blob found at the canonical storage_key (client never completed the PUT, or signed URL expired)
- `409` — path already has a node (overwrite via signed URL is not supported)

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

- **`agent_mode: "react"`** (Agent mode, or legacy `use_deep_agent: true`): ReAct agent (`langchain.agents.create_agent` in `agent_harness.py`) with 13 workspace tools + SummarizationMiddleware. The HTTP handler **persists the user message**, enqueues a **`chat_completion_jobs`** row, returns **`202 Accepted`**, and runs the agent in a **background task** so the client can keep using the API and **poll** job status for step text. No file count limit — agent reads files on demand.
- **`agent_mode: "deep_agent"`**: Legacy Deep Agent (`deepagents.create_deep_agent` in `deep_agent_compat.py`). Same async job pattern. Removable — falls back to ReAct if `deep_agent_compat.py` is deleted.
- **`agent_mode: "one_shot"`** (Chat mode, or legacy `use_deep_agent: false`): one-shot model call. Returns **`200 OK`** with the assistant message in the body (no job). Files inlined into the prompt; capped at `MAX_ATTACHMENTS = 10` files, `MAX_TEXT_CHARS = 200,000` per file. Frontend enforces the limit (blocks selection beyond 10, trims on mode switch).

The SPA persists a **Chat / Agent** segmented toggle in `localStorage`; that overrides the server default when set. **Presets** (`POST .../presets/.../run`) also accept `agent_mode` and `model_profile_id`, so shortcut runs can follow the same mode/profile selection.

**Context selection:** Selected `node_ids` inline excerpts from workspace files into the user turn and help edit resolution. They are **optional** in Agent mode: tools can **list/read** all entity workspace nodes without prior selection. In Chat mode, frontend enforces a 10-file selection limit with toast notifications.

### Environment (summary)

| Variable | Purpose |
|----------|---------|
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Legacy chat + presets |
| `GEMINI_MODEL` | Main chat model id (default `gemini-3.1-pro-preview`) |
| `GEMINI_METADATA_EXTRACTION_MODEL` | Presets such as `extract_info` and **node metadata pre-process** (file lookup JSON); default `gemini-3.1-flash-lite-preview` |
| `CHAT_USE_DEEP_AGENT` | Server default for deep agent when request omits `use_deep_agent` |
| `CHAT_DEFAULT_MODEL_PROFILE` | Default profile id if client omits `model_profile_id` |
| `CHAT_AGENT_RECURSION_LIMIT` | LangGraph recursion limit (default 100) |
| `WORKSPACE_MAX_FILE_BYTES` | Max upload size per file (default 50 MB) |
| `WORKSPACE_VERSION_RETENTION_DAYS` | How long old file versions are kept (default 30 days) |
| `MOONSHOT_API_KEY`, `KIMI_CODE_API_KEY`, URLs, `KIMI_CODE_MODEL`, etc. | Moonshot Open Platform vs Kimi Code routing — see `backend/app/config.py`, `backend/.env_sample`, `model_profiles.py` |

Other: `CHAT_ENABLE_GOOGLE_SEARCH`, attachment/history limits. Deep-agent steps are pushed to `chat_completion_jobs.step_detail` for UI polling.

**Agent-mode file handling:** In Agent mode, user-selected files are passed as a **pointer list** (path, type, size, description) — no file content is pre-injected. The agent reads files on demand via `workspace_read_file`. For PDFs, Gemini receives compressed native binary (multimodal tool response). Office formats (docx/pptx/xlsx + legacy doc/ppt/xls via LibreOffice) are extracted to text. No file count limit.

**Chat-mode file handling:** In Chat (one-shot) mode, selected files are inlined into a single Gemini call. PDFs are compressed via ghostscript; images sent as native binary. Capped at 10 files / 200k chars per file (backend truncates silently; frontend prevents over-selection).

**Agent tools (14 total):** 13 workspace tools — `workspace_get_tree`, `workspace_list_files`, `workspace_read_file`, `workspace_search_files`, `workspace_create_folder`, `workspace_move`, `workspace_rename`, `workspace_write_file`, `workspace_annotate`, `workspace_delete`, `workspace_file_versions`, `workspace_restore_version`, `workspace_history` — plus 1 reference tool `legal_template_read(template_id)` that fetches raw text of a YC SAFE / NVCA template from the catalog (see `GET /settings/legal-templates`). Always loaded together via `agent_harness._build_agent_core`.

### `GET /entities/{entity_id}/chat/presets`

List shortcut presets. Each preset has an `id` (for example `red_team`, `extract_info`, `legal_review`, `initial_screening`, `initial_screening_v2`), display fields, and output hints. **Preset run** behavior is defined in `backend/app/services/preset_registry.py`:
- `red_team` — markdown report at `Deliverables/Reports/risk_analyze.md`. Honors the chat/agent toggle (can run one-shot or react).
- `extract_info` — agent-only (force-`react` server-side). Agent browses the workspace autonomously, extracts Tier 1-3 VC metadata (~26 fields), writes `Company Profile.json` at the workspace root, and post-processing syncs the JSON into `Entity.metadata_json`. Auto-updates `Entity.name` / `Entity.website` when extraction finds better values. On re-runs, the agent receives the previous extraction as incremental context and focuses on new/changed files. Workspace versioning snapshots prior versions automatically. Post-processing also routes hard-fact fields through `fact_manager.record_fact_in_metadata` so each field gains a `_ledger[]` entry with `source={type:"upload", preset:"extract_info"}, confidence:0.85`.
- `legal_review` — agent-only (force-`react` server-side). Reviews **user-selected** legal documents for a single funding round against a two-tier reference system: Tier R1 raw templates (YC SAFE + NVCA — fetched on demand via `legal_template_read`) and Tier R2 distilled checklist (injected into the prompt). Auto-detects scenario per round (`new_investment` / `follow_on` / `retrospective`) from `metadata._positions[]` + prior `legal_reviews[]`. Writes `Legal Review.json` at the workspace root; post-processing validates, rebuilds `documents_reviewed[]` + `checklist_version` from trusted server sources, merges by `round_name` into `Entity.metadata_json.legal_reviews[]`, and re-persists the authoritative copy. Same belt-and-suspenders pattern as extract_info: salvage from agent text reply + plain-text failure message when the deliverable is missing/unreadable. Ledger routing runs on the term-block leaves with `source={type:"legal_doc"|"cap_table", preset:"legal_review"}, confidence:0.95`.
- `initial_screening` — agent-only (force-`react`). Three-stage pipeline matching Taihill's internal **Monday Screening Template** (reference samples at `reference-project/Initial Screening & DD Samples/`): (1) ReAct research agent with workspace tools + `web_search` + `propose_fact_update` writes **five** section JSONs (`team.json`, `market.json`, `product_tech.json`, `business_model.json`, `funding_traction.json`) under `Deliverables/Analysis/initial_screening/`. Each section JSON has `facts[]` / `claims[]` / `open_gaps[]` plus structured `extras{}` that the composer renders as template sub-parts (e.g. `product_tech.extras = {technology, advantages_and_moats, product_commercial_value, product_milestones}`, `team.extras.profile_type = "business" | "academic"` to select the layout). (2) One-shot composer reads the 5 JSONs + entity `referral_source` (for `[6] Source`) and writes `Deliverables/Memos/initial_screening.md` in Taihill's Monday format (`Intro`, `[1] Team`, `[2] Market & Industry Pain Point`, `[3] Product/Tech`, `[4] Business Model`, `[5] Funding & Traction`, `[6] Source`, optional `Follow-up questions`). (3) One-shot reviewer verifies the draft against sources and writes a CLEAN revised memo + audit-trail `initial_screening_review_notes.md` (corrections applied silently — no strikethroughs in the memo; before/after table in the review notes). Phase-1 recursion limit is overridden to 120 (the `.env` default 50 is too tight for five domains).
- `initial_screening_v2` — agent-only (force-`react`). Same three-stage output shape as `initial_screening`, but Phase 1 is split into a **survey agent** (≤25 recursion, identifies primary source docs, no web search) + **five parallel section agents** via `asyncio.gather` (≤45 recursion each, restricted toolkit — no tree browsing). Writes to `Deliverables/Analysis/initial_screening_v2/` + `initial_screening_v2.md` so v1 / v2 outputs coexist for comparison. Compose + review reuse v1's code paths (parameterised on `analysis_dir` / `memo_path` / `review_notes_path`). Reliability patterns: **pre-delete target files** before dispatch (honest freshness — no stale-file false positives), **dual delivery path** (agent may call `workspace_write_file` OR emit JSON as final reply text — orchestrator accepts either), **invoke-error-tolerant verification** (recursion-after-write still counts as delivered). Failure-isolated: one section failing still ships 4-5/5 + memo that degrades via cross-reference.

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

One-shot: files inlined (Gemini native binary / Kimi text). Agent mode: pointer list only — agent reads files on demand via tools. Search behavior follows the active profile when enabled.

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
- `session_id` is **required** for any agent-mode preset run (the job + assistant card message attach to a session).
- `extract_info` and `legal_review` are both force-pinned to `agent_mode="react"` server-side regardless of the client toggle — each must browse the workspace and write a JSON deliverable via workspace tools.
- `red_team` honors the client toggle exactly — it does NOT auto-promote to agent mode.
- `extract_info` post-processing trusts the workspace file, not the agent's text reply: `_extracted_at` is overwritten with the real server timestamp, and `_files_examined` is rebuilt from the status_trace (files the `workspace_read_file` tool actually touched). If the agent skips `workspace_write_file`, post-processing attempts `parse_json_loose` on the agent's final text as a salvage path and writes the file itself; if that also fails, the chat card falls back to a plain-text failure message.
- `legal_review` post-processing mirrors extract_info: `review_date` + `checklist_version` are stamped server-side, `documents_reviewed[]` is rebuilt from status_trace (template reads routed to `reference_templates_consulted` instead — separate tool emits a different notification prefix), each review is merged by `round_name` into `Entity.metadata_json.legal_reviews[]`, and the `Legal Review.json` file is re-persisted at workspace root with the merged array so file + DB stay in sync. Salvage path via `parse_json_loose` covers the agent-skipped-write case.

---

## Data Models

### Entity
| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| type | string | "company" (MVP only) |
| name | string | Entity name (required); auto-updated by `extract_info` when extraction finds a better value |
| website | string | Optional website URL; auto-updated by `extract_info` when extraction finds a better value |
| status | string | "active" or "archived" |
| metadata | object \| null | Parsed from `metadata_json` TEXT column. Populated by the `extract_info` preset with Tier 1-3 VC schema (company_name, legal_name, one_liner, description, industry_tags, business_model, hq_location, website, founded_date, incorporation_jurisdiction, incorporation_entity_type, founders[], team_size, key_team[], investment_stage, raise_amount, raise_currency, raise_instrument, valuation_cap, pre_money_valuation, prior_rounds[], existing_investors[], referral_source, priority_indicators[], red_flags[], competitors[], plus meta: `_extracted_at`, `_extraction_version`, `_files_examined[]`). Also carries `_positions[]` (user-edited via `EntityEditModal`) and `legal_reviews[]` (populated by the `legal_review` preset, one entry per round — see the preset write-up above). `Company Profile.json` and `Legal Review.json` in the workspace root mirror the extract_info / legal_review slices respectively; workspace versioning keeps extraction / review history. |
| created_at | datetime | Creation timestamp |
| updated_at | datetime | Last update timestamp |

`PATCH /entities/{id}` accepts raw `metadata_json` as a JSON string if programmatic updates are needed; typical flow is to let `extract_info` manage it.

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
Create custom weight preset. `{ "name": "My Preset", "weights": { "academic_excellence": 0.3, ... } }` — keys must exist in the current dimension list (see `GET /academic/custom-dimensions`).

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

All evaluation dimensions — the four MECE defaults (`academic_excellence`, `tech_transfer_experience`, `founder_potential`, `growth_trajectory`) and any user-added ones — live in a single file-backed config at `data/config/dimensions.json` and are treated uniformly by the CRUD endpoints below. On first read the runtime file is auto-seeded from the tracked `backend/app/services/academic/dimensions_seed.json` (canonical prompts, committed to git, shipped with the backend image). The `dim_runner.py` evaluation loop reads each dim's prompt and passes it directly to the scoring LLM, so adding, editing, or deleting a dimension takes effect on the next tick with no code changes.

> **Route name note**: the route path is still `/academic/custom-dimensions` for backwards compatibility, but the endpoint now manages the **full** dimension list, not just user-added ones. All dimensions are fully editable and deletable.

#### GET /academic/custom-dimensions
List all evaluation dimensions (defaults + user-added). Returns `[{ name, key, prompt }, ...]`.

#### POST /academic/custom-dimensions
Create a new dimension. Body: `{ "name": "Patent Quality", "key": "patent_quality", "prompt": "Assess patent filing activity, prosecution quality, and commercial licensing." }`. Returns `409` if `key` already exists.

#### PUT /academic/custom-dimensions/{key}
Update an existing dimension (including rename). Body matches POST. If `body.key != {key}` and the new key already exists, returns `409`.

#### DELETE /academic/custom-dimensions/{key}
Delete a dimension. Returns `404` if the key doesn't exist.

> **Operator warning**: ranking-preset weights in `backend/app/routers/academic.py` hardcode the default dimension keys. Deleting a default (e.g. `academic_excellence`) won't crash the API but will leave the built-in ranking presets referencing a missing key. Only delete defaults if you're aware of this.

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
| dimensions | object | Map of `{dimension_key: { score, explanation, evidence }}` for every dimension defined in `data/config/dimensions.json`. Default keys (the four MECE dims): `academic_excellence`, `tech_transfer_experience`, `founder_potential`, `growth_trajectory`. Users can add/remove/rename dimensions via the `/academic/custom-dimensions` endpoints, and the scoring loop picks up the new list on the next heartbeat tick. |
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
