# Academic Tracking — Randomized E2E Test Protocol (v2)

## Purpose

Validate the Academic Tracking v2 pipeline end-to-end using real API calls (Gemini + Semantic Scholar + SerpAPI + Google Search) against a pool of 20 known scholars in `academic_test_scholars.json`. Each test run randomly selects 4 scholars and varies which URLs are submitted, simulating realistic frontend usage where the user may provide only a homepage, only a Google Scholar link, or a partial subset of known URLs.

**v2 architecture**: 3-layer continuous monitoring (identity resolution → Layer 2 source fetchers → Layer 3 dim evals + phase classifier + narrative synthesizer). 4 MECE dimensions (Academic Excellence, Tech-transfer Experience, Founder Potential, Growth Trajectory). Evaluations are per-dim JSONL files, not monolithic JSON. Reports are narrative.jsonl, not reports/*.md.

## Test Pool

The 20 scholars span:
- **Career stages:** established (5), mid (8), early (7)
- **Fields:** CS/AI, Mathematics, Physics, Neuroscience, Astrophysics, Materials Science, Economics, Marine Biology, Computational Biology, Computational Chemistry, Environmental Science
- **Geography:** US, Canada, UK, Germany, Switzerland

Each scholar entry includes `expected` ground-truth values (`google_scholar_id`, `min_h_index`, `min_papers`) for validation.

## Test Infrastructure

- Spin up an isolated FastAPI server with a fresh temporary SQLite DB (same pattern as `test_academic_e2e.py`)
- Use a dedicated port (e.g., 8879) to avoid conflicts with dev server or other tests
- All API calls go through HTTP (httpx) to the test server, matching the real frontend flow
- Pipeline timeout: **180 seconds** per scholar

## Per-Run Procedure

### 1. Scholar Selection (random 4 of 20)

```
random.sample(scholars, 4)
```

### 2. URL Strategy (random per scholar)

For each selected scholar, randomly choose one of these strategies:

| Strategy | What is sent as `input_url` | `other_urls` |
|---|---|---|
| **single-homepage** | First non-Google-Scholar URL | _(empty)_ |
| **single-gs** | Google Scholar URL (if available) | _(empty)_ |
| **partial** | First URL | Random subset (1 to n-1) of remaining URLs, comma-separated |
| **all** | First URL | All remaining URLs, comma-separated |

If a scholar has only 1 URL, always use that as `input_url` with no `other_urls`.

### 3. Scholar Creation

```
POST /academic/scholars
{
  "name": "<scholar name>",
  "urls": ["<chosen url>", ...additional urls if partial/all strategy...],
  "tracking_priority": "high",
  "tags": ["<field>", "<career_stage>"]
}
```

Assert: status 200, response has `id` and `status == "active"`.

### 4. Pipeline Execution

```
POST /academic/scholars/{scholar_id}/evaluate
```

Assert: status 200. This triggers `bootstrap_scholar` as a background task (identity resolution → Layer 2 sources → phase classifier → Layer 3 dim evals → narrative synthesizer). Scholar status transitions to `"evaluating"`.

### 5. Polling (max 180s)

```
GET /academic/scholars/{scholar_id}
```

Poll every 5 seconds. Record elapsed time. Pipeline is done when `status != "evaluating"` (expect `"active"` on success).

## Validation Checkpoints

### Phase 1 — Identity Extraction

After pipeline completes, fetch the scholar record:

```
GET /academic/scholars/{scholar_id}
```

**Must check:**

| Field | Criterion | Severity |
|---|---|---|
| `name` | Non-empty, reasonably matches input name | FAIL if empty |
| `google_scholar_id` | Matches `expected.google_scholar_id` when a GS URL was provided | FAIL if mismatch when GS URL given; WARN if missing when only homepage given |
| `affiliation` | Non-empty string | WARN if null |
| `h_index` | `>= expected.min_h_index * 0.5` (allow some tolerance since GS stats fluctuate) | WARN if below threshold, FAIL if null when GS URL was provided |
| `i10_index` | Non-null | WARN if null |
| `total_citations` | Non-null and > 0 | WARN if null |
| `research_areas` | Non-empty list | WARN if empty |
| `discovered_urls` | Dict with at least one key | WARN if empty |

### Phase 2 — Semantic Scholar Resolution + Papers

**Must check:**

| Field | Criterion | Severity |
|---|---|---|
| `semantic_scholar_id` on scholar | Non-null (SS author was resolved) | WARN if null — some scholars may not be on SS |
| `paper_count` | `>= expected.min_papers * 0.3` (allow tolerance for SS coverage gaps) | WARN if below threshold |
| Top paper by citations | Has title, year, citations > 0 | WARN if missing |

Fetch papers for detail inspection:

```
GET /academic/scholars/{scholar_id}/papers?limit=5
```

Log top 3 papers (title, year, citations) for manual review.

### Phase 3 — Evaluation + Narrative

Fetch the v2 evaluations bundle:

```
GET /academic/scholars/{scholar_id}/evaluations
```

Response shape: `{dimensions: {dim_id: DimEvalResult|null}, narrative: NarrativeReport|null, peer_group: PeerGroup|null, red_flags: [...]}`

**Must check:**

| Field | Criterion | Severity |
|---|---|---|
| `dimensions` | At least 1 dim has a non-null scored eval | FAIL if all null |
| Each scored dim | `score` in [0, 100], `mini_report` non-empty, `evidence` list non-empty | WARN per dim |
| `peer_group` | Non-null, has `phase` and `field` | WARN if null |
| `narrative` | Non-null, `headline` and `summary` non-empty | WARN if null |
| `narrative.summary` | Contains scholar name (case-insensitive) | WARN if missing |
| `narrative.open_questions` | Non-empty list | WARN if empty |

### Overall

| Field | Criterion | Severity |
|---|---|---|
| Scholar status | `== "active"` (returned from evaluating) | FAIL |
| Elapsed time | `< 180s` | FAIL (timeout) |

## Severity Definitions

- **FAIL**: Test is marked failed. Indicates a broken pipeline or incorrect behavior.
- **WARN**: Test passes but logs a warning. Indicates degraded quality that may need investigation (e.g., SS not found for a niche scholar, h-index missing when only a homepage was given).

## Output Format

For each scholar, print a structured result block:

```
============================================================
SCHOLAR: Yoshua Bengio
URL Strategy: single-homepage (https://yoshuabengio.org/en)
Pipeline: completed in 45s (status=1)
------------------------------------------------------------
Phase 1 — Identity:
  Name: Yoshua Bengio
  Affiliation: Universite de Montreal / Mila
  Google Scholar ID: kukA0LcAAAAJ  [PASS — matches expected]
  H-Index: 182  [PASS — expected min 150]
  Citations: 680,000
  Research Areas: ["Deep Learning", "Neural Networks", ...]
  Discovered URLs: {google_scholar: ..., semantic_scholar: ...}

Phase 2 — Semantic Scholar:
  SS Author ID: 1726629  [PASS — resolved]
  Papers stored: 623  [PASS — expected min 500]
  Top papers:
    1. Deep Learning (2015) — 48,000 cit.
    2. Generative Adversarial Nets (2014) — 42,000 cit.
    3. Representation Learning: A Review (2013) — 12,000 cit.

Phase 3 — Report:
  Status: completed
  Length: 3,847 chars
  Contains name: YES
  Section coverage: 7/7
============================================================
```

At the end, print a summary:

```
RESULTS: 4 scholars tested, 3 PASSED, 1 WARNED (Melanie Weber: SS not found)
  Elapsed total: 210s
  URL strategies used: single-homepage x1, single-gs x1, partial x1, all x1
```

## Running the Test

```bash
cd backend/
../venv/bin/python tests/test_academic_randomized.py
```

Optional: set `RANDOM_SEED` env var for reproducible runs:

```bash
RANDOM_SEED=42 ../venv/bin/python tests/test_academic_randomized.py
```

## Edge Cases the Random Selection Naturally Covers

- **GS-only URL** (e.g., Nicola Spaldin has only a GS link): Tests whether the pipeline can extract identity from Google Scholar alone
- **Early-career scholars** (lower h-index, fewer papers): Tests tolerance for smaller publication records
- **Non-US scholars** (ETH, EMBL, Imperial, UCL, Oxford): Tests robustness across international institutions
- **Diverse fields** (physics, economics, marine biology): Tests Gemini's ability to handle non-CS researchers
- **Homepage-only input**: Tests URL discovery pipeline (Phase 1 must crawl homepage to find GS/SS links)
