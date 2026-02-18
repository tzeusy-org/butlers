# Dashboard Modules

Module management UI and API for the Butlers dashboard. Provides runtime visibility into each butler's loaded modules — their health, enabled state, failure details, and the tools they provide — plus toggle switches for enabling or disabling modules at runtime.

Module state is read from the butler daemon via the `module.states` MCP tool. The enabled flag is mutated through the `module.set_enabled` MCP tool (never via direct DB writes). This follows the write-through-MCP pattern established in D11.

The API is implemented in `src/butlers/api/routers/modules.py`. Module health uses three values: `active` (operational), `failed` (startup failure), and `cascade_failed` (dependency-induced failure). The `has_config` field indicates whether `butler.toml` contains a `[modules.{name}]` section for the module.

---

## ADDED Requirements

### Requirement: Get module states API

The dashboard API SHALL expose `GET /api/butlers/{name}/module-states` which calls the named butler's `module.states` MCP tool and returns runtime state for all loaded modules.

The response SHALL be wrapped in the standard `ApiResponse` envelope: `{"data": [...]}`. Each module object in the `data` array MUST include:
- `name` (string) — the module's registered name
- `health` (string) — one of `"active"`, `"failed"`, or `"cascade_failed"`
- `enabled` (boolean) — whether the module is currently enabled
- `failure_phase` (string or null) — the startup phase where failure occurred; null if healthy
- `failure_error` (string or null) — the error message from startup failure; null if healthy
- `has_config` (boolean) — whether `butler.toml` contains a `[modules.{name}]` section

The endpoint MUST return HTTP 404 if the butler name is not registered. It MUST return HTTP 503 if the butler daemon is unreachable.

#### Scenario: Fetch module states for a healthy butler

- **WHEN** `GET /api/butlers/switchboard/module-states` is called
- **AND** the `switchboard` butler is reachable and has two modules loaded (`telegram`, `email`)
- **THEN** the API MUST return HTTP 200 with a response body `{"data": [...]}`
- **AND** each element in `data` MUST include `name`, `health`, `enabled`, `failure_phase`, `failure_error`, and `has_config`
- **AND** healthy modules MUST have `health = "active"`, `failure_phase = null`, `failure_error = null`

#### Scenario: Failed module included in response

- **WHEN** `GET /api/butlers/switchboard/module-states` is called
- **AND** the `telegram` module failed during startup with `failure_phase = "startup"` and `failure_error = "Connection refused"`
- **THEN** the API MUST return the `telegram` module with `health = "failed"`, `failure_phase = "startup"`, `failure_error = "Connection refused"`

#### Scenario: Cascade-failed module included in response

- **WHEN** `GET /api/butlers/switchboard/module-states` is called
- **AND** the `email` module failed because its dependency (`core`) failed, resulting in `health = "cascade_failed"`
- **THEN** the API MUST return the `email` module with `health = "cascade_failed"`
- **AND** `failure_phase` and `failure_error` MAY be null if the cascade failure provides no additional detail

#### Scenario: Module without butler.toml config section

- **WHEN** `GET /api/butlers/switchboard/module-states` is called
- **AND** the `telegram` module is loaded but `butler.toml` has no `[modules.telegram]` section
- **THEN** the `telegram` module object MUST have `has_config = false`

#### Scenario: Butler not registered returns 404

- **WHEN** `GET /api/butlers/nonexistent/module-states` is called
- **AND** no butler named `"nonexistent"` is registered in the roster
- **THEN** the API MUST return HTTP 404 with an error message indicating the butler was not found

#### Scenario: Butler daemon unreachable returns 503

- **WHEN** `GET /api/butlers/switchboard/module-states` is called
- **AND** the `switchboard` butler daemon is not running
- **THEN** the API MUST return HTTP 503 with an error message indicating the butler is unreachable

---

### Requirement: Set module enabled API

The dashboard API SHALL expose `PUT /api/butlers/{name}/module-states/{module_name}/enabled` which toggles the enabled/disabled state of a module by calling the named butler's `module.set_enabled` MCP tool.

The request body MUST be a JSON object containing:
- `enabled` (boolean, required) — whether to enable (`true`) or disable (`false`) the module

On success, the API SHALL return HTTP 200 wrapped in the standard `ApiResponse` envelope with the updated module state: `{"data": {module state object}}`.

The endpoint MUST return HTTP 404 if the butler or module is not found. It MUST return HTTP 409 if the module is unavailable (health `failed` or `cascade_failed`) and cannot be toggled. It MUST return HTTP 503 if the butler daemon is unreachable.

#### Scenario: Enable a disabled module

