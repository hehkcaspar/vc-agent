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
Delete entity and all associated resources/artifacts.

#### GET /entities/{id}/resources
Get all resources for an entity (sorted by created_at desc).

**Response:**
```json
[
  {
    "id": "uuid",
    "entity_id": "uuid",
    "resource_type": "file|text|url",
    "title": "Resource Title",
    "mime_type": "application/pdf",
    "original_filename": "document.pdf",
    "relative_path": "{entity_id}/resources/{resource_id}/document.pdf",
    "url": null,
    "origin_ingest_id": "uuid",
    "created_at": "2024-01-01T00:00:00",
    "updated_at": "2024-01-01T00:00:00"
  }
]
```

#### GET /entities/{id}/artifacts
Get all artifacts for an entity (sorted by created_at desc).

**Response:**
```json
[
  {
    "id": "uuid",
    "entity_id": "uuid",
    "artifact_type": "memo|factsheet|report|other",
    "title": "extract_info",
    "version": 1,
    "status": "draft|final",
    "relative_path": "{entity_id}/artifacts/{artifact_id}/v1.md",
    "created_at": "2024-01-01T00:00:00",
    "updated_at": "2024-01-01T00:00:00"
  }
]
```

`title` is optional and may be `null`. JSON artifacts (for example from the `extract_info` preset) use a `.json` file suffix in `relative_path`; markdown reports use `.md`.

#### GET /entities/{id}/artifacts/{artifact_id}/view

Return the artifact body as UTF-8 text for display in the UI.

**Response:**
```json
{
  "id": "uuid",
  "type": "report",
  "version": 1,
  "status": "draft",
  "content": "…markdown or JSON string…",
  "created_at": "2024-01-01T00:00:00"
}
```

#### PUT /entities/{id}/artifacts/{artifact_id}/content

Replace the artifact file on disk with **pretty-printed JSON** derived from the request body. Accepts any JSON-serializable value (`object`, `array`, string, number, boolean, or `null`). Used by the entity UI when saving structured artifacts from the Form or Raw JSON editor.

**Request body:** arbitrary JSON (for example a nested `object`).

**Response:** `ArtifactResponse` for the updated row (same shape as list items, including `title` and `relative_path`).

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

## Entity chat (Gemini one-shot + optional Deep Agent harness)

All routes are scoped to an existing entity.

### Which path runs for `POST .../messages`?

The server computes an **effective** deep-agent flag per request:

```text
use_deep_agent = body.use_deep_agent if body.use_deep_agent is not None else CHAT_USE_DEEP_AGENT
```

- **`use_deep_agent` true:** LangChain **Deep Agents** (`create_deep_agent`) with entity-scoped tools (`portfolio_*` in `portfolio_deep_agent.py`). The HTTP handler **persists the user message**, enqueues a **`chat_completion_jobs`** row, returns **`202 Accepted`**, and runs the agent in a **background task** so the client can keep using the API (e.g. read artifacts) and **poll** job status for step text.
- **`use_deep_agent` false:** **Legacy** one-shot call via **google-genai** (`generate_with_context`). Returns **`200 OK`** with the assistant message in the body (no job).

The SPA persists an **Agent** on/off toggle (`use_deep_agent` on each send) in `localStorage`; that overrides the server default when set. **Presets** (`POST .../presets/.../run`) always use the legacy Gemini preset pipeline, not the harness.

**Context selection:** Selected `resource_ids` / `artifact_ids` inline excerpts into the user turn and help edit resolution. They are **optional** in Agent mode: tools can **list/read** all entity artifacts and resources without prior selection.

### Environment (summary)

| Variable | Purpose |
|----------|---------|
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Legacy chat + presets |
| `GEMINI_MODEL` | Main chat model id (default `gemini-3.1-pro-preview`) |
| `GEMINI_METADATA_EXTRACTION_MODEL` | Presets such as `extract_info` (default `gemini-3.1-flash-lite-preview`) |
| `CHAT_USE_DEEP_AGENT` | Server default for deep agent when request omits `use_deep_agent` |
| `CHAT_DEFAULT_MODEL_PROFILE` | Default profile id if client omits `model_profile_id` |
| `CHAT_AGENT_RECURSION_LIMIT` | LangGraph recursion limit (default 50) |
| `CHAT_ARTIFACT_*` | Edit policy (overwrite gate, default mode, resolver threshold) |
| `MOONSHOT_API_KEY`, `KIMI_CODE_API_KEY`, URLs, `KIMI_CODE_MODEL`, etc. | Moonshot Open Platform vs Kimi Code routing — see `backend/app/config.py`, `backend/.env_sample`, `model_profiles.py` |

