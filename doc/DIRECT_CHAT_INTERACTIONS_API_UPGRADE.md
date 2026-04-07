# Direct Chat Path: Interactions API Upgrade & Model Swap Support

**Status:** Plan (v3.2)
**Date:** 2026-04-06
**Scope:** Portfolio chat direct path; cleanup of gemini_runner.py

---

## 1. Problem Statement

The portfolio chat "direct" path (non-agentic, `use_deep_agent=false`) has three limitations:

1. **Stateless Gemini calls** — Every turn replays up to 40 messages (text-only) via `generate_content()`. No server-side session memory. Wastes tokens, latency, and cost. Worse: prior turns' multimodal attachments are completely lost from history replay — only the current turn gets `context_parts`.
2. **Gemini-only** — The `model_profile_id` field exists on the request schema but the direct path ignores it. Model swapping (Gemini ↔ Kimi) only works in the agentic path.
3. **Hard history cutoff** — At 40 messages, older conversation context is silently dropped with no summarization. When the Interactions API chain breaks (stale ID, model swap), the full-history fallback loses everything beyond the 40-message window.

Gemini's **Interactions API** (`client.interactions.create()`) solves problem 1 with `previous_interaction_id` — server-managed conversation state retained for 55 days (paid tier), including full multimodal content from prior turns. Problem 2 requires a parallel Kimi direct-call path. Problem 3 requires a history summarization mechanism for chain-break fallbacks.

### Known Limitation (Future Work)

The current DB stores conversation history as text-only (`ConversationMessage.content` is a text column). When a Gemini Interactions API chain breaks and falls back to history replay, **all multimodal context from prior turns is lost** — the DB simply doesn't have it. The Interactions API chain masks this problem (Gemini's server retains everything), but any chain break exposes it.

Similarly, when switching Gemini → Kimi → Gemini, the fresh Gemini chain cannot recover multimodal context from the pre-Kimi period. A future upgrade should consider storing richer per-turn metadata (attachment references, interaction IDs per message) to enable better context recovery on chain breaks. For now, the text-only replay + summarization is the best we can do without a larger storage redesign.

---

## 2. Current Architecture

### 2.1 Direct Path Flow

```
POST /entities/{id}/chat/sessions/{sid}/messages  (use_deep_agent=false)
  │
  ├─ Load prior messages from DB → _history_from_messages() → List[(role, text)]
  │    └─ TEXT ONLY: multimodal attachments from prior turns are NOT in history
  │    └─ HARD CAP: last 40 messages (20 pairs), older messages silently dropped
  ├─ build_context_parts(nodes) → List[types.Part]  (Gemini multimodal, current turn only)
  ├─ build_portfolio_system_prompt() → system instruction string
  │
  └─ generate_with_context()   ← gemini_runner.py
       ├─ _history_to_contents() → Part.from_text() for each history entry
       ├─ Append user turn + context_parts (multimodal only on final message)
       └─ client.models.generate_content(model, contents, config)  ← OLD API
  │
  ├─ Persist user + assistant ConversationMessage rows
  └─ Return 200 ChatMessageResult
```

### 2.2 Call Sites of `gemini_runner.py`

| Call Site | Function | Purpose |
|-----------|----------|---------|
| `chat.py:603` — `post_chat_message` | `generate_with_context` | Multi-turn chat (**upgrade target**) |
| `chat.py:780` — `run_chat_preset` (markdown) | `generate_with_context` | One-shot preset |
| `chat.py:742` — `run_chat_preset` (JSON) | `generate_json_with_context` | One-shot preset |
| `metadata_preprocess_jobs.py:147` | `generate_json_with_context` | Single-file indexing |

### 2.3 What History Replay Actually Loses

