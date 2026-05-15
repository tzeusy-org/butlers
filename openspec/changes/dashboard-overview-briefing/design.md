## Context

The dashboard home page is the surface the owner sees first. Today its opening is a stripe chart that shows session activity over the past 24 hours. The chart answers "is the system working" but does not answer "what should I look at right now."

The editorial archetype in `about/heart-and-soul/design-language.md` introduces a different opening: a Display headline ("Things are quiet, with two exceptions.") plus a serif elaboration paragraph that names the most important attention item by name and time. To make that opening work without making the page wait on LLM latency, the backend composes the briefing once, caches it for a window that matches its purpose (5 minutes), and serves it as a stable shape the frontend renders verbatim.

The headline must be deterministic. The elaboration may be LLM-written. The page must always have something to render.

## Goals / Non-Goals

**Goals:**

- Define the wire shape of `GET /api/dashboard/briefing` and the `Briefing` object the frontend renders.
- Define `classify(state) -> state_class` and the deterministic headline table `headline_for(state_class) -> body`.
- Define the LLM prompt and local runtime dispatch path for the elaboration sentence.
- Define the fallback contract: what the endpoint returns when the LLM is unavailable, slow, or returns empty.
- Define the per-owner caching contract: 5-minute TTL, cache key, and invalidation rules.
- Define the post-generation voice lint that gates LLM responses against the dashboard voice rules.

**Non-Goals:**

- The frontend page layout. The editorial archetype is settled in `about/heart-and-soul/design-language.md`; consuming the briefing in the Overview page is a follow-up change.
- Real-time briefing. The briefing sets a mood, not a status. The attention list and Next list are the real-time surfaces.
- Multi-user / multi-viewer briefings. The dashboard is single-tenant.
- Animation specs. Motion is governed by the design language.
- A new database table for briefings. The cache is in-process.

## Decisions

### D1: Two-part composition with a deterministic headline

The Briefing has two parts: a templated headline (greeting + body) and an LLM-elaborated paragraph. The split is deliberate: the headline table is owned by code, audited, and never breaks even when the model is unavailable. The elaboration is the only place model variability appears.

`classify(state)` returns one of five values:

| state_class       | Trigger                                               |
|-------------------|-------------------------------------------------------|
| `urgent`          | one or more attention items at severity `high`        |
| `busy`            | three or more attention items, none at severity high  |
| `mild`            | one or two attention items, none at severity high     |
| `degraded-quiet`  | zero attention items but one or more butlers in `degraded` or `error` |
| `quiet`           | zero attention items, all butlers `healthy`           |

`state.now` is computed in the owner's configured general timezone before classification. `time_of_day` is computed from that owner-local `state.now.hour`:

- `late-night` if hour < 5
- `morning` if 5 <= hour < 12
- `afternoon` if 12 <= hour < 17
- `evening` if 17 <= hour < 21
- `night` otherwise

`headline_for(state_class)` returns a `body` string:

| state_class       | body (singular form)                                | body (plural form)                                  |
|-------------------|-----------------------------------------------------|-----------------------------------------------------|
| `urgent`          | "One thing needs you now."                          | "{n} things need you now."                          |
| `busy`            | "Things are busy with {total} items waiting."       | "Things are busy with {total} items waiting."       |
| `mild`            | "Things are quiet, with {n} exception."             | "Things are quiet, with {n} exceptions."            |
| `degraded-quiet`  | "Quiet, but {n} butler is degraded."                | "Quiet, but {n} butlers are degraded."              |
| `quiet`           | "Everything is in hand."                            | "Everything is in hand."                            |

Singular form fires when the count or item is exactly 1; plural form otherwise. `mild` is bounded at one or two attention items by the classifier, so the plural form covers `n == 2` only. `busy` ranges over `total >= 3` and uses the plural phrasing in all cases.

`greet` is `"Good {time_of_day}."`. The frontend renders `greet` muted and `body` in foreground, on two lines under the Display headline.

### D2: Local-runtime LLM elaboration with a pinned prompt and a hard fallback

The elaboration is one to three sentences, max 50 words, written by the local catalog-backed runtime adapter path. The prompt is pinned and versioned. Voice rules in the prompt mirror the dashboard voice rules in `about/heart-and-soul/design-language.md`: past tense for events, present tense for state, no future tense, no exclamation marks, no first person, avoid "your" when "the" works, no hedging adverbs.

The endpoint invokes the existing no-tool, single-turn runtime dispatcher used by lightweight classification paths. It resolves runtime type, model id, extra runtime args, quota, and timeout from `public.model_catalog` using the synthetic butler identity `__dashboard_briefing__` and the `trivial` complexity tier. In a normal dev stack this means a local Codex runtime session, not a direct Anthropic API call. The briefing path does not receive MCP tools and does not create a full butler task session; it is API-side prose generation with the same adapter and catalog controls as the rest of the system.

The prompt receives a bounded internal context snapshot rather than the public response body. That snapshot includes owner-local time, attention counts, the top attention items with butler/source/description/timestamps, unhealthy butler summaries, and recent notification details. This context is not exposed on the `GET /api/dashboard/briefing` wire response; it exists only so the generated elaboration can name the most important current ecosystem fact without inventing details.

On any failure (timeout, exception, empty response, content rejected by the voice lint in D5), `elaborate_fallback(state, state_class)` returns a templated paragraph from `src/butlers/dashboard/briefing/fallback.py`. The Briefing object's `source` field reflects which path produced it:

