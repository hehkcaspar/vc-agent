# Facts vs Opinions

## Principle

Entity state splits cleanly into two categories:

- **Facts** are verifiable assertions about a company's reality — legal name, raise amount, board composition, our positions, prior-round terms, founder roster. They live exactly once, canonically, on `Entity.metadata_json`, with `Company Profile.json` at the workspace root as a readable mirror. They mutate only through deliberate writes: user edits, ingestion of source documents, or user-accepted reconciliations. **Agents never silently mutate facts.**
- **Opinions** are agent-derived assessments — priority indicators, red flags, killer questions, unusual-term concern levels, narrative summaries. They live in per-preset workspace JSON files, one file per preset, overwritten in place. Workspace `.versions/` supplies history for free.

When an opinion run reads source material that contradicts canonical state, it surfaces a **fact discrepancy** for user adjudication. Canonical facts change only on Accept.

This keeps facts auditable, opinions iterable, and the trust boundary explicit.

## Why the split

Prior to this design, `Entity.metadata_json` mashed three things into one blob:
1. Facts (legal_name, raise_amount, founders, `_positions[]`)
2. Opinions (priority_indicators, red_flags, competitors)
3. Round-scoped mix — `legal_reviews[]` entries carried BOTH factual term blocks AND opinions

This created real bugs. Example: the header "Invested" chip read `_positions[].invested_amount` (user-edited), while a legal review reading the SAFE would say "the correct amount is $750k". Nothing reconciled them. If the user forgot to edit their position, the chip stayed stale forever.

With the split, the legal review agent is *expected* to flag the mismatch (via `propose_fact_update`) and the user adjudicates. Facts never drift silently.

## Data model

### `entity.metadata_json` (facts only)

```
{
  // Tier 1 — Identity
  company_name, legal_name, one_liner, description, industry_tags,
  business_model, hq_location, website, founded_date,
  incorporation_jurisdiction, incorporation_entity_type,

  // Tier 2 — Team
  founders[],          // {name, title, background, linkedin_url, status}
  team_size, key_team[],

  // Tier 3 — Deal & funding
  investment_stage, raise_amount, raise_currency, raise_instrument,
  valuation_cap, pre_money_valuation,
  current_round_name,  // points at a prior_rounds[] entry
  prior_rounds[],      // per-round fact bag — see below
  existing_investors[], referral_source,

  // User-managed (never agent-touched)
  _positions[],        // fund_id, invested_amount, currency, current_value, ...

  // Lifecycle-managed
  _fact_discrepancies[],  // see below

  // System-managed
  _extracted_at, _extraction_version, _files_examined[]
}
```

### `prior_rounds[]` per-round fact bag

Each entry:

```
{
  round_name: str,                    // join key
  instrument_type: "safe" | "convertible_note" | "priced_round" | null,
  scenario: "new_investment" | "follow_on" | "retrospective" | null,
  effective_date: str | null,
  amount: str | null,
  currency: str | null,
  lead_investor: str | null,
  company_terms: { ... },             // class_of_shares, price_per_share, pre_money_valuation, ...
  safe_terms: { ... } | null,
  priced_round_terms: { ... } | null,
  governance: { board_composition, major_investor_threshold, protective_provisions, voting_structure },
  investor_rights: { ... },
  transfer_restrictions: { ... },
  regulatory: { ... },
  our_position: { ... } | null        // shares, amount, fully_diluted_pct, position_changes, rights_changes
}
```

`extract_info` writes shallow entries (round_name, amount, effective_date, lead_investor, instrument_type) from pitch decks. `legal_review` writes deep term blocks from SAFE / SPA / COI contents. The merge is field-level — shallow rows don't clobber term blocks, and vice versa (see `services/legal_review_facts.py:merge_prior_round_facts`).

### Fact discrepancies

```
_fact_discrepancies[] = [
  {
    id: uuid,
    detected_at: iso,
    detected_by: preset_id,              // "legal_review" | "extract_info"
    source_run: { agent_run_id, preset_id, ... },
    field_path: str,                     // "raise_amount" | "prior_rounds[Series A].safe_terms.valuation_cap" | "_positions[fund_id=taihill_iii].invested_amount"
    round_name: str | null,              // required when field_path enters prior_rounds[...]
    current_value: any,
    proposed_value: any,
    source_doc_node_id: workspace_node_id,
    source_doc_quote: str | null,
    confidence: "low" | "medium" | "high",
    rationale: str,
    status: "pending" | "accepted" | "rejected",
    resolved_at: iso | null,
    resolved_by: "user" | null,
    dismiss_reason: str | null,
  }
]
```

