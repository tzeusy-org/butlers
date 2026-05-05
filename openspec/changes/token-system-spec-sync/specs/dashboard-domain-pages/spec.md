# Dashboard Domain Pages — Delta (token-system-spec-sync)

This delta replaces raw hex color literals in the `dashboard-domain-pages` spec
with the named CSS custom properties that landed in `frontend/src/index.css`
as part of Vertical C (bu-v1tt2). No code changes are required; the token
migration already shipped.

Three requirements are affected. All other requirements are unchanged.

---

## MODIFIED Requirements

### Requirement: Health measurements page with trend charting

The chart for `blood_pressure` MUST render two lines: systolic using
`var(--category-1)` (blue) and diastolic using `var(--category-5)` (rose). For
all other measurement types, the chart MUST render a single line using
`var(--category-1)` (blue) plotting the primary numeric value.

All other sub-requirements for this requirement remain unchanged.

#### Scenario: Blood pressure dual-line chart

- **WHEN** the user selects `blood_pressure` as the measurement type
- **AND** there are measurements with `value` containing `systolic` and
  `diastolic` keys
- **THEN** the chart MUST render two lines: systolic colored with
  `var(--category-1)` (blue) and diastolic colored with `var(--category-5)` (rose)
- **AND** the chart tooltip MUST label them "Systolic" and "Diastolic"

---

### Requirement: Symptoms page with severity visualization

Severity visualization MUST use a 16px-wide progress bar colored with named
CSS tokens: `var(--severity-low)` (green) for severity 1–3, `var(--severity-medium)` (amber)
for 4–6, and `var(--severity-high)` (red) for 7–10.

#### Scenario: Severity color mapping

- **WHEN** a symptom has severity 2
- **THEN** the severity bar MUST be colored `var(--severity-low)` at 20% width
- **WHEN** a symptom has severity 8
- **THEN** the severity bar MUST be colored `var(--severity-high)` at 80% width

---

### Requirement: Contacts page with search, label filtering, and Google sync

Label badge colors MUST be deterministically derived from a hash of the label
name using the eight-step categorical token palette when no explicit `color` is
set on the label.

#### Scenario: Label color determinism

- **WHEN** a label named "family" has no explicit `color` set
- **THEN** its badge color MUST be deterministically derived from a hash of
  "family" using the categorical palette:
  `var(--category-1)`, `var(--category-2)`, `var(--category-3)`,
  `var(--category-4)`, `var(--category-5)`, `var(--category-6)`,
  `var(--category-7)`, `var(--category-8)`
- **AND** the same label MUST always render with the same color

---

## Source References

- `frontend/src/index.css` — `--severity-low`, `--severity-medium`,
  `--severity-high` token definitions (lines 67–69); `--category-1` through
  `--category-8` definitions (lines 78–85). Both sets are also aliased into
  Tailwind via `--color-severity-*` and `--color-category-*` (lines 263–281).
- Epic bu-v1tt2 (Vertical C) — token system migration that introduced the named
  CSS tokens; this spec change closes the remaining spec-code drift.
- `about/heart-and-soul/design-language.md` — token exemption for `--chart-*`
  palette (chart axis/line tokens are a separate axis; not replaced here).
