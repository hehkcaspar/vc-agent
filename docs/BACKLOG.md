# Backlog

Future-development items that are known but not yet scheduled. Newest first.

---

## URL routing — restore browser back/forward + survive refresh

**Status:** not started · **Priority:** high (UX-blocking) · **Filed:** 2026-04-19

### Problem
The app is a pure React state-machine with **no URL routing**. Every navigation — top tab, entity selection, sub-tab, scholar, settings section, modals — is held in component-local `useState`. Concretely:

- Browser **back/forward buttons do nothing** (single URL the entire session).
- **Refresh dumps the user to the portfolio root**, losing the open entity/scholar, sub-tab, file preview, chat session, expanded folders, and any open modal.
- **Cannot bookmark or share** a deep link to "Entity X → Facts tab" or "Scholar Y → Evaluation tab".
- **In-flight agent/eval jobs** poll in-memory only — refresh kills the polling UI even though the backend job keeps running. User has no way to reattach.

### Current state (snapshot 2026-04-19)
- `react-router-dom@6.22.0` is **already in `frontend/package.json`** (line 19) but **not imported anywhere** — `frontend/src/main.tsx` renders `<App />` directly with no `<BrowserRouter>`.
- Partial persistence already exists and should be preserved/migrated:
  - `localStorage`: theme (`Layout.tsx:20,29`), agent-mode toggle (`EntityConversation.tsx:46–59`), chat model profile (`ChatModelProfileContext.tsx:11–34`).
  - `sessionStorage` via `TabContext` (`store/TabContext.tsx:12,48–50`): portfolio stage filter, status filter, view mode, `selectedEntityId`. Survives tab switches, dies on refresh.
- Stage filter normalisation already migrates legacy values (`PortfolioTab.tsx::normaliseStageFilter`) — the same pattern can absorb URL → state migrations.

### State that must move into the URL
Grouped by surface, with current `useState` site for each:

**Top nav**
- Active tab `portfolio | academic | settings` — `Layout.tsx:13`
- Settings section `funds | legal-checklist | legal-templates | continuous-tasks | dimensions | ranking-presets | appearance | about` — `SettingsPage.tsx:82`

**Portfolio**
- Stage filter, status filter, view mode (list/grid), search query — `PortfolioTab.tsx:73–75` (today in sessionStorage)
- Selected entity ID — `PortfolioTab.tsx:76` → should become `/portfolio/entities/:entityId`
- Entity sub-tab `workroom | facts | screening_v1 | screening_v2 | news` — `EntityDetail.tsx:146–148`
- Preview node ID (which file is open in the side panel) — `EntityDetail.tsx:143`
- Chat session ID — `EntityConversation.tsx:89` (already a stable ID, just needs to be in URL)

**Academic**
- View mode `list | ranking | tasks` — `AcademicTab.tsx:64`
- Status filter — `AcademicTab.tsx:65`
- Selected scholar ID — `AcademicTab.tsx:55` → `/academic/scholars/:scholarId`
- Scholar sub-tab `report | timeline | evaluation | publications | profiles | chat` — `ScholarDetail.tsx:63`

**Modals (decision needed — see open questions)**
- `CreateEntityModal`, `EntityEditModal`, `FileUploadModal`, parking-lot, delete confirms (`PortfolioTab.tsx:77–81`, `EntityDetail.tsx:144`)
- `AddScholarModal`, `EditProfileModal`, feed/digest/activity-log dialogs (`AcademicTab.tsx`)

### Proposed scope (one PR, not piecemeal)
1. Wrap `<App />` in `<BrowserRouter>` in `main.tsx`.
2. Define route tree:
   - `/` → redirect to `/portfolio`
   - `/portfolio` (list) · `/portfolio/entities/:entityId/:subTab?` (detail)
   - `/academic` · `/academic/scholars/:scholarId/:subTab?`
   - `/settings/:section?`