**Field path grammar:**

- Segments separated by `.`
- Array selectors: `[key=value]` (explicit) or `[value]` (shorthand; uses default key — `round_name` for `prior_rounds`, `fund_id` for `_positions`, `name` for `founders`/`key_team`)
- Leaf segment is the field to write

Examples:
- `raise_amount`
- `prior_rounds[Series A-5].company_terms.pre_money_valuation`
- `_positions[fund_id=taihill_iii].invested_amount`
- `founders[Jane Doe].status`

## Workspace layout

- `Company Profile.json` — facts only, root level, written by `extract_info`.
- `Legal Review.json` — opinions only, root level, written by `legal_review`.
- `Deliverables/Analysis/extract_info_signals.json` — `priority_indicators` + `red_flags` + `competitors`, written by `extract_info` when any signal array is non-empty.
- `Deliverables/Reports/risk_analyze.md` — red-team report (markdown), written by `red_team`. Was already pure opinion; no schema change.

All of these use the standard workspace versioning — every overwrite snapshots to `.versions/{node_id}/`.

## Agent contract

Opinion-producing presets (`legal_review`, `extract_info`) do three things:

1. **Write their deliverable file(s)** (Company Profile.json + signals file; or Legal Review.json).
2. **Call `propose_fact_update(...)`** whenever source material contradicts canonical state. Never silently overwrite.
3. **Optionally emit `fact_claims[]`** at the root of their JSON output as a fallback — post-processing converts any leftover claims to discrepancies.

The tool signature:

```
propose_fact_update(
  field_path: str,
  current_value: str,       # JSON-stringified
  proposed_value: str,      # JSON-stringified
  source_doc_path: str,     # resolved to node_id server-side
  confidence: "low" | "medium" | "high",
  rationale: str,           # 1-3 sentences
  round_name: str = "",     # required if field_path enters prior_rounds[...]
  source_doc_quote: str = "", # optional short excerpt
) -> { ok: true, discrepancy_id } | { ok: false, error }
```

## Lifecycle

```
 Agent detects contradiction
           │
           ▼
 propose_fact_update(...)          ← tool call during run
           │
           ▼
 _fact_discrepancies[] append      ← status="pending", metadata mutated
           │
           ▼
 UI banner (EntityHeader badge)    ← count of pending rows
           │
           ▼
 User opens FactDiscrepancyPanel
           │
       ┌───┴───┐
       ▼       ▼
   Accept   Reject (with optional reason)
       │       │
       │       ▼
       │   status = "rejected"
       │   dismiss_reason set
       │
       ▼
   status = "accepted"
   proposed_value applied to field_path
   resolved_at + resolved_by = "user"
```

Both transitions are idempotent on already-resolved rows. Accept is not reversible (the user can manually re-edit the field via EntityEditModal if they change their mind — discrepancy history is preserved).

### Cascade on selector-key accept

When the accepted leaf IS the array row's selector key (e.g. `_positions[fund_id=X].fund_id` with `proposed_value = Y`), pending sibling discrepancies that pointed at the same row still reference the OLD selector value. Without a rewrite, their subsequent accepts would miss the renamed row and auto-create stub entries — corrupting state.

`accept_discrepancy` cascades automatically: after applying the change, it scans `_fact_discrepancies[]` for pending rows whose `field_path` starts with the old `array[selector=old]` prefix (or the shorthand `array[old]` form) and rewrites them to the new value. Already-accepted / already-rejected rows keep their historical paths.

Worked example (CyberNexus, three pending after a `legal_review` run):

