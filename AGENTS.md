# VC Portfolio Manager

## Project Overview

This is a **VC Portfolio Manager** web application designed for a US-based VC firm to manage portfolio companies as **Entities**. The application follows an **Entity-Canonical, Parking-Lot Ingestion** architecture.

### Core Purpose

The system manages portfolio companies with two kinds of content per Entity:
- **Resources**: User-provided materials (PDF, images, text/markdown, URLs)
- **Artifacts**: System-generated outputs (MVP: markdown files)

**Core MVP value**: Upload/store/browse materials reliably; nothing gets lost; future ingestion and smarter matching can be added without refactoring.

### Architecture Philosophy

1. **No Loss**: Every inbound submission is persisted to Parking Lot immediately
2. **Downstream Simplicity**: All normal portfolio/resource APIs operate only on **canonical** records (never missing entity_id)
3. **Resolver Isolation**: All entity-matching complexity lives behind `EntityResolver`
4. **Storage Abstraction**: Business logic uses a `StorageAdapter` so local FS can be swapped later

---

## Technology Stack

### Backend
- **Language**: Python 3.x
- **Framework**: FastAPI
- **Database**: SQLite (MVP), designed for future Postgres migration
- **Storage**: Local filesystem with `StorageAdapter` abstraction

### Frontend
- **Framework**: React
- **UI Pattern**: Admin panel with left sidebar navigation
- **State Management**: Tab state persistence required across navigation

### AI Integration
- **Gemini API**: For future AI-powered features
- **SDK**: `google-genai` (Python) or `@google/genai` (JavaScript)

---

## Project Structure

```
vc-agent/
├── doc/
│   └── MVP-prd.md              # Product Requirements Document
├── .agent/
│   ├── rules/                  # Agent behavior rules
│   │   ├── python-venv.md      # Python environment rules
│   │   └── using-powershell.md # PowerShell environment rules
│   └── skills/                 # Agent skills (symlinked from .agents)
├── .agents/
│   └── skills/                 # Installed agent skills
│       ├── brainstorming/      # Design exploration skill
│       ├── find-skills/        # Skill discovery
│       ├── gemini-api-dev/     # Gemini API development
│       └── vercel-react-best-practices/  # React optimization
└── skills-lock.json            # Skill version lock file
```

### Data Storage Layout (MVP)

```
/data/entities/
  /00000/                       # Parking lot pseudo-entity
    /parkinglot/{ingest_id}/
      /files/                   # Raw uploaded files
      /payload/
        text.md                 # Optional pasted text
        urls.json               # Optional URLs
        meta.json               # Source + hints + timestamps

  /{entity_id}/
    /resources/{resource_id}/   # Canonical resources
    /artifacts/{artifact_id}/   # Versioned markdown files
      v1.md
      v2.md
```

---

## Development Environment

