## ADDED Requirements

### Requirement: Spine Sort Control And Recency Resolution
The spine's `?sort=` control SHALL offer exactly two modes, `severity` (default) and `alpha`; the
`recency` mode is REMOVED, not renamed or reskinned, because its backing `lastTouchOrder` value was
never a real activity signal — it was a fixed per-family constant (`800` for every `user` row, a raw
array index or another fixed constant for `system` and `cli` rows), not a last-used or last-touched
timestamp. Sort mode reorders rows WITHIN each of the five spine groups defined by the
Passport-Book Information Architecture requirement; it does not reorder the groups themselves, and
it introduces no new recency, activity, or last-used data of any kind.

#### Scenario: Recency control is removed, not canonicalized
- **WHEN** the SortPicker renders
- **THEN** it offers only `severity` and `alpha` as selectable modes; no `recency` option is
  rendered
- **AND** no field resembling `lastTouchOrder` (or any other synthetic last-touch/activity proxy)
  is read by the sort implementation — canonicalizing the misleading label while keeping the same
  synthetic ordering was considered and rejected, because relabeling it would still present
  array-position data as if it meant something about time

#### Scenario: `alpha` sort changes the within-group tie-break, never the severity ordering
- **WHEN** `?sort=alpha` is active
- **THEN** severity rank remains the primary within-group ordering key, unconditionally — this
  carries forward the prior spec's invariant that `needs-hand` "is always pinned and
  severity-sorted regardless of the `?sort=` mode," now extended uniformly to every group, per the
  "Spine grouping order" scenario
- **AND** `alpha` mode changes only the tie-break applied AFTER severity: rows sharing a severity
  rank are ordered by label, case-insensitive, then focus key — skipping the `severity` mode's
  family-rank tie-break, so `alpha` mode is the one that visibly ignores family precedence in
  favor of pure alphabetical browsing within same-severity rows
- **AND** the five groups themselves keep their fixed `needs-hand`, `in-progress`, `stale`,
  `ready`, `not-set` order regardless of `?sort=` mode — `alpha` and `severity` are both
  within-group tie-break controls, never a mechanism for promoting a healthy row above a broken
  one or vice versa

#### Scenario: Legacy `?sort=recency` URL falls back to `severity`
- **WHEN** the page loads with a `?sort=recency` query parameter (an old bookmark or shared link
  predating this delta)
- **THEN** the page renders as if `?sort=severity` (the default) were given — no error, no blank
  sort state, and no rows dropped
- **AND** the page does NOT rewrite the URL to drop or replace the stale `sort=recency` value; the
  fallback is applied only to the rendered sort behavior, matching this spec's existing pattern for
  a stale legacy `s:` focus-key prefix, which is resolved in-memory without a forced URL rewrite
- **AND** any other unrecognized `?sort=` value falls back to `severity` by the same rule

#### Scenario: Real-inventory comparison method is content-blind and ephemeral
- **WHEN** this specification's group-mapping and ordering claims are verified against a live,
  non-degraded `GET /api/secrets/inventory` response (`meta.sources_degraded` empty) rather than
  mock fixtures
- **THEN** the comparison method's per-row record retains only four fields: `family` (`user` /
  `system` / `cli`), `state` (the `CredentialState` value), `publishedLabel` (the row's already-
  public display label — the provider's display label for `user` rows, the raw `key` for `system`
  and `cli` rows), and `ephemeralRowToken` (a value generated fresh for that single comparison run,
  used only to correlate an expected row with an actual row within that run)
- **AND** the record SHALL NOT retain a fingerprint, probe message, audit note, scope or
  capability data, timestamp, or any other field from the inventory response
- **AND** no comparison record is written to disk, a database, a cache, or a log; it exists only in
  the verifying process's memory for the duration of the single comparison run and is discarded
  when the run ends
- **AND** the method is run only against a non-degraded response, so an absent family is never
  misattributed to a state-mapping defect in this spec

## MODIFIED Requirements

### Requirement: Passport-Book Information Architecture
The dashboard SHALL replace the existing 3-tab `/secrets` shell with a single passport-book route at `/secrets` consisting of a left **spine** index and a right **page** editorial body. The page SHALL NOT render any of the deprecated patterns: tab strip, `SecretsTable` (`••••••••` row with eye-toggle as the primary affordance), six bespoke provider Setup cards, separate `CLIAuthCard`, embedded `EntityPicker` in the page header, or prototype tweaks chrome.

