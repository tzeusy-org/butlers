## Why

The dashboard capability specs (`openspec/specs/dashboard-*`, `system-overview-page`) had drifted from
the live frontend and API: requirements described deleted pages and hooks, the same fact was homed in
several specs, and normative text carried bead ids, change history, and pixel/hook-level internals.
A spec that contradicts the code misleads every agent that reads it as binding.

## What Changes

- Remove requirements whose surface no longer exists in code (legacy approvals/ingestion/settings
  pages, backfill UI, deleted hooks, Groups/CRM pages, the superseded connector-filter UI spec).
- Rewrite live requirements that describe the code wrongly so they state its observable behavior.
- Give each duplicated fact a single home and replace the other copies with a cross-reference.
- Condense endpoint and hook inventories to their normative clauses.
- Strip change narrative (bead ids, "this change", history blockquotes, tombstones, archived
  change paths) and raise pixel/Tailwind/hook-name detail to observable behavior; pixel values stay
  only in `dashboard-design-language`.

Out of scope: splitting oversized specs and backfilling missing `## Source References` sections.

## Impact

Spec-only. No code or API behavior changes.
