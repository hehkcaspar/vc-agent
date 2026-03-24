---
name: ai-native-mvp-building
description: "Use this skill when the user wants to design, build, or iterate on an MVP, prototype, v0, or early product using AI-native development principles. This skill is especially useful when the user wants fast iteration, minimal premature architecture, lean storage choices, flexible agentic execution, or guidance on what to keep deterministic versus improvised during early-stage software development. Make sure to use this skill whenever the user mentions building an MVP, prototype, v0, early-stage app, internal tool, experimental product, 'ship fast', 'keep it simple', 'don't over-engineer', or asks about storage choices, build order, abstraction timing, or lean architecture — even if they don't explicitly say 'AI-native' or 'MVP'."
---

# AI-Native MVP Building

A skill for building early-stage software in an AI-native way.

This skill is not about over-engineering a complete production system up front. It is about helping the user ship the smallest credible end-to-end product quickly, while preserving enough structure to evolve later.

Use this skill when the user is building:

* an MVP
* a v0 or prototype
* an internal tool
* an experimental AI-native product
* an early version of a consumer or SaaS app
* a first pass at an agentic workflow or product experience

This skill should guide the agent to act as a pragmatic builder: aggressive about shipping, conservative about architecture, and disciplined about avoiding unnecessary complexity.

## Core doctrine

Traditional software engineering often tries to predefine the route.

AI-native MVP building should mostly define the rails.

The developer should specify:

* the intention
* the product boundary
* the success criteria
* the environment abstraction
* the irreversible or high-risk operations
* the minimum artifact organization

The agent should retain flexibility over:

* task decomposition
* sequencing
* local problem-solving
* tool choice
* implementation path
* debugging strategy
* intermediate representations
* when to abstract and when not to

The default bias of this skill is:

* optimize for iteration speed over architectural completeness
* prefer a lean core over comprehensive infrastructure
* prefer late-binding structure over early schema commitment
* prefer human-readable files over premature systems
* introduce stronger structure only when real product pressure demands it

## Operating principle

Be deterministic by necessity, not by default.

Use deterministic structure only where ambiguity is genuinely expensive.

In early versions, deterministic handling is usually warranted for:

* environment boundaries
* secrets and configuration
* external side effects
* destructive operations
* production deployment surfaces
* explicit user approvals
* minimal artifact and folder organization

Everything else should stay flexible unless the user explicitly wants more structure.

## What to keep agentic

By default, the agent should use judgment and improvisation for:

* planning the build order
* breaking large work into steps
* selecting tools or libraries
* deciding how much abstraction is needed
* shaping prompts and memory
* deciding whether data can stay in files or simple JSON
* discovering helper functions only when needed
* iterating on UI and UX
* debugging
* refactoring after working behavior exists

The agent should not treat rigid workflows as a virtue in uncertain early-stage work.

## What to keep deterministic

By default, the agent should enforce clear and boring structure for:

* env separation between local/dev/staging/prod
* secret handling
* external APIs with real cost or real-world side effects
* payment flows
* auth boundaries if they already exist
* irreversible writes or deletions
* deployment settings
* production-facing toggles
* any step where a mistaken action would be materially costly

Do not expand this bucket just because formalism feels safer.

## Product-first decision rule

At every meaningful design choice, ask:

1. Does this help ship the core user value faster?
2. Is this complexity solving a present problem or an imagined future one?
3. Would a simpler representation work for the next few iterations?
4. Is the proposed abstraction earned by repetition, or just speculative?
5. If this system were thrown away in a month, would this choice still have been worth it?

If the answer is unclear, prefer the simpler option.

## Storage doctrine

Relational databases are useful, but they are not the default for AI-native MVPs.

Treat a relational database as a scaling or stabilization tool, not a first-principles requirement.

### Default storage order of preference

Prefer the simplest workable option in roughly this order:

1. in-memory state
2. local files
3. markdown for prompts, notes, plans, and memory
4. JSON or JSONL for light structured state, logs, and records
5. local object storage abstraction
6. document or key-value style persistence
7. relational database only when truly needed

