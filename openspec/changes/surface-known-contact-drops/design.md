## Decisions

1. **Marker location.** `drop_context` is a top-level key of the stored `full_payload`, not part of `control`, because `IngestControlV1` and `IngestEnvelopeV1` forbid unknown keys and replay rebuilds the envelope from this payload. `_sanitize_replay_payload` removes it. A dedicated column was rejected to avoid a migration on a partitioned table for a flag the aggregate can read by JSONB path within a bounded `received_at` window.
2. **Known-contact basis.** The Gmail sender is checked against the same known-contact set that drives tier assignment, directly rather than through `assignment_rule`, because the label-exclusion drop returns before tier assignment.
3. **No content.** The marker holds a boolean and a basis string. Stored raw stays `{}` (filtered-content privacy tier) and nothing derived from message text is added, so the bearer-material scrub is not bypassed.
4. **Outstanding means unanswered.** The aggregate counts `filtered` and `replay_failed` rows. `replay_pending` is in flight and `replay_complete` is resolved.
5. **Episodes.** One episode is a distinct `(filter_reason, sender_identity)` pair.
6. **Honest unknown.** A failed read returns HTTP 200 with `available=false` and zero counts; the opener treats a missing, errored, or unavailable aggregate as "gate harm unknown".
7. **Limit.** Marking depends on the connector's known-contact set. If that set failed to load the marker is absent, which this slice does not detect.
