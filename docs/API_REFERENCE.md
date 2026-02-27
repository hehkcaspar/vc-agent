# VC Portfolio Manager - API Reference

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
    "version": 1,
    "status": "draft|final",
    "relative_path": "{entity_id}/artifacts/{artifact_id}/v1.md",
    "created_at": "2024-01-01T00:00:00",
    "updated_at": "2024-01-01T00:00:00"
  }
]
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
| version | int | Version number |
| status | string | "draft" or "final" |
| relative_path | string | Path to markdown file |
| created_at | datetime | Creation timestamp |
| updated_at | datetime | Last update timestamp |

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