| Content Type | In DB? | In history replay? | In Gemini server-side session? |
|---|---|---|---|
| User text | ✅ | ✅ (last 40 msgs) | ✅ (full conversation) |
| Assistant text | ✅ | ✅ (last 40 msgs) | ✅ (full conversation) |
| PDF/image attachments | ❌ (blob in storage) | ❌ | ✅ (retained with interaction) |
| Messages beyond 40 | ✅ (in DB) | ❌ (truncated) | ✅ (full conversation) |

---

## 3. Target Architecture

### 3.1 Interactions API Integration

```
POST /entities/{id}/chat/sessions/{sid}/messages  (use_deep_agent=false)
  │
  ├─ Load session (includes last_gemini_interaction_id + timestamp)
  ├─ Load prior messages from DB (needed for Kimi path, fallback, & summarization)
  ├─ Resolve model_profile_id → "gemini_google" | "kimi_moonshot"
  │
  ├─ IF gemini_google:
  │    ├─ Check: valid previous_interaction_id? (exists + within TTL)
  │    ├─ IF valid:
  │    │    └─ interactions.create(input=user_msg, previous_interaction_id=last_id)
  │    │       (Gemini remembers full context server-side — minimal tokens)
  │    ├─ IF invalid/missing:
  │    │    └─ Build summarized history (see §3.3)
  │    │    └─ interactions.create(input=[summary + history + user_msg])
  │    ├─ Try/catch: if Gemini rejects previous_interaction_id → fall back to fresh chain
  │    └─ Store new interaction.id + timestamp on session
  │
  ├─ IF kimi_moonshot:
  │    ├─ Clear session.last_gemini_interaction_id (invalidate Gemini chain)
  │    └─ OpenAI-compatible chat.completions.create(messages=[summary + history])
  │
  ├─ Persist user + assistant messages (with model_profile_id on both)
  └─ Return 200 ChatMessageResult  ← SAME SHAPE AS TODAY
```

### 3.2 Model Swap — Simple Invalidation with Frontend Confirmation

**When Kimi is used, clear `last_gemini_interaction_id` on the session.** Next Gemini call starts a fresh chain with history replay + summarization.

**Crucially, this is a destructive action** — the Gemini server-side session (including all multimodal context from prior turns) is permanently lost. The frontend must confirm before proceeding (see §3.5).

| Scenario | What Happens |
|----------|-------------|
| **Pure Gemini** | `previous_interaction_id` chains turns. Server-side memory handles everything. |
| **Pure Kimi** | Full history from DB every call. Stateless. |
| **Gemini → Kimi** | **Frontend confirms first (§3.5).** Kimi gets full text history. Gemini chain invalidated. |
| **Kimi → Gemini** | No valid interaction ID → fresh chain with summarized history. No confirmation needed (nothing to lose). |

**Known trade-off:** When switching back to Gemini after a Kimi period, the fresh chain loses multimodal context from the pre-Kimi Gemini turns. This is a consequence of text-only DB storage. A future upgrade could preserve per-message interaction IDs to enable partial chain resumption, but that requires a richer storage model.

### 3.5 Frontend Model Swap Confirmation

Switching from Gemini to Kimi destroys the Gemini Interactions API chain (server-side session memory including multimodal context). The user must explicitly confirm this.

**Backend surface:** Expose `has_gemini_chain: bool` on `ChatSessionResponse`, computed from `last_gemini_interaction_id is not None` and `_interaction_still_valid(session)`. The frontend already loads the session via SWR, so no extra round trip.

**Frontend flow:**

1. User sends a message with `model_profile_id = kimi_moonshot`
2. Before sending, frontend checks: `session.has_gemini_chain === true`?
3. If yes → show confirmation dialog:
   > **Switch to Kimi?**
   > This will end the current Gemini session. Multimodal context (images, PDFs) from earlier turns will be permanently lost. Text history is preserved.
   > [Cancel] [Switch & Send]
4. On confirm → send the message normally (backend clears chain)
5. On cancel → message not sent, model selector stays (user can revert or retry)

