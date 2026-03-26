# Documentation Index

This folder is the canonical source for technical documentation.

## Start Here

- `../README.md` - Product overview, quick start, and links.
- `DEVELOPER_GUIDE.md` - Local setup, workflow, troubleshooting, and deployment notes.
- `ARCHITECTURE.md` - Backend/frontend architecture, data flow, storage model, and extension points.
- `API_REFERENCE.md` - Endpoint contracts and data models.

## Plans

- `plans/2026-03-26-vc-portfolio-agent-harness-design-and-plan.md` — Agent harness, multimodel, tools, and artifact editing (canonical).

## Recommended Reading Order

1. `../README.md`
2. `ARCHITECTURE.md`
3. `DEVELOPER_GUIDE.md`
4. `API_REFERENCE.md`

## Documentation Ownership

- Keep high-level project narrative in `../README.md`.
- Keep implementation details in the files under `docs/`.
- Prefer linking to existing sections instead of duplicating content.

## Update Checklist

- If API behavior changes, update `API_REFERENCE.md` (including chat `202` vs `200`, `use_deep_agent`, job polling).
- If system behavior/data flow changes, update `ARCHITECTURE.md`.
- If setup/workflow changes, update `DEVELOPER_GUIDE.md`.
- If user-facing capabilities change, update `../README.md`.
- Historical design notes live in `plans/`; treat `API_REFERENCE.md` + `ARCHITECTURE.md` as the **source of truth** for current behavior.
