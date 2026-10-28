# Re-scope signed confirm interactions onto the goal runtime

## Why

The live gap-interview buttons POST without authentication and receive HTTP 401. Their
replacement bead bu-jjy9p depends on bu-ow5a4, whose signed confirm transport is unbuilt.
The prior confirm draft targets retired ingest/pinned_target routing and provides no
registered deterministic sink for Chronicler. Collision row 56 requires a goal-runtime
re-scope before implementing this shared transport.

## What Changes

Preserve bounded choices, immutable durable prompts, recipient authentication, domain-separated
signatures, first-answer/replay fencing, recoverable expiry, independent concurrent prompts,
noninteractive fallback and separation from approval authority. Replace conversational reentry
with trusted goal admission using exact server-held prompt references. Define closed registered
domain sinks for deterministic service consumers, durable application receipts and retry after
ambiguous RPC outcomes. Keep non-owner replies third_party/exposed and delivery subject to
normal approval/rule checks. Add the inherited simple-card fact/claim/quote rendering contract.

The current dispatch packet specifies 2-4 options; this replaces the stale draft's 2-8.
The proposed signature covers recipient chat rather than stored requested_at, allowing
verification before database lookup. Sender identity is also checked against stored bindings.
These changes require exact-artifact review and owner adoption before implementation.

## Capabilities

### New Capabilities

- `notify-confirm-interaction`: signed durable confirm answers, verified goal admission,
  deterministic registered sinks and shared request-card rendering.

### Modified Capabilities

- `core-notify`: additive target-state confirm envelope.
- `module-telegram`: immutable option keyboard and terminal rendering.
- `connector-telegram-bot`: authenticated typed confirm forwarding.

## Impact

Phase A authoring under bu-ow5a4 only. `design.md` maps current source, new interfaces,
producer ownership, behavior and Phase B steps. No implementation, migration, runtime
activation, Telegram interaction or deployment. Runtime emission remains dark until the
canonical RFC 0038 switch. bu-jjy9p consumes the transport after its own existing gates.
No completion or archival is claimed. Tests: +0 ~0 -0 (specification only).