### Operating System
- **Platform**: Windows 11
- **Shell**: PowerShell
- **Path Separator**: Use backslash (`\`) for file paths

### Python Environment Rules

**CRITICAL**: Never install Python packages or execute Python scripts in the global environment.

Always use one of these approaches:

1. **Activate virtual environment and chain commands**:
   ```powershell
   .\venv\Scripts\Activate.ps1 && python script.py
   ```

2. **Use direct executable path**:
   ```powershell
   .\venv\Scripts\python.exe script.py
   .\venv\Scripts\pip.exe install package
   ```

**Note**: Shell context does not persist across commands. Each `Shell` tool call starts fresh.

---

## Build and Development Commands

### Backend (Python/FastAPI)

```powershell
# Setup virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Run development server
uvicorn main:app --reload

# Run tests
pytest
```

### Frontend (React)

```powershell
# Install dependencies
npm install

# Start development server
npm run dev

# Build for production
npm run build

# Run tests
npm test
```

### Skill Management

```powershell
# Search for skills
npx skills find [query]

# Install a skill
npx skills add <owner/repo@skill> -g -y

# Check for updates
npx skills check

# Update all skills
npx skills update
```

---

## Code Style Guidelines

### General Principles

1. **Brainstorming First**: You MUST use the brainstorming skill before any creative work (creating features, building components, adding functionality, or modifying behavior).

2. **Design Approval**: Do NOT invoke implementation skills, write code, or scaffold projects until you have presented a design and the user has approved it.

3. **YAGNI**: Remove unnecessary features from all designs.

### Python (Backend)

- Follow PEP 8 style guidelines
- Use type hints for function signatures
- Use async/await for I/O operations
- Implement interfaces/abstract base classes for:
  - `ParkingLotManager`
  - `EntityResolver`
  - `ResourceMaterializer`
  - `StorageAdapter`

### React (Frontend)

Follow the **Vercel React Best Practices** skill which includes:

| Priority | Category | Impact |
|----------|----------|--------|
| 1 | Eliminating Waterfalls | CRITICAL |
| 2 | Bundle Size Optimization | CRITICAL |
| 3 | Server-Side Performance | HIGH |
| 4 | Client-Side Data Fetching | MEDIUM-HIGH |
| 5 | Re-render Optimization | MEDIUM |
| 6 | Rendering Performance | MEDIUM |
| 7 | JavaScript Performance | LOW-MEDIUM |
| 8 | Advanced Patterns | LOW |

Key patterns to remember:
- Use `Promise.all()` for independent async operations
- Import directly from source files (avoid barrel files)
- Use dynamic imports for heavy components
- Implement proper Suspense boundaries
- Minimize serialization at RSC boundaries

---

## Testing Instructions

### Backend Testing

```powershell
# Run all tests
pytest

# Run with coverage
pytest --cov=app tests/

# Run specific test file
pytest tests/test_entities.py
```

### Frontend Testing

```powershell
# Run all tests
npm test

# Run with coverage
npm test -- --coverage

# Run specific test file
npm test -- ComponentName.test.tsx
```

### Testing Requirements

1. **Unit Tests**: Test individual functions and components
2. **Integration Tests**: Test API endpoints and data flow
3. **E2E Tests**: Test critical user workflows

---

## Data Models

### Entity
```python
{
    "id": UUID,
    "type": "company",  # MVP only
    "name": str,  # required
    "created_at": datetime,
    "updated_at": datetime,
    "website": str | None,
    "status": "active" | "archived"
}
```

### IngestItem (Parking Lot)
```python
{
    "ingest_id": UUID,
    "source": "frontend" | "email" | "im" | "api",
    "status": "parked" | "resolution_required" | "failed" | "materialized",
    "parkinglot_path": str,
    "entity_hint_name": str | None,
    "entity_hint_domain": str | None,
    "created_at": datetime,
    "updated_at": datetime,
    "error": str | None
}
```

### Resource (Canonical)
```python
{
    "id": UUID,
    "entity_id": UUID,  # Must be real (not "00000")
    "resource_type": "file" | "text" | "url",
    "title": str,
    "mime_type": str | None,
    "original_filename": str | None,
    "relative_path": str,
    "url": str | None,
    "origin_ingest_id": UUID | None,
    "created_at": datetime,
    "updated_at": datetime
}
```

### Artifact (Canonical)
```python
{
    "id": UUID,
    "entity_id": UUID,
    "artifact_type": "memo" | "factsheet" | "report" | "other",
    "version": int,
    "status": "draft" | "final",
    "relative_path": str,
    "created_at": datetime,
    "updated_at": datetime
}
```

---

## API Endpoints

### Ingestion
```
POST /ingest/resources
```
Returns: `{ status: "resolved" | "resolution_required" | "failed", ... }`

### Parking Lot Management
```
GET    /parkinglot?status=...
GET    /parkinglot/{ingest_id}
POST   /parkinglot/{ingest_id}/resolve
```

### Portfolio Browsing
```
GET    /entities
POST   /entities
GET    /entities/{id}
GET    /entities/{id}/resources
GET    /entities/{id}/artifacts
```

---

## Security Considerations

### Authentication & Authorization

1. **Server Actions**: Always authenticate inside Server Actions (functions with `"use server"`). Do not rely solely on middleware or page-level checks.

2. **Input Validation**: Validate all inputs using Zod or similar before processing.

3. **Storage Security**: The `StorageAdapter` abstraction ensures storage backend can be swapped without exposing credentials in business logic.

### Materialization Safety

Prefer **copy → verify → write DB → delete parking** (safer than move-first).

---

## Deployment

### MVP Phase
- Local filesystem storage
- SQLite database
- Single-instance deployment

### Future Considerations
- Cloud storage via `StorageAdapter` (S3/GCS)
- Postgres database migration
- Multi-instance deployment with shared storage

---

## Extension Points

The architecture supports future extensions:

- **New ingestion sources** (email/IM): Add to `/ingest/resources` endpoint
- **Smarter matching**: Update `EntityResolver` only
- **Cloud storage**: Swap `StorageAdapter` implementation
- **Artifact generation engine**: Write through `ArtifactStore` adapter

---

## Resources

- **Skills CLI**: https://skills.sh/
- **Gemini API Docs**: https://ai.google.dev/gemini-api/docs/
- **Vercel React Best Practices**: See `.agents/skills/vercel-react-best-practices/`
