## Context

`public.qa_patrols.status` has a stable database constraint with six values, and the
QA module writes those values throughout a patrol lifecycle. The current contract is
fragmented: the writer uses string literals, the patrol-list endpoint owns a local
filter set, API and TypeScript models expose unconstrained `string`, and overview
status rendering gives all unrecognised values a green dot. As a result, valid
`suppressed`, `running`, and `skipped_overlap` rows can look clean.

The dashboard-design-language spec permits one visual status affordance in the
overview strip, while the patrol detail already has a textual metadata caption. It
also reserves green for healthy state, amber for degraded/attention, red for error,
and permits no new motion for this interaction.

## Goals / Non-Goals

**Goals:**

- Make the existing persisted vocabulary explicit and reuse it for writer and
  request-filter validation.
- Preserve read-only visibility of corrupt or future persisted values so the UI can
  signal them instead of turning a response into an opaque server error.
- Give every patrol-status consumer one total frontend presentation function with
  accessible labels and a fail-closed unknown fallback.
- Keep polling and concurrent patrol writes presentation-only: the client reads the
  latest returned row and performs no patrol mutation.

**Non-Goals:**

- Changing dispatch, deduplication, cooldown, suppression, or overlap policy.
- Adding or migrating patrol-status values.
- Changing the QA case rail, adding overview actions, or adding animation.

## Decisions

### Canonical backend vocabulary is a small core QA module

Add a shared Python module that owns a `QaPatrolStatus` literal type, an immutable
`VALID_PATROL_STATUSES` set, and a narrow validation helper. The QA module imports
the type for every persisted status write, and the API router imports the same set
for `GET /api/qa/patrols?status=` validation.

Keeping the set inside the router would leave the writer and API independently
editable. Deriving the vocabulary from database metadata would add a database round
trip to a hot read path and would not give the writer a static, reviewable contract.

### Request validation is strict; persisted reads remain transparent

The patrol-list filter accepts only the canonical six values and rejects any other
filter with HTTP 422. Response models deliberately retain `status: str`: a malformed
or newer persisted value must reach the dashboard's explicit unknown-state renderer,
not fail response serialisation and disappear behind a generic request error.

### Frontend presentation is one total pure mapping

Introduce a frontend QA-patrol-status helper used by `QaOverviewPage`,
`QaPatrolDetailPage`, and the QA butler detail's patrol cadence stripe. The API types
distinguish the known filter vocabulary from a read status that can also contain an
arbitrary server value. The helper returns a human label and semantic token for every
known value, plus a destructive `unknown patrol status` fallback for every other
string.

The overview retains its single dot affordance visually, but each patrol link gets
an accessible name using the human label and its tooltip uses the same label. The
detail caption renders the same human label. This avoids relying on color alone and
does not expose an unbounded corrupt value as UI copy.

### Existing semantic tokens and no additional motion

The mapping uses existing Dispatch tokens only: green for `clean`, amber for
`findings_dispatched` and `suppressed`, destructive red for `error` and unknown,
and muted dots for `running` and `skipped_overlap`. No transition, pulse, or other
motion is added; tooltip appearance remains the existing instant behavior, so
reduced-motion users receive the same information without an animation.

## Risks / Trade-offs

- [Backend and frontend are different languages] → The shared backend vocabulary
  prevents server drift, while table-driven API and page tests pin the published
  six-value wire contract at both consumer boundaries.
- [An unknown persisted value may indicate database corruption] → Read it
  transparently but label it as an unknown destructive state, giving operators a
  visible signal without silently treating it as healthy.
- [Muted `running`/`skipped_overlap` dots are less urgent than amber/red] → Their
  labels explicitly name the non-success state; they never use the healthy green
  token or a success label.

## Migration Plan

1. Ship the contract-only change without a schema migration because the database
   constraint already permits the complete vocabulary.
2. Roll back by reverting the code and OpenSpec change; stored rows are unaffected.
3. A future status addition must update the database constraint, canonical backend
   vocabulary, frontend mapping, tests, and this requirement together.

## Open Questions

None.
