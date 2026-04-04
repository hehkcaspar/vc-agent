# Academic Tracking v2 — Implementation Design

> **Status:** Final — ready for implementation
> **Date:** 2026-03-30
> **Stack:** FastAPI + Deep Agents SDK (LangGraph) + async SQLite + React 18 / TypeScript / SWR

---

## 1. What This Document Is

This is the complete design spec for rebuilding the Academic Tracking module. A developer with zero prior context should be able to read this and implement the system. The document covers storage, agent architecture, domain tools, frontend, signal monitoring, evaluation, and a concrete migration plan from the existing v1 codebase.

**The core change from v1**: replace the hardcoded 5-phase pipeline with a goal-driven Deep Agents harness backed by a document-oriented storage layer. Same data, same quality — but extensible, resumable, and capable of continuous monitoring instead of one-shot evaluation.

### Design Principles

1. **Minimal SQL, rich documents.** 3 SQL tables for cross-scholar indexing and scheduling. Everything the agent reads/writes lives in JSON/JSONL/markdown files on disk per scholar. No migrations for new fields — just start writing them.

2. **Build on Deep Agents SDK.** LangChain's Deep Agents wraps LangGraph with a pluggable filesystem backend (`CompositeBackend`), planning (`write_todos`), subagent spawning (`task`), and context management. We add domain tools and scholar-scoped configuration.

3. **Agentic over procedural.** One agent factory, one toolkit, different goals. Initial evaluation, refresh, signal investigation, custom analysis, and comparative evaluation are all the same agent with different system prompts.

---

## 2. Storage Architecture

### 2.1 Two Layers

| Layer | Purpose | Technology | Source of Truth |
|-------|---------|------------|-----------------|
| **Document Store** | Full scholar state — everything the agent reads/writes | JSON/JSONL/markdown files on disk, accessed via Deep Agents `CompositeBackend` | Yes |
| **SQL Index** | Cross-scholar queries, scheduling, signal feed | 3 SQLite tables (`scholars`, `scholar_events`, `channels`) | No — rebuildable from documents via `sync_sql_index` tool |

### 2.2 File System Layout

```
data/
├── scholars/
│   └── {scholar_id}/
│       ├── profile.json              # identity, affiliations, metrics, links, aliases
│       ├── papers.json               # summary header + paper array
│       ├── events.jsonl              # append-only event log
│       ├── channels.json             # monitored sources + snapshots
│       ├── evaluations/              # immutable evaluation snapshots
│       │   └── {date}_{type}.json
│       ├── reports/                  # generated markdown reports
│       │   └── {date}_{type}.md
│       ├── uploads/                  # user-uploaded docs
│       └── agent_runs/              # agent execution traces
│           └── {date}_{goal_slug}.json
├── config/
│   ├── heartbeat.json               # scheduler checklist
│   ├── field_archetypes.json        # commercialization signal guides per field
│   └── ranking_presets/             # optional saved weight vectors
└── academic.db                      # SQLite (separate from portfolio's vc_portfolio.db)
```

### 2.3 Agent's Virtual Filesystem (via CompositeBackend)

```python
backend = CompositeBackend(
    default=StateBackend(),                                          # ephemeral working memory
    routes={
        "/dossier/": LocalBackend(root=f"data/scholars/{scholar_id}/"),  # scholar's files
        "/config/":  LocalBackend(root="data/config/"),                  # shared config
    }
)
```

When the agent calls `read_file("/dossier/profile.json")`, the backend reads `data/scholars/{id}/profile.json`. When it calls `write_file("/workspace/temp.json", ...)`, data goes to ephemeral in-memory storage. The agent never sees physical paths.

### 2.4 SQL Schema (3 Tables)

```sql
CREATE TABLE scholars (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    status            TEXT DEFAULT 'active',        -- active | paused | archived
    tracking_priority TEXT DEFAULT 'medium',        -- high | medium | low
    tags              TEXT,                          -- JSON array
    entity_id         TEXT,                          -- FK to portfolio entity (nullable)
    dossier_path      TEXT NOT NULL,
    created_at        TIMESTAMP,
    updated_at        TIMESTAMP
);

CREATE TABLE scholar_events (
    id              TEXT PRIMARY KEY,
    scholar_id      TEXT NOT NULL REFERENCES scholars(id),
    event_type      TEXT NOT NULL,
    significance    TEXT DEFAULT 'medium',           -- high | medium | low
    title           TEXT,
    is_read         BOOLEAN DEFAULT FALSE,
    event_date      TIMESTAMP,
    created_at      TIMESTAMP
);

CREATE TABLE channels (
    id                     TEXT PRIMARY KEY,
    scholar_id             TEXT NOT NULL REFERENCES scholars(id),
    channel_type           TEXT NOT NULL,
    url                    TEXT,
    is_active              BOOLEAN DEFAULT TRUE,
    polling_interval_hours INTEGER DEFAULT 168,
    last_polled_at         TIMESTAMP,
    last_changed_at        TIMESTAMP,
    poll_error_count       INTEGER DEFAULT 0,
    created_at             TIMESTAMP
);
```

**Rule of thumb:** need to query/sort across scholars → SQL. Agent needs it for reasoning → document file.

---

## 3. Document Schemas

Schemas are baselines — the agent can add fields freely. No migration needed.

### 3.1 profile.json

