# Evidence before acknowledgement

## Why

For several sources `switchboard.message_inbox`, a routing buffer, held the only verbatim copy of
what arrived, and a partition-drop function could destroy it. Owner decision
run24-inbox-not-archive (2026-11-22) makes the inbox a routing buffer, never evidence.

## What Changes

- Every connector commits an evidence row before it acknowledges, checkpoints or submits.
- `connectors.evidence_journal` serves sources with no evidence table (core_361).
- The Switchboard spec no longer promises a one-month inbox horizon.

## Capabilities

### New Capabilities

### Modified Capabilities

- `connector-base-spec`: Evidence Before Acknowledgement.
- `butler-switchboard`: Misroute Correction Re-dispatch names no retention horizon.

## Impact

Companion RFC 0014 amendment; connector capture manifests under docs/connectors.