```
Before any accept:
  A: _positions[fund_id=taihill_v3_lp].fund_id           → "taihill_venture_seed_iii_lp"
  B: _positions[fund_id=taihill_v3_lp].invested_amount   → 300000
  C: _positions[fund_id=taihill_v3_lp].round_at_entry    → "Series Angel-1"

User accepts A (the rename).
  • _positions[0].fund_id becomes "taihill_venture_seed_iii_lp".
  • Cascade rewrites B and C:
      B: _positions[fund_id=taihill_venture_seed_iii_lp].invested_amount → 300000
      C: _positions[fund_id=taihill_venture_seed_iii_lp].round_at_entry  → "Series Angel-1"

User accepts B, then C.
  • Both land on the same (renamed) row. No phantom positions.
  • Final _positions:
      {fund_id: taihill_venture_seed_iii_lp, invested_amount: 300000, round_at_entry: Series Angel-1, …}
```

Tests: `backend/tests/test_fact_discrepancies_cascade.py` covers the 3-discrepancy happy path, shorthand selector, non-selector leaves left alone, and historical entries preserved.

## Migration

No scripted migration. Lazy compat on read:

- `validate_entity_metadata` normalises legacy `prior_rounds[]` short shape (`{round, amount, date, lead_investor}` → fact bag) and tolerates missing `_fact_discrepancies` (defaults to `[]`).
- Legacy top-level `legal_reviews[]` on existing entities is read once then ignored. First legal_review re-run drops the key.
- Legacy root `Legal Review.json` continues to be read by post-processing; next legal_review run writes the new opinion-only shape.

Cost: re-run `legal_review` on existing portfolio entities. Cheap, and gives visibility into discrepancies the agent couldn't surface before.

## Fact ledger (provenance + history)

Flat metadata fields alone don't tell you *where* a value came from or *when*. The fact ledger sits alongside the flat fields inside `Entity.metadata_json` and records every hard-fact write as an append-only entry:

```
metadata_json = {
  ...flat fields unchanged (founders[], prior_rounds[], website, …)...,
  "_fact_discrepancies": [ ... ],
  "_ledger": [
    { "entry_id": "fle-…", "fact_path": "founders[name=Joe].title",
      "value": "CEO", "source": { "type": "upload", "ref": "workspace://…",
      "quote": "…", "preset": "extract_info", "run_id": "…" },
      "confidence": 0.85, "as_of": "2025-12-01", "recorded_at": "…Z",
      "supersedes": null, "status": "active", "notes": null,
      "linked_discrepancy_id": null }
  ]
}
```

### Hard vs soft

The ledger only carries **hard facts** — verifiable, provenance-bearing fields listed in `hard_fact_catalog.py`. Roughly: identity (website, name, incorporation, founded), team titles + LinkedIn URLs, everything under `prior_rounds[round_name=…]`, positions, raise terms. *Soft claims* (one-liner, description, market narrative, industry tags, priority_indicators, red_flags, competitors) never enter the ledger — they are per-run opinions that flow through preset workspace files as before.

### Source tiers

Each entry's `source.type` ∈ `cap_table` > `legal_doc` > `user` > `upload` > `third_party` > `communication` > `web` > `self_claim`. `fact_manager.detect_contradiction` compares a proposed source's tier against the existing active entry's tier so the UI can decide whether a discrepancy needs human adjudication (weaker/equal tier) or can auto-promote (stronger tier — reserved for future v2).

### Lifecycle

`fact_manager.record_fact` is the single writer. Semantics:

- **Fresh path** → append `status:"active"`, sync flat field.
- **Same value, same source** → idempotent no-op.
- **Same value, different source** → append `status:"verified"` (corroboration).
- **Different value** → prior entry flipped to `superseded`, new entry appended `active` with `supersedes` pointer, flat field rewritten.
- **Proposed** → appended but flat field untouched until user accepts.

The discrepancy shim links `_fact_discrepancies[]` and `_ledger[]`: `propose_fact_update` dual-writes; `accept_discrepancy` calls `fact_manager.promote_proposed_to_active`; `reject_discrepancy` calls `fact_manager.reject_proposed`.

### Preset producers

- **`extract_info`** — every hard field in the validated payload is routed through `fact_manager.record_fact` with `source={type:"upload", ref:<primary file>, preset:"extract_info"}, confidence: 0.85`.
- **`legal_review`** — every leaf under each round's `fact_block` is routed through `record_fact_in_metadata` with `source={type:"legal_doc"|"cap_table", ref:<primary doc>}, confidence: 0.95`. Opinion block stays in `Legal Review.json`.
- **`initial_screening`** — hard findings go through `propose_fact_update` from within the agent. Soft findings stay in section JSONs. The memo never touches canonical facts.

