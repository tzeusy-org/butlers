## MODIFIED Requirements

### Requirement: Frontend Route Registration
The dashboard router SHALL register the Settings Console route, three Settings sub-routes, and a replacement `/approvals` route in `frontend/src/router.tsx`.

#### Scenario: Settings routes
- **WHEN** the frontend router is configured
- **THEN** the following routes are registered, each rendering within the `RootLayout`:
  - `/settings` → `SettingsConsolePage`
  - `/settings/models` → `SettingsModelsPage`
  - `/settings/spend` → `SettingsSpendPage`
  - `/settings/permissions` → `SettingsPermissionsPage`
- **AND** the existing `/settings` → `SettingsPage` registration is REMOVED in the same router change
- **AND** the `frontend/src/pages/SettingsPage.tsx` file is DELETED in the same PR.

#### Scenario: Approvals route replacement
- **WHEN** the frontend router is configured
- **THEN** `/approvals` renders the new `ApprovalsPage` (rewritten in this change), not the legacy page.

#### Scenario: Per-user OAuth stays at /secrets
- **WHEN** the frontend router is configured
- **THEN** provider-setup cards (`GoogleOAuthSection`, `HomeAssistantSetupCard`, `OwnTracksSetupCard`, `SpotifySetupCard`, `SteamSetupCard`, `WhatsAppSetupCard`, `GoogleHealthStatusCard`) are consumed by `SecretsPage` and NOT by any `/settings/*` route
- **AND** `/settings` is system-side only (catalog, spend, permissions, audit, webhooks); per-user OAuth lives on `/secrets`.

### Requirement: Sidebar Navigation Config
The dashboard sidebar SHALL surface `/settings` as a single nav entry without nested sub-route entries.

#### Scenario: Sidebar Settings entry
- **WHEN** the sidebar renders
- **THEN** a `Settings` nav item links to `/settings`
- **AND** no separate sidebar entries exist for `/settings/models`, `/settings/spend`, or `/settings/permissions` — these are reached via the Console panels.

#### Scenario: Sidebar Approvals entry
- **WHEN** the sidebar renders
- **THEN** an `Approvals` nav item links to `/approvals`
- **AND** the badge count reflects `header.open_approvals` from `GET /api/settings/console` (or the equivalent live count).

## Source References
- PLAN.md §4 routes contract (`pr/overview/settings-refactor/PLAN.md`).
- `about/heart-and-soul/design-language.md` — Sidebar/composition: 56px icon rail, one elevation, no nested nav.
- `about/heart-and-soul/v1.md` — Per-user OAuth (Google, Spotify, Telegram, Steam, etc.) is explicitly out of v1 system-settings scope; OAuth setup remains on `/secrets` to keep `/settings` system-side only.
- `about/heart-and-soul/vision.md` Non-Negotiable Rule 1 (composure) and Rule 6 (governing-document-driven scope).
