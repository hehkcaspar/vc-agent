# Entity Workspace — Hierarchical File System Design

> **Status:** Draft v4 — post first-principles review
> **Date:** 2026-04-04
> **Stack:** FastAPI + SQLAlchemy (async SQLite) + React 18 / TypeScript / SWR
> **Future:** S3-compatible object storage via `StorageAdapter` swap

---

## 1. Problem

The portfolio module has two parallel file systems that shouldn't both exist:

- **Resources**: flat `{entity_id}/resources/{resource_id}/{filename}` — no folders, no agent manipulation, UUID-addressed
- **Artifacts**: flat `{entity_id}/artifacts/{artifact_id}/v{version}.md` — versioned, typed, with fuzzy resolution + intent detection + audit trail

Real-world company research has natural structure (data rooms, legal docs by round, technical docs by topic). Neither system supports folders. And maintaining two parallel file abstractions means every future feature (permissions, search, sharing, quotas) gets built twice.

**Goals:**

1. **One file system per entity** — a hierarchical workspace tree that replaces both resources and artifacts.
2. **Folder upload** — drag-and-drop or zip, structure preserved.
3. **Agent-driven restructuring** — same primitives as an agentic IDE (tree, read, write, move, rename, search).
4. **File versioning** — built into the workspace so overwrites are always recoverable.
5. **Clean replacement** — no backward-compat shims, no dual toolkits.
6. **Cloud-portable** — local FS today, S3/GCS tomorrow with only a StorageAdapter swap.

---

## 2. Why Artifacts Merge In

The artifact system has ~700 lines of carefully built machinery: versioning, fuzzy resolution, intent detection, edit validation, audit trail. Before merging, each piece needs to justify its continued existence against the workspace design.

| Artifact Feature | Why It Exists Today | What Workspace Does Instead | Verdict |
|---|---|---|---|
| **Fuzzy resolution** (30+ regex patterns, multi-signal scoring) | Artifacts addressed by UUID; agent has only vague hints | Agent uses paths: `search_files("series-a")` or `read_file("Deliverables/series-a-memo.md")` | **Eliminated** — paths solve it |
| **Intent detection** (create vs edit, EN+ZH regex) | Agent can't tell if artifact exists without querying | `write_file("new-path")` = create, `write_file("existing-path")` = overwrite. Path existence IS the intent signal | **Eliminated** — inherent in paths |
| **Versioning** (v1.md, v2.md separate files) | Overwrites lose history | **Absorbed** — workspace file versioning (§4.3). Every overwrite snapshots the old content | **Absorbed as workspace feature** |
| **Edit validation** (JSON parse check) | Prevents broken JSON artifacts | Pre-write hook: `*.json` files validated before write | **Absorbed as write hook** |
| **Audit trail** (5-state ArtifactEditEvent) | Multi-step fallible edit pipeline needs forensics | `workspace_ops` log covers all mutations. The 5-state lifecycle collapses because there's no resolution/intent step | **Simplified** — op log is sufficient |
| **Typed deliverables** (memo, factsheet, report) | UX: distinguish agent outputs from raw data | Folder convention: `Deliverables/Memos/`, `Deliverables/Reports/`. Plus `metadata_json` on node for machine-readable type | **Convention, not schema** |
| **Lineage** ((entity, type, title) grouping) | "All memos about Series A" query | Folder structure + search. `ls Deliverables/Memos/` or `search_files("series-a")` | **Eliminated** — folders are lineage |

**Net result:** Artifact system = ~700 LOC. After merge: ~0 LOC of artifact-specific code. Versioning (~80 LOC) and validation (~20 LOC) move into WorkspaceService as general features.

---

## 3. Workspace Tree

Each entity gets a single workspace:

```
data/entities/{entity_id}/
└── workspace/
    ├── Inbox/                          ← landing zone for new ingests
    │   └── pitch-deck.pdf
    ├── Data Room/
    │   ├── Financials/
    │   │   ├── 2025-Q4.xlsx
    │   │   └── 2026-Q1.xlsx
    │   └── Cap Table.pdf
    ├── Technical/
    │   └── Architecture.md
    ├── Deliverables/                   ← replaces artifacts/
    │   ├── Memos/
    │   │   └── series-a-analysis.md
    │   └── Reports/
    │       └── due-diligence-v2.md
    └── .versions/                      ← hidden, auto-managed
        └── {node_id}/
            ├── v1_2026-04-01T10:30:00.md
            └── v2_2026-04-03T14:15:00.md
```

No more `resources/` or `artifacts/` directories. One tree. This is the **logical** structure (what users and agents see). Physical storage is a flat blob store keyed by node_id — see §10.1.

---

## 4. Data Model

### 4.1 `workspace_nodes` (replaces both `resources` and `artifacts`)

```python
class WorkspaceNode(Base):
    __tablename__ = "workspace_nodes"

    id            = Column(String, primary_key=True, default=generate_uuid)
    entity_id     = Column(String, ForeignKey("entities.id"), nullable=False, index=True)

    # Tree structure
    node_type     = Column(String, nullable=False)   # "file" | "folder" | "bookmark"
    name          = Column(String, nullable=False)    # display name
    path          = Column(String, nullable=False)    # materialized: "Data Room/Financials/2025-Q4.xlsx"
    parent_id     = Column(String, ForeignKey("workspace_nodes.id"), nullable=True)

    # File-specific (ignored for folders and bookmarks)
    mime_type     = Column(String, nullable=True)
    size_bytes    = Column(Integer, nullable=True)
    checksum      = Column(String, nullable=True)     # SHA-256 of current content
    storage_key   = Column(String, nullable=True)     # path-independent: "{entity_id}/workspace/blobs/{node_id}/{filename}"
    url           = Column(String, nullable=True)      # for bookmark nodes only

    # Versioning
    version       = Column(Integer, default=1)         # increments on content overwrite

    # Provenance
    origin_type   = Column(String, nullable=True)     # "upload" | "agent" | "ingest" | "shared"
    origin_ref    = Column(String, nullable=True)     # ingest_id, agent_run_id, etc.

    # Metadata (carries resource metadata_json + deliverable type/status)
    metadata_json = Column(Text, nullable=True)

    created_at    = Column(DateTime, default=utc_now)
    updated_at    = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at    = Column(DateTime, nullable=True)   # soft delete

    __table_args__ = (
        Index("uq_entity_path", "entity_id", "path", unique=True,
              sqlite_where=text("deleted_at IS NULL")),  # partial: allows re-create after soft delete
        Index("ix_workspace_parent", "entity_id", "parent_id"),
    )
```

