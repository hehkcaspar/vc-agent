# Backlog

Future-development items that are known but not yet scheduled. Newest first.

**Current priority order (2026-04-22 audit):**
1. ~~`test_chat_api.py` — 3 red tests~~ — **✅ resolved 2026-04-22**: all 3 were test-code drift against evolved production (use_deep_agent→react, CHAT_USE_DEEP_AGENT→CHAT_DEFAULT_AGENT_MODE, preset session-id requirement). Tests realigned; full suite 250/250 (excluding pre-existing `test_three_paths_e2e.py` fixture bug, see below).
2. ~~URL routing — restore browser back/forward + survive refresh~~ — **✅ resolved 2026-04-22**: 6-commit rollout wired BrowserRouter + detail routes (`/portfolio/entities/:id/:subTab?`, `/academic/scholars/:id/:subTab?`) + search-param filters + modal routes (`/portfolio/new`, `/portfolio/parking-lot`, `/portfolio/entities/:id/edit`, `/academic/new`) + settings section path param + `?chat=<sid>` for session reattach + 404 catch-all. Job reattach already worked via backend `active_job_id` — no new endpoint needed. Two polish items deferred (see below).
3. Backfill grounding-sourced URLs — **medium** (live scholar data, 58% broken)
4. `test_three_paths_e2e.py` missing `r` fixture — **medium** (28 collection errors hide pre-existing e2e coverage)
5. URL routing follow-ups (preview deep-link + scroll restoration) — **low** (see below)
6. `early_break` Tasks-view aggregation — low (quick obs win)
7. `_MAX_PAPERS` config override — low
8. Legal config → JSON-seed migration — low (ergonomics)
9. `docs/ARCHITECTURE.md` v2 rewrite — low (doc-drift)
10. Dim-seed collapse — **n/a on main**; move to `gc-deploy` branch's own backlog (`backend/app/defaults/` does not exist on `main`)

---

## URL routing follow-ups — preview deep-link + scroll restoration

**Status:** not started · **Priority:** low · **Filed:** 2026-04-22 (deferred from main URL-routing PR)

### Problem
The URL-routing refactor (shipped 2026-04-22) deferred two polish items flagged in its plan:

1. **`?preview=<nodeId>` file-preview deep link** — `EntityDetail.previewNode` is a `WorkspaceTreeNode` object (not just an ID) that's mutated from ~6 call sites (click handler, discrepancy-panel open-in-preview, fact-provenance callback, etc.). Making it URL-controlled requires either (a) derive from URL on every render with a tree lookup or (b) sync via useEffect. Both add complexity beyond the routing PR's scope.

2. **Scroll restoration** — React Router v6's `<ScrollRestoration>` requires a data router (`createBrowserRouter`), but the routing PR uses declarative `<BrowserRouter>`. Additionally, PortfolioTab/AcademicTab use container-level overflow scroll (not window scroll), which `<ScrollRestoration>` doesn't handle. Would need a custom scroll manager keyed by route.

### Fix
- **Preview deep-link**: add `?preview=<nodeId>` search param in EntityDetail. Derive `previewNode` via `tree.find(n => n.id === previewId)`. All 6 `setPreviewNode(x)` sites become `setSearchParams({ preview: x.id })`. Handle the case where the target node is no longer in the tree (stale link) by gracefully falling back to no-preview.
- **Scroll restoration**: two-part. (1) Migrate to `createBrowserRouter` + `<ScrollRestoration>` for window scroll. (2) For container scroll, extend TabContext to key `scrollPosition` on route pathname instead of tabId, save on route-change, restore on matching-route mount.

### Effort
Each small-medium. Preview deep-link ~1h if done cleanly. Scroll restoration ~2-3h (bigger because of the router migration).

---

## `test_three_paths_e2e.py` — 28 collection errors from missing `r` fixture

**Status:** not started · **Priority:** medium (test-coverage gap) · **Filed:** 2026-04-22

### Problem
All 28 tests in `backend/tests/test_three_paths_e2e.py` error at collection with `fixture 'r' not found`. The tests reference `r` (a `TestResult` dataclass) as if it were a pytest fixture, but no `@pytest.fixture def r()` is defined. Surfaced while verifying the chat_api test fixes (2026-04-22) — the full backend suite shows `250 passed, 28 errors`, all errors concentrated here.