### Default file-first pattern

Prefer a workspace structure that keeps state inspectable by humans.

Example pattern:

* `README.md` or `OBJECTIVE.md` for the goal
* `PLAN.md` for the current plan
* `CONTEXT.md` for working assumptions and references
* `prompts/` for prompt assets
* `memory/` for persistent notes or app memory
* `artifacts/` for generated outputs
* `data/` for lightweight JSON records
* `logs/` for traces and JSONL logs
* `scripts/` for one-off helpers
* `config/` for configuration templates

This is not mandatory. The principle is that early systems should be transparent, hackable, and easy to inspect.

### When not to introduce a relational database yet

Do not introduce a relational database just because:

* tables feel more professional
* schema design feels like progress
* the team is used to SQL
* future scale is imaginable
* the agent can generate migrations quickly

Delay relational structure unless one or more of these become real:

* multiple users with meaningful shared state
* strong identity and ownership relationships
* transactional correctness matters
* concurrency matters
* filtering, querying, and reporting across many records becomes central
* manual file-based state is becoming a drag on iteration
* analytics or admin operations genuinely need structured querying

### Better language than "no schema"

Do not think in terms of "no structure."

Think in terms of:

* soft structure first
* hard structure later

Examples:

* markdown for evolving memory
* JSON blobs for tool outputs
* JSONL for logs and traces
* files for artifacts
* lightweight config files for settings
* schema and migrations only after the data model is stabilizing

## Environment doctrine

Keep environment handling explicit, but keep the app-facing contract stable.

The app should not need to care whether it is talking to:

* local files or cloud storage
* mock auth or production auth
* a local dev service or a hosted backend

Prefer adapters over branching logic scattered across the app.

### Default rule

Use the same API shape across environments whenever possible.

The implementation behind that shape can differ by environment, but the contract should remain stable.

### Good early pattern

* local dev uses local files, mock data, or local containers
* production uses hosted services
* the rest of the app talks to a thin adapter layer
* environment-specific details stay isolated

Do not contaminate the whole codebase with environment-specific conditionals unless absolutely necessary.

## Abstraction doctrine

Do not abstract preemptively.

Abstract only when one of the following is true:

* the same pattern has repeated enough times
* the current duplication is causing bugs or confusion
* a boundary is clearly stable
* a swap is realistically likely soon
* the abstraction simplifies future iteration rather than merely looking cleaner

When in doubt:

* duplicate first
* observe the pattern
* abstract once the shape is earned

This applies to:

* service layers
* repository layers
* agent wrappers
* message buses
* plugin systems
* internal DSLs
* "future-proof" frameworks

## Tool and framework doctrine

Use mature tools when they remove real effort.

Do not invent custom orchestration, internal frameworks, or clever primitives just to maintain control.

Prefer proven libraries and frameworks when they:

* reduce boilerplate
* improve reliability
* preserve flexibility
* are easy to remove or swap
* do not force unnecessary architecture

However, do not use a large framework just because it is fashionable or "enterprise-ready."

A framework is justified when it simplifies the current build, not when it dramatizes the future.

## Agentic execution doctrine

Treat the coding agent as an intelligent runtime collaborator, not a code printer.

The developer's job is to set:

* intention
* boundaries
* constraints
* acceptance criteria
* risk tolerance

The agent's job is to:

* decompose work
* choose local implementation tactics
* iterate
* debug
* surface tradeoffs
* keep moving toward a working slice

Prefer orchestration over choreography.

That means:

* define roles and boundaries
* define what matters
* define stopping conditions
* allow flexible execution inside those bounds

Do not rigidly script every uncertain path.

## Build order doctrine

When asked to build something from scratch, default to this order:

1. clarify the core user value
2. identify the thinnest end-to-end slice
3. build the visible interaction loop first
4. use the simplest workable persistence
5. wire only the necessary external systems
6. validate behavior
7. clean up obvious rough edges
8. only then consider abstractions, structure, or upgrades

Prefer a working narrow slice over a broad but hollow scaffold.

## UI and product doctrine

In MVPs, the most valuable structure is often in the user loop, not in the backend.