### API + UI

- `GET /entities/{id}/facts/provenance` — returns `{groups: {fact_path: {current: FactEntry, history: [FactEntry...]}}}`.
- `EntityFactsTab` / `EntityHeader` — `Info` icons next to hard facts; click opens `FactProvenancePopover` with source tier pill, confidence, source link, quote, dates, collapsible history.
- `FactDiscrepancyPanel` — source-tier pill (read from the mirrored proposed ledger entry) next to the confidence level.

## Initial Screening (three-stage)

Two presets with the same deliverable shape, matching Taihill's internal **Monday Screening Template**. Reference samples at `reference-project/Initial Screening & DD Samples/` (Agent Arena, GGWP, InnerCosmos, Lynq, Quest).

### Output template (both versions produce this)

```
# {entity_name} — Initial Screening

## Intro                              — 2-3 sentences with quantitative data
## [1] Team                           — business vs academic format per person
## [2] Market & Industry Pain Point   — sizing (+table if multi-sector) + pains
## [3] Product/Tech                   — 4 sub-parts: Technology / Moats /
                                        Commercial Value / Product milestones
## [4] Business Model                 — revenue streams + unit economics
## [5] Funding & Traction             — History / Current Round / Traction
## [6] Source                         — deal referrer (from entity metadata)
## Follow-up questions (optional)     — deduped from section `open_gaps[]`
```

Team's `[1]` format varies by person:
- **Business operator**: `Name | Role / Prior Co | role / details (scale, funding/exit, outcomes)`.
- **Academic**: `Name | Role / School | Degree | Field / Research focus / GS metrics (citations, h-index, i-10) / publications with impact factors`.

### `initial_screening` — monolithic Phase 1

- **Phase 1** — ReAct agent with workspace tools + `legal_template_read` + **`web_search`** (Gemini Google Search grounding). Writes five section JSONs under `Deliverables/Analysis/initial_screening/{team,market,product_tech,business_model,funding_traction}.json` — each with `facts[]`, `claims[]`, `open_gaps[]`, `extras{}` (the composer reads `extras` to render template sub-sections). Hard findings surface via `propose_fact_update`. Recursion limit overridden to 120.
- **Phase 2 (compose)** — one-shot Gemini (no tools, no search), seeded only with the 5 section JSONs + the entity's canonical `referral_source` (for `[6] Source`). Writes `Deliverables/Memos/initial_screening.md`.
- **Phase 3 (review)** — one-shot Gemini reads draft + section JSONs. Applies corrections silently (no strikethroughs / tracked-changes in the memo — the before/after lives in the review notes). Writes revised memo + `initial_screening_review_notes.md`.

### `initial_screening_v2` — split Phase 1

Same three-stage shape, but Phase 1 decomposes into:

1. **Survey agent** (sequential, ≤25 recursion limit, no web search, restricted to browse/read tools) — scans the workspace tree, emits a JSON handoff identifying primary source docs + per-section hints.
2. **Five section agents in parallel** (`asyncio.gather`, ≤45 recursion limit each) — one agent per section (`team`, `market`, `product_tech`, `business_model`, `funding_traction`). Each gets the survey handoff, its own focused prompt, and a restricted toolkit. Writes one JSON at `Deliverables/Analysis/initial_screening_v2/{section}.json`.

Compose + review reuse the v1 functions, parameterised on `analysis_dir` / `memo_path` / `review_notes_path` so v2 writes to `Deliverables/Memos/initial_screening_v2.md` + `..._v2_review_notes.md` (v1 + v2 artifacts coexist for comparison).

**Tradeoffs observed:**

- Wall-clock: v1 beats v2 on a well-tuned clean run (v1 CyberNexus 188s vs v2 ~280-300s) — serial efficiency + shared context beat parallelization overhead for ~10-call sub-tasks.
- Predictability: v2 wins — per-section budget is capped; one section thrashing can't starve the others.
- Failure isolation: v2 wins — a failing section still lets 4-5/5 ship + memo. v1 is all-or-nothing.
- Prompt maintenance: v1 has 3 prompts (research/compose/review); v2 has 9 (survey + 5 sections + compose + review). v2 section prompts are easier to iterate independently.
- Cross-section reasoning: v1 wins — one agent sees everything. v2's sections are isolated.

