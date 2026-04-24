## Why

Butlers already collect rich timestamped evidence about the owner's life:
Spotify listening, Steam play activity, OwnTracks location transitions,
completed calendar events, communication metadata, and agent sessions. The
evidence is useful but fragmented by source and domain. No current capability
answers the retrospective question: "What was I doing with my time?"

A user-facing lived-time timeline should be the first consumer of a deeper
capability, not the capability itself. The existing `/timeline` route is already
an operational event stream, so this proposal intentionally leaves the final
dashboard route unresolved. The system needs a retrospective Time Butler that
owns a derived temporal model of lived time while preserving source provenance,
overlap, and uncertainty.

## What Changes

- Add **Chronicler**, the retrospective Time Butler.
- Add a Chronicler-owned temporal model with point events, overlapping episodes,
  source links, boundary provenance, and owner overrides.
- Build episodes/events from persisted evidence streams using three cost tiers:
  direct projection, deterministic aggregation, and sparse LLM interpretation.
- Keep passive timeline construction out of Switchboard routing competition.
  Switchboard routes to Chronicler only for explicit user requests.
- Define a Chronicler compatibility contract for future timestamped sources
  such as Fitbit.
- Leave the user-facing dashboard route unresolved for now. The existing
  `/timeline` route is already specified as an operational event stream; this
  change does not claim that route or specify UX design.

## Capabilities

### New Capabilities

- `butler-chronicler`: Domain butler for retrospective lived-time
  reconstruction.
- `chronicler-source-compatibility`: Contract requiring future timestamped
  sources to declare how Chronicler can consume their evidence.

### Modified Capabilities

- `butler-switchboard`: Routes explicit time-review/timeline requests to
  Chronicler, but does not route every passive source event to Chronicler.
- `chronicler-api`: Exposes Chronicler-owned read and correction endpoints under
  `/api/chronicler/*`; this change does not claim the operational `/timeline`
  route.
_None for dashboard UI in this change. Dashboard route/UX is explicitly deferred._

## Impact

- **Roster:** New `roster/chronicler/` butler identity and manifesto.
- **Database:** New Chronicler schema tables for events, episodes, links,
  overrides, projection checkpoints, and source adapter metadata.
- **Projection jobs:** Deterministic adapters for initial sources: core session
  records, completed calendar instances, and durable Spotify session summaries.
  Steam, OwnTracks, communication bursts, fine-grained Spotify track timelines,
  Home Assistant, live-listener, and Fitbit-like sources are candidate future
  sources, not initial adapters.
- **Switchboard:** Classification prompt/schema update for explicit Chronicler
  intents only.
- **Dashboard/API:** Future read APIs under a Chronicler-owned namespace such as
  `/api/chronicler/*` for time review queries, episode correction, and source
  provenance. The UI route is TBD and must not conflict with the existing
  operational `/timeline`.
- **No connector ingestion change required for the initial release:** Existing
  sources can be consumed through adapters where durable evidence contracts
  exist. New timestamped sources should include a Chronicler compatibility note
  or explicit deferral.

## Non-Goals

- Future agenda, schedule planning, forecasting, or time-blocking.
- Productivity scoring or judgment.
- LLM interpretation of every ingestion event.
- Making Chronicler the global context broker or router.
- Replacing specialist domain truth in Lifestyle, Relationship, Health, Travel,
  Home, Calendar, or core session records.
- UX design for `/timeline`.
- Claiming the existing operational `/timeline` route.
- Treating the already-scoped v1 system as amended before Heart and Soul is
  explicitly updated.
