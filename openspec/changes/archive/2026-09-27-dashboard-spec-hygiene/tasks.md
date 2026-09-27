## 1. Stale surfaces

- [x] 1.1 Remove requirements for surfaces absent from code (legacy approvals/ingestion/settings pages, backfill UI, deleted hooks, CRM/Groups pages, operator/resident detail modes, bulk-confirm)
- [x] 1.2 Rewrite live-but-wrong requirements to observable behavior (butler detail tab set, activity endpoints, route registry, settings console panels, Steam credentials, rule-promotion API)
- [x] 1.3 Remove the superseded `dashboard-connector-filter-ui` capability

## 2. Dedupe and condense

- [x] 2.1 Home refresh cadence in `dashboard-shell` "Bus-Aware Poll Architecture"; home loading/empty/error states in the shell state requirements
- [x] 2.2 Home design tokens and primitives in `dashboard-design-language`
- [x] 2.3 Home settings console, overview, audit log, issues, ingestion timeline and spend in one spec each; other copies cross-reference them
- [x] 2.4 Condense the dashboard-api endpoint inventory and query-hook tables to their normative clauses

## 3. Narrative and altitude

- [x] 3.1 Strip bead ids, dates, change history, tombstones and archived-change paths from normative text
- [x] 3.2 Raise pixel/Tailwind/hook-name detail to observable behavior outside `dashboard-design-language`

## 4. Verification

- [x] 4.1 `openspec validate dashboard-spec-hygiene --strict`
- [x] 4.2 Confirm no MODIFIED/REMOVED requirement collides with an active change (`make check-spec-overwrites`)
- [x] 4.3 Archive and run `scripts/check_archived_requirements_landed.py`