3. Move filters/view modes from sessionStorage to URL search params (`?stage=diligence&view=grid`); keep sessionStorage as a fallback for first-load defaults.
4. Replace `setSelectedEntity(entity)` / `setSelectedScholar(scholar)` patterns with `navigate(...)`; entity/scholar objects are looked up by ID from existing SWR caches — no payload in URL.
5. **Reattach in-flight jobs on mount**: on entity/scholar detail mount, hit a "list active jobs for this entity/scholar" endpoint and resume polling for any `pending|running` job. (Backend already persists job state — this is a frontend-only reconnect.)
6. Audit ~40 `useState` sites flagged in the investigation to decide URL vs local; prefer URL for anything a user might bookmark or share.

### Open questions
- **Modals in URL?** Standard SPA practice is to keep transient confirms (delete, simple edits) as local state, but elevate "create entity" and "edit entity X" to routes (`/portfolio/entities/new`, `/portfolio/entities/:id/edit`) so they're shareable and survive refresh. Decide per-modal.
- **Workspace tree expansion state** — fetched fresh today, no persistence. Probably not worth URL-ifying; consider sessionStorage by entity ID instead.
- **File-preview node ID in URL?** Useful for "look at this exact file in this entity" links, but adds URL churn on every click. Could go in the search params (`?preview=<nodeId>`) so it's optional/dismissable.
- **Chat session ID in URL?** Yes — they're stable backend IDs. `/portfolio/entities/:id/chat/:sessionId` lets the user share a conversation link.

### Out of scope
- No SSR, no migration to Next.js — `react-router-dom` client-side is sufficient for this app.
- No change to backend routes; this is purely a frontend navigation refactor.

### Effort estimate
Medium. The router library is already installed; the work is mechanical (wire routes, replace setState→navigate at ~40 sites) plus one design pass on modals + job reattach. ~1–2 focused days.

---

## `continuous_tasks.json` has no in-code default — fragile on fresh clones + noisy in tests

**Status:** not started · **Priority:** medium · **Filed:** 2026-04-19

### Problem
`backend/app/services/academic/continuous_config.py::load_continuous_tasks()` raises `FileNotFoundError` when `ACADEMIC_CONFIG_DIR/continuous_tasks.json` is missing — no Python fallback. Consequences:

- The academic heartbeat tick fires every ~60 s and logs `heartbeat: continuous_tasks.json failed to load` if the file isn't there.
- Pytest fixtures using a tmpdir as `ACADEMIC_CONFIG_DIR` hit the error every run (visible in `test_chat_api.py` setup logs as of today).
- Production on Cloud Run is only saved by `backend/app/defaults/continuous_tasks.json` (gc-deploy-only) being copied to the bucket by `ensure_universal_configs_seeded()` in lifespan. A fresh-clone `pytest` / `python run.py` on main has no such copy.

Every sibling config has an in-code default: `dimensions` (new seed JSON, 3092789), `legal_review_checklist._default_checklist()`, `legal_templates._default_config()`, `funds` (no default but pure user data). `continuous_tasks` is the outlier.

### Fix
Same JSON-seed-in-package pattern as today's dimensions fix: add `backend/app/services/academic/continuous_tasks_seed.json`, load + Pydantic-validate at import, use as fallback when the runtime file is missing. Drops `continuous_tasks.json` from `config_seeding._FLAT_FILES` (and eventually makes the whole `backend/app/defaults/` + `config_seeding.py` path dead; see next entry).

### Effort
Low. ~20 LOC in `continuous_config.py` + a small seed file.

---

## Collapse redundant dim-seed mechanism on `gc-deploy`

