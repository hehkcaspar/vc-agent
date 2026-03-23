# VC Portfolio Manager

A premium admin-style webapp for VC firms to manage portfolio companies with an **Entity-Canonical, Parking-Lot Ingestion** architecture.

**Design:** Refined Financial Tech aesthetic with dark mode, glassmorphism, and distinctive typography.

![Design Preview](docs/images/design-preview.png)

## ✨ Features

### Core Functionality
- **🏢 Entity Management** - Create, edit, archive portfolio companies
- **📎 Resource Management** - Upload files, add text notes, and URLs per entity
- **📊 Entity Status** - Active/Archived status with visual indicators
- **🅿️ Parking Lot Ingestion** - Durable staging for all inbound content
- **🔄 Entity Resolution** - Smart matching or manual assignment
- **👁️ Resource Viewer** - Preview PDFs, images, and text files inline

### UI/UX Features
- **🎨 Premium Dark Theme** - Deep navy with indigo and gold accents
- **💫 Glassmorphism Effects** - Backdrop blur and transparency
- **✨ Smooth Animations** - Hover effects, transitions, micro-interactions
- **💾 Tab State Persistence** - View mode, selection preserved across navigation
- **📱 Responsive Layout** - Adapts to different screen sizes

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+

### Setup

```powershell
# 1. Setup Python environment
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt

# 2. Setup frontend
cd frontend
npm install
cd ..

# 3. Start backend (Terminal 1)
cd backend
..\venv\Scripts\python.exe run.py

# 4. Start frontend (Terminal 2)
cd frontend
npm run dev
```

Open http://localhost:3000

## 📖 Documentation

| Document | Description |
|----------|-------------|
| [API Reference](docs/API_REFERENCE.md) | Complete API documentation |
| [Architecture](docs/ARCHITECTURE.md) | System design and data flow |
| [Developer Guide](docs/DEVELOPER_GUIDE.md) | Setup, development, and deployment |
| [Gap Analysis](docs/GAP_ANALYSIS.md) | Comparison with PRD requirements |
| [Design Doc](docs/plans/2025-02-27-vc-portfolio-mvp-design.md) | Original design specification |

## 🏗️ Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Frontend      │────▶│   Backend API    │────▶│   Storage       │
│   (React)       │◄────│   (FastAPI)      │◄────│   (Local FS)    │
│   Dark Theme    │     │                  │     │                 │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌──────────────────┐
                        │   SQLite DB      │
                        └──────────────────┘
```

### Key Components
- **ParkingLotManager** - Durable staging for all uploads
- **EntityResolver** - Matches uploads to existing entities
- **ResourceMaterializer** - Safely moves files (Copy→Verify→Write→Delete)
- **StorageAdapter** - Abstract interface (local FS now, cloud later)

### Design System
- **Display Font:** Playfair Display (elegant serif)
- **Body Font:** Plus Jakarta Sans (modern geometric)
- **Mono Font:** JetBrains Mono (code/previews)
- **Primary Colors:** Deep navy `#0a0a0f`, Indigo `#6366f1`, Gold `#fbbf24`

## 💾 Data Flow

### Creating an Entity
1. User fills form with name, website, optional content
2. Content saved to Parking Lot immediately
3. EntityResolver attempts name match
4. Auto-creates entity and materializes resources
5. User sees new entity with all content

### Uploading to Existing Entity
1. Open entity detail
2. Click "+ Add" → choose File, Text, or URL
3. Content saved to Parking Lot
4. Auto-attached to current entity
5. Resources list updates immediately

### Editing Entity Metadata
1. Hover over entity card/row
2. Click ✏️ (edit) button
3. Edit name, website, or status
4. Auto-formats website URL (prepends https:// if missing)

### Archiving Entities
1. Hover over entity card/row
2. Click 📥 (archive) or 📂 (unarchive) button
3. Entity status toggles
4. Visual indicators show archived state

## 📁 File Storage

```
data/entities/
├── 00000/                          # Parking lot pseudo-entity
│   └── parkinglot/{ingest_id}/
│       ├── files/                  # Raw uploads
│       └── payload/                # metadata, text, urls
│
└── {entity_uuid}/                  # Real entities
    ├── resources/{resource_id}/    # Canonical resources
    └── artifacts/{artifact_id}/    # Versioned markdown
```

## 🛠️ Tech Stack

**Backend:**
- Python 3.11+
- FastAPI
- SQLAlchemy (async) + SQLite
- Local filesystem storage

**Frontend:**
- React 18 + TypeScript
- Vite
- SWR (data fetching)
- CSS Variables (design system)

## 📋 API Highlights

```bash
# Ingest content (files, text, URLs)
POST /ingest/resources

# Manage entities
GET    /entities
POST   /entities
PATCH  /entities/{id}    # Update name, website, status
DELETE /entities/{id}

# Manage parking lot
GET    /parkinglot
POST   /parkinglot/{id}/resolve

# Browse resources/artifacts
GET /entities/{id}/resources
GET /entities/{id}/artifacts
```

See [API Reference](docs/API_REFERENCE.md) for complete documentation.

## ✅ MVP Acceptance Criteria

| Criteria | Status |
|----------|--------|
| Tab state preserved | ✅ |
| No upload loss | ✅ |
| Canonical resources only | ✅ |
| Resolution handshake works | ✅ |
| Filesystem correctness | ✅ |
| Entity detail separation | ✅ |
| Local-only MVP | ✅ |
| Resource viewer | ✅ |
| Entity edit/archive | ✅ |
| Premium UI/UX | ✅ |

**Result: 10/10 Pass** ✅

## 🎯 Recent Updates

### UI/UX Redesign
- Complete dark theme with glassmorphism
- Distinctive typography (Playfair Display + Plus Jakarta Sans)
- Smooth animations and micro-interactions
- Gold accent colors for archived states

### New Features
- **Entity Edit Modal** - Edit name, website, status
- **Archive/Unarchive** - Toggle entity status with visual indicators
- **Resource Types** - Add files, text notes, or URLs from entity detail
- **Resource Viewer** - Preview PDFs, images, and text inline
- **URL Auto-formatting** - Website field auto-prepends https://
- **Schema-driven Forms** - Centralized metadata configuration

### Improvements
- Consistent modal design across all components
- Better PDF viewer (no double scrollbars)
- Archive buttons alongside edit buttons
- Visual distinction for archived entities

## 🔮 Future Extensions

- **Email/IM ingestion** - Add new `source` values
- **Smarter matching** - Update `EntityResolver` only
- **Cloud storage** - Swap `StorageAdapter` implementation
- **Artifact generation** - Write markdown directly
- **Search/Filtering** - Add search endpoints
- **Multi-tenancy** - Add `tenant_id` to tables

## 📂 Project Structure

```
vc-agent/
├── backend/              # FastAPI application
│   ├── app/
│   │   ├── routers/      # API endpoints
│   │   └── services/     # Business logic
│   └── requirements.txt
├── frontend/             # React application
│   └── src/
│       ├── components/   # UI components
│       ├── hooks/        # Data hooks
│       ├── services/     # API client
│       ├── store/        # State management
│       └── styles/       # Design system
├── data/                 # Runtime data (gitignored)
└── docs/                 # Documentation
```

## 🤝 Contributing

1. Read the [Developer Guide](docs/DEVELOPER_GUIDE.md)
2. Check [Architecture](docs/ARCHITECTURE.md) for design patterns
3. Follow the existing code style
4. Update documentation

## 📄 License

MIT
