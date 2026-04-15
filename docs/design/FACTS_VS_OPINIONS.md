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

## Code map

- `backend/app/services/metadata_extraction.py` — `_ENTITY_METADATA_DEFAULTS`, `validate_entity_metadata`, `merge_entity_metadata`, `_migrate_prior_round_entry`
- `backend/app/services/legal_review_facts.py` — `validate_legal_reviews`, `split_legal_review_entry`, `merge_prior_round_facts`, `validate_legal_review_opinions`, `merge_legal_review_opinions`
- `backend/app/services/fact_discrepancies.py` — `append_discrepancy`, `accept_discrepancy`, `reject_discrepancy`, `list_discrepancies`, `resolve_current_value`, dotted `_parse_field_path` / `_apply_field_path` / `_read_field_path`
- `backend/app/services/extract_info_signals.py` — `split_extract_info_payload`, `build_signals_document`, `has_any_signal`, `SIGNALS_WORKSPACE_PATH`
- `backend/app/services/workspace_tools.py` — `propose_fact_update` tool
- `backend/app/routers/chat.py` — extract_info + legal_review post-processing with split + fact_claims recovery
- `backend/app/routers/discrepancies.py` — GET / accept / reject endpoints
- `backend/app/prompts/extract_info.md` / `legal_review.md` — agent contract
- `frontend/src/components/FactDiscrepancyPanel.tsx` — adjudication UI
- `frontend/src/components/EntityHeader.tsx` — badge with pending count
- `frontend/src/services/api.ts` — `discrepancies.list` / `accept` / `reject`