**Deliverable metadata convention** (stored in `metadata_json`):

```json
{
  "deliverable_type": "memo",
  "status": "draft",
  "generated_by": "agent",
  "agent_run_id": "abc-123"
}
```

This replaces `Artifact.artifact_type` and `Artifact.status` without requiring schema columns.

### 4.2 `workspace_ops` (audit + undo, replaces `artifact_edit_events`)

```python
class WorkspaceOp(Base):
    __tablename__ = "workspace_ops"

    id            = Column(String, primary_key=True, default=generate_uuid)
    entity_id     = Column(String, ForeignKey("entities.id"), nullable=False, index=True)
    batch_id      = Column(String, nullable=True, index=True)  # group for atomic undo

    op_type       = Column(String, nullable=False)    # create_file | create_folder | overwrite |
                                                       # move | rename | copy | delete | restore |
                                                       # upload_tree | extract_zip
    actor_type    = Column(String, nullable=False)    # "user" | "agent" | "system"
    actor_ref     = Column(String, nullable=True)

    node_id       = Column(String, nullable=True)
    payload_json  = Column(Text, nullable=False)      # op-specific data
    inverse_json  = Column(Text, nullable=True)       # for undo

    # Versioning checkpoints (replaces ArtifactEditEvent checksums)
    before_checksum = Column(String, nullable=True)
    after_checksum  = Column(String, nullable=True)

    undone_at     = Column(DateTime, nullable=True)
    created_at    = Column(DateTime, default=utc_now)
```

For file overwrites, the op includes `before_checksum` and `after_checksum`, covering the forensic use case that `ArtifactEditEvent` served.

### 4.3 File Versioning

When `workspace_write_file` overwrites an existing file, the old content is snapshotted:

```
workspace/.versions/{node_id}/
├── v1_2026-04-01T10:30:00Z.md
├── v2_2026-04-03T14:15:00Z.md      ← previous content before latest overwrite
└── manifest.json                     ← [{version, timestamp, checksum, size}]
```

**Implementation in WorkspaceService (complete flow — provenance → validation → CAS → version → write):**

```python
async def write_file(self, db, entity_id, path, content, mime_type, actor,
                     expected_checksum: Optional[str] = None):
    # 1. Pre-write validation hook (§4.4)
    suffix = Path(path).suffix.lower()
    validator = WRITE_VALIDATORS.get(suffix)
    if validator:
        result = validator(content)
        if not result.ok:
            raise ValidationError(result.errors)

    node = await self.get_node_by_path(db, entity_id, path)

    if node:
        # 2. Provenance check (§7.2) — block agent overwrite of user uploads
        if actor.type == "agent" and node.origin_type in ("upload", "ingest"):
            raise ProtectedFileError(
                f"Cannot overwrite user-uploaded file '{path}'. "
                f"Create a derivative (e.g., '{_suggest_derivative_path(path)}') "
                f"or ask the user for explicit permission."
            )

        # 3. Optimistic lock (§8.1) — CAS guard
        if expected_checksum and node.checksum != expected_checksum:
            raise ConflictError(
                f"File changed since you read it. "
                f"Expected {expected_checksum[:8]}..., current is {node.checksum[:8]}..."
            )

        # 4. OVERWRITE — snapshot old version first
        old_content = await self.storage.read_file(node.storage_key)
        old_checksum = node.checksum
        version_key = (
            f"{entity_id}/workspace/.versions/{node.id}/"
            f"v{node.version}_{utc_now_iso()}{_suffix(node.name)}"
        )
        await self.storage.write_file(version_key, old_content)

        # 5. Write new content
        new_checksum = sha256(content)
        await self.storage.write_file(node.storage_key, content)
        node.version += 1
        node.checksum = new_checksum
        node.size_bytes = len(content)
        node.updated_at = utc_now()

        self._log_op(db, entity_id, "overwrite", actor, node.id,
                     payload={"path": path, "version": node.version},
                     before_checksum=old_checksum, after_checksum=new_checksum)
    else:
        # CREATE — new file (storage_key is path-independent)
        node = await self._create_file_node(db, entity_id, path, content, mime_type, actor)
        # storage_key = f"{entity_id}/workspace/blobs/{node.id}/{sanitize(name)}"
        self._log_op(db, entity_id, "create_file", actor, node.id,
                     payload={"path": path, "size": len(content)})

    return node
```

**Why `.versions/` directory instead of DB blobs:**
- Same storage layer (local FS now, S3 later) — no special handling
- Versions can be large (PDFs, spreadsheets) — DB is the wrong place
- The `manifest.json` gives quick version listing without scanning disk
- `.versions/` is hidden from normal `list_children` / `get_tree` queries (filtered by convention)

### 4.4 Pre-Write Validation Hooks

Replaces artifact-specific JSON validation with a general mechanism:

```python
WRITE_VALIDATORS = {
    ".json": validate_json_content,
    # Extensible: add .yaml, .csv, etc.
}

async def write_file(self, db, entity_id, path, content, ...):
    suffix = Path(path).suffix.lower()
    validator = WRITE_VALIDATORS.get(suffix)
    if validator:
        result = validator(content)
        if not result.ok:
            raise ValidationError(result.errors)
    # ... proceed with write
```

### 4.5 Tables Removed

- `resources` → replaced by `workspace_nodes`
- `artifacts` → replaced by `workspace_nodes` (deliverables = files in `Deliverables/` with metadata)
- `artifact_edit_events` → replaced by `workspace_ops` (with checksums)

---

## 5. Feature Parity Map

Every existing user-facing capability must survive. Nothing lost.