```json
{
  "id": "uuid",
  "name": "Jane Chen",
  "aliases": ["J. Chen", "Chen, Jane"],

  "identity": {
    "google_scholar": {
      "id": "ABCDEF123",
      "url": "https://scholar.google.com/citations?user=ABCDEF123",
      "confidence": "verified",
      "verified_by": "deterministic_parse"
    },
    "semantic_scholar": {
      "id": "12345678",
      "confidence": "high",
      "verified_by": "cross_reference"
    },
    "linkedin": { "url": "...", "confidence": "medium" },
    "homepage": { "url": "https://janechen.stanford.edu", "confidence": "verified" }
  },

  "affiliation": {
    "current": "Stanford University",
    "department": "Computer Science",
    "role": "Associate Professor",
    "history": [{"institution": "MIT", "role": "Postdoc", "period": "2018-2020"}]
  },

  "metrics": {
    "h_index": 47, "i10_index": 120, "total_citations": 8200,
    "source": "google_scholar", "updated_at": "2026-03-01"
  },

  "research_areas": ["quantum computing", "error correction"],
  "field_archetype": "stem_applied",
  "user_notes": "Met at CES 2026.",
  "tags": ["quantum", "stanford", "high-potential"],
  "created_at": "2026-01-15",
  "updated_at": "2026-03-10"
}
```

Key decisions: `identity` is a map (new sources = new key, no migration). `aliases` handles name variants (particles, umlauts, Asian name order). No `career_stage` field — the agent computes it from paper dates. `metrics.source` tracks which source we trust (GS and SS report different numbers).

### 3.2 papers.json

For prolific scholars (500+ papers), the full array can reach 1MB. Solution: a summary header the agent reads by default; the full array is only accessed by deterministic tools operating on disk.

```json
{
  "updated_at": "2026-03-01",
  "summary": {
    "total": 523,
    "by_position": {"first": 85, "last": 210, "middle": 200, "sole": 28},
    "by_decade": {"2020s": 180, "2010s": 250, "2000s": 80, "1990s": 13},
    "top_cited": [{"title": "...", "year": 2014, "citations": 45000, "venue": "NeurIPS"}],
    "recent_5": [{"title": "...", "year": 2026, "citations": 5, "venue": "ICML", "position": "last"}]
  },
  "papers": [
    {
      "id": "paper-uuid",
      "title": "...",
      "authors": [{"name": "Jane Chen", "id": "ss-12345678", "position": "first"}],
      "year": 2026, "venue": "Nature", "publication_type": "JournalArticle",
      "citations": 12, "influential_citations": 3,
      "fields_of_study": ["Quantum Computing"],
      "ss_paper_id": "abc123", "url": "https://...", "source": "semantic_scholar"
    }
  ]
}
```

Deduplication: match on (normalized_title + year) or DOI/SS paper ID. Summary header recomputed after each batch write.

### 3.3 events.jsonl (Append-Only Log)

```jsonl
{"id":"evt-001","type":"new_paper","date":"2026-02-01","significance":"high","title":"New paper in Nature","payload":{...},"source":"channel:ch-ss-001"}
{"id":"evt-002","type":"metric_snapshot","date":"2026-03-01","significance":"low","title":"h-index 45→47","payload":{"h_index":{"old":45,"new":47}},"source":"channel:ch-gs-001"}
{"id":"evt-003","type":"career_change","date":"2026-03-20","significance":"high","title":"Appointed advisor at QuantumLeap","payload":{...},"source":"channel:ch-news-001"}
```

JSONL because append is O(1). SQL `scholar_events` mirrors key fields for cross-scholar queries. Event types are open-ended — common ones: `new_paper`, `new_preprint`, `citation_milestone`, `identity_discovered`, `affiliation_changed`, `career_change`, `patent_filed`, `startup_founded`, `news_mention`, `evaluation_completed`, `metric_snapshot`, `user_note_added`.

### 3.4 evaluations/{date}_{type}.json

```json
{
  "id": "eval-uuid",
  "type": "full",
  "trigger": "manual",
  "model": "gemini-2.5-flash",
  "created_at": "2026-01-15T14:30:00Z",

  "dimensions": {
    "research_impact": {
      "score": 82,
      "explanation": "Strong h-index (47) for 12-year career...",
      "evidence": ["h_index=47 (92nd percentile)", "3 top-venue first-author papers"]
    },
    "commercialization": {
      "score": 65,
      "archetype_used": "stem_applied",
      "explanation": "One patent filed, advisory role...",
      "evidence": ["1 patent filed", "Scientific Advisor at QuantumLeap"]
    },
    "career_trajectory":      { "score": 78, "explanation": "...", "evidence": [...] },
    "collaboration_strength":  { "score": 71, "explanation": "...", "evidence": [...] },
    "field_position":          { "score": 85, "explanation": "...", "evidence": [...] },
    "public_profile":          { "score": 40, "explanation": "...", "evidence": [...] },
    "founder_potential":       { "score": 55, "explanation": "...", "evidence": [...] }
  },

  "computed_metrics": {
    "first_author_papers": 45, "last_author_papers": 30,
    "sole_author_papers": 8, "first_author_citation_pct": 0.42,
    "career_years": 12, "papers_per_year_avg": 10.0, "papers_per_year_recent": 15.0,
    "citation_growth_rate": 0.23, "peak_citation_year": 2024,
    "unique_coauthors": 89, "influential_paper_count": 12, "top_venue_papers": 15
  },

  "field_context": {
    "primary_field": "Quantum Computing",
    "percentile_estimate": 92,
    "benchmark_h_index_median": 25, "benchmark_h_index_top10": 60,
    "context_explanation": "..."
  },

  "commercialization_signals": {
    "patents": [{"title": "...", "year": 2025, "url": "..."}],
    "startups": [{"name": "QuantumLeap", "role": "Scientific Advisor"}],
    "industry_collabs": [{"company": "IBM", "context": "Quantum research partnership"}],
    "tech_transfer_summary": "..."
  },

  "delta": {
    "vs_evaluation": "2025-10-01_full",
    "dimension_changes": {
      "research_impact": {"old": 79, "new": 82, "change": "+3"},
      "commercialization": {"old": 57, "new": 65, "change": "+8"}
    },
    "new_papers_since": 5,
    "notable_events": ["First Nature paper", "Patent filed"]
  },

  "agent_trace_ref": "agent_runs/2026-01-15_initial_eval.json"
}
```

