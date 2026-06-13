## ADDED Requirements

### Requirement: Butler Detail Page — Dispatch Fold-In
The existing `/butlers/{name}` detail page SHALL fold in the `ButlersExpanded` design from `pr/overview/settings-refactor/settings-expanded.jsx`, with sections for fallback chain, system prompt, tools, memory access, activity, and kill switch.

#### Scenario: Page structure post-fold-in
- **WHEN** a user navigates to `/butlers/{name}`
- **THEN** the page renders the existing tab archetype plus, on the "Configuration" or equivalently-named tab, sections in this order:
  - **§1 Identity & routing** — fallback chain (primary + ordered fallbacks; `+ add fallback` link), schedule, `$/day ceiling`, approvals policy, timeout, concurrency.
  - **§2 System prompt** — serif prompt body, mono caption (`tokens · NNN · last edit · <actor>`), links `history · N versions →` and `diff vs vN-1 →`.
  - **§3 Tools & integrations** — table of `tool · description · scope · on` rows with toggles.
  - **§4 Memory access** — three tiles for short / mid / long term, each with read/write badges.
  - **§5 Activity** — 24h stripe-chart (sessions per hour).
  - **§6 Kill switch** — `kill switch · 30s grace →` link.

#### Scenario: Kill switch with grace
- **WHEN** a user clicks the kill switch link
- **THEN** a confirmation modal appears showing the grace seconds and the butler name
- **AND** on confirm, `POST /api/butlers/{name}/kill {grace_seconds: 30}` is called
- **AND** `audit.append("butler.kill", target=butler_name, note=f"grace={grace_seconds}s")` is invoked
- **AND** the butler initiates shutdown after the grace window.

### Requirement: System Prompt Versioning API
The dashboard SHALL expose CRUD over a butler's system prompt with version history.

#### Scenario: Read current prompt
- **WHEN** `GET /api/butlers/{name}/prompt` is called
- **THEN** the response is `ApiResponse[PromptVersion]` with `prompt: str`, `version: int`, `updated_at`, `updated_by`.

#### Scenario: Update prompt snapshots history
- **WHEN** `PUT /api/butlers/{name}/prompt {prompt: str}` is called
- **THEN** the current row is inserted into `public.system_prompt_history` (the snapshot), then the new prompt is stored as the current version with `version = old.version + 1`
- **AND** `audit.append("butler.prompt", target=butler_name, note=f"v{new_version}")` is invoked.

#### Scenario: Prompt history list
- **WHEN** `GET /api/butlers/{name}/prompt/history?limit=20` is called
- **THEN** the response is `PaginatedResponse[PromptVersion]` ordered `version DESC`, defaulting to the most recent 20 versions.

### Requirement: Tools & Scope API
The dashboard SHALL expose per-butler tool grants and scopes.

#### Scenario: Read tools
- **WHEN** `GET /api/butlers/{name}/tools` is called
- **THEN** the response is `ApiResponse[ButlerTool[]]` with `name`, `description`, `allowed: bool`, `scope: str | null`.

#### Scenario: Update a tool grant
- **WHEN** `PUT /api/butlers/{name}/tools/{tool} {allowed: bool, scope?: str}` is called
- **THEN** the grant is updated atomically
- **AND** `audit.append("butler.tool", target=f"{name}.{tool}", note=f"allowed={allowed}")` is invoked.

### Requirement: Memory Access Tiles API
The dashboard SHALL expose per-butler memory tier access.

#### Scenario: Read memory access
- **WHEN** `GET /api/butlers/{name}/memory-access` is called
- **THEN** the response is `ApiResponse[MemoryAccess]` with `read: ("short"|"mid"|"long")[]`, `write: ("short"|"mid"|"long")[]`, `namespace: str`, `embedding_model: str`, `drops_7d: int`.

## Source References
- PLAN.md §6 Phase 7 — fold-in scope.
- `pr/overview/settings-refactor/settings-expanded.jsx :: ButlersExpanded` is the visual reference.
- Reuses `audit.append()` from dashboard-audit-log on every mutation.
- Existing dashboard-butler-management requirements (fleet list, detail tabs) are unchanged by this delta.
