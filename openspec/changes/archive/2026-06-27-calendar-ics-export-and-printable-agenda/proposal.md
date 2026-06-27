## Why

The calendar-workspace UX roadmap (epic `bu-l3k0zg`) treats owner sovereignty /
anti-lock-in as a first-class concern: the user's calendar data should be
portable out of Butlers, not trapped inside the dashboard. Today there is no way
to take the workspace calendar elsewhere — no machine-readable export and no
print-friendly rendering. Bead `bu-8yi687` adds a low-cost, read-only data
portability surface: an ICS export endpoint and a printable agenda render mode.

This is deliberately read-only: no provider write, no LLM session, no new table.
The endpoint adds an HTTP contract (a new dashboard API route) so it is specified
here before implementation (the project is spec-driven). The printable agenda is
pure frontend over the existing workspace response and is spec-exempt under the
single-owner craft-and-care override, but is noted here for context.

## What Changes

- **NEW `GET /api/calendar/export/ics`** — streams the calendar workspace
  entries for a date range as a valid `text/calendar` (ICS / VCALENDAR) file,
  generated with the `icalendar` library. It reuses the existing workspace
  read/projection and the same `view` / `butlers` / `sources` / `status` /
  `source_type` filters as `GET /api/calendar/workspace`, so the export matches
  what the user sees. The `BUTLER:` title prefix on butler-authored events is
  preserved verbatim in the exported `SUMMARY`. Read-only: no provider write, no
  LLM, no migration. (Bead `bu-8yi687`.)
- **Printable agenda render mode (FE-only)** — a read-only, print-friendly
  agenda view over the entries already loaded by the workspace read, grouped by
  day. Pure frontend over the existing contract (no new fetch); spec-exempt, not
  specified below.
- **Out of scope (follow-up)** — ICS subscribe / `webcal` live feed and
  `.ics` import-with-dedup are intentionally NOT built here; the export is a
  one-shot download only.

## Impact

- Affected specs: `dashboard-api` (one new read-only requirement).
- Affected code: `src/butlers/api/routers/calendar_workspace.py` (new
  `export_router`), `src/butlers/api/app.py` (router registration), `icalendar`
  added as a dependency. Frontend: a new `CalendarAgendaView` component + an
  "Agenda" toolbar action on the calendar workspace page.
- No database migration, no new MCP tool, no provider write.