This is orthogonal to the chat_api fixes; the file likely broke in an earlier refactor (possibly when `TestResult` was extracted to a class) and nobody noticed because the 28 "errors" look superficially like they could be env-gated e2e skips.

### Fix
Either define a `pytest.fixture` that yields a fresh `TestResult` instance per test, or refactor the pattern to construct `r` inline in each test body. The dataclass is likely meant to be a per-test state holder; fixture is cleaner.

### Effort
Small. One fixture definition, ~10 lines.

---

## Tasks-view aggregation of `early_break` stat

**Status:** not started · **Priority:** low (observability) · **Filed:** 2026-04-21

### Problem
`google_scholar_papers.run()` writes `"early_break": true/false` into its snapshot detail whenever incremental pagination stopped early because the current page's GS ids were all in the ledger. The Tasks view (`GET /academic/continuous-tasks`) aggregates snapshots into per-task health (7d runs, success rate, avg duration) but doesn't surface how often early-break fires. Operators can't see "we saved N SerpAPI calls this week" without greping logs.

### Fix
Extend the per-task health aggregator in the Continuous Tasks endpoint to count `snapshot.detail.early_break == true` across the 7d window, expose as `early_break_saves_7d` alongside the existing health fields. Frontend tasks tile shows it as a muted sub-line ("early-break: 12/14 runs this week"). Purely observability — no runtime behaviour change.

### Effort
Small. One aggregator field; one frontend line.

---

## Lift `_MAX_PAPERS` ceiling for 1000+ paper scholars

**Status:** not started · **Priority:** low · **Filed:** 2026-04-21

### Problem
Both `google_scholar_papers._MAX_PAPERS` and `semantic_scholar_papers._DEFAULT_LIMIT` cap at 500 papers per run. Long-tail scholars (Bengio-scale, 1000+ papers) are silently truncated — older papers that sit past page 5 never reach `papers.json`. Not a pressing issue for the current 15 tracked scholars, but will surface as the roster grows.

### Fix
Make the cap per-scholar overridable from `continuous_tasks_seed.json` (new optional `max_papers_override` field). Default stays 500; scholars flagged as high-volume get 2000. Both sources read the same override.

### Effort
Small. Two constants become config lookups; one seed-schema field.

---

## Backfill grounding-sourced URLs into existing scholar dossiers

**Status:** not started · **Priority:** medium · **Filed:** 2026-04-21

### Problem
The 2026-04-21 grounded-search URL fix only affects new writes. Existing records in `data/scholars/*/news.jsonl`, `patents.json`, `startups.json`, `red_flags.jsonl` still carry ~58% broken LLM-fabricated URLs (audit report at `data/audits/source_urls_report.json` — 221 hard 404s + 16 DNS failures out of 539 URLs).

### Fix
One-shot CLI that re-runs the now-fixed `grounded_search_json` on each record's title (tight "find the canonical URL for this story about <scholar>" prompt), swaps in the grounding URL when one comes back, falls through to `llm_validated` / `google_search` otherwise. Respects the same 3-tier semantics used on fresh writes. Run scholar-by-scholar with pacing so we don't burn the Gemini quota.

### Effort
Small. Reuses the fixed helper. Main work is the CLI wrapper + idempotent re-write of each JSONL/JSON file (preserve ids, only overwrite the URL fields + `_url_source`).

---

## `docs/ARCHITECTURE.md` — full v1→v2 academic-module rewrite is outdated

**Status:** not started · **Priority:** low (doc-drift) · **Filed:** 2026-04-20

### Problem
The academic-module section of `docs/ARCHITECTURE.md` (roughly the "Agent Goals" + "Backend Service Modules" + "Key Design Decisions" subsections, ~lines 720–770) still describes the pre-Apr-13 v1 architecture:

- References `services/academic/scholar_agent.py` / `scholar_prompts.py` / `domain_tools.py` / `build_scholar_tools(scholar_id)` closure pattern — all **removed** in commit `f27ca6a` (Academic Tracking v2 rewrite, Apr 13).
- Describes the "Deep Agents harness" + 12-tool closure model — v2 uses `google-genai` Interactions API with function tools (`services/academic/scholar_chat.py`) and a 3-layer fact-store / sources / dim-eval architecture (`dim_runner.py`, `narrative_synthesizer.py`, `phase_classifier.py`, `sources/*`).
- "Agent Goals" table lists v1 endpoints (`POST /scholars/{id}/evaluate` with a monolithic Deep Agents run) that no longer exist as a single-shot agent invocation.
- Design decisions 4 (`@tool` docstring rule), 5 (closure-bound tools), and parts of 9 (Gemini content-block normalisation via `_extract_text`) describe v1 code paths.

Today's update (2026-04-20) fixed the dim-specific and seed-pattern lines (items 744, 764) but left the surrounding v1 text intact because rewriting the whole section was out of scope.

### Fix
Full section rewrite. Mirror the structure already in `CLAUDE.md`'s "Academic Tracking v2" block (the canonical up-to-date description) plus the cross-references already in `docs/design/SCHOLAR_EVALUATION_FRAMEWORK.md`. Delete or re-target every v1 reference.

Scope of rewrite (estimate):
- Redo "Agent Goals" table for v2 dispatch model (heartbeat tick → sources → dim_runner → narrative_synthesizer).
- Rewrite "Backend Service Modules" table to match actual files under `services/academic/`.
- Delete v1-only decision entries; keep decisions that still hold (file-backed dims, stuck-evaluating recovery, event-vs-discovery date).

### Effort
Medium. One focused doc pass, cross-checked against CLAUDE.md + `docs/design/SCHOLAR_EVALUATION_FRAMEWORK.md` (both already accurate). No code changes.

---

## URL routing — restore browser back/forward + survive refresh

**Status:** ✅ **resolved 2026-04-22** · **Priority:** high (UX-blocking) · **Filed:** 2026-04-19

### Resolution (2026-04-22)
Six-commit rollout in one session:

1. **Router shell** — `<BrowserRouter>` in `main.tsx`; `Layout` reads active tab from `useLocation()`; `/`, `/portfolio`, `/academic`, `/settings` wired.
2. **Detail routes** — `/portfolio/entities/:entityId/:subTab?` + `/academic/scholars/:scholarId/:subTab?` via new `EntityDetailRoute` / `ScholarDetailRoute` wrappers. `EntityDetail` / `ScholarDetail` made controlled (`contentTab` + `onContentTabChange` props replace internal `useState`).
3. **Search-param filters** — PortfolioTab / AcademicTab filters (`view`, `stage`, `status`) now live in URL via `useSearchParams()`. Default values omitted from URL (clean URLs). SettingsPage section = path param (`/settings/:section`).
4. **Modal routes** — `/portfolio/new`, `/portfolio/parking-lot`, `/portfolio/entities/:id/edit`, `/academic/new` promoted to routes. Delete confirms, upload modal, list-row quick-edit, digest/feed/activity-log dialogs stay local.
5. **Session preservation** — `?chat=<sessionId>` in entity detail URL. Job reattach already worked via backend `active_job_id` on session-detail GET; no new endpoint needed. `localStorage` approach originally proposed was unnecessary.
6. **404 catch-all** — `*` route → `NotFound` component with "Back to portfolio" link.

Verified each commit with Playwright headed. `npm run build` clean at every step. Two polish items deferred (see "URL routing follow-ups" above).

---

## URL routing — ORIGINAL BACKLOG ENTRY (kept below for historical reference)
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

## Collapse redundant dim-seed mechanism on `gc-deploy`