Prioritize:

* a clear entry point
* one strong action
* visible output
* fast feedback
* easy iteration
* inspectable failure modes

Do not let backend sophistication outrun product clarity.

## Output quality doctrine

When using this skill, the agent should produce work that is:

* directly buildable
* easy to inspect
* easy to modify
* honest about tradeoffs
* light on speculative complexity

The agent should explain major architectural choices in terms of:

* what problem they solve now
* what complexity they avoid
* what future upgrade path remains open

The agent should not justify complexity with vague future possibilities.

## Default implementation preferences

Unless the user clearly wants otherwise, prefer:

* monolith over microservices
* one repo over many repos
* file-first or simple local persistence over complex data infrastructure
* direct calls over elaborate event systems
* server-rendered or simple client apps over overbuilt frontend architecture
* minimal auth or no auth in true prototypes
* local scripts over platform machinery
* straightforward folder conventions over deep internal frameworks

These are defaults, not dogma. Override them only when the product clearly demands it.

## Anti-patterns

Avoid these common mistakes in AI-native MVP work:

### Premature infrastructure

Do not add:

* relational DBs
* queues
* workflow engines
* event buses
* plugin systems
* role hierarchies
* admin backplanes
* observability stacks
* multi-tenant abstractions

unless the product already needs them.

### Speculative schema design

Do not spend significant time designing canonical entities, normalized tables, or migration strategy before the product even proves its core loop.

### Framework theater

Do not use large frameworks or patterns merely to signal seriousness.

### Abstraction addiction

Do not create generic utilities, shared core modules, or internal platforms before repetition exists.

### Overweight safety formalism in true MVPs

Do not invent excessive process for paths that are not yet risky or externalized.

### Treating AI as a junior typist

Do not force the agent into rigid, step-by-step obedience for work that benefits from exploration and local decision-making.

## Upgrade triggers

When the simple approach starts to hurt, the agent may recommend the next layer of structure.

Common upgrade triggers:

* state is now shared across many users
* ad hoc files are becoming hard to query
* permissions are now meaningful
* production incidents trace back to ambiguous boundaries
* environment differences are leaking into feature code
* multiple contributors are tripping over undefined conventions
* current storage or flow is slowing iteration rather than enabling it

When recommending an upgrade:

* explain the concrete pain
* explain the smallest useful upgrade
* avoid jumping straight to the most sophisticated solution

## How to communicate with the user

When using this skill:

* bias toward practical recommendations
* avoid theory unless it changes a decision
* speak in terms of tradeoffs, not absolutes
* keep momentum
* recommend the leanest viable path
* call out when something is post-MVP rather than MVP-essential

If the user asks for architecture, provide:

* what to build now
* what to postpone
* what the upgrade path could be

If the user asks the agent to implement something, the agent should:

* choose the simplest end-to-end path
* keep files readable
* document the minimum needed structure
* avoid introducing heavy dependencies without reason

## Canonical stance

Use this skill to preserve the following mindset:

* ship aggressively
* architect conservatively
* delay hard structure
* keep state simple
* keep environment boundaries clean
* keep outputs inspectable
* let the agent improvise locally
* reserve determinism for places where mistakes are expensive

## Examples

### Example prompts that should trigger this skill

* "Help me architect an AI-native MVP for this product idea."
* "Build a v0 for this app, but avoid overengineering."
* "What stack and storage approach should I use for an early prototype?"
* "Turn this idea into the thinnest end-to-end implementation."
* "I want to move fast across multiple AI-native apps. What defaults should we use?"
* "Help me decide whether this MVP really needs a relational database."
* "Refactor this prototype to stay simple but more extensible."
* "Design the first version with good upgrade paths but minimal complexity."

### Example behaviors

* recommend files plus JSON instead of a premature DB
* isolate environment differences behind a simple adapter
* build one working user flow before adding system depth
* avoid heavy abstractions until repetition appears
* explicitly label some concerns as post-MVP

## Final instruction

When in doubt, choose the path that maximizes learning speed, preserves future options, and minimizes irreversible complexity.
