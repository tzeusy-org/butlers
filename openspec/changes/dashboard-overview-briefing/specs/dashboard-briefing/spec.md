# Dashboard Briefing

## Purpose

The dashboard briefing is the editorial opening of the dashboard home page: a templated greeting plus a templated headline that classifies the state of the system, plus a one-to-three sentence LLM-elaborated paragraph that names what is true right now in butler voice. The briefing is composed server-side and returned as a single object the frontend renders verbatim.

This spec defines the wire contract, the classification taxonomy, the headline table, the LLM prompt and parameters, the deterministic fallback, the per-owner caching contract, and the voice enforcement that the endpoint applies before returning. Visual presentation (typography, layout, the status pill) is governed by `about/heart-and-soul/design-language.md`.

## ADDED Requirements

### Requirement: Briefing Response Schema

The endpoint `GET /api/dashboard/briefing` SHALL return a JSON object with exactly six fields: `greet`, `headline`, `elaboration`, `source`, `state_class`, `generated_at`. The schema MUST be stable across implementation changes.

#### Scenario: Response shape on success

- **WHEN** an authenticated owner calls `GET /api/dashboard/briefing`
- **THEN** the response is HTTP 200
- **AND** the body is a JSON object with the six required fields
- **AND** `greet` matches `"Good {time_of_day}."` for one of the five time_of_day values
- **AND** `headline` is the templated body for the computed `state_class`
- **AND** `source` is one of `"llm"` or `"fallback"`
- **AND** `state_class` is one of `"urgent"`, `"busy"`, `"mild"`, `"degraded-quiet"`, `"quiet"`
- **AND** `generated_at` is an ISO 8601 timestamp recording the wall-clock time at which the Briefing object was finalized, set once per composition regardless of whether `source` is `"llm"` or `"fallback"` and regardless of how long the underlying LLM call took

### Requirement: State Classification

The endpoint SHALL classify the current dashboard state into one of five `state_class` values using a deterministic function over the attention list and butler health.

#### Scenario: Urgent class

- **WHEN** at least one attention item has severity `high`
- **THEN** `state_class` is `"urgent"`
- **AND** `headline` is `"{n} things need you now."` if there is more than one high-severity item, or `"One thing needs you now."` if exactly one

#### Scenario: Busy class

- **WHEN** there are three or more attention items
- **AND** none of them are severity `high`
- **THEN** `state_class` is `"busy"`
- **AND** `headline` is `"Things are busy with {total} items waiting."`

#### Scenario: Mild class

- **WHEN** there are one or two attention items
- **AND** none are severity `high`
- **THEN** `state_class` is `"mild"`
- **AND** `headline` is `"Things are quiet, with {n} exception."` for n == 1, or `"Things are quiet, with {n} exceptions."` for n == 2

#### Scenario: Degraded-quiet class

- **WHEN** there are zero attention items
- **AND** at least one butler is `degraded` or `error`
- **THEN** `state_class` is `"degraded-quiet"`
- **AND** `headline` is `"Quiet, but {n} butler is degraded."` for n == 1, or `"Quiet, but {n} butlers are degraded."` for n > 1

#### Scenario: Quiet class

- **WHEN** there are zero attention items
- **AND** all butlers report `healthy`
- **THEN** `state_class` is `"quiet"`
- **AND** `headline` is `"Everything is in hand."`

### Requirement: Time-of-Day Greeting

The endpoint SHALL compute `time_of_day` from `state.now.hour` and return a templated greeting.

#### Scenario: Time-of-day buckets

- **WHEN** `state.now.hour` is less than 5
- **THEN** `greet` is `"Good late-night."`

- **WHEN** `state.now.hour` is greater than or equal to 5 and less than 12
- **THEN** `greet` is `"Good morning."`

- **WHEN** `state.now.hour` is greater than or equal to 12 and less than 17
- **THEN** `greet` is `"Good afternoon."`

