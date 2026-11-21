# JARVIS pursuit, run 24: the regenerable day (2026-11-21)

**Reader:** the owner deciding what the Chronicler ecosystem must keep and index now so that
better models can rebuild past days later. **Status:** planning only; nothing below is claimed
delivered. **Epic:** `bu-uhbhzq`. **Release gate:** `bu-0j96x7`, assigned to the owner. Closing the gate
unblocks all fifteen children and the fleet starts executing them. **Artifact report:**
https://claude.ai/artifact/Q9KpzJeuGvsWnmRo2GdkQv (private to the owner; no structured attachment is published).

## Direction

The owner's framing for this run: the system will eventually be good enough to regenerate a full
timeline of every day from the data fed in, so ingestion and indexing have to be right now. The
test applied to every finding was: could a much smarter Chronicler in two years rebuild any past
day minute by minute from what we kept, and find that evidence efficiently?

Today the answer is no, for four reasons that the moves below address in order:

1. Some evidence is never captured or is destroyed. The calendar connector has never ingested an
   event, desktop activity is entirely dark, superseded memory facts are deleted after seven
   days, and calendar edits overwrite the prior state.
2. Raw evidence survives by accident, not by contract. Pruners are unscheduled, a purge fails on
   a foreign key, the largest raw store exists only because one spec contradicts another, and
   backups rotate out after about six months.
3. Derived history cannot be re-derived safely. No row records its derivation, rule changes
   reach the past through one-off in-place migrations, and owner corrections are bound to row
   ids that a rebuild replaces.
4. The system cannot tell a quiet day from a blind one. Source health is evaluated at the
   present moment and written onto past days.

## Scope and evidence

A Chronicler-only run: nine read-only auditors (eight ecosystem lenses, one page surface), not
the standing page inventory. The owner removed the hourly stagger for this run, so three agents
ran first and six ran together. Lenses: retention census, capture fidelity, replay machinery,
storage and indexing, new evidence sources, the regenerable-day design, indexing by person and
place, and source coverage. They returned 86 findings and 52 proposals.

The run began with a live investigation of the dev stack (k3s namespace `butlers-dev`,
Postgres 17.8, read-only). Measured on 2026-11-21:

| Fact | Value |
|---|---|
| Database size | 27 GB |
| `connectors.filtered_events` | 19 GB, about 4.2 GB and 2.4 M rows a month, over 99.9% Home Assistant sensor readings |
| Cost per sensor reading | about 1.77 KB including five indexes |
| `chronicler` schema | 131 MB: 36.6k point events, 22.5k episodes, rollups for 208 dates |
| Stored daily narratives | 0 of 208 dates |
| Registered sources / sources that never projected a row | 24 / 5 |
| Raw location fixes | 2,761 since April, 9 to 30 a day |
| Desktop activity rows | 0 ever; the connector fails every minute |
| Calendar events ingested by the connector | 0 ever; full sync repeats every few seconds |
| Superseded memory facts deleted since May | about 7,200 |
| Chat messages with sender recorded as `multiple` | all 2,851 from the two user-client channels |
| Downsampling or compaction tiers | none |
| Backup horizon | 14 daily, 8 weekly, 6 monthly snapshots; no never-pruned archive |

Each critical claim was re-checked by the orchestrator against the live database, logs or
source before ranking. Structured per-agent output stays in the local, untracked harvest; the
packets live in the beads' structured fields.

## Ranked moves

Trust and evidence-loss defects lead; the regeneration machinery follows; new senses and the
page come last. Moves 12 and 15 depend on earlier moves and are blocked on them in Beads.

1. **Repair Google Calendar capture and keep an append-only event revision journal** (`bu-uhbhzq.1`, cost M). The calendar connector has never ingested an event: its full sync asks for `orderBy=startTime`, which suppresses the sync token, so it re-syncs every few seconds and logs a save that never happens. The calendar lane Chronicler reads is a separate per-butler copy that overwrites and deletes in place, and Google keeps no edit history. Fix the sync, then journal every observed change so "what was planned for that day, as seen that day" survives.

2. **Unblock daily narration: retired sources, per-lane gating, and availability frozen per day** (`bu-uhbhzq.2`, cost M). No day has a stored narrative: 0 of 208 dates. One decommissioned source (`health.meals`, four rows ever) has marked 105 consecutive days `feeder_dark`, and narration skips the whole day on any dark feeder. The flag is also computed from the state at `now()` and written onto past days, so a source that recovers today erases the record of the days it was really down. Add a retired state, gate per lane, and freeze each day's source availability when the day settles.

