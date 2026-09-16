# Change: Specify Messenger-owned voice egress

## Status

Candidate contract only. The six policy decisions recorded on `bu-7exe4.13`
authorize this specification work, not adoption, implementation, provider or
presence access, endpoint binding, credentials, audio output, deployment, or a
live canary. Independent security/spec review and owner approval of the exact
artifact remain separate gates.

## Why

Butlers can ingest microphone speech through Live Listener, but no accepted
contract permits speech to leave the system through a room speaker. Treating
voice as another best-effort notification channel would create a physical side
effect with unsafe retry, target, presence, privacy, and quiet-hours failure
modes. The contract must put that side effect behind deterministic authority
boundaries before any implementation or provider experiment begins.

## What changes

- Add the `messenger-voice-egress` capability with stable requirement IDs for:
  Messenger ownership, authenticated Switchboard lineage, an opaque versioned
  endpoint registry, evidence-gated local-first providers, reply/explicit-send
  initiation, fresh room-specific owner-positive presence, unconditional DND
  and quiet-hours suppression, physical-side-effect state and idempotency,
  ambiguous no-retry handling, one linked text-only non-voice fallback, content-blind
  retention/telemetry, compatibility, and rollback.
- Amend `core-notify` so `voice` is accepted only when explicitly selected,
  never inferred as a preferred/default channel, and never enters generic
  quiet-hours deferral or coalescing. The Messenger voice gate remains the
  authoritative DND/quiet-hours decision immediately before provider handoff.
- Add draft RFC 0034 as the design contract and update topology maps for the
  Switchboard, Messenger, Home, and provider boundaries.
- Amend Messenger's infrastructure contract to make voice ownership and its
  refusal boundaries explicit. Live Listener remains ingress-only.

## Preserved boundaries

- No device or room identifiers are stored in `public.entity_info` or exposed
  to a model-visible envelope.
- Messenger and Home do not call each other directly or read each other's
  schema. Their deterministic request/attestation exchange is MCP-only through
  Switchboard.
- Home owns presence facts and any Home Assistant actuation. Messenger owns
  endpoint binding, delivery state, provider handoff truth, and fallback
  intent. Switchboard owns authenticated lineage and fallback resolution.
- No generic Messenger delivery tracker is restored. Voice uses a narrow,
  channel-specific physical-side-effect receipt and replay fence.
- No cloud TTS, proactive voice, VAD-as-presence, deferred voice, coalesced
  voice, quiet-hours bypass, recursive fallback, or automatic retry after a
  possible provider start is authorized.

## Reconciliation note

The earlier `Channel Validation` delta was archived by
`2026-09-12-make-routed-approvals-replayable`; its routed WhatsApp clauses are
now part of the canonical baseline. This change contains one fresh MODIFIED
block copied from that current baseline and adds `voice` without deleting any
existing clause or scenario. Before archival, authors must repeat the
same-requirement scan and rebuild this block if another change has touched the
baseline.

## Impact

- **Specs:** new `messenger-voice-egress`; modified `core-notify` channel,
  preferred-channel, and quiet-hours contracts.
- **RFC:** new draft RFC 0034.
- **Topology:** Messenger ownership and the authenticated voice-egress flow.
- **Roster identity:** Messenger infrastructure contract.
- **Implementation:** none in this change.
