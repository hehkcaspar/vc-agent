# VC Portfolio Agent Harness — Unified Design and Plan

**Planning and rationale** for the portfolio **agent harness**, **chat**, **tools**, **multimodel**, and **artifact editing**. For **current HTTP contracts and behavior** (including `202` async jobs, `GET .../jobs/{id}`, `use_deep_agent`, and env vars), use **`docs/API_REFERENCE.md`** and **`docs/ARCHITECTURE.md`** — they track the codebase; this file tracks **design intent** and the original task checklist.

**How to use this doc for implementation**

1. Respect **§1–§11** as architecture and product rules; **§13** as the ordered task list; **§14–§15** for quality gates and open choices.
2. **Prefer Deep Agents’ native APIs and patterns first** (see §5)—do not duplicate planning, subagents, virtual FS, streaming, or persistence that the harness already provides unless the docs force a gap; extend via documented customization (middleware, backends, `model`, subagent dicts, LangSmith).
3. **Option B** (§9) is the default **mutate contract** until an explicit Option C pilot is flagged.
4. **Presets** are an allowed **exception** to “everything through the harness path” until you intentionally unify them (see §3 and §7).

---

## Contents

1. [North star and principles](#1-north-star-and-principles)
2. [Context: artifact editing and stack](#2-context-artifact-editing-and-stack)
3. [UX: chat-only surface](#3-ux-chat-only-surface)
4. [Reference architectures and our synthesis](#4-reference-architectures-and-our-synthesis)
5. [Chosen implementation path: LangChain Deep Agents](#5-chosen-implementation-path-langchain-deep-agents)
6. [Multimodel strategy (Gemini + Kimi K2.5)](#6-multimodel-strategy-gemini--kimi-k25)
7. [Tooling model and portfolio domain tools](#7-tooling-model-and-portfolio-domain-tools)
8. [Artifact editing: modes, resolver, pipeline, audit](#8-artifact-editing-modes-resolver-pipeline-audit)
9. [Option B vs Option C (writes): scope and future upgrade](#9-option-b-vs-option-c-writes-scope-and-future-upgrade)
10. [API surface](#10-api-surface)
11. [Orchestration: stay inside Deep Agents unless forced out](#11-orchestration-stay-inside-deep-agents-unless-forced-out)
12. [Rollout roadmap](#12-rollout-roadmap)
13. [Implementation checklist (file-mapped)](#13-implementation-checklist-file-mapped)
14. [Testing strategy](#14-testing-strategy)
15. [Open decisions and initial defaults](#15-open-decisions-and-initial-defaults)
16. [Implementation prerequisites](#16-implementation-prerequisites)
17. [References](#17-references)

---

## 1. North star and principles

The product evolves from **artifact editing** to a single **VC Portfolio Agent Harness**: one conversational surface that can research, answer, and change saved work—as complexity grows (diligence, legal-style review, coding-agent-style iteration)—**without** forking the UX into separate “modes.”

**Frozen principles**

1. **Chat-first** — Normal work is conversation. **Shortcuts and presets** are *starters* (task template or system bias into the same harness), not a parallel framework.
2. **Server-owned side effects** — Search, storage, artifact mutation, MCP, and subagent calls run only as **server-executed tools** with policy checks—not opaque model filesystem access.
3. **Stable extension** — New behavior is **new tools** or **subagent profiles** (prompt + tool allowlist + policy), not new top-level product modes.
4. **Observable runs** — Each user turn maps to a **run** (correlation id, optional tool/run trace, later streaming steps).
5. **Bounded autonomy** — **Max tool turns**, **timeouts**, **policy gates** on mutates. Long work may span **multiple messages** or **async jobs**; never unbounded silent loops.
6. **Write safety** — **Option B** for applies: resolve → validate → apply + audit. **Option C** is an **internal** plan/execute upgrade when needed—still behind chat.
7. **Deep Agents native-first** — Orchestration, planning (`write_todos`), context/offloading (virtual filesystem + backends), subagent delegation, and (where applicable) streaming/checkpointers/Memory Store follow **[Deep Agents docs](https://docs.langchain.com/oss/python/deepagents/overview)**. Custom code is for **portfolio domain** concerns (entity scoping, `StorageAdapter`, SQLite audit, apply pipeline), not a second in-house agent loop.

**Complex job archetypes (same harness, different profiles)**

| Use case | Harness behavior (target) |
|----------|---------------------------|
| Full diligence | Multi-round resource/artifact reads + search; optional subagents (market, financials, team); draft/apply artifacts |
| Legal / policy review | Conservative apply policy; heavy audit; optional Option C for coordinated edits |
| Coding-agent-style work | Many tool rounds; scratch context; subagents; per-message caps; continue in thread or job |

---

## 2. Context: artifact editing and stack

**Editing capabilities**

1. **Versioned edit** — New `vN+1`; prior content retained.
2. **In-place overwrite** — Replace content in place per **policy** (policy-gated).

Both must be available as **capability**; **which mode is exposed** to whom is a later product/policy decision.

**Schema note:** Current code paths version by **new `Artifact` row + new file** (`create_artifact_for_entity`). **Overwrite** semantics must be defined explicitly in implementation (e.g. replace bytes at existing `relative_path` and update `updated_at`, vs “logical overwrite” still creating a new version). Capture the chosen behavior in `ARCHITECTURE.md` / API docs when implemented.

**Existing constraints**

- FastAPI, SQLite, entity-scoped chat sessions
- Artifacts via `create_artifact_for_entity` and versioned paths on disk
- Today: mostly **one-shot** Gemini calls (`generate_with_context`); not yet a full agent harness in code

---

## 3. UX: chat-only surface

- One conversational thread per session. Users do **not** need a separate “edit” or “search” control to unlock behavior for **normal chat**.
- Optional **context chips** (`resource_ids`, `artifact_ids`) are **hints**, not triggers.
- For **`POST .../messages`**, portfolio side effects (search via tools, artifact reads, artifact applies) happen only through tools invoked from the **agent harness**.

**Explicit exception (until unified): preset shortcuts**

- Today, **`POST .../entities/{entity_id}/chat/presets/{preset_id}/run`** generates artifacts via a **dedicated preset pipeline** (`create_artifact_for_entity`), not through the Deep Agents chat harness.
- Product UX may still *feel* “one workspace,” but implementers must treat presets as a **separate code path** until a deliberate migration routes them through the same harness (optional future).

**Non-goals**

- Mandatory second button or rigid command grammar for edit vs research (optional power-user shortcuts may exist later).

---

## 4. Reference architectures and our synthesis

| System | Takeaway for us |
|--------|------------------|
| [OpenClaw](https://github.com/openclaw/openclaw) | Control plane + sessions + **first-class tools** + policy; untrusted input |
| [DeerFlow](https://github.com/bytedance/deer-flow) | Harness: skills, tools, **subagents**, MCP, sandboxes, long jobs |
| [LangChain Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview) | **Agent harness**: planning (`write_todos`), filesystem/context tools, subagents, pluggable backends; built on LangGraph |

**What we adopt**

- **One** primary HTTP path: chat messages.
- **Tool registry** + validation on every mutate.
- **Deep Agents** as the **product harness** (see §5), with **portfolio-specific** tools and policies.
- **LangGraph** only as the **runtime Deep Agents already uses**—not a separate DIY graph unless §11 exception; we do **not** embed DeerFlow/OpenClaw wholesale.

---

## 5. Chosen implementation path: LangChain Deep Agents

**Official pattern (recommended by LangChain)**

- Package: `deepagents`
- Pattern: `create_deep_agent(tools=..., system_prompt=...)` then `agent.invoke({"messages": [...]})`
- Capabilities: planning (`write_todos`), virtual FS tools, subagent spawning, backends, persistence options

References:

- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview)
- [LangChain Deep Agents product page](https://www.langchain.com/deep-agents)
- [deepagents on PyPI / GitHub](https://github.com/langchain-ai/deepagents)

**Integration in this repo**

- Invoke the harness from **`post_chat_message`** (feature-flag rollout; keep legacy one-shot path for rollback).
- Register **domain tools** (artifact list/read/apply, entity-scoped resource access, etc.) alongside harness built-ins where appropriate.
- Use **native observability** first: e.g. **LangSmith** tracing (`LANGSMITH_TRACING`) per Deep Agents docs; add **app-level** `run_id` / DB correlation only when product requirements exceed what LangGraph/LangSmith already capture.

**Native-first policy (do not reinvent the harness)**

| Capability | Prefer (Deep Agents / LangChain) | Custom in this repo only when… |
|------------|-----------------------------------|--------------------------------|
| Multi-step tool loop, interrupts, streaming | Deep Agents + LangGraph runtime ([overview](https://docs.langchain.com/oss/python/deepagents/overview)) | Docs require a gap-fill (document the exception) |
| Task planning / todos | Built-in `write_todos` and harness middleware | — |
| Large context / scratch work | Virtual FS + **backends** ([backends](https://docs.langchain.com/oss/python/deepagents/backends)) | Canonical artifact **apply** still via domain tools + storage |
| Subagents / delegation | Built-in `task` / subagent config ([subagents](https://docs.langchain.com/oss/python/deepagents/subagents)) | Portfolio-specific **prompt + tool allowlists** only |
| Long-term / cross-thread memory | LangGraph Memory Store when enabled by harness ([persistence](https://docs.langchain.com/oss/python/langgraph/persistence)) | Encrypt/PII policy in app config |
| Model binding (Gemini, Kimi) | Pass `model=` into `create_deep_agent` / agent config per customization docs | Thin **profile** wrapper that returns LC `BaseChatModel` |

**Anti-patterns**

- A parallel **`while`/`for` “PortfolioAgentRunner”** that re-implements tool dispatch, todo lists, or subagent spawning **outside** what `create_deep_agent` already wraps.
- Replacing Deep Agents’ FS tools with ad-hoc string blobs **in the LLM** for work the harness already offloads to backends.

**Filesystem tools vs portfolio storage**

- Deep Agents’ virtual FS is for **agent working context** (scratch, large tool results)—**not** a replacement for canonical artifact storage. **Applying** an artifact still goes through **validated apply** tools that write via `StorageAdapter` and DB rules.

---

## 6. Multimodel strategy (Gemini + Kimi K2.5)

**Goal:** Runtime-swappable models (future UI selector) without changing domain tool code.

### 6.1 Model profiles

Each selectable model is a **profile**:

- `profile_id`
- `provider`: e.g. `gemini_google` | `openai_compatible` (Kimi)
- `model_name`: e.g. `gemini-2.5-flash`, `kimi-k2.5`
- Auth: env keys / config
- `capabilities`: e.g. `native_google_search_grounding`, `web_search`, `vision`, `thinking`

Session or message may carry `model_profile_id` (future: column on `ConversationSession`).

### 6.2 Gemini + native Google Search grounding

Use LangChain `ChatGoogleGenerativeAI` with Google Search as a **native** tool:

- `bind_tools([{"google_search": {}}])` or pass `tools=[{"google_search": {}}]` on invoke per LC docs.

Reference: [ChatGoogleGenerativeAI — Google Search](https://docs.langchain.com/oss/python/integrations/chat/google_generative_ai)

**Structured output + search:** follow LangChain guidance (e.g. `bind` with `response_mime_type` / schema—not conflicting patterns with `with_structured_output` where docs warn).

### 6.3 Kimi K2.5 (Moonshot)

- OpenAI-compatible API: `base_url` e.g. `https://api.moonshot.ai/v1`, model e.g. `kimi-k2.5`.
- Tool use and **official web search** per Moonshot docs.

Reference: [Kimi K2.5 quickstart / tool use](https://platform.moonshot.ai/docs/guide/kimi-k2-5-quickstart)

**Constraint:** Kimi K2.5 **thinking** mode may be **incompatible** with native web search in some configurations. **Harness policy:** for search rounds, disable thinking for that request or use a **server-side generic** `web_search` tool.

### 6.4 Single semantic “search” in prompts

The agent reasons about **web_search** as one capability; **implementation** is profile-specific (Gemini grounding vs Kimi native vs generic server search).

---

## 7. Tooling model and portfolio domain tools

**Categories**

1. **Provider-native** — e.g. `google_search` on Gemini (via LC), Kimi web search where enabled.
2. **Portfolio domain (server)** — deterministic, entity-scoped:
   - list/read artifacts (and resources as needed)
   - resolve artifact target; validate; **apply_edit** (only mutator for artifact content)
3. **Delegation** — Deep Agents **native** subagent / `task` tool with narrow tools (see [Subagents](https://docs.langchain.com/oss/python/deepagents/subagents)); configure with dicts (`name`, `description`, `system_prompt`, `tools`, optional `model`) rather than a custom runner.
4. **Future** — `mcp.invoke` behind allowlist.

**Rules (writes)**

- **Edit existing artifact content** (versioned or overwrite): only through validated **`artifact_apply_edit`** (or equivalent single mutator), never raw model file access.
- **Create new artifact rows/files** today: **presets** use `create_artifact_for_entity`; the harness may later gain an explicit `artifact_create` tool—until then, do not assume the agent can create canonical artifacts except via defined tools/routes.
- **Other REST** (e.g. manual artifact upload APIs if present): unchanged until explicitly integrated with the harness.

---

## 8. Artifact editing: modes, resolver, pipeline, audit

### 8.1 Mode selection (precedence)

1. Explicit user wording (“overwrite” / “new version”) when clear
2. Policy / feature flags (overwrite gated)
3. Agent fallback: default **versioned**; overwrite only when policy allows

**Matrix (initial)**

- Substantive edits → `versioned`
- Cosmetic overwrite → only if user asked + policy allows
- Sensitive artifacts → `versioned` always
- Ambiguous → clarify in thread or default `versioned`

### 8.2 Target resolution

- Required: `entity_id`
- Optional: `artifact_id`, title, type, version hints
- Rank by id match, title, recency, session hints; below confidence → **disambiguation only, no write**

### 8.3 Tool contract (illustrative)

Non-exhaustive; names may map to one or more Python tools:

- `artifact_resolve_target` → id, version, path, confidence
- `artifact_read_content` → content, checksum, metadata
- `artifact_validate_edit` → ok, violations
- `artifact_apply_edit` → **only** mutating apply after validation

Representations: full replace first; structured patches later.

### 8.4 State machine (edit lifecycle, Option B)

Use names that do **not** imply Option C’s two-pass LLM planner:

`intent_received` → `target_resolved` → `mode_resolved` → `edit_payload_validated` → `applied` | `failed`

- **`edit_payload_validated`**: deterministic server validation passed (content size, JSON/markdown checks, policy).
- **`failed`**: disambiguation, validation error, conflict, timeout, or model/tool error before safe apply.

**Option C (future):** may insert internal sub-states (e.g. `llm_plan_proposed`, `llm_plan_validated`) *before* `edit_payload_validated`; keep Option B states stable so audits stay comparable.

Log every transition with a **correlation id** (and optional link to agent `run_id`).

### 8.5 `artifact_edit_events` (audit table)

Fields include: ids, session, requested/resolved mode, state, intent, **bounded** JSON blobs for model/tool context and validation results (avoid naming that implies Option C only), before/after checksums, timestamps. If Option C is enabled later, the same columns can store planner output with a `pipeline_version` or `edit_pipeline` discriminator.

---

## 9. Option B vs Option C (writes): scope and future upgrade

### 9.1 Option B (current release target)

**Per apply operation:** model proposes an edit (may span **multiple agent turns** and tool calls); the server runs **deterministic validate → apply** once the payload is ready. **No** separate user-facing “plan mode.”

**Not implied by Option B:** a single LLM call for the whole session—Deep Agents may use many steps; Option B scopes **write safety** at the **apply boundary**.

### 9.2 Option C (future, internal only)

**Planner** produces structured plan → validated → **Executor** generates patch/content under constraints → apply via same tools.

**Triggers to adopt Option C**

- Frequent complex multi-artifact writes, compliance need for explainable plans, high failure rate on single-pass applies, etc.

**Orthogonal to subagents**

- **Subagents** = delegation for research/drafting.
- **Option C** = staged **write** pipeline when edits are too fragile for one round.

**API**

If ever exposed over HTTP, prefer **internal/admin/test** routes only. **Primary:** `POST .../messages` performs plan→execute internally.

**Feature flag:** e.g. `CHAT_ARTIFACT_EDIT_ENABLE_OPTION_C=false` until pilot.

---

## 10. API surface

**Implemented contracts** (legacy vs deep agent, `use_deep_agent`, `202` + `GET .../jobs/{job_id}`, schemas) are documented in **`docs/API_REFERENCE.md`**.

**Design intent (summary):** entity-scoped chat messages; harness path persists user text then completes assistant text asynchronously; mutating artifacts flows through Option B tools + audit. Optional admin/test routes remain out of scope unless needed.

---

## 11. Orchestration: stay inside Deep Agents unless forced out

- **Deep Agents** already sits on **LangGraph** for durable execution, streaming, human-in-the-loop, etc.—treat that as the default orchestration layer ([overview](https://docs.langchain.com/oss/python/deepagents/overview)).
- **Do not** add a separate hand-rolled ReAct loop in FastAPI “because it is simpler”; it will diverge from upstream behavior and break upgrades.
- **When to touch lower-level LangGraph:** only via **documented Deep Agents extension points** (customization guide, middleware, backends, compiled sub-graphs if officially supported) or after proving the harness cannot express a requirement—and then **minimize** custom graph surface area.
- A **standalone LangGraph-only** app (no Deep Agents) remains a **last resort**: higher control, but you rebuild harness features you explicitly wanted to avoid reimplementing.
- **Portfolio code** stays focused on **domain tools**, **apply pipeline**, **audit**, and **FastAPI/session mapping** into `agent.invoke({...})`.

---

## 12. Rollout roadmap

| Phase | Focus |
|-------|--------|
| **1 — Foundation** | Deep Agents behind chat (flag); domain tools; edit state machine; `artifact_edit_events`; versioned apply first; tool trace persisted |
| **2 — Overwrite + policy** | Overwrite flag; concurrency; in-thread clarification only |
| **3 — Harness maturity** | Subagent profiles; richer read/list; internal Option C for selected paths; optional streaming |
| **4 — Scale** | Long-running jobs; MCP bridge; observability; evaluate heavier LangGraph customization only if needed |
| **5 — Ongoing** | New tools/profiles via config (diligence, legal, IC memo) |

**Multimodel rollout**

- Land **Gemini + grounding** first; add **Kimi** after adapter + search policy tests pass.

---

## 13. Implementation checklist (file-mapped)

### Future phases (post–Option B ship; aligns with §12)

- [ ] Subagent profiles using **Deep Agents subagent dicts** (native), not a custom subprocess spawner
- [ ] Trace/replay/async: prefer **LangGraph checkpointing / Memory Store / harness backends** per docs before new `agent_run` tables (add SQLite only if required for VC-specific auditing beyond LangSmith)
- [ ] Streaming / step events via **harness-native streaming** (optional UI)
- [ ] Long-running job queue when sync budget exceeded (hybrid: checkpoint-native resume where possible)
- [ ] MCP via documented integration patterns + allowlist + audit
- [ ] Option C internal pilot (§9)
- [ ] Deeper LangGraph only through **documented Deep Agents extension** or justified exception (§11)

### Phase 0 — Prep and guardrails

- [ ] Config: default edit mode `versioned`; overwrite off by default; max artifact rewrite size; feature flags; **agent limits** (recursion/turn caps, timeouts) prefer **Deep Agents / LangGraph documented config** over ad-hoc loops
- [ ] Document in `docs/API_REFERENCE.md`, `docs/DEVELOPER_GUIDE.md`

**Files:** `backend/app/config.py`, docs above.

### Phase 1 — Data model and schemas

- [ ] `artifact_edit_events` model + SQLite init
- [ ] Schemas: request/response types for internal/admin flows if needed; optional `ResolvedTarget`, `ModeResolution`, `EditWarning`

**Files:** `backend/app/models.py`, `backend/app/database.py`, `backend/app/schemas.py`

### Phase 2 — Services: harness + editing

**2.0 Deep Agents integration (chat entry)**

- [ ] Build agent via **`create_deep_agent`** per [Quickstart / Customization](https://docs.langchain.com/oss/python/deepagents/quickstart/) — pass **`model`**, **`tools`** (domain), **`system_prompt`**, and harness options from docs (middleware, **backend**/store, subagents) rather than wrapping a second loop
- [ ] Model factory: **Gemini** profile (`ChatGoogleGenerativeAI` + `google_search` when enabled); **Kimi** profile (`ChatOpenAI` + Moonshot `base_url`; search/thinking policy)
- [ ] Tracing: enable **LangSmith** per Deep Agents docs before building custom trace plumbing; add DB correlation only if product requires it
- [ ] Optional: MCP via mechanisms described in LC ecosystem when adopted—do not duplicate MCP client framework in-app

**Files:** `backend/app/routers/chat.py`, thin module e.g. `backend/app/services/portfolio_deep_agent.py` (factory + `invoke` wiring only), `backend/app/services/model_profiles.py`; may shrink `gemini_runner.py` as LC becomes the primary caller

**2.1–2.4 Editing core**

- [ ] Target resolver (no mutate)
- [ ] Mode resolver (policy precedence)
- [ ] Validate + apply (`versioned` / `overwrite`) + checksums
- [ ] Audit event lifecycle

**Files:** `backend/app/services/artifact_editing.py` (new), extend `artifact_service.py`, `storage.py` if needed

### Phase 3 — Router (`chat.py`)

- [ ] Feature-flag: harness vs legacy `generate_with_context`
- [ ] Pass context hints; presets unchanged unless explicitly unified
- [ ] Final assistant message summarizes actions; optional `_vc_chat` artifact cards
- [ ] Optional admin-only routes only

**Files:** `backend/app/routers/chat.py`, `frontend/src/lib/chatArtifactCard.ts`

### Phase 4 — Prompts

- [ ] Edit-safe and tool-use instructions in `prompt_assembly` / agent system prompt
- [ ] Option B: JSON payloads are **normalized intent**, not separate Option C planner

**Files:** `backend/app/services/prompt_assembly.py`

### Phase 5 — Frontend

- [ ] Extend chat client types for optional `tool_trace` / `run_id` / `job_id`
- [ ] No **required** edit-only API for UX
- [ ] Optional power-user mode hint (hidden is fine)
- [ ] Future: run progress UI when streaming/jobs exist

**Files:** `frontend/src/services/api.ts`, `frontend/src/types/index.ts`, `EntityConversation.tsx`, `EntityDetail.tsx`, CSS as needed

### Phase 6 — Tests

- [ ] Unit: resolvers, validators, apply modes, **model adapter** (Gemini search binding; Kimi base_url + thinking/search policy)
- [ ] Integration: mocked tool loop; no mutate on disambiguation; audit on all attempts; multimodel adapters
- [ ] Regression: presets + existing chat
- [ ] Optional E2E (keys): grounding (Gemini), search (Kimi policy), diligence-style multi-step (instrumented)

**Files:** `backend/tests/test_chat_api.py`, new `test_artifact_editing.py`, adapter tests as appropriate

### Phase 7 — Docs and ops

- [ ] API + architecture + runbook: modes, audit, troubleshooting, metrics

**Files:** `docs/API_REFERENCE.md`, `docs/ARCHITECTURE.md`, `docs/DEVELOPER_GUIDE.md`

### Suggested implementation order

1. Model profiles + adapters (Gemini grounding + Kimi base)
2. Domain tools + audit table
3. Deep Agents wiring + feature flag on `post_chat_message`
4. Tests + docs

### Definition of Done (MVP harness + edits)

- [ ] Chat-only: research + Q&A + edits without mandatory separate UI actions
- [ ] `versioned` + `overwrite` (latter gated)
- [ ] Deterministic mode resolution and apply path
- [ ] Full audit for mutating attempts
- [ ] No ambiguous-target writes
- [ ] Presets and legacy path preserved or intentionally superseded with migration note
- [ ] Gemini grounding + Kimi profile validated per test matrix (§14)
- [ ] Option C **not** in production path until flagged (§9)

---

## 14. Testing strategy

**Unit**

- Mode and target resolvers; markdown/json validation; apply semantics; checksums
- **Gemini:** `google_search` tool bound per LC pattern
- **Kimi:** OpenAI-compatible config; thinking disabled or fallback when search required (per Moonshot constraints)

**Integration**

- Mock LLM/tool loop: search → read → propose → apply / reject
- Subagent allowlist isolation (when enabled)
- Concurrency / optimistic locking if implemented

**E2E (staging, optional)**

- Gemini: grounded factual query
- Kimi: web search success under harness policy
- Diligence archetype: multi-round read + final versioned artifact

**Regression**

- Preset artifact creation; viewer compatibility

---

## 15. Open decisions and initial defaults

**Open**

1. Who may use overwrite in production first?
2. Overwrite: `updated_at` only vs extra snapshot policy?
3. Optional UI mode hint exposure?
4. Max artifact size for full rewrite vs patch-only?

**Defaults**

- Default apply mode: **versioned**
- Overwrite: **flagged off** for general users until policy ready
- High-confidence target required before apply
- Log all attempts including rejections

---

## 16. Implementation prerequisites

**Python packages (typical; pin together in `backend/requirements.txt`)**

- `deepagents` — harness ([PyPI](https://pypi.org/project/deepagents/))
- `langchain` / `langchain-core` — per Deep Agents / LC docs
- `langchain-google-genai` — `ChatGoogleGenerativeAI`, Gemini native tools (e.g. `google_search`)
- `langchain-openai` — `ChatOpenAI` for OpenAI-compatible providers (Kimi / Moonshot)

**Configuration**

- Env keys: e.g. `GOOGLE_API_KEY` or Gemini key as used today; `MOONSHOT_API_KEY` (or chosen name) for Kimi; existing app settings in `backend/app/config.py`.

**Verification before claiming “done”**

- [ ] One E2E path: **Gemini** + `google_search` grounding ([LC integration](https://docs.langchain.com/oss/python/integrations/chat/google_generative_ai))
- [ ] One E2E path: **Kimi** + search policy (thinking vs web search per [Moonshot docs](https://platform.moonshot.ai/docs/guide/kimi-k2-5-quickstart))
- [ ] One mocked path: **apply_edit** rejects invalid payload without DB/storage change

---

## 17. References

- [OpenClaw](https://github.com/openclaw/openclaw)
- [DeerFlow](https://github.com/bytedance/deer-flow)
- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview)
- [LangChain Deep Agents](https://www.langchain.com/deep-agents)
- [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents)
- [ChatGoogleGenerativeAI (Google Search)](https://docs.langchain.com/oss/python/integrations/chat/google_generative_ai)
- [Kimi K2.5 quickstart / tools](https://platform.moonshot.ai/docs/guide/kimi-k2-5-quickstart)

---

*End of unified design and plan.*
