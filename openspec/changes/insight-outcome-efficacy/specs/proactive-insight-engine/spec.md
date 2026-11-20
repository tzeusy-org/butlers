## ADDED Requirements

### Requirement: Premise-Outcome Ledger

ID: REQ-proactive-insight-engine-037
Source: bu-s11n0s.15 adopted design (2026-10-16 regroom) and RFC 0011 as kept by RFC 0038; vision success markers (reduce frequency when the owner disengages)
Scope: v1-mandatory

Switchboard SHALL enroll every terminal premise-bound insight candidate, while its observation window is open, into `public.insight_outcomes` with exactly one exposure: `delivered` or `coalesced` (treated), `held_budget` or `held_context` (natural holdouts; nothing is withheld deliberately), or `withdrawn`, `shadowed`, `held_custody`, `held_unverified` or `failed` (excluded from both arms). A delivery refused because an owner endpoint was under a custody hold SHALL be `held_custody`, never a holdout. A daily deterministic sweep SHALL record the outcome once: `resolved` with the owner-condition episode's exact `resolved_at` and `resolution_reason` (`unspecified` when absent), or with the sweep time and precision `observed_by` for a probe premise seen to stop holding within the window; `persisted` when the premise still holds at window close; a probe flip first seen after the window closed SHALL never be `resolved`: it is `persisted` when a check at or after the window end saw the premise holding, otherwise `unmeasurable`; `unmeasurable` after three unknown reads, never "ineffective". A settled row SHALL never be reopened, enrollment and the sweep SHALL be idempotent, and the ledger SHALL be readable and writable only by the Switchboard runtime role. This slice SHALL NOT change what the broker delivers.

#### Scenario: Both arms resolve and a rerun changes nothing

- **WHEN** two premised bill candidates meet a budget of one, so one is delivered and the other expires held by budget, and both bills are then paid
- **THEN** the ledger holds one `delivered` and one `held_budget` row, the sweep records both `resolved` with precision `observed_by`, and a second enrollment and sweep change no row

#### Scenario: An owner-condition premise resolves exactly

- **WHEN** a delivered candidate's owner-condition episode resolves with a resolution reason
- **THEN** its outcome is `resolved` with the episode's own `resolved_at`, precision `exact` and that reason

#### Scenario: Custody and unknown reads are not attention signals

- **WHEN** a candidate's delivery is refused by an endpoint custody hold and its probe premise cannot be read
- **THEN** its exposure is `held_custody` and, after three unknown reads, its outcome is `unmeasurable`

### Requirement: Premise-Outcome Efficacy

ID: REQ-proactive-insight-engine-038
Source: bu-s11n0s.15 adopted design (2026-10-16 regroom), slice 2; RFC 0011 as kept by RFC 0038
Scope: v1-mandatory

After each sweep, Switchboard SHALL roll settled ledger rows up into `public.insight_class_efficacy`, one row per origin butler, category, priority band and day, comparing the treated arm (`delivered`, `coalesced`) with the natural holdout (`held_budget`, `held_context`) only within one priority band: `low` below 50, `normal` from 50, and `urgent` at or above the urgent threshold. Each row SHALL carry both arms' measured counts, resolved counts and median time to resolve from `proposed_at`; excluded exposures and `unmeasurable` outcomes SHALL count in neither arm. A class with fewer than five measured rows in either arm SHALL be `unmeasured`; otherwise its verdict SHALL be one of `effective`, `self_resolving` or `ignored`, and its evidence SHALL label it observational. A sweep that fails or yields to a concurrent run SHALL leave the previous rows in force. `GET /api/attention/efficacy` SHALL return the latest row per class and band with the degraded envelope: `source_available: false` when the Switchboard pool is unreachable, never an empty calm list. No verdict SHALL change what the broker delivers in this slice.

#### Scenario: A self-resolving class

- **WHEN** six delivered and five budget-held candidates of one class in one band all resolve within a day
- **THEN** the class's row reads `self_resolving` with 6 of 6 treated and 5 of 5 held resolved, and its evidence says it is observational

#### Scenario: Too few to judge

- **WHEN** a class has fewer than five measured rows in either arm
- **THEN** its verdict is `unmeasured`

#### Scenario: Efficacy source unreachable

- **WHEN** the dashboard API cannot reach the Switchboard pool
- **THEN** `GET /api/attention/efficacy` returns no rows with `source_available: false`
