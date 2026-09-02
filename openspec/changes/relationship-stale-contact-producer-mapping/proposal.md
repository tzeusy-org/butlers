## Why

Relationship currently turns elapsed time since `MAX(interaction_*.valid_at)` into an
owner-facing stale-contact claim without proving which producer was expected to record the next
interaction. A dead connector, a transport that never writes interaction facts, or a mixed legacy
history can therefore look like owner neglect; this mapping is the prerequisite that lets the
continued expected-signals adoption under `bu-8cdl1.3` fail closed in a new PR instead.

## What Changes

- Define one authoritative, server-attested producer mapping for Relationship interaction inputs:
  Gmail email, Telegram user-client, WhatsApp user-client, and explicitly owner-entered manual
  observations.
- Classify Telegram bot, Discord, calendar-derived, legacy, missing, mixed, and otherwise
  unprovable sources as unmeasurable for stale-contact purposes.
- Require every stale-contact consumer to suppress owner-facing candidates, reconnect nudges,
  overdue counts, and false all-clear copy while the contact's signal is unmeasurable.
- Preserve the existing Dunbar/`stay_in_touch_days` cadence and stale-contact priority policy once
  a single producer is measurable and the cadence has elapsed.
- Bind every mapped connector attestation and liveness decision to the exact server-derived
  endpoint identity; a healthy sibling endpoint cannot substitute.
- Record the exact downstream handoff to continued `bu-8cdl1.3` adoption: after this prerequisite
  lands, a new branch/PR from `main` extends RFC 0029 with this mapping and performs the
  runtime/API/UI adoption.

Explicit non-goals:

- No guessed aggregate connector or "any healthy connector" fallback.
- No new outreach policy, cadence, priority, ranking, or notification behavior.
- No cross-schema read shortcut; Relationship continues to use the shared expected-signals
  contract supplied by the downstream change.
- No expected-signals table, RFC 0029 copy, runtime integration, API change, or UI implementation
  in this prerequisite.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `butler-relationship`: Bind every stale-contact consumer to a single provable producer or an
  unmeasurable fail-closed result.
- `dashboard-domain-pages`: Keep Relationship overdue surfaces honest when source measurability is
  unavailable.

## Impact

The change constrains the Relationship interaction writer/provenance contract, the deterministic
`insight-scan` candidate path, `contacts_overdue()` and its weekly reconnect consumer, and the two
dashboard overdue surfaces (Relationship Contacts tab and Plex attention rail). It updates
operator guidance and adds a planning-contract test that executes the full producer/liveness
matrix against the merged expected-signals contract. The named downstream implementation consumer
is a new Relationship adoption PR under `bu-8cdl1.3`; merged PR #3965 is not mutated.
