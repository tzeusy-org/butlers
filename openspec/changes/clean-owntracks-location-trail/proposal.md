## Why

A good OwnTracks trail (40-70 fixes a day in Beijing and at the Simatai/Gubei Great Wall, Oct 16-18) came out of Chronicler as noise (bu-2tsgqb.6, Chronicler review 2026-10-22):

- An eastern-Poland fix at landing time became its own "Movement (1 points)" episode.
- Beijing and Guangzhou fixes (~1,900 km apart) alternated within a minute.
- Single-fix movement episodes showed durations, because an extension counted only the fixes in the current batch.
- Movement chunks overlapped (Oct 16 08:14-12:01 and 08:25-09:26).
- Every cluster was `place_unknown`, and nothing said the owner was away from home.

## What changes

- One shared trail-plausibility policy (`owntracks_trail.py`) governs the point projection, the place-cluster projection and the dashboard Map trail. Impossible jumps stay as evidence with a `trail_outlier` marker and never feed episodes or the trail.
- Movement episodes need at least two accepted fixes. Their point count covers the whole episode, and a fresh segment that overlaps a stored episode for the same endpoint widens it instead of adding a duplicate.
- Place labels come only from owner-justified names: an `OWNTRACKS_PLACE_REFERENCES` entry, then the owner's own OwnTracks region (`inregions`), else an explicit `place_unknown`, recorded as `label_source`.
- With an owner `home` reference, the place-cluster projection emits an evidence-layer `away_episode` for fixes more than 50 km from home.

## External-data decision (recorded)

No reverse geocoder, gazetteer or other external location service is introduced. The telemetry-distillation contract forbids external geocoding for place labels, and the bead design forbids inventing a provider approval. So a city name such as "Beijing" is **not** produced unless the owner has named the place, by a reference point or an OwnTracks region. City-level naming of unnamed places needs an owner decision on the provider and data flow (offline gazetteer vs. remote geocoder). That decision is a separate follow-up and blocks no part of this change.

## Non-goals

- Retroactive cleanup of duplicate movement rows already stored. New projections stop creating them; existing rows stay until a replay or tombstone pass.
- Raising movement confidence. A single GPS source stays `low` under the confidence ladder.
