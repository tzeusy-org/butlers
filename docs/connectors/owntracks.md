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

## Evidence before acknowledgement

For a location event the connector writes `connectors.owntracks_points` first, then
submits to Switchboard, then saves the checkpoint and answers the phone. A failed
evidence write answers 503, so the phone keeps the point and nothing is submitted.
A failed submit keeps the row; the existing retry, park and replay path is unchanged.
A point that can never be stored (a malformed or out-of-range `tst`, `lat`, `lon` or `acc`,
or one the database refuses as data) is acknowledged and not ingested, counted and logged by
reason category only, because OwnTracks resends in order and a resend would fail the same way.
NUL characters are stripped from text fields first. Connection, serialization, deadlock,
missing-table and privilege errors still answer 503. `message_inbox` is a routing buffer, not
an evidence surface.

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
Raw expiry is **off until the owner turns it on** (bu-s11n0s.75): the policy's
`enforced` switch (chronicler_041) defaults to false, and the response reports
`status: not_enforced` while it is off and `enforcing` while it is on. The owner
sets it with the same compare-and-set PUT as the horizon (`enforced: true|false`;
omitted, it is kept), and the change is audited. The dashboard's
**Settings > Location retention** page (`/settings/location-retention`) sets both: the horizon,
and the switch behind a confirmation that says what turning it on deletes; shortening the
horizon while expiry is on is confirmed too. While it is off, raw fixes are retained
indefinitely. `OWNTRACKS_RETENTION_DAYS` governs only the
`public.ingestion_events` audit rows.

- **On.** The hourly `chronicler_location_retention` job prepares a grant from the
  adapters' current revisions, asks Switchboard to forget its copies (only if the switch, read
  again just before, is still on), reconciles what the
  connector executed and states lag. The connector's retention worker (on in the chart)
  fetches ready grants and deletes exactly their rows.
- **Off.** Preparation refuses (`EnforcementOff`, writing nothing) and the
  `location_retention.grants_ready` offer is empty, read in the same statement, so the
  connector's next fetch deletes nothing. A grant it fetched before the switch went off may
  still execute. The job only reconciles, measures no lag and withdraws open lag conditions.
  A missing policy row counts as off.
- **Smoke.** `scripts/location_retention_smoke.py` (DSN on stdin) prints a read-only,
  count-only report and exits non-zero on a missing policy, a failing or (while on) stale
  run, a grant with rows prepared since the switch last changed while off, or a failed grant.

The deletion path, built dark slice by slice and now switched as above:

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
    fix (an anchor without an id: once older than the cutoff).
  - Prose cannot be coarsened, so in the same transaction it deletes cached day-close prose
    (`tier2_cache`, superseded rows included) built on the granted rows. It deletes a row that
    cites a granted point event or a summary those rows fed, or that cites nothing but whose
    window holds a granted fix. A row over a granted fix whose provenance is not a list of refs
    fails the whole run, because its overlap cannot be determined. A deleted day regenerates
    from the coarsened summaries (bu-s11n0s.7 S4b-4a).
  - A later batch that merges into a stored movement and starts at the same instant keeps the
    stored start and its `spatial_precision_m`/`spatial_scheme` markers, as a tie on the end
    keeps the stored end. A replay cannot write an exact fix back over a coarsened start.
  - Only the switched retention job calls it, and it refuses while the owner's switch is off.
    `tests/chronicler/test_location_retention_dark_guard.py` fails on any reference to
    `prepare_retention_grant`, `append_grant_state` or the retention modules outside them, but
    the one scheduled-job registration.
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
    `ready` (`HoldersIncomplete`) unless every required holder has a `complete` receipt (S4c-1,
    below). Receipts are recorded only by the holder collector (S4c-3).
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
  - The downgrade refuses while either holds a row.