- **WHEN** `state.now.hour` is greater than or equal to 17 and less than 21
- **THEN** `greet` is `"Good evening."`

- **WHEN** `state.now.hour` is greater than or equal to 21
- **THEN** `greet` is `"Good night."`

### Requirement: LLM Elaboration

The endpoint SHALL call Claude Haiku 4.5 with a pinned prompt to produce a one-to-three sentence elaboration paragraph. The prompt MUST encode the dashboard voice rules.

#### Scenario: LLM happy path

- **WHEN** the LLM call returns within 4 seconds
- **AND** the response passes the post-generation voice lint
- **THEN** `elaboration` is set to the LLM response
- **AND** `source` is `"llm"`

#### Scenario: LLM timeout

- **WHEN** the LLM call exceeds 4 seconds
- **THEN** the endpoint cancels the call
- **AND** `elaboration` is set to the templated fallback for the computed `state_class`
- **AND** `source` is `"fallback"`

#### Scenario: LLM error or empty response

- **WHEN** the LLM call raises an exception or returns an empty body
- **THEN** `elaboration` is set to the templated fallback
- **AND** `source` is `"fallback"`

### Requirement: Voice Enforcement

The endpoint SHALL run a post-generation lint over the LLM response and reject responses that contain banned tokens.

#### Scenario: Voice lint rejects banned tokens

- **WHEN** the LLM response contains an exclamation mark, an em-dash, a first-person pronoun (`I`, `we`, `us`, `our`), a future-tense marker (`will be`, `is going to`), or a hedging adverb (`currently`, `presently`, `just`, `simply`, `basically`)
- **THEN** the response is rejected
- **AND** `elaboration` falls through to the templated fallback
- **AND** `source` is `"fallback"`
- **AND** the rejection emits a `briefing.elaboration.rejected` metric

#### Scenario: Voice lint respects word boundaries

- **WHEN** the LLM response contains the substring "actually" only inside a longer word like "factually"
- **THEN** the response is not rejected for that match
- **AND** the lint check uses word-boundary regex matching

### Requirement: Per-Owner Caching

The endpoint SHALL cache the Briefing per owner contact for 5 minutes.

#### Scenario: Cache hit

- **WHEN** an owner calls the endpoint within 5 minutes of a prior successful call
- **THEN** the response is served from cache
- **AND** `generated_at` reflects the original cached generation time, not the current time

#### Scenario: Cache miss after TTL

- **WHEN** more than 5 minutes have elapsed since the last cached Briefing for the owner
- **THEN** a fresh Briefing is composed
- **AND** the cache is repopulated
- **AND** `generated_at` reflects the new generation time

### Requirement: Owner-Only Access

The endpoint SHALL be accessible only to the owner contact.

#### Scenario: Non-owner request

- **WHEN** an authenticated session that is not the owner contact calls the endpoint
- **THEN** the response is HTTP 403
- **AND** no cache entry is read or written

#### Scenario: Unauthenticated request

- **WHEN** an unauthenticated request hits the endpoint
- **THEN** the response is HTTP 401 (the standard dashboard auth gate)

### Requirement: Endpoint Robustness

The endpoint SHALL never raise to the caller. Failures internal to the briefing pipeline (LLM, lint, classification) SHALL be caught and surfaced as the templated fallback. The endpoint MAY return HTTP 500 only when the templated fallback itself fails (which implies a code or import error).

#### Scenario: Total LLM unavailability

- **WHEN** the LLM transport is unreachable (DNS failure, TLS failure, upstream 5xx)
- **THEN** the response is HTTP 200
- **AND** `source` is `"fallback"`
- **AND** the fallback paragraph is one of the five templated paragraphs

#### Scenario: Classification exception

- **WHEN** the classification function raises (a malformed state row, missing column, schema drift)
- **THEN** the endpoint logs the error
- **AND** returns `state_class = "quiet"` with the quiet templated paragraph
- **AND** `source` is `"fallback"`
- **AND** an internal error metric is emitted