**No `overall_score`.** The dimension scores ARE the output — each independently meaningful with evidence. Collapsing them into a single number hides the shape. Optional weighted ranking happens client-side only (see §6.3).

### 3.5 channels.json

```json
{
  "channels": [
    {
      "id": "ch-gs-001",
      "type": "google_scholar_profile",
      "url": "https://scholar.google.com/citations?user=ABCDEF123",
      "is_active": true,
      "polling_interval_hours": 168,
      "last_polled_at": "2026-03-01T10:00:00Z",
      "last_snapshot": {"h_index": 47, "total_citations": 8200, "paper_count": 150},
      "poll_error_count": 0
    },
    {
      "id": "ch-ss-001",
      "type": "semantic_scholar_profile",
      "url": "https://api.semanticscholar.org/graph/v1/author/12345678",
      "is_active": true,
      "polling_interval_hours": 72,
      "last_snapshot": {"paper_count": 145, "known_paper_ids": ["abc123"]}
    },
    {
      "id": "ch-news-001",
      "type": "news_alert",
      "query": "\"Jane Chen\" Stanford quantum",
      "is_active": true,
      "polling_interval_hours": 24,
      "last_snapshot": {"seen_urls": ["https://..."]}
    }
  ]
}
```

SQL `channels` table mirrors scheduling fields only. Full snapshots live here. Channels are auto-created by the agent during identity resolution (e.g., when it finds a GS profile, it creates a `google_scholar_profile` channel). Users can also add channels manually. Error handling: `poll_error_count` increments on failure; channel auto-deactivates at 5 consecutive errors; user can reactivate.

---

## 4. Agent Architecture

### 4.1 Deep Agents Harness

```
Deep Agents SDK
├── BUILT-IN: write_todos, read/write/edit/ls/glob (via CompositeBackend),
│             task (subagents), context middleware (auto-summarization)
├── DOMAIN TOOLS: fetch_gs_metrics, fetch_ss_papers, compute_bibliometrics,
│                 verify_identity, search_web, append_event, sync_sql_index, ...
├── LANGGRAPH (inherited): checkpointing, streaming, human-in-the-loop, LangSmith tracing
└── CONFIGURATION: CompositeBackend routes /dossier/ → scholar dir, /config/ → shared config
```

### 4.2 Agent Factory

```python
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, LocalBackend, StateBackend
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

def create_scholar_agent(scholar_id: str, goal: str, model_name: str = "gemini-2.5-flash"):
    backend = CompositeBackend(
        default=StateBackend(),
        routes={
            "/dossier/": LocalBackend(root=f"data/scholars/{scholar_id}/"),
            "/config/":  LocalBackend(root="data/config/"),
        }
    )
    agent = create_deep_agent(
        model=ChatGoogleGenerativeAI(model=model_name),
        tools=DOMAIN_TOOLS,  # see §4.3
        system_prompt=build_scholar_system_prompt(goal),
        backend=backend,
    )
    return agent  # CompiledStateGraph

# Invoke with checkpointing:
checkpointer = AsyncSqliteSaver.from_conn_string("data/checkpoints.db")
config = {"configurable": {"thread_id": f"scholar-{scholar_id}-{run_id}"}}
result = await agent.ainvoke({"messages": [HumanMessage(content=goal)]}, config=config)
```

### 4.3 Domain Tools

Tested against 20 real scholars (h-index 5 to 150+, across 12 fields). Each algorithm was classified as "must be a tool" vs "agent can handle natively." The guiding principle: **tools exist for things the LLM cannot do reliably** (HTTP calls, precise arithmetic, URL parsing, name matching with thresholds) or **should not do** (to avoid hallucinated numbers, wasted tokens). Everything the LLM is naturally good at (reasoning, scoring, writing reports, searching the web) stays with the agent.

**12 tools total:**

```
DETERMINISTIC (no LLM, pure code):
  classify_urls(urls)                    Extract GS/SS/LinkedIn/DBLP IDs from URL patterns
  compute_bibliometrics()                15+ metrics from papers.json (reads from disk, not context)
  append_event(event)                    JSONL append + SQL index sync
  sync_sql_index(scholar_id)             Rebuild SQL rows from document files

API + DETERMINISTIC LOGIC (external calls + precise matching):
  search_semantic_scholar(name, h_index?) Tier 1: SS API name search + scoring + verification
  search_ss_by_papers(name, area)         Tier 2: paper-based SS author discovery + verification
  fetch_ss_papers(ss_id, since?)          Fetch papers from SS API (batched, up to 500)

SERPAPI (structured API, no LLM — see Implementation Note below):
  fetch_gs_metrics(gs_id)                SerpAPI google_scholar_author → h-index, citations, areas
  search_web(query)                      SerpAPI google → organic results with snippets
  search_patents(name, affiliation)      SerpAPI google_patents → structured patent records
  search_news(query)                     SerpAPI google_news → structured news items

GEMINI + WEB SEARCH (needs LLM to interpret unstructured pages):
  crawl_url(url)                         Crawl arbitrary URL, extract outbound links
```