- The connector can execute a grant, off by default (bu-s11n0s.7 S4b-3b):
  - Chronicler offers grants with a `ready` state and neither `reconciled` nor `failed`
    (`location_retention_handoff.ready_grant_payloads`): grant id, cutoff, and each raw row's
    id, logical key and digest, nothing else. `ready` cannot be written until holder copies
    land (S4c), so nothing is offered yet.
  - The offer reaches the connector only through the `location_retention.grants_ready` named
    relay on Switchboard, whose one allowed caller is the `connector-owntracks` workload
    speaking for `owntracks`. The connector needs a relay-audience workload token for that
    (S4b-3c); without one it fetches nothing.
  - `owntracks_retention.execute_grant` runs one transaction per grant. It takes
    `connectors.owntracks_forget_lock()` (core_343, one exclusive advisory lock), then refuses
    the whole grant if a key is already tombstoned or a present row's key, recomputed digest or retention clock does not
    match. Otherwise it deletes the present rows, counts missing ones as already forgotten,
    tombstones every key and writes the receipt. Executing a grant again returns its receipt.
  - The points trigger takes the same lock shared (`owntracks_forget_guard()`) before it reads
    tombstones, and also fires on `UPDATE OF idempotency_key`. A webhook replay racing a
    deletion waits for its commit and is then skipped, instead of slipping in after the delete.
    Inserts never wait for each other, and a transaction holds one lock entry however many rows
    it inserts.
  - The same transaction withholds the connector's parked `connectors.filtered_events` copies
    of each granted fix (S4c-4): envelopes parked after their ingest attempts were spent (the
    point itself is still persisted). A copy matches when `'owntracks:' || sender_identity || ':' ||
    external_message_id` (`{tst}:location`) is a granted key; text is never matched, and
    transition and waypoints copies never spell a location key. The row stays for the park
    audit, but its `full_payload.payload` becomes `[location withheld: raw retention]`
    and it carries `replay_unavailable`, so the replay drain refuses it rather than submitting
    a hollow event. A failure here rolls back the delete, the tombstones and the receipt.
  - A policy-filtered fix is recorded to `filtered_events` without its raw body and never reaches
    `owntracks_points`, so it has no point row, never enters a grant and is outside a grant's
    reach. Such a row keeps no coordinates in the first place: see the policy-blocked bullet
    below (bu-s11n0s.73).
  - A copy written after the fix was forgotten (a resend parked again) is withheld on insert
    by core_345's `filtered_events_withhold_forgotten` trigger, which takes the forget guard
    first, so a copy racing a deletion waits for its commit.
  - The worker runs only with `OWNTRACKS_RAW_RETENTION_ENABLED=true` (hourly by default,
    `OWNTRACKS_RAW_RETENTION_INTERVAL_S`). An unreachable relay, a refusal, a missing token or
    a malformed reply executes nothing; a grant that no longer matches is skipped.
- Chronicler reconciles each ready grant against what the connector committed
  (bu-s11n0s.7 S4b-4b, `location_retention_reconcile.reconcile_ready_grants`):
  - It reads the batch receipt, the tombstones and `connectors.owntracks_points` under its own
    `SELECT` grants. The connector's reply is never evidence.
  - With no receipt the grant stays `ready` (pending); nothing re-mints a deletion set.
  - `reconciled` needs the receipt's item count to equal the grant's, the tombstones to be
    exactly the grant's items (raw id, logical key, digest), and no granted row present by id
    or by logical key. Anything else is `failed`. Both receipts are counts only.
  - States are appended through `append_grant_state`, so a grant another run settled first is
    skipped.
- A retention job wraps reconciliation and states what is overdue
  (bu-s11n0s.7 S4b-4c, `location_retention_job.run_location_retention_job`, run hourly by
  `run_scheduled_location_retention` as above):
  - `chronicler.location_retention_runs` (chronicler_038) holds one run record. `running` commits
    before any work; a run still `running` within 30 minutes makes the next one skip, and an
    older one counts as a failure (`Unfinished`) that stays standing even if the takeover run
    succeeds; only a later clean run clears it. The outcome is written in its own transaction
    and keeps only the exception class.
  - Raw fixes past the policy cutoff are counted by the first reason that applies:
    `grant_failed`, `awaiting_deletion`, `holder_blocked`, `awaiting_holders`, `held_open`,
    `uncovered` (no exact coverage at some adapter's last recorded revision: malformed, refused,
    `existing_only` miss, pre-coverage) or `awaiting_grant`. Each non-zero category is an owner condition under
    `chronicler:location-retention-projection-lag` (count and oldest age in hours only).
  - The failure streak, grants that failed reconciliation and ready grants with no receipt after
    24 hours (a connector that keeps refusing a grant leaves it pending forever) are conditions
    under `chronicler:location-retention-purge-failed`.
  - Lag is reconciled only after a complete read, so a failed run never resolves it. The job
    proposes no insight and sends nothing.
- A grant becomes `ready` only with every holder's complete receipt
  (bu-s11n0s.7 S4c-1, `location_retention_grant.record_holder_receipt`):
  - `chronicler.location_retention_holder_receipts` (chronicler_039, append-only) records what
    Chronicler read back from each holder of a copy derived from granted rows: `complete` at
    most once per grant and holder, or `blocked` (still processing, routed onward, ambiguous),
    which may repeat until then and never counts. A holder that completed takes no later receipt,
    so `ready` never rests on a complete that a newer reading contradicts. Receipts are counts
    only and stop once the grant is ready.
  - The one required holder is `switchboard.message_inbox`, whose normalized text carries exact
    coordinates. Route copies make its receipt `blocked`. The connector's `filtered_events`
    copies are withheld in the deletion transaction itself (S4c-4), so they are not a holder.
  - Only the holder collector (S4c-3, below) writes receipts, and only when the job is given
    a forgetter.