Keep both: default to v1 for standard runs, pick v2 for rich workspaces or when section-level failure visibility matters.

### Frontend presentation

The memo(s) surface in `EntityDetail` as conditional tabs next to **Workroom** / **Facts**:

- `Deliverables/Memos/initial_screening.md` → **Initial Screening** tab (labeled **Screening v1** when v2 also exists).
- `Deliverables/Memos/initial_screening_v2.md` → **Initial Screening** tab (labeled **Screening v2** when v1 also exists).

The tab button is hidden entirely when its memo is absent. Rendering lives in `components/EntityInitialScreeningTab.tsx` — a single path-agnostic component (`memoPath` + optional `reviewPath` props) used for both versions. It splits the md on h2 boundaries and renders each section as a `.facts-section` card so the page reads like the Facts tab. The sibling `*_review_notes.md` is lazily loaded behind a "Review notes" disclosure. The five section JSONs under `Deliverables/Analysis/initial_screening[_v2]/` are not read here — the memo is the assembled view; users can open the JSONs from the workspace tree when they need the raw evidence layer.

### Section-agent reliability patterns (v2)

Four patterns combined to take v2 from 2/5 → 4/5 clean section deliveries on CyberNexus (rich Chinese-deck workspace):

1. **Pre-delete target files** (`_delete_if_exists`) — soft-delete the section's expected path BEFORE dispatching the agent. Post-run: file exists → agent delivered fresh content. Missing → honest failure (no stale-file false positive).
2. **Dual delivery path** — section prompts accept either `workspace_write_file` (preferred) OR the JSON as final reply text (fallback). Orchestrator checks for a fresh file first; falls back to parsing the reply text via `_parse_section_json`. Tool-only fails when agents skip the write; text-only fails when agents return empty replies. Together, one path almost always converges.
3. **Invoke-error-tolerant verification** — wrap `invoke_react_portfolio_agent` in a narrow try/except. If it raises (usually recursion after a successful write), still check the file. If the deliverable landed, mark success with a `(agent thrashed after write but file is good)` note.
4. **Moderate recursion limit (45)** — 30 was too tight (compliant agents failed on rich workspaces); 120 delayed real failures. 45 (~14-15 tool calls) is the right budget: enough for 2 reads + 3-4 searches + write + think-steps, still catches pathological thrashing.

**Anti-pattern that didn't help:** aggressively tightening prompts with "MANDATORY" language to force the agent to call `workspace_write_file` and stop. That made compliant agents write then keep ruminating. The real cure is structural (pre-delete + dual path + error-tolerant verify), not prompt-based exhortation.

### Section JSON schema — `extras` structured handoff

Each section JSON has `facts[]`, `claims[]`, `open_gaps[]`, plus `extras{}` that the composer reads to render template sub-parts. Per-section `extras` shape:

- **`team.facts[].extras`**: `{name, role, profile_type: "business|academic", prior_roles[], gs_metrics: {citations, h_index, i10_index}, publications[], status}`. Composer picks business-operator vs academic layout based on `profile_type`.
- **`market.extras`**: `{market_size_table: [{sector, current, projected, cagr, source}], pain_points: [str]}`. Composer renders the table + a bulleted list of pains.
- **`product_tech.extras`**: `{technology, advantages_and_moats, product_commercial_value, product_milestones}` — four 1-3 sentence strings. Composer renders as bold-labeled paragraphs under `[3] Product/Tech`.
- **`business_model.extras`**: `{summary_paragraph, revenue_streams: [{stream, pricing, share}], unit_economics: {gross_margin, acv, ltv, cac, ltv_to_cac}}`. Composer renders as bullets + optional sub-table when 3+ streams.
- **`funding_traction.extras`**: `{funding_history[], founder_priors[], current_round{}, traction: {financial, customers, product_usage}, coinvestors_notes[]}`. Composer renders sub-sections under [5] Funding & Traction.

The composer's core discipline: **every claim in the memo must trace to `facts[]`, `claims[]`, or `extras.*` — no outside knowledge.** The review stage enforces this by re-reading the section JSONs and striking anything that can't be anchored.

## Code map

