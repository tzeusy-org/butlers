# Tasks — calendar-ics-export-and-printable-agenda

One read-only backend endpoint + one pure-FE render mode. No DB migration, no new
MCP tool, no provider write. The export reuses the existing workspace projection;
the agenda renders over the existing workspace response.

## 1. ICS export endpoint (bu-8yi687)

- [x] 1.1 Add `GET /api/calendar/export/ics` streaming the workspace entries for
  a date range as a `text/calendar` VCALENDAR generated with the `icalendar`
  library; reuse the existing workspace read/projection and the
  `view`/`butlers`/`sources`/`status`/`source_type` filters
- [x] 1.2 Preserve the `BUTLER:` title prefix verbatim in the exported `SUMMARY`;
  emit DATE-valued DTSTART/DTEND for all-day entries and UTC instants otherwise
- [x] 1.3 Validate the range like the workspace read (end > start, ≤ 90 days,
  known status/source_type facets); read-only — no provider write, no LLM
- [x] 1.4 Register the export router in the dashboard app; add `icalendar` to
  project dependencies
- [x] 1.5 Tests: valid VCALENDAR/VEVENT parsed by the library, `BUTLER:` prefix
  preserved, read-only (no MCP client requested), empty range → empty calendar,
  inverted range → 400, missing range → 422

## 2. Printable agenda render mode (bu-8yi687, FE-only)

- [x] 2.1 Add a `CalendarAgendaView` component rendering the loaded workspace
  entries grouped by day in a print-friendly layout (no new data fetch)
- [x] 2.2 Add an "Agenda" toolbar action that opens the agenda overlay; a Print
  button triggers `window.print()` with `@media print` isolation
- [x] 2.3 Tests: day grouping + chronological order, `BUTLER:` prefix preserved,
  all-day labelling, Print/Close handlers, empty-state
