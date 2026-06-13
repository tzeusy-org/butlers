## Layer C: Shared primitive beads (must land before Layer D)

- [ ] C.1 `bu-iuol4.13` Implement KpiCell + Panel atoms (`frontend/src/components/butler-detail/atoms/`): `<Panel title sub span scroll height>` atom + `<KpiCell label value subLine tone>` atom. Includes Storybook stories and unit tests.
- [ ] C.2 `bu-iuol4.14` Implement RangeToggle (`frontend/src/components/butler-detail/RangeToggle.tsx`): 24h/7d/30d segmented control, mono labels, single instance per page, persists per-tab in URL or component state.
- [ ] C.3 `bu-iuol4.15` Implement DayBars7d30d (`frontend/src/components/butler-detail/DayBars7d30d.tsx`): 4-col bar chart companion to ActivityStripe for 7d and 30d ranges.

## Layer D base-tab beads (gate on Layer C and relevant Layer B analytics endpoints)

- [ ] D.1 `bu-iuol4.16` Frontend: ButlerActivityTab redesign (replaces stub). KPI quartet (sessions, p50, p95, errors) + activity chart (Stripe24 or DayBars per range) + kind breakdown panel. Gates on bu-iuol4.4/.5/.6/.7 (analytics endpoints) + Layer C atoms.
- [ ] D.2 `bu-iuol4.17` Frontend: ButlerLogsTab redesign (replaces stub). Full-width scroll panel, INFO/DEBUG/WARN/ERROR filter chips, mono 11px lines (78px ts + 56px level + flex msg). Gates on bu-iuol4.10 (logs endpoint) + Layer C atoms.
- [ ] D.3 `bu-iuol4.18` Frontend: ButlerApprovalsTab redesign (replaces stub). Full-width scroll panel, severity dot + title + meta + action link, empty state "No items pending review." No new backend. Gates on Layer C atoms.
- [ ] D.4 `bu-iuol4.19` Frontend: ButlerSpendTab redesign (replaces stub). KPI quartet (today, 30d, per-session, tokens in/out) + bar trend + model breakdown KV list. Gates on bu-iuol4.8/.9 (costs endpoints) + Layer C atoms.
- [ ] D.5 `bu-iuol4.20` Frontend: ButlerMemoryTab redesign (replaces stub). KPI quartet (episodes, facts, entities, rules with "+N today" sub-lines) + recent-writes feed. Gates on bu-iuol4.12 (memory endpoint) + Layer C atoms.
- [ ] D.6 Config tab restyle. 2x2 panel grid (process / schedule / scopes-oauth / integrations). MarkdownSections collapse to accordion. No new API. Gates on Layer C Panel atom.

## Reconciliation

- [ ] D.18 Reconciliation report (bu-iuol4.18 epic slot): verify all resident-mode tabs (Activity/Logs/Approvals/Spend/Memory/Config) render against live data with no stub content, no hardcoded butler names, no oklch literals in JSX, all timestamps via `<Time>`, no em-dashes in copy. Closes the spec gate opened by this change. Note: numbered D.18 to match the bu-iuol4.18 epic slot, not as a sequential continuation of D.6.
