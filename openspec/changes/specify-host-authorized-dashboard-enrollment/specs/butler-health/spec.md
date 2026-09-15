## MODIFIED Requirements

### Requirement: [TARGET-STATE] Health Voice briefing route

- The health butler's dashboard API SHALL expose `GET /api/health/briefing`, an owner-only LLM Voice
composer that mirrors `GET /api/dashboard/briefing` (see the `dashboard-briefing` spec). It returns a
`Briefing` object (`greet`, `headline`, `elaboration`, `source`, `state_class`, `generated_at`). It
SHALL be **templated-only by default**, with LLM elaboration enabled only behind a cost flag; it
SHALL cache per owner for 5 minutes; and it SHALL never raise — any LLM, lint, or timeout failure
falls through to the deterministic templated paragraph. The `source` field is exactly one of
`"llm"` (a model-written elaboration) or `"fallback"` (the deterministic templated paragraph); the
dashboard BriefingStatus pill renders `source = "llm"` as `llm · cached` and `source = "fallback"`
as `templated`.
- The briefing copy MUST pass a **non-diagnostic voice-lint** that extends the global
`voice_lint_passes`; on failure the elaboration MUST fall through to the templated fallback (never
raise). The lint MUST reject: (1) diagnosis/advice tokens — `diagnos*`, "you (may|might|could)
have", "risk of", "symptom of", "consistent with", "indicates", "should see a doctor", or any
treatment advice; (2) celebration/judgment — exclamation marks, first-person pronouns, praise tokens,
green-check/streak language; (3) future-tense markers ("will", "going to") — prediction is
diagnosis-adjacent; (4) clinical verdict adjectives ("elevated", "dangerously high") where a
measurement should instead be paired with the owner's own stored reference range.
- The central `dashboard-owner-auth` boundary SHALL admit a valid configured
`X-API-Key` or a valid server-managed owner session before protected body reads,
domain-pool acquisition, caches or handlers. Passkey verification issues a session;
it is not a new per-route credential. Cookie-backed unsafe actions additionally
require independent synchronizer CSRF and exact Origin validation. Unavailable
authoritative auth state returns safe `503`; missing, expired, revoked or invalid
caller authority returns `401`. An absent API key alone is not unavailability when
healthy keyless session authority exists. Domain checks remain mandatory after
central authentication; auth-store reads necessary for verification are distinct
from forbidden pre-authentication domain access.
- The never-raise/templated-fallback promise applies only after authentication and
the independent Health owner-domain assertion. Authentication denial/unavailability
MUST NOT return a private templated briefing or touch its per-owner cache.
`GET /api/health/briefing` is not an exact public health probe.

ID: REQ-butler-health-001
Source: dashboard-owner-auth successor design D1-D9; existing butler-health behavior preserved except explicit owner-auth supersession
Scope: v1-mandatory

#### Scenario: Templated-only by default

- **WHEN** the cost flag for LLM elaboration is off
- **THEN** `GET /api/health/briefing` MUST return a deterministic templated briefing with `source =
  "fallback"`
- **AND** it MUST NOT invoke an LLM

#### Scenario: Cached LLM elaboration when the flag is on

- **WHEN** the cost flag is on and an owner calls the endpoint within 5 minutes of a prior successful
  call
- **THEN** the response MUST be served from the per-owner 5-minute TTL cache
- **AND** `generated_at` MUST reflect the original cached generation time

#### Scenario: Voice-lint rejects a diagnostic line

- **WHEN** an LLM elaboration contains a diagnosis/advice token (e.g. "risk of" or "you may have")
- **THEN** the elaboration MUST be rejected and replaced with the templated fallback
- **AND** `source` MUST be `"fallback"`
- **AND** the endpoint MUST NOT raise

#### Scenario: Endpoint never raises

- **WHEN** the LLM transport is unreachable or times out
- **THEN** the response MUST be HTTP 200 with the templated fallback paragraph
- **AND** `source` MUST be `"fallback"`

#### Scenario: Owner-only access

- **WHEN** a non-owner session calls `GET /api/health/briefing`
- **THEN** the response MUST be HTTP 403 and no cache entry is read or written
