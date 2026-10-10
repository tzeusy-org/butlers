## ADDED Requirements

### Requirement: Approval origin survives list and detail projection

The authenticated approval read API SHALL preserve explicit server-stored prepared origin in every list, history and detail projection used by the approval rail and selected dossier, using additive nullable `origin: "prepared" | null` classification. Only the exact stored string `"prepared"` SHALL establish Prepared; absent, legacy, malformed or unrecognized origin SHALL remain unknown, never inferred from action status, an absent push or a missing delivery receipt. The API SHALL preserve existing read availability, redaction, authorization, action identity and delivery-failure truth; origin SHALL confer no decision, execution, delivery, retry or provider authority.

ID: REQ-dashboard-api-068
Source: prepared-approval-origin-presentation/design.md; heart-and-soul/security.md Approval Gates; owner-released run13 S3 (bu-3b6goa)
Scope: v1-mandatory

#### Scenario: Persisted prepared origin reaches every approval projection

- **WHEN** an owning producer has persisted an action with exact `origin="prepared"` and that action is read through the paginated action list, flat or history summary and selected detail
- **THEN** each applicable response preserves the same explicit prepared classification for the same action
- **AND** intended digest-only non-send remains distinct from a confirmed failed push.

#### Scenario: Unknown origin cannot be inferred from absence

- **WHEN** a readable action has absent, legacy, malformed or unrecognized origin, including an action with no push outcome or delivery receipt
- **THEN** summary and detail return `origin=null` and no projection invents prepared origin
- **AND** existing status, independently observed delivery failure and unknown delivery evidence retain their own meaning.

#### Scenario: Unavailable origin source preserves incomplete coverage

- **WHEN** an eligible approval source or selected-detail read fails or is degraded
- **THEN** the API retains the existing named degraded or unavailable response contract instead of fabricating an action, Prepared classification or complete empty result
- **AND** successful other-source rows retain their own observed origin without certifying complete coverage.

#### Scenario: Origin projection preserves redaction and action authority

- **WHEN** an authenticated reader inspects a prepared action containing sensitive tool arguments or execution data, or an unauthorized reader attempts the same read
- **THEN** existing redaction and read authorization rules remain enforced
- **AND** neither origin nor delivery context exposes recipient identity, callback material, action keys, raw provider responses or raw errors
- **AND** no origin field changes approval, execution, retry or provider permissions.

#### Scenario: Repeated reads preserve action identity without mutation

- **WHEN** a prepared action is repeatedly read or a selected dossier is changed to another action
- **THEN** origin remains bound to the actual action ID and owning source returned by that read
- **AND** the read creates no approval, push, execution, origin backfill or other persistent mutation.