### 5.1 Resource Features

| Current | New |
|---|---|
| Upload files → `POST /ingest/resources` | Same endpoint → materializes to `workspace/Inbox/` as `WorkspaceNode` |
| List files → `GET /entities/{eid}/resources` | `GET /entities/{eid}/workspace/tree` (hierarchical) |
| View/download → `GET /entities/{eid}/resources/{rid}/view` | `GET /entities/{eid}/workspace/file/{node_id}` |
| Rename → `PATCH /entities/{eid}/resources/{rid}` | `POST /entities/{eid}/workspace/rename` |
| Delete → `DELETE /entities/{eid}/resources/{rid}` | `DELETE /entities/{eid}/workspace/node` (soft delete) |
| Metadata extraction → `POST /metadata-preprocess` | Same, targets `WorkspaceNode` instead of `Resource` |
| Chat file selection → `resource_ids[]` | `node_ids[]` |

### 5.2 Artifact Features

| Current | New |
|---|---|
| Agent creates memo → `portfolio_create_artifact(content, type="memo")` | `workspace_write_file("Deliverables/Memos/{title}.md", content)` |
| Agent creates report → `portfolio_create_artifact(content, type="report")` | `workspace_write_file("Deliverables/Reports/{title}.md", content)` |
| Agent edits artifact → resolve → validate → `apply_artifact_edit` | `workspace_write_file("Deliverables/Memos/existing.md", new_content)` — versioning automatic |
| List artifacts → `portfolio_list_artifacts()` | `workspace_list_files("Deliverables/")` (or `get_tree("Deliverables/")`) |
| Read artifact → `portfolio_read_artifact(id)` | `workspace_read_file("Deliverables/Memos/series-a.md")` |
| View versions → v1.md, v2.md in `artifacts/{id}/` | `GET /workspace/file/{node_id}/versions` → lists `.versions/{node_id}/` |
| Artifact types (memo/factsheet/report) | `metadata_json.deliverable_type` + folder path |
| Artifact status (draft/final) | `metadata_json.status` |
| Fuzzy search "the memo about X" | `workspace_search_files("X")` filtered to `Deliverables/` |
| Version diff | `GET /workspace/file/{node_id}/diff?v1=2&v2=3` (new, was impossible before) |

### 5.3 New Capabilities (Not Possible Before)

- Folder nesting at any depth
- Drag-and-drop folder upload
- Zip extraction with structure preservation
- Agent-driven file reorganization (move, rename, mkdir)
- Cross-file search
- Version history for ALL files (not just artifacts)
- Undo any operation
- Batch undo (agent restructures)
- Deliverable version diff

---

## 6. Agent Toolkit (Unified)

One set of tools. No resource tools, no artifact tools.

### 6.1 Tool Roster: 13 Tools

```
# ── Workspace (browse + organize) ── 7 tools
workspace_get_tree(path, max_depth)                  # deep browse (rarely needed — tree is in context)
workspace_list_files(path)                           # single-level listing with details
workspace_read_file(path)                            # read content (text, PDF/Office extraction)
workspace_search_files(query, folder?, content?)     # search by name + content, scoped to folder
workspace_create_folder(path)                        # create folder structure
workspace_move(from_path, to_path)                   # move file or folder (DB-only, cascades paths for descendants)
workspace_rename(path, new_name)                     # rename in place

# ── Workspace (write + manage) ── 6 tools
workspace_write_file(path, content, meta?)           # create or overwrite (auto-versions)
workspace_annotate(path, description)                # set node description (shown in tree context)
workspace_delete(path)                               # soft delete (reversible)
workspace_file_versions(path)                        # list version history
workspace_restore_version(path, version)             # revert to previous version
workspace_history(limit)                             # recent ops audit
```

**Compared to old system:** 8 old tools (2 resource + 6 artifact) → 13 new tools. Each is simpler — no fuzzy resolution, no intent detection, no validation ceremony. The agent rarely needs `get_tree()` since the annotated tree is injected into context automatically (§9). Net complexity is much lower.

**`workspace_search_files` with folder scoping:**

```python
@tool
async def workspace_search_files(query: str, folder: str = "", content: bool = True) -> str:
    """Search for files by name and optionally content.
    - query: search terms
    - folder: restrict to this subtree (e.g., "Deliverables/Memos/")
    - content: if True, also search inside file contents (slower but finds more)"""
```

### 6.2 How the Agent Creates Deliverables

Old flow (7 steps):
```
1. portfolio_list_artifacts()          → scan existing
2. _looks_like_create_intent()         → regex intent classification
3. portfolio_create_artifact(content, type="memo", title="Series A Analysis")
   → validate → assign UUID → compute path → write file → write DB
4. Return artifact_id
```

New flow (1-2 steps):
```
1. workspace_write_file(
     path="Deliverables/Memos/series-a-analysis.md",
     content="...",
     meta={"deliverable_type": "memo", "status": "draft"}
   )
   → auto-create parents → write file → write DB → log op
```

If the file exists, it's an edit (old version auto-snapshotted). If it doesn't, it's a create. The path IS the intent.

### 6.3 How the Agent Edits Deliverables

Old flow (5-7 steps):
```
1. portfolio_resolve_artifact_target(title_hint="series a analysis")
   → fuzzy scoring → confidence check → maybe "ambiguous, pick one"
2. portfolio_read_artifact(artifact_id)
3. portfolio_validate_artifact_edit(artifact_id, new_content)
4. portfolio_apply_artifact_edit(artifact_id, new_content, mode="versioned")
   → re-resolve → check intent policy → validate → write v2 → audit log
```

New flow (2 steps):
```
1. workspace_read_file("Deliverables/Memos/series-a-analysis.md")
2. workspace_write_file("Deliverables/Memos/series-a-analysis.md", updated_content)
   → old content auto-versioned → new content written → op logged
```

If the agent isn't sure which file: `workspace_search_files("series a")` → get path → read → write. Same outcome, no fuzzy scoring machinery.

### 6.4 System Prompt