**When confirmation is NOT needed:**
- `has_gemini_chain` is false (no chain to lose — Kimi→Kimi, fresh session, chain expired)
- Switching Kimi → Gemini (starts a fresh chain, nothing to destroy)
- Agentic path (no Interactions API chain involved)

### 3.3 History Summarization on Chain Break

When the Interactions API chain is unavailable, the fallback replays history from DB. Currently this is a hard truncation at 40 messages.

**Design:** If conversation exceeds the history window, summarize the truncated portion and prepend it.

```python
async def _build_history_with_summary(
    all_messages: List[ConversationMessage],
    max_pairs: int,
) -> Tuple[List[Tuple[str, str]], Optional[str]]:
    """
    If conversation fits in window: returns all messages, no summary.
    If exceeds: summarizes older portion via cheap flash model,
    returns recent window + summary preamble.
    """
    history = _history_from_messages(all_messages, max_pairs)

    total_eligible = sum(1 for m in all_messages if m.role in ("user", "assistant"))
    if total_eligible <= max_pairs * 2:
        return history, None

    # Older messages that got truncated
    all_pairs = _history_from_messages(all_messages, len(all_messages))
    truncated = all_pairs[: -(max_pairs * 2)]

    summary = generate_one_shot(  # uses cheap flash model
        system_instruction="You are a conversation summarizer.",
        history=[],
        user_message_text=(
            "Summarize this conversation concisely. "
            "Preserve key facts, decisions, and context.\n\n"
            + "\n".join(f"{r.title()}: {t}" for r, t in truncated)
        ),
        enable_google_search=False,
        model=settings.GEMINI_METADATA_EXTRACTION_MODEL,
    )

    preamble = f"[Summary of earlier conversation ({len(truncated)} messages):\n{summary}\n]"
    return history, preamble
```

**When it runs:** Fresh chain creation, stale ID fallback, Kimi calls (when conversation exceeds window).

**When it does NOT run:** Chained Gemini turns (server-side memory — no replay needed).

### 3.4 Staleness Guard

```python
# config.py
GEMINI_INTERACTION_TTL_DAYS: int = 50   # 55 - 5 buffer; set to 0 for free tier

def _interaction_still_valid(session) -> bool:
    if not session.last_gemini_interaction_id:
        return False
    if not session.last_gemini_interaction_at:
        return False
    age = (utc_now() - session.last_gemini_interaction_at).days
    return age < settings.GEMINI_INTERACTION_TTL_DAYS
```

Defense in depth: TTL pre-check avoids wasted API call; try/catch handles unexpected rejection.

```python
def _is_interaction_not_found(e: Exception) -> bool:
    """Check if a Gemini exception indicates the previous_interaction_id was invalid/expired."""
    # Gemini SDK raises google.genai.errors.ClientError with status 404 for expired interactions.
    # Match on error type + message pattern to avoid catching unrelated errors.
    err_str = str(e).lower()
    return "not found" in err_str or "interaction" in err_str
```

This helper is intentionally broad-match with a narrow call site (only used when `prev_id` was set). If the SDK introduces a typed exception later, tighten the check.

---

## 4. Schema Changes

### 4.1 `ConversationSession` — Add 2 Columns + 1 Computed Property

```python
class ConversationSession(Base):
    # ... existing columns ...
    last_gemini_interaction_id = Column(String, nullable=True)
    last_gemini_interaction_at = Column(DateTime, nullable=True)

    @property
    def has_gemini_chain(self) -> bool:
        """Whether this session has a valid (non-expired) Gemini Interactions API chain."""
        if not self.last_gemini_interaction_id or not self.last_gemini_interaction_at:
            return False
        from app.config import settings
        age = (utc_now() - self.last_gemini_interaction_at).days
        return age < settings.GEMINI_INTERACTION_TTL_DAYS
```

Pydantic's `from_attributes = True` on `ChatSessionResponse` reads Python properties, so this surfaces automatically in API responses without manual serialization logic.