- **WHEN** `PUT /api/butlers/switchboard/module-states/telegram/enabled` is called with body `{"enabled": true}`
- **AND** the `telegram` module is currently disabled but has `health = "active"`
- **THEN** the API MUST call `module.set_enabled` on the `switchboard` butler with `name = "telegram"` and `enabled = true`
- **AND** the API MUST return HTTP 200 with the updated module state reflecting `enabled = true`

#### Scenario: Disable an enabled module

- **WHEN** `PUT /api/butlers/switchboard/module-states/email/enabled` is called with body `{"enabled": false}`
- **AND** the `email` module is currently enabled with `health = "active"`
- **THEN** the API MUST call `module.set_enabled` with `name = "email"` and `enabled = false`
- **AND** the API MUST return HTTP 200 with the updated module state reflecting `enabled = false`

#### Scenario: Toggle unavailable module returns 409

- **WHEN** `PUT /api/butlers/switchboard/module-states/telegram/enabled` is called
- **AND** the `telegram` module has `health = "failed"`
- **THEN** the butler daemon MUST reject the toggle with an unavailability error
- **AND** the API MUST return HTTP 409 with an error message indicating the module is unavailable and cannot be toggled

#### Scenario: Module not found returns 404

- **WHEN** `PUT /api/butlers/switchboard/module-states/nonexistent/enabled` is called with body `{"enabled": true}`
- **AND** no module named `"nonexistent"` is registered on the `switchboard` butler
- **THEN** the butler daemon MUST return a not-found error
- **AND** the API MUST return HTTP 404 with an error message indicating the module was not found

#### Scenario: Butler not registered returns 404

- **WHEN** `PUT /api/butlers/nonexistent/module-states/telegram/enabled` is called
- **THEN** the API MUST return HTTP 404 with an error message indicating the butler was not found

#### Scenario: Butler daemon unreachable returns 503

- **WHEN** `PUT /api/butlers/switchboard/module-states/telegram/enabled` is called
- **AND** the `switchboard` butler daemon is not running
- **THEN** the API MUST return HTTP 503 with an error message indicating the butler is unreachable

---

### Requirement: Modules tab on butler detail page

The dashboard frontend SHALL render a **Modules** tab within each butler's detail page, accessible via `/butlers/:name?tab=modules`.

The Modules tab MUST:
1. Fetch module states from `GET /api/butlers/{name}/module-states` using TanStack Query
2. Display a list of module cards (one per module) with the fields described below
3. Provide toggle switches for enabling/disabling each module
4. Refetch module state after every successful toggle

The **Modules** tab SHALL be added to the always-rendered tab list in the butler detail page (see dashboard-butler-detail spec). The accepted `?tab=modules` query parameter SHALL be added to the list of accepted tab values.

#### Module card contents

Each module card SHALL display the following:
- **Name** — the module's registered name in a prominent heading
- **Health indicator** — a colored status dot with a label:
  - Green dot labeled "Active" when `health = "active"`
  - Red dot labeled "Failed" when `health = "failed"`, with a tooltip showing `failure_phase` and `failure_error`
  - Red dot labeled "Cascade Failed" when `health = "cascade_failed"`, with a tooltip explaining the module was disabled due to a dependency failure
- **Toggle switch** — reflects the current `enabled` state. The toggle MUST be disabled (non-interactive) when `health` is `"failed"` or `"cascade_failed"`. An explanation tooltip MUST appear on hover over a disabled toggle, stating why it cannot be toggled (e.g., "This module failed to start and cannot be enabled").
- **Config status badge** — "Configured" (green badge) when `has_config = true`, "Unconfigured" (gray badge) when `has_config = false`

#### Scenario: Modules tab displays all modules

- **WHEN** a user navigates to `/butlers/switchboard?tab=modules`
- **AND** `GET /api/butlers/switchboard/module-states` returns two modules (`telegram` and `email`)
- **THEN** the Modules tab MUST display two module cards
- **AND** each card MUST show the module name, health indicator dot, toggle switch, and config status badge

#### Scenario: Healthy module shows green indicator and active toggle

- **WHEN** a module has `health = "active"` and `enabled = true`
- **THEN** the health indicator MUST be a green dot labeled "Active"
- **AND** the toggle switch MUST be in the "on" position and interactive

#### Scenario: Healthy but disabled module shows green indicator and inactive toggle

- **WHEN** a module has `health = "active"` and `enabled = false`
- **THEN** the health indicator MUST be a green dot labeled "Active"
- **AND** the toggle switch MUST be in the "off" position and interactive

#### Scenario: Failed module shows red indicator and disabled toggle

- **WHEN** a module has `health = "failed"` and `failure_error = "SMTP connection refused"`
- **THEN** the health indicator MUST be a red dot labeled "Failed"
- **AND** the toggle switch MUST be in the disabled (non-interactive) state
- **AND** hovering over the toggle MUST display a tooltip: "This module failed to start and cannot be enabled"
- **AND** hovering over the health indicator MUST display a tooltip showing the `failure_phase` and `failure_error` values