```
Your context includes the entity's workspace tree (with file descriptions) and
workspace notes. Use these to navigate directly — you rarely need workspace_get_tree().

**Reading:** Use workspace_read_file(path) with paths from the tree context.
Use workspace_search_files(query, folder) when the tree doesn't have what you need.

**Creating deliverables:** Write to Deliverables/ folder:
  - Memos: Deliverables/Memos/{title}.md
  - Reports: Deliverables/Reports/{title}.md
  - Factsheets: Deliverables/Factsheets/{title}.md
Set metadata {"deliverable_type": "...", "status": "draft|final"} on write.

**Editing:** Write to the same path. Old version is automatically preserved.

**Write zones:**
  - You CAN freely create/edit files you created (in Deliverables/ or elsewhere)
  - You CAN move/rename any file to organize — that's always safe
  - You CANNOT overwrite or delete user-uploaded files (the system will block you)
  - If you need to analyze an uploaded file, create a derivative:
    "Data Room/pitch-deck.pdf" → create "Deliverables/pitch-deck-analysis.md"
  - If the user explicitly asks you to modify a source file, confirm first

**Annotating:** After reading a file, use workspace_annotate(path, description).
Descriptions appear in the tree context for future conversations.

**Workspace notes:** After cross-referencing files or learning non-obvious context,
update WORKSPACE_NOTES.md. Focus on cross-file dependencies, data quality issues,
process context, and information gaps. Keep concise. Delete stale notes.
```

---

## 7. Write Zones: Provenance Rules + Folder Conventions

The old system enforced source/output separation structurally: resources were read-only inputs, artifacts were agent-writable outputs. The unified workspace needs to preserve this invariant without rigid folder enforcement.

### 7.1 The Real Invariant

It's not "certain folders are writable." It's: **agents must not silently mutate files the user uploaded.** A user who uploads a signed SPA would be alarmed if an agent rewrote it. But moving it to a different folder is fine — that's organizing, not mutating.

### 7.2 Hard Rules (Enforced in WorkspaceService)

Provenance-based, checked on every content-mutating operation:

| Operation | Agent-created (`origin_type="agent"`) | Shared (`"shared"`) | User-uploaded (`"upload"\|"ingest"`) |
|---|---|---|---|
| **Read** | Always | Always | Always |
| **Overwrite content** | Allowed | Allowed | **Blocked** — error with derivative path suggestion |
| **Move / rename** | Allowed | Allowed | Allowed (organizing is non-destructive) |
| **Delete** | Allowed (soft) | **Blocked** | **Blocked** — requires user confirmation |
| **Annotate** | Allowed | Allowed | Allowed (metadata is non-destructive) |

See composed `write_file` in §4.3, step 2 for implementation.

**Why move/rename is allowed for uploads:** Organizing is the entire point of workspace agents. Moving `Inbox/pitch-deck.pdf` to `Data Room/pitch-deck.pdf` doesn't lose data.

**Escape hatch:** When the user explicitly asks to modify a source file, the request flows through with user confirmation context. The hard rule only blocks *unsolicited* agent overwrites.

### 7.3 Soft Conventions (Prompt-Level)

Folder names are guidance, not enforcement. The agent adapts to whatever structure it finds.

```
Workspace zone conventions:

- Inbox/           — Upload landing zone. Organize out of here, don't leave files.
- Deliverables/    — Your write zone. Memos, reports, factsheets go here.
- Data Room/       — User's source material. Read freely, organize freely,
                     never overwrite content.
- Everything else  — Follow the user's structure. Read and organize freely.

Rules:
1. Write your outputs under Deliverables/ (create subfolders as needed)
2. You CAN move/rename any file to organize — that's always safe
3. You CANNOT overwrite user-uploaded files — create a derivative instead
   Example: to analyze "pitch-deck.pdf", create "Deliverables/Memos/pitch-deck-analysis.md"
4. If the user explicitly asks you to modify a source file, confirm before proceeding
```

