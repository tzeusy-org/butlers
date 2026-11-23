## ADDED Requirements

### Requirement: Research admits live citation links, live pinned probes and one approved live search
This requirement implements RFC 0038 D20 Amendment 1, adopted 2026-11-22. It refines `REQ-capability-authorization-024` and `REQ-capability-authorization-006` for exactly three cases and changes nothing else in them.

The broker SHALL issue a citation handle for each citation URL it records in the goal ledger for a research call, bound to the goal, the call and the exact recorded URL. Owner-facing delivery code, not the model, SHALL render a link live only when the message references a handle that the broker resolves to a ledger entry whose URL matches exactly, and only for delivery to the owner with link previews disabled. Every citation link, cached or live, SHALL carry the full recorded URL with no query-string or fragment trimming, and each live link SHALL show its destination host beside it. Every other URL, including a model-written URL, an altered or redirected URL, a provider-opened URL that was not a citation, and a URL whose handle is unknown, foreign, unavailable or belongs to a Stopped or deleted goal, SHALL stay inert, and a resolution failure SHALL degrade to inert rendering without blocking the message. A handle SHALL confer no approval, grant or authority.

A pinned research probe is a question, an optional hostname allowlist and a cadence that the owner pinned through an owner-authenticated surface and that deterministic code runs with no model composing or altering it. The broker SHALL run a pinned probe live through the same research host, profile, budget reservation, caps and refusal rules as `REQ-capability-authorization-024`, sharing its caps with goal research and adding no separate budget or spend. The probe SHALL send exactly the pinned question and carry no goal, memory, contact or other Butlers data, hold no grant, token or goal, and be refused and recorded, with no fallback, when its pin cannot be read, its digest does not match, its budget does not fit or its tuple cannot prove the live profile. Its result SHALL be third-party content, and running it SHALL NOT mark any goal exposed. Editing a pin SHALL create a new probe, and no pin SHALL be a wildcard or template.

An exposed caller SHALL still run cached by default. It MAY pass a bounded `request_live` flag that only asks the broker to park the call; the flag SHALL NOT select or widen the mode. The broker SHALL park the call for per-event owner approval, valid for the existing goal-approval lifetime, showing the exact question, the allowlist and the warning that the question leaves Butlers and may reach sites the provider opens, pinning the question and allowlist by digest, the calling invocation, one call and live mode. On approval the runner SHALL launch that one live search with the pinned question. A replay, a changed question or allowlist, a different caller or mode, or an expired approval SHALL be refused and recorded without launching. A declined or expired approval, or a Stop, SHALL launch nothing live and SHALL NOT fall back to a cached run. The caller SHALL stay exposed, the result SHALL be third-party content, and the approval SHALL NOT be a standing-rule exemption or count toward standing-rule promotion. Third-party goals SHALL never hold `web_research` and so SHALL NOT request it. No wildcard or standing research grant SHALL exist.

ID: REQ-capability-authorization-025
Source: RFC 0038, D20 Amendment 1 (adopted 2026-11-22); owner decisions on bu-cs33zx (2026-10-20, choice A) and bu-6qaju2.6 (2026-11-22, link rule C, trigger A, text read waived)
Scope: v1-mandatory

#### Scenario: A recorded citation renders as a live link to the owner
- **WHEN** an owner `notify` message references a citation handle that resolves to a ledger URL it matches exactly
- **THEN** the link is delivered live with link previews off, carrying the full recorded URL with its destination host shown beside it
- **AND** the same message's model-written URLs are delivered inert

#### Scenario: A handle that does not resolve stays inert
- **WHEN** a message references an unknown handle, a handle from another goal, a handle of a Stopped goal, or a URL altered from the recorded one
- **THEN** the URL is delivered inert and the message is still delivered

#### Scenario: A pinned probe runs live without goal data
- **WHEN** deterministic code runs an owner-pinned research probe whose digest matches
- **THEN** the broker launches the live research profile with exactly the pinned question and no goal data, and records the call in the probe's own audit record
- **AND** no goal becomes exposed by the run

#### Scenario: A pinned probe with an unreadable or changed pin is refused
- **WHEN** the pin cannot be read or its digest differs from the stored pin
- **THEN** the call is refused and recorded and nothing launches, live or cached

#### Scenario: The owner approves one live search from an exposed goal
- **WHEN** an exposed lead passes `request_live` with its question
- **THEN** the broker parks the call for per-event owner approval showing the exact question and allowlist
- **AND** on approval exactly one live search launches with that pinned question and the lead stays exposed

#### Scenario: An approved live search is replayed or changed
- **WHEN** the approval is replayed, the question or allowlist differs, the caller or mode differs, or the approval has expired
- **THEN** the call is refused and recorded and nothing launches

#### Scenario: The owner declines the live search
- **WHEN** the owner declines, the approval expires, or the goal is Stopped first
- **THEN** nothing launches live and no cached run is substituted
- **AND** the lead may call again without the flag for the cached default