### 4.2 `ConversationMessage` — Add 2 Columns

```python
class ConversationMessage(Base):
    # ... existing columns ...
    model_profile_id = Column(String, nullable=True)  # "gemini_google", "kimi_moonshot"
    node_ids_json = Column(Text, nullable=True)        # JSON array of workspace node IDs
```

**`model_profile_id`:** On user messages — which model the message was sent to. On assistant messages — which model produced the response.

**`node_ids_json`:** On user messages only — which workspace nodes (files) were attached to this turn (e.g., `["node-abc", "node-def"]`). NULL when no files attached. NULL on assistant messages.

This is **provenance metadata, not a multimodal recovery mechanism.** The node IDs point to mutable workspace objects — files can be overwritten, moved, or deleted after the turn. Storing them enables:

- UI display: "📎 2 files attached" on historical messages
- Debugging: what context did the model receive on turn N?
- Analytics: which files are most referenced in conversations?
- Parity with the agentic path (which already stores `node_ids_json` on `ChatCompletionJob`)

What it does **not** solve: faithfully reconstructing the multimodal content Gemini saw on a prior turn. That would require immutable content snapshots (version hashes or blob refs per turn) — a storage redesign tracked in §1 as future work.

### 4.3 Migration

Dev phase: `python scripts/reset_sqlite_db.py --yes`. No production data.

---

## 5. File Change Plan

### 5.1 Replace `gemini_runner.py` → `direct_llm.py`

`gemini_runner.py` is deleted. A new `direct_llm.py` absorbs all its functionality and adds the new paths. This is the single module for all direct (non-LangChain) LLM calls.

**Why not keep both?** No backward compatibility needed. Having two files that both call Gemini with overlapping helpers (`_get_client`, retry logic, text extraction) creates confusion. One file, clear sections.

**Structure:**

```python
"""Direct LLM calls: Gemini (Interactions API + one-shot) and Kimi (OpenAI-compatible)."""

# ── Shared ────────────────────────────────────────────────────

def _get_client() -> genai.Client:
    """Gemini API client (from GEMINI_API_KEY / GOOGLE_API_KEY)."""

def _extract_text(response) -> str:
    """Extract text from Gemini response (interaction or generate_content)."""

def _retry(fn, max_attempts=3):
    """Retry with exponential backoff."""

# ── Gemini: Interactions API (session-stateful chat) ──────────

def generate_with_interaction(
    system_instruction: str,
    user_message_text: str,
    context_parts: Optional[List[types.Part]] = None,
    enable_google_search: Optional[bool] = None,
    previous_interaction_id: Optional[str] = None,
    history_for_fresh_chain: Optional[Sequence[Tuple[str, str]]] = None,
) -> Tuple[str, str]:
    """
    Interactions API call.
    Returns: (reply_text, new_interaction_id)

    If previous_interaction_id is valid: sends only the new turn.
    If None: includes history_for_fresh_chain in input.
    """

# ── Gemini: One-shot (presets, metadata, summarization) ───────

def generate_one_shot(
    system_instruction: str,
    history: Sequence[Tuple[str, str]],
    user_message_text: str,
    context_parts: Optional[List[types.Part]] = None,
    enable_google_search: Optional[bool] = None,
    model: Optional[str] = None,
) -> str:
    """Stateless generate_content call. Replaces old generate_with_context."""

def generate_json_one_shot(
    system_instruction: str,
    history: Sequence[Tuple[str, str]],
    user_message_text: str,
    context_parts: Optional[List[types.Part]] = None,
    enable_google_search: Optional[bool] = None,
    model: Optional[str] = None,
) -> str:
    """JSON-constrained one-shot. Replaces old generate_json_with_context."""

# ── Kimi: OpenAI-compatible (stateless chat) ──────────────────

def generate_with_kimi(
    system_instruction: str,
    history: Sequence[Tuple[str, str]],
    user_message_text: str,
) -> str:
    """OpenAI-compatible chat call to Kimi/Moonshot. Stateless."""
```