> **Implementation Note — SerpAPI Adoption (2026-03-30)**
>
> The original design classified `fetch_gs_metrics`, `search_web`, `search_patents`, and `search_news` as "GEMINI + WEB SEARCH" because they appeared to need LLM interpretation of web content. During implementation, we discovered that SerpAPI provides **structured JSON** for all four — Google Scholar author profiles, Google organic search, Google Patents, and Google News — eliminating the need for LLM interpretation at the tool layer.
>
> This change **preserves all three design principles**:
>
> 1. **"Tools exist for things the LLM cannot do reliably"** — SerpAPI returns precise, structured data (exact h-index, patent IDs, news metadata) that the LLM would otherwise need to scrape and parse from HTML. The tool layer is MORE reliable, not less.
>
> 2. **"Agentic over procedural"** — The agent still orchestrates all tool calls, decides what to search for, handles edge cases, and adapts its strategy. The tools just return faster. The agent's value-add (scoring dimensions, writing reports, reasoning about commercialization signals) is unchanged.
>
> 3. **"Everything the LLM is naturally good at stays with the agent"** — The agent still interprets search results (e.g., reading `search_web` snippets to assess field position), scores dimensions with evidence, and writes reports. The LLM's reasoning role is preserved; only the redundant inner-LLM fetch calls are removed.
>
> `crawl_url` remains Gemini-powered because arbitrary web pages genuinely require LLM interpretation to extract structure and discover outbound profile links. SerpAPI cannot visit and interpret arbitrary URLs.
>
> **Impact**: ~55% reduction in execution time (120s vs 270s per scholar), elimination of flaky JSON parsing from Gemini tool responses, and zero LLM cost for data fetching. The agent's Gemini calls are now spent entirely on reasoning and writing — where the LLM adds real value.

**What the agent does WITHOUT tools** (absorbed from v1 — these were separate Gemini calls in v1 but are natural agent capabilities):

- **Scoring dimensions** — v1 had `generate_evaluation_scores()` as a Gemini call. In v2, the agent reads the dossier + computed metrics + web search results and scores dimensions as part of its goal. The agent IS the LLM.
- **Writing reports** — v1 had `generate_report()`. In v2, the agent writes reports via `write_file()`.
- **Field positioning** — v1 had `assess_field_position()`. In v2, the agent calls `search_web("median h-index quantum computing professor")` and reasons about it.
- **Commercialization signals** — v1 had `search_commercialization_signals()`. In v2, the agent calls `search_web` + `search_patents` and assembles the findings.
- **Comparing evaluations** — v1 had `compare_evaluations()`. In v2, the agent reads two evaluation files and diffs them.
- **SS author resolution tier 3** — v1 had Gemini AFC calling SS tools. In v2, the deterministic tiers 1+2 handle most cases. For hard cases, the agent (which IS an LLM) can call `search_semantic_scholar` and reason about results directly.

### 4.3.1 Tool Implementation Details (Ported from v1)

**`classify_urls(urls: list[str]) → dict`** — Port from v1 `_classify_urls()`. Tested: 20/20 scholars correct, including `scholar.google.fr` TLD, SS URLs with names in path, extra query params. Patterns: `scholar.google.*` + `/citations` → extract `?user=` param. `semanticscholar.org/author/` → extract trailing numeric ID. `linkedin.com/in/` → store URL. `dblp.org/` → store URL. Ground truth — overrides any LLM extraction.

**`search_semantic_scholar(name, expected_h_index?, expected_citations?) → {ss_id, confidence}`** — Combines v1 `_try_ss_name_search()` + `_verify_ss_author()` + `_names_match()` into one tool. Internally: (1) SS API search by name (limit=10), (2) filter candidates by name match, (3) score by h-index proximity (`ratio × 100 + paper_count × 0.01`), (4) verify winner by metric plausibility. Returns best match or null.

**`search_ss_by_papers(name, research_area, expected_h_index?, expected_citations?) → {ss_id, confidence}`** — Port of v1 `_try_ss_paper_search()`. More discriminating for common names. Searches papers by `"{name} {area}"`, extracts author IDs from top-cited results, verifies each candidate via name match + metrics.

**Key algorithms inside these tools (preserve exactly):**

- **Name matching** (`_names_match`): tokenize → NFKD Unicode normalize (handles Schölkopf, de Rham) → match tokens by exact, initial (M→Michael), or 3-char prefix (Kate→Katherine). Threshold: 1 strong match (len>2) + min(2, shorter_list) total, OR ≥2 matches. Known limitation: "Bronstein" vs "Brown" false-positive on "bro" prefix — acceptable because metric verification is the real gate.
- **Metric verification** (`_verify_ss_author`): h-index ratio ≥0.3 (3x divergence = different person, only checked if expected h>10). Citation ratio ≥0.1 (10x divergence, only checked if expected citations>1000). This catches the "Song Han" problem where name match alone fails.
- **Paper deduplication** (inside `fetch_ss_papers` write path): key = (lowercase + collapse whitespace title, year). On duplicate: update missing fields (author_position, ss_paper_id, venue) but keep higher citation count.
- **Author position**: match scholar's SS author_id in paper's author list. sole if total=1, first if idx=0, last if idx=len-1, middle otherwise.

