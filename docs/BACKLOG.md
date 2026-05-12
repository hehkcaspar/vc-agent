# Backlog

Future-development items that are known but not yet scheduled. Newest first.

---

## Audit other `Literal[...]` status fields for cancellation-grade extensibility

**Status:** not started · **Priority:** LOW · **Filed:** 2026-05-11

### Problem
The stop-button rollout (2026-05-11) hit prod with the chat poll endpoint 500'ing because `ChatMessageJobStatus.status` was `Literal["pending","running","succeeded","failed"]` and the runner now writes `"cancelled"`. Pydantic raised on every poll, the frontend's 25-error tolerance kept the UI wedged for ~12 s, then toasted "connection lost." Patched in [HEAD], but two sibling literals carry the same shape and would break the same way the moment we cancel any of those jobs:

- `MetadataPreprocessJobStatus` (`schemas.py:208`)
- `InboxProcessJobStatus` (`schemas.py:246`)

### Fix sketch
Either:
- Mirror the `"cancelled"` extension into both literals proactively (small, cheap), or
- Convert all four status fields to plain `str` with the values documented in a comment — pyenum-style validation isn't paying for itself here.

### Effort
~10 min. Not urgent because neither job has a cancel endpoint today.

### Lesson
When adding a new value to a state machine, grep ALL `Literal[...]` types referencing that state column. Pydantic literals are response-time-validating — even pure additions can break read endpoints on existing rows. Test by manually flipping a row to the new value and hitting the read endpoint, not just by exercising the write path.

---

## `deploy_cloudrun.sh` should fail-closed on empty `APP_PASSWORD`

**Status:** not started · **Priority:** **HIGH** (security regression risk) · **Filed:** 2026-05-02

### Problem
On 2026-05-02 a re-deploy to `vc-agent-backend` left the SPA wide open for ~5 minutes because the deployer didn't export `APP_PASSWORD` and the script's `: "${APP_PASSWORD:=}"` line silently defaulted to empty. `--set-env-vars="…@@APP_PASSWORD=${APP_PASSWORD}"` then OVERWROTE the previously-set value on the Cloud Run revision, and `services/main.py::shared_password_gate` skips the gate when `settings.APP_PASSWORD` is empty:

```python
expected = settings.APP_PASSWORD
if not expected:
    return await call_next(request)   # ← gate disabled
```

Caught immediately by `curl /entities` returning 200 without auth header; re-deployed with `APP_PASSWORD='TH2026+'` exported. But the failure mode is silent — a rushed deploy could ship the wide-open revision and only get noticed when someone external pokes the API.

### Fix
In `backend/deploy_cloudrun.sh`, replace the silent default with an explicit guard. Two acceptable shapes:

**Option A — fail-closed with a clear message:**
```bash
if [[ -z "${APP_PASSWORD:-}" ]]; then
  echo "ERROR: APP_PASSWORD is not set. Re-run with APP_PASSWORD=... bash backend/deploy_cloudrun.sh" >&2
  echo "       To deliberately deploy WITHOUT a password gate (local-only / staging), pass APP_PASSWORD=DISABLE_GATE." >&2
  exit 2
fi
if [[ "${APP_PASSWORD}" == "DISABLE_GATE" ]]; then
  APP_PASSWORD=""   # explicit opt-out for staging/dev
fi
```

**Option B — move APP_PASSWORD to Secret Manager.** Already on the backlog (`APP_PASSWORD → Secret Manager hardening`); reading from a secret eliminates the "did the deployer remember to export it?" failure mode entirely. Combine with Option A's guard so a missing secret reference is caught at deploy time instead of post-deploy.

### Effort
~15 minutes for Option A. Option B is the bigger structural fix (already filed).

---

## Tier 2: extract `services/grounded_extraction/` shared module

**Status:** not started · **Priority:** medium · **Filed:** 2026-05-02

