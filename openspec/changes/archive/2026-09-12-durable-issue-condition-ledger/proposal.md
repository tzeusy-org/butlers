## Why

Two operator promises on the Issues surface fail at the same boundary: what a
condition *is*, and what a piece of evidence is *scoped to*.

Acknowledging a continuously-unreachable butler cannot stick. `GET /api/issues`
learns reachability from a live MCP ping and stamps the resulting issue with the
request's own clock, so acknowledge-until-recurrence compares `now <= ack_time`
on the very next poll and the ack lapses immediately. The probe's clock was
being used as the condition's identity.

The Audit Log's "View in Issues" door is an approximation twice over. It rebuilt
the backend's grouping key client-side from the error's first line, then
substring-matched it against a feed already bounded by the Issues page's own
default seven-day window. A miss on either axis rendered as an empty Issues
page, which reads as an all-clear the lookup never established.

## What Changes

- Persist butler reachability as a durable outage **episode** in
  `public.butler_reachability_conditions`: one uninterrupted outage is one row
  with a stable onset, recovery closes it, and a later down transition opens a
  genuinely new one. `GET /api/issues` is the sole writer.
- Separate the epochs an issue carries. `recurrence_at` is what an
  acknowledgement is held against; `last_seen_at` stays the honest observation
  clock. For audit groups the two coincide, so that lane is unchanged.
- Derive a reachability acknowledgement's watermark server-side from the open
  episode, and fail the acknowledgement closed (503) rather than record one that
  is guaranteed to lapse.
- Add `GET /api/issues/group-for-audit/{audit_id}`: the exact, server-computed
  Issues-group identity for one audit row, resolved through the same grouping
  CTE the feed uses, with an explicitly-stated absence and an auto-widened
  window that actually contains the row.
- Make the Issues page's empty and all-clear copy name the scope it searched,
  and pin the feed to one server-resolved `issue_key` via `?group=`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-api`: adds the condition ledger, the recurrence-epoch distinction,
  the exact audit evidence door, and scope-naming empty copy to the Issues
  aggregation requirement.

## Impact

Adds one `public` table via core migration `core_200` and one read endpoint.
`GET /api/issues` gains a write side effect, which is documented at the endpoint
and surfaced through the existing `meta.sources_degraded` envelope when it
fails. No background poller is introduced; the ledger advances only when the
feed is requested. The existing degraded-source and truncation gating is
preserved unchanged, including its suppression of the all-clear.