The page is rendered in the binding **Dispatch** design language specified in `openspec/specs/dashboard-design-language/spec.md`. Every visual decision in the spec MUST preserve that language — typography (Inter Tight 500 display, Source Serif 4 voice, JetBrains Mono code/eyebrow), spacing (4px multiples; 48px × 56px page padding; 56px section gutter; `1.4fr 1fr` two-column editorial grid), colour (oklch tokens; `--red/--amber/--green` only when state demands), motion (briefing cross-fade, sidebar chevron, theme fade, tooltip; nothing else).

#### Scenario: Single-route surface
- **WHEN** a user navigates to `/secrets`
- **THEN** the page renders a two-column layout: left spine (sticky, scrollable index), right page (editorial body for the focused credential)
- **AND** there is no tab strip, no `<Tabs>` shell, and no horizontal navigation between System / User / CLI families
- **AND** the page header contains the title "Secrets", a mono eyebrow, and the identity chip (when more than one identity is in scope)

#### Scenario: Spine grouping order
- **WHEN** the spine renders
- **THEN** rows are grouped in this exact order: `needs-hand` (pinned, act-now), `in-progress` (transient), `stale` (quiet, unverified — bu-976n0), `ready` (healthy), `not-set` (never configured) — superseding the prior family-first `needs-hand, stale, CLI runtimes, System, User` order, and superseding the narrower user-row-only ordering question `bu-5r9hy` left open (`bu-5r9hy` is closed; this requirement is its replacement, per `bu-k87os`)
- **AND** every credential state maps to exactly one of the five groups per the "Every CredentialState maps to exactly one group" scenario below; no state is a member of more than one group and no state is unmapped
- **AND** severity rank (the existing state-catalog rank, ascending) is always the primary within-group ordering key, UNCONDITIONALLY — carrying forward and generalizing the prior spec's invariant that `needs-hand` "is always pinned and severity-sorted regardless of the `?sort=` mode" to every group, not `needs-hand` alone; this differentiates rows inside `needs-hand` (e.g. `expired`/`revoked`/`failed` before `scope_mismatch`/`expiring`/`authorization_needed`) and inside `in-progress` (`rotating` before `checking`); it is a no-op inside `stale`/`ready`/`not-set`, each of which has exactly one member state
- **AND** the `?sort=` mode (`severity` or `alpha`; see the Spine Sort Control And Recency Resolution requirement) selects only the tie-break applied AFTER severity: `severity` mode breaks ties by (2) family rank in the fixed order `cli` < `system` < `user` (matching the existing row-construction and group-render order, unchanged to minimize churn), then (3) label case-insensitive, then (4) focus key; `alpha` mode breaks ties by (2) label case-insensitive, then (3) focus key, skipping family rank — either way the chain is a total order, so two rows are never left in an undefined relative position
- **AND** a group MAY contain rows from more than one family in the same list (mixed families), rendered with the shared one-row-template per the "One Row Template Across All Three Families" requirement — the family boundary is no longer a group boundary
- **AND** `checking` and `rotating` rows render inside `in-progress` as transient, quiet indicators (see the "Transient states render quietly in `in-progress`" scenario below) rather than inside `needs-hand`; an in-flight rotation the owner initiated is not an act-now failure
- **AND** group eyebrows render in mono 10px uppercase with tracking 0.14em

#### Scenario: Every CredentialState maps to exactly one group
- **WHEN** the five spine groups are populated
- **THEN** the mapping is exactly:
  - `needs-hand`: `expired`, `revoked`, `scope_mismatch`, `expiring`, `authorization_needed`, `failed`
  - `in-progress`: `checking`, `rotating`
  - `stale`: `warn`
  - `ready`: `ok`
  - `not-set`: `never_set`
- **AND** this list is exhaustive over the full `CredentialState` union — every state named above appears in exactly one group, and no group contains a state not named above
- **AND** `rotating` moves from the prior implementation's `needs-hand` membership to `in-progress`: an in-flight rotation the owner initiated is a transient operation in progress, not a broken credential demanding attention
- **AND** `authorization_needed`, already treated as a `needs-hand` state by the prior implementation, is named explicitly here for the first time, closing a gap between the prior spec text (which never listed it) and the prior code (which already classified it as `needs-hand`)
- **AND** a `warn` credential is an unknown, not a failure — carrying forward the prior spec's stated rationale — and per the exactly-one-group rule above it MUST NOT appear in `needs-hand`, or in any group other than `stale`

#### Scenario: Empty `needs-hand` group
- **WHEN** every credential is healthy (no row has state in {`expired`, `revoked`, `scope_mismatch`, `expiring`, `authorization_needed`, `failed`})
- **THEN** the spine omits the `needs-hand` group entirely (no empty-state stub) and the page renders **zero red and zero amber pixels**, even if `in-progress`- or `stale`-state rows are present in their own quiet groups

