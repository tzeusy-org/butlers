# File-Location Map — the FE→BE trace layers

Append this to each fan-out agent's prompt. It gives the *layers a trace passes through*; for the
authoritative, maintained topology it routes to the project's own shape docs rather than restating
them (those docs are the source of truth and this map must not become a second one).

## Authoritative topology — read these, don't trust a hardcoded list

- **`about/lay-and-land/frontend.md`** — routing surface, page archetypes, where pages/components
  live. (Invoke `lay-and-land`.)
- **`about/lay-and-land/components.md`** — backend router layout (`src/butlers/api/routers/`) and
  component inventory.
- **`about/lay-and-land/deployment.md`** — service topology + ports (for live verification).

If anything below has drifted from those docs, the docs win — fix this file.

## The trace layers (the skill's novel contribution)

The audit-specific part is *how the layers chain*, which the topology docs don't spell out. For
any interactive element, walk:

1. **Handler** — the `onClick`/`onSubmit`/effect in the page or component (`frontend/src/pages/`,
   `frontend/src/components/<area>/`).
2. **API client fn** — usually in `frontend/src/api/client.ts`; gives the real HTTP method + path.
3. **Hook** — `frontend/src/hooks/use-*.ts` wraps the client fn (react-query). *An `use-*` hook
   with no component caller is a tell for shape 5 (backend-ready, FE-not-wired).*
4. **Backend route** — `src/butlers/api/routers/*.py` (core) or `roster/<butler>/api/router.py`
   (per-butler, auto-discovered). Confirm a matching `@router` decorator exists (no match → shape
   6, FE-wired/backend-stub).
5. **Runtime consumer — the step everyone skips.** `grep` the table/column the route wrote across
   `src/` (`src/butlers/core/`, `context_bus.py`, `jobs/`, module storage in
   `src/butlers/modules/<m>/`) and confirm a **non-test reader** exists. A write with no reader is
   shape 1 (decorative persistence). This is where "the endpoint exists" gets falsified.
6. **Spec (intended behaviour)** — `openspec/specs/dashboard-*/spec.md`, `module-*/spec.md`; the
   redesign brief in `docs/redesigns/`. A spec-mandated affordance that isn't rendered is shape 5.