### Problem
The verify→triage→url_fallback pipeline exists in two places:
- `services/academic/refinement.py` — async fire-and-forget, scholar-keyed via `scholar_id`, writes through `services/academic/file_utils.py`.
- `services/portfolio/sources/news_web.py::run` — synchronous inline (added 2026-05-01), entity-keyed via `entity_id`, writes through `services/portfolio/file_utils.py`. Imports `verify_item` and `triage` directly out of `services/academic/`.

Two consequences:
1. **Drift risk**: any verify-prompt or triage-rule change has to be made twice. Different storage / lock / context-builder wiring on each side. Fixing the subject-identity verdict in 2026-05-02 (Tier 1) fortunately needed only a prompt edit; future structural changes won't be that lucky.
2. **Cross-module reach**: portfolio code does `from ...academic.item_triage import triage` — wrong direction; portfolio should not import from academic internals.

### Fix sketch
New top-level `backend/app/services/grounded_extraction/`:

```
grounded_extraction/
    __init__.py
    fetcher.py            # grounded_search_json (no_grounding filter, byte→char, search-meta capture)
    item_verification.py  # moved from academic/
    item_triage.py        # moved from academic/
    url_fallback.py       # moved from academic/
    refinement.py         # entity-agnostic verify+triage+url_fallback orchestrator
    storage.py            # Protocol[append, rewrite, read] for ledger writers
    contexts.py           # Protocol[build_context(subject_id) -> str]
```

Then:
- `services/academic/refinement.py` becomes a thin shim wiring scholar `file_utils` + `build_scholar_context` into `grounded_extraction.refinement.refine`.
- `services/portfolio/sources/news_web.py` calls `grounded_extraction.refinement.refine` with portfolio writers + `_build_verify_context` instead of inlining the loop.

Both paths converge on a single async fire-and-forget pipeline. Portfolio gets fire-and-forget for free (current sync inline → 30-60s wait on REFRESH NOW becomes ~5s + background completion).

### Effort
~1 day. The risk is keeping academic's existing tests green during the move (a lot of imports change paths). Plan: one PR for module move + import-shim, one PR for entity-agnostic refactor.

---

## Tier 3: retry-on-zero-grounded in `grounded_search_json`

**Status:** not started · **Priority:** low · **Filed:** 2026-05-02

### Problem
Empirically observed (2026-05-02 portfolio-wide refresh): on 4 of 9 entities, the model returned items with ZERO grounding chunks across the entire response — meaning Gemini decided not to use Google Search at all, despite the prompt saying "You MUST use Google Search". Our `no_grounding` filter correctly drops these as untrustable, but the user sees an empty news tab without knowing whether (a) there genuinely is no news, or (b) the model arbitrarily skipped search this turn.

### Fix sketch
In `grounded_search_json`, after the `no_grounding` filter, if EVERY item was dropped:
- Retry once with a stricter prompt (prepend `"You MUST call Google Search at least once for each subject before answering. If you cannot find search results, return [] — do not answer from memory."`).
- If the retry still produces 0 grounded items, return `[]` with a structured warning that the caller can persist on the snapshot detail (`detail.no_search_executed: true`) so the UI can distinguish "no results" from "search failed".

### Effort
~2 hours. The async-retry plumbing is small; the prompt-stiffening is one block.

### Why low priority
Tier 1 (subject-identity check) is the higher-leverage fix for the data-quality complaints we have. Tier 3 only matters when the user wants to DEBUG why a refresh returned nothing. Defer until Tier 1 + Tier 2 land and we have data on whether silent zero-fetches are a real recurring problem.

---

## Render Google `searchEntryPoint` widget — TOS compliance for grounded responses

**Status:** not started · **Priority:** medium (TOS compliance) · **Filed:** 2026-05-01

### Problem
Per the Gemini grounding API docs, `groundingMetadata.searchEntryPoint.renderedContent` is the HTML+CSS for a "Search Suggestions" widget that is **required** to be displayed when grounding was used. Quote from the docs:

