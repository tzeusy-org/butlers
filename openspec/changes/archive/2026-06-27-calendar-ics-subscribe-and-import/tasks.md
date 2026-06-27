# Tasks — calendar-ics-subscribe-and-import

Two read-only/read-write backend endpoints over the existing calendar workspace
projection. No DB migration, no new MCP tool (import reuses
`calendar_create_event`).

## 1. ICS subscribe feed (bu-t2zxj)

- [x] 1.1 Add `GET /api/calendar/subscribe.ics` re-rendering the current
  workspace entries on each fetch over a rolling `now − 30d … now + 60d` window,
  reusing the export's ICS serialization, filters, and `BUTLER:` prefix
- [x] 1.2 Serve `Content-Disposition: inline` + `Cache-Control: no-cache` so a
  calendar app treats it as a live subscription feed; no provider write, no LLM
- [x] 1.3 Tests: subscribe re-renders current entries as a valid VCALENDAR,
  inline disposition, unknown facet → 400

## 2. ICS import-with-dedup (bu-t2zxj)

- [x] 2.1 Extract the read-model `(origin_ref, starts_epoch)` /
  `(title, starts_epoch)` collapse keys into shared helpers
- [x] 2.2 Add `POST /api/calendar/import/ics` parsing an uploaded `.ics` and
  creating each VEVENT via `calendar_create_event`, deduped against existing
  workspace entries using the `(title, starts_epoch)` collapse key
- [x] 2.3 Skip events already present and collapse duplicates within the file;
  return `parsed` / `imported` / `skipped_duplicates`
- [x] 2.4 Tests: import creates non-duplicate events; re-importing the same
  `.ics` is a no-op (0 imported); duplicate-within-file collapsed; empty/invalid
  payload rejected
