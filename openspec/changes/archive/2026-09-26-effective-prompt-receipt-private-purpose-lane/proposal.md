## Why

The dashboard currently shows only the mutable database prompt row while the runtime composes a
larger effective system prompt from roster and dynamic layers. Session evidence therefore cannot
answer which instruction bytes actually reached a runtime, and the butler console can describe an
empty database row as if no prompt exists. Connector and routed sessions also lack a content-blind
purpose label that can explain where private-channel work originated without storing message,
sender, recipient, or thread content.

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
- Keep `purpose_lane` as content-blind provenance only. It does not alter catalog eligibility,
  provider/runtime choice, priority, tier fallthrough, quota, breaker, or same-tier failover.

## Non-Goals

- No prompt-authoring UI or change to the existing prompt write semantics.
- No roster or manifesto rewrite and no adoption of the active roster-overlay proposal.
- No general model-ranking, tier, quota, breaker, failover, or spend-governance redesign.
- No source-specific provider/locality restriction and no private-purpose override authority.
- No prompt, message, recipient, sender, or private-content bytes in audit, metric, dispatch-attempt,
  or list metadata.

## Capabilities

### Modified Capabilities

- `core-sessions`: persist the effective-prompt and purpose-lane receipt on new sessions.
- `dashboard-visibility`: expose a protected session prompt detail door and purpose-lane badge.
- `dashboard-butler-management`: show the composed prompt and honest roster-drift state.
- `model-catalog`: persist purpose-lane evidence while preserving canonical model resolution.

## Impact

This affects additive core migrations, prompt composition/session creation, session and butler
management APIs, connector discretion routing, session/spend UI projections, runtime/model-routing
documentation, OpenSpec deltas, and focused unit/API/real-PostgreSQL/frontend tests. It does not
authorize live data reads, provider calls, deployment, or runtime activation.
