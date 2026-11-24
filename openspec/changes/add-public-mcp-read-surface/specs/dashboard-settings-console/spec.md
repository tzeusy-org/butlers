## MODIFIED Requirements

### Requirement: Settings Console Page
The dashboard SHALL have a top-level page at `/settings` rendered in the Dispatch design language: the system-configuration root, a header KPI strip and an `AttentionStrip` of items demanding human attention, followed by a panel grid of summary cards, one per system sub-surface. The console is system-side only; per-user preferences and credentials are not surfaced here.

ID: REQ-dashboard-settings-console-002
Source: openspec/specs/dashboard-settings-console/spec.md (Settings Console Page); RFC 0039 D16 (Public MCP sub-surface added to the panel grid)
Scope: v1-mandatory

#### Scenario: Console page layout
- **WHEN** a user navigates to `/settings`
- **THEN** the page renders, in vertical order:
  - **Page header**: title "Settings", mono eyebrow "system · console", clock (mono, `HH:MM` 24h, tabular nums).
  - **KPI strip**: four cells from `GET /api/settings/console` `header_counts` — Active Butlers, Spend MTD (USD), Open Approvals, and Models OK (verified / total). Open Approvals renders in the red state color when above zero; a `null` count renders as an em-dash, never a `0`; each cell shows a skeleton while loading.
  - **AttentionStrip**: a rule-separated list of `{id, tone: red|amber, kind, text, action_route}` items, initially the capped `attention[]` view from `GET /api/settings/console`. Each row carries the attention tint and rail in its `tone` color (see `dashboard-design-language`, Requirement: State Color Discipline), a tone indicator, the item text, and a `Review →` action that navigates to `action_route`.
  - **Panel grid**: one summary panel per sub-surface — Models (`/settings/models`), Spend (`/spend`), Approvals (`/approvals`), Permissions (`/settings/permissions`), and Secrets (`/secrets`). Activating a panel navigates to its route. Each panel with a live summary fetches it independently; a slow or failing fetch in one MUST NOT block the others.
- **AND** the panel grid also holds a Public MCP panel (`/settings/public-mcp`) after Secrets, which behaves like every other panel: activating it navigates to its route, and its summary fetch is independent of the others.
- **AND** the page uses only the fonts and tokens defined by `dashboard-design-language`; it introduces no new tokens.
- **AND** the page contains no card chrome, no drop shadows, no gradients.

#### Scenario: Inline attention overflow
- **WHEN** `attention_truncated_count > 0`
- **THEN** the strip renders an inline native control labelled `"...N more →"` that expands the omitted items from `attention_all[]` in the same strip
- **AND** the control exposes its state with `aria-expanded`, supports keyboard activation, and collapses the same local list without navigation
- **AND** the control does NOT navigate to `/audit-log` or any other route.

#### Scenario: Empty attention strip
- **WHEN** `attention_all[]` is empty
- **THEN** the strip section renders a single serif-italic line "Everything is in hand." and no rows.

#### Scenario: Panel summary load failure
- **WHEN** one panel's summary fetch fails
- **THEN** the panel renders a mono caption "Failed to load." with a `Retry →` link
- **AND** the other panels render normally.

#### Scenario: Public MCP panel summary
- **WHEN** the console renders the Public MCP panel
- **THEN** the panel summarizes how many of the seven domains are disabled and whether `all` is disabled, and activating it navigates to `/settings/public-mcp`

## ADDED Requirements

### Requirement: Public MCP Surface Panel With Domain Kill Switches
The dashboard SHALL have a page at `/settings/public-mcp`, registered as a Settings sub-surface and reached from the Public MCP panel of the Settings console. It SHALL list the `all` switch and each domain (`knowledge`, `relationship`, `time`, `travel`, `finance`, `health`, `proposals`). For each it SHALL show the state (`enabled` or `disabled`) and the time and actor of the last change. It SHALL let an owner-authenticated session disable or re-enable each one through a dashboard-API owner route that writes `mcp_surface.controls`. The page SHALL state that a change reaches the public server through replication within 15 minutes, while the Relationship proposal importer honours it at its next pass, and SHALL name the break-glass ConfigMap and scale-to-zero paths for immediate effect. Disabling SHALL take one action with no unlock step. While the control state loads, each row SHALL show a skeleton and no switch SHALL be actionable. A failed write SHALL leave the displayed state unchanged and show the error.

ID: REQ-dashboard-settings-console-001
Source: RFC 0039 D16 (owner switch on the Settings page); owner decision OD3 (kill switches for incident response); craft-and-care/review-and-documentation.md Spec discipline
Scope: v1-mandatory

#### Scenario: Owner disables finance from Settings
- **WHEN** an owner-authenticated session disables `finance` on the page
- **THEN** the `finance` control row SHALL become disabled with the owner as actor, and the page SHALL show `disabled` with the change time

#### Scenario: Unauthenticated write is refused
- **WHEN** the owner route is called without an owner session
- **THEN** the response SHALL be 401 and no control row SHALL change

#### Scenario: The page does not overstate effect
- **WHEN** a switch has just been changed
- **THEN** the page SHALL say that it takes effect within 15 minutes and SHALL point to the break-glass path, rather than claim immediate effect

#### Scenario: Loading state is not actionable
- **WHEN** the control state is still loading
- **THEN** each row SHALL render a skeleton and no switch SHALL accept activation

#### Scenario: Write failure keeps the truthful state
- **WHEN** the control write fails
- **THEN** the page SHALL keep showing the previous state and an error message, and SHALL offer retry

#### Scenario: Controls are accessible
- **WHEN** the owner navigates the page by keyboard
- **THEN** each switch SHALL be reachable, SHALL announce its domain and state to assistive technology, and SHALL not rely on color alone

## Source References

- Non-Negotiable Rule 1 (owner sovereignty: the owner holds the kill switches), `about/heart-and-soul/vision.md`.
- RFC 0039 (`about/legends-and-lore/rfcs/0039-public-mcp-read-surface.md`), D16.
- `openspec/specs/dashboard-design-language/spec.md`, interaction affordances and accessibility.
- `openspec/specs/dashboard-settings-console/spec.md`, Settings Console Page panel grid.