**Why conventions, not enforcement:** Real workspaces are messy. Users create their own folders, put notes in unexpected places, deviate from templates. The provenance rule (don't overwrite uploads) holds regardless of folder structure. Conventions help the agent navigate but don't constrain the user.

### 7.4 How Provenance Flows

| Action | Resulting `origin_type` |
|---|---|
| User uploads via UI | `"upload"` |
| Parking lot materialization | `"ingest"` |
| Agent creates file | `"agent"` |
| Agent moves/renames a file | **Preserved** from original (move doesn't change provenance) |
| User creates file via UI | `"user"` (same protection as agent-created) |
| `WORKSPACE_NOTES.md` (template-created) | `"shared"` — both user and agent can edit |

**`origin_type="shared"`** exists for the rare case where a file is intentionally collaborative between user and agent. Currently only used for `WORKSPACE_NOTES.md`. Without this, either the user or the agent would be locked out of editing it, defeating its purpose as scoped memory.

### 7.5 Derivative File Pattern

When an agent is blocked from overwriting, the error suggests a derivative path:

```python
def _suggest_derivative_path(original_path: str) -> str:
    """Suggest a path for agent-created derivative of a protected file."""
    stem = Path(original_path).stem
    suffix = Path(original_path).suffix
    # Put analysis/summaries in Deliverables, keep data derivatives nearby
    return f"Deliverables/{stem}-analysis.md"
```

Examples:
- `Data Room/Legal/series-a-spa.pdf` → agent creates `Deliverables/series-a-spa-analysis.md`
- `Data Room/Financials/cap-table.xlsx` → agent creates `Deliverables/cap-table-summary.md`
- `Technical/architecture.md` → agent creates `Deliverables/architecture-review.md`

---

## 8. Additional Design Considerations

### 8.1 Concurrency (Optimistic Locking)

**Problem:** Two agents (or user + agent) edit the same file simultaneously. One overwrites the other's changes silently.

**Solution:** Compare-and-swap using checksums (see composed `write_file` in §4.3, step 3).

The agent tool `workspace_read_file` returns the checksum alongside content. `workspace_write_file` optionally accepts it:

```python
@tool
async def workspace_write_file(path: str, content: str,
                                expected_checksum: str = "",
                                meta: str = "") -> str:
    """Write or overwrite a file. If expected_checksum is provided,
    the write fails if the file has changed since you last read it
    (prevents accidental overwrites in concurrent editing)."""
```

### 8.2 Storage Quotas *(post-MVP — add when SaaS launches)*

**Problem:** SaaS needs per-entity (and eventually per-tenant) storage limits.

**Solution:** Quota check on every write operation. Defer until multi-tenant — single-user MVP doesn't need this, and adding it prematurely means testing quota edge cases before the core file system works.

```python
# In WorkspaceService:
async def _check_quota(self, db, entity_id, additional_bytes: int):
    total = await db.execute(
        select(func.sum(WorkspaceNode.size_bytes))
        .where(WorkspaceNode.entity_id == entity_id)
        .where(WorkspaceNode.deleted_at.is_(None))
    )
    current = total.scalar() or 0
    limit = await self._get_quota(entity_id)  # from config or tenant settings
    if current + additional_bytes > limit:
        raise QuotaExceededError(
            f"Entity storage: {current / 1e6:.1f}MB / {limit / 1e6:.1f}MB. "
            f"Cannot write {additional_bytes / 1e6:.1f}MB."
        )
```

Config:

```python
# config.py
WORKSPACE_MAX_FILE_BYTES: int = 50 * 1024 * 1024        # 50MB per file
WORKSPACE_MAX_ENTITY_BYTES: int = 500 * 1024 * 1024     # 500MB per entity
WORKSPACE_MAX_UPLOAD_FILES: int = 200                     # per single upload
```

### 8.3 URL Bookmarks

**Problem:** Current `Resource` model stores URLs as `resource_type="url"` with no file. Should workspace handle this?

**Decision:** URLs are **bookmark nodes** — `node_type="bookmark"` with `url` set and `storage_key=None`. They appear in the tree but are semantically distinct from files. File-specific logic (checksums, versioning, size_bytes, content search) does not apply to bookmarks — no guard clauses needed, just skip bookmarks in file-only queries. On view/download, bookmarks resolve to the URL.

### 8.4 Trash / Recycle Bin

**Problem:** Soft delete sets `deleted_at` but users need to see and restore deleted items.

**Solution:** API endpoint + UI component.

```
GET /entities/{eid}/workspace/trash              → list soft-deleted nodes
POST /entities/{eid}/workspace/trash/{node_id}/restore
DELETE /entities/{eid}/workspace/trash/{node_id}  → hard delete (permanent, admin only)
```

Trash auto-purges after configurable retention period (default: 30 days).

### 8.5 Content Search

**Problem:** `workspace_search_files(query)` searching only filenames is insufficient for a VC tool. Users need to find "the doc that mentions liquidation preferences."

**Solution:** Two-tier search.

- **Tier 1 (immediate):** Filename + path pattern matching (glob-style). Fast, no indexing needed.
- **Tier 2 (indexed):** Full-text content search. On file write, extract text content (using existing PDF/Office extractors) and store in a search index.

For SQLite (current): FTS5 virtual table (raw DDL, not an ORM model — created outside `Base.metadata.create_all()`).
For cloud (future): swap to Elasticsearch or similar via search adapter.

```sql
-- Created via raw DDL in DB init, not via SQLAlchemy ORM
CREATE VIRTUAL TABLE IF NOT EXISTS workspace_search_index USING fts5(
    node_id,
    entity_id,
    content,
    content='',          -- external content mode: we manage inserts/deletes
    tokenize='porter'    -- stemming for English
);
```

**Post-MVP consideration:** FTS5 is zero-dependency and handles <300 files per entity easily. Content extraction on write (PDF/Office → text) reuses existing extractors. Defer building this until filename search proves insufficient — per AI-native principle, ship without it first, add when users hit the wall.

### 8.6 Workspace Templates

**Problem:** Every new entity starts empty. VC firms have standard folder structures.

**Solution:** Configurable template applied on entity creation.

```python
DEFAULT_WORKSPACE_TEMPLATE = [
    "Inbox",
    "Data Room",
    "Data Room/Financials",
    "Data Room/Legal",
    "Technical",
    "Deliverables",
    "Deliverables/Memos",
    "Deliverables/Reports",
    "Deliverables/Factsheets",
]
```

Created automatically when entity is created. Configurable per tenant in SaaS.

---

## 9. Agent Context: Three-Layer Workspace Awareness

The agent needs to orient itself in the workspace without burning tool calls. Three layers provide this, each with different scope, freshness, and maintenance.

### 9.1 Layer 1: Auto-Generated Tree (always fresh)

Built from `workspace_nodes` table on every agent invocation. Pure structure — what files exist, where, how big. Zero maintenance.

```
Inbox/ (3 files)
  pitch-deck-v4.pptx  (2.4MB)
  financials-update.xlsx  (890KB)
  team-bios.pdf  (1.1MB)
Data Room/
  Financials/
    2025-Q4-audited.xlsx
    2026-projections.xlsx
    cap-table-v3.xlsx
  Legal/
    series-a-spa.pdf
    series-a-term-sheet.pdf
    ip-assignment-agreement.pdf
Deliverables/
  Memos/
    series-a-investment-memo.md
    competitive-landscape.md
```

Generated by a single query: `SELECT path, name, node_type, size_bytes, mime_type, metadata_json FROM workspace_nodes WHERE entity_id = ? AND deleted_at IS NULL ORDER BY path`. For <300 files, sub-millisecond.

### 9.2 Layer 2: Node Descriptions (per-node, in `metadata_json`)

Short labels on individual files/folders. What this thing IS — terse, factual, rendered inline with the tree. Stored in `metadata_json.description` on each `WorkspaceNode`.

Set by:
- **Metadata extraction agent** — after processing an uploaded file, writes a one-line description
- **Chat agent** — after deep-reading a file (e.g., answering "what's the share price?"), annotates the node
- **User** — inline edit on file card in UI
- **Organize-uploads agent** — annotates files and folders as it classifies content

Tool: `workspace_annotate(path, description)`.

These enrich the tree when rendered:

```
Data Room/
  Financials/ — audited statements + projections
    2025-Q4-audited.xlsx
    2026-projections.xlsx — current ARR, updated Mar 2026
    cap-table-v3.xlsx — post-Series A, fully diluted
  Legal/ — Series A transaction docs
    series-a-spa.pdf — signed, 2026-01-15, $4.50/share
    ip-assignment-agreement.pdf — US patents only
Deliverables/
  Memos/
    series-a-investment-memo.md — primary thesis, v3, draft
    competitive-landscape.md — stale, last updated 2025-11
```

### 9.3 Layer 3: Workspace Notes (entity-scoped knowledge, agent/user maintained)

A `WORKSPACE_NOTES.md` file at the workspace root. Cross-cutting knowledge that doesn't belong to any single node — relationships between files, data quality warnings, process context, navigation guidance.

This is a **scoped memory system**, analogous to global `MEMORY.md` but narrowed to one entity's workspace.

```markdown
# Workspace Notes — Acme Corp

## Key relationships
- Series A docs split between Legal/ and Financials/ — always check both
- Cap table and SPA should have matching share price; verify if either updates

## Data quality
- ARR in pitch-deck-v4 is from Dec 2025; 2026-projections.xlsx has current numbers
- Employee count (47, Mar 2026) was verbal from founder, not in data room

## IP
- IP assignment covers US patents only; international status unclear
- 2 more patent applications filed since Q4 (per founder email, not in data room)

## Process
- Legal review pending — don't share factsheet externally
- Series A close expected mid-April 2026
```

**Maintenance:**
- Agent writes/updates with `workspace_write_file("WORKSPACE_NOTES.md", ...)` — no special tool
- User can edit directly in UI (it's a normal workspace file)
- Agent is prompted to update after cross-referencing multiple files or learning non-obvious context
- Version-controlled by workspace op log like any other file

**What goes here vs. node descriptions:**

| Node descriptions | Workspace notes |
|---|---|
| What one file/folder IS | What I KNOW across files |
| `"signed SPA, $4.50/share"` | `"SPA and cap table should match on share price"` |
| Labels (short, factual) | Knowledge (relationships, warnings, gaps) |
| Rendered inline with tree | Rendered below tree as separate section |

### 9.4 Composed Context Injection

All three layers are injected into the agent's context at the start of every turn, before the user's message. Same injection point as the current resource preamble in `build_harness_user_attachment_text()`.

```
=== Entity Workspace: Acme Corp (23 files, 8 folders) ===

Inbox/ (3 files)
  pitch-deck-v4.pptx  (2.4MB)
  financials-update.xlsx  (890KB)
  team-bios.pdf  (1.1MB)
Data Room/
  Financials/ — audited statements + projections
    2025-Q4-audited.xlsx
    2026-projections.xlsx — current ARR, updated Mar 2026
    cap-table-v3.xlsx — post-Series A, fully diluted
  Legal/ — Series A transaction docs
    series-a-spa.pdf — signed, 2026-01-15, $4.50/share
    series-a-term-sheet.pdf
    ip-assignment-agreement.pdf — US patents only
  Technical/
    architecture-overview.md
    patent-portfolio.pdf — 7 granted, 3 pending
Deliverables/
  Memos/
    series-a-investment-memo.md — primary thesis, v3, draft
    competitive-landscape.md — stale, needs refresh
  Factsheets/
    acme-corp-factsheet.md — last updated 2026-03-28

--- Workspace Notes ---
- Series A docs split between Legal/ and Financials/ — check both
- ARR in pitch-deck-v4 is from Dec 2025; use 2026-projections.xlsx instead
- IP assignment covers US only; international status unclear
- Legal review pending — don't share factsheet externally
- Series A close expected mid-April 2026
```

**Token budget:** For a typical entity with <300 files, this is ~2000-5000 tokens. Comparable to the current resource preamble. The tree portion scales linearly with file count; notes are bounded by how much the agent writes (system prompt caps at ~20 bullet points).

### 9.5 Agent System Prompt Addition

```
Your context includes the full workspace tree with descriptions and workspace notes.
Use this to navigate directly — you rarely need workspace_get_tree() since you already
have the structure.

After completing tasks that involve reading multiple files or discovering non-obvious
relationships, update WORKSPACE_NOTES.md with what you learned. Focus on:
- Cross-file relationships and dependencies
- Data quality issues or contradictions between files
- Process context from the user (deadlines, restrictions, pending actions)
- Information gaps (things you expected to find but didn't)

Keep it concise — bullet points, not paragraphs. Don't repeat what's already in
node descriptions. Delete notes that are no longer relevant.
```

### 9.6 `WORKSPACE_NOTES.md` in the Tree

The file exists at the workspace root but is rendered specially:
- **Tree view:** The file is listed in the tree like any other file, so users know it exists
- **Context injection:** Its content is pulled into a separate "Workspace Notes" section below the tree, not duplicated as a tree entry
- **Hidden from `Deliverables/` listing:** It's at root level, not inside any subfolder, so it doesn't clutter functional folders

---

## 10. Storage Layer

### 10.1 Physical Layout

**Critical design choice: `storage_key` is decoupled from `path`.**

File paths in the tree (what users see) are independent of where bytes live on disk/S3. This means **moves and renames are DB-only operations** — no physical file rename, no S3 copy+delete cascade.

```
data/entities/{entity_id}/workspace/
├── blobs/                              ← flat by node_id, not mirroring tree
│   ├── {node_id_1}/pitch-deck.pdf
│   ├── {node_id_2}/2025-Q4.xlsx
│   └── {node_id_3}/series-a-analysis.md
└── .versions/
    └── {node_id}/
        ├── v1_2026-04-01T10:30:00Z.md
        └── manifest.json
```

`storage_key` format: `{entity_id}/workspace/blobs/{node_id}/{filename}`

The tree structure exists only in `workspace_nodes` rows (path + parent_id). The filesystem is a flat blob store. This is how Google Drive and Dropbox work internally — it makes moves O(1) and eliminates a whole class of consistency bugs when paths change.

### 10.2 StorageAdapter Extensions

```python
class StorageAdapter(ABC):
    # ... existing: write_file, read_file, copy_file, delete_file,
    #               ensure_dir, exists, delete_recursive, get_full_path ...

    @abstractmethod
    async def file_checksum(self, relative_path: str) -> str:
        """SHA-256 of file contents."""
```

**Why no `move_file` or `list_dir`:** With decoupled storage keys (§10.1), workspace moves/renames are DB-only — they update `path` and `parent_id` on workspace_nodes rows, but `storage_key` stays the same. The storage layer never needs to move files. Directory listing comes from DB queries, not filesystem scans. This keeps the StorageAdapter minimal — it's just a blob store.

### 10.3 Cloud (S3)

- `storage_key` maps directly to S3 object key (already path-independent, no migration needed)
- Folders are virtual (DB-only rows, no S3 object)
- Moves/renames = zero S3 operations (just DB path updates)
- `.versions/` stored as S3 objects with lifecycle rules for auto-expiry
- Multi-tenancy via key prefix: `s3://{bucket}/{tenant_id}/entities/{entity_id}/workspace/blobs/...`

---

## 11. Chat Context Passing

Two context channels, complementary:

1. **Workspace tree + notes** (§9) — auto-injected on every turn. Gives the agent orientation without tool calls.
2. **User-selected file content** — when user selects specific files in chat UI, their content is injected as multimodal parts / text preamble (same as current behavior, different model class).

```
Frontend: user selects files from workspace tree → sends node_ids[]
Backend: loads WorkspaceNode rows → builds multimodal parts + text preamble
         + generates annotated tree + reads WORKSPACE_NOTES.md
Agent: receives tree context + selected file content + workspace tools
```

**gemini_context.py field mapping** (mechanical refactor):

| Old (`Resource`) | New (`WorkspaceNode`) |
|---|---|
| `res.id` | `node.id` |
| `res.resource_type == "url"` | `node.url is not None` |
| `res.title` | `node.name` |
| `res.mime_type` | `node.mime_type` |
| `res.original_filename` | `node.name` |
| `res.relative_path` | `node.storage_key` |
| `res.url` | `node.url` |
| `res.metadata_json` | `node.metadata_json` |

Same logic, same MIME-type handling, same size limits, same PDF/Office extraction.

---

## 12. Parking Lot Integration

Unchanged except materialization destination:

```
Ingest → Parking Lot → Materialize → workspace/Inbox/{filename}
```

The materializer creates `WorkspaceNode` rows. The `Inbox/` folder is the landing zone. Users or agents reorganize from there.

---

## 13. Agent Orchestration

### 13.1 Goal-Driven Workspace Agents

| Goal | Trigger | What It Does |
|---|---|---|
| **Organize uploads** | After ingest to Inbox/ | Classify files, create folders, move from Inbox/ to proper location |
| **Restructure** | User request | Review tree, propose new structure, execute moves |
| **Process documents** | After upload | Read files, extract metadata, create summaries in Deliverables/ |
| **Clean up** | Scheduled | Find duplicates (by checksum), flag stale files |

Same pattern as academic module: one agent factory, different goal prompts, same toolkit.

### 13.2 Safety

```
Agent → workspace tools → WorkspaceService → workspace_ops log + StorageAdapter
```

- Scoped per entity (closure-bound tools)
- Soft delete only (hard purge = admin op)
- Optimistic locking via checksums
- Batch undo via `batch_id`
- Storage quotas enforced

### 13.3 Plan-Then-Execute

For restructuring, agent builds plan → user approves → batch execute:

```
1. workspace_get_tree() → current state
2. Reason about optimal structure
3. Output plan: [{move, from, to}, ...]
4. Execute as batch (shared batch_id for atomic undo)
```

---

## 14. API Endpoints

```
# ── Tree ──
GET    /entities/{eid}/workspace/tree?path=&depth=3
GET    /entities/{eid}/workspace/ls?path=
GET    /entities/{eid}/workspace/node/{node_id}
GET    /entities/{eid}/workspace/search?q=&content=true

# ── Files ──
GET    /entities/{eid}/workspace/file/{node_id}                    # download by ID
GET    /entities/{eid}/workspace/file?path=                        # download by path
POST   /entities/{eid}/workspace/file?path=                        # upload single file
POST   /entities/{eid}/workspace/upload                            # folder/zip upload
POST   /entities/{eid}/workspace/folder?path=                      # create folder

# ── Versioning ──
GET    /entities/{eid}/workspace/file/{node_id}/versions           # version history
GET    /entities/{eid}/workspace/file/{node_id}/versions/{version} # download specific version
POST   /entities/{eid}/workspace/file/{node_id}/restore/{version}  # revert
GET    /entities/{eid}/workspace/file/{node_id}/diff?v1=&v2=       # text diff between versions

# ── Mutations ──
POST   /entities/{eid}/workspace/move     {from_path, to_path}
POST   /entities/{eid}/workspace/rename   {path, new_name}
POST   /entities/{eid}/workspace/copy     {from_path, to_path}
DELETE /entities/{eid}/workspace/node?path=

# ── Trash ──
GET    /entities/{eid}/workspace/trash
POST   /entities/{eid}/workspace/trash/{node_id}/restore
DELETE /entities/{eid}/workspace/trash/{node_id}                   # hard delete

# ── History ──
GET    /entities/{eid}/workspace/ops?limit=50
POST   /entities/{eid}/workspace/ops/{op_id}/undo

# ── Metadata ──
POST   /entities/{eid}/workspace/node/{node_id}/metadata-preprocess
PATCH  /entities/{eid}/workspace/node/{node_id}                    # update metadata/name
```

---

## 15. Upgrade Path

### 15.1 Clean Start (No Migration)

This is a clean replacement. Existing `resources`, `artifacts`, `artifact_edit_events` tables and their `data/entities/{id}/resources/` / `data/entities/{id}/artifacts/` directories are dropped. No migration script, no ID preservation, no backward-compat shims.

**Why:** The product is pre-launch. Carrying old data structure forward costs more in complexity than re-ingesting a small amount of test data. First-principle: don't build migration infrastructure for data that doesn't exist yet in production.

**Steps:**
1. Drop old tables (`resources`, `artifacts`, `artifact_edit_events`)
2. Delete old file directories (`data/entities/*/resources/`, `data/entities/*/artifacts/`)
3. Create new tables (`workspace_nodes`, `workspace_ops`)
4. On entity creation, apply workspace template (§8.6) to scaffold default folders

Chat history referencing old `resource_ids_json` / `artifact_ids_json` will show "file not found" — acceptable for dev data.

### 15.2 Implementation Order

**Step 1: Schema + Core Service**
- `workspace_nodes`, `workspace_ops` tables
- `WorkspaceService` (all mutations, versioning, validation hooks, quota checks)
- `StorageAdapter` extensions
- Optimistic locking

**Step 2: Backend API**
- All `/workspace/` endpoints
- Update materializer (write to workspace)
- Update `gemini_context.py` (WorkspaceNode input)
- Update chat router (node_ids)

**Step 3: Agent Tools**
- `build_workspace_tools()` — 13 tools
- Wire into `create_portfolio_agent()`
- Remove all old resource + artifact tools from agent
- Update system prompt

**Step 4: Frontend**
- Workspace tree component (replaces flat resource list)
- Workspace tree in deliverables view (replaces artifact list)
- Folder upload (drag-and-drop + zip)
- File version history viewer
- Trash view
- Update chat file selector

**Step 5: Cleanup**
- Drop `resources`, `artifacts`, `artifact_edit_events` tables and old `data/entities/*/resources/`, `data/entities/*/artifacts/` directories
- Remove dead code (artifact_service, artifact_editing, old resource tools)

---

## 16. Affected Files

| File | Change |
|------|--------|
| `models.py` | Add `WorkspaceNode`, `WorkspaceOp`. Remove `Resource`, `Artifact`, `ArtifactEditEvent`. FTS5 virtual table via raw DDL in DB init |
| `config.py` | Add workspace config (quotas, template, version retention). Remove artifact config |
| `services/workspace.py` | **NEW** — `WorkspaceService` |
| `services/workspace_tools.py` | **NEW** — `build_workspace_tools()` |
| `services/storage.py` | Add `move_file`, `list_dir`, `file_checksum` |
| `services/materializer.py` | Write to workspace, create `WorkspaceNode` |
| `services/gemini_context.py` | `Resource` → `WorkspaceNode` field mapping |
| `services/metadata_preprocess_jobs.py` | Read from `WorkspaceNode.storage_key` |
| `services/portfolio_deep_agent.py` | Replace all tools with workspace tools. Remove intent detection, fuzzy resolution |
| `services/artifact_service.py` | **DELETE** |
| `services/artifact_editing.py` | **DELETE** (read logic absorbed into workspace_tools) |
| `routers/entities.py` | Remove resource + artifact endpoints. Add workspace endpoints |
| `routers/chat.py` | `resource_ids` → `node_ids`, load from workspace |
| `routers/ingest.py` | Materializer writes to workspace |
| `frontend/src/components/EntityDetail.tsx` | Tree view replaces flat resource + artifact lists |
| `frontend/src/services/api.ts` | New workspace API methods |
| `frontend/src/types.ts` | `WorkspaceNode` type replaces `Resource` + `Artifact` |

---

## 17. Key Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Unify artifacts into workspace | Yes | Artifact complexity (fuzzy resolution, intent detection) exists to work around flat-file design. Workspace eliminates the root cause |
| Versioning scope | All files, not just deliverables | An overwritten data room doc is just as important to recover as an overwritten memo |
| Version storage | `.versions/` directory on disk | Same storage layer as files; scales to large binaries; auto-managed |
| Deliverable identity | Folder convention + metadata_json | No schema columns for artifact_type/status — metadata is flexible, folders are visual |
| Concurrency | Optimistic locking via checksum CAS | Handles agent-agent and agent-user conflicts without distributed locks |
| Quotas | Post-MVP — add when SaaS launches | Don't test quota edge cases before the core file system works |
| Content search | Filename search MVP; FTS5 when users hit the wall | Defer content extraction complexity; filename + path glob covers most VC use cases |
| Trash | Soft delete + 30-day auto-purge | Users need undo; storage can't grow forever |
| Workspace template | Configurable default folders | Saves setup time; establishes conventions agents follow |
| Storage key | Decoupled from path: `blobs/{node_id}/{filename}` | Moves are DB-only; no S3 copy cascades; eliminates path↔storage consistency bugs |
| URL bookmarks | `node_type="bookmark"` (not "file") | Avoids guard clauses in file-specific logic (checksum, versioning, content search) |
| WORKSPACE_NOTES.md | `origin_type="shared"` — both user and agent can edit | Without this, either user or agent is locked out; defeats purpose of scoped memory |
| Data upgrade | Clean start, no migration | Pre-launch product; migration infrastructure costs more than re-ingesting test data |
| Agent context model | Three layers: auto-tree + node descriptions + WORKSPACE_NOTES.md | Tree is always fresh; descriptions are per-node labels; notes are cross-cutting scoped memory |
| Context injection | Auto-inject annotated tree + notes on every agent turn | Eliminates cold-start `get_tree()` call; agent is immediately productive |
| Node annotations | `metadata_json.description` + `workspace_annotate` tool | Inline labels enrich tree; set by agents, metadata extraction, or users |
| Workspace notes | `WORKSPACE_NOTES.md` at root, agent/user maintained | Scoped memory for cross-file knowledge; analogous to MEMORY.md but per-entity |
| Search scoping | `folder` param on `workspace_search_files` | Agent can narrow search to subtree (e.g., "Deliverables/Memos/") |
| Write zones | Provenance-based hard rules + soft folder conventions | Agents can't overwrite uploads (enforced); folder names are guidance, not enforcement |
| Source protection | `origin_type` check on overwrite/delete | Real invariant is "don't mutate user uploads" — independent of folder structure |
| Derivative pattern | Agent creates analysis alongside protected source | Suggested path in error message guides agent to correct behavior |
