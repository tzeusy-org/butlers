# Tasks

## 1. Spec landing

- [x] 1.1 Create `openspec/changes/chronicles-editorial-rewrite/{proposal,design,tasks}.md`.
- [x] 1.2 Add modified-capability spec under `openspec/changes/chronicles-editorial-rewrite/specs/dashboard-chronicles/spec.md` covering: editorial archetype landing, briefing endpoint, attention endpoint, KPI endpoint, new episode types and aggregations mapping, drilldown panel preservation.
- [x] 1.3 Validate via `openspec validate chronicles-editorial-rewrite`.

## 2. Adapter child work

- [x] 2.1 `bu-7uiei`: extend `src/butlers/chronicler/adapters/google_health.py` with workout / steps / heart-rate projection.
- [x] 2.2 `bu-x1no2`: add `src/butlers/chronicler/adapters/focus.py` with `FocusInferredAdapter`.
- [x] 2.3 `bu-g6qqh`: add `src/butlers/chronicler/adapters/reading.py` with `ReadingInferredAdapter`.
- [x] 2.4 Update `src/butlers/chronicler/aggregations.py` with three new mappings (`workout_episode -> other`, `focus_block -> tasks`, `reading_block -> tasks`).
- [x] 2.5 Register the four new adapters in `src/butlers/chronicler/adapters/__init__.py` and `src/butlers/chronicler/jobs.py`.
- [x] 2.6 Add scoped adapter tests under `tests/chronicler/`.

## 3. API child work

- [x] 3.1 `bu-018re`: add Pydantic models for `ChroniclesBriefing`, `ChroniclesAttentionItem`, `ChroniclesRecentDay`, `ChroniclesKpi` in `roster/chronicler/api/models.py`.
- [x] 3.2 Add three endpoints to `roster/chronicler/api/router.py`: `/briefing`, `/attention`, `/kpi`. Briefing reads `chronicler.tier2_cache` for the `voice_paragraph`; falls back to a templated paragraph when missing or stale. No new LLM call paths.
- [x] 3.3 Add API tests under `tests/chronicler/test_editorial_api.py` covering briefing, attention, and KPI endpoints.

## 4. Frontend child work

- [x] 4.1 `bu-l1eg1`: add `'editorial'` to the `<Page archetype>` discriminant and let the Chronicles consumer own its Display-headline heading block.
- [x] 4.2 Add hooks `use-chronicles-briefing.ts`, `use-chronicles-attention.ts`, `use-chronicles-kpi.ts` under `frontend/src/hooks/`.
- [x] 4.3 Add `RecentDaysIndex.tsx` and `ChroniclesDrilldownPanel.tsx` under `frontend/src/components/chronicles/`.
- [x] 4.4 Rewrite `frontend/src/pages/ChroniclesPage.tsx` as the editorial-archetype consumer. Compose the owner-date eyebrow, status pill, `Headline`, `Elaboration`, `KpiStrip`, `AttentionList`, `RecentDaysIndex`, and `ChroniclesDrilldownPanel`.
- [x] 4.5 Add frontend tests under `frontend/src/pages/ChroniclesPage.test.tsx` and components as needed.

## 5. Integration and verification

- [x] 5.1 `bu-nwvwy`: run `uv run ruff check src/ tests/ roster/ conftest.py --output-format concise`.
- [x] 5.2 Run `uv run ruff format --check src/ tests/ roster/ conftest.py -q`.
- [x] 5.3 Run `uv run pytest tests/chronicler --ignore=tests/e2e -q --maxfail=3 --tb=short`.
- [x] 5.4 Run `cd frontend && npx tsc --noEmit && npx vitest run`.

## 6. Out of scope

- Lane taxonomy restructure.
- Episode-merging / fragmentation policy changes.
- Per-event LLM invocation paths.
- New database schema.
- Manifesto rewrite.
