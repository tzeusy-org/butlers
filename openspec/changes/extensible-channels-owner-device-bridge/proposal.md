## Why

Butlers currently treats inbound source identity as a closed code literal, uses timestamp hour
offsets to keep relationship interactions from colliding, and recognizes `sms` as a notify input
even though no SMS delivery adapter exists. This blocks additive sources, omits shipped Discord
ingress from passive relationship scoring, and leaves the owner's calls and SMS outside the
system's perception without a settled authentication, privacy, or delivery-safety contract.

This draft is the specification prerequisite for `bu-8cdl1.14` under the released run-11 pursuit.
It is proposed behavior only: it is not owner approval and authorizes no implementation,
configuration, provider or credential operation, deployment, activation, runtime publication, or
message send.

## What Changes

- Define a migration-owned, read-only-at-runtime catalog for canonical inbound
  channel/provider pairs. Catalog admission authorizes only `ingest.v1` validation; it never
  authorizes outbound delivery.
- Replace closed-world semantic validation with bounded token syntax followed by authoritative
  catalog validation, including a last-known-good cache that may preserve known pairs during a
  transient read failure but can never admit a new pair.
- Define an additive representation → propagation → enforcement rollout that seeds every legacy
  pair before relaxing the static validator, plus a rollback that refuses catalog-only pairs
  without deleting or reinterpreting accepted history.
- Admit the already-shipped Discord bot-token source to passive interaction sync with
  source-aware idempotency and real interaction timestamps. This does not authorize, implement,
  or broaden Discord OAuth user-context v2.
- Define **Watched Source** as a reusable connector profile that inherits the complete connector
  base lifecycle instead of bypassing Switchboard classification, filtering, replay, checkpoint,
  heartbeat, or observability contracts.
- Reserve an owner-device calls/SMS bridge behind an exact provider, authentication, consent,
  privacy, retention, and ingress-topology owner decision. Telnyx is the only researched V1
  candidate and remains conditional; Twilio is retained only as a future alternative that needs a
  separately approved freshness/retry contract. No provider is selected by this draft.
- Preserve the current `sms` unsupported response. SMS delivery requires a later, separately
  approved OpenSpec change, an implemented adapter, credential/reachability checks, and the
  applicable approval-delivery policy. Unknown post-handoff outcomes may never be blindly retried.
- Propose a content-blind owner setup/status surface: credentials and message/call bodies never
  appear in catalog/status APIs, audit metadata, telemetry, or browser payloads. No activation
  action is available until all owner decisions and prerequisites are satisfied.

## Capabilities

### New Capabilities

- `source-channel-catalog`: Inbound catalog authority, semantic validation/cache behavior,
  read-only projection, additive rollout, and rollback.
- `connector-watched-source`: Reusable watched-source lifecycle and transport boundary.
- `owner-device-bridge`: Proposed calls/SMS ingress, authentication, privacy, provider, setup,
  revocation, and future delivery boundary.

### Modified Capabilities

- `connector-base-spec`: Replaces closed source enums and static Pydantic pair authority at the
  staged catalog cutover while preserving every other `ingest.v1` field and scenario.
- `passive-interaction-sync`: Source-aware interaction identity, Discord resolution, concurrent
  replay behavior, and removal of the channel-capacity dependency on timestamp offsets.
- `connector-discord`: Limits the new relationship input to the shipped bot-token event contract
  and preserves the unresolved OAuth v2 gate.
- `core-notify`: Makes unsupported SMS and its separate approval/adapter activation prerequisite
  explicit without adding SMS to the deliverable set.

## Impact

- **Design contract:** proposed RFC 0033 reconciles RFC 0003's static ingest pair list, RFC 0013
  D4's hour-offset scheme, the accepted connector boundary, and proposed RFC 0023's
  provider-ambiguity model.
- **Future database/API/UI:** an additive public catalog and least-privilege grants; content-blind
  catalog and owner-device status reads; an owner setup flow only after exact approval.
- **Future runtime:** Switchboard ingest validation, connector conformance, relationship sync,
  Discord sender resolution, and Messenger adapter registration.
- **Active overlap:** draft PR #4046 (`bu-poven`) changes connector presentation and grants no
  authority to this change. Foreign draft PR #3960 (`bu-7exe4.2`) changes RFC 0003 and the ingest
  envelope and is currently conflicting; this draft must be rebuilt against any version that
  lands before owner review or merge. Proposed RFC 0023 and its active change remain separate
  authority for durable approval presentation and provider-handoff recovery.
- **External providers:** no new dependency is selected. Telnyx facts are sourced from official
  documentation but account eligibility, number availability, regulatory requirements, and the
  actual ingress route remain owner/provider prerequisites. Twilio research is retained for a
  future change and grants no V1 eligibility.
- **Tooling follow-up:** `aib-7an` owns the separate `spec-trace-check.py` delta-Purpose defect.
  OpenSpec 1.9 accepts an authored Purpose on each new-capability delta and carries it into the new
  main spec on archive; the current checker falsely reports those three headings as errors. This
  change preserves the valid prose and does not modify the shared checker.
- **Tests:** +0 ~0 -0 in this specification-only change. Future behavior verification is named in
  `tasks.md`; no live canary or exact test-count obligation is introduced.
