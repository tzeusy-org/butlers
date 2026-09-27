# Exclude sessionless spend sources from roster divergence

## Why

The Spend ledger intentionally records connector discretion and synthetic dashboard runtime calls
under non-roster attribution identities. Those rows have no task session by design, but the
ledger-versus-session detector currently treats every ledger identity absent from the roster as
missing evidence. Expected sources such as WhatsApp `wa:*@lid` therefore keep the comparison in a
permanent degraded state and hide genuine roster divergence.

## What Changes

- Carry whether each grouped ledger row has a task session into the diagnostic detector.
- Exclude explicitly sessionless ledger sources from roster-session coverage checks.
- Preserve fail-closed `source_error` behavior for real or ambiguous unknown roster identities.
- Keep the public degraded explanation content-blind and leave ledger pricing unchanged.

## Impact

- Affected capability: `dashboard-spend-dashboard`
- Affected code: Spend ledger query and diagnostic divergence classifier
- Affected UI: verification only; the existing generic degraded explanation remains unchanged
- No schema, pricing, runtime, provider, connector, or WhatsApp behavior change
