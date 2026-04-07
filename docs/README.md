# Documentation Index

This folder is the canonical source for technical documentation.

## Start Here

- `../README.md` - Product overview, quick start, and links.
- `DEVELOPER_GUIDE.md` - Local setup, workflow, troubleshooting, and deployment notes.
- `TRACING.md` - LangSmith tracing scope, env setup, verification, and troubleshooting.
- `ARCHITECTURE.md` - Backend/frontend architecture, data flow, storage model, and extension points.
- `API_REFERENCE.md` - Endpoint contracts and data models.

## Plans

- `plans/2026-03-26-vc-portfolio-agent-harness-design-and-plan.md` — Agent harness, multimodel, tools, and artifact editing (canonical).

## Recommended Reading Order

1. `../README.md`
2. `DEVELOPER_GUIDE.md`
3. `TRACING.md`
4. `ARCHITECTURE.md`
5. `API_REFERENCE.md`

## Documentation Ownership

- Keep high-level project narrative in `../README.md`.
- Keep implementation details in the files under `docs/`.
- Prefer linking to existing sections instead of duplicating content.

## Update Checklist

- If **REST / chat contracts** change, update `API_REFERENCE.md` (`202` vs `200`, `use_deep_agent`, job polling, tool names, env vars).
- If **`metadata_json` / pre-process** behavior or response shapes change, update `API_REFERENCE.md` and the **metadata** subsection in `ARCHITECTURE.md` (and the services tree or test list in `DEVELOPER_GUIDE.md` when files move).
- If **runtime behavior or data flow** change, update `ARCHITECTURE.md` (chat tools, artifact policy, frontend refresh).
- If **setup, tests, or dev workflow** change, update `DEVELOPER_GUIDE.md`.
- If **LangSmith** changes, update `TRACING.md`.
- If **product-level** capabilities change, update `../README.md`.
- If **Academic Tracking v2** agent, API, storage, or models change, update `API_REFERENCE.md` (Academic section), `ARCHITECTURE.md` (Academic module section), and `../CLAUDE.md` (Academic Tracking Module). Design spec lives in `../doc/ACADEMIC_TRACKING_V2_DESIGN.md` — do not modify.
- **Design history** lives in `docs/plans/`; **`API_REFERENCE.md` + `ARCHITECTURE.md`** are the source of truth for *current* shipped behavior.

## Workspace & chat (quick map)

| Topic | Where |
|--------|--------|
| Workspace endpoints (tree, files, versioning, trash, ops) | `API_REFERENCE.md` → Workspace |
| Chat endpoints, bodies, env summary | `API_REFERENCE.md` → Entity chat |
| Deep agent, 13 workspace tools, provenance enforcement | `ARCHITECTURE.md` → Portfolio chat |
| Workspace design (full spec) | `../doc/ENTITY_WORKSPACE_DESIGN.md` |
| Local env, pytest (unit + optional real LLM E2E), manual checklist | `DEVELOPER_GUIDE.md` → Configuration, Testing |

## Academic Tracking v2 (quick map)

| Topic | Where |
|--------|--------|
| Full design spec (storage, agent, tools, frontend, migration) | `../doc/ACADEMIC_TRACKING_V2_DESIGN.md` |
| Architecture, two-layer storage, agent goals, service modules, SQL tables | `ARCHITECTURE.md` → Academic Tracking Module (v2) |
| 38 API endpoints, request/response schemas, data models | `API_REFERENCE.md` → Academic Tracking (v2) |
| E2E tests, manual testing checklist | `DEVELOPER_GUIDE.md` → Testing |
| Config vars (ACADEMIC_GEMINI_MODEL, etc.) | `backend/.env_sample` |
| Module overview, code structure, design decisions | `../CLAUDE.md` → Academic Tracking Module (v2) |
