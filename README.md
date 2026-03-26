# VC Portfolio Manager

VC Portfolio Manager is a web application for managing portfolio companies as canonical entities, with durable ingestion via a parking-lot workflow.

## What It Does

- Manage portfolio entities (create, edit, archive).
- Ingest files, text, and URLs without data loss.
- Resolve inbound content to existing/new entities.
- Browse entity resources and artifacts in a responsive UI.
- **Entity workspace:** Gemini-backed chat with sessions, context from selected resources/artifacts, and shortcuts (presets) that can generate markdown or structured **JSON** artifacts (for example portfolio metadata extraction).
- **Artifact viewer:** Markdown preview or JSON editors with **Form** vs **Raw JSON** modes (shared segmented-toggle styling with list/grid).
- Support light/dark themes and adaptive layout for desktop/laptop/mobile.

## Tech Stack

- Backend: FastAPI, SQLAlchemy (async), SQLite
- Frontend: React + TypeScript + Vite + SWR
- Storage: Local filesystem via `StorageAdapter`

## Quick Start

### Prerequisites

- Python 3.11+ (3.13 supported; use the pinned backend requirements as written)
- Node.js 18+

### Setup

```powershell
# From repo root — always install backend deps from this file (pins matter on Python 3.13).
python -m venv venv
.\venv\Scripts\Activate.ps1
.\venv\Scripts\pip.exe install -U pip
.\venv\Scripts\pip.exe install -r "backend/requirements.txt"

cd frontend
npm install
cd ..
```

### Backend dependencies and Python 3.13

The versions in `backend/requirements.txt` are chosen so a clean install works on **Python 3.13** (especially Windows) without extra toolchains:

| Symptom | Cause | Fix (already in `requirements.txt`) |
|--------|--------|-------------------------------------|
| `pydantic-core` metadata / build error; “requires Rust and Cargo” | Older Pydantic pins pull `pydantic-core` with no **cp313** wheel; pip builds from source | **Pydantic 2.12+** (and matching **pydantic-settings**) so `pydantic-core` installs as a wheel |
| `AssertionError: ... SQLCoreOperations ... TypingOnly ... __static_attributes__` when starting the app | **SQLAlchemy** before ~2.0.31 is incompatible with Python 3.13’s class layout | **SQLAlchemy 2.0.48** (async extra unchanged) |

If you change Python major versions, reinstall:  
`.\venv\Scripts\pip.exe install -r "backend\requirements.txt"` from repo root after activating the venv.

### Run

```powershell
# Terminal 1
cd backend
..\venv\Scripts\python.exe run.py

# Terminal 2
cd frontend
npm run dev
```

- Frontend: http://localhost:3000
- Backend: http://localhost:8000

## Project Structure

```text
vc-agent/
├── backend/
├── frontend/
├── data/                 # runtime data (gitignored)
├── docs/                 # technical documentation
└── doc/                  # product docs (PRD)
```

## Documentation

Use `docs/README.md` as the documentation index.

- `docs/ARCHITECTURE.md`
- `docs/DEVELOPER_GUIDE.md`
- `docs/API_REFERENCE.md`
- `doc/MVP-prd.md`

## Core Architecture Principles

1. No loss: persist inbound content to parking lot first.
2. Canonical downstream: portfolio/resource APIs operate on canonical records.
3. Resolver isolation: matching logic stays in `EntityResolver`.
4. Storage abstraction: business logic depends on `StorageAdapter`.

## Current Status

MVP is functional for local use and designed for future extension (cloud storage, smarter matching, artifact generation, and multi-tenant hardening).
