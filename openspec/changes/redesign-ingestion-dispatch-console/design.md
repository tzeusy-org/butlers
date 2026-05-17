## Context

The `/ingestion` surface today is a single React route with a `?tab=` URL param toggling four mixed-concern tabs (Timeline, Connectors, Filters, History). The backend pairs offset+total pagination (`UNION ALL` of `public.ingestion_events` + `connectors.filtered_events` + `COUNT(*)` on every poll) with a per-event GET that augments responses with `decomposition_output` from `switchboard.message_inbox` without an audit-log entry — a live PII leak that landed in main as bu-ty7gh during Phase 1 review. Connector lifecycle actions (pause / run-now / disconnect / rotate-token / reauth) have no UI contract and no spec backing. `priority_contacts` does not exist; Gmail policy resolution depends on a flat-file env var `GMAIL_KNOWN_CONTACTS_PATH` read at evaluator boot. Connector state aggregates (`spark24h`, `rate1h`, `routedPct`, `filtered24h`) are mentioned in `connector-base-spec` lines 350-354 as DB rollup tables, but sw_025 dropped those tables in favor of Prometheus without amending the spec.

Stakeholders: owner (single-user surface — no multi-tenancy required); future-self (operator UX); butlers (downstream consumers of `priority_contacts` and `channel_defaults`); the in-flight `redesign-settings-dispatch-console` change (bu-do5q0) which renames `complexity_tier.discretion → specialty` and is read by the connector discretion evaluator.

Four Phase 1 review rounds and four Phase 2 agent passes have reconciled the proposal against `about/heart-and-soul/vision.md` (10 hard constraints), `about/heart-and-soul/security.md`, and 7 in-scope existing specs. The synthesis (`.tmp/ingestion-planning/phase2-D-synthesis.md`) is the canonical input to this change.

## Goals / Non-Goals

**Goals:**
- Replace the tab-param surface with first-class sub-routes (`/ingestion`, `/ingestion/connectors`, `/ingestion/connectors/:type/:identity`, `/ingestion/filters`, `/ingestion/history`). 301-redirect old tab-param URLs.
- Replace offset+total pagination with keyset cursor (BREAKING for `useIngestionEvents` hook shape).
- Introduce `priority_contacts` (FK-join schema) + CRUD API + GmailPolicyEvaluator wiring with a one-cycle deprecation of `GMAIL_KNOWN_CONTACTS_PATH`.
- Introduce connector lifecycle ceremony with per-action gates: audit-only (pause, run-now) vs Approvals-gated (disconnect, rotate-token, reauth).
- Introduce bulk-replay endpoint with `FOR UPDATE SKIP LOCKED` + max-batch-size 50; block `source_channel='email'` until idempotency policy ratifies.
- Add `GET /api/ingestion/pipeline?window=24h` via Prometheus + 60s TTL cache (degraded mode returns zeros, never 500).
- Add `GET /api/ingestion/connectors/available` discovery endpoint.
- Audit every mutation on the surface (`audit.append()` → `public.audit_log`, indefinite retention).
- Resolve `sender_identity` to contact name in event drawer via `resolve_contact_by_channel()`; show "unresolved" indicator on miss.
- Close all 7 doctrine gaps identified in research-02 without violating any of the 10 hard constraints.

**Non-Goals:**
- Live SSE stream (no protocol/throttle spec; 30s polling is v1-sufficient).
- Full-text payload search (raw PII in `filtered_events.full_payload`; requires separate privacy RFC).
- DSL editor for ingestion rules (no page home).
- Native per-step token tracking (SDK exposes durations only; flame strip stays a duration-proportional approximation, labeled as such).
- Timeline virtualisation (defer until production measurement shows >200 events typical).
- Gmail non-idempotent replay (gated on `connector-replay-idempotency-policy/spec`).
- Multi-user surfaces or tenant-scoped filtering.
- Any LLM-token-cost-bearing feature (no AI-summary-per-event; flame strip is approximation, not classification).

## Decisions

**D1: `priority_contacts` schema — FK join table over boolean column.**
Chosen: `priority_contacts(contact_id FK → public.contacts.id ON DELETE CASCADE, butler TEXT, added_at TIMESTAMPTZ, added_by TEXT, PK(contact_id, butler))`.
Alternatives considered:
- (a) Boolean `is_priority` on `public.contacts`: rejected — `public.contacts` is RFC-0004 territory with strict role-write rules (PATCH /api/contacts only); priority is an ingestion policy concern, not an identity concern; co-locating risks role-write confusion.
- (b) FK join table (chosen): clean separation; cascade delete; per-butler granularity; matches existing `connectors.filtered_events` and `switchboard.ingestion_rules` patterns.
- (c) Standalone unmanaged table with `email TEXT, ...`: rejected — no FK integrity; duplicates contacts surface; breaks RFC-0004 identity-as-canonical principle.

**D2: Gmail flat-file deprecation — one-cycle supersession over union.**
Chosen: `priority_contacts` supersedes `GMAIL_KNOWN_CONTACTS_PATH`. gmail.py retains env var as one-cycle fallback (DB lookup primary; flat-file consulted only on DB query failure), then removed in the following deploy.
Alternatives: (a) union semantics — rejected as added complexity with no single-owner benefit; (b) hard cutover — rejected as too risky for live filtering.

**D3: Connector state aggregates — Prometheus PromQL + 60s TTL cache.**
Chosen: existing `prometheus.py` performs PromQL at request time; results cached in-process for 60s. Degraded mode (Prometheus unreachable): response body returns zero-valued aggregates with `aggregates_available: false` and HTTP 200 — never 500.
Alternatives:
- (a) DB rollup tables (per `connector-base-spec:350-354`): rejected — sw_025 deliberately dropped these; re-introduction requires explicit storage/ETL cost justification not present in the synthesis.
- (b) Materialized view refreshed on cron: kept as fallback if Prometheus is unavailable in deployment.
- (c) Per-request UNION ALL aggregation: rejected — Conflict E (concurrency); 30s poll cadence × 50 cards = 100 queries/min worst case.

**D4: `discretion → specialty` cross-change sequencing — flag-and-gate, not block.**
Chosen: `redesign-settings-dispatch-console` rename is a Phase 3 sub-epic precondition for any ingestion bead touching connector model resolution. Flag in the ingestion epic bead description; sub-beads explicitly declare the dependency. Does NOT block opsx:ff or Phase 3 graph generation.
Alternatives: (a) hard block on rename merging — rejected because the rename's scope is narrow (one cross-table rename + evaluator update); flag-and-gate keeps the ingestion epic moving while preserving the safety contract.

**D5: Bulk replay correctness — `FOR UPDATE SKIP LOCKED` + max-batch-size 50.**
Chosen: bulk endpoint locks rows with `FOR UPDATE SKIP LOCKED` to prevent races against `filtered_event_buffer.py:drain`; max-batch-size 50 caps lock-window duration. Email channel blocked at handler (HTTP 409) until `connector-replay-idempotency-policy/spec` ratifies safe semantics.
Alternatives: (a) advisory lock per batch — rejected as coarser-grained; (b) optimistic concurrency with retry — rejected because retry storms on contention beat the original problem.

**D6: Audit log as source of replay history (v1) — defer dedicated table.**
Chosen: `GET /api/ingestion/events/:id/replays` queries `public.audit_log` for entries with `action='ingestion.event.replay'` and `target=<event_id>`. Promote to dedicated `connector_replay_history` table only if query cost in production justifies.
Alternative: dedicated table upfront — deferred; YAGNI for v1 single-owner deployment; audit log already records all the required fields.

**D7: Sub-route wrappers preserve existing FiltersTab / BackfillHistoryTab.**
Chosen: `/ingestion/filters` and `/ingestion/history` mount the existing `FiltersTab` and `BackfillHistoryTab` components inside thin route wrappers. No rewrite, no behavior change.
Alternatives: rewrite both — rejected. FiltersTab is 1750 lines / 43 tests; BackfillHistoryTab is 664 lines / 12 tests. Rewriting against an unrelated redesign exceeds the synthesis scope.

**D8: Cursor pagination = no COUNT, no `total` field in response.**
Chosen: keyset cursor on `(received_at DESC, id DESC)`; response includes `next_cursor` and `has_more`. Hook `useIngestionEvents` BREAKING-changes to drop `total`; UI gets infinite-scroll affordance instead of page count.
Alternatives: ≥30s TTL-cached COUNT — kept as fallback if a UI surface absolutely requires page count, but no surface in the redesign needs it.

## Risks / Trade-offs

- [Risk] `useIngestionEvents` hook shape change breaks any out-of-tree consumer. → Mitigation: documented BREAKING in proposal §What Changes; grep confirms only `TimelineTab.tsx` consumes the hook; refactor in same bead.
- [Risk] Cursor pagination loses "page N of M" UX. → Mitigation: replaced with infinite-scroll + jump-to-date affordance; matches the design language's continuous-ledger aesthetic.
- [Risk] Prometheus dependency for `connector-state-aggregates` introduces a new failure mode. → Mitigation: degraded mode returns zeros + `aggregates_available: false`, HTTP 200; UI hides volume sparklines and shows "metrics unavailable" eyebrow rather than blank cards.
- [Risk] `priority_contacts` cascade-delete on contact removal could silently drop priority status. → Mitigation: audit-log entry on cascade event (trigger-based); UI surface for "contact removed, X priority entries pruned".
- [Risk] Cross-change coupling with `redesign-settings-dispatch-console` (discretion→specialty rename) — silent failure if connector evaluator reads stale string. → Mitigation: flag-and-gate at sub-bead level; concrete dependency declared per affected bead; the rename itself is small and ships independently.
- [Risk] Bulk replay max-batch-size 50 too small for backfill scenarios. → Mitigation: max-batch is per-request; operators can submit multiple requests; lock window is the real constraint.
- [Risk] Sub-route migration disrupts existing in-page navigation state. → Mitigation: 301 redirects preserve filter state via query-string (period, channel, status); component-level state is per-route and explicitly resets on tab switch in current implementation, so behavior is preserved.
- [Trade-off] Audit-log-only for pause/run-now means destructive recovery (resume from pause) is human-recoverable but not Approvals-gated. → Accepted: pause is operator-recoverable; mis-pause has bounded damage; Approvals gating would add friction for routine triage.
- [Trade-off] Audit log as replay-history source means high-volume replay queries scan a wide table. → Accepted for v1; promote to dedicated table if `audit_log` query cost exceeds 100ms p99 in production.

## Migration Plan

**Phase A — prerequisites (must close first):**
- bu-ty7gh (audit gate on `GET /api/ingestion/events/{id}`) shipped to main via PR #1718. Confirmed.
- `connector-detail-archetype-conformance` change must amend its breadcrumb `href` and merge before any sub-route bead starts.

**Phase B — Wave 1 (parallel, no spec gate):**
1. Sub-route scaffolding (router.tsx + 301 redirects).
2. Cursor pagination (backend handler + hook + TimelineTab — single coordinated bead, BREAKING).
3. Replay history endpoint (read from audit_log).
4. Timeline UI additions (hour-group headers, flame strip with tooltip label, click-to-copy, drawer anchor-scroll).
5. Saved views (client-side localStorage).
6. Connector attention strip (depends on bu-ju4kh OR extracted primitive — decided at bead-writing time).

**Phase C — Wave 2 (parallel, spec-delta gated):**
1. `priority_contacts` migration + CRUD API + Gmail evaluator wiring (gate: ingestion-priority-contacts spec).
2. `channel_defaults` migration + CRUD API (gate: ingestion-ui-IA spec or minimal channel-defaults spec).
3. Connector lifecycle pause/run-now audit-only handlers (gate: connector-lifecycle-ceremony spec).
4. `/ingestion/connectors` list page + ConnectorsListPage extraction (gate: connector-detail-archetype-conformance amendment merged).
5. Connector available discovery endpoint (gate: ingestion-event-registry amendment).
6. Bulk rule operations (gate: ingestion-policy amendment).

**Phase D — Wave 3 (serial, each gate-dependent):**
1. Bulk replay handler (gate: connector-replay-idempotency-policy spec; FOR UPDATE SKIP LOCKED mandatory; email blocked).
2. PipelineStats endpoint (gate: connector-state-aggregates spec; Prometheus + 60s TTL).
3. Connector lifecycle Approvals-gated actions: rotate-token, reauth, disconnect (gate: lifecycle ceremony + OAuth scope surface specs + Approvals wiring).
4. Gmail flat-file deprecation (gate: priority_contacts live + one deploy cycle).

**Rollback strategy:**
- Wave 1 sub-routes ship behind feature flag `INGESTION_DISPATCH_CONSOLE=true`; disable to fall back to legacy tab UI.
- BREAKING hook change is rollback-by-revert (no migration); affected commits squash-merge.
- Schema migrations (priority_contacts, channel_defaults, replay_safe column, deleted_at column) are additive — drop on rollback if Wave 2 reverts.
- Audit-log entries from new mutation paths persist indefinitely (by spec); rollback does not purge.

## Open Questions

1. `AttentionStrip` — extract from bu-ju4kh's work as a primitive, or wait for bu-ju4kh and import? Decide at bead-writing time. Phase 3 bead writer needs to inspect bu-ju4kh PR state.
2. Should the Phase 2 cross-change coordination gate be a hard bead-level `blocked-by`, or a soft flag in the epic? Synthesis §2-D4 chose soft flag; revisit if discretion-evaluator-touching beads multiply.
3. Saved Views persistence — localStorage only (v1), or eventually `user_preferences` table? Defer to user-feedback-driven follow-up; not a Wave 1 blocker.
4. Flame strip cost-shape disclaimer — tooltip language exact wording? Draft: "Approximation: bars are proportional to step duration, not actual token cost." UI bead to refine.
5. `connector_replay_history` table — when (if ever) to promote from audit_log query? Defer to production query-cost measurement; document threshold (≥100ms p99) in the bulk-replay bead.
