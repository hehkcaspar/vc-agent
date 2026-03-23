# VC Portfolio Manager

VC Portfolio Manager is a web application for managing portfolio companies as canonical entities, with durable ingestion via a parking-lot workflow.

## What It Does

- Manage portfolio entities (create, edit, archive).
- Ingest files, text, and URLs without data loss.
- Resolve inbound content to existing/new entities.
- Browse entity resources and artifacts in a responsive UI.
- Support light/dark themes and adaptive layout for desktop/laptop/mobile.

## Tech Stack

- Backend: FastAPI, SQLAlchemy (async), SQLite
- Frontend: React + TypeScript + Vite + SWR
- Storage: Local filesystem via `StorageAdapter`

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+

### Setup

```powershell
# From repo root
python -m venv venv
.\venv\Scripts\Activate.ps1
.\venv\Scripts\pip.exe install -r "backend/requirements.txt"

cd frontend
npm install
cd ..
```

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
