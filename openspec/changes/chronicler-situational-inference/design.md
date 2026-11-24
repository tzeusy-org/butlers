# Design: Chronicler situational inference

## Context

Chronicler projects evidence into episodes. Only `activity`-layer episodes count toward lane totals and "untracked" time. Before this change it had no account of *where* the owner was while the phone was silent. It also had no way to decide *what* the owner was doing from where they were. The proposal lists the observed failures. This document fixes the mechanisms.

## 1. Stays survive sparse reporting (place clustering)

OwnTracks in significant-change mode reports when the phone moves about 500 m or more, plus occasional pings. A phone that stays put sends nothing. Silence between two fixes at the same place therefore means the owner stayed. Silence followed by a fix elsewhere means the owner stayed until shortly before that fix.

`cluster_points` keeps its radius, centroid and glitch rules and gains a **bridge cap**, `DEFAULT_BRIDGE_MAX_HOURS = 16`:

- **Same-place bridge.** A fix within the radius after a gap longer than `max_gap` (60 min) but no longer than the cap extends the cluster. The span records `bridged = true`.
- **Departure bridge.** When a fix outside the radius ends a cluster after such a gap (the glitch rule still applies first), the cluster's `end_at` becomes that fix's time and `bridged = true`. Departure is noticed within minutes of leaving, so the overstatement is small. It is declared by `precision = minute`.
- **Held departure.** When that out-of-radius fix is the endpoint's last fix in the batch, the glitch rule cannot see the next fix yet. The fix is held in carryover as `pending_departure` and the stay keeps its last in-place end. The next batch decides: a first fix back inside the radius drops the held fix as a stray, and anything else ends the stay at the held fix, which starts the next cluster.
- A gap longer than the cap never bridges. A dead phone overnight and the next day out must not become a 30-hour home stay.

Carryover keeps the last *observed* fix time, so bridging stays correct across batches.

A `place_episode` becomes `layer = evidence`. It answers "where", and the situational adapter (§3) owns the counted "what". Before this, every place episode counted as Rest wherever it was. A migration tombstones the existing `owntracks.place_cluster` `place_episode` rows, then resets that adapter's watermark and carryover so retained evidence is re-clustered with bridging. `away_episode` rows re-upsert on the same `source_ref` and are left alone.

## 2. Anchor places

`butlers.chronicler.anchors` resolves the owner's anchor places for deterministic readers only:

```
AnchorPlace(kind: home|work|airport|other, label, lat, lon, radius_m, source)
source ∈ owner_places | graph | env_reference | inferred_overnight
```

Resolution per kind, first non-empty source wins:

1. **Owner place registry.** `load_active_owner_places`; a registry row carries its own kind and radius. Chronicler only reads it; the registry editor and its reader cutover stay with bu-jv1moa.7.
2. **Memory graph.** Read-only, through existing Chronicler grants on `relationship.entity_facts`/`public.entities` plus a new SELECT grant on `relationship.address_geocodes`:
   - work: the owner's active `works-at` edge(s) → object entity → its active `has-address` fact. If there are several, prefer `primary`, then the latest `updated_at`. An owner `has-address` fact whose `metadata.label` is `work` or `office` also counts.
   - home: the owner's own active `has-address` fact (label empty or `home`). Failing that, the owner's `rental-location` / `lives-at` edge → place entity → its `has-address`.
   - The address only resolves when `relationship.address_geocodes` holds a `resolved` row for that fact whose `address_sha256` matches the fact's current text (`sha256(object.strip().encode("utf-8")).hexdigest()`, the same in both butlers). A stale or failed geocode is not an anchor.
   - Default radius is 150 m for home and 200 m for work.
3. **Legacy env references.** `OWNTRACKS_PLACE_REFERENCES` entries whose label is `home` or `work`.
4. **Inferred home (home only).** Over the last 28 days, take place episodes that cover 03:00 local. Group them by centroid within 150 m. The group with the most distinct nights, at least 3, becomes `home` with `source = inferred_overnight` and lower confidence. Work is never inferred (owner decision 1).

No coordinate, address or SSID reaches a model, prompt, log line or MCP response. Payloads carry the anchor kind, its label and source, and distance from home rounded to 100 m.

## 3. Relationship geocodes the owner's anchor addresses

Relationship owns the address facts, so it owns their conversion. The Chronicler manifesto forbids it ingesting external data.

- Migration: `relationship.address_geocodes(fact_id PK → entity_facts.id, address_sha256, status resolved|not_found|failed, lat NUMERIC(7,5), lon NUMERIC(8,5), provider onemap|nominatim, confidence, attempts, geocoded_at, next_attempt_at)`. SELECT is granted to `butler_chronicler_rw`.
- Deterministic job `relationship_geocode_owner_addresses` (job mode, hourly). It selects only the owner anchor address facts listed in §2. It geocodes those with no row, a hash mismatch, or a retry that is due:
  - The provider is OneMap (`https://www.onemap.gov.sg/api/common/elastic/search`) when the address is in Singapore: a country of `SG`/Singapore, or a 6-digit postal code with no other country. Otherwise it is Nominatim (`/search?format=json&limit=1`, identifying User-Agent, at most 1 request/s). OneMap falls through to Nominatim on an empty result.
  - OneMap credentials come from `CredentialStore` keys `ONEMAP_EMAIL`/`ONEMAP_PASSWORD`, which are optional. Unauthenticated search is used when they are absent.
  - `not_found` and `failed` back off: 1 h, then 6 h, then 24 h, capped at 7 days. The job never logs address text or coordinates.
