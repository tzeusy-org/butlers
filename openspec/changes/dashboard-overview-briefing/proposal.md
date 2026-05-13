## Why

The dashboard home page (`/`) opens with a stripe chart that answers "is the system working" but does not answer "what should I look at." The owner walks up to the dashboard several times a day; the first thing on the page is a chart, not a sentence.

The editorial archetype settled in `about/heart-and-soul/design-language.md` (Display tier, Voice surface, status pill, attention list, KPI strip) introduces a different opening: a date eyebrow, a Display headline, a serif Voice paragraph, then the dense facts. The opening sentence is system-spoken: a templated headline that classifies the world, plus an LLM-elaborated paragraph that says, in butler voice, what is true right now.

To support that opening without coupling the page to LLM latency, the dashboard needs a server-side briefing endpoint that returns a stable, cacheable object the page renders verbatim. Classification stays deterministic; only the elaboration sentence is LLM-written, with a templated fallback when the model is unavailable.

## What Changes

- **New Capability**: `dashboard-briefing` -- the dashboard SHALL expose `GET /api/dashboard/briefing` returning a `Briefing` object: a templated greeting, a templated headline that classifies the current state, an LLM elaboration paragraph (with deterministic fallback), and a `source` label the frontend renders in a status pill.
- The endpoint is per-owner cached for 5 minutes. The briefing sets a mood, not a real-time status.
- Classification is deterministic and lives in `src/butlers/dashboard/briefing/classify.py`. Five state classes: `urgent`, `busy`, `mild`, `degraded-quiet`, `quiet`.
- LLM elaboration uses the local catalog-backed runtime adapter path with a pinned prompt (max 50 words, past tense for events, present tense for state, no future tense, no first person, no hedging adverbs). Runtime/model/timeout come from `public.model_catalog` at the `trivial` tier for synthetic butler `__dashboard_briefing__`; runtime failure falls back to a templated paragraph.
- A post-generation voice lint rejects responses that contain banned tokens (em-dashes, exclamation marks, first-person pronouns, future-tense markers, hedging adverbs). Rejected responses fall through to the templated path.
- The endpoint does not modify or replace `dashboard-overview`. The page restructure that consumes the briefing is tracked separately.

## Capabilities

### New Capabilities

- `dashboard-briefing`: `GET /api/dashboard/briefing` contract, the `Briefing` schema, the `state_class` taxonomy, the headline table, the LLM prompt and parameters, the fallback contract, the per-owner caching contract, and the voice lint that gates LLM responses.

## Impact

- **New API router**: `src/butlers/api/routers/dashboard_briefing.py` (added to the registration list in `src/butlers/api/app.py`, following the same pattern as `routers/system.py` and the other dashboard routers).
- **New module**: `src/butlers/api/briefing/{__init__,classify,prompts,fallback}.py`. Co-locates classifier, prompt, fallback table, and voice lint with the router that consumes them. Distinct from `src/butlers/jobs/briefing.py` (the cross-butler daily aggregation job), which is a different capability.
- **New frontend types**: `Briefing` in `frontend/src/api/types.ts`, `getDashboardBriefing()` in `frontend/src/api/client.ts`, `useBriefing()` hook in `frontend/src/hooks/use-briefing.ts`. The hook ships as a stub in this change (no consumer yet), so the implementation PR can wire it without contract churn.
- **No database schema changes**: classification reads existing tables (`*.notifications`, `*.issues`, `*.sessions`, `core.butlers`).
- **Page restructure deferred**: the editorial archetype landing for `/` is a follow-up change. This change is endpoint-only.
- **Specs touched**: new `dashboard-briefing`.