- `source: "llm"` if the model returned a usable paragraph that passed the lint.
- `source: "fallback"` if the templated path fired.

The frontend renders `source` in the status pill so the owner always knows whether they are reading model voice or templated voice.

### D3: Per-owner 5-minute TTL cache

The endpoint caches one Briefing per owner contact for 5 minutes. The cache lives in process (an LRU with TTL). On cache hit the response includes the original `generated_at`; the frontend's `useBriefing` hook reads the same TTL and refetches on window focus. The cache is invalidated on dashboard restart but not on individual state changes; the briefing is a 5-minute mood, not a real-time view.

A future change MAY add explicit invalidation on the highest-severity state transitions (an `urgent` item appearing inside a `quiet` cache, for instance). This change does not specify that path.

### D4: Endpoint surface and response shape

```
GET /api/dashboard/briefing -> 200
{
  "greet": "Good afternoon.",
  "headline": "Things are quiet, with two exceptions.",
  "elaboration": "The Health butler logged a missed reauth at 14:08, and Spotify dropped backfill...",
  "source": "llm",
  "state_class": "mild",
  "generated_at": "2026-05-08T14:21:00+08:00"
}
```

The endpoint requires the same owner authentication as other `/api/dashboard/*` routes; access by a non-owner returns HTTP 403. Errors fall back to the templated path and still return HTTP 200; HTTP 500 is reserved for the case where even the fallback fails (which implies a code or import error and is not part of normal operation).

### D5: Voice contract enforcement

The LLM prompt specifies the voice rules, but the prompt cannot be the only enforcement. The pipeline runs a post-generation lint that rejects responses containing:

- exclamation marks
- em-dashes
- first-person pronouns (`I`, `we`, `us`, `our`)
- future-tense markers (`will be`, `is going to`)
- hedging adverbs (`currently`, `presently`, `just`, `simply`, `basically`)

Rejected responses fall through to the templated path. The lint emits a `briefing.elaboration.rejected` metric so prompt drift is observable. The lint is a regex pass over the response string; matches are case-insensitive but respect word boundaries (no false-positive on "actually" inside "factually").

### D6: Conservative classification on malformed input

If the classification function raises (a malformed state row, missing column, schema drift), the endpoint logs the error, returns `state_class = "quiet"`, and uses the `quiet` templated paragraph. This is deliberately conservative: the page should never read as `urgent` because of a code bug. An internal error metric is emitted so the bug is visible.

### D7: Dual-source attention items and audit severity assignment

`state.attention_items` is populated from two sources before classification runs:

1. **Notification records** — unread or open notifications from the last 24 hours. Each notification contributes one attention item at the notification's own severity.
2. **Audit-log error groups** — rows from `dashboard_audit_log` with `result = 'error'` in the last 7 days, grouped by first-line error summary. A group receives `severity = "high"` when any row in the group originated from a scheduled session (i.e. `trigger_source LIKE 'schedule:%'`); otherwise `severity = "medium"`.

The assignment of `"high"` to scheduled-task failures is intentional: a repeated schedule failure signals the system is not operating as configured and warrants owner attention before a notification is sent. A generic non-scheduled audit error (e.g. a one-off ad-hoc session error) is `"medium"`, keeping it below the `urgent` threshold.

This was introduced in commit 4143128f and is the reason a scheduled-task failure can force `state_class = "urgent"` even when the owner has seen no notification. The spec coverage for this was added retroactively by [bu-5y5ve].

## Risks / Trade-offs

- **R1: Classification simplicity.** Five classes is coarse. A page with two `medium`-severity items and a degraded butler buckets as `mild` even though the page should arguably read as `degraded-quiet`. Mitigation: revisit the table after one week of observation; add a metric for class distribution.
- **R2: Cache coherency on first daily access.** The first owner visit of the day pays the configured local runtime cost. Subsequent visits hit the cache. Mitigation: a `cron`-driven warm-up at the start of the owner's morning window pre-populates the cache. Tracked as a follow-up, not part of this change.
- **R3: Prompt drift.** The voice rules in the prompt may not match the doctrine after a doctrine update. Mitigation: D5's post-generation lint catches the most egregious drift; the prompt module references the design-language path so a CI check can verify the cross-link is live.
- **R4: LLM hallucinated facts in the elaboration.** The prompt injects a state JSON the LLM is supposed to draw from, but the model might invent. Mitigation: the prompt forbids future tense and requires named items to appear in the input state; a follow-up lint stage MAY verify any quoted item title appears in the input state JSON.
- **R5: Cache overspecification.** Cache key on owner contact id only. If a future change introduces multi-viewer dashboards, the cache key needs to expand. Documenting the assumption here.

## Migration Plan

The endpoint is additive. Frontend `useBriefing()` is a stub in this change (no consumer wired). The consumer (Overview page restructure) is a separate change that lands after the endpoint is implemented and exercised under load. No database migrations.

## Open Questions

- Whether the post-generation lint should reject or auto-correct. Current decision: reject, fall through to templated. The templated path is the safety net; auto-correct adds complexity without clear value.
- Whether the per-owner cache key should include locale. The dashboard is single-tenant so locale is constant; the cache key is keyed only on owner contact id today.
- Whether `elaboration` should use sentences from the state JSON verbatim (citation-style) when the LLM path is unavailable, or whether the templated paragraph should remain fully canned. Current decision: fully canned templates per `state_class`. Revisit if owners report templated paragraphs feeling too generic.
