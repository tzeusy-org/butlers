## Why

Relationship's email-enrichment heuristic sees inbound recurrence but cannot tell
whether Messenger ever sent anything to the same person. The email module
reports SMTP acceptance as `sent`, and the only records of outbound mail (audit,
notification, inbox rows) carry content, so none of them can serve as
correspondence evidence.

## What Changes

- Add a Messenger-private, metadata-only email correspondence ledger with four
  states: `accepted`, `confirmed`, `failed`, `unknown`.
- Only an explicit provider-Sent confirmation (Gmail API native send plus an
  exact-ID metadata check, performed by Messenger itself) makes a row
  `confirmed`. SMTP acceptance stays `accepted` and expires to `unknown`.
- Expose one read-only, per-address aggregate of confirmed outbound mail to a
  deterministic daily Relationship job under the RFC 0010 exception pattern.
  Relationship combines it with its existing inbound signal; the result is
  positive or unknown, never negative.
- Hard-delete ledger rows 180 days after their intent time.
- Deferred (see design.md): authenticated ingress epochs, Switchboard broker and
  connector principals, negative evidence, alias authority, protected scheduler
  jobs, and cleanup of existing content-bearing mirrors.

## Capabilities

### New Capabilities

- `email-correspondence-ledger`: private outbound evidence, state machine,
  provider-Sent confirmation, retention, and the confirmed-outbound aggregate.

### Modified Capabilities

- `module-email`: sends return a typed, content-free outcome; SMTP acceptance is
  not confirmation.
- `butler-messenger`: owns the ledger, native confirmation, and a deterministic
  retention/expiry job.
- `butler-relationship`: consumes only the bounded aggregate in a deterministic
  job.
- `database-security`: one read-only aggregate view granted to Relationship.

## Impact

Messenger migration chain and `butler.toml`, `src/butlers/modules/email.py`,
Relationship jobs, `src/butlers/scheduled_jobs.py`, PostgreSQL grants, RFC 0024,
and Messenger/Relationship docs. No provider operation, backfill, or live-data
change is authorized by this proposal.
