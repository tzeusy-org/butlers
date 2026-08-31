## Context

The shipped Chronicles map constructs a MapLibre style from four CARTO light
or dark label-free raster templates, emits OpenStreetMap and CARTO attribution,
and optionally appends a trimmed, URL-encoded browser key. The canonical spec
still contains an OpenStreetMap-only, no-token requirement. See `proposal.md`
for the resulting contract drift.

The archived `2026-04-26-add-dashboard-chronicles` delta is immutable evidence.
Replacing its clauses directly in the baseline causes
`check_archived_requirements_landed.py` to report that its OpenStreetMap-only
prose and tile-source scenario no longer landed.

## Goals / Non-Goals

**Goals:**

- Reconcile the canonical contract through the OpenSpec lifecycle while
  preserving the archived delivery ledger.
- Preserve all unrelated scenario names and clauses in the two touched
  baseline requirements.
- State the browser-visible, provider-restricted key boundary without treating
  the value as a backend secret.

**Non-Goals:**

- No frontend runtime, Compose, dependency, credential, BWS, deployment, or
  runtime changes.
- No key values or environment-specific domains in tracked artifacts.
- No rewrite of any pre-existing file under `openspec/changes/archive/`.

## Decisions

### Supersede the stale requirement by title

The delta removes `MapLibre Dependency Justification` and adds
`MapLibre and CARTO Raster Basemap Contract` with the preserved `License and
tile source` and `Bundle measurement` scenario names plus explicit key edge
cases. Once archived, the recorded removal tells the archived-requirements
guard that the old contract was intentionally superseded, while the added
successor remains independently accountable in the canonical spec.

A `MODIFIED` block alone was rejected because the archive ledger would
correctly report the old OpenStreetMap-only clauses as missing. Editing the
archived snapshot or ratchet was rejected because it would rewrite historical
evidence. A two-change remove-and-restore sequence was unnecessary because no
scenario heading needs renaming; a distinct successor title can complete the
supersession atomically.

### Modify the style-load requirement as a complete block

`Map Widget Style-Load Resilience` contains one provider-specific OSM clause
and one unrelated update scenario. The delta copies the complete baseline
block, changes only the stale provider wording, preserves both scenario names,
and backfills traceability metadata. This avoids whole-requirement replacement
loss during archive.

### Keep the key boundary explicit and content-blind

The contract names only the public Vite configuration behavior: trim a
configured non-blank value, URL-encode it, append it to every theme template,
and leave URLs unchanged when configuration is absent or blank. Because the
browser receives the value, provider-side domain restriction is mandatory;
neither the change nor its validation needs to read a credential value.

## Risks / Trade-offs

- [Another active delta modifies either touched requirement before archive] →
  Re-run the exact requirement-heading search immediately before archive and
  stop or rebuild from the refreshed baseline if another change appears.
- [Whole-block archive drops unrelated resilience behavior] → Keep every
  baseline scenario name and clause in the modified block and run the
  overwrite guard before and after archive.
- [The successor requirement moves to the end of the capability spec] → Accept
  the OpenSpec append behavior; requirement names and traceability, not file
  position, carry the contract.
- [A test fixture accidentally resembles a real key] → Use only synthetic
  placeholder values already present in focused tests and keep PR metadata
  content-blind.

## Migration Plan

1. Validate the complete active change and verify no other active delta names
   either touched requirement.
2. Confirm the existing focused Chronicles tests prove light/dark templates,
   URL encoding, attribution, and blank-key behavior.
3. Apply the completed tasks, then archive with `openspec archive` so the tool
   updates the canonical spec and records the supersession.
4. Run strict OpenSpec validation, the archived-requirements guard, and the
   spec-overwrite guard against the archived result.

Rollback is a normal revert of the documentation commit; no runtime or data
rollback exists because this change performs no operational mutation.
