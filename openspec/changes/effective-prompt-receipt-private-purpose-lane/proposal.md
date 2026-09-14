## Why

The dashboard currently shows only the mutable database prompt row while the runtime composes a
larger effective system prompt from roster and dynamic layers. Session evidence therefore cannot
answer which instruction bytes actually reached a runtime, and the butler console can describe an
empty database row as if no prompt exists. Separately, connector discretion calls carrying private
WhatsApp or Telegram content share a model tier with unrelated work, so catalog priority can select
a non-local model without a purpose-specific refusal.

## What Changes

- Persist an additive, immutable receipt on every new session: the exact effective system prompt,
  its SHA-256 digest, total UTF-8 bytes, and an ordered provenance list of named composition sources.
- Add an owner-dashboard session prompt detail door that returns the receipt on demand without
  adding prompt content to list, spend, audit, metric, or telemetry surfaces.
- Project the composed prompt in the existing butler Configuration surface and compare its roster
  provenance with the latest executed receipt so roster drift is explicit rather than presented as
  current truth.
- Add a closed `purpose_lane` dispatch label. WhatsApp and Telegram discretion calls and routed
  sessions are `private_content`; other work is `standard`.
- Require `private_content` work to use an OpenCode `ollama/` catalog model through the loopback or
  RFC 0008 owner-local Ollama origin captured for that dispatch. A current, explicitly targeted,
  successfully audited operator routing rule is the only bounded remote-model exception. Missing
  locality proof, local capacity, or audit evidence refuses before adapter invocation and records
  only safe refusal evidence.

## Non-Goals

- No prompt-authoring UI or change to the existing prompt write semantics.
- No roster or manifesto rewrite and no adoption of the active roster-overlay proposal.
- No general model-ranking, tier, quota, breaker, failover, or spend-governance redesign.
- No prompt, message, recipient, sender, or private-content bytes in audit, metric, dispatch-attempt,
  or list metadata.

## Capabilities

### Modified Capabilities

- `core-sessions`: persist the effective-prompt and purpose-lane receipt on new sessions.
- `dashboard-visibility`: expose a protected session prompt detail door and purpose-lane badge.
- `dashboard-butler-management`: show the composed prompt and honest roster-drift state.
- `model-catalog`: enforce the local-only private-content lane with a narrow audited exception.

## Impact

This affects additive core migrations, prompt composition/session creation, session and butler
management APIs, connector discretion routing, session/spend UI projections, runtime/model-routing
documentation, OpenSpec deltas, and focused unit/API/real-PostgreSQL/frontend tests. It does not
authorize live data reads, provider calls, deployment, or runtime activation.
