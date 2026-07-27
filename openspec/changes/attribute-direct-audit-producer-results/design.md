## Context

`audit_router.append()` intentionally accepts an optional `result` so legacy
and generic callers can continue to append compatible history. Nine current
direct production writers omit it, however, including attention-notification
markers that are written only after a confirmed delivery. The audit row then
cannot distinguish detection, escalation, delivery, or a successful check.

## Goals / Non-Goals

**Goals:**

- Make every current direct production `audit_router.append` call supply the
  producer's real outcome.
- Preserve the model-breaker notification's confirmed-delivery attribution as
  `result="delivered"`.
- Prevent a future direct writer from omitting `result`, with an actionable
  source location in the regression failure.

**Non-Goals:**

- Rewriting historical audit rows or adding a data migration.
- Making `result` non-optional on the generic audit router.
- Changing notification, debounce, retry, or escalation behavior.
- Normalizing result vocabulary outside the direct-writer sweep.

## Decisions

### Write the outcome at the direct producer boundary

Each producer already knows what occurred at the point where it appends its
row. A successful external deadman ping records `success`; first stale/drift
observation records `detected`; a completed QA escalation records `escalated`;
and rows written only after a confirmed owner notification record `delivered`.
This records observed fact rather than inferring it later from action names or
notes.

Using one generic fallback result was rejected because it would erase the
distinction between detection, escalation, and delivery that operators need.

### Preserve generic router compatibility

The existing `result: str | None = None` parameter stays optional. Direct
producers receive explicit arguments, while wrappers and callers outside this
sweep keep their current compatibility semantics. A router-level `NOT NULL`
rule or signature change was rejected because it would broaden the contract
and break unrelated valid append callers.

### Guard direct call syntax with focused source-level coverage

A contract test parses production Python under `src/butlers` and `roster` and
finds direct `audit_router.append(...)` calls. It fails with `path:line` for any
call that does not provide the `result` keyword, and a fixture proves both the
roster missing-result failure path and the generic-alias compatibility path.
Producer tests retain the semantic assertion that the model-breaker delivery
uses `delivered`.

An audit-table trigger was rejected because it would impose a global result
policy and cannot know a producer's actual outcome vocabulary.

## Risks / Trade-offs

- **A future alias bypasses the direct-call guard** → The guard deliberately
  covers the repository's direct `audit_router.append` convention, and code
  review plus its source-root sweep make new direct omissions visible without
  changing generic router semantics.
- **A result label is mistaken for delivery** → Use `delivered` only after the
  existing producer has confirmed delivery; suppressed or failed paths retain
  their current no-marker behavior.

## Migration Plan

Deploy the forward-only source and test changes together. No migration or
historical backfill is required. Rollback restores the prior code behavior but
does not edit already appended audit history.

## Open Questions

None.