- An OwnTracks event the connector's ingestion policy blocks keeps no coordinates
  (bu-s11n0s.73):
  - It returns before `owntracks_points`, so no retention grant can name it, yet its
    `connectors.filtered_events` row lives for twelve months. The row therefore stores
    `payload.raw = null` and coordinate-free `normalized_text` (`build_filtered_event_text`): the
    reported regions for a location, the region and direction for a transition, the waypoint
    names for a waypoints sync. The device and event time stay in `sender_identity` and
    `external_message_id`, so the filter audit still counts it.
  - Without its body the event cannot be replayed, so the row carries `replay_unavailable` and a
    replay request ends `replay_failed` with `not replayable: a policy-blocked OwnTracks event
    keeps no coordinates`.
  - core_346 gives rows recorded before this change the same shape once (idempotent; parked rows
    and other connectors untouched). Its waypoints text keeps the count, not the names.
- Switchboard can forget its inbox copies of granted fixes, still dark (bu-s11n0s.7 S4c-2,
  sw_082, `roster/switchboard/tools/location_retention.forget_location_copies`):
  - Every accepted OwnTracks envelope stays in `switchboard.message_inbox`, and its
    `normalized_text` and `raw_payload.payload` carry exact coordinates even at the metadata tier.
    Rows are matched by `source_channel = 'owntracks'` and exact `request_context.idempotency_key`.
  - A copy Switchboard handled by policy alone (`skipped`, no session, a `skip` policy bypass)
    has both fields replaced by a fixed withheld marker. Its `request_context.content_hash_key` is
    dropped too: a 16-hex hash of text and sender that could be brute-forced back to the fix.
    Everything else stays, so dedupe and the lifecycle audit still work.
  - Any other copy may still be processing or was routed into sessions, so the whole key is
    left as it is and counts as blocked (fail closed). Nothing reaches routed copies.
  - Every unblocked key is tombstoned in `switchboard.location_retention_forgotten_keys`. A
    `BEFORE INSERT` trigger withholds a later OwnTracks envelope for a tombstoned key. A shared
    advisory lock makes an insert racing the forgetter wait for its commit.
  - `switchboard.location_retention_forgetting` keeps one count-only receipt per run: `complete`
    (once per grant; a repeat returns it) or `blocked` (repeats until the block clears).
  - Both tables are append-only by trigger; the runtime role may only read and insert them.
    The downgrade refuses while either holds a row.
  - A key with any `switchboard.dead_letter_queue` row (by `request_context.idempotency_key`)
    is blocked too: dead letters are never purged and the forgetter does not reach them. Today
    only dashboard messages are dead-lettered (bu-s11n0s.7 S4c-3).
- Chronicler collects Switchboard's receipt and makes a grant ready (bu-s11n0s.7
  S4c-3, `location_retention_holders.collect_holder_receipts`):
  - The retention job, given a forgetter, first asks Switchboard about each prepared grant with
    rows and no state, least recently asked first (five per run). In production the forgetter
    is the `location_retention.forget_copies` named relay, which only Chronicler may call and
    Switchboard answers itself, with no MCP tool behind it.
  - The reply must be exactly the count-only receipt for that grant's item count. A `complete`
    one is recorded and the grant becomes `ready`; a `blocked` one is recorded and asked again
    next run. A refusal, an unreachable relay or a malformed reply records nothing for that
    grant. The other grants are still asked, reconcile and the lag snapshot still run, and then
    the run fails, so its failure streak shows it.
  - Each ask's time is kept in `location_retention_holder_asks` (chronicler_040), written before
    the ask. "Least recently asked" therefore counts failed asks too, and a grant that keeps
    failing goes behind the others instead of holding the front of every run.
  - Without a forgetter (the job while expiry is off) no holder is asked, so no grant
    becomes ready.
  - A grant whose holder's latest reading is `blocked` counts its fixes as `holder_blocked` lag,
    ahead of `awaiting_holders`.
- The raw-retention promise covers transition and waypoint envelopes only at the metadata
  ingestion tier (the default; `CONNECTOR_INGESTION_TIER` unset). There their inbox copies hold
  region or waypoint names and no coordinates. At the full tier their `raw_payload` keeps the
  OwnTracks body with coordinates, which no grant names because only location fixes reach
  `connectors.owntracks_points`; withholding those is bu-s11n0s.70 (S4c-5).

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
