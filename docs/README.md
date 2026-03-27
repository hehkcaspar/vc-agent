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
- If **runtime behavior or data flow** change, update `ARCHITECTURE.md` (chat tools, artifact policy, frontend refresh).
- If **setup, tests, or dev workflow** change, update `DEVELOPER_GUIDE.md`.
- If **LangSmith** changes, update `TRACING.md`.
- If **product-level** capabilities change, update `../README.md`.
- **Design history** lives in `docs/plans/`; **`API_REFERENCE.md` + `ARCHITECTURE.md`** are the source of truth for *current* shipped behavior.

## Chat & artifacts (quick map)

| Topic | Where |
|--------|--------|
| Endpoints, bodies, env summary | `API_REFERENCE.md` → Entity chat |
| Deep agent, tools, Option B edits, create-vs-edit policy | `ARCHITECTURE.md` → Portfolio chat |
| Local env, pytest (unit + optional real LLM E2E), manual checklist | `DEVELOPER_GUIDE.md` → Configuration, Testing |