**Status:** not started · **Scope:** `gc-deploy` branch only — not applicable on `main` (`backend/app/defaults/` does not exist here) · **Priority:** low (cleanup after today's fix) · **Filed:** 2026-04-19

### Problem
After commit `e14e69a` on `gc-deploy`, the 4 MECE dim prompts ship via **two parallel mechanisms**:

- `backend/app/defaults/dimensions.json` (19,085 B, `ensure_ascii=True` escaping) copied to `/mnt/gcs/config/` by `ensure_universal_configs_seeded()` on lifespan startup
- `backend/app/services/academic/dimensions_seed.json` (19,368 B, raw UTF-8) loaded at module import by `_load_seed()`, written by `read_dimensions()` when the runtime file is missing

Both carry semantically identical content (diff is pure JSON escaping). On fresh Cloud Run boot, lifespan wins the race and seeds from `defaults/`; on every other path (local dev, tests, a bucket-deleted re-seed like today's prod fix) the in-package seed takes over. Belt-and-suspenders, but risks drift if someone edits one and not the other, and diverges from `main` which has only the in-package seed.

### Fix
Delete `backend/app/defaults/dimensions.json` and drop `"dimensions.json"` from `config_seeding._FLAT_FILES`. The in-package `_load_seed()` + `read_dimensions()` writeback fully covers the fresh-boot case (validated in prod after `gsutil rm`). `continuous_tasks` already uses the in-package seed + sparse overrides (shipped 2026-04-22), so with `dimensions.json` collapsed too, `config_seeding.py` + the whole `backend/app/defaults/` tree can go.

While collapsing, normalise JSON encoding (pick one of `ensure_ascii=True` vs raw UTF-8 — `write_dimensions()` currently uses default, my seed was generated raw — minor, cosmetic).

### Effort
Very low. No longer blocked — `continuous_tasks` has already migrated to the seed + overrides architecture.

---

## `test_chat_api.py` — 3 red tests on pristine `main`

**Status:** ✅ **resolved 2026-04-22** · **Priority:** high (test-suite red, masks regressions) · **Filed:** 2026-04-19 · **Re-audited:** 2026-04-22

### Resolution (2026-04-22)
Three separate test-code drifts, all fixed by realigning tests to current production semantics (no product changes):

- `uses_deep_agent_when_enabled` — test monkeypatched retired `CHAT_USE_DEEP_AGENT` flag; swapped to `CHAT_DEFAULT_AGENT_MODE="react"` and patched `create_react_portfolio_agent` / `invoke_react_portfolio_agent`.
- `override_on_uses_harness` — test patched `create_portfolio_agent`, but `use_deep_agent=True` now maps to react mode; swapped patch targets to the react factories.
- `extract_info_preset_creates_json_deliverable` — test sent `{node_ids: []}` with no `session_id`; preset endpoint now requires one (extract_info is force-routed to react). Split into `test_extract_info_preset_requires_session_id` (400 dispatch check) + end-to-end test that stubs the react harness and verifies the salvage path writes `Company Profile.json`.

Full backend suite passes 250/250 after the fix (excluding pre-existing `test_three_paths_e2e.py` fixture bug tracked separately above).

---

### Problem
Confirmed red on current `main` (2026-04-22). The original filing described all three as "assert 202, got 200" — that's wrong. Actual failure shapes differ and likely represent **three separate regressions**, not one dispatch-gate issue:

| Test | Line | Failure | Inferred root |
|---|---|---|---|
| `test_post_message_deep_agent_override_on_uses_harness` | 120 | job status ends `failed` (not `succeeded`) — dispatch works, harness errors mid-run | Harness-side exception — likely tool schema / import / missing fixture |
| `test_post_message_uses_deep_agent_when_enabled` | 146 | `assert r.status_code == 202` but got **200** — sync one-shot dispatched when deep_agent enabled | Config-gate or flag regression in `routers/chat.py` dispatch branch |
| `test_extract_info_preset_creates_json_deliverable` | 278 | `assert r.status_code == 200` but got **400 Bad Request** — request validation fails at the preset endpoint | Schema drift after the facts/signals split (preset payload now fails Pydantic) |

Other 119/122 tests pass, so scope is isolated to these three code paths.

### Investigation order
1. `test_post_message_uses_deep_agent_when_enabled` first — it's the cleanest dispatch-gate failure; read `routers/chat.py` 200-vs-202 branch and the env/default it consults.
2. `test_extract_info_preset_creates_json_deliverable` — read response body for the validation detail; cross-check against the preset request schema post facts/signals split.
3. `test_post_message_deep_agent_override_on_uses_harness` last — surface the real `error_message` from the job record (test currently only checks `status`); may unblock itself once #1/#2 are understood.

### Effort
Low-medium per test, done serially. Probably 3 small fixes, not one architectural change.

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
