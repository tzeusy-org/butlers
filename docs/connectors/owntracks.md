# OwnTracks

OwnTracks supplies the durable phone-location evidence used by Chronicler's
movement and place inference. The connector can be online while that evidence
is too sparse to infer movement, so phone reporting cadence is an operator-owned
part of setup.

## Configure the phone

OwnTracks labels and menu locations vary slightly by platform and app version.
Use the equivalent setting on your device when the wording differs.

1. Keep the existing HTTP connection pointed at the Butlers OwnTracks webhook.
2. Enable extended location data for the connectivity metadata your platform
   supports. On iOS, confirm that a location payload contains the optional
   uppercase `SSID` field while connected to Wi-Fi. OwnTracks does not currently
   document `SSID` as an Android location field, and VPN or OS privacy
   restrictions can prevent it from appearing even on supported devices.
3. Set a stable Tracker ID (`tid`) of one or two ASCII letters or digits. When
   no `OWNTRACKS_TRACKER_ID` override is configured, Butlers uses it as the
   per-device connector identity; malformed or longer values are ignored rather
   than creating a device record.
4. Create regions/waypoints named `Home` and `Office` at the corresponding
   locations. Use distinct, stable names: the phone sends them verbatim in the
   location payload's `inregions` list and in enter/leave transitions.
   - iOS: long-press the map at the desired location, then create or edit the
     region and radius.
   - Android: open the app's Regions/Waypoints screen and use the place picker.
5. During waking hours, consider **Move** monitoring mode when movement history
   remains sparse. Move mode requests substantially more frequent/high-power
   location updates and therefore costs more battery; switch back to a lower
   power mode overnight if that tradeoff is preferable.

