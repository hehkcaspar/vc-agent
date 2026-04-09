# Scholar Evaluation Framework (WIP)

Design doc for rewriting the scholar evaluation prompts. Work in progress — we are
iterating on this file before touching `scholar_prompts.py` or `dimensions.json`.

Status: **4-dimension design finalized**. D1 Academic Excellence, D2 Tech-transfer Experience, D3 Founder Potential (absorbs Public Profile as a factor), D4 Growth Trajectory. Each dim is self-contained and transparent to the others — ready for implementation.

---

## Goals

- Replace today's vague 0–100 "score" with **percentile-style evaluation** against
  a well-defined peer group.
- Force **evidence-backed** scoring — no score ≥50 without cited primary evidence.
- Fix known false positives (e.g. Liangbing Hu's Commercialization = 95 with no
  visibility into the two startups' actual traction).
- Make the framework **reusable** across dimensions so prompts stay DRY.

---

## Scope of this doc

- Shared concepts that apply to **every** dimension.
- 4-dimension MECE design (adopted after reviewing VC and bibliometric
  literature — no professional framework uses more than 5):
  **D1 Academic Excellence**, **D2 Tech-transfer Experience**,
  **D3 Founder Potential**, **D4 Growth Trajectory**.
- Per-dimension rewrites live in the "Per-dimension rewrites" section.

## Why 4 dimensions

The original 7-dim design had significant overlap:
- Research Impact + Field Position + Collaboration Strength all measured
  aspects of "academic standing" and correlated ~80% → merged into **D1
  Academic Excellence**
- Career Trajectory was a derivative of the other dims → kept as **D4
  Growth Trajectory**, explicitly framed as cross-cutting synthesis
- Public Profile was orthogonal but low decision-relevance → absorbed as a
  factor inside **D3 Founder Potential** (public presence is a founder-
  relevant skill, not a standalone decision signal)
- Commercialization renamed to **D2 Tech-transfer Experience** to make the
  "historical commercial track record" framing explicit
- Founder Potential unchanged in name and scope, gains public-presence as
  one of its factors

## Reading order

The eight shared concepts build on each other:

1. **Score semantics** (percentile bands)
2. **Peer group** (who to compare against — field + phase)
3. **Evidence contract** (what counts as proof of a score)
4. **Author-position weighted citations** (how to count paper impact fairly)
5. **Storage conventions** (how everything persists — JSONL vs JSON)
6. **Continuous monitoring architecture** (3-layer: facts → refresh → eval)
7. **Red flags fact channel** (negative signals as facts, not a dim)
8. **Relative traction rubric** (helper used by commercial/founder dims)

---

## Shared Concept 1 — Score semantics = percentile

- Score = "percent of comparable peers this scholar beats on this dimension".
- Only 50+ is interesting. Below 50 → agent outputs single bucket `<50` and stops
  differentiating (save tokens, avoid false precision).
- Band anchors (same for every dimension, only the evidence differs):

  | Band    | Meaning                                                |
  |---------|--------------------------------------------------------|
  | `<50`   | unremarkable for peer group — skip                     |
  | 50–74   | above median, solid                                    |
  | 75–89   | top quartile, notable                                  |
  | 90–94   | top decile, strong signal                              |
  | 95–98   | top 5%, rare                                           |
  | 99      | singular, generational (a handful per field per gen)   |

- "Score" is still the stored field name for backward compat, but it represents
  a percentile.

---

## Shared Concept 2 — Peer group = field + career phase (two-axis)

Neither "years since PhD" nor "title" alone works. A 10-yr assistant prof and a
1-yr assistant prof are wildly different; a 10-yr post-PhD can be anything from
a 2nd-year tenure-track star to a long-term staff scientist. The user
explicitly asked for a **combined classification** that uses both graduation
timing AND achievements.

### Standards review (from literature)

We surveyed existing frameworks before designing ours:

- **European Commission R1–R4** — the most widely adopted standard. Defines
  four tiers by *independence and leadership*, not title/years:
  - **R1 First Stage** — PhD students, research assistants
  - **R2 Recognised** — PhD holders not yet fully independent (postdocs,
    lecturers)
  - **R3 Established** — independent researcher with established reputation,
    publishes as lead author, can lead collaborative projects
  - **R4 Leading** — leads research in their area/field, leads a team or
    research group
- **ERC grants** use time-since-PhD as *eligibility* windows, not identity:
  Starting 2–7 yrs, Consolidator 7–12 yrs, Advanced unlimited. From 2027
  these expand to 0–10 and 5–15 with deliberate overlap — an explicit
  acknowledgement that years alone don't cleanly partition researchers.
- **NIH Early Stage Investigator (ESI)** — ≤10 yrs post-PhD AND no prior major
  NIH award. A *conjunction* of time + achievement, not either alone.
- **Academic age vs chronological age** — literature strongly prefers
  "academic age" (years since PhD or first publication), adjustable for
  parental leave, illness, clinical training, national service.
- **Consensus**: the "middle" of the career is notoriously ill-defined
  (Nature, 2022 — "muddle of the middle"); any framework must accept fuzzy
  boundaries in the middle phases.

### Our framework — R1–R4-aligned, two-axis

We adopt the **R1–R4 skeleton** (well-known, defensible, maps to how funders
actually think) but score each scholar on **both axes** and reconcile.

**Axis A — Academic age** (years since PhD, adjustable):
- Computed as `current_year − phd_year`
- Adjust for documented leave / clinical training / national service when known
- Used as *evidence*, not as the sole classifier

**Axis B — Achievement gates** (binary, cumulative — reaching a later gate
requires having passed earlier ones):
1. **G1 — First independent position** (tenure-track, group leader, permanent
   researcher). Staff scientist / long-term postdoc does NOT count.
2. **G2 — First major independent grant** as PI (ERC Starting/equivalent, NIH
   R01, NSF CAREER, major national-council grant).
3. **G3 — Tenure or equivalent permanence** + second major grant + "signature"
   result the subfield recognizes.
4. **G4 — Field leadership markers**: field-level awards, editor-in-chief /
   major editorial board, keynote density, trainees running their own labs,
   named chair, society fellowships.

**Phase assignment rule:**

| Phase | Required gates | Typical academic age | Notes |
|---|---|---|---|
| **R1 Trainee**     | none (pre-PhD or no G1)    | n/a      | Skip — not our target |
| **R2 Recognised**  | G1 not yet passed          | 0–10     | Postdocs, long-term staff scientists |
| **R3a Emerging Independent** | G1 ✓, G2 not yet | 0–7      | New PI building first group |
| **R3b Established** | G1 ✓, G2 ✓, G3 not yet    | 5–15     | Consolidated PI, signature result not yet clear |
| **R3c Consolidated** | G1 ✓, G2 ✓, G3 ✓         | 10–25    | Mid-senior PI, recognized voice |
| **R4 Leading**     | all of G1–G4               | 15+      | Field-shaping leader |

**Conflict resolution between the two axes:**

This is the critical rule — it's what fixes the "10-yr assistant prof" case.

1. **Gates dominate age.** A scholar 15 yrs post-PhD who has not passed G2 is
   **R3a**, not R3b — they are still "Emerging Independent" despite the years.
   This is how the framework handles the long-term assistant prof: evidence of
   achievement, not calendar time, sets the phase.
2. **Age caps upward mobility.** A scholar 3 yrs post-PhD who happens to have
   an early G2 is **R3a**, not R3b — the age window caps promotion because
   the typical benchmarks for R3b don't apply yet at that academic age.
3. **No skipping gates.** Even if academic age is 20 yrs, failing G1 means R2
   (this is the long-term staff scientist case — treated as R2, scored against
   R2 peers).
4. **Clock adjustment.** If `phd_year` unknown, agent estimates from
   first-publication year − 4 (typical PhD length) and marks
   `academic_age_uncertain: true`.

### Storage — `peer_group.jsonl` (see Concept 5)

Each line is one phase classification; the latest line is the current state.

```json
{
  "id": "2026-04-01T10-00-00Z",
  "field": "cellulose nanomaterials / transparent wood",
  "field_parent": "nanomaterials",
  "cohort_size_estimate": 12,
  "cohort_examples": [
    "Scholar A (MIT)", "Scholar B (Tsinghua)", "Scholar C (KTH)"
  ],
  "academic_age": 17,
  "academic_age_adjustments": [],
  "gates_passed": ["G1", "G2", "G3", "G4"],
  "phase": "R4",
  "phase_evidence": [
    "G1: Yale appointment 2009",
    "G2: NSF CAREER 2012; DOE Early Career 2013",
    "G3: tenure 2015; Nature cover paper on transparent wood 2016",
    "G4: MRS Medal 2020; group of ~20 trainees"
  ],
  "context_modifiers": {
    "institution": {
      "name": "Yale University",
      "tier": "elite",
      "resource_level": "high"
    },
    "geographic_region": "north_america",
    "data_availability": "high"
  },
  "prev_id": "2024-01-10T09-00-00Z",
  "change_reason": "G4 reached — MRS Medal award detected"
}
```

### Context modifiers (Q7 — geography and institution are NOT peer-group splitters)

Peer groups are **not** split by geography or institution tier. The percentile
is always computed against the full global field cohort. Instead,
`context_modifiers` captures observable context that consumers use to *frame*
the score without partitioning the cohort.

**Why no splits:**

- **VC decisions are global.** A score that says "top 5% of Chinese scholars"
  isn't directly actionable — the VC still compares globally anyway.
- **Splitting would collapse cohorts.** "Top 5% of Chinese cellulose
  nanomaterials scientists" might be 2 people.
- **Institution tier is what we want to SEE, not erase.** Normalizing by tier
  would hide the very thing a deep-tech VC wants: is this person producing
  commercializable output despite weaker institutional resources? Top-5% at a
  non-elite school is often a *stronger* signal than top-50% at MIT — peer
  group splitting would destroy that distinction.
- **Legitimate regional confounders are already handled** by the hybrid grant
  list in Q8 and the narrowest-subfield rule in Q3.

**Institution tier taxonomy (coarse by design):**

- `elite` — top ~20 globally in the scholar's field
- `strong` — top ~100 globally, well-resourced, consistent output
- `regional` — well-regarded in country/region, mid-resources
- `emerging` — newer or resource-constrained institutions

**Geographic regions:** `north_america | europe | east_asia | south_asia | middle_east | africa | oceania | latin_america`.

**Data availability:** `high | medium | low`. This is the **one modifier that
does touch scores** — dim agents bump `uncertainty` up one level when
`data_availability: low`. This handles non-English-corpus bias and thin SS/GS
coverage without inventing a separate cohort.

**How modifiers are used downstream:**

- **Percentile calculation**: modifiers do NOT affect the score.
- **Narrative synthesizer**: uses modifiers to frame scores in plain English
  ("scoring 92 at an elite, high-resource institution is strong but expected;
  the same score at a regional institution would be more unusual").
- **UI**: surfaces modifiers as tags next to the phase.
- **`data_availability: low`** → dim agents add one level of uncertainty.

### Rules for downstream dimensions

1. Every dimension scores against the **phase**, never raw age or title.
2. Cross-phase comparisons are forbidden.
3. The declared phase is set once and reused — no per-dimension
   re-classification.
4. `missing_data` on Axis B gates: if G2 cannot be verified, phase is capped
   one tier lower and flagged.

### G2 grant list (resolved — hybrid)

**For US/EU scholars — hardcoded canonical list:**
- **US**: NIH R01, NSF CAREER, DOE Early Career, DARPA Young Faculty Award,
  ONR/AFOSR Young Investigator Program, Sloan Research Fellowship, Packard
  Fellowship, NIH Director's New Innovator
- **EU**: ERC Starting Grant, ERC Consolidator Grant, national-equivalent
  first-PI grants (e.g. DFG Emmy Noether, UKRI Future Leaders Fellowship,
  ANR JCJC, VIDI/VICI)

**For non-US/EU scholars — agent infers** what counts as a major first-PI
grant in the scholar's country/region, using local standards (e.g. NSFC
Excellent Young Scientists / Distinguished Young Scholars for China, JSPS
Kakenhi Grant-in-Aid for Scientific Research (A) for Japan, etc.). Agent must
record the inferred standard in `phase_evidence` so it can be audited.

### Field granularity — as fine as defensible

The agent classifies the scholar into the **narrowest defensible subfield** —
not the broad discipline. Granularity target: "the specific research problem
this group is known for", not the department name.

**Examples:**
- ❌ too coarse: "materials science" / "nanomaterials"
- ✅ right: "cellulose nanomaterials / transparent wood"
- ❌ too coarse: "machine learning"
- ✅ right: "efficient inference for transformer LLMs" or
  "diffusion models for protein structure"
- ❌ too coarse: "biology"
- ✅ right: "CRISPR base editors for therapeutic applications"

**Rule:** pick the narrowest subfield where the agent can still name a
cohort of **roughly 10+ identifiable peers worldwide**. If the cohort shrinks
below ~10, step one level broader — but never broader than necessary.
Top-5% deep specialists legitimately live in cohorts of 10–15; going
broader erases exactly the signal a VC wants.

**Why finer is better for our use case:**
- VC decisions are made on specific capabilities, not discipline labels.
- "Top-5% in transparent wood" is a more useful signal than "top-20% in
  materials science".
- Percentile claims only hold if the cohort is actually comparable.

The `cohort_examples` field in `peer_group.jsonl` (3–5 named peers) is the
**audit mechanism**: if the agent can't name real peers, the subfield is too
narrow (or the agent doesn't know the field), and it must back off one level.

### Design note — no direct phase override

Users cannot manually set the phase. Corrections flow through **chat**: the
user tells the agent what looks wrong, and the agent takes notes and
updates the fact store. The chat-driven correction mechanism is out of scope
for this framework — it will be specified in a separate design doc. For now:
agent classification is the only source of phase.

---

## Shared Concept 3 — Evidence contract

Every score ≥50 must emit structured evidence. Scores without evidence get
capped.

```json
{
  "score": 92,
  "peer_group_ref": "2026-04-01T10-00-00Z",   // ID of entry in peer_group.jsonl
  "evidence": [
    {
      "claim": "...",
      "source": "url | paper_id | patent_id | grant_id",
      "weight": "primary | supporting"
    }
  ],
  "uncertainty": "low | medium | high",
  "missing_data": [
    "funding/revenue of InventWood unknown — capped score"
  ]
}
```

### Rules

- **≥75 with <2 primary evidence items** → agent must downgrade to `<50` or
  explicitly mark `uncertainty: high`.
- `missing_data` is a first-class field. It is the mechanism that prevents
  false positives like Liangbing's Commercialization = 95.
- Sources must be real identifiers (URL, paper_id, patent_id), not prose.

---

## Shared Concept 4 — Author-position-weighted citations

Raw citation count is nearly worthless. A 100k-cite scholar whose 80k come from
one 5th-author paper is a completely different animal than a 30k-cite scholar
whose top 10 papers are first/last author.

### Per-paper weights

| Author position                      | Weight | Rationale                                |
|--------------------------------------|--------|------------------------------------------|
| First author                         | 1.0    | Did the work                             |
| Last author (≥3 authors)             | 1.0    | PI, owns the direction                   |
| Co-first / co-corresponding (marked) | 1.0    | Treat as first                           |
| Second author                        | 0.4    | Substantial, not owner                   |
| Second-to-last (≥4 authors)          | 0.3    | Senior contributor, not PI               |
| Middle author                        | 0.1    | Collaborator                             |
| Consortium / ≥20 authors             | 0.05   | Membership                               |

### Derived metrics (must be stored)

```json
"attributed_metrics": {
  "total_citations_raw": 136985,
  "attributed_citations": 48200,
  "attribution_ratio": 0.35,
  "first_author_citations": 12400,
  "last_author_citations": 33100,
  "first_last_h_index": 42,
  "top5_first_or_last": [
    {"paper_id": "...", "title": "...", "position": "last", "citations": 4200}
  ],
  "inflation_flags": [
    "80k citations concentrated in one 5th-author paper (Nature 2015) — discounted to 8k"
  ]
}
```

### Downstream rules

1. **Academic Excellence** scores against `attributed_citations`, never raw.
   Use a first/last-author h-index.
2. **Concentration check**: if >40% of attributed citations come from a
   single paper, flag `inflation: concentrated` and cap Academic Excellence
   at ~85 unless the paper is truly field-defining.
3. **Position-over-time check** (anti-gaming): first-author → last-author
   trajectory is healthy. A scholar stuck as middle author on big consortium
   papers for 10 years is a red flag, surfaces in Growth Trajectory.
4. **Venue × position**: note venue tier alongside position but do not
   double-weight (venue is already encoded in citation count).

### Co-first / co-corresponding detection

- API-reported position is the default.
- For the scholar's top 10 papers by raw citation, fetch abstract/PDF metadata
  to check for co-first / co-corresponding footnotes.
- If unknowable, use API position and mark `uncertainty: medium`.

### Where computation lives

`attributed_metrics` is computed once by Layer 2 (ingest/refresh) and stored
in `attributed_metrics.json` in the fact store. Dim agents in Layer 3 read
these values; they never recompute. See Concept 6 for the full orchestration.

---

## Shared Concept 5 — Storage conventions (one layout for everything)

All scholar data lives under `data/scholars/{scholar_id}/`. There are exactly
**two file shapes** in use.

### Rule 1 — Append-only history = JSONL

Everything that is a log, an event stream, or a history of states uses
**one-file-per-record-type JSONL**:

```
data/scholars/{scholar_id}/{record_name}.jsonl
```

| File                        | Content                                                     |
|-----------------------------|-------------------------------------------------------------|
| `peer_group.jsonl`          | One line = one phase classification; latest line is current |
| `red_flags.jsonl`           | Event log: `flag`, `dismissal`, `resolution` events         |
| `snapshot_log.jsonl`        | One line = one Layer 2 fact-store snapshot marker           |
| `events.jsonl`              | Scholar timeline events (already exists)                    |
| `news.jsonl`                | News items discovered by `news_web` source (append-only)    |
| `evaluations/{dim}.jsonl`   | One line = one dim eval run (replaces per-date files)       |
| `narrative.jsonl`           | One line = one synthesized narrative report                 |

### Rule 2 — Pure current-state = single JSON

Files that are rewritten wholesale on update (not append logs) stay as single
JSON:

| File                      | Content                                                    |
|---------------------------|------------------------------------------------------------|
| `profile.json`            | Current scholar profile (tags, affiliation, URLs, notes)   |
| `papers.json`             | Current paper list (rewritten on SS/GS refresh)            |
| `grants.json`             | Current grant list (rewritten on grant search refresh)     |
| `patents.json`            | Current patent list (rewritten on patent search refresh)   |
| `startups.json`           | Current affiliated-startup list                            |
| `attributed_metrics.json` | Current derived metrics (rewritten when papers change)     |
| `channels.json`           | Current channel config                                     |

### Rule 3 — IDs are ISO timestamps

Every record in every JSONL file has an `id` field:

```
2026-04-01T10-00-00Z
```

- No prefix — the filename already disambiguates what kind of record it is.
- Lexical sort == chronological sort, so the log is naturally ordered.
- Collisions are not handled by suffixes. Instead: a **per-scholar write
  lock** serializes appends. This is a single-user / tiny-user system; a
  mutex is simpler and safer than timestamp collision logic.

### Rule 4 — Attachments are inline as `source_url` + `source_summary`

No external blob files per record. Every record that references a source
stores:

```json
"source_url": "https://...",
"source_summary": "...concise text summary of what's at that URL..."
```

If the source is a PDF, the agent writes a text summary of the PDF contents
into `source_summary`. If we ever need original bytes, we re-fetch from
`source_url`. Keeps the storage model uniform.

### Rule 5 — Mutability inside JSONL is achieved via event projection

Records in JSONL files are never edited in place. Mutable state (e.g. red
flag status can transition `unresolved → dismissed → resolved`) is
represented as **additional events** that reference the original record's
`id`:

```jsonl
{"id":"2026-04-01T10-00-00Z","type":"flag","category":"retraction","severity":"high","claim":"...","source_url":"...","source_summary":"...","affected_dimensions":["research_impact"]}
{"id":"2026-04-10T14-22-00Z","type":"dismissal","target_id":"2026-04-01T10-00-00Z","reason":"user confirmed retraction was corrected","actor":"user_chat"}
```

Current state is a **fold** over the log (replay events, apply reducer,
return projection). Audit trail is free — every historical state is
reconstructible.

### Rule 6 — Shared primitives (one implementation)

All JSONL read/write goes through four helpers in
`services/academic/file_utils.py`:

```python
def append_record(scholar_id: str, record_name: str, obj: dict) -> str
def read_records(scholar_id: str, record_name: str) -> list[dict]
def fold_records(scholar_id: str, record_name: str, reducer) -> Any
def latest_record(scholar_id: str, record_name: str) -> dict | None
```

Every log uses these four primitives. One set of tests, one locking
behavior, one crash-recovery story. Per-scholar write lock is implemented
inside `append_record`.

---

## Shared Concept 6 — Continuous monitoring architecture (3-layer)

Each dimension evaluation is an **independent, scheduled task**. Re-evaluation
is continuous, not triggered by hand. This concept defines how the system
stays fresh without burning cost or creating inconsistent snapshots across
dimensions.

### First-principles motivation

Two things happen in any re-evaluation: **(A) learning what's true about the
scholar right now**, and **(B) judging that truth on a dimension**. Today's
code conflates them. We separate them.

**Why separation is right:**

- **Dimensions share facts.** All four dimensions (Academic Excellence,
  Tech-transfer Experience, Founder Potential, Growth Trajectory) overlap
  heavily on source data — papers, profile, news. Letting each dim re-fetch
  independently pays the rate limit multiple times for data that did not
  change.
- **Consistency across dims.** If two dims read the same scholar at different
  moments, they can disagree about basic facts in the same report. That is a
  credibility bug.
- **Cost asymmetry.** Fetching is cheap; judging is expensive LLM work.
  Bundling them means every re-score pays fetch cost even when nothing changed.
- **Different natural cadences.** Paper feeds weekly; patents monthly; news
  daily. These don't map onto dimension cadences.
- **Auditability.** "This score was computed against snapshot X" is a stronger
  claim than "the agent looked around and decided".

### The 3 layers

```
Layer 3 — Dimension evaluation jobs (per-dim, scheduled by heartbeat)
   reads snapshot → diff vs last eval → LLM triage → maybe re-score
   may REQUEST a targeted refresh via Layer 2 before scoring
   writes one per-dim eval file
                         ▲  request refresh        ▲  read snapshot
                         │                         │
Layer 2 — Refresh jobs (per-source, scheduled by heartbeat)
   SS papers weekly · GS stats weekly · patents monthly
   news daily · crunchbase monthly · on-demand triggers
   dedupes in-flight requests
   writes to fact store, increments snapshot ID
                         ▲  append facts
                         │
Layer 1 — Fact store (per scholar)
   Current state (single JSON, rewritten wholesale):
     profile.json, papers.json, grants.json, patents.json,
     startups.json, channels.json, attributed_metrics.json
   Append-only logs (JSONL):
     events.jsonl, news.jsonl, snapshot_log.jsonl,
     peer_group.jsonl, red_flags.jsonl, narrative.jsonl,
     evaluations/{dim}.jsonl
```

See Concept 5 for the full storage table and the rule that decides which
shape each file takes.

### Load-bearing rule — dim agents CANNOT touch external APIs directly

Dim agents in Layer 3 can only call `trigger_refresh(source, scope, reason)`,
which runs through Layer 2. Layer 2 fetches, writes to the fact store, returns
the new snapshot ID. This is the single discipline that prevents every dim
agent from drifting into its own data silo.

Violating this rule = returning to "every dim fetches its own" with extra
steps. No exceptions.

### Sub-decisions (all resolved)

#### Q4.1 — Diff materiality: LLM triage

A cheap LLM triage call looks at the diff between the current snapshot and the
snapshot this dim last scored against, and decides `material | not_material`.
If not material, the dim run writes only a "checked, no change" log entry and
skips the expensive re-score.

No rule-based short-circuit — we want the system to learn over time what
"material" means per dimension, and hardcoded rules would constrain that.
LLM triage is cheap relative to full re-scoring.

#### Q4.2 — Cross-dim triggers: none

Dimensions are flat and **transparent to each other**. An Academic
Excellence score movement never pushes Growth Trajectory to re-score. Each
dim cares only about itself. Cross-effects emerge naturally on the next
scheduled run of the other dim.

Design intent: ideally dimensions become **more orthogonal** over time as we
iterate on them. Coupling dims would fight that goal. We will experiment with
different dim setups; flat independence is what makes experimentation safe.

#### Q4.3 — Storage layout: per-dim JSONL files

```
data/scholars/{id}/evaluations/
  research_impact.jsonl
  commercialization.jsonl
  career_trajectory.jsonl
  ...
data/scholars/{id}/narrative.jsonl
```

Each dim has its own append-only log. Every run appends one line. The UI
composes the latest-per-dim view at read time (last line of each file).
There is no more "full eval" file. See Concept 5 for storage conventions.

#### Q4.4 — Snapshot retention: keep all

Fact-store snapshots are append-only and never pruned. JSON storage is cheap,
and full history is critical for "why did this score move" debugging and for
reproducing old evals exactly.

#### Q4.5 — Cold start: bootstrap mode

First eval of a new scholar runs Layer 2 in `bootstrap` mode (full fetch from
every source), then runs all Layer 3 dim jobs against the bootstrap snapshot.
Subsequent runs are incremental diffs.

#### Q4.6 — Scheduling: all handled by heartbeat

`services/academic/heartbeat.py` owns all continuous tasks. It becomes the
dispatcher that knows:
- per-source cadences (Layer 2)
- per-dim cadences (Layer 3)
- per-scholar priority overrides (high-priority scholars get tighter cadences)
- in-flight dedupe (if a refresh is already running, a second request
  piggy-backs on the first)

No other module schedules anything. One scheduler, one source of truth for
"when does what run".

#### Q4.7 — Reporting: narrative synthesizer + expandable per-dim mini-reports

Two report artifacts coexist:

1. **Per-dim mini-reports** — each dim eval file contains a short narrative
   explaining that dim's score + evidence. The UI shows these as collapsed
   cards, expandable to read the full mini-report per dim.
2. **Narrative synthesizer job** — a separate Layer 3 job that runs on
   its own cadence, reads the latest per-dim eval for every dim, and appends
   a unified cross-dim narrative report to `narrative.jsonl`. This is what
   VCs read as the "main" report. Sub-scores feed the narrative.

The narrative synthesizer is NOT a dim — it cannot trigger refreshes, it only
reads existing dim evals and facts. It is pure synthesis.

### Eval file shape (per dim)

All IDs are ISO timestamps per Concept 5 Rule 3. `snapshot_id` references an
entry in `snapshot_log.jsonl`; `peer_group_ref` references an entry in
`peer_group.jsonl`.

```json
{
  "id": "2026-04-15T09-12-00Z",
  "dimension_id": "academic_excellence",
  "scholar_id": "...",
  "snapshot_id": "2026-04-15T09-00-00Z",
  "prev_snapshot_id": "2026-04-01T09-00-00Z",
  "peer_group_ref": "2026-04-01T10-00-00Z",
  "triage_decision": "material",
  "triage_reason": "3 new first-author papers since last eval",
  "score": 92,
  "evidence": [ ... ],
  "uncertainty": "low",
  "missing_data": [],
  "mini_report": "…short narrative explaining the score…",
  "questions_for_investor": [
    "What is the current revenue and customer count for InventWood?",
    "What is the status of High T-Tech — still operating, and what traction?"
  ],
  "diff_from_last": {
    "prev_score": 90,
    "delta": 2,
    "drivers": ["3 new first-author papers", "citation bump on 2024 Science paper"]
  }
}
```

**`questions_for_investor` rules:**
- Optional — empty array is fine when the dim is unambiguous
- **Max 3 questions per dim**
- Each question must be **specific to an ambiguity affecting this scholar's
  score** — never generic, never boilerplate
- Only generate questions when the answer would **change the score or
  materially improve confidence**. If the dim is already clear (e.g. the
  scholar is obviously tier-1 on this axis, or obviously not), emit `[]`.
- Questions should be actionable: the investor could ask directly in a
  meeting, send via email, or research to find out

If `triage_decision == "not_material"`, only `id`, `dimension_id`,
`scholar_id`, `snapshot_id`, `prev_snapshot_id`, `peer_group_ref`,
`triage_decision`, `triage_reason` are written. No score change, no LLM
re-scoring cost.

### Unified continuous-task config (resolved — Q4.8)

**Every continuous task is configurable from one file.** Both Layer 2 source
fetchers and Layer 3 dim evaluators are declared in the same config, and
heartbeat reads it on every tick. No hardcoded cadences anywhere.

**Location:** `data/config/continuous_tasks.json` (sits alongside
`dimensions.json`, `heartbeat.json`, `field_archetypes.json`).

**Shape:**

```json
{
  "sources": {
    "semantic_scholar_papers": {
      "layer": 2,
      "enabled": true,
      "default_cadence_days": 7,
      "priority_overrides": {
        "high": 3,
        "low": 14
      },
      "rate_limit_per_minute": 60,
      "on_failure": "retry_next_tick",
      "description": "Fetch papers + citation counts from Semantic Scholar"
    },
    "google_scholar_stats": {
      "layer": 2,
      "enabled": true,
      "default_cadence_days": 7,
      "priority_overrides": { "high": 3, "low": 14 },
      "description": "Fetch h-index, i10, total citations from Google Scholar (SerpAPI)"
    },
    "patents_lens": {
      "layer": 2,
      "enabled": true,
      "default_cadence_days": 30,
      "description": "Patent search via Lens.org"
    },
    "news_web": {
      "layer": 2,
      "enabled": true,
      "default_cadence_days": 1,
      "description": "Targeted news search for scholar name + known startups"
    },
    "crunchbase_startups": {
      "layer": 2,
      "enabled": false,
      "default_cadence_days": 30,
      "description": "Funding / revenue data for scholar-affiliated startups"
    }
  },

  "dimensions": {
    "academic_excellence": {
      "layer": 3,
      "enabled": true,
      "default_cadence_days": 14,
      "required_sources": ["semantic_scholar_papers", "google_scholar_stats", "news_web"],
      "triage_model": "gemini-3-flash-preview",
      "scoring_model": "gemini-3.1-pro-preview"
    },
    "tech_transfer_experience": {
      "layer": 3,
      "enabled": true,
      "default_cadence_days": 7,
      "required_sources": ["patents_lens", "news_web", "crunchbase_startups"],
      "triage_model": "gemini-3-flash-preview",
      "scoring_model": "gemini-3.1-pro-preview"
    },
    "founder_potential": {
      "layer": 3,
      "enabled": true,
      "default_cadence_days": 14,
      "required_sources": ["news_web", "crunchbase_startups", "semantic_scholar_papers"]
    },
    "growth_trajectory": {
      "layer": 3,
      "enabled": true,
      "default_cadence_days": 30,
      "required_sources": ["semantic_scholar_papers", "google_scholar_stats", "news_web"]
    }
  },

  "narrative_synthesizer": {
    "layer": 3,
    "enabled": true,
    "default_cadence_days": 30,
    "model": "gemini-3.1-pro-preview",
    "on_demand_only": false
  },

  "phase_classifier": {
    "layer": 3,
    "enabled": true,
    "default_cadence_days": 60,
    "required_sources": ["semantic_scholar_papers", "google_scholar_stats", "news_web"],
    "triage_model": "gemini-3-flash-preview",
    "classifier_model": "gemini-3.1-pro-preview",
    "writes_to": "peer_group.jsonl",
    "description": "Classify scholar into R1-R4 phase (Axis A academic age + Axis B achievement gates). Non-scoring task — writes classification events, not dim scores."
  }
}
```

### Rules for the config

1. **Heartbeat reads on every tick** — changes take effect without restart.
2. **`enabled: false`** pauses a task without deleting its config.
3. **`required_sources`** links a Layer 3 dim to the Layer 2 sources whose
   data it consumes. Heartbeat uses this to: (a) make sure required sources
   have fresh-enough data before the dim runs, and (b) let a dim request an
   ad-hoc refresh on one of its required sources.
4. **`priority_overrides`** allows per-scholar priority tags to tighten or
   loosen cadences — the scholar record carries `priority: high|normal|low`.
5. **`default_cadence_days` is a floor, not a ceiling.** A dim can run more
   often if a source refresh triggers a manual re-check; it can never run
   less often than the cadence demands.
6. **Unknown keys are rejected** — heartbeat validates against a schema on
   load and refuses to run on a malformed config (fail loud, not silent).
7. **Per-scholar overrides** live in the scholar profile (e.g. "always use
   high priority for this one") — not in the global config.

### Q4.9 (resolved) — Narrative synthesizer is just another configurable task

Subsumed by Q4.8. The narrative synthesizer lives in
`continuous_tasks.json` alongside every other task — same config knobs
(`enabled`, `default_cadence_days`, `on_demand_only`, `model`). No special
case.

### Q4.10 (resolved) — Clean slate, no migration

Existing monolithic `evaluations/YYYY-MM-DD_full.json` files are **deleted**,
not migrated. All scholar dossiers start fresh under the new Concept 6
layout. No migration code, no legacy shim in the frontend, no "read old
format" fallback. Delete and re-bootstrap.

**Implications:**
- All existing scholar evaluations are lost. User has confirmed this is
  acceptable — there is no production data to preserve yet.
- First run of each scholar under the new system goes through Layer 2
  bootstrap mode (see Q4.5) and rebuilds everything from sources.
- Cleanup script should wipe:
  - `data/scholars/{id}/evaluations/` (all contents)
  - `data/scholars/{id}/reports/` (legacy report files)
  - Relevant rows in `scholars` and `scholar_events` tables if stale
- Dimensions config (`data/config/dimensions.json`) can also be rewritten
  as part of this cleanup with the revised per-dim prompts.

---

## Shared Concept 7 — Red flags fact channel (not a dimension)

Business reputation / negative signals are **not a dimension**. They live as
structured facts in Layer 1 and act as global caps across dims in Layer 3.

### Why not a dimension

1. **Not orthogonal.** A reputation dim would overlap hard with every
   existing dimension — fighting the Q4.2 orthogonality goal.
2. **Hard to score 0–100.** Reputation is mostly binary for VC decisions:
   "any red flags?" is a yes/no gate, not a percentile.
3. **Positive reputation is already covered** by existing dims:
   - Board seats / advisory roles → Founder Potential
   - Partnerships / licensing → Tech-transfer Experience
   - Editorial roles / awards / peer recognition → Academic Excellence
   - Media / testimonials → Founder Potential (as the "public presence"
     factor)

   A "reputation" dim would double-count.
4. **Negative reputation behaves globally.** A single retraction or
   misconduct finding can cap every dim, not just one. That is not how
   dim-scoped scores work.

### Layer 1 — `red_flags.jsonl` (event log per Concept 5 Rule 5)

```
data/scholars/{id}/red_flags.jsonl
```

Append-only event log. Three event types:

**`flag`** — a new red flag was detected:
```json
{
  "id": "2026-04-01T10-00-00Z",
  "type": "flag",
  "category": "retraction | misconduct | lawsuit | failed_venture | ethics_concern | clawback | sanctions | export_control | political_risk",
  "severity": "low | medium | high | critical",
  "claim": "2019 Nature paper retracted for image manipulation",
  "source_url": "https://retractionwatch.com/...",
  "source_summary": "Image duplication in Fig 3; editor notice Jan 2024",
  "affected_dimensions": ["research_impact", "field_position"]
}
```

**`dismissal`** — a flag is being dismissed (via chat-driven correction):
```json
{
  "id": "2026-04-10T14-22-00Z",
  "type": "dismissal",
  "target_id": "2026-04-01T10-00-00Z",
  "reason": "user confirmed retraction was corrected and paper reinstated",
  "actor": "user_chat"
}
```

**`resolution`** — a flag's state changes (e.g. lawsuit dismissed, venture recovered):
```json
{
  "id": "2026-06-01T09-00-00Z",
  "type": "resolution",
  "target_id": "2026-04-15T09-00-00Z",
  "new_status": "resolved",
  "source_url": "...",
  "source_summary": "..."
}
```

**Current state = fold over events.** Active flags = all `flag` events minus
any whose `id` appears as a `target_id` in a `dismissal` event. Resolution
events update the flag's status in the projection.

- `affected_dimensions` is **agent-decided per flag**, not hardcoded per
  category. A failed venture with clawbacks affects Commercialization and
  Founder Potential; a clean-exit failed venture may only affect
  Commercialization. The agent justifies the choice in the `claim` field.
- Dismissal flows through the chat-driven correction mechanism (same path as
  phase corrections — Q2). Dismissed flags are preserved in the log for
  audit and excluded from the projection used by scoring.

### Layer 2 — new source in `continuous_tasks.json`

```json
"red_flags_watch": {
  "layer": 2,
  "enabled": true,
  "default_cadence_days": 3,
  "description": "Retraction Watch + PubPeer + web search for misconduct/lawsuits/failed ventures/ethics concerns"
}
```

3-day cadence because red flags are the **one signal where latency matters**
— a freshly-retracted paper should surface in under a week, not wait weeks
for the next Academic Excellence run.

### Layer 3 — severity → dim caps

Every dim folds `red_flags.jsonl` into its current-state projection (active
flags = all `flag` events minus those whose `id` appears as a `target_id` in
a `dismissal` event, with `resolution` events applied) before scoring, then
applies caps to dims listed in `affected_dimensions`:

| Severity  | Effect on affected dims                                        |
|-----------|----------------------------------------------------------------|
| low       | Note in evidence, no cap                                       |
| medium    | Cap at 85                                                      |
| high      | Cap at 70, mark `uncertainty: high`                            |
| critical  | Cap at `<50`, narrative synthesizer flags prominently          |

Multiple flags stack the cap downward — the lowest cap wins. Unaffected dims
are untouched.

### Narrative synthesizer rule

Red flags surface at the **top of every synthesized report**, regardless of
whether they moved any score. Critical-severity flags get a prominent
warning block. Dismissed flags are not shown.

### Category list (initial)

- `retraction` — paper retractions (Retraction Watch, journal notices)
- `misconduct` — research misconduct findings (ORI, PubPeer, investigations)
- `lawsuit` — active litigation against the scholar or their ventures
- `failed_venture` — shut-down startup, especially with clawback / investor losses
- `ethics_concern` — IRB violations, undisclosed COI, IP disputes
- `clawback` — grant or funding clawback
- `sanctions` — personal or institutional sanctions (OFAC, entity lists)
- `export_control` — export control / dual-use technology concerns
- `political_risk` — country-level political exposure that affects commercial viability

Categories are a config list, extendable without schema changes.

---

## Shared Concept 8 — Relative traction rubric (helper)

Used by Commercialization and Founder Potential when judging a startup's or a
scholar's commercial record. Treats traction the same way we treat citations:
relative to peers.

- **Startup traction metric** = `(funding raised OR revenue) / years since founding`
- Percentile-mapped against typical deep-tech startups **in the same field**
- **Unknown funding/revenue** = cannot score the startup → caps parent dimension
  at ~70 and listed in `missing_data`

Example anchors (deep-tech materials, will vary by field):

| Traction ($/yr) | Band        |
|-----------------|-------------|
| < $1M           | weak        |
| $1–5M           | median      |
| $5–20M          | top quartile |
| $20M+           | top 5%      |

Agent is instructed to build analogous anchors for the scholar's field when
scoring; hardcoded numbers are only an example.

---

## Decision log (all resolved)

All framework-level questions are closed. Recorded here so future readers
can trace the reasoning.

- **Q1** — Two-axis R1–R4 with R3a/b/c split (aligned with EU R1–R4 standard and ERC/NIH practice).
- **Q2** — No direct phase override. Corrections flow through chat-driven agent notes (separate design).
- **Q3** — Narrowest defensible subfield; agent must name 3–5 peer examples; cohort target ~10+.
- **Q4** — 3-layer continuous monitoring architecture (Concept 6). All sub-decisions Q4.1–Q4.10 resolved inside Concept 6.
- **Q5** — Business reputation is NOT a dimension. Red flags live as a Layer 1 fact channel (Concept 7) with per-severity dim caps.
- **Q6** — Eval schema is defined by Concepts 5 + 6 + 7. Per-dim JSONL eval files reference `peer_group_ref` + `snapshot_id` (both ISO timestamps); `evidence`/`missing_data`/`uncertainty` live inline; `attributed_metrics` lives in the fact store. Phase classification is its own Layer 3 non-scoring task (`phase_classifier`) writing to `peer_group.jsonl`.
- **Q7** — No peer-group splits by geography or institution tier. `peer_group.jsonl` carries a `context_modifiers` block (institution + tier + resource level, geographic region, data availability). Only `data_availability: low` touches scores (bumps uncertainty); the rest is observable context for the narrative synthesizer and UI.
- **Q8** — Hybrid G2 grant list: hardcoded canonical for US/EU, agent-inferred for non-US/EU.

---

## Per-dimension rewrites

**Status: first-pass drafts.** These apply the shared framework (Concepts 1–8)
with dimension-specific anchors, evidence types, and anti-patterns. We will
review and refine each dimension one-by-one after this draft pass.

Each dimension section is **completely self-contained and transparent to
the other dimensions**. A dim agent reads only its own section and has no
awareness that other dimensions exist. This lets each dim run independently
on its own schedule, and lets us iterate on dimensions without coupling
their prompts to each other. The only external reference any dim makes is
`required_sources`, which points at the source fetchers in
`continuous_tasks.json`.

**Design rule:** never reference other dimensions by name inside a dim's
prompt. If a distinction needs to be made (e.g. "don't confuse research
excellence with founder capability"), make it conceptually, not by dim name.

Each section has the same structure:

- **What it measures** — single-sentence definition
- **Core principle** — how to think about scoring this dim
- **VC framing** — how to calibrate top bands (billion-dollar outcome) and
  the "excellence not weakness" mindset
- **Required sources** — Layer 2 fetchers this dim depends on
- **What to look for** — signals to evaluate, principle-driven
- **What to weight** — interpretive principles, no formulas
- **Percentile anchors** — what each band looks like for this dimension
- **Handling unknowns** — how to downgrade or cap when data is thin
- **Anti-patterns** — known false positives (stated conceptually, without
  naming other dimensions)
- **Mini-report structure** — what the `mini_report` field must contain

**Output convention — optional key questions for investor:**

Every dim output may include up to **3 questions_for_investor** — specific
questions whose answers would change the score or materially improve
confidence. Generate questions only when there is real ambiguity the data
could not resolve; emit an empty list when the dim is already clear. Each
question must be specific to this scholar, not boilerplate. Questions
should be phrased as things the investor could ask the scholar directly
or research to find out. Cap at 3 per dim.

---

### Dimension 1 — Academic Excellence

**What it measures:** How strong is this scholar as a scientist in their
subfield, judging both the work they have produced and the recognition the
field has given them?

**Core principle:** Academic Excellence is a **holistic judgment of
scientific contribution + peer standing**. A scholar is excellent if the
field cites, builds on, and socially recognizes their work, and if they
personally drove that work (rather than merely being listed on it). Judge
these aspects together; surface divergences when they exist.

**VC framing — calibrate top bands against billion-dollar outcome
potential, not mere competence.** A score of 95 means "this scholar's body
of work could plausibly anchor a billion-dollar deep-tech outcome" — not
"excellent among peers". Look for *signs of excellence*, not *lack of
weakness*: one exceptional signal (a field-defining paper, a dominant role
the field cannot work around) can carry a band even when other signals are
absent.

**Required sources:** `semantic_scholar_papers`, `google_scholar_stats`,
`news_web`.

**What to look for:**

- **Attributed scientific contribution.** Raw citation count is misleading
  — a scholar with 100k citations concentrated in middle-author consortium
  papers has very different standing than one with 30k citations where the
  top papers are first/last-author work. Use author-position-weighted
  citation metrics from the fact store (`attributed_metrics.json`), which
  encode authorship ownership. The question: what has this person *driven*,
  not what has their name appeared on?
- **Field recognition.** Named awards, editorial roles (editor, associate
  editor, editor-in-chief of top-tier journals), society fellowships, named
  lectures, keynote density at flagship venues. The question: how has the
  field *socially validated* this person?
- **Collaboration quality, not count.** Who they work with matters far
  more than how many co-authors they have. A scholar whose frequent
  collaborators are themselves top-of-field is embedded in the right
  network; one with sprawling low-quality co-author lists is not. Cross-
  institution and cross-disciplinary reach are positive signals when
  combined with ownership; consortium membership alone is not.
- **Intellectual ownership trajectory.** Healthy scholars migrate from
  first author (as trainees) to last author (as PIs) over time. A scholar
  stuck as middle author on other people's papers is a red flag regardless
  of raw numbers.
- **Signature work.** Is there a recognizable intellectual thread? A
  scholar with a clear signature problem advanced over years usually
  outranks one with scattered output at similar citation counts.

**What to weight — principles, apply case-by-case:**

- **Authorship ownership over raw numbers.** Always. First and senior
  authors carry the work; middle authors are collaborators; deep middle or
  consortium positions are near-zero signal. The fact store provides
  pre-computed authorship-weighted metrics — use them.
- **Quality beats count.** One first-author paper the subfield cannot stop
  citing outranks twenty mid-tier papers.
- **Recognition should match contribution.** Awards without matching
  citation evidence are ceremonial or political — note them, don't stack
  them. Citations without matching recognition suggest an outsider or
  under-recognized scholar — flag this as a divergence worth investigating.
- **Concentration is a warning sign.** If most of a scholar's citation
  impact comes from a single paper, cap the score unless that paper is
  genuinely field-defining (and justify why). A brilliant one-hit is not
  the same as sustained excellence.
- **Phase-sensitive.** The same h-index means very different things early
  vs late in a career. Compare against peers at the same career phase, not
  absolute numbers.
- **Skeptical of volume without direction.** A scholar with 300 scattered
  papers is a weaker signal than one with 60 papers concentrated on a
  signature problem.
- **Skeptical default.** If the authorship trail is thin or ambiguous,
  score low. Do not fill gaps with optimism.

**Percentile anchors** — against phase + subfield peers:

- `<50` — below-median authorship-weighted output for phase; no notable
  recognition; insular or low-quality collaboration pattern
- 50–74 — solid authorship-owned output; some recognition (society
  membership, associate editor role); healthy collaboration with recognized
  peers
- 75–89 — multiple influential first/last-author papers; named awards or
  editorial leadership; strong collaborator network
- 90–94 — field-level recognition (keynote density, society fellowship,
  multiple named awards); consistently-cited signature work; central node
  in the collaboration graph
- 95–98 — subfield-defining work AND dominant recognition (editor-in-chief
  of top-tier journal, top field prize, society fellow) AND breadth of
  top-of-field collaborators. Their body of work could plausibly anchor a
  billion-dollar deep-tech outcome.
- 99 — field-defining figure (Nobel-adjacent)

**Handling unknowns:**

- **Single-paper concentration** → cap the score unless the paper is
  clearly field-defining, and justify in the mini-report
- **Inflation flags** from the fact store → discount accordingly and
  explain
- **Co-first / co-corresponding unverified** → mark `uncertainty: medium`
- **Data availability low** (from the scholar's `peer_group.jsonl` context
  modifier) → bump `uncertainty` one level
- **Recognition without citation backing** → flag as divergence; treat as
  ceremonial, not primary evidence
- **Citations without recognition** → flag as divergence; may indicate an
  outsider or under-recognized scholar, worth further investigation

**Anti-patterns:**

- Scoring on raw citation count (use authorship-weighted metrics)
- Treating consortium-paper co-authorship as personal contribution
- Awarding high scores based on single-paper inflation
- Ignoring divergences between citation impact and social recognition
- Treating h-index out of phase context
- Rewarding venue prestige (Nature, Science) without checking whether the
  scholar was the driver or a middle author
- Counting large author lists as "strong collaboration" when most of them
  are consortium membership
- Missing that a central-node collaboration position is qualitatively
  different from high co-author count
- Accepting a single recent award as recognition when no citation evidence
  supports it
- Padding the score with volume when the authorship pattern is thin

**Mini-report structure:**

1. **Authorship-owned contribution** — authorship-weighted citation
   summary, first/last-author h-index, top first/last-author papers with
   venues
2. **Recognition inventory** — named awards, editorial roles, fellowships,
   keynotes
3. **Collaboration profile** — top collaborators with their own standing,
   cross-institution and cross-disciplinary reach
4. **Divergences** — any mismatch between citation impact and social
   recognition, flagged explicitly
5. **Score justification** — which band, why, against which phase peers
6. **`questions_for_investor`** — optional list of up to 3 concrete
   questions whose answers would change the score or improve confidence;
   emit `[]` if the dim is clear

---

### Dimension 2 — Tech-transfer Experience

**What it measures:** What commercial tech-transfer has this scholar's
research produced, and is that output validated by the market? This is the
historical track record of moving research to market.

**Core principle:** Score against **commercial peers, not academic peers**.
Before scoring, name a few comparable deep-tech ventures in the same
commercial segment — these are the anchor points. Without a named
commercial cohort, you cannot justify a percentile claim.

**VC framing — calibrate top bands against billion-dollar outcome
potential, not mere "has shipped something".** A score of 95 means "this
scholar's commercial footprint could plausibly anchor a billion-dollar
deep-tech outcome" (or already has). Look for *signs of excellence* — one
exceptional outcome (a successful strategic acquisition, an IPO, a widely-
licensed IP family) can carry a band even when other categories are absent.

**Required sources:** `patents_lens`, `news_web`, `crunchbase_startups`,
`semantic_scholar_papers`.

**What counts as commercial output:**
- **Ventures** — startups founded, operating roles (not "advisor")
- **IP** — patents, licensing agreements with named licensees
- **Market validation** — customer revenue, exits, strategic acquirer
  interest, regulatory milestones
- **Partnerships** — named industry partnerships with concrete deliverables
  (not joint papers)

**What to weight** — these are principles, apply them case-by-case:

- **Revenue > investor funding > grants.** Customers paying is the strongest
  signal. Investor equity is second. Government R&D grants (SBIR, ARPA-E,
  etc.) are technical promise, not market validation — they never qualify as
  primary evidence here.
- **Quality > count.** One startup with strong traction outranks five
  dormant ventures. One heavily-cited, broadly-licensed patent outranks
  twenty university filings. Patent quality depends on the combination of
  inventor position, assignee, family breadth, forward citations, and
  licensing — judge holistically.
- **IP ownership structure matters.** A patent assigned to the scholar's
  own spinout company signals real commercial intent. A patent assigned to
  the university's tech-transfer office where the scholar is one of many
  named inventors is a much weaker signal — the scholar may have no
  control over the IP and no stake in its commercialization. Check
  assignee and inventor order before weighting.
- **Verified > claimed.** Third-party data (Crunchbase, SEC filings, named
  customers) is strong. CV self-claims, press releases, and "stealth mode"
  are weak.
- **Recent > historical.** A 2010 exit still counts as commercial output,
  but top-percentile scores need ongoing commercial engagement. A decade-old
  win can only reach 95+ if it was truly categorical.
- **Exceptional carries alone.** One outstanding outcome can justify a high
  score even when other categories are absent. One great thing beats many
  moderate things stacked.
- **Skeptical default.** If evidence is thin, the score is low. Do not fill
  gaps with optimism.

**Percentile anchors** — against the **commercial cohort**, not academic peers:

- `<50` — no commercial output, or all ventures have fully unknown state
- 50–74 — above-median commercial engagement for the cohort
- 75–89 — strong commercial presence: clearly-validated output visible to
  the market
- 90–94 — top-decile within the cohort
- 95–98 — top-5%: exit at scale, IPO, dominant IP backing a major product,
  or equivalent
- 99 — category-defining commercial impact (rare)

Judge which band the scholar fits **by comparing to the named cohort**. If
two ventures in the cohort are at $50M+/yr and the scholar's venture is at
$5M/yr, the scholar is not in the top decile of that cohort regardless of
how impressive $5M sounds in isolation.

**Handling unknowns** — "don't know" is not the same as "nothing":
- All named ventures fully unknown → cap at **60**
- Some verified, some unknown → cap at **75**, list unknowns in `missing_data`
- No named commercial cohort → `uncertainty: high`, cap at **75**

**Anti-patterns:**
- Counting raw patent number without assessing patent quality or IP
  ownership structure
- Treating university-owned patents where the scholar is one of many
  inventors as strong commercial evidence
- Treating university press releases as venture traction
- Accepting scholar CV self-claims without independent verification
- Treating SBIR / ARPA-E / government R&D grants as commercial validation
- Using narrative strength ("this is a breakthrough!") as a substitute for
  evidence of market adoption
- Inferring traction from startup age alone ("been around 5 years, must be
  doing well")
- Counting advisory seats as operating roles
- Over-weighting vague "partnership with BigCorp" announcements without
  concrete deliverables
- Treating academic-leadership roles ("Founding Director of Center for X")
  as commercial engagement — these are institutional, not commercial
- Importing citation impact or research prestige as commercial evidence;
  academic brilliance is not commercial traction

**Mini-report structure** (what the `mini_report` field must contain):
1. **Commercial cohort** — 3–5 named comparables with one-line traction state
2. **Output inventory** — ventures, valuable patents, licensing deals,
   partnerships, regulatory milestones
3. **Traction verdict per venture** — one qualitative line each, with cited
   evidence
4. **Gaps** — what couldn't be verified
5. **Score justification** — which band, why, against which cohort members
6. **`questions_for_investor`** — optional list of up to 3 concrete
   questions whose answers would change the score or improve confidence;
   emit `[]` if the dim is clear

---

### Dimension 3 — Founder Potential

**What it measures:** If this scholar became a founder or co-founder of a
company in their domain, how likely would that company succeed with them in
a founding role?

**Core principle:** Predict, don't audit. Founder Potential is about future
commercial success probability, not a checklist of past experiences. The
strongest predictors come from founder-market fit, determination, commitment,
and team-attracting ability — not from counting past founding attempts.

**Critical framing note:** The best model for most academic founders is
**scientific co-founder paired with an experienced business co-founder**,
not professor-as-CEO. Research shows academic-inventor-CEOs tend to raise
less capital and IPO less often than hybrid teams. A scholar's willingness
to partner with a strong business co-founder is itself a positive signal
(self-awareness). **Do not require CEO-track evidence to score well.**
Conversely, fresh PhDs without prior experience can outperform serial
founders in the right spinout context — lack of prior founding is not a
disqualifier.

**VC framing — calibrate top bands against billion-dollar outcome
potential.** A score of 95 means "this scholar could plausibly anchor a
billion-dollar deep-tech outcome as a founder" — not merely "competent
operator". Look for *signs of excellence*, not *lack of weakness*: one
exceptional signal (deep domain dominance, visible grit on a hard problem,
a track record of attracting top talent) can carry a band even when other
signals are absent.

**Required sources:** `news_web`, `crunchbase_startups`,
`semantic_scholar_papers`.

**Core signals** — judge holistically, not as a checklist:

1. **Founder-market fit.** Does the scholar understand the *commercial*
   market for their research, not just the science? Signs: speaks about
   customers, unit economics, regulatory paths, competitive landscape,
   second-order market effects. A scholar who only discusses scientific
   novelty when asked about commercialization has low founder-market fit
   regardless of scientific brilliance. **Top signal per VC literature.**
   **Domain dominance is its own founder-market fit signal** — if the
   scholar is genuinely the top person in the world working on a specific
   technology, they are structurally the right founder for any company
   built around that technology, even without explicit commercial framing.
   Do not penalize a world-dominant domain expert for not having "business
   vocabulary"; the dominance itself is the fit.

2. **Determination / conviction.** The #1 predictor in Paul Graham / YC
   framework. Does this person push through obstacles on hard problems?
   Signs: sustained multi-year commitment to a hard research agenda, visible
   grit through failed experiments or funding gaps, willingness to take
   contrarian positions, public framing of the work as something they *have
   to* do. Detect through sustained focus and obstacle-overcoming behavior.

3. **Commitment / bridging signals.** Actual willingness to leave the lab,
   not just talk about it. Sabbaticals at startups, running a lab and a
   company simultaneously, personal capital at risk, explicit public framing
   of commercialization as a goal. Full professors with tenure who have
   never bridged are weak bets regardless of research quality. **Near-
   necessary for top percentiles.**

4. **Team-attracting ability.** Can the scholar bring great people with
   them? Both VC literature and spinout research flag this as a top-tier
   predictor. Evidence: notable lab alumni trajectories (alumni who went on
   to found or operate companies), high-quality co-founders on past
   ventures, named advisors and mentors, quality of recent co-authors. A
   scholar whose lab consistently produces people who go do interesting
   things is showing mentorship and network quality that credentials don't
   capture.

5. **Resourcefulness / execution range.** Have they done work beyond
   research? Running a large academic lab is running a small business —
   grants, hiring, firing, budgets, project management, external comms. Some
   academics do all this well; others outsource everything to admins. Look
   for signs of wider execution capability: navigating political friction,
   turning around struggling projects, building infrastructure that didn't
   exist.

6. **Public presence & reachability.** Can this scholar tell a compelling
   story to a non-academic audience? Are they visible and reachable as a
   potential founder? This matters both as a sourcing signal (can VCs and
   operators find them?) and as a founder-relevant skill (can they pitch
   vision, frame products, attract attention, fundraise?). Signs: personal
   website or lab page with active updates, active presence on the platforms
   their field uses (Twitter/X for ML, LinkedIn for biotech, etc.), media
   features in named outlets, public talks at non-academic venues (TEDx,
   podcasts, industry conferences), op-eds or popular writing.
   **Critical distinction:** this scores *founder-relevant* public presence.
   A scholar with a huge Twitter following who cannot frame a product story
   scores low on this factor. A scholar with no social media but who has
   given a memorable product-framing TEDx talk scores high. Visibility
   alone is not the point — founder-relevant communication is.

7. **Prior founding / operating experience (bonus, not required).** Having
   done it is a strong positive signal, and failures count as learning. Not
   having done it is NOT a disqualification — a fresh PhD can be a great
   founder. Use prior founding to refine judgment on the above signals; never
   use it as a gatekeeper.

**What to weight:**

- **Founder-market fit is the top signal.** For a deep-tech scholar, this
  means commercial-market understanding, not just technical depth.
- **Determination beats credentials.** A modestly-pedigreed scholar with
  obvious grit outranks a decorated scholar with visible comfort.
- **Commitment signals are near-necessary at top percentiles.** You cannot
  reach 90+ without evidence the scholar will actually leave the lab. Pure
  academics with no bridging cap at 80.
- **Team-attracting ability is a silent but strong predictor.** Weight
  heavily.
- **Skeptical default.** Research brilliance alone does not imply founder
  potential. Pure academic tracks with no commercial signal score `<50`.

**Percentile anchors** — against deep-tech founder peers:

- `<50` — pure academic, no commercial framing, no bridging, no evidence of
  founder-market fit
- 50–74 — clear interest in commercialization, early founder-market-fit
  signals, some team-attracting evidence, but no real commitment yet
- 75–89 — meaningful founder-market fit + visible commitment to bridging +
  team-attracting track record
- 90–94 — proven commitment (operator role, sabbatical, dual roles) + strong
  team signal + deep founder-market fit, with or without prior founding
- 95–98 — active serial operator in the ecosystem, proven ability to attract
  great co-founders and build companies, deep market understanding
- 99 — iconic founder track record that itself anchors a VC thesis

**Handling unknowns:**

- No commercial framing or bridging evidence found → `<50` with
  `missing_data` note
- Early-career scholar with limited signal either way → `uncertainty:
  medium`, cap at 75
- Scholar deliberately low-profile → note in mini-report; score on whatever
  signal exists; don't penalize privacy

**Anti-patterns:**

- Treating prior founding as the dominant signal — founder-market fit
  matters more
- Requiring CEO-track evidence — research shows hybrid scientific+business
  co-founder teams are the best model
- Penalizing fresh PhDs for lack of experience — research shows they can
  outperform
- Treating academic titles (dean, chair) as founder experience
- Treating polished public speaking or media presence as founder capability
- "Founder in name only" — listed as co-founder while others did the actual
  operating work
- Self-styled titles in stealth mode without corroboration
- Scoring on research brilliance or pedigree instead of founder-relevant
  signals
- Counting advisory-seat inflation as network depth
- Using "pure professor" as either gate-up or gate-down — require actual
  signal either way
- Treating raw social-media follower count as founder potential (vanity
  metric)
- Confusing academic visibility (talks at domain conferences, citation
  counts) with founder-relevant visibility (product story-telling, pitching,
  non-academic media)

**Mini-report structure:**

1. **Founder-market fit read** — qualitative assessment of commercial-market
   understanding
2. **Commitment signals** — bridging evidence, sabbaticals, active engagement
3. **Team-attracting signal** — alumni trajectories, co-founder quality, lab
   mentorship signals
4. **Determination / resourcefulness** — observable signs of grit and wider
   execution
5. **Public presence** — personal site, socials, non-academic media, public
   talks, and qualitative read of founder-relevant communication skill
6. **Prior founding/operating** — if present, named and outcome-shaped
7. **Score justification** — which band, why, what's carrying or blocking
8. **`questions_for_investor`** — optional list of up to 3 concrete
   questions whose answers would change the score or improve confidence;
   emit `[]` if the dim is clear

---

### Dimension 4 — Growth Trajectory

**What it measures:** Is this scholar's overall profile accelerating,
plateauing, or declining? This is the derivative dimension — it scores the
*slope* across academic work, tech-transfer activity, and founder signals.

**Core principle:** Growth Trajectory answers "where is this person
going?" It measures the **slope of the scholar's overall profile** —
scientific output, commercial activity, and founder-relevant signals —
over the recent past. Flat trajectory is fine for established field
leaders (already at the top); concerning for early-career scholars who
should be climbing. Declining is concerning regardless of phase.

**VC framing — trajectory matters because investments pay off over years,
not snapshots.** A scholar accelerating into their peak is a better bet
than a declining star. Calibrate top bands against *category-shifting
velocity*, not mere activity: a 95 means "this scholar's trajectory is
compressing what would normally take a decade into a year or two". Look
for signs of acceleration across multiple axes, not single-event spikes.

**Required sources:** `semantic_scholar_papers`, `google_scholar_stats`,
`news_web`.

**What to look for** — judge holistically across three axes of a scholar's
recent activity:

- **Scientific momentum** — year-over-year growth in authorship-weighted
  citations, recent high-impact papers with first/last-author position,
  author-position trajectory (first → last migration is healthy; stuck
  middle-author is a red flag), recent major grants, new awards, new
  editorial roles.
- **Commercial momentum** — new patents (granted or filed) in the last 24
  months, new ventures founded or new rounds closed, new traction signals
  from existing ventures, new licensing deals, new named industry
  partnerships.
- **Operator/bridging momentum** — new commitment signals (sabbatical
  announcements, new operator roles, new public framing around
  commercialization), evolving lab-alumni trajectories (graduates moving
  into founder or operator roles), new public presence signaling founder
  readiness.

**What to weight:**

- **Multi-axis acceleration > single-axis spike.** One big recent paper is
  a spike, not a trajectory. A scholar with forward signals across academic
  + tech-transfer + founder axes is on a real trajectory.
- **Phase-sensitive interpretation.** Flat is OK at R4 (already a leader),
  concerning at R3a (should be climbing). Declining is always concerning.
- **Recency weight.** The last 24 months matter more than the last 5 years.
- **Skeptical of one-off events.** A single prestigious award doesn't make
  a trajectory; a pattern of growing recognition does.
- **Trajectory cannot exceed what the scholar's current-state evidence
  supports.** If there is no meaningful scientific, commercial, or founder-
  relevant activity at all, there is nothing to have a trajectory on. A
  recent step change must be visible in real output, not inferred from a
  single announcement.

**Percentile anchors** — against phase peers:

- `<50` — flat or declining output, no new forward signals across any axis,
  stuck author position
- 50–74 — steady with some forward signals (new grant, continued output,
  group growth)
- 75–89 — clear acceleration on at least two axes (citation growth + new
  commercial signal, for example)
- 90–94 — strong acceleration across multiple axes; disproportionate recent
  wins vs phase peers
- 95–98 — rocket trajectory; recent 3-year window visibly exceeds prior
  5-year baseline across multiple axes
- 99 — once-a-generation trajectory (transformation moment, e.g. Hassabis
  post-AlphaFold)

**Handling unknowns:**
- **<3 years of data** (very early career) → `uncertainty: high`, cap at 85;
  trajectory needs a baseline
- **`data_availability: low`** → bump `uncertainty`

**Anti-patterns:**
- Scoring on level (that is D1–D3's job) rather than slope
- Treating one spike paper as a trajectory
- Ignoring stuck author-position as a negative signal
- Rewarding new-hire count without corresponding output growth
- Missing declining trajectories — flat output in R3a is a quiet red flag
- Over-weighting a single award or grant as a trajectory change
- Treating a recent pivot to a new topic as "acceleration" before there is
  any output evidence

**Mini-report structure:**
1. **Scientific momentum read** — citation growth, new papers, position
   trajectory
2. **Commercial momentum read** — new patents, ventures, deals in last
   24 months
3. **Operator/bridging momentum read** — new commitment signals, evolving
   alumni trajectories
4. **Overall direction** — accelerating / steady / declining, with
   confidence level
5. **Score justification** — which band, why, and which axes are
   contributing
6. **`questions_for_investor`** — optional list of up to 3 concrete
   questions whose answers would change the score or improve confidence;
   emit `[]` if the dim is clear

---

## (Old dim drafts removed)

The following original dimensions were merged or absorbed during the
4-dimension MECE redesign and are no longer scored independently:

- **Research Impact** → merged into **D1 Academic Excellence** (as the
  attributed-citation core)
- **Field Position** → merged into **D1 Academic Excellence** (as the
  recognition component)
- **Collaboration Strength** → merged into **D1 Academic Excellence** (as
  the network component)
- **Career Trajectory** → renamed to **D4 Growth Trajectory**
- **Public Profile** → absorbed into **D3 Founder Potential** as the
  "Public presence & reachability" factor

The draft text for each of these is deleted from the doc to prevent drift.
The merged content is preserved inside the new dimension sections.

---


## Change log

- **2026-04-08** — initial draft. Shared concepts 1–5 captured; 7 open questions
  outstanding; no per-dimension rewrites yet.
- **2026-04-08 (pm)** — major iteration. Added Concept 6 (3-layer continuous
  monitoring architecture), Concept 7 (red flags fact channel), Concept 8
  (storage conventions — JSONL for logs, JSON for state, ISO-timestamp IDs,
  per-scholar write lock, inline attachment summaries, event-projection for
  mutability, four shared file_utils primitives). Resolved Q1, Q2, Q3, Q4
  (with sub-decisions Q4.1–Q4.10), Q5, Q6, Q8. Added `phase_classifier` as a
  Layer 3 non-scoring task.
- **2026-04-08 (pm, final)** — Resolved Q7. Geography and institution tier are
  NOT peer-group splits; instead `peer_group.jsonl` carries a
  `context_modifiers` block (institution name/tier/resource level, geographic
  region, data availability). Only `data_availability: low` touches scores
  (bumps uncertainty). Shared framework is fully locked — ready to begin
  per-dimension rewrites.
- **2026-04-08 (per-dim first pass)** — Drafted all 7 dimension sections
  (Commercialization, Founder Potential, Research Impact, Field Position,
  Career Trajectory, Collaboration Strength, Public Profile) using a
  consistent structure: what it measures, scored-against, required sources,
  percentile anchors by band, primary vs supporting evidence, dimension-
  specific caps, cross-dimension interactions, anti-patterns. Drafts are
  first-pass — ready for dim-by-dim review and refinement.
- **2026-04-09 (finalization pass)** — Made each dimension fully
  self-contained and transparent to the others: deleted all
  "Cross-dimension interactions" sections; rewrote anti-patterns to use
  conceptual distinctions (e.g. "citation impact is not commercial
  traction") rather than naming other dimensions. Stripped formula-like
  overfitting from D1 Academic Excellence: removed the exact
  author-position weight table, the attribution-ratio threshold, the
  concentration %, the explicit `attributed_metrics.json` field list, and
  the h-index examples — replaced with principles the LLM applies
  holistically. Added "VC framing" block to each dim (billion-dollar
  outcome calibration + "excellence not weakness" mindset) inline so each
  dim remains self-contained. Added IP-ownership-structure principle to D2
  Tech-transfer Experience. Strengthened domain-dominance as a first-class
  founder-market-fit signal in D3 Founder Potential. Updated per-dim
  section intro to document the self-contained design rule. This
  finalizes the 4-dim design pending live-agent testing.
- **2026-04-09 (MECE redesign, 7 → 4 dimensions)** — Adopted 4-dimension
  MECE design after reviewing VC and bibliometric literature (no
  professional framework uses >5 dims). Merged Research Impact + Field
  Position + Collaboration Strength into **D1 Academic Excellence**;
  renamed Commercialization to **D2 Tech-transfer Experience** to make the
  historical-track-record framing explicit; kept **D3 Founder Potential**
  and absorbed Public Profile into it as the "Public presence &
  reachability" factor; renamed Career Trajectory to **D4 Growth
  Trajectory** with explicit framing as a cross-cutting synthesis of D1/D2/
  D3 history. Deleted the obsolete Research Impact, Field Position, Career
  Trajectory, Collaboration Strength, and Public Profile sections.
  `continuous_tasks.json` example updated to reflect 4-dim layout. D1 and
  D4 drafted from scratch using the MECE principle; D2 and D3 preserved
  with renames and cross-dim references updated.
- **2026-04-08 (coherence pass)** — Thorough review of the whole doc. Bugs
  fixed: (1) renumbered concepts into natural reading order 1–8 (Storage
  moved from 8 to 5; Relative traction moved from 5 to 8); (2) all
  `snap_XXXXX` IDs replaced with ISO timestamps per Concept 5; (3)
  `peer_group_ref` now correctly points at `peer_group.jsonl` entries instead
  of `profile.json`; (4) narrative path fixed from stray `.md` files to
  `narrative.jsonl`; (5) red-flags filter language updated from stale
  `status != dismissed` to the event-projection fold; (6) Concept 6 Layer 1
  file list synced with Concept 5 storage tables; (7) `grants.json`,
  `patents.json`, `startups.json`, `news.jsonl` added explicitly to Concept 5
  tables; (8) Concept 2 cleaned — removed duplicate `peer_group` JSON block,
  merged Q3 field-granularity content inline, relocated Q2 as "Design note";
  (9) stale "Open questions on this concept" subsections removed across
  Concepts 4, 5, 7; (10) consolidated open-questions list rewritten as a
  "Decision log". No semantic design changes — this pass is
  coherence only.