### 5.2 Update All Importers

Since we're replacing `gemini_runner.py`, every import must be updated:

| File | Old Import | New Import |
|------|-----------|------------|
| `routers/chat.py` | `from app.services.gemini_runner import generate_with_context, generate_json_with_context` | `from app.services.direct_llm import generate_with_interaction, generate_with_kimi, generate_one_shot, generate_json_one_shot` |
| `services/metadata_preprocess_jobs.py` | `from app.services.gemini_runner import generate_json_with_context` | `from app.services.direct_llm import generate_json_one_shot` |
| `tests/test_chat_api.py` | `monkeypatch("app.routers.chat.generate_with_context", ...)` | `monkeypatch("app.routers.chat.generate_one_shot", ...)` etc. |
| `tests/test_metadata_preprocess.py` | `monkeypatch("...gemini_runner.generate_json_with_context", ...)` | `monkeypatch("...direct_llm.generate_json_one_shot", ...)` |

### 5.3 `backend/app/routers/chat.py` — Direct Path Dispatch

**Context-building must be profile-aware.** The current code (lines 524-526) unconditionally calls `build_context_parts(nodes)` for non-deep-agent, which returns Gemini `types.Part` objects. Kimi needs `build_harness_user_attachment_text(nodes)` instead. Fix: resolve profile *before* building context.

Replace the current non-deep-agent context block (lines 524-526) and direct-path call (lines 602-630) with:

```python
# ── Pre-dispatch: resolve profile + build appropriate context ──
profile_id = normalize_profile_id(body.model_profile_id)

if profile_id == "kimi_moonshot":
    attach_preamble, warnings = build_harness_user_attachment_text(nodes)
    context_parts = None  # Kimi doesn't use types.Part
else:
    attach_preamble = ""
    context_parts, warnings = await build_context_parts(nodes)

# ... system_prompt build (unchanged) ...

# ── Direct path dispatch ──
if profile_id == "kimi_moonshot":
    # Kimi: stateless, text-only attachments

    history_pairs, summary_preamble = await _build_history_with_summary(
        prior, settings.CHAT_MAX_HISTORY_MESSAGES // 2
    )
    user_text = body.text.strip()
    if attach_preamble:
        user_text = attach_preamble + "\n\n--- User message ---\n" + user_text
    if summary_preamble:
        user_text = summary_preamble + "\n\n" + user_text

    reply_text = generate_with_kimi(
        system_instruction=system_prompt,
        history=history_pairs,
        user_message_text=user_text,
    )

    # Invalidate Gemini chain
    session.last_gemini_interaction_id = None
    session.last_gemini_interaction_at = None

else:
    # Gemini with Interactions API
    prev_id = (
        session.last_gemini_interaction_id
        if _interaction_still_valid(session)
        else None
    )

    if prev_id:
        history_for_fresh = None
        summary_preamble = None
    else:
        history_pairs, summary_preamble = await _build_history_with_summary(
            prior, settings.CHAT_MAX_HISTORY_MESSAGES // 2
        )
        history_for_fresh = history_pairs

    user_text = body.text.strip()
    if summary_preamble:
        user_text = summary_preamble + "\n\n" + user_text

    try:
        reply_text, new_id = generate_with_interaction(
            system_instruction=system_prompt,
            user_message_text=user_text,
            context_parts=context_parts,
            previous_interaction_id=prev_id,
            history_for_fresh_chain=history_for_fresh,
        )
    except Exception as e:
        if prev_id and _is_interaction_not_found(e):
            # Chain broke — fall back to fresh
            if not history_for_fresh:
                history_pairs, summary_preamble = await _build_history_with_summary(
                    prior, settings.CHAT_MAX_HISTORY_MESSAGES // 2
                )
                history_for_fresh = history_pairs
                if summary_preamble:
                    user_text = summary_preamble + "\n\n" + body.text.strip()
            reply_text, new_id = generate_with_interaction(
                system_instruction=system_prompt,
                user_message_text=user_text,
                context_parts=context_parts,
                previous_interaction_id=None,
                history_for_fresh_chain=history_for_fresh,
            )
        else:
            raise

    session.last_gemini_interaction_id = new_id
    session.last_gemini_interaction_at = utc_now()

# Persist messages with model provenance + file provenance
user_msg = ConversationMessage(
    id=str(uuid.uuid4()),
    session_id=session_id,
    role="user",
    content=body.text.strip(),
    model_profile_id=profile_id,
    node_ids_json=json.dumps(body.node_ids) if body.node_ids else None,
)
assistant_msg = ConversationMessage(
    id=str(uuid.uuid4()),
    session_id=session_id,
    role="assistant",
    content=reply_text,
    model_profile_id=profile_id,
)
```

