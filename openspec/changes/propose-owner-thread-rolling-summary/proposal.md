## Why

The current `src/butlers/api/conversation_envelope.py` limits historical content to 500 characters per message and silently takes the last 4000 characters of the serialized preamble. Long dashboard threads lose context without an honest omission signal. This candidate proposes a real rolling summary of earlier eligible history plus a bounded recent raw tail, with explicit failure/truncation disclosure.

**Status: DRAFT CANDIDATE, NOT ADOPTED.** No implementation, migration, prompt, configuration, provider call, source activation, owner decision, sync or archive occurs. Independent semantic/security review of the exact head precedes any owner adoption request. Original AC2 (exact adoption) and AC3 (implementation filed from the adopted artifact) remain unearned.

## What Changes

- Amend the complete `Continue Conversation` and `Dashboard Ingestion Envelope Construction` requirements while preserving all 10 existing scenario bodies verbatim.
- Specify same-owner/same-final-thread, admitted persisted dashboard-visible inputs; hidden prompts, raw tools, page_context, secrets and foreign/cross-channel inputs are excluded.
- Bind exact through-message membership, source revisions, generator/model/exclusion versions and created_at; treat summary as untrusted derivative and raw history as truth.
- Specify stale CAS rejection and idempotence by `(thread, through_message_id, summary_version)`, with invalidation/restart and replay compatibility.
- Preserve a real summary success path and a marked bounded-raw-tail failure path. Proposed finite budgets are 4000 context/1600 summary/2000 raw-tail/400 framing code points; generation is bounded to 1000 messages/64000 content code points/60 seconds and off the user-turn hot path.
- Place protected internal state on the final owner-thread identity from bu-0ynlk.15, with no interim column and no client/tool exposure. One current full payload; raw-lifetime content-blind receipts, replacement/source-deletion invalidation and final-thread disposal.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-conversations`: proposed rolling context, private derivative custody and same-owner/thread publication semantics.

## Impact

Future implementation would touch the actual API envelope builder, current history selection and router, a private thread-owned store on final .15 placement, the existing configured ephemeral generation lane and focused database/API/context tests. It does not alter ingest.v1 wire identity, pinned targets, page_context forwarding, durable Stop/retry, provenance/SSE or current API fields.

The current router fetches `message_list(limit=20, offset=0)`; that helper sorts ascending, so this is an early window rather than proof of a genuine newest tail. Future adopted implementation must select the exact snapshot prefix/recent tail at the real data seam. This bead changes no product query.

PR4396 owns composer UI; PR4495 owns actions; .15 and cross-channel owners retain schema/handoff scope. Its proposed client `summary` field does not grant exposure authority here. Placement, trusted owner/binding/order, visibility/secret admission and private-store grants remain explicit integration gates before implementation. See `integration.md`, `funnel.md`, `verification.md` and `baseline.json`.

## Adoption Options and Default

Recommended exact candidate: private final-thread state, raw-prefix generation without recursive derivative input, one active full payload, source invalidation/deletion, and the stated finite marked fallback. The owner must adopt this full artifact including retention, secret/visibility admission, prompt-injection framing and numeric budgets after independent review. Status quo default: current raw-history behavior continues, this candidate grants no summary execution; the original silent-truncation outcome remains unmet. An interim store, client exposure, recursive summary input or different retention/budget is a separately rebuilt/reviewed exact proposal, not a toggle assumed approved here.

## Out of Scope

Product code, migrations/SQL/grants, prompts, new API/UI/MCP fields, paid/runtime generation, live data/credentials, Telegram summaries, cross-thread memory, client summary UI, owner messages, decisions, Beads mutations, deployment, merge, queue, activation, archive or baseline sync. Tests: +0 ~0 -0.
