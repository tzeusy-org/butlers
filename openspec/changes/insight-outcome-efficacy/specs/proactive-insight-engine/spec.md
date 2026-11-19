## ADDED Requirements

### Requirement: Premise-Outcome Ledger

ID: REQ-proactive-insight-engine-037
Source: bu-s11n0s.15 adopted design (2026-10-16 regroom) and RFC 0011 as kept by RFC 0038; vision success markers (reduce frequency when the owner disengages)
Scope: v1-mandatory

Switchboard SHALL enroll every terminal premise-bound insight candidate, while its observation window is open, into `public.insight_outcomes` with exactly one exposure: `delivered` or `coalesced` (treated), `held_budget` or `held_context` (natural holdouts; nothing is withheld deliberately), or `withdrawn`, `shadowed`, `held_custody`, `held_unverified` or `failed` (excluded from both arms). A delivery refused because an owner endpoint was under a custody hold SHALL be `held_custody`, never a holdout. A daily deterministic sweep SHALL record the outcome once: `resolved` with the owner-condition episode's exact `resolved_at` and `resolution_reason` (`unspecified` when absent), or with the sweep time and precision `observed_by` for a probe premise that stopped holding; `persisted` when the premise still holds at window close; `unmeasurable` after three unknown reads, never "ineffective". A settled row SHALL never be reopened, enrollment and the sweep SHALL be idempotent, and the ledger SHALL be readable and writable only by the Switchboard runtime role. This slice SHALL NOT change what the broker delivers.

#### Scenario: Both arms resolve and a rerun changes nothing

- **WHEN** two premised bill candidates meet a budget of one, so one is delivered and the other expires held by budget, and both bills are then paid
- **THEN** the ledger holds one `delivered` and one `held_budget` row, the sweep records both `resolved` with precision `observed_by`, and a second enrollment and sweep change no row

#### Scenario: An owner-condition premise resolves exactly

- **WHEN** a delivered candidate's owner-condition episode resolves with a resolution reason
- **THEN** its outcome is `resolved` with the episode's own `resolved_at`, precision `exact` and that reason

#### Scenario: Custody and unknown reads are not attention signals

- **WHEN** a candidate's delivery is refused by an endpoint custody hold and its probe premise cannot be read
- **THEN** its exposure is `held_custody` and, after three unknown reads, its outcome is `unmeasurable`
