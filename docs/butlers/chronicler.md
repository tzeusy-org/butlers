# Chronicler

> **Purpose:** Butler-specific Chronicler contracts that have no other home.
> **Audience:** Developers changing Chronicler adapters or editorial endpoints.
> **Prerequisites:** [Butlers](index.md), [Chronicler manifesto](../../roster/chronicler/MANIFESTO.md).

Moved verbatim from `AGENTS.md` (Butler-specific rules without a docs page). Identity and scope live in the manifesto.

- Chronicler adapters whose evidence is mirrored across schemas derive `source_ref` from the
  upstream identifier (`calendar:{origin_instance_ref}`), so the `(source_name, source_ref)` upsert
  dedupes the fan-out. `health.steps` and `health.heart_rate` are point-event sources with no lane
  mapping. `SpotifySessionAdapter` falls back to `track_names` when both context fields are NULL.
- Chronicler editorial endpoints (`/api/chronicler/briefing|attention|kpi`) already accept any
  `?date=&tz=` deterministically via `editorial.compose_briefing_payload`; date navigation is a
  frontend concern.

### Owner-day composition

The bounded day-close bundle separates `owner_day.observations` from source
inventories and diagnostic counts. Its deterministic narration seeds use only
normal-privacy evidence in the exact owner-local day, retain readable timestamps,
confidence and machine source references, and select representative kinds before
the single interpretation call. Automated Butler sessions are processing records,
not owner conversations. Inbound email does not establish a human exchange;
actual owner outbound evidence supports sending a message, without inventing a
recipient or reply. Playback overlapping recorded sleep is background or
uncertain playback; playback alone never proves attentive listening. Unknown
places, movement destinations, travel modes and gaps remain explicit.

Raw episodes/events, per-source roll-ups and counts remain diagnostics. Their
producer classifications, time aggregates and editorial corrections are unchanged.
Derived observations share the existing character/byte budget and retain a raw
evidence anchor while present, so they cannot authorize an empty-day coverage
witness. Privacy masking precedes all derivation; sensitive sleep cannot qualify
normal playback by leaking its timing through this view. The existing masked
envelopes retain their separate limited narration rules.

The automatic session-episode caller excludes recognized delivery/tool-summary
bookkeeping only for the configured Chronicler's exact scheduled
`chronicler_day_close` trigger. It uses the cache classifier's existing scaffold
family without applying the cache's broader JSON/prose rules to memory. Session
results and audit records remain intact; routed corrections, other tasks,
other Butlers and genuine retrospective prose retain their existing memory path.
This hook is distinct from model-invoked memory write tools and deterministic
daily fact writeback; those keep their own admission and ownership rules.

### Late material and missingness

Day-close writeback distinguishes a supported numeric zero from an absent or
zero-filled rollup. A target lane needs an actual sample in the target local
day/timezone; its baseline contains only supported samples from the requested
window. Unavailable reads skip synthesis. Existing feeder-dark flags suppress
claims only for affected lanes on each sample's actual local date; target-day
availability cannot certify historical baseline days. Missing sleep cannot produce debt from baseline-only
samples. Cumulative debt also requires the complete supported requested calendar
window, so missing dates are never compressed into a shorter claimed interval.
The generic balance API retains its zero-filled response shape.

### Write-back integrity

- **Configured-absent sources are not outages.** An `optional_schema` source whose adapter skipped
  because its evidence surface is absent, and which has never projected successfully, does not
  raise `feeder_dark` (`flags.is_source_configured_absent`). It still gates the behavioral flags
  that depend on it, so a missing sleep feeder never produces `sleep_missing`. A source that fed
  the chronicler before and then went inactive, or any inactive required source, is still dark.
- **Sleep debt is only "building" on a short night.** The closed day must itself fall short of the
  usual, so a long night never yields both "slept more than usual" and "debt building".
- **Confidence is evidence-derived.** `writeback.evidence_confidence` scales linearly from 0.4 to
  0.9 with baseline coverage (supported sample days over the lookback window). Self-reminders carry
  the 0.4 floor. The value is written to the facts `confidence` column through `store_fact`, not
  the blanket 1.0 default.
- **The day-close session writes no memory.** Its prompt forbids memory write tools; delivery and
  scheduling receipts (sent, deferred, not configured) are operational status, never facts.

The daemon owns one canonical historical-refresh service shared with the
existing dashboard control. The dashboard still refuses runtime-origin calls.
Only the daemon passes its private continuation object to scheduler dispatch;
job arguments cannot select a caller or collaborator. Projection and rollup jobs
remain deterministic. Their successful registered return precedes the scheduler
tail, which may resume one eligible silent refresh. An ordinary day-close in that
tick suppresses an additional refresh. No cron, narrator, notification surface or
memory writer is added; the existing hour-one recap and opted-in interview retain
their separate behavior.

Own-schema `state` records opaque narration-visible revisions, pending outcomes
and a fair closed-day cursor. Discovery reads durable local tuples, so restart or
a missed in-memory hook does not lose an update or deletion. Volatile rollup
computation timestamps are not revisions. Only the accepted privacy projection
enters a digest; raw material is never stored in continuation state. A checkpoint
or empty rollup is not source-completeness or quiet-day proof. Only the existing
canonical writer's current admission, successful witness write and durable
readback can establish coverage. An older authoritative row alone cannot prove
this execution's persistence; idempotent successful writes need not advance its
timestamp. A newly discovered empty
projection without lawful visible material or an authoritative prior witness
remains pending; zero-fill alone never starts an automatic quiet refresh.
A changed revision is marked processed only after that outcome and a second
material read agree. Ambiguous, invalid, unavailable and cancelled outcomes stay
pending. Successful quiet refreshes share the existing 24-hour rate limit. The
existing enabled task's token budget and the 100-second absolute operation bound,
including lock wait, also apply to this caller.

Timeout and cancellation retain the whole operation and native asyncpg holder
release. Shutdown stops intake and uses its existing finite cleanup budget. An
incomplete release quarantines the physical pool and dependent modules; retained
cleanup closes them once settlement completes. It never reports success while a
release is incomplete or closes the shared pool underneath retained work.
Local software controls do not establish real PostgreSQL, model/provider or
owner-delivery outcomes; those require their source-bound hosted evidence.

### Travel intents and location (bu-2tsgqb.7)

`reconcile_day` checks one-way travel intents (a flight, or a route block `A -> B`, 30 min to 24 h)
against accepted OwnTracks fixes (`src/butlers/chronicler/travel_location.py`). Same place before
and after the window drops the intent with `evidence="location"`, but only for an intent that
expects a long trip (explicit flight wording in the title); a
route without flight wording, like `Home -> Office` or `SIN -> Aomori`, stays `unknown`. 300 km or more at a plausible
speed keeps it with a `consistent` note that states only the displacement, never that the trip was
taken; anything else is `unknown`. Each anchor needs two fixes; `trail_outlier`, coarse (>1 km) and
naive-timestamp fixes are ignored. There is no geocoding, so an event's stated place is never
checked. Every intent also carries `reconciliation.plan_producer`: `butler` for events the calendar
module marked `butler_generated` (or titled `BUTLER:`), else `calendar`.