**`compute_bibliometrics() → dict`** — Port of v1 `_compute_evaluation_metrics()`. Reads papers.json **from disk** (not LLM context — critical for Bengio's 500+ papers). Computes 15+ metrics: authorship breakdown (first/last/sole/middle counts + percentages), career years (max_year - min_year), papers/year (lifetime + recent 5yr), citation growth rate (GS citations_per_year: avg recent 3 full years vs prior 3, excludes current year, requires ≥4 years), peak citation year, unique coauthors (case-insensitive), influential paper count (SS influential_citations > 0), top-venue count (~80 venues: NeurIPS, ICML, Nature, Science, Cell, PRL, etc. — case-insensitive substring match).

**`fetch_gs_metrics(gs_id) → dict`** — Port of v1 `extract_identity()`. Uses Gemini + Google Search to visit `scholar.google.com/citations?user={gs_id}`, extracts: h-index, i10-index, total citations, citations_per_year (chart data), research interests. Temperature=0.0. The prompt explicitly forbids fabricating IDs. Returns are merged with `classify_urls()` ground truth.

**`append_event(event) → event_id`** — JSONL append (not overwrite) + SQL `scholar_events` insert. The only custom file I/O — everything else uses Deep Agents' built-in filesystem tools.

### 4.4 Invocation Patterns

Same agent, same tools, different goals:

```python
# Initial evaluation
goal = """Perform comprehensive initial evaluation. Start from /dossier/profile.json URLs.
Discover identity, fetch papers, compute metrics, score all dimensions, generate report.
Read /config/field_archetypes.json for commercialization guidance."""

# Refresh (what changed)
goal = """Refresh this scholar. Read latest evaluation from /dossier/evaluations/.
Fetch only new papers. Compute delta. Generate delta report."""

# Signal investigation
goal = """News channel detected: 'Stanford professor Jane Chen joins QuantumLeap as advisor.'
Verify this is about our scholar. If confirmed, update dossier and assess investment significance."""

# Custom analysis (user-driven via chat)
goal = """User asks: 'Compare her patent portfolio to typical tech-transfer timelines in quantum computing.'
Read dossier, search for additional context, generate analysis."""

# User upload processing
goal = """User uploaded meeting notes to /dossier/uploads/2026-03-30_coffee_chat.md.
Extract key info, append relevant events, update profile."""
```

### 4.5 Checkpointing and Failure Recovery

`create_deep_agent()` returns a LangGraph `CompiledStateGraph` — checkpointing via `AsyncSqliteSaver` is free. If the server crashes mid-evaluation, the agent resumes from last checkpoint. Critical for long-running evaluations (2-3 minutes with all API calls).

### 4.6 Agent Tracing

After each run, save a structured trace to `agent_runs/`:

```json
{
  "run_id": "run-uuid",
  "goal": "Refresh evaluation for Dr. Chen",
  "started_at": "2026-03-10T14:00:00Z",
  "completed_at": "2026-03-10T14:02:15Z",
  "model": "gemini-2.5-flash",
  "tools_called": ["read_file", "fetch_gs_metrics", "compute_bibliometrics", "append_event"],
  "files_modified": ["papers.json", "evaluations/2026-03-10_refresh.json"],
  "tokens_used": 12400,
  "cost_estimate_usd": 0.008
}
```

LangSmith (if enabled) provides full tool-calling traces with token counts. The `write_todos` tool shows the agent's plan before execution — the user can interrupt if the plan looks wrong.

---

## 5. Signal Monitoring System

### 5.1 Heartbeat Scheduler

An asyncio loop reads `data/config/heartbeat.json` and dispatches due actions:

```json
{
  "checks": [
    {"id": "channel_poll", "enabled": true, "interval_minutes": 5, "action": "poll_due_channels"},
    {"id": "high_priority_refresh", "enabled": true, "interval_minutes": 10080,
     "action": "refresh_stale_scholars", "filter": {"tracking_priority": "high", "stale_days": 7}},
    {"id": "weekly_digest", "enabled": true, "interval_minutes": 10080, "action": "generate_digest"}
  ]
}
```

```python
async def heartbeat_loop():
    while True:
        config = json.loads(Path("data/config/heartbeat.json").read_text())
        for check in config["checks"]:
            if check["enabled"] and is_due(check):
                await dispatch_check(check)
        await asyncio.sleep(60)
```

Adding/removing checks = editing JSON, no code change. The agent can modify the heartbeat if instructed ("start monitoring this scholar more frequently").

### 5.2 Channel Polling Flow

```
Heartbeat fires "poll_due_channels"
  └─ SQL: channels WHERE is_active AND last_polled_at + interval < now
       └─ For each due channel:
            ├─ ChannelPoller.poll(channel, last_snapshot)  [deterministic, no LLM]
            ├─ Diff against last_snapshot
            ├─ If changed:
            │    ├─ Append events (events.jsonl + SQL)
            │    ├─ Update snapshot (channels.json + SQL timing)
            │    ├─ If significance >= high → spawn scholar agent for investigation
            │    └─ Else → just log the event
            ├─ If error:
            │    ├─ Increment poll_error_count
            │    └─ If poll_error_count >= 5 → deactivate channel, log warning event
            └─ If unchanged → update SQL last_polled_at only
```

**Channel types and fetch methods:**

| Channel Type | Fetch Method | Diff Produces |
|-------------|-------------|---------------|
| `google_scholar_profile` | Gemini + web search | `metric_snapshot`, `new_paper` events |
| `semantic_scholar_profile` | SS API get_author_papers | `new_paper` events |
| `news_alert` | Web search for scholar name | `news_mention` events (agent verifies) |
| `personal_website` | Fetch + content hash | `website_updated` event |
| `patent_watch` | Web search for patents | `patent_filed` events |

Pollers are deterministic (API calls + diffing). The LLM is only invoked when a change needs interpretation — keeping costs low for routine polling.

### 5.3 Significance Assessment

Deterministic rules (fast, no LLM): paper in Nature/Science/Cell → high. h-index jump > 3 → high. Career change → high. Patent filed → high. Routine citation increment → low.

When a signal can't be auto-classified, spawn the scholar agent with: "Assess the investment significance of this signal."

---

## 6. Evaluation System

### 6.1 Dimensions as Primary Output

The LLM scores each dimension independently (0-100) with explanation and evidence. The dimension scores **are** the output — no mandatory aggregation. Default dimensions: `research_impact`, `commercialization`, `career_trajectory`, `collaboration_strength`, `field_position`, `founder_potential`, `public_profile`.

These are not hardcoded — the agent reads them from configuration and can score additional custom dimensions if defined.

### 6.2 Field Archetypes

"Commercialization" means different things across fields. Archetypes are agent configuration stored at `data/config/field_archetypes.json` — they tell the agent **what signals to search for**, not how much to weight:

```json
{
  "archetypes": {
    "stem_applied": {
      "match_fields": ["Computer Science", "Robotics", "Engineering", "AI"],
      "signals": "Patents, startups, VC funding, licensing, industry R&D partnerships, open-source adoption.",
      "examples": "Abbeel → Covariant; Leskovec → multiple startups from graph ML."
    },
    "biomedical": {
      "match_fields": ["Neuroscience", "Biology", "Chemistry", "Medicine"],
      "signals": "Clinical trials, FDA filings, biotech licensing, method patents, tool companies.",
      "examples": "Deisseroth → optogenetics method licensed widely."
    },
    "social_science_policy": {
      "match_fields": ["Economics", "Political Science", "Public Policy"],
      "signals": "Advisory roles, consulting, policy influence, think tanks, books.",
      "examples": "Mazzucato → UCL IIPP, bestselling books, EU/UN advisory."
    },
    "pure_science": {
      "match_fields": ["Mathematics", "Theoretical Physics", "Cosmology"],
      "signals": "Foundational contributions enabling downstream applications, prestige, textbooks.",
      "examples": "Tao → foundational math enabling signal processing, cryptography."
    },
    "data_platform": {
      "match_fields": ["Data Science", "Environmental Science", "Computational Biology"],
      "signals": "Public datasets, data tools/platforms, open-source tools, media/publishing.",
      "examples": "Ritchie → Our World in Data, bestselling book."
    }
  }
}
```

The agent auto-selects the best archetype from the scholar's `research_areas` and records it in the evaluation (`archetype_used`). New archetypes = edit JSON, no code change.

### 6.3 Optional Weighting (Client-Side Ranking Only)

Weighting is a frontend-only operation on already-computed scores. It never triggers an LLM call or alters stored evaluations:

```typescript
function computeWeightedRank(scores: Record<string, number>, weights: Record<string, number>): number {
  const totalWeight = Object.entries(weights)
    .filter(([dim]) => dim in scores)
    .reduce((sum, [, w]) => sum + w, 0);
  if (totalWeight === 0) return 0;
  return Object.entries(weights)
    .filter(([dim]) => dim in scores)
    .reduce((sum, [dim, w]) => sum + scores[dim] * w / totalWeight, 0);
}
```

Users can save weight presets to `data/config/ranking_presets/` for repeated use. These are simple `{dimension: weight}` JSON files.

### 6.4 Custom Dimensions

Users define extra dimensions with a name + guiding prompt. The agent scores them alongside defaults using the same format. Custom dimensions require an LLM call but are stored and reusable like any other dimension.

---

## 7. Frontend Architecture

### 7.1 What Exists (v1) and What Changes (v2)

The current frontend has these components, all of which are **preserved and evolved**:

| v1 Component | v1 Behavior | v2 Evolution |
|-------------|------------|-------------|
| `AcademicTab.tsx` | Task list with CRUD, execute/stop, polling | → **Scholar list** with status, priority, last activity, tag filtering |
| `TaskDetail.tsx` | Detail view with Report/Evaluation/Publications/Profiles tabs | → **Scholar workspace** with Timeline/Evaluation/Publications/Profiles/Chat tabs |
| `CreateTaskModal.tsx` | Create task form (name, type, URLs) | → **Add Scholar** modal (same fields, type removed — scholars only for now) |
| `useAcademic.ts` | SWR hooks polling task/report status | → Same pattern, new endpoints for scholar-centric API |
| `academicApi.ts` | API client for task endpoints | → Updated for scholar-centric endpoints |

### 7.2 New/Changed UI Components

**Scholar List (evolved from AcademicTab)**:
- Columns: Name, Affiliation, Priority (high/medium/low), Status, Last Activity, Tags
- Actions: Evaluate, Pause/Resume Monitoring, Edit, Delete
- Signal badge: unread high-significance event count per scholar
- Tag filtering and search

**Scholar Workspace (evolved from TaskDetail)**:

The existing 4-tab detail view gains new capabilities:

1. **Timeline tab** (NEW) — chronological event feed from events.jsonl (via API that reads SQL `scholar_events` for list, fetches full payload from JSONL for expanded view). Filter by event type and significance. Mark events as read.

2. **Evaluation tab** (EVOLVED) — replace the v1 single-score display with:
   - **Radar chart** showing all dimension scores (use recharts `RadarChart`)
   - **Score table** with expandable rows showing explanation + evidence per dimension
   - **Delta indicators** showing change vs. previous evaluation (▲ green / ▼ red)
   - **Evaluation history** sidebar — list of past evaluations, click to compare
   - Remove: `overall_score` card (v1 had this prominently — drop it)
   - Keep: computed metrics (authorship, career, field positioning, commercialization signals) as collapsible sections below the dimension scores

3. **Publications tab** (PRESERVED) — keep existing table with role filtering, citation sorting, influential badges. No changes needed — v1 implementation is solid.

4. **Profiles tab** (PRESERVED) — keep existing profile links grid. Add: channel monitoring status indicator (active/paused/error) next to each profile link.

5. **Chat tab** (NEW) — per-scholar chat backed by the scholar agent. Extends the existing `EntityConversation` pattern from the portfolio module. User can ask: "What's new?", "Run a deep eval on patents", "Compare to Dr. Park". Agent responds with context from the dossier.

6. **Reports tab** (EVOLVED from v1 report sidebar) — move report sidebar to its own tab. Keep: report list, markdown rendering, generate button, delete, polling for pending reports.

**Portfolio Dashboard (NEW section in Scholar List)**:
- **Signal Feed**: cross-scholar unread events sorted by significance + recency. SQL query: `SELECT * FROM scholar_events WHERE is_read = FALSE AND significance IN ('high', 'medium') ORDER BY created_at DESC`
- **Stale Alerts**: scholars overdue for refresh based on priority level
- **Ranking View**: table of scholars with dimension scores, sortable by any dimension or weighted preset. Client-side computation using `computeWeightedRank()`.

### 7.3 API Changes

The API shifts from task-centric to scholar-centric. Key endpoint changes:

```
# v1 (task-centric)                    →  v2 (scholar-centric)
POST   /academic/tasks                 →  POST   /academic/scholars          (create scholar)
GET    /academic/tasks                 →  GET    /academic/scholars          (list scholars)
GET    /academic/tasks/{id}            →  GET    /academic/scholars/{id}     (get scholar)
PUT    /academic/tasks/{id}            →  PUT    /academic/scholars/{id}     (update scholar)
DELETE /academic/tasks/{id}            →  DELETE /academic/scholars/{id}     (delete scholar)
POST   /academic/tasks/{id}/execute    →  POST   /academic/scholars/{id}/evaluate  (run agent)
POST   /academic/tasks/{id}/stop       →  POST   /academic/scholars/{id}/stop

# New endpoints
GET    /academic/scholars/{id}/events         (timeline, from SQL + JSONL expansion)
PUT    /academic/scholars/{id}/events/{eid}   (mark read, update significance)
GET    /academic/scholars/{id}/channels       (monitoring channels)
PUT    /academic/scholars/{id}/channels/{cid} (pause/resume/adjust interval)
GET    /academic/scholars/{id}/evaluations    (evaluation history)
GET    /academic/scholars/{id}/chat           (chat history)
POST   /academic/scholars/{id}/chat           (send message to scholar agent)
GET    /academic/signal-feed                  (cross-scholar unread events)

# Preserved (same as v1)
GET    /academic/scholars/{id}/papers         (with filtering/sorting)
GET    /academic/reports/{id}                 (single report)
DELETE /academic/reports/{id}                 (delete report)
```

### 7.4 Data Fetching Strategy

**Frontend reads evaluations from JSON files served via API** — not from SQL. The API endpoint `GET /scholars/{id}/evaluations` reads evaluation files from `data/scholars/{id}/evaluations/`, parses them, and returns the list. This is the source of truth.

**Events**: list view from SQL (fast cross-scholar queries); expanded detail from JSONL (full payload). The API assembles this.

**Polling**: SWR hooks poll at 3s intervals while any scholar has `status === 'evaluating'` or any report is pending. Same pattern as v1.

---

## 8. Implementation Plan

This is a clean build — v1 data and tables are discarded. Delete v1 academic tables (`academic_tracking_tasks`, `academic_scholars`, `academic_papers`, `academic_reports`, `academic_task_scholar_relations`, `academic_scholar_evaluations`) and their corresponding models/schemas/router.

### Phase 1: Foundation

**New files:**
- `backend/app/academic_models.py` — replace with 3 new SQL tables (§2.4)
- `backend/app/academic_schemas.py` — new Pydantic schemas for scholar-centric API
- `data/scholars/` directory structure (created on first scholar add)
- `data/config/heartbeat.json`, `data/config/field_archetypes.json` (seed from §5.1 and §6.2)

**Deliverable**: empty system with new schema, ready for scholars

### Phase 2: Scholar Agent + Core API

**New files:**
- `backend/app/services/academic/scholar_agent.py` — agent factory (`create_scholar_agent()`)
- `backend/app/services/academic/domain_tools.py` — domain tools as `@tool` functions
- `backend/app/services/academic/scholar_prompts.py` — system prompt builder
- `backend/app/services/academic/tool_utils.py` — shared utilities ported from v1

**Port from v1 (extract, don't copy wholesale):**

| v1 Source | v2 Target | What to Preserve |
|-----------|-----------|-----------------|
| `_classify_urls()` | `classify_urls` tool | Deterministic URL→ID extraction (GS user param, SS author ID, LinkedIn, DBLP) |
| `_try_ss_name_search()` | `search_semantic_scholar` tool | Tier 1 SS resolution: API name search + scoring |
| `_try_ss_paper_search()` | `search_ss_by_papers` tool | Tier 2 SS resolution: paper-based author discovery |
| `_names_match()` | `tool_utils.py` | Token matching with NFKD, initials, prefix, bidirectional |
| `_verify_ss_author()` | `verify_identity` tool | Name match + h-index ratio ≥30% + citation ratio ≥10% |
| `_compute_evaluation_metrics()` | `compute_bibliometrics` tool | Authorship, career years, citation growth, top venues, coauthors |
| `_is_top_venue()` | `tool_utils.py` | ~80 hardcoded top venue list, case-insensitive substring |
| `_norm_title()` | `tool_utils.py` | Lowercase + collapse whitespace for dedup |
| `extract_identity()` | `fetch_gs_metrics` + `crawl_url` tools | GS scraping via Gemini, link discovery |
| `search_commercialization_signals()` | same-name tool | Gemini + Google Search for patents/startups/industry |
| `assess_field_position()` | same-name tool | Field benchmarks, percentile via Gemini |
| `semantic_scholar.py` | **keep unchanged** | Clean API client, used by tools directly |

**Rewrite:** `backend/app/routers/academic.py` — scholar-centric endpoints (§7.3)

**Frontend:** Update `AcademicTab` → scholar list, `CreateTaskModal` → add scholar modal, `TaskDetail` → scholar workspace. Keep existing `useAcademic.ts` pattern (SWR + polling), update endpoints.

**Test**: run agent against 20 test scholars, verify output quality.

**Deliverable**: working scholar agent with evaluate/stop, scholar list, basic detail view

### Phase 3: Monitoring + Timeline

- Build channel pollers (Google Scholar, Semantic Scholar)
- Implement heartbeat scheduler (asyncio loop in FastAPI lifespan)
- Wire pollers to event creation (events.jsonl + SQL)
- Build Timeline tab (frontend)
- Build Profiles tab with channel status indicators
- **Deliverable**: scholars monitored, events in timeline

### Phase 4: Signal Feed + Chat + Evaluation UI

- Portfolio signal feed (cross-scholar unread events)
- News alert and website change pollers
- Per-scholar chat (extend `EntityConversation` pattern)
- Evaluation tab with radar chart (recharts `RadarChart`), score table, delta indicators, evaluation history
- User upload → agent processing
- **Deliverable**: full investor workflow

### Phase 5: Ranking + Comparative

- Client-side ranking with weight presets
- Ranking view in portfolio dashboard
- Comparative evaluation (subagent pattern)
- Digest generation
- **Deliverable**: portfolio-level analysis

---

## 9. Technical Considerations

### 9.1 JSONL Append

Deep Agents' `write_file` overwrites. The `append_event` tool handles JSONL append + SQL sync:

```python
@tool
def append_event(scholar_id: str, event: dict) -> str:
    """Append event to events.jsonl AND insert into SQL index."""
    event["id"] = str(uuid4())
    event["created_at"] = datetime.utcnow().isoformat()
    events_path = Path(f"data/scholars/{scholar_id}/events.jsonl")
    with open(events_path, "a") as f:
        f.write(json.dumps(event) + "\n")
    db.execute("INSERT INTO scholar_events (...) VALUES (...)", ...)
    return event["id"]
```

### 9.2 Context Window Management

Deep Agents handles this with built-in middleware: auto-summarization of long conversations, large output routing to filesystem. Our domain tools also return summaries with file references — e.g., `fetch_ss_papers` writes full data to `/workspace/` and returns a count summary.

### 9.3 LLM Cost Management

- **Deterministic first**: `compute_bibliometrics`, `classify_urls`, channel pollers — no LLM
- **Model tiering**: `gemini-2.5-flash` for routine work; `gemini-2.5-pro` for comprehensive evaluations
- **Subagent isolation**: each subagent has its own context window, preventing cost blowup
- **Budget tracking**: agent trace records token usage; can enforce per-scholar monthly budgets

### 9.4 SQLite Coexistence

- WAL mode for concurrent read/write
- Checkpoint DB (`data/checkpoints.db`) separate from main DB — no lock contention
- SQL index rebuildable from documents (`sync_sql_index` tool)
- Academic uses its own SQLite DB (`data/academic.db`), separate from portfolio's `vc_portfolio.db`

### 9.5 Portfolio Entity Link

`scholars.entity_id` → nullable FK to portfolio `Entity`. When a scholar starts a company, the user links them. Enables: "show scholars linked to active deals" and "create entity from scholar dossier."

---

## 10. Design Validation (20 Real Scholars)

Validated against `academic_test_scholars.json` — 20 scholars across 3 career stages, 12 fields, 5 countries, h-indices from 5 to 150+.

| Edge Case | Scholar | Design Response |
|-----------|---------|----------------|
| 500+ papers, papers.json too large | Bengio | Summary header; `compute_bibliometrics` reads from disk |
| Only 1 GS URL input | Spaldin | Agent adapts: starts from GS, discovers outward |
| Name particle ("de Rham") | de Rham | `aliases` + token matching in name algorithm |
| Umlaut ("Schölkopf") | Schölkopf | Unicode NFKD normalization in name matching |
| Non-.com GS TLD (scholar.google.fr) | Vincent | URL parser matches across all TLDs |
| Economics — no patents/startups | Mazzucato | `social_science_policy` archetype |
| Pure math — near-zero commercialization | Tao | `pure_science` archetype — foundational enablement signals |
| Labeled "early" but h>50 | Theis | No `career_stage` field — agent computes from papers |
| Very early career (h~5) | Weber | Field-relative scoring via benchmarks |
| Dual academic/industry role | Abbeel | Profile supports multiple affiliations |

---

## Appendix: Key Decisions Summary

| Decision | Rationale |
|----------|-----------|
| 3 SQL tables, everything else in files | Schema evolution without migrations; agent reads files naturally via Deep Agents |
| Deep Agents over raw LangGraph | CompositeBackend, planning, subagents, context middleware — pre-built |
| No `overall_score` | Dimension scores are independently meaningful; weighted ranking is client-side only |
| Field archetypes as agent config | "Commercialization" varies by field; config tells agent where to look, not how much to weight |
| JSONL for events | O(1) append; SQL mirrors key fields for cross-scholar queries |
| Channels auto-created by agent | Agent creates monitoring channels during identity resolution |
| Checkpointing via LangGraph | Long-running evaluations (2-3 min) survive server crashes |
| Extract v1 pipeline into @tool functions | Preserve proven algorithms (name matching, SS resolution, bibliometrics) |