### 5.4 Preset calls — update to new function names + Kimi dispatch

In `run_chat_preset()`, the non-deep-agent branch has the same two issues as the chat path: (a) function renames, and (b) always calls Gemini regardless of `model_profile_id`. The same context-building fix applies: resolve profile, then branch context building (Gemini `context_parts` vs Kimi `attach_preamble`).

```python
# Non-deep-agent preset path:
profile_id = normalize_profile_id(body.model_profile_id)

if profile_id == "kimi_moonshot":
    attach_preamble, warnings = build_harness_user_attachment_text(nodes)
    context_parts = None
else:
    attach_preamble = ""
    context_parts, warnings = await build_context_parts(nodes)

# Then dispatch:
if preset.output_kind == "json":
    if profile_id == "kimi_moonshot":
        raw_json = generate_with_kimi(system_instruction=system_prompt, ...)
    else:
        raw_json = generate_json_one_shot(...)   # was generate_json_with_context
else:
    if profile_id == "kimi_moonshot":
        deliverable_body = generate_with_kimi(system_instruction=system_prompt, ...)
    else:
        deliverable_body = generate_one_shot(...)  # was generate_with_context
```

Note: Kimi preset calls don't invalidate Gemini chains (presets are one-shot, not part of the session turn sequence).

### 5.5 Other File Changes

| File | Change |
|------|--------|
| `models.py` | Add 4 columns (§4) |
| `schemas.py` | Add `model_profile_id: Optional[str] = None` to `ChatMessageResponse`; add `has_gemini_chain: bool = False` to `ChatSessionResponse` (§3.5) |
| `config.py` | Add `GEMINI_INTERACTION_TTL_DAYS: int = 50`; rename "legacy" → "direct" in comments |
| `.env_sample` | Add `GEMINI_INTERACTION_TTL_DAYS` with docs; rename comment |
| `SidebarModelSelect.tsx` | Update tooltip (§7.B) |
| `EntityConversation.tsx` | Add model-swap confirmation dialog before sending Kimi message when `session.has_gemini_chain` (§3.5) |
| `types/index.ts` | Add `has_gemini_chain?: boolean` to session type |

**`has_gemini_chain` on `ChatSessionResponse`:** Computed property, not a stored column. Backend computes it in the session response serializer from `_interaction_still_valid(session)`. This avoids exposing internal fields (`last_gemini_interaction_id`, timestamps) to the frontend.

### 5.6 Frontend: Model Swap Confirmation Dialog

In `EntityConversation.tsx`, intercept the message-send handler:

```typescript
// Before calling postMessage:
if (
  profileId === 'kimi_moonshot' &&
  !chatAgentOn &&              // direct mode only
  session?.has_gemini_chain    // active Gemini chain exists
) {
  const confirmed = await showModelSwapConfirm();
  if (!confirmed) return;      // user cancelled — don't send
}
// proceed with postMessage(...)
```

