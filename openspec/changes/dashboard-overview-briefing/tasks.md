## 1. Spec Landing

- [ ] 1.1 Land this proposal, design, and delta spec (`dashboard-briefing`) via the OpenSpec workflow.
- [ ] 1.2 File a follow-up bd issue for the page restructure that consumes the briefing (the editorial archetype landing for `/`).

## 2. Backend: Endpoint

- [x] 2.1 Create `src/butlers/api/routers/dashboard_briefing.py` exposing `GET /api/dashboard/briefing`. Register it in `src/butlers/api/app.py` alongside the other dashboard routers (follow the `system_router` pattern). Distinct from `src/butlers/jobs/briefing.py`, which is the cross-butler daily aggregation job.
- [x] 2.2 Create `src/butlers/api/briefing/__init__.py`, `classify.py`, `prompts.py`, `fallback.py`. Implement `classify(state) -> state_class` and `headline_for(state_class, n)` per the tables in `design.md`. The classifier reads from existing tables: `*.notifications`, `*.issues`, `*.sessions`, `core.butlers`.
- [x] 2.3 Implement the LLM call against the local catalog-backed runtime adapter path with the pinned prompt and model/runtime/timeout resolved from `public.model_catalog` at the `trivial` tier.
- [x] 2.4 Implement `elaborate_fallback(state, state_class)`. Cover all five `state_class` values; verify each fallback string complies with the voice rules.
- [x] 2.5 Implement the post-generation voice lint (D5). Reject responses containing banned tokens; emit `briefing.elaboration.rejected` and `briefing.elaboration.fallback` metrics.
- [x] 2.6 Implement the per-owner LRU+TTL cache. Cache key is owner contact id; TTL 5 minutes.
- [x] 2.7 Owner-only access gate: HTTP 403 for non-owner sessions; HTTP 401 for unauthenticated.
- [x] 2.8 Conservative classification fallback (D6): on any classification exception, return `state_class = "quiet"` with the quiet templated paragraph and emit an internal error metric.
- [x] 2.9 Enrich the internal briefing context with human-readable attention items, timestamps, notification details, and unhealthy butler summaries while preserving the six-field public response.
- [x] 2.10 Tests under `tests/dashboard/test_briefing.py`:
  - [x] classify covers all five branches
  - [x] headline_for produces the expected string for each class (singular and plural variants)
  - [x] LLM happy path returns `source: "llm"`
  - [x] LLM timeout, error, and empty response each return `source: "fallback"` with templated paragraph
  - [x] voice lint rejects responses containing each banned token
  - [x] cache TTL respects 5 minutes (hit preserves `generated_at`, miss regenerates)
  - [x] 403 path covers non-owner access
  - [x] classification exception falls through to the `quiet` paragraph
  - [x] internal context includes attention descriptions, timestamps, notification details, and health summaries

## 3. Frontend: Stub

- [ ] 3.1 Add `Briefing` type to `frontend/src/api/types.ts` with the six fields (greet, headline, elaboration, source, state_class, generated_at). Lands in this PR.
- [ ] 3.2 Add `getDashboardBriefing()` to `frontend/src/api/client.ts` hitting `GET /api/dashboard/briefing`. Lands in this PR.
- [ ] 3.3 Add `useBriefing()` hook at `frontend/src/hooks/use-briefing.ts` with 5-minute `staleTime` and `refetchOnWindowFocus: true`. Lands in this PR.

## 4. Verification

- [ ] 4.1 Run `openspec validate dashboard-overview-briefing`.
- [ ] 4.2 Run `openspec verify dashboard-overview-briefing` after backend implementation lands.
- [ ] 4.3 Manually verify the endpoint on a running instance: each `state_class` produces the right headline; local-runtime happy path returns `source="llm"` within the configured timeout; fallback path produces a coherent paragraph; cache hit preserves `generated_at`.

## 4a. Spec / Behavior Alignment Fix [bu-5y5ve]

This section records the resolution of bu-5y5ve: a silent behavioral change
where audit-derived attention items could force `state_class = "urgent"` without
any spec coverage.

**Decision: option (a) — legitimize the behavior in the spec.**

Rationale: The behavior shipped in commit 4143128f with test coverage already in
place. Raising `urgent` on a failed scheduled task is the correct call: the
system is not operating as configured and the owner needs to know. Reverting to
`medium` would have silently suppressed a real signal. The fix is to close the
spec gap rather than downgrade the severity.

- [x] 4a.1 Add `Requirement: Attention Item Sources` to the dashboard-briefing spec
      enumerating both notification-derived and audit-derived attention items and
      documenting the `"high"` vs `"medium"` severity assignment rule for audit
      groups. (bu-5y5ve)
- [x] 4a.2 Add design decision D7 to `design.md` explaining dual-source attention
      items, the audit severity assignment rationale, and the commit that introduced
      the behavior. (bu-5y5ve)
- [x] 4a.3 Add tests for audit-derived attention items producing `state_class =
      "urgent"` when scheduled, and `"mild"` / `"busy"` when not scheduled.
      (bu-5y5ve)

## 5. Follow-Up

- [ ] 5.1 Page restructure (editorial archetype) consumes the briefing. Tracked as a separate change once the endpoint lands.
- [ ] 5.2 Optional: cron warm-up to pre-populate the per-owner cache ahead of the owner's morning window.
- [ ] 5.3 Optional: explicit cache invalidation on `quiet -> urgent` state transitions.
- [ ] 5.4 Optional: extend the post-generation lint with a fact-grounding stage that verifies any quoted item title appears in the input state JSON (R4 follow-up).
