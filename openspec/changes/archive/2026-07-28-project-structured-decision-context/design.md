## Context

`compute_decision_digest()` is the sole dashboard-readable boundary for the
host-generated Beads export. It filters open, non-epic `decision`-labeled
records, computes escalation information, and fails closed when the export is
missing, stale, or unreadable. Today it intentionally reduces every selected
record to summary fields, even though the same JSONL records contain the
decision description, `metadata.decision.options`,
`metadata.decision.default`, and native `due_at`.

The completed Decisions-lane change did not sync its delta specs into canonical
API/Decisions specs and explicitly excluded this detail payload. This successor
therefore records the current read-only baseline while extending only the
existing JSONL-to-digest-to-API-to-page path.

## Goals / Non-Goals

**Goals:**

- Project trusted decision context through the existing digest without changing
  its classification, ordering, escalation, or export freshness behavior.
- Preserve option order and require a default to match a projected option.
- Make malformed/missing structured source data visible per record rather than
  rendering a calm empty-options state.
- Let a URL select an existing detail while leaving unknown deep links harmless.
- Keep the canonical OpenSpec API and Decisions contracts synchronized with the
  delivered behavior.

**Non-Goals:**

- Live Dolt/host access, Beads reads or writes from dashboard request paths,
  linter execution in request paths, default application, approval/close
  controls, Telegram controls, or tracker-data migration.
- Sidebar/badge changes owned by `bu-27dxl.7`, or later mutation/Telegram work
  owned by `bu-ckkpz.3`.

## Decisions

### Preserve the existing exported-JSONL boundary

The digest continues to parse only the mounted export file. Adding a direct
`bd` client, a Dolt bridge, or a database copy would cross an intentional
process/network trust boundary and is outside this UI projection.

### Validate details as a separate per-record state

Whole-export failures retain `meta.decisions_available=false` and an empty
list exactly as today. For a readable export, each record carries nullable
description/options/default/due_at values plus
`structured_details_available` and a named unavailable reason. Missing
metadata is distinct from malformed metadata; invalid options/defaults or an
invalid native due date never become an empty list of choices. Valid values may
still be shown when another detail is unavailable, but the page names the
degradation instead of implying the detail bundle is complete.

### Keep source-field meaning and ordering intact

The parser accepts only a non-empty ordered list of distinct non-blank option
strings and a non-blank default that exactly matches one option. It does not
sort, normalize, infer, or apply a default. `due_at` is parsed as the Beads
native timestamp, not regenerated from prose. Description is projected only
when it is a string; it is never synthesized.

### Make selection URL-backed but defensive

`?bead=<id>` selects and expands only a currently present digest record. A
missing/unknown id leaves the regular list unselected; it does not create a
placeholder or hide rows. Click and j/k selection update the same query key so
the selected state remains deep-linkable, while existing keyboard triage stays
operable.

## Risks / Trade-offs

- [A structurally invalid export record looks like a valid empty decision] →
  validate every governed field and expose a named unavailable reason.
- [A readable but stale/unreadable snapshot is treated as a per-record error]
  → retain the existing global degraded envelope before constructing rows.
- [Deep-link synchronization disrupts keyboard triage] → keep the existing
  `useListTriage` owner and route all selection sources through one handler.
- [A future mutation feature mistakes display data for authority] → display
  read-only copy only and add no API endpoint, control, callback, or apply
  path.

## Migration Plan

1. Add the successor delta specs and sync canonical API/Decisions specs before
   implementation.
2. Add failing digest/API/page tests for valid, malformed, degraded, known
   deep-link, and unknown deep-link states.
3. Implement the narrow projection and UI rendering, then run focused and
   final backend/frontend/spec gates.

Rollback is a normal code rollback: no persistent data, schema, or Beads state
changes are introduced. The pre-existing summary/degraded behavior remains the
fallback shape.