- `backend/app/services/metadata_extraction.py` — `_ENTITY_METADATA_DEFAULTS`, `validate_entity_metadata`, `merge_entity_metadata`, `_migrate_prior_round_entry`
- `backend/app/services/legal_review_facts.py` — `validate_legal_reviews`, `split_legal_review_entry`, `merge_prior_round_facts`, `validate_legal_review_opinions`, `merge_legal_review_opinions`
- `backend/app/services/fact_discrepancies.py` — `append_discrepancy`, `accept_discrepancy`, `reject_discrepancy`, `list_discrepancies`, `resolve_current_value`, dotted `_parse_field_path` / `_apply_field_path` / `_read_field_path`
- `backend/app/services/fact_ledger_schema.py` — `FactEntry`, `FactSource`, `FactStatus`, `CONFIDENCE_STRING_TO_FLOAT`
- `backend/app/services/hard_fact_catalog.py` — `HARD_FACT_PATTERNS`, `HARD_FACT_PREFIXES`, `EVIDENCE_TIERS`, `is_hard_fact`, `evidence_tier`
- `backend/app/services/fact_manager.py` — `record_fact`, `record_fact_in_metadata`, `get_current`, `get_history`, `get_provenance`, `detect_contradiction`, `extract_hard_facts_from_payload`, `promote_proposed_to_active`, `reject_proposed`, `record_proposed_for_discrepancy`
- `backend/app/services/initial_screening_job.py` — v1 monolithic Phase 1, plus `run_compose_stage` / `run_review_stage` (parameterized on analysis_dir / memo_path / review_notes_path so v2 reuses them)
- `backend/app/services/initial_screening_v2_job.py` — v2 survey + 5-section parallel orchestrator (`run_survey_stage`, `_run_one_section`, `run_parallel_sections`, `run_research_v2`, `run_compose_review_v2`). Includes reliability helpers: `_delete_if_exists` (pre-delete for freshness), `_peek_updated_at`, `_parse_section_json` (text-reply fallback). Constants: `SURVEY_RECURSION_LIMIT=25`, `SECTION_RECURSION_LIMIT=45`, `_SURVEY_TOOL_ALLOWLIST`, `_SECTION_TOOL_ALLOWLIST`.
- `backend/app/services/web_search_tool.py` — `build_web_search_tool` (Gemini-grounded research tool; uses Flash model for ~4x faster sub-calls vs Pro)
- `backend/app/services/extract_info_signals.py` — `split_extract_info_payload`, `build_signals_document`, `has_any_signal`, `SIGNALS_WORKSPACE_PATH`
- `backend/app/services/workspace_tools.py` — `propose_fact_update` tool (dual-writes a `proposed` ledger entry alongside `_fact_discrepancies[]`)
- `backend/app/routers/chat.py` — extract_info + legal_review + initial_screening post-processing; ledger routing for each
- `backend/app/routers/discrepancies.py` — GET / accept / reject endpoints (accept/reject sync the linked ledger entry)
- `backend/app/routers/fact_ledger.py` — `GET /entities/{id}/facts/provenance`
- `backend/app/prompts/extract_info.md` / `legal_review.md` / `initial_screening_research.md` / `initial_screening_compose.md` / `initial_screening_review.md` — monolithic (v1) agent contracts. `initial_screening_compose.md` is shared by both versions — it codifies the Taihill Monday Screening memo template.
- `backend/app/prompts/initial_screening_v2_{survey,team,market,product_tech,business_model,funding_traction}.md` — v2 survey + 5 per-section agent contracts
- `reference-project/Initial Screening & DD Samples/` — real Taihill samples (Agent Arena, GGWP, InnerCosmos, Lynq, Quest IS) + the internal Monday template. Source of truth for output format.
- `frontend/src/components/FactProvenance.tsx` — `FactProvenanceProvider`, `FactProvenanceBadge`, `SourceTierPill`, popover + history drawer
- `frontend/src/components/FactDiscrepancyPanel.tsx` — adjudication UI with source-tier pill
- `frontend/src/components/EntityFactsTab.tsx` — badges on website, founder titles, co-investors, round amount + lead
- `frontend/src/components/EntityHeader.tsx` — badge on founder chips + pending-discrepancy count
- `frontend/src/services/api.ts` — `factLedger.getProvenance`, `discrepancies.list` / `accept` / `reject`
