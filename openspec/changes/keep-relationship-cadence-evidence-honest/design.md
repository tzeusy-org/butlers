## Context

`PulseStrip` currently filters `useEntityTimeline()` in memory. That endpoint returns at most the
first 50 rows across notes, gifts, loans, life events, Dunbar overrides, and interactions, with no
completeness marker. The strip nevertheless presents the derived interaction count as evidence for
an exact 30-day window and renders a zero as "Quiet".

## Goals / Non-Goals

**Goals:**

- Bind the displayed window label to the exact window requested from and echoed by the server.
- Make bounded-read completeness explicit.
- Allow "Quiet" only for a successful, complete zero.
- Preserve a small, content-blind projection that reads interaction identifiers only.

**Non-Goals:**

- Changing cadence policy, Dunbar tiers, priority, ranking, outreach copy, or social inference.
- Reading a provider, credential, or new relationship source.
- Redesigning the entity detail page or changing the existing unified timeline contract.

## Decisions

### D1 - Use a dedicated bounded cadence projection

`GET /api/relationship/entities/{entity_id}/cadence` accepts `window_days` and a bounded `limit`.
It queries active stable relationship-scoped interaction-event facts with a literal
`interaction_` predicate prefix, excluding the episodic `interaction_note` annotation,
inside one server-captured half-open
window and fetches `limit + 1` identifiers. The response echoes the window, returns the observed
count capped at `limit`, and marks `completeness='incomplete'` plus `has_more=true` when the extra
row exists.

This leaves the general-purpose timeline response backward-compatible and avoids treating a mixed
timeline page as complete cadence evidence. Identifiers and interaction content are not returned.

### D2 - Make calm conditional on matching complete evidence

The PulseStrip query key includes both entity and window. The tile label uses the requested window,
and its value is accepted only when the response echoes bounds spanning that same duration and
its end is no more than 90 seconds old (allowing at most 30 seconds of future clock skew). The
mounted query polls every 30 seconds and refetches on focus; a separate display clock ages out
cached evidence even if a refresh stalls. A fresh complete count of zero renders "Quiet"; a
positive complete count renders the existing interaction-count copy. A capped or mismatched
response renders "Incomplete", an old response renders "Stale", and a failed query or refetch
renders "Unavailable" even if a prior complete zero remains cached. Loading remains a placeholder
and never renders a calm claim.

### D3 - Preserve existing authority and behavior

The endpoint is read-only, uses the existing Relationship database and fact vocabulary, and does
not infer missing interactions. It does not change the existing timeline, cadence policy, Dunbar
ranking, overdue evaluation, or any provider/runtime state.

## Failure and rollback matrix

| Condition | Result |
|---|---|
| Complete window, zero interactions | Label names the echoed window; value is "Quiet". |
| Complete window, positive count | Label names the echoed window; value is the existing count copy. |
| Read reaches its cap | Response is incomplete; UI renders "Incomplete", never "Quiet" or an exact count. |
| Response window differs from the active request | UI renders "Incomplete" until matching evidence arrives. |
| Echoed end is older than 90 seconds or implausibly future-dated | UI renders "Stale" until a fresh matching result arrives. |
| Open page crosses the freshness bound | The display clock removes "Quiet" even if the cached response has not been replaced. |
| API/query failure | UI renders "Unavailable", never "Quiet". |
| Window changes | Query key changes; the label and eventual count are recomputed for that window. |
| Concurrent reads | Each response is self-contained by its echoed bounds; no shared state is mutated. |
| Revert | Remove the additive endpoint/client hook and restore the prior tile derivation; no data rollback exists. |

## Verification

- Relationship API tests prove exact window echo and complete versus capped response semantics.
- PulseStrip tests prove complete-zero calm, positive counts, incomplete/error attention, and
  refreshed-window label/count coherence.
- Strict OpenSpec validation and the overwrite guard prove the full modified requirement preserves
  its baseline scenarios.
