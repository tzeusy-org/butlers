## REMOVED Requirements

### Requirement: Tools & Scope API
**Reason**: Nothing reads `public.butler_tools`, so a tool marked not allowed stayed callable by every session; the grants and the audit entries that recorded them read as effective policy and were not.

**Migration**: None. The `public.butler_tools` table and its rows are kept untouched; registered tools appear in the MCP tab and the Management tab Tool surface panel.

## MODIFIED Requirements


### Requirement: Butler Detail Page — Dispatch Fold-In
The existing `/butlers/{name}` detail page SHALL fold in the `ButlersExpanded` design, with sections for fallback chain, system prompt, memory access, activity, and kill switch.

#### Scenario: Page structure post-fold-in
- **WHEN** a user navigates to `/butlers/{name}`
- **THEN** the page renders the existing tab archetype plus, on the "Configuration" or equivalently-named tab, sections in this order:
  - **§1 Identity & routing** — fallback chain (primary + ordered fallbacks; `+ add fallback` link), schedule, `$/day ceiling`, approvals policy, timeout, concurrency.
  - **§2 System prompt** — serif prompt body, mono caption (`tokens · NNN · last edit · <actor>`), links `history · N versions →` and `diff vs vN-1 →`.
  - **§3 Memory access** — three tiles for short / mid / long term, each with read/write badges.
  - **§4 Activity** — 24h stripe-chart (sessions per hour).
  - **§5 Kill switch** — `kill switch · 30s grace →` link.
- **AND** the page SHALL NOT present per-tool grants or toggles, because nothing enforces them; registered tools appear in the MCP tab and the Tool surface panel.

#### Scenario: Kill switch with grace
- **WHEN** a user clicks the kill switch link
- **THEN** a confirmation modal appears showing the grace seconds and the butler name
- **AND** on confirm, `POST /api/butlers/{name}/kill {grace_seconds: 30}` is called
- **AND** `audit.append("butler.kill", target=butler_name, note=f"grace={grace_seconds}s")` is invoked
- **AND** the butler initiates shutdown after the grace window.
