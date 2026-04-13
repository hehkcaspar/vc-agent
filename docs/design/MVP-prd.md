> **Status:** Implemented. The unified Workspace model replaced the original Resources/Artifacts dual model — see `ENTITY_WORKSPACE_DESIGN.md`. Parking-lot ingestion, resolver isolation, and StorageAdapter abstraction remain as designed.

## MVP PRD — VC Portfolio Manager (Entity-Canonical, Parking-Lot Ingestion)

### 0. Goal

Build a minimal admin-style webapp for a US-based VC firm to manage portfolio companies as **Entities**, with two kinds of content per Entity:

* **Resources**: user-provided (PDF, images, text/markdown, URLs)
* **Artifacts**: system-generated outputs (MVP: markdown files)

**Core MVP value:** upload/store/browse materials reliably; nothing gets lost; future ingestion + smarter matching can be added without refactoring.

---

## 1. Scope (MVP) and Non-goals

### In scope

1. **Admin panel UI** with left sidebar modules (tabs). MVP ships with:

   * **Portfolio** tab (only functional module)
2. **Tab state persistence**

   * Switching tabs must not lose the leaving tab’s state (view mode, selection, scroll, draft inputs).
3. **Portfolio**

   * Entity list/grid (toggle)
   * Create Entity via modal (name + files/text/urls)
   * Entity detail view with **two separate zones**: Resources zone + Artifacts zone
4. **Ingestion pipeline**

   * All incoming materials go to a **Parking Lot** first (durable)
   * Then pass through **EntityResolver** (abstract backend function)
   * Canonical resources are **materialized** under a real Entity folder
5. **Local filesystem storage**

   * Resources stored as raw files under entity folders
   * Artifacts stored as independent markdown files under entity folders

### Non-goals (explicit)

* Search, tagging, advanced filtering
* Collaboration (comments, mentions, approvals)
* Automated parsing/embedding/vector DB
* Email/IM integrations (but APIs are shaped for future sources)
* Complex role-based permissions (keep simple for MVP)

---

## 2. Definitions & Invariants

### Entity

Canonical object representing a portfolio company/project (MVP: company only).

### Resource (canonical)

User-provided item that is **already attached to a real Entity**.

* Canonical Resources **must always have a non-parking entity_id**.

### Artifact (canonical)

System-generated content for an Entity (MVP: markdown files, versioned).

### Parking Lot item (IngestItem)

Durable staging record for inbound content before entity resolution/materialization.

### Invariants

1. **No loss:** every inbound submission is persisted to Parking Lot immediately.
2. **Downstream simplicity:** all normal portfolio/resource APIs operate only on **canonical** records (never missing entity_id).
3. **Resolver isolation:** all entity-matching complexity lives behind `EntityResolver`; other modules never implement matching logic.
4. **Storage abstraction:** business logic uses a `StorageAdapter` so local FS can be swapped later.

---

## 3. Storage (MVP: Local + Metadata DB)

### Filesystem root

Configurable `DATA_ROOT` (local disk).

### Folder layout (required)

```
/data/entities/
  /00000/                         # parking lot pseudo-entity
    /parkinglot/{ingest_id}/
      /files/                     # raw uploaded files (pdf/img/text files)
      /payload/
        text.md                   # optional pasted text
        urls.json                 # optional urls
        meta.json                 # source + hints + timestamps

  /{entity_id}/
    /resources/{resource_id}/     # raw canonical resources (files or notes)
      ...
    /artifacts/{artifact_id}/
      v1.md
      v2.md
```

### Metadata store

Use **SQLite** for MVP (swap to Postgres later). Required tables:

* `entities`
* `resources` (canonical only; entity_id != "00000")
* `artifacts`
* `ingest_items` (parking lot)

---

## 4. Data Model (Minimum)

### Entity

* `id` (UUID)
* `type` (`company`)
* `name` (required)
* `created_at`, `updated_at`
* optional: `website`, `status(active|archived)`

### IngestItem (Parking Lot record)

* `ingest_id` (UUID)
* `source` (`frontend` now; future `email|im|api`)
* `status` (`parked|resolution_required|failed|materialized`)
* `parkinglot_path` (relative)
* optional hints: `entity_hint_name`, `entity_hint_domain`
* `created_at`, `updated_at`
* optional: `error`

### Resource (Canonical)

* `id` (UUID)
* `entity_id` (must be real)
* `resource_type` (`file|text|url`)
* `title`
* file fields: `mime_type`, `original_filename`, `relative_path`
* text fields: `relative_path` (store as file for uniformity) OR `content` (pick one; recommend file)
* url fields: `url`
* `origin_ingest_id` (traceability)
* `created_at`, `updated_at`

### Artifact (Canonical, Markdown)

