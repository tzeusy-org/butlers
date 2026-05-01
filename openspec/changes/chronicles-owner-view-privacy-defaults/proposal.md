## Why

The `Map Render Privacy Contract` requirement at
`openspec/specs/dashboard-chronicles/spec.md` L144–169 was specced as if the
dashboard had multiple viewer classes — its `Sensitive episodes masked`
scenario assumes some sources arrive with `privacy=sensitive` and the
frontend masks the envelope and excludes coordinates from the map.

[Observed] In practice, only one source — `owntracks.points` — was being
classified `sensitive` at the adapter (`src/butlers/chronicler/adapters/owntracks.py`,
prior `privacy=Privacy.SENSITIVE` at L375 + L565). The dashboard has exactly
one viewer (the owner), so the masking machinery only ever ran against the
owner's own location data, rendering the Travel lane envelope as `"Travel:
N min"` with no detail and emptying the Map widget when only owntracks data
existed.

[Observed] The doctrine source `about/heart-and-soul/security.md` L168–185
("Sensitive Data Categories") explicitly states that location data
"is governed by the same trust model as all other data: the user owns the
instance and the database. The system does not apply differential privacy,
anonymization, or special-purpose encryption to any data category.
Connector-level controls (ingestion tier, retention periods, opt-in
activation) are the primary privacy mechanism." The render-time `sensitive`
masking in the dashboard spec was therefore misaligned with doctrine.

[Push-back, per /project-direction Rule 4] This change retroactively documents
a code change that has already shipped on `fix-chronicles-dashboard-ux`
(merged into `main` as commit `f05b7d6c`). The "no coding before signoff"
rule was violated because the existing spec was already misaligned with
doctrine and the user requested an immediate fix to a broken dashboard.
The corrective path is to refine the spec to match doctrine *and* add the
missing per-recipient toggle requirement so the render-time masking
machinery has a future trigger that is actually spec-grounded.

## What Changes

- **BREAKING (spec-only)**: `owntracks.points` adapter outputs default to
  `privacy=normal`. The render-time masking contract for `sensitive`
  episodes is unchanged — only the source classification moved.
- **Specs — refine existing requirement**: rewrite the `Map Render Privacy
  Contract` requirement so the `Sensitive episodes masked` scenario no longer
  implicitly assumes any specific source emits `sensitive` rows by default.
  Add an explicit cross-reference to the owner-view doctrine in
  `about/heart-and-soul/security.md` L168–185.
- **Specs — add new requirement**: `Per-Recipient Masking Toggle`. Codify a
  future requirement for an explicit viewer-context toggle (shared dashboard
  links, screenshot views, third-party access) that re-engages the
  render-time `sensitive` masking machinery for non-owner viewers. Not
  implemented in this change — only the requirement is specced. Implementation
  is a downstream change with its own beads work.
- **Already shipped on `main` (commit `f05b7d6c`)** — this OpenSpec change
  documents and codifies the following:
  - `OwnTracksPointAdapter` privacy default: `SENSITIVE → NORMAL`
  - Alembic backfill `core_086_backfill_owntracks_privacy_normal.py`
  - Test invariant flips in `tests/chronicler/test_privacy_defaults.py`
  - `roster/chronicler/AGENTS.md` privacy-defaults table

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-chronicles`: refines `Map Render Privacy Contract` to align
  with the owner-view doctrine, and adds a new `Per-Recipient Masking
  Toggle` requirement so the existing render-time masking machinery has a
  spec-grounded trigger.

## Impact

- **Specs**: `openspec/specs/dashboard-chronicles/spec.md` L144–169 (refined)
  + new requirement section.
- **Doctrine cross-check**: cites `about/heart-and-soul/security.md`
  L168–185 as the controlling doctrine for owner-view data classification.
- **Code (already merged on `main`)**:
  `src/butlers/chronicler/adapters/owntracks.py`,
  `alembic/versions/core/core_086_backfill_owntracks_privacy_normal.py`,
  `tests/chronicler/test_owntracks_adapter.py`,
  `tests/chronicler/test_privacy_defaults.py`,
  `roster/chronicler/AGENTS.md`.
- **Out of scope** (separate change + beads work): per-recipient masking
  toggle UI, viewer-context plumbing through the dashboard API, and the
  Gantt/Map render path that re-engages masking for non-owner viewers.
- **No data-migration risk** beyond the idempotent `core_086` backfill that
  has already shipped.
- **[Inferred]** Other adapters (`spotify.session_summary`, `home_assistant`,
  `google_calendar.completed`, etc.) are unaffected: per
  `roster/chronicler/AGENTS.md` adapter-defaults table, none of them
  default to `sensitive` after the prior `core_085` cleanup.
- **[Unknown]** Whether the per-recipient toggle should default to "mask
  for everyone except owner" or require explicit per-recipient configuration
  — design discussion deferred to the toggle implementation change.