#### Scenario: Imminent expiry uses the canonical state
- **WHEN** a set credential expires within its configured imminent-expiry lead time but has not yet expired
- **AND** its most recent probe succeeded, or no probe has run
- **THEN** the inventory returns state `expiring` rather than `warn` in the no-probe case
- **AND** the passport treats `expiring` as a `needs-hand` state
- **AND** `expiring` is the sole imminent-expiry state name in both contracts

#### Scenario: Empty `stale` group
- **WHEN** no credential is in the `warn` state
- **THEN** the spine omits the `stale` group entirely (no empty-state stub)

#### Scenario: Empty `in-progress`, `ready`, and `not-set` groups
- **WHEN** no credential is in a state belonging to `in-progress` (`checking`, `rotating`), or none is `ok`, or none is `never_set`
- **THEN** the spine omits that group entirely (no empty-state stub), the same rule the `needs-hand` and `stale` groups already follow
- **AND** omitting `ready` or `not-set` never affects the zero-red/zero-amber pixel rule, since neither group ever renders alarm color

#### Scenario: `warn` state is quiet, not alarm-colored
- **WHEN** a credential is in the `warn` state (set-but-never-probed, or a stale prior probe)
- **THEN** its spine row renders with a `--dim` state dot and no left-edge sliver — never `--amber` and never a sliver
- **AND** its subline reads `unverified` (or `verified <when>` if a prior verification timestamp exists)
- **AND** it does NOT count toward `meta.failing_count` or the "N need hand" KPI caption; it counts toward `meta.unverified_count` and a separate quiet KPI caption instead
- **AND** the Passport consumes the backend's aggregate and per-family count metadata for headline urgency and KPI captions, rather than recomputing those state counts from adapted credential rows

#### Scenario: Transient states render quietly in `in-progress`
- **WHEN** a credential is `checking` or `rotating`
- **THEN** its spine row renders with a `--dim` state dot and no left-edge sliver — the same quiet treatment `checking` already had, now extended to `rotating` for consistency (the prior implementation rendered `rotating` with an amber tone and no sliver, an inconsistent middle state this scenario resolves)
- **AND** neither state counts toward `meta.failing_count`; both are transient, self-clearing states that resolve to a terminal state (`ok`, `expired`, `revoked`, etc.) on their own without further owner action once the in-flight operation completes

#### Scenario: Search and keyboard order follow the five-group order
- **WHEN** the owner types into the spine search field, or presses ArrowUp/ArrowDown while a row has focus
- **THEN** the filtered/roving row order is the flattened five-group order (`needs-hand`, `in-progress`, `stale`, `ready`, `not-set`, each internally ordered per the "Spine grouping order" scenario's tie-break chain) — never the pre-search or pre-navigation family order
- **AND** a group with zero rows remaining after the search filter is applied is omitted from both the visual list and the roving tab order, per the empty-group scenarios above

#### Scenario: Identity switch re-projects only User rows within their group
- **WHEN** the owner switches identity per the Projection-Lens Identity Switcher requirement
- **THEN** only `user`-family rows inside each of the five groups re-project to the selected identity's credentials
- **AND** `cli`-family and `system`-family rows inside the same groups are unaffected — those families are not identity-scoped, per the existing Projection-Lens Identity Switcher requirement
- **AND** a group's overall position and eyebrow count updates only from the change in its User-family membership, never by reordering `cli`/`system` rows already in that group

#### Scenario: Row family is accessible without a family-labeled group heading
- **WHEN** a spine row renders inside any of the five state groups
- **THEN** the row's accessible name (e.g. an `aria-label` or an `sr-only` qualifier preceding the visible label) SHALL include the credential's family as a qualifier distinguishable from the label text — for example "CLI · claude" or "System · BUTLER_TELEGRAM_TOKEN" — so a screen-reader user can still determine family membership per row
- **AND** this qualifier is required because the prior family-labeled group headings (`CLI runtimes`, `System`, `User`) no longer exist under this IA and family is otherwise conveyed only by sighted-only visual cues (mono label styling, provider glyph)

#### Scenario: Degraded inventory never fabricates group membership
- **WHEN** `GET /api/secrets/inventory` reports one or more `meta.sources_degraded` families
- **THEN** the five spine groups are populated only from rows the response actually returned — a degraded, absent family contributes zero rows to every group, never a synthetic "unknown" row
- **AND** a group that is empty because a family is degraded renders identically to a group that is empty because every credential in it is genuinely absent (per the empty-group scenarios above) — the existing top-level degraded indicator (unchanged by this delta) is the sole signal that the empty state may be due to degradation rather than health
