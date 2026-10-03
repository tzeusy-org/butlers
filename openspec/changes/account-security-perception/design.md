## Context

Bearer material (OTP codes, reset links) is scrubbed at ingest. The sensor works from metadata only and never reads, stores or logs a body, code or link. `auth_artifact_observed` (bu-q7vx1q.20) stays a separate deferred slice; the packet's `auth_artifact_requested` kind is therefore not produced.

## Decisions

- **Trust**: exact first-party sender addresses only (no domains, no subdomains). `Authentication-Results`, when the connector kept it (Gmail full tier), rejects a reported DMARC/DKIM/SPF failure and upgrades a DMARC pass or an aligned DKIM pass to `authenticated`. Without the header the verdict is `unverified`. Only `authenticated` events may ever interrupt the owner; `unverified` is a quiet record, so a forged alert cannot page at night.
- **Placement**: the classifier runs in the ingest tool next to, not inside, policy evaluation, so no rule outcome changes the event. The connector-side evaluator carve-out keeps a global `skip` from dropping the mail before submission. Connector-scope `block` rules are owner-authored filters and are left alone.
- **Dedup**: ingest dedupe prevents a second ingest row; the publisher also checks (provider, kind, external_event_id) against `public.domain_events`.
- **Answer door**: `no` keys a fleet case on `account_security:<event_id>`; a repeat reuses it; `yes` never closes a case.

## Risks

- A crash between the ingest commit and the publish loses one event (best-effort publish, as for the fleet-event fan-out).
- A provider subject change is a false negative; unclassified allowlisted-sender mail is not counted yet.