The confirmation dialog is a simple modal/alert with:
- **Title:** "Switch to Kimi?"
- **Body:** "This will end the current Gemini session. Multimodal context (images, PDFs) from earlier turns will be permanently lost. Text history is preserved."
- **Actions:** [Cancel] [Switch & Send]

Implementation: a small `useState`-driven dialog component or `window.confirm()` for MVP (upgrade to styled modal later).

---

## 6. What Does NOT Change

| Component | Why |
|-----------|-----|
| **Agentic path** (`portfolio_deep_agent.py`) | LangChain execution. Interactions API doesn't apply. |
| **Academic module** | Separate system, separate DB. |
| **`gemini_context.py`** | `build_context_parts()` returns `types.Part` — Interactions API accepts the same format. |
| **`model_profiles.py`** | Kimi credentials reused by `generate_with_kimi()`. `_kimi_openai_credentials()` and `_kimi_code_request_extras()` are `_`-prefixed but imported by `direct_llm.py` (same services package, convention not enforcement). LangChain builders untouched. |
| **`prompt_assembly.py`** | System prompts are model-agnostic. |
| **Frontend response contract** | 200/202 shape preserved. `model_profile_id` + `has_gemini_chain` on response are additive. |

**Minor parity fix (agentic path):** `run_chat_agent_job()` line 280 creates assistant `ConversationMessage` without `model_profile_id`. The job already stores it (line 97). Add `model_profile_id=job.model_profile_id` to the assistant message for consistency. One-line change.

---

## 7. Discovered Issues (Fix as Final Step)

### A. Presets Ignore `model_profile_id` in Direct Path

`run_chat_preset()` non-deep-agent branch always calls Gemini. Also has the same context-building issue — always calls `build_context_parts()` (Gemini multimodal), never `build_harness_user_attachment_text()` for Kimi.

**Fix:** Resolve profile first, branch context building, then dispatch: if `kimi_moonshot`, call `generate_with_kimi`; if `gemini_google`, call `generate_one_shot` / `generate_json_one_shot`. See §5.4.

### B. SidebarModelSelect Tooltip Is Misleading

Current: `"Used for portfolio chat when the server runs the Deep Agent harness."`

**Fix:** `"Model used for portfolio chat and presets (both direct and agent modes)."`

---

## 8. Multimodal Attachments During Kimi Turns

When `kimi_moonshot`: use `build_harness_user_attachment_text()` (text extraction — same as agentic Kimi path). When `gemini_google`: use `build_context_parts()` (native multimodal `types.Part`). No new code — but the call site must be restructured so context building happens *after* profile resolution (see §5.3, §5.4). The current code unconditionally calls `build_context_parts()` for non-deep-agent, which produces Gemini-specific `types.Part` objects useless to Kimi.

---

## 9. Test Impact

| Test File | Changes |
|-----------|---------|
| `test_chat_api.py` | Update mock targets to `direct_llm.*`. Add mocks for `generate_with_interaction` (`(str, str)` return) and `generate_with_kimi` (`str` return). Add test cases for Kimi dispatch, model provenance, session bookmark. |
| `test_chat_e2e_llm.py` | Verify interaction ID stored after real Gemini call. |
| `test_metadata_preprocess.py` | Update mock target from `gemini_runner.generate_json_with_context` to `direct_llm.generate_json_one_shot`. |
| `test_model_profiles.py` | No changes. |

---

## 10. Implementation Order

### Phase 1: Schema + Module Replacement

1. Add 4 columns to `models.py` (session: 2, message: 2); update `schemas.py`
2. Create `direct_llm.py` with all 4 public functions
3. Delete `gemini_runner.py`
4. Update all imports (`chat.py`, `metadata_preprocess_jobs.py`)
5. Rename function calls: `generate_with_context` → `generate_one_shot`, `generate_json_with_context` → `generate_json_one_shot`
6. Reset dev DB
7. Rename "legacy" → "direct" in comments/docs/config

