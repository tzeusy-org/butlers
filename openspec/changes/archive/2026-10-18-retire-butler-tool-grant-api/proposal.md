## Why

The butler detail Management tab and `GET/PUT /api/butlers/{name}/tools` presented
per-butler tool grants that nothing enforces. No runtime code reads
`public.butler_tools`, so a tool marked not allowed stayed callable by every session,
while `butler.tool_set` audit entries read as effective policy changes. A control that
claims an effect it does not enforce is worse than no control.

## What Changes

- Remove the "Tools & Scope API" requirement and the two routes it describes.
- Remove the per-tool grant section from the Dispatch Fold-In page structure and state
  that the page presents no per-tool grants.
- Keep `public.butler_tools` and its rows; dropping owner-entered data is a separate,
  owner-gated decision.

## Impact

- Spec: `dashboard-butler-management` (REMOVED "Tools & Scope API", MODIFIED
  "Butler Detail Page — Dispatch Fold-In").
- Code: `src/butlers/api/routers/butler_management.py`, the Management tab and its
  client code under `frontend/src/`.