Other: `CHAT_ENABLE_GOOGLE_SEARCH`, attachment/history limits. Deep-agent steps are pushed to `chat_completion_jobs.step_detail` for UI polling.

**Artifact edits (Option B):** resolve → validate → `portfolio_apply_artifact_edit` → audit rows in **`artifact_edit_events`**. Tools include resource list/read for entity materials (text/URL; some binaries rejected in-tool).

### `GET /entities/{entity_id}/chat/presets`

List shortcut presets. Each preset has an `id` (for example `red_team`, `extract_info`), display fields, and output hints. **Preset run** behavior is defined in `backend/app/services/preset_registry.py`: markdown-style outputs are stored as `.md` artifacts; `extract_info` produces a versioned **JSON** artifact (title `extract_info`) with structured company metadata.

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
  "resource_ids": ["uuid"],
  "artifact_ids": ["uuid"],
  "model_profile_id": "gemini_google",
  "use_deep_agent": true
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `text` | yes | User message |
| `resource_ids` | no | Canonical resources to emphasize this turn |
| `artifact_ids` | no | Canonical artifacts to emphasize / edit hints |
| `model_profile_id` | no | `gemini_google` \| `kimi_moonshot` (harness only) |
| `use_deep_agent` | no | If set, overrides server `CHAT_USE_DEEP_AGENT` for **this** message |

**Responses**

- **`202 Accepted`** — Deep agent path. Body (`ChatMessageJobAccepted`): `job_id`, `user_message` (already stored), `warnings`. Client should **poll** `GET .../jobs/{job_id}` until `status` is `succeeded` or `failed`; then load session messages or read `assistant_message` from the job payload.
- **`200 OK`** — Legacy path. Body (`ChatMessageResult`): `assistant_message`, `warnings` (no `run_id` / `tool_trace` unless you extend legacy).

Legacy: multimodal context where supported. Harness: text-inline preamble from selections; tools can fetch the rest. Search behavior follows the active profile when enabled.

### `GET /entities/{entity_id}/chat/sessions/{session_id}/jobs/{job_id}`

Poll deep-agent progress after a `202` from `POST .../messages`. Returns `ChatMessageJobStatus`: `status` (`pending` \| `running` \| `succeeded` \| `failed`), `step_detail` (human-readable step for UI), optional `assistant_message` when succeeded, `error_message`, `warnings`, `run_id`, `tool_trace`.

### `POST /entities/{entity_id}/chat/presets/{preset_id}/run`

Run a preset and **create a new canonical artifact** (markdown or JSON on disk + DB row, depending on preset). JSON body:

```json
{
  "resource_ids": [],
  "artifact_ids": [],
  "session_id": "optional — if set, appends assistant note to that session",
  "industry": "optional",
  "stage": "optional",
  "artifact_type": "optional override",
  "artifact_status": "optional override"
}
```

Response: `{ "artifact_id", "assistant_summary", "warnings" }` (exact fields may include artifact metadata for UI artifact cards).

When `session_id` is provided, the run can append a short assistant message to that session (for example an artifact card referencing the new artifact).

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

### Resource (Canonical)
| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| entity_id | UUID | Parent entity (required, not "00000") |
| resource_type | string | "file", "text", or "url" |
| title | string | Display title |
| mime_type | string | MIME type for files |
| original_filename | string | Original upload name |
| relative_path | string | Path relative to DATA_ROOT |
| url | string | URL for url-type resources |
| origin_ingest_id | UUID | Traceability to parking lot |
| created_at | datetime | Creation timestamp |
| updated_at | datetime | Last update timestamp |

### Artifact (Canonical)
| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| entity_id | UUID | Parent entity |
| artifact_type | string | "memo", "factsheet", "report", "other" |
| title | string \| null | Optional display key (for example `extract_info`); versioning may group by title |
| version | int | Version number |
| status | string | "draft" or "final" |
| relative_path | string | Path under `DATA_ROOT` to file (typically `v{n}.md` or `v{n}.json`) |
| created_at | datetime | Creation timestamp |
| updated_at | datetime | Last update timestamp |

### Chat completion job (Deep Agent only)

Persisted in **`chat_completion_jobs`** for async turns. Not exposed as a full CRUD resource; use the job GET above. Stores `status`, `step_detail`, FKs to `user_message_id` / `assistant_message_id`, serialized attachment ids, `harness_extras`, `warnings_json`, `tool_trace_json`, `agent_run_id` (correlates with `artifact_edit_events.run_id`).

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
