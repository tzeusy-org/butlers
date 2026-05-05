## Why

Vertical C (bu-v1tt2) migrated all hex color literals to named CSS custom
properties in `frontend/src/index.css`:

- `--severity-low` / `--severity-medium` / `--severity-high` (health symptom
  severity bands)
- `--category-1` through `--category-8` (categorical label/group palette)
- `--permanence-*` tokens (memory tier permanence levels, covered by a separate
  exemption for chart-* tokens)

The `dashboard-domain-pages` spec was not updated during that migration. It
still carries raw `#RRGGBB` hex literals in three places:

1. **Measurements page** (line 20): `#3b82f6` and `#f43f5e` for the
   blood-pressure dual-line chart.
2. **Symptoms severity scenario** (lines 98-100): `#22c55e` (green) and
   `#ef4444` (red) for the severity progress bar.
3. **Contacts label color scenario** (line 173): the eight-entry categorical
   palette expressed as a bare comma-separated list of hex codes.

Leaving these hex literals in the spec creates silent drift: future implementers
reading the spec would hard-code color values, bypassing the token system and
dark-mode aware OKLCH ramps.

**Out of scope:** chart axis / line palette (`--chart-*` tokens) are
intentionally a separate axis per the design-language.md token exemption and
are NOT replaced in this change.

## What Changes

- **Modified capability**: `dashboard-domain-pages` — three in-spec hex
  references replaced with named token references.
  - Measurements: `#3b82f6` → `var(--category-1)`, `#f43f5e` → `var(--category-5)`.
  - Symptoms severity: `#22c55e` → `var(--severity-low)`,
    `#ef4444` → `var(--severity-high)`.
  - Contacts label palette: eight hex literals → `var(--category-1)` through
    `var(--category-8)`.

## Capabilities

### Modified Capabilities

- `dashboard-domain-pages`: three requirements updated to reference named CSS
  tokens instead of hex literals.

### New Capabilities

None. This is a spec-alignment delta only. No new code is required.

## Impact

- **Delta spec**: `openspec/changes/token-system-spec-sync/specs/dashboard-domain-pages/spec.md`
  — replaces the three stale hex-literal references.
- **No code changes.** The token migration in `frontend/src/index.css` already
  shipped via Vertical C (bu-v1tt2).
- **No database changes.**
- **No API changes.**

## Source References

- Non-Negotiable Rule 2: "The `Page` is a primitive." (`about/heart-and-soul/design-language.md`)
- `frontend/src/index.css` — `--severity-*`, `--category-1..8` token definitions (Vertical C)
- Epic bu-v1tt2 (Vertical C) — token system migration
