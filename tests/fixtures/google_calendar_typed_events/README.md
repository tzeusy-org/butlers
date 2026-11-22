# Typed Google Calendar event fixtures (bu-s11n0s.4 AC B)

Every payload here is **synthetic**. Each one is shaped after the public Google Calendar API v3
Events resource reference (`eventType`, `outOfOfficeProperties`, `focusTimeProperties`,
`workingLocationProperties`). None was captured from a real account. The recorded-payload requirement was dropped by a 2026-11-22
owner decision (recorded capture is infeasible on a consumer account); this synthetic corpus is
the accepted fixture set, with its provenance disclosed. A captured payload would need its own sanitized file, its own
`provenance` entry naming where and how it was captured, and a review before it lands.

`manifest.json` lists every file once with its `sha256` and the typed fields the real normalizer
must produce. `tests/modules/test_calendar_typed_event_fixtures.py` checks that the list and
hashes match the files, that every file is labelled synthetic and holds no real identity or
provider URL, and that each file normalizes as declared.
`tests/integration/test_calendar_event_type_projection.py` projects the same corpus into
`calendar_events` on real PostgreSQL.

If you edit a fixture, update its hash in the manifest.