- No other address is ever sent: not contacts' addresses, and not the location trail.

## 4. Situational activity precedence (`chronicler.situation_inferred`)

A Chronicler-reads-Chronicler adapter, like occupation/focus. It runs `*/30` and re-derives each owner-local day in a lookback window of 3 days by default, from the start of the oldest day through now.

Pure core (`butlers.chronicler.situation`):

1. **Where-segments.** Live `place_episode` rows overlapping the day, clipped to it. A **tail hold** follows: if the latest accepted location fix is later than every place and movement episode end, the owner is held at that fix until the earliest of fix + 12 h, now, or day end, marked `provisional`. The hold starts at the earliest fix of the trailing run within 150 m of the latest fix (no silence over 16 h), so a stay the clusterer has not closed yet is covered from its start.
2. **Classify each segment.** The nearest anchor whose radius contains the centroid gives the kind. Otherwise the segment is `unregistered`, with distance from home if a home anchor exists. An unregistered segment more than 50 km from home (a trip) gets no block.
3. **Precedence.** Lanes rank `sleep > exercise > travel > work > play > eat > social > rest`. A segment's block lane is work for a work anchor, travel for an airport, social for unregistered or other, and rest for home. The block is the segment minus the union of spans from every higher-ranked lane, taken from the day's other `activity` episodes. The adapter's own rows are excluded. So are point-like sources (`core.sessions`, `comms.message_bursts`), episodes shorter than 10 minutes, and ambient music (`spotify.session_summary`). Music never displaces a place-derived block. An owner gap-interview answer (`chronicler.owner_answer`) displaces every lane: the owner said what that time was. Leftover pieces shorter than 15 minutes are dropped.
4. **Rows.** Episode types are `work_presence_block` (category `occupation` → Work), `outing_block` (`social` → Social), `airport_block` (`travel` → Travel) and `home_block` (`home` → Rest). All are `layer = activity`, minute precision, normal privacy.
   - Confidence comes from `derive_confidence`. Work and home use location dwell plus a registered anchor (strong) → medium. Add an SSID or Wi-Fi corroborator → high. Inferred home → low. Unregistered outing → low. A provisional (tail-hold) block never counts the anchor as strong, so it stays low unless Wi-Fi corroborates it.
   - `source_ref = chronicler.situation:{date}:{kind}:{start_epoch}`. Payload: `local_date`, `place_kind`, `place_label`, `anchor_source`, `distance_from_home_m` (rounded to 100 m), `place_episode_ids`, `bridged`, `provisional`.
5. **Re-derivation.** Each run upserts the day's blocks. It tombstones that day's earlier situation rows that were not re-emitted, unless they carry an owner override. Late sleep or a late fix therefore reshapes the day without leaving stale rows.

Same-lane overlaps with other candidates (an `occupation_block` inside a `work_presence_block`) are merged by day-close reconciliation as before.

## 5. Gap interview targets one block

`evaluate_gap_interview` keeps its trigger: more than 2 h of waking time unaccounted, or a low-confidence occupation block.

- With an occupation block, it asks the existing confirm question.
- Otherwise it picks the **longest uncovered waking interval**, at least 30 minutes, and names it in owner-local time. A day whose unexplained time is only shorter slivers asks nothing. The day's episodes are read by overlap, so sleep that began before midnight still covers the morning. It adds what location says, from the day's location fixes: "no location fixes", "about N km from home" (the farthest fix), or "at <anchor label>" when every fix lies in one anchor; with no home anchor, "away from any registered place". A failed location read degrades to "no location fixes" rather than costing the ask. Example: "Yesterday 07:55–10:40 isn't accounted for (no location fixes). What were you doing?"
- The buttons are `work`, `social`, `play`, `eat`, `rest` and `dismiss`, encoded as `cgi:<date>:<answer>`, at most 22 bytes.
  - The block window is stored in the pending state with the question.
  - A lane answer writes one `chronicler.owner_answer` episode over the block: type `owner_<lane>_block`, `layer = activity`, `confidence = high`, minute precision, with a payload naming the interview. Its category maps to the lane chosen.
  - `dismiss` records nothing that counts. `confirm`/`correct` keep their occupation semantics for existing prompts.
- The resolver stays idempotent. The connector toast table gains the new answers.

## Trade-offs

- **Bridge cap of 16 h.** A shorter cap misses overnight-then-morning stays (22:30 → 12:44 is 14 h). A longer one risks inventing stays across a dead phone. The cap is a constructor argument.
- **No home-office work.** By owner decision, home time is Rest unless something better-ranked overlaps. A future owner decision can add a home-office rule without changing the representation.
- **Graph over registry for semantics.** The owner wants "work" to mean the place the graph says they work at. The registry remains the curated override, with SSIDs and exact radius, and takes precedence when present.
