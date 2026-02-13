# Frontend Source-of-Truth Specifications

This directory is the canonical specification set for the current dashboard frontend implementation.

- Source basis:
  - `docs/FRONTEND_PROJECT_PLAN.md` (original intent and architecture direction)
  - `frontend/src/**` (actual implemented behavior)
- Source-of-truth precedence:
  - `docs/frontend/**` (this directory) is authoritative for current behavior.
  - `docs/FRONTEND_PROJECT_PLAN.md` is planning/history context.

## Spec Set

- `docs/frontend/purpose-and-single-pane.md`
  - Purpose, role, and why the dashboard is the operational single-pane-of-glass.
- `docs/frontend/information-architecture.md`
  - Global navigation, route map, and tab structures.
- `docs/frontend/feature-inventory.md`
  - Existing implemented features, including current gaps/placeholders.
- `docs/frontend/data-access-and-refresh.md`
  - API domains, polling/refresh behavior, and write-operation surfaces.
- `docs/frontend/backend-api-contract.md`
  - Target-state backend endpoints and payload contracts required by the frontend.

For backend support, `docs/frontend/backend-api-contract.md` is normative target-state behavior, not a best-effort snapshot.

## Update Rule

When frontend behavior changes (routes, tabs, capabilities, or operator workflows), update this directory in the same change.