**Status:** not started · **Priority:** low (cleanup after today's fix) · **Filed:** 2026-04-19

### Problem
After commit `e14e69a` on `gc-deploy`, the 4 MECE dim prompts ship via **two parallel mechanisms**:

- `backend/app/defaults/dimensions.json` (19,085 B, `ensure_ascii=True` escaping) copied to `/mnt/gcs/config/` by `ensure_universal_configs_seeded()` on lifespan startup
- `backend/app/services/academic/dimensions_seed.json` (19,368 B, raw UTF-8) loaded at module import by `_load_seed()`, written by `read_dimensions()` when the runtime file is missing

Both carry semantically identical content (diff is pure JSON escaping). On fresh Cloud Run boot, lifespan wins the race and seeds from `defaults/`; on every other path (local dev, tests, a bucket-deleted re-seed like today's prod fix) the in-package seed takes over. Belt-and-suspenders, but risks drift if someone edits one and not the other, and diverges from `main` which has only the in-package seed.

### Fix
Delete `backend/app/defaults/dimensions.json` and drop `"dimensions.json"` from `config_seeding._FLAT_FILES`. The in-package `_load_seed()` + `read_dimensions()` writeback fully covers the fresh-boot case (validated in prod today after `gsutil rm`). Once `continuous_tasks.json` migrates too (see entry above), `config_seeding.py` + the whole `backend/app/defaults/` tree can go.

While collapsing, normalise JSON encoding (pick one of `ensure_ascii=True` vs raw UTF-8 — `write_dimensions()` currently uses default, my seed was generated raw — minor, cosmetic).

### Effort
Very low. Blocked until `continuous_tasks.json` migrates (else `config_seeding.py` still has to exist).

---

## `test_chat_api.py` — 3 deep-agent tests red on pristine `main`

**Status:** not started · **Priority:** medium (test-suite red) · **Filed:** 2026-04-19

### Problem
On pristine `main` (confirmed today via `git stash` → run → stash pop), three tests fail:

- `test_post_message_deep_agent_override_on_uses_harness`
- `test_post_message_uses_deep_agent_when_enabled`
- `test_extract_info_preset_creates_json_deliverable`

All three assert the endpoint returns `202 Accepted` (async dispatch path for `agent_mode=react` / `deep_agent`) but get `200 OK` (sync one-shot path). Pytest setup log also shows `heartbeat: continuous_tasks.json failed to load` each run — may or may not be related (that's the academic heartbeat, not the chat path directly).

Noise in the signal: 119/122 tests pass; only the deep-agent dispatch branch is wrong. None of it is related to today's dimensions work.

### Investigation order
1. Reproduce one failure with `--tb=long` + `caplog` + actual response body — does it 200 because the agent ran synchronously, because of a config gate, or because job enqueue silently fails?
2. Grep `routers/chat.py` for the branch that picks 202 vs 200 and which env/config gates drive it (`CHAT_DEFAULT_AGENT_MODE`?).
3. Fix the `continuous_tasks.json` noise first (previous entry) to eliminate it as a variable.

### Effort
Low-medium. Likely a small dispatch/config fix, not architectural.

---

## Migrate `legal_review_checklist` + `legal_templates` to JSON-seed pattern

**Status:** not started · **Priority:** low (consistency/ergonomics) · **Filed:** 2026-04-19

### Problem
`legal_review_checklist_config.py::_default_checklist()` is a 33 KB Python function that returns a 650-string-literal nested dict. `legal_templates_config.py::_default_config()` is similar (14 KB, 243 literals). Both construct Pydantic models wholly in code.

It works, but iterating on the content is painful:
- Long embedded string literals lose syntax highlighting; every backtick/quote needs escaping.
- A single content tweak produces a huge `git diff` in Python source.
- Reviewers can't skim it as structured data — it reads like code even though it's pure content.

Today's `dimensions_seed.json` pattern solves both: ship JSON next to the reader module, load at import, validate with the existing Pydantic model.

### Fix
- Generate `backend/app/services/legal_review_checklist_seed.json` from `_default_checklist().model_dump()` once.
- Replace the Python default with a `_load_seed()` import that Pydantic-validates the JSON.
- Delete the literal function.
- Same treatment for `legal_templates_config.py`.

### Effort
Medium. Mostly mechanical port, but needs care with Pydantic field aliases / required-vs-optional on round-trip, plus test coverage. Do it only after the `continuous_tasks` migration lands and the pattern has proven out in a second config.