* `id` (UUID)
* `entity_id`
* `artifact_type` (`memo|factsheet|report|other`)
* `version` (int)
* `status` (`draft|final`)
* `relative_path` (markdown file path)
* `created_at`, `updated_at`

---

## 5. Backend Architecture (Decoupled) (Python)

### Required components (interfaces)

1. **ParkingLotManager**

   * Persists inbound content → creates `IngestItem` + writes files/payload under `00000`.
2. **EntityResolver (abstract)**

   * Input: `ingest_id` + extracted hints
   * Output:

     * `resolved_entity_id`, OR
     * `resolution_required` (+ candidates), OR
     * `failed`
   * MVP behavior: only simple exact match; else ask frontend.
3. **ResourceMaterializer**

   * Converts an `IngestItem` into canonical Resources under a real Entity:

     * creates entity if needed
     * moves/copies files from `00000` → `{entity_id}`
     * writes `resources` rows
     * marks ingest item `materialized`
4. **StorageAdapter**

   * MVP: LocalFilesystemAdapter
   * Future: S3/GCS adapter without changes to business logic

### Materialization safety rule (MVP)

Prefer **copy → verify → write DB → delete parking** (safer than move-first).

---

## 6. Entity Resolution (MVP Rules)

### Resolution logic (MVP minimal)

If request contains `entity_id`:

* validate entity exists → materialize into that entity

If missing `entity_id`:

* simple matching attempt (case-insensitive exact name; optional domain match)
* if single confident match → materialize automatically
* else → return `resolution_required` and let UI decide (select existing or create new)

If resolver fails:

* keep in Parking Lot with `failed` status; user can manually resolve later.

---

## 7. Frontend UX (MVP) (React)

### Global layout

* Left sidebar with tabs (Portfolio active). Switching tabs **preserves state**.

### Portfolio tab

#### Entity list view

* Toggle **List / Grid**
* Default sort: `updated_at desc`
* Create button

#### Create Entity modal (from Portfolio)

Fields:

* Entity name (required)
* Files (0..N): PDF, images, text/markdown files
* Free text (0..1)
* URLs (0..N)

Submit behavior:

* Calls ingestion endpoint; typically resolved immediately since name is present.
* On success, navigate to Entity detail view.

#### Entity detail view (required separation)

Two zones:

1. **Resources** (list, recent first)
2. **Artifacts** (list, recent first)

Resource actions:

* View (PDF/image/text/MD)
* URL opens in new tab

Artifact actions:

* View markdown (rendered)
* (Optional MVP) “Create artifact stub” hidden or admin-only; artifacts can also be created internally via API.

#### Parking Lot view (minimal but required)

In Portfolio tab, a small entry:

* **Parking Lot (N)** showing items with `parked|resolution_required|failed`

Each item shows:

* timestamp, source, filename/summary
  Actions:
* Attach to existing entity (select)
* Create new entity + attach (name input)

---

## 8. API Contract (Minimal) (FastAPI)

### Ingestion (single front door)

`POST /ingest/resources`

* multipart + JSON payload
* accepts:

  * files (0..N)
  * text (optional)
  * urls (optional)
  * optional `entity_id`
  * optional hints: `entity_hint_name`, `entity_hint_domain`

Returns one of:

* `{ status: "resolved", entity_id, resources: [...] }`
* `{ status: "resolution_required", ingest_id, candidates: [...] }`
* `{ status: "failed", ingest_id, error }`

### Parking Lot management

* `GET /parkinglot?status=...`
* `GET /parkinglot/{ingest_id}`
* `POST /parkinglot/{ingest_id}/resolve`

  * `{ entity_id }` OR `{ create_entity: { name } }`

### Portfolio browsing

* `GET /entities`
* `POST /entities` (optional; can be implicit via create flow)
* `GET /entities/{id}`
* `GET /entities/{id}/resources`
* `GET /entities/{id}/artifacts`

---

## 9. Acceptance Criteria (MVP “Done”)

1. **Tab state preserved:** switching away and back restores view mode + selection + scroll.
2. **No upload loss:** every submission creates a Parking Lot folder + ingest record immediately.
3. **Canonical resources only:** entity resource lists never include parking lot items.
4. **Resolution handshake works:** unresolved items prompt user choice; resolution materializes into the selected/created entity.
5. **Filesystem correctness:** resources/artifacts are stored under the specified folder structure; metadata matches what’s on disk.
6. **Entity detail separation:** Resources and Artifacts are distinct zones, both recency-sorted.
7. **Local-only MVP:** no external storage required; storage adapter boundary exists for later swap.

---

## 10. Future-proofing (explicit extension points)

* New ingestion sources (email/IM) only need to call `/ingest/resources`.
* Smarter matching only changes `EntityResolver`.
* Cloud storage only changes `StorageAdapter`.
* Artifact generation engine only writes markdown + metadata through `ArtifactStore`/adapter; no changes to Portfolio browsing UI.

---