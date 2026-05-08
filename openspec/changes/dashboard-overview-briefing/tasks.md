## 1. Spec Landing

- [ ] 1.1 Land this proposal, design, and delta spec (`dashboard-briefing`) via the OpenSpec workflow.
- [ ] 1.2 File a follow-up bd issue for the page restructure that consumes the briefing (the editorial archetype landing for `/`).

## 2. Backend: Endpoint

- [ ] 2.1 Create `src/butlers/api/routers/dashboard_briefing.py` exposing `GET /api/dashboard/briefing`. Register it in `src/butlers/api/app.py` alongside the other dashboard routers (follow the `system_router` pattern). Distinct from `src/butlers/jobs/briefing.py`, which is the cross-butler daily aggregation job.
- [ ] 2.2 Create `src/butlers/api/briefing/__init__.py`, `classify.py`, `prompts.py`, `fallback.py`. Implement `classify(state) -> state_class` and `headline_for(state_class, n)` per the tables in `design.md`. The classifier reads from existing tables: `*.notifications`, `*.issues`, `*.sessions`, `core.butlers`.
- [ ] 2.3 Implement the LLM call against Claude Haiku 4.5 with the pinned prompt, `max_tokens=120`, `temperature=0.4`, `timeout=4.0` seconds.
- [ ] 2.4 Implement `elaborate_fallback(state, state_class)`. Cover all five `state_class` values; verify each fallback string complies with the voice rules.
- [ ] 2.5 Implement the post-generation voice lint (D5). Reject responses containing banned tokens; emit `briefing.elaboration.rejected` and `briefing.elaboration.fallback` metrics.
- [ ] 2.6 Implement the per-owner LRU+TTL cache. Cache key is owner contact id; TTL 5 minutes.
- [ ] 2.7 Owner-only access gate: HTTP 403 for non-owner sessions; HTTP 401 for unauthenticated.
- [ ] 2.8 Conservative classification fallback (D6): on any classification exception, return `state_class = "quiet"` with the quiet templated paragraph and emit an internal error metric.
- [ ] 2.9 Tests under `tests/dashboard/test_briefing.py`:
  - classify covers all five branches
  - headline_for produces the expected string for each class (singular and plural variants)
  - LLM happy path returns `source: "llm"`
  - LLM timeout, error, and empty response each return `source: "fallback"` with templated paragraph
  - voice lint rejects responses containing each banned token
  - cache TTL respects 5 minutes (hit preserves `generated_at`, miss regenerates)
  - 403 path covers non-owner access
  - classification exception falls through to the `quiet` paragraph

## 3. Frontend: Stub

- [ ] 3.1 Add `Briefing` type to `frontend/src/api/types.ts` with the six fields (greet, headline, elaboration, source, state_class, generated_at). Lands in this PR.
- [ ] 3.2 Add `getDashboardBriefing()` to `frontend/src/api/client.ts` hitting `GET /api/dashboard/briefing`. Lands in this PR.
- [ ] 3.3 Add `useBriefing()` hook at `frontend/src/hooks/use-briefing.ts` with 5-minute `staleTime` and `refetchOnWindowFocus: true`. Lands in this PR.

## 4. Verification

- [ ] 4.1 Run `openspec validate dashboard-overview-briefing`.
- [ ] 4.2 Run `openspec verify dashboard-overview-briefing` after backend implementation lands.
- [ ] 4.3 Manually verify the endpoint on a running instance: each `state_class` produces the right headline; LLM happy path takes under 4 seconds; fallback path produces a coherent paragraph; cache hit preserves `generated_at`.

## 5. Follow-Up

- [ ] 5.1 Page restructure (editorial archetype) consumes the briefing. Tracked as a separate change once the endpoint lands.
- [ ] 5.2 Optional: cron warm-up to pre-populate the per-owner cache ahead of the owner's morning window.
- [ ] 5.3 Optional: explicit cache invalidation on `quiet -> urgent` state transitions.
- [ ] 5.4 Optional: extend the post-generation lint with a fact-grounding stage that verifies any quoted item title appears in the input state JSON (R4 follow-up).