> `searchEntryPoint`: Contains the HTML and CSS to render the required Search Suggestions. Full usage requirements are detailed in the [Terms of Service](https://ai.google.dev/gemini-api/terms#grounding-with-google-search).

Today (2026-05-01) `services/academic/llm_client.py::_extract_grounding` captures `search_entry_html` into the grounding dict, but no caller persists or renders it. The portfolio News tab + scholar dossiers display per-item URLs as attribution (`item.url`, `item.source`) but the Google-mandated Search Suggestions widget is missing.

### Fix sketch
- Persist `search_entry_html` per refresh (e.g., on the `last_snapshot` for news_web) so the frontend can render it once per refresh instead of per item.
- Frontend News tab: render the widget at the bottom of the feed when present (sandboxed `<iframe srcdoc=...>` to isolate Google's CSS from our app's).
- Same for scholar dossier news.
- Decide whether IS / Risk Analysis memos (which also use grounded search) need a per-memo Search Suggestions block.

### Effort
~Half day. Trivially small backend (already extracted); frontend needs the iframe + sandboxing right.

### Open question
Strict reading of the TOS says "must be displayed". Pragmatic reading: as long as we cite each grounded source per item, attribution is satisfied. Recommend a quick legal-review check before deciding scope.

---

## Inline markdown editor for IS / Risk Analysis memos

**Status:** not started · **Priority:** medium · **Filed:** 2026-05-01

### Problem
Users want to hand-edit generated `.md` deliverables (`Deliverables/Memos/initial_screening.md`, `..._v2.md`, `Deliverables/Reports/risk_analyze.md`) directly in the UI — typically to fix LLM mistakes that aren't structural (a wrong number the model fabricated, a sentence that reads awkwardly) and that the upstream "fix the facts and recompose" flow can't address cheaply.

The CS3 work shipped 2026-05-01 deliberately did NOT build this:
- We extended `EntityEditModal` with all canonical metadata fields (Identity & deal, Founders, Key team, team_size).
- We added a **Recompose** button that re-runs only the composer phase against the existing section JSONs (~10 s, one Gemini call).
- Together those serve "memo is wrong because facts are wrong" — fix facts, recompose, done.

But "memo is wrong because the LLM phrased something poorly / fabricated a number not in the JSONs" is a real residual case. The user still wants a markdown editor for those.

### Open design question
Need to decide how this composes with the agent-rerun lifecycle. Three options:
- (a) Auto-flip `origin_type` to `user` on first manual edit (silent) — protects the edit but hides the divergence from the section JSONs forever.
- (b) "Fork" semantics — manual edit creates a `*_user_edited.md` sibling and the original stays agent-managed; UI shows both with a toggle.
- (c) Section-JSON editor instead — let users edit the structured JSONs, then Recompose. More aligned with Facts-vs-Opinions but more clicks.

User's explicit request (2026-05-01) is option (a) or (b) — i.e., a real `.md` editor — so plan for that. Option (c) is a non-substitute for the residual case.

### Fix sketch
- Backend: new `PUT /entities/{entity_id}/workspace/file/{node_id}/content` accepting `{ content: str, expected_version_id: str | null }`. Reuses existing `WorkspaceService.write_file` with `origin_type="user"`. Rejects 415 for binary MIME types.
- Frontend `FilePreview` (in `EntityDetail.tsx`): Edit button (lucide `Pencil`) for markdown / json / txt. Swaps `MarkdownView` → `<textarea>`. Save → PUT → refetch.
- Frontend `EntityInitialScreeningTab`: same Edit button next to Recompose / Review notes / Source.
- Decide (a)/(b) before implementation. Surface the implication in the Save toast: "Auto-promoted to user-managed (next agent run will not overwrite)" or "Saved as fork: <path>".

### Effort
~1 day for the editor itself. The hard part is the (a)/(b) UX call — make sure the user has signed off before implementing, since reverting either choice means breaking saved edits.

---

**Current priority order (2026-04-23 audit):**
1. ~~`test_chat_api.py` — 3 red tests~~ — **✅ resolved 2026-04-22**: all 3 were test-code drift against evolved production (use_deep_agent→react, CHAT_USE_DEEP_AGENT→CHAT_DEFAULT_AGENT_MODE, preset session-id requirement). Tests realigned; full suite 250/250 (excluding pre-existing `test_three_paths_e2e.py` fixture bug, see below).
2. ~~URL routing — restore browser back/forward + survive refresh~~ — **✅ resolved 2026-04-22**: 6-commit rollout wired BrowserRouter + detail routes (`/portfolio/entities/:id/:subTab?`, `/academic/scholars/:id/:subTab?`) + search-param filters + modal routes (`/portfolio/new`, `/portfolio/parking-lot`, `/portfolio/entities/:id/edit`, `/academic/new`) + settings section path param + `?chat=<sid>` for session reattach + 404 catch-all. Job reattach already worked via backend `active_job_id` — no new endpoint needed. Two polish items deferred (see below).
3. Backfill grounding-sourced URLs — **medium** (live scholar data, 58% broken)
4. `test_three_paths_e2e.py` missing `r` fixture — **medium** (28 collection errors hide pre-existing e2e coverage)
5. URL routing follow-ups (preview deep-link + scroll restoration) — **low** (see below)
6. `early_break` Tasks-view aggregation — low (quick obs win)
7. `_MAX_PAPERS` config override — low
8. Legal config → JSON-seed migration — low (ergonomics)
9. `docs/ARCHITECTURE.md` v2 rewrite — low (doc-drift)
10. `APP_PASSWORD` → Secret Manager — **low** (hardening; prod gate works today, but the plain env-var is readable by anyone with project view access)
11. Dim-seed collapse — **n/a on main**; move to `gc-deploy` branch's own backlog (`backend/app/defaults/` does not exist on `main`)

---

## `APP_PASSWORD` → Secret Manager (hardening)

**Status:** not started · **Priority:** low (gate is effective today; this is a defence-in-depth upgrade) · **Filed:** 2026-04-23

### Problem
The shared-password gate (shipped 2026-04-23) stores `APP_PASSWORD` as a plain `--set-env-vars` entry on the Cloud Run service. Anyone with `roles/run.viewer` (or broader project read) can list the service config and see the password in cleartext. That's a narrow exposure surface inside the GCP project, but it's below the bar already set by `gemini-api-key`, `portfolio-db-url`, and `academic-db-url`, all of which live in Secret Manager and are grant-gated to the compute SA alone.

The gate itself works correctly: `backend/app/main.py::shared_password_gate` uses `secrets.compare_digest` against `settings.APP_PASSWORD`, and the SPA's `LoginGate` + fetch shim round-trip correctly on prod. This backlog item is purely about moving where the value is stored, not how it's used.

### Fix
- Create a Secret Manager secret `app-password` in `vc-agent-taihill`; grant `roles/secretmanager.secretAccessor` to the compute SA.
- Update `backend/deploy_cloudrun.sh`: move `APP_PASSWORD=${APP_PASSWORD}` out of `--set-env-vars` and append `APP_PASSWORD=app-password:latest` to `--update-secrets`.
- Redeploy; remove the `APP_PASSWORD` line from the service's env-var config (it'll be shadowed by the mounted secret, but cleaner to delete).
- Rotate the password value in Secret Manager when needed — no redeploy required (env from secret is fetched per-revision, so a new secret version takes effect on next revision rollover; add `--update-secrets=APP_PASSWORD=app-password:latest` forces a revision).

### Effort
Small. One Secret Manager create + one IAM grant + one deploy-script line swap + one redeploy. ~15 min.

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
