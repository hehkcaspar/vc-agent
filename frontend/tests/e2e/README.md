# Playwright E2E tests

Native Python Playwright scripts that drive the running app at
`localhost:3000` (frontend) + `localhost:8000` (backend). Run by hand
during local dev and as part of the pre-deploy checklist for
`gc-deploy` (see `docs/DEPLOYMENT_GCP.md`).

## Setup

The Python deps are already in `backend/requirements.txt` (Playwright
+ Chromium are installed via the project venv).

```sh
# Make sure both servers are up
cd backend && source ../venv/bin/activate && python run.py &   # :8000
cd frontend && npm run dev                                     # :3000
```

## Run

```sh
# From repo root
venv/bin/python frontend/tests/e2e/session_changes.py
```

Screenshots land in `/tmp/e2e_screens/`. Console output reports per-
surface checks (✓ / ❌) and any JS console errors / warnings.

## What's covered

`session_changes.py` exercises every user-facing surface touched by
the 2026-05-02 batch:

| Surface | Check |
|---|---|
| Login gate (`APP_PASSWORD`) | Password flow works |
| Portfolio list | Entities render, no JS errors |
| Entity Facts tab — Team Info icon | Tooltip text refined |
| Entity Facts tab — FactProvenanceBadge | Long-form source descriptions |
| Entity Facts tab — Co-investors section | Linked chips when `co_investor_details` present |
| News tab | Items render, "unverified" pills on `url_status != verified` |
| Initial Screening tab — Recompose button | Present + correct tooltip |
| EntityEditModal | All 6 sections (Deal stage, Identity, Founders, Key team, Team size, Positions); 6+ Identity fields |
| JS console | 0 errors |

## When to update

Add a new test surface when:
- A user-facing component is added or restructured
- A previous deploy hit a regression that was caught here (regression test)
- A new prompt / pipeline change has UI side-effects

Keep individual checks **assertion-soft** (log `present`/`missing`
rather than `assert`) so a single missing element doesn't abort the
whole run — the goal is broad surface scanning, not per-surface
gating.