#### Scenario: Cascade-failed module shows red indicator and disabled toggle

- **WHEN** a module has `health = "cascade_failed"`
- **THEN** the health indicator MUST be a red dot labeled "Cascade Failed"
- **AND** the toggle switch MUST be in the disabled (non-interactive) state
- **AND** hovering over the toggle MUST display a tooltip: "This module is unavailable due to a dependency failure"

#### Scenario: Configured module shows "Configured" badge

- **WHEN** a module has `has_config = true`
- **THEN** the config status badge MUST display "Configured" with a green visual style

#### Scenario: Unconfigured module shows "Unconfigured" badge

- **WHEN** a module has `has_config = false`
- **THEN** the config status badge MUST display "Unconfigured" with a muted/gray visual style

#### Scenario: Empty state when butler has no modules

- **WHEN** `GET /api/butlers/heartbeat/module-states` returns an empty `data` array
- **THEN** the Modules tab MUST display an empty state message such as "No modules loaded" indicating the butler has no modules registered

#### Scenario: Butler unreachable shows error state

- **WHEN** `GET /api/butlers/switchboard/module-states` returns HTTP 503
- **THEN** the Modules tab MUST display an error state message such as "Unable to load module states — butler is unreachable"
- **AND** the tab MUST NOT display any module cards

---

### Requirement: Toggle interaction with optimistic UI update

The Modules tab SHALL use optimistic UI updates when a toggle is clicked: the switch updates immediately in the UI, then the API call is made. If the API call fails, the toggle reverts to its previous state and an error toast is displayed.

The toggle interaction MUST follow this sequence:
1. User clicks the toggle switch on an active module
2. The toggle switch immediately flips to the new state (optimistic update)
3. `PUT /api/butlers/{name}/module-states/{module_name}/enabled` is called with the new `enabled` value
4. On success: the module card reflects the new enabled state; no toast is shown (silent success)
5. On failure: the toggle reverts to its previous state; an error toast is displayed with the error message

#### Scenario: Successful toggle updates UI immediately and persists

- **WHEN** a user clicks the toggle switch on the `telegram` module (currently `enabled = true`)
- **THEN** the toggle MUST immediately move to the "off" position (optimistic update)
- **AND** `PUT /api/butlers/switchboard/module-states/telegram/enabled` MUST be called with body `{"enabled": false}`
- **AND** on API success, the toggle MUST remain in the "off" position

#### Scenario: Failed toggle reverts and shows error toast

- **WHEN** a user clicks the toggle switch on the `email` module
- **AND** the API call to `PUT /api/butlers/switchboard/module-states/email/enabled` returns an error
- **THEN** the toggle MUST revert to its previous state
- **AND** an error toast MUST be displayed with a message describing the failure (e.g., "Failed to update email module: Butler unreachable")

#### Scenario: Toggle unavailable module shows error toast (not a UI interaction, API-driven rejection)

- **WHEN** a user somehow triggers `PUT /api/butlers/switchboard/module-states/telegram/enabled` for a `health = "failed"` module
- **AND** the API returns HTTP 409
- **THEN** an error toast MUST be displayed with a message such as "Module 'telegram' is unavailable and cannot be toggled"
- **AND** the toggle MUST remain in its previous state

---

### Requirement: Modules tab in butler detail tab list

The butler detail page (see dashboard-butler-detail spec) SHALL include the **Modules** tab as an always-rendered tab for every butler.

The updated always-rendered tab list SHALL be:

1. **Overview** — Quick snapshot of identity, health, activity, and error summary
2. **Sessions** — Cross-session history with filters and session detail drawer
3. **Modules** — Module list with health indicators and enable/disable toggles (this spec)
4. **Config** — Butler configuration files
5. **Skills** — Available skills
6. **Schedules** — Scheduled tasks with mutations
7. **Trigger** — Freeform prompt submission
8. **State** — Key-value store browser
9. **CRM** — Relationship/contact data
10. **Memory** — Memory tier cards and memory browser

The `?tab=modules` query parameter SHALL be valid for all butlers (always-rendered, no condition).

#### Scenario: Modules tab appears on every butler detail page

- **WHEN** a user navigates to `/butlers/health`
- **THEN** the butler detail page MUST render a "Modules" tab in the tab bar
- **AND** clicking the "Modules" tab MUST navigate to `/butlers/health?tab=modules`
- **AND** the Modules tab content MUST load module states from `GET /api/butlers/health/module-states`

#### Scenario: Deep link to Modules tab

- **WHEN** a user navigates to `/butlers/switchboard?tab=modules`
- **THEN** the Modules tab MUST be the active tab
- **AND** the module states for `switchboard` MUST be fetched and displayed