OwnTracks' official references describe [tracker IDs](https://owntracks.org/booklet/features/tid/),
[waypoints and regions](https://owntracks.org/booklet/features/waypoints/), [location monitoring
modes](https://owntracks.org/booklet/features/location/), and the optional [`SSID` and `inregions`
location fields](https://owntracks.org/booklet/tech/json/).

## Verify the evidence stream

After changing the phone configuration:

1. Trigger a manual location publish from OwnTracks.
2. Open **Ingestion → Connectors** and find the phone's OwnTracks identity.
3. Confirm the connector remains online and the sparse-cadence warning clears
   after at least 24 durable location points have arrived in a trailing 24-hour
   window.

The 24-point threshold is a minimum operational baseline, not a claim that 24
points are sufficient for every inference. A warning means the durable evidence
stream is definitely sparse; it does not mean the webhook transport is down.
If the cadence source itself is unavailable, the roster displays a degraded
source note instead of treating the missing diagnostic as an all-clear.

## Label Wi-Fi networks for presence

Chronicler only interprets networks the owner labels explicitly. Store the
mapping in the Chronicler state key `chronicler/owntracks/ssid_places` through
the existing state API:

```bash
curl --request PUT \
  --header 'Content-Type: application/json' \
  --data '{"value":{"Home WiFi":"home","Corp WiFi":"work"}}' \
  http://localhost:41200/api/butlers/chronicler/state/chronicler/owntracks/ssid_places
```

SSID keys are exact and case-sensitive. Place values are limited to `home` and
`work`; malformed mappings safely produce no SSID presence until corrected.
Missing and unlabelled SSIDs are explicit gaps rather than inferred places.

The scheduled projector reads this state on every run. Changing the mapping
causes retained OwnTracks evidence to be replayed idempotently, so an SSID can
be labelled after observations arrive. Removing or relabelling a mapping
tombstones stale derived episodes before replay, so old place claims do not
remain active. The mapping uses the existing JSONB state store and requires no
migration. Projected episodes contain only the canonical `payload.place`; raw
network names remain in the private source evidence and state value.

## How Chronicler cleans the trail

The point and place-cluster projections share one plausibility policy
(`src/butlers/chronicler/adapters/owntracks_trail.py`):

- A fix that would need more than 1000 km/h from the endpoint's last accepted
  fix (jumps under 1 km are never judged on speed) is an impossible jump. Its
  `location` point event is still projected, but carries `payload.trail_outlier`.
  It is excluded from movement, place and away episodes and from the dashboard
  Map trail. Three consecutive fixes that agree with each other re-anchor the
  trail, so one bad fix cannot hide a real one.
- Tied effective device timestamps use the source UUID as the same stable
  tie-break in the point, place and movement paths. This does not change the
  existing timestamp-only cursor or recover fixes omitted at a batch split.
- A first fix is provisional until a plausible companion confirms it. Three
  mutually consistent candidates can reject an isolated bad seed even when
  they arrive across batches. Only retained, explicitly identified projections
  are revised; mature or unidentified legacy anchors are not retracted.
  Candidate identities are bounded checkpoint state, not duplicate raw payloads.
- A `movement_episode` needs at least two accepted fixes. Its point count
  covers the whole episode across batches. A new movement that overlaps a
  stored one for the same endpoint widens it rather than adding a second chunk.
  Terminal coordinates come from the same fix as the widened end time;
  a later legacy end without a valid coordinate pair stays unknown.
- Each movement records `payload.path_m`: the cumulative great-circle distance
  over its ordered accepted fixes, counting each connecting edge once across
  batches (the carry keeps the path and the fix it ends on). It is `null`,
  never 0 or the straight-line endpoint distance, when it cannot be rebuilt
  exactly: an episode carried over from before path tracking, or new fixes
  widening a stored episode. An exact replay keeps the stored value. Raw expiry
  needs this metric to survive the fixes it summarises.
  Migration `chronicler_027` applied the same rule once to episodes stored
  before it: each overlapping group keeps its earliest episode, widened, and
  tombstones the rest. Groups holding a user correction are left alone.
- Place clusters are named, in order, from an `OWNTRACKS_PLACE_REFERENCES`
  entry, from an OwnTracks region (`inregions`) seen on at least half the
  cluster's fixes, or else stay explicitly `place_unknown`
  (`payload.label_source`). No geocoding service is called.
- With a `home` entry in `OWNTRACKS_PLACE_REFERENCES`, accepted fixes more
  than 50 km from home form an evidence-layer `away_episode` ("Away from
  home"), emitted once it spans at least 3 hours. It closes at the first fix
  back within 50 km, and a gap of more than 36 hours splits it. It frames the
  day and never counts as lived time.

## Privacy note

OwnTracks location payloads contain precise coordinates and may include the
Wi-Fi network name. For successful durable evidence writes, Butlers preserves
the original accepted location payload in
`connectors.owntracks_points.raw_payload` as durable Chronicler evidence,
including `SSID` and `inregions` when the phone sends them. Restrict database
access accordingly and use SSID/region names that reveal no more than intended.

The raw-fix retention horizon is declared in Chronicler at
`GET`/`PUT /api/chronicler/location-retention`. It defaults to 30 days, and the
owner may shorten it to as little as 1 day; the PUT is owner-only and versioned.
Raw expiry is **not yet enforced**: the response reports `status: not_enforced`
until deletion is gated on per-row projection coverage (bu-s11n0s.7). Until
then, raw fixes are still retained indefinitely. `OWNTRACKS_RETENTION_DAYS`
governs only the `public.ingestion_events` audit rows.

Groundwork for that gate is in place, and still deletes nothing:

- Each raw fix carries an immutable retention clock, `retention_at` (core_340): the device
  timestamp, or the server receipt time when the device timestamp is more than 4 hours after
  receipt. A trigger sets it on insert, whatever the writer sends, and refuses any later change
  to `ts`, `recorded_at` or `retention_at`.
- All three OwnTracks adapters record, in the same transaction as their outputs and checkpoint,
  which raw rows they have finished with (`chronicler.location_projection_coverage`,
  chronicler_032) and which are still held by an open carry
  (`chronicler.location_projection_open_members`; a row may sit in several carries, so each
  open member stores a set of carry keys, chronicler_033). A later expiry may delete a row only
  when it has coverage from every adapter and no adapter holds it open.
  - `owntracks.points` covers a row with its point event (and movement episode); it holds the
    open movement segment and pending trail-consensus rows.
  - `owntracks.place_cluster` holds the open stay, its held departure fix, the open away trip
    and its trail seed and pending rows. A closed stay or trip covers its members with the
    episode; a fix it decided to emit nothing for (a stay or trip below the minimum, a rejected
    fix) is recorded as `evaluated_no_output`.
  - `owntracks.ssid_presence` holds the open presence span or singleton; an unmapped or missing
    network, or a singleton that never grew, is `evaluated_no_output`.
  - Each record names exactly what it decided (chronicler_034): the raw row's content digest
    (`v1:` sha256 over its identity, timestamps, coordinates, accuracy, trigger, event and
    payload, computed in SQL) and the adapter's config revision (`v1:` sha256 over the settings
    that change its decisions; for SSID presence this includes the owner's mapping, for place
    clustering only the homes). A record is current only when both match; records from before
    chronicler_034 read `legacy` and are never current. A changed setting or rewritten row needs a
    new record; nothing is updated or deleted.
  - Each adapter saves its config revision in carryover beside its open carries. A run under a
    different revision (or with none saved) voids those carries and the outputs their members
    gained: a held row read again is decided afresh, and one not read again is released without
    coverage, left for discovery. So an away trip open when the owner moves home never closes
    into a current-revision record. An SSID mapping change also replays from the start.
  - Owner episode corrections do not invalidate coverage: they edit the output, not the
    decision about the raw row.
  - Each adapter run upserts one attempt row (`chronicler.location_projection_attempts`): the
    outcome, the exception class (never its message), consecutive failures, and the last
    success. An unavailable source is a failed attempt (`source_unavailable`).
  - A successful run also counts settled raw rows (arrived more than 1 hour and at most 7 days
    ago) that lack current coverage and are not held open, capped at 1000. The count is
    reported only: those rows are not re-projected. They have no current record, so raw expiry
    will never delete them; a window-scoped rebuild that drains them is a later follow-up.
  - No deletion uses coverage yet.
- Summaries are ready to outlive the fixes they summarise (bu-s11n0s.7 S3), still deleting
  nothing:
  - Movement episodes carry their cumulative `path_m` (see "How Chronicler cleans the trail").
  - `butlers.chronicler.spatial_precision` defines the reduced precision summaries will keep:
    scheme `eqcell-150m-v1`, the centre of an approximately 150 m cell (equal latitude bands of
    the 6 371 008.8 m mean-radius sphere, each cut into about 150 m-wide longitude cells, never
    fewer than one; the antimeridian is one seam). Coarsening is idempotent and keeps nothing
    of the exact point. It is the nominal cell size, not a sensor accuracy, and makes no
    anonymity claim. Nothing coarsens stored rows yet.
  - chronicler_035 adds `chronicler.location_forgetting_receipts` (one row per forgetting run:
    policy version, cutoff, spatial scheme, counts) and `chronicler.location_evidence_tombstones`
    (one row per forgotten point event: its former id, `source_ref`, receipt and the episodes it
    supported; no coordinates, title, network name or payload). Both are append-only, and the
    runtime role may only read and insert. `location_forgetting.record_forgetting` writes a
    run's tombstones and its receipt in one transaction, counting only tombstones it added; no
    code calls it yet.
  - The point and place-cluster projections never project a fix whose `source_ref` is
    tombstoned, so a replayed webhook, reset cursor or wider horizon cannot restore a forgotten
    point, stay or trip. It is inert until raw expiry writes tombstones.
  - A forgotten fix is dropped before trail classification: it feeds no point event, episode,
    carry, trail anchor or hold, and gets no projection-coverage record, so coverage never
    calls it projected. The batch continues. The run counts the dropped fixes in
    `rows_refused_forgotten`, never in `rows_projected` or `point_events`, and warns with the
    count only: no source, key, timestamp or coordinate (warnings persist with the job result).
    SSID presence applies the same floor after normalization, so a forgotten fix never joins a
    network-presence span again. Its `project()` takes the SSID projection lock itself when
    called outside `run()`, so it cannot interleave with a run or a grant preparation.
- Raw expiry is prepared; raw rows are still never deleted (bu-s11n0s.7 S4a, S4b-2):
  - chronicler_036 adds `chronicler.location_retention_grants` (one row per preparation run,
    its receipt: policy version, horizon, cutoff, every adapter's config revision, the count
    granted and whether the limit stopped it) and `chronicler.location_retention_grant_items`
    (one row per granted raw row: id, idempotency key, retention clock, content digest). Both
    are append-only; the runtime role may only read and insert. A raw row is granted at most
    once.
  - `location_retention_grant.prepare_retention_grant` grants a raw row only when every covered
    adapter has an exact current coverage record for it (current config revision, current
    content digest; a `legacy` or other-revision record does not count), no adapter holds it
    open, its `retention_at` is strictly older than `now() - days` and it arrived more than an
    hour ago. It holds the policy row and every OwnTracks projection lock for the run, oldest
    rows first, at most 500 per run. Under those locks the caller's config revisions must equal
    each adapter's last recorded run (`location_projection_attempts.config_revision`);
    otherwise it raises `StaleRevisions` and writes nothing. Nothing reads a grant yet: the
    connector-side delete and its receipt are a later slice.
  - Preparation is irreversible for Chronicler's own copies. In the grant transaction it
    deletes the granted rows' location point events, each leaving a tombstone and the run a
    forgetting receipt (`record_forgetting`), so the resurrection floor refuses them again. It
    snaps every coordinate in the place, movement and away summaries those rows fed (found
    through their coverage records and point-event links) to its 150 m cell
    (`spatial_precision.coarsen_location_payload`; a payload with a coordinate key it does not
    know fails the whole run), and coarsens a trail anchor in carryover that rests on a granted
    fix (an anchor without an id: once older than the cutoff). Day-close prose caches
    (`tier2_cache`) are not rewritten.
  - Nothing calls it: no job, schedule, MCP tool, route or script.
    `tests/chronicler/test_location_retention_dark_guard.py` fails on any reference to
    `prepare_retention_grant`, `append_grant_state` or the module outside the module itself.
    Enabling it is an owner act.
  - A grant row stays `prepared`. chronicler_037 adds `chronicler.location_retention_grant_states`,
    where each later state is a new row: `ready` (holder copies read back; deletion may start),
    `unknown` (a deletion's committed receipt could not be read; may repeat), `reconciled` and
    `failed`. `ready`, `reconciled` and `failed` occur at most once per grant; no `ready` row
    means the grant is withheld. Each row carries a count-only JSON receipt. The table is
    append-only and the runtime role may only read and insert; its downgrade refuses while any
    state is recorded.
  - `append_grant_state` is the only writer: under a per-grant advisory lock it appends a state
    only when it legally follows the history (`unknown`, `reconciled` or `failed` only after
    `ready`; `reconciled` and `failed` exclude each other and end the history). It refuses
    `ready` outright (`HoldersIncomplete`) until holder copies are recorded (slice S4c), so no
    grant can authorize a deletion yet.
- The connector side of a deletion has somewhere to leave its evidence, still deleting nothing
  (bu-s11n0s.7 S4b-3a, core_342):
  - `connectors.owntracks_retention_batches` is the connector's receipt, one row per grant it
    executed: items, rows deleted, rows already gone (`deleted + already_forgotten = items`).
  - `connectors.owntracks_retention_tombstones` keeps one row per forgotten raw row: its
    `idempotency_key` (the logical key), its former id, the grant's content digest and grant id.
    Every tombstone needs its receipt in the same transaction (deferred foreign key).
  - Both are append-only by trigger, because an `init-db.sql` replay re-grants `connector_writer`
    every privilege on `connectors`. A fresh migration grants `connector_writer` only select and
    insert and `butler_chronicler_rw` only select.
  - `connectors.owntracks_points` skips an insert whose `idempotency_key` is tombstoned, as it
    skips a duplicate, so a replayed or late webhook cannot restore a forgotten fix. It is keyed
    on the raw logical key, so it holds even for a granted row that never had a point event.
  - Nothing writes these tables yet. The downgrade refuses while either holds a row.

## Implementation Notes

- The Chronicler `owntracks.points` adapter tracks ingestion progress by arrival order, not
  device time: it pages `(recorded_at, id)` and keeps `{version, recorded_at, id}` under the
  carryover key `"_ingest_cursor"` (`src/butlers/chronicler/adapters/owntracks.py`), so an
  offline-queued fix uploaded after a newer one is still projected (bu-e6lrho). Rows that arrived
  within the last 60 s wait for the next run, which keeps the cursor behind slow-committing
  inserts. Episodes still use device `ts`, and the relational `watermark` still reports the
  newest device `ts`. A legacy ts-only checkpoint cuts over at the earliest-arrived row with
  `ts > watermark` (re-reading only rows that arrived since, idempotently), not a history rescan.
  A late fix (device `ts` at or before its endpoint's open-episode `end_at`) always gets its point
  event but never replaces that carryover, and it writes no episode when it lies within the movement
  gap of an open or stored movement episode; only a late span more than the gap away from every
  episode becomes its own episode.
  Index: `connectors.idx_owntracks_points_recorded_at_id` (core_309).

- The Chronicler `owntracks.ssid_presence` adapter keeps its cursor as `{watermark, uuid}` under the
  checkpoint-carryover key `"_source_cursor"`
  (`src/butlers/chronicler/adapters/owntracks_ssid.py::_SOURCE_CURSOR_KEY`) (OwnTracks ids are
  UUIDs, `watermark_id` is BIGINT) and pages by `(ts, id)`.
  A malformed or mismatched cursor triggers a bounded replay. One `run()` transaction under a
  source-keyed advisory lock holds every write and the checkpoint.

- The point/place trail adapters serialize each source from checkpoint read
  through outcome under a transaction-scoped advisory lock. Marker-only
  revisions and bounded bootstrap/carryover state commit with the success
  watermark; database errors and cancellation cannot acknowledge partial state.
  Revisions keep existing point identities, privacy, retention and tombstones.
  Missing evidence is not recreated. Pending retained candidates are reread by
  exact UUID only. The accepted anchor UUID distinguishes tied-time replay from
  a continuation at that mature anchor; unavailable seed evidence leaves it trusted
  with an explicit warning rather than fabricating a historical correction.