3. **Stop deleting belief history: supersession closes an interval and never deletes** (`bu-uhbhzq.3`, cost S). Every memory butler hard-deletes superseded facts after 7 days, ignoring retention class: about 7,200 fact rows since May. Those rows are the closed validity intervals a regenerator needs to answer "what was true on that day" (employer, address, a device's fault). Volume is a few thousand small rows, so keeping them costs nothing.

4. **Evidence before acknowledgement: every connector commits a durable evidence row first, and the routing inbox stops being the archive** (`bu-uhbhzq.4`, cost L). For 8 of 12 sources the only verbatim copy of what arrived is `switchboard.message_inbox`, a routing buffer whose spec authorises a one-month horizon that happens not to be enforced. OwnTracks shows the failure mode: the evidence write runs after the Switchboard submit and is non-fatal, so 1,764 early fixes exist only as inbox text. Make a connector-owned evidence journal the commit point, record a per-source capture manifest, and bind the loaded short horizons to an explicit contract before anyone enables them.

5. **End the 'multiple' sender: a content-blind party ledger at ingestion and re-resolvable entity links** (`bu-uhbhzq.5`, cost L). Every WhatsApp and Telegram user-client message reaches the registry Chronicler reads with the literal sender `multiple`. So 2,102 social episodes are unattributed, concurrent chats with different people merge into one episode, and per-message times are lost. Entity links are also frozen at projection time. Record who and which chat per message at ingestion without content, keep the raw identifier on every evidence row, and add a deterministic pass that re-resolves links when the graph improves.

6. **Derivation ledger: algorithm version, config snapshot, run and evidence on every derived row** (`bu-uhbhzq.6`, cost M). No derived row records which algorithm version, configuration or run produced it, and derived adapters read today's anchors, routines, Wi-Fi mapping and timezone when projecting any past date. Nine of 41 Chronicler migrations rewrote derived history in place. Stamp every derived row, record each run, and start capturing the as-of configuration now, because past configuration cannot be reconstructed later.

7. **Retention charter: a per-source evidence-preservation contract, the location-forgetting decision, and a never-pruned cold archive** (`bu-uhbhzq.7`, cost M). Little raw evidence is deleted today, but mostly because pruners are unscheduled, drop functions have no caller, and one purge fails on a foreign key. No contract says, per source, how long raw, compacted and derived forms live or who may delete them. The 30-day location promise is hard-capped in DDL and would leave only coarsened summaries; it conflicts directly with the regeneration goal. Backups rotate out after about six months. Write the charter, enforce it in CI, decide location, and seal closed months into an archive that outlives the backup rotation.

8. **Typed telemetry series store: narrow rows, a dictionary, raw and minute tiers, fed before the filter** (`bu-uhbhzq.8`, cost L). Home sensor history lives in the replay quarantine table: 19 of 27 GB, about 1.77 KB per reading including five indexes, four copies of the state object per row, and no index by entity, so one sensor's history is a 13 GB sequential scan. At today's rate that is about 575 GB in ten years. The store also exists only because Home Assistant writes full payloads into filtered rows, which another spec forbids, and the significant readings go to a different table. A typed store on stock Postgres needs about 52 bytes per reading, about 17 GB in ten years, which makes keep-forever affordable.

9. **Sense coverage ledger: per stream and local day, with typed absence reasons** (`bu-uhbhzq.9`, cost L). For a past day the system cannot tell "nothing happened" from "the sense was down". Heartbeat counters persist 0 when the counter is unreadable, blocked connectors stop heartbeating, absence causes live only in logs, and the planned connector event clock (bu-h40h2b.12) measures endpoints, not evidence streams. Record, per stream and local day, what cadence was expected, what arrived, and a typed reason for absence: down, consent-gated, not yet connected, or genuinely quiet.

10. **Projection hygiene: yield sentinels, the cursor defects found, and day-scale indexes** (`bu-uhbhzq.10`, cost M). Five adapters have run thousands of times and produced nothing, and nothing notices. The session projection's global watermark has been pinned at April; the ActivityWatch adapter pages by event time and would skip late evidence forever; the motion adapter is eleven hours behind against a throughput cap. Every override-aware day read sequentially scans all episodes because the corrected views defeat the time indexes, and the day-repair proof has read 5.76 billion tuples for want of one index.

11. **Day evidence manifest: what evidence existed for each date, frozen at close** (`bu-uhbhzq.11`, cost M). Nothing records which evidence a day had. The reconciled day is computed inside the day-close tool, truncated, and never persisted; the frozen bundle survives only incidentally in a session's tool-call log. Write one manifest row per date and source at day close: counts, covered intervals, raw-store pointers, retention state and a content hash. It is the index a future backfill uses to find thin days and to prove a regeneration read everything.

12. **The regenerable day: window-addressable replay with shadow generations, an immutable day account, a budgeted queue and a quality gate** (`bu-uhbhzq.12`, cost L). This is the vision made concrete. Today a rule change reaches history only through a one-off migration that mutates rows in place, with no dry run, diff or receipt; replay through upsert resurrects tombstones and never removes rows that are no longer emitted; narratives are overwritten with no model or input hash. Replace that with a replay job addressed by source and date window that writes a shadow generation, shows a diff, and promotes by supersession. The day account becomes a versioned, immutable artifact. Deterministic rebuilds are unbounded; re-narration is capped per night; a regenerated day is promoted only when it measurably explains more.

13. **Owner assertions: corrections anchored to time intervals that every regeneration must honour** (`bu-uhbhzq.13`, cost M). Your corrections are bound to the UUID of a derived row. Re-derivation replaces that row, so the correction is stranded; a known bug already lets a note-only override undo a tombstone. Store corrections and gap-interview answers as first-class evidence anchored to a time interval and subject, so they outrank any derivation and survive any rebuild. Orphans are surfaced, never dropped silently.

14. **Owner-device evidence receiver: a push-based desktop sense that replaces the ActivityWatch pull** (`bu-uhbhzq.14`, cost M). Desk hours are the largest invisible stretch of the day: activity coverage from 10:00 to 18:00 is 16 to 24 percent. The ActivityWatch connector cannot work as deployed, because the pod polls its own localhost and the egress policy forbids anything else; it has failed every minute of its life. Even if it worked it would erase evidence after 14 days. Replace the pull with a small shipper on the owner's machine that pushes to an authenticated receiver, and project in arrival order so late evidence is never skipped.

15. **Chronicles shows coverage, lineage and replayability: a coverage calendar, a raw-evidence door, and a derivation stamp** (`bu-uhbhzq.15`, cost L). The Chronicles page shows source health only in the present tense, and its spec deliberately hides it for past days, so a day with a dead sense looks like a quiet day. The evidence chain stops at the Chronicler point event; no block shows which derivation made it or how old it is; an API that exposes dead projection sources has no UI consumer. Add a months-long calendar of explained minutes per day and source, a door from any block through to the raw row with its retention verdict, and a derivation stamp on every block and briefing.

## Owner decisions

The moves turn on choices only the owner can make. Each bead repeats its own and tells the
worker not to choose. The one that shapes the rest is location in move 7: the declared 30-day
raw-location forgetting promise (not yet enforced; every fix already carries an expiry time)
conflicts directly with keeping enough to regenerate.

- **Move 1.** Journal the whole provider history as baseline revisions (about 4k events) or only a bounded window. Recommended: whole history. Revisions keep full prior titles or only digests. Recommended: full titles, sensitive class. Whether subscribed calendars that are not the owner's are journaled. Recommended: no.
- **Move 2.** Partial narration that names dark lanes, or skip the whole day when any lane is dark. Recommended: partial; the whole-day skip has produced zero narratives. Whether the day-close message may name long-running outages (for example 'Telegram has not been captured for 126 days'). Recommended: yes, one clause, inside the existing message.
- **Move 3.** Keep superseded facts forever or N years. Recommended: forever.
- **Move 4.** Approve the amendment that message_inbox is not an evidence surface. Approve the one-shot copy of April to June inbox-only location fixes into the location evidence table.
- **Move 5.** Ledger stores keyed digests of handles or normalized raw identifiers. Recommended: keyed digests, raw handles stay in connector stores. Which resolver source re-resolution reads: the existing direct grant on relationship.entity_facts or only the public identity projection.
- **Move 6.** Replay uses the configuration in force on the day (bitemporal) or today's understanding applied to the past. Recommended: record both, default to as-of-day. Whether the owner's timezone may differ per date on travel days.
- **Move 7.** Location: keep the 30-day raw purge with a regeneration-grade coarse trail that keeps every fix timestamp; or lift the cap so raw fixes are kept until you say otherwise; or move expired raw fixes to sealed escrow. Recommended: lift the cap and make the horizon your explicit choice; storage cost is negligible (4 MB in seven months). Approve the per-source horizon table. Archive destination and key custody: the existing offsite restic repository under a never-forget tag, or a separate store.
- **Move 8.** Keep raw readings forever (about 1.7 GB a year) or raw for N years and minute rollups forever. Recommended: forever. Whether Home Assistant context ids and attribute snapshots are kept for actuator domains. Whether occupancy sensors count as room presence in the day account.
- **Move 9.** Whether a consent-pending Telegram connector may heartbeat at all. Recommended: yes, under the pending identity. Whether you may annotate past stretches with a reason by hand. Retention of raw heartbeat samples once state runs are kept.
- **Move 10.** Override-aware function with no spec change, or materialized effective columns on the canonical row (needs a Correction Overlay amendment). Recommended: the function.
- **Move 11.** Whether a day may be frozen automatically when raw evidence expires, or only when you freeze it.
- **Move 12.** Nightly re-narration budget and model tier, and whether a model upgrade alone justifies re-narrating unchanged accounts. Whether a regenerated narration may replace the recap shown for a past day or appears beside it. How long superseded generations are kept. Recommended: all; the whole schema is 131 MB.
- **Move 13.** When later strong evidence contradicts an assertion: the assertion wins silently, or it raises one question in the next gap interview. Recommended: wins, and asks once. An orphaned correction blocks promotion of a regenerated day, or promotes with a visible notice. Recommended: visible notice.
- **Move 14.** Which machines run the shipper. Recommended: home PC first. Retention class for window titles and URLs: keep as sensitive, keep only domain plus salted hash, or keep N days.
- **Move 15.** Approve the spec amendment that lets past days show point-in-time source state. Whether degraded days may show partial content instead of bypassing it.

## Supporting tier board

No prior run tiered these keys, so no movement is claimed.

| Key | Verdict | Baseline |
|---|---|---|
| Chronicles page (`/chronicles`) | functional | new |
| Retention and cleanup | functional | new |
| Capture fidelity | weak | new |
| Replay and reprojection | weak | new |
| Storage and indexing | weak | new |
| Day account and regeneration | weak | new |
| Indexing by person and place | weak | new |
| Source coverage | weak | new |
| Evidence sources for a full day | weak | new |

## Themes

- **Survival by accident.** Evidence persists because deletion code is dormant, not because a
  contract protects it: unscheduled pruners, a purge that fails on a foreign key, a quarantine
  table that doubles as the sensor archive against its own spec.
- **Present state written onto the past.** Source darkness, anchors, routines, Wi-Fi mapping and
  timezone are all read as of now and applied to any past date.
- **Logs that claim success.** The calendar connector logs "syncToken saved" on a branch that
  saves nothing; adapters report healthy while yielding nothing for thousands of runs; unreadable
  heartbeat counters persist as zero.
- **Summaries where the raw form was available.** Sleep stages become a minutes total, chat
  becomes batch flushes with one sender, a sensor reading becomes four copies of a state object.
- **Identity bound to things that change.** Corrections attach to derived row ids, entity links
  freeze at projection time, evidence references are bare ids that will not survive compaction.

## Release and verification

Gate `bu-0j96x7` is assigned to the owner and is the only ready bead. Every child was created with a
full packet in its structured `design` and `acceptance` fields and depends on the gate.
`bd lint` over the epic, the gate and all fifteen children reported no warnings. This run
changed no runtime code, specs or data; this dossier and its index rows are its only tracked
output.

Two existing beads overlap and are named in the packets: `bu-h40h2b.12` (connector event clock;
move 9 extends it from endpoints to evidence streams) and `bu-gkofvo` (note-only override undoes
a tombstone; move 13 subsumes it).

## Dropped and deferred

Deferred by the fifteen-move cap or by ordering, not rejected. None is filed.

- **Wearable at full fidelity: sleep stage intervals, minute heart rate and steps, provider revisions** (lens output: capture-fidelity 1, new-evidence-sources 1). The provider keeps the history, so this stays recoverable; ranks first for the next run.
- **Places as first-class entities with geometry and persistent place candidates** (lens output: life-graph-indexing 2). Waits on the location decision in move 7 and overlaps the open anchor-place work.
- **Co-presence index separating in-person, remote and scheduled time** (lens output: life-graph-indexing 3). Needs move 5's party ledger first.
- **Find-by-person, place, thing or text retrieval, and the situation at an instant** (lens output: life-graph-indexing 4). Needs moves 5 and 11.
- **Finance purchases as timed place evidence with honest time precision** (lens output: new-evidence-sources 3). Small; held back only by the 15-move cap. 41% of purchases carry a made-up local-midnight time.
- **Split Telegram consent so owner-activity metadata runs without content ingestion** (lens output: new-evidence-sources 4). An owner consent decision more than a build.
- **Phone senses through Home Assistant companion sensors** (lens output: new-evidence-sources 2). Reuses move 14's receiver; depends on phone OS.
- **Owner-supplied historical imports (location timeline, photos, streaming history, git)** (lens output: new-evidence-sources 5, capture-fidelity 3). Valuable for years before Butlers; needs move 4's evidence journal to land into.
- **Spotify device, context and pause intervals; Steam achievement unlock times** (lens output: capture-fidelity 3 and 5). Per-track plays are already kept; incremental.
- **Typed, range-addressable evidence references with a reverse index** (lens output: storage-indexing 5). Folded into move 12's design; file separately if it grows.
- **Review-a-day keyboard loop and partial rendering for degraded days** (lens output: chronicles-ux 5). Follows move 15.

Dropped as already owned: place-stay bridging and anchor places, trail plausibility, the
situation precedence tree, the gap interview, persisted rollups and anomaly flags, the taste
ledger, personal baselines, and re-proposing the location purge itself.