**At this point:** Everything works exactly as before, just cleaner names and module structure. No behavior change yet.

### Phase 2: Interactions API + Kimi Direct + Summarization

8. Implement `generate_with_interaction()` in `direct_llm.py`
9. Implement `generate_with_kimi()` in `direct_llm.py`
10. Implement `_is_interaction_not_found(e)` helper (check Gemini SDK exception for stale/invalid interaction ID)
11. Wire dispatch in `post_chat_message()` — including profile-aware context building (§5.3)
12. Implement `_build_history_with_summary()`
13. Staleness guard + try/catch fallback
14. Session bookmark management (update on Gemini, clear on Kimi)
15. Persist `model_profile_id` + `node_ids_json` on messages
16. Add `has_gemini_chain` to `ChatSessionResponse` (computed from session state)
17. Agentic path: add `model_profile_id` to assistant message in `run_chat_agent_job()` (parity — job already has it)

### Phase 3: Frontend, Tests, Docs, Bug Fixes

18. Frontend: add `has_gemini_chain` to session type, add model-swap confirmation dialog (§5.6)
19. Update all test files (mock targets + new test cases)
20. Fix preset `model_profile_id` dispatch + context building (§7.A, §5.4)
21. Fix SidebarModelSelect tooltip (§7.B)
22. Update `CLAUDE.md`, `.env_sample`

---

## 11. Risk Assessment

| Risk | Level | Mitigation |
|------|-------|------------|
| Import path changes break something | Low | Grep for all `gemini_runner` references; update comprehensively in Phase 1. |
| Interactions API behavior differs from `generate_content` | Medium | E2E test. `system_instruction` and `tools` re-specified per call. |
| Stale `previous_interaction_id` | Low | TTL pre-check + try/catch fallback. |
| Summarization quality on chain break | Low | Cheap flash model. Recent 40 messages still sent in full. Summary is additive context. |

---

## 12. Dependencies

**Python packages:** `google-genai>=1.56.0` (installed), `openai` (installed via `langchain-openai`). No new installs.

**Config:**

| Setting | Type | Default | Purpose |
|---------|------|---------|---------|
| `GEMINI_INTERACTION_TTL_DAYS` | int | 50 | Staleness threshold. Set 0 for free tier. |

---

## 13. Resolved Design Decisions

1. **Replace `gemini_runner.py` entirely** with `direct_llm.py`. No backward compat needed. One file for all direct LLM calls.

2. **No gap injection on model swap.** Clear the Gemini chain on Kimi use. Fresh replay on return. Simple, correct.

3. **Summarize on chain break.** Flash model summarizes truncated history. Addresses the hard-cutoff problem.

4. **`model_profile_id` on messages: keep.** Future use for analytics, UI indicators, debugging.

5. **`node_ids_json` on messages: add as provenance.** Records which files were attached per turn. Enables UI display, debugging, analytics, and parity with the agentic path. Honestly framed: this is metadata, not a multimodal recovery mechanism — nodes are mutable references, not immutable snapshots.

6. **Track interaction ID per session only.** Last bookmark is sufficient.

7. **Known limitation acknowledged:** Text-only DB storage means chain breaks lose multimodal context. Interactions API chain masks this; breaks expose it. Future work: immutable per-turn content snapshots for reliable recovery.

8. **Frontend confirmation on Gemini→Kimi swap.** Switching to Kimi destroys the Gemini server-side session (multimodal context permanently lost). Backend exposes `has_gemini_chain` on session response; frontend shows confirmation dialog before sending the first Kimi message in a session with an active chain. No confirmation needed for Kimi→Gemini (nothing to lose) or when no chain exists.

9. **Profile-aware context building.** The non-deep-agent branch must resolve `model_profile_id` *before* building context parts. Gemini uses `build_context_parts()` (native multimodal `types.Part`); Kimi uses `build_harness_user_attachment_text()` (text extraction). This applies to both chat and preset paths.
