## 1. Travel location check

- [x] 1.1 Add the pure `travel_location` check (travel-intent detection, accepted fixes, origin/destination anchors, consistent/contradicted/unknown) with synthetic-trail tests.
- [x] 1.2 `reconcile_day` drops a location-contradicted travel intent with `evidence_kind="location"` and annotates kept travel intents with the location verdict.
- [x] 1.3 The day-close bundle tool reads normal-privacy OwnTracks fixes over each travel window plus lookback and exposes the reconciler notes.

## 2. Plan provenance

- [x] 2.1 The calendar adapter records `butler_generated`/`butler_name` from `calendar_events.metadata`.
- [x] 2.2 Every reconciled intent carries `reconciliation.plan_producer`; dropped intents report it.
- [x] 2.3 The day-close prompt narrates butler plans as plans and cites only the location claim the reconciler made.
