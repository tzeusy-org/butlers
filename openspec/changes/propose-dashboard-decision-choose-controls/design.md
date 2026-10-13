## Context

See `proposal.md` for motivation and exclusions. The original `bu-tku4df` outcome is explicit offered-option capture; this candidate does not perform it. At the bound baseline, canonical Decisions detail and the merged active recorded-state requirement prohibit choose controls. PR4403 merged at `6f16bcbb5c8b2e866d2993f809e3281a7bc04a88`; it explicitly excluded browser mutation controls. The October8 owner ruling and RFC0025 amendment authorize only the interim dev bridge topology. `bu-ckkpz.3` remains `in_progress` under Tzeusy for rollout and task4.2 smoke, with task4.3 Telegram activation separately gated.

The complete delta below is a review candidate only. It is intentionally fenced documentation in this CLI-scaffolded `skip_specs` change, not a second active delta or new capability pretending to override the prohibitions. `integration.md` defines its separately owned application. Adoption, canonical integration, source landing, implementation and runtime authority are distinct.

## Goals / Non-Goals

**Goals:** give reviewers complete clauses rather than unresolved policy sketches; retain every original control/pending/repeat/conflict/degraded/accessibility/applier-smoke outcome; pin source truth and authority limits; preserve all unrelated existing scenario bodies.

**Non-goals:** change the existing API/store/applier or current authoritative specs. The candidate defines neither approval decisions nor execution of an option's business meaning. Existing writer/token/role/default/Telegram sibling work remains external, as listed in the proposal.

## Decisions

### D1. One explicit choice through the existing owner endpoint

Use the typed `frontend/src/api/client.ts` wrapper and `use-decisions.ts` mutation through existing `apiFetch -> ownerFetch`, with body `{option}` at the encoded `/decisions/{bead_id}/intent` path. GET remains a read-only digest; the dashboard still never runs bd or reaches the tracker. Source order and exact strings are preserved, including the existing request character bound. Do not invent option hashes, version tokens, body actor/source or new endpoints. Stale-but-available source can accept an unchanged exact string under the current contract; later tracker drift is the applier's responsibility.

Native labelled buttons in the selected detail are the pointer and Tab/Enter/Space route, outside the existing selection button. Retain j/k and the actual shared footer/? hints; no invented one-key default or number bindings. Dispatch forms/focus and accessible busy/results use existing primitives. Explicit selection is the consent boundary for this already bounded governance capture. No extra chat-action confirmation policy is imported, and no fake undo/cancel is promised.

### D2. Fail closed for unknown choice-source availability

The recommended new UI choice disables new choices when digest, structured detail or recorded-choice source is unavailable, while keeping usable read context. Existing `decision_intents` degradation means intent:null is unknown. This is an explicit adoption subject, not an existing guarantee. Alternative considered: permit new POSTs while the status read is degraded and rely on server uniqueness; rejected for this candidate because it presents no dependable prior-choice state. The unchanged endpoint remains the final admission authority.

### D3. Capture identity and resolve the durable winner

Bind request and busy/result state to the captured bead id and exact option. Local row locking does not replace sw_042's `uq_decision_intents_live_bead`. Same-option replay returns the existing receipt with200/meta.created=false; different-option conflict returns409/intent_conflict:<state>. This includes browser/Telegram races. Backend order returns a known live intent before digest validation; do not change repeat behavior after closure/source loss. Already handled never supplies fabricated winning-option data.

### D4. Distinguish confirmed acknowledgement from refresh and unknown submission

A successful POST is admission evidence and updates that captured row from its returned receipt. A failed/lagging GET must not erase that acknowledgement or reopen a live choice. Keep the acknowledgement only within current authenticated session state, not localStorage or a new durable store. Existing logout/session-loss teardown still clears protected caches; late callbacks cannot restore it.

A POST timeout/disconnect/5xx is an unknown admission outcome. Do not add automatic mutation retry or replay after authentication recovery; read refresh precedes an explicit owner retry through the unchanged idempotent endpoint. Keep unavailable/refusal/conflict messages distinct. Alternative considered: discard acknowledgement and always trust the latest read; rejected because export/read lag can falsely imply no prior choice.

### D5. Preserve server and management-plane authority

`OwnerAuthMiddleware` precedes domain access. Browser sessions retain existing HTTPS Origin/CSRF handling; header-mode callers retain their adopted non-browser rules. The owner intent route sets source=dashboard and actor=owner@dashboard. Extra caller actor/source/header data creates no authority. Connector callback credentials authorize exact prompt routes only. Primary-owner/HMAC dsk1 verification stays with Telegram; no callback secret, dsk token or expanded grant reaches the browser.

The store admits pending/applying/applied/failed under REQ-owner-decision-desk-001. Only the existing management CronJob uses the applier to revalidate, close under its fixed audit actor and marker, reconcile crashes and run export. REQ-owner-decision-desk-002, REQ-beads-projection-007 and RFC0025 remain unchanged. Controls do not execute an option's payload, trigger a deploy or change its risk policy.

## Complete proposed dashboard-decisions payload

This is the exact future replacement payload for semantic review. The original requirement/scenario names are retained. Only the affected legacy valid-detail scenario gains the explicit pending-adoption/integration condition; all four other canonical detail scenario bodies and both recorded-state scenario bodies remain unchanged. Existing missing IDs are proposed backfills002/004 after collision recheck, not renumbering of existing IDs. Owner-decision-desk IDs remain unchanged. The new controls requirement uses003. The replacement of the active recorded-state block is not applied here.

```markdown
## MODIFIED Requirements

### Requirement: Read-Only Decision Detail and Selection

The `/decisions` page SHALL render eligible decisions as a rule-separated,
keyboard-operable row list with id, title, priority when present, age, and
escalation state. The list SHALL retain j/k roving selection through
`useListTriage`, publish its existing footer hints, and expand only the
selected row inline. Click and keyboard selection SHALL remain usable without
introducing approve, deny, close, default-application, or Telegram controls.
- After the exact controls amendment has owner adoption and owned canonical integration,
selected details SHALL additionally permit only the explicit offered-option controls defined by
REQ-dashboard-decisions-003. Before those gates, the existing detail remains read-only.
These gates are source authority prerequisites, not caller assertions or a new runtime flag.
- When a selected decision has `structured_details_available: true`, its detail
SHALL render the exported description when present, options in API order, the
matching default distinctly, and the native due timestamp. When structured
details are unavailable, its detail SHALL name the unavailable/malformed reason
and MUST NOT imply that the decision has no options or invent a default. The
existing created-at and escalation details SHALL remain visible.
- The `bead` query parameter SHALL deep-link selection: `/decisions?bead=<id>`
selects and expands a record only when that id occurs in the current digest. An
unknown id SHALL leave the normal list visible and unselected; it MUST NOT
create a fabricated record or hide usable rows.

ID: REQ-dashboard-decisions-002
Source: heart-and-soul/vision.md (earned calm); this candidate design D1/D2; current canonical Read-Only Decision Detail and Selection
Scope: v1-mandatory

#### Scenario: Valid detail remains read-only

- **WHEN** the owner selects a record with valid structured details before the controls amendment
  completes owner adoption and owned canonical integration
- **THEN** its inline panel shows the description, ordered options, default,
  native deadline, created-at, and any escalation detail
- **AND** no decision mutation control is rendered

#### Scenario: Malformed metadata is named instead of calming the owner

- **WHEN** the owner selects a record with
  `structured_details_available: false` because metadata is malformed
- **THEN** its inline panel names structured details as unavailable/malformed
- **AND** it does not render a calm no-options message or a fabricated default

#### Scenario: Present deep link selects the matching record

- **WHEN** the owner opens `/decisions?bead=bu-present` and `bu-present` is in
  the current digest
- **THEN** only that record expands inline

#### Scenario: Unknown deep link leaves the list usable

- **WHEN** the owner opens `/decisions?bead=bu-unknown` and that id is absent
- **THEN** no record is fabricated or auto-expanded
- **AND** the owner can still select any listed record by click or j/k

#### Scenario: Degraded digest names itself instead of a calm empty state

- **WHEN** `meta.decisions_available` is `false`
- **THEN** the list area renders a named Decisions degraded note instead of
  `No decisions waiting.`

### Requirement: Recorded Decision Intent State Is Visible

When a selected decision carries a recorded intent, its inline detail SHALL render the intent's
chosen option and an honest status line: `pending` and `applying` read as recorded and awaiting
application to the tracker, `applied` reads as applied, and `failed` reads as failed with its
categorical reason. A pending intent carrying a `last_error` SHALL name that the last attempt did
not complete. Until the controls amendment completes exact owner adoption and owned canonical integration,
this rendering SHALL remain read-only: it SHALL NOT add approve, deny, close, choose,
retry, or default-application controls, and a decision without an intent SHALL render exactly as
before. After those gates, recorded status itself SHALL remain read-only; a decision with no live
intent MAY expose a new explicit choice only under REQ-dashboard-decisions-003. A failed intent
SHALL NOT be treated as live, and a new choice SHALL NOT be labelled as force apply or a retry of
that failed intent. No control SHALL reset, cancel or mutate a recorded intent directly.

ID: REQ-dashboard-decisions-004
Source: REQ-owner-decision-desk-001/-002; merged Recorded Decision Intent State Is Visible; this candidate design D3/D4
Scope: v1-mandatory

#### Scenario: A pending intent is shown as awaiting application

- **WHEN** the owner selects a decision whose intent is `pending`
- **THEN** the detail names the chosen option and that it awaits application
- **AND** no mutation control is rendered

#### Scenario: A failed intent names its reason

- **WHEN** the owner selects a decision whose intent is `failed`
- **THEN** the detail names the chosen option, that applying it failed, and the categorical reason

## ADDED Requirements

### Requirement: Explicit Offered-Option Capture in Selected Decisions

After exact owner adoption and owned canonical integration of the controls amendment, the selected decision detail SHALL offer native labelled choose buttons in source option order only when the digest and recorded-choice source are readable, structured details are valid, the option satisfies the existing intent request bound, no live intent is known, and that row has no in-flight choice. Choosing SHALL send only the exact option text to the existing owner intent endpoint. The browser SHALL NOT invent, normalize, truncate or apply an option/default, assert an actor/source, use a connector callback token, or acquire tracker authority.
A fresh accepted choice SHALL render its server-confirmed `pending` receipt as recorded and awaiting application. An identical live repeat returning `200` with `meta.created=false`, or a conflicting choice returning `409` with `intent_conflict:<state>`, SHALL report Already handled and preserve or refresh the actual existing receipt; it SHALL NOT describe the losing choice as selected or applied. A known live intent SHALL render Already handled and no actionable choose control. An unrecognized conflict SHALL remain a named conflict, not fabricated receipt authority. Local in-flight suppression SHALL be per captured bead and SHALL NOT replace the existing durable one-live-intent gate.
A confirmed receipt SHALL remain visible within the same authenticated session when a subsequent read fails or lags, with a separate refresh warning. A previously confirmed live receipt SHALL NOT be overwritten by an older or null read and used to re-enable a choice. An unconfirmed POST timeout, disconnect or server failure SHALL name the outcome as unknown, SHALL NOT automatically replay a mutation, and SHALL offer read recovery before explicit owner retry. Late replies SHALL affect only their captured bead and current authenticated session. Auth teardown SHALL still clear protected caches and SHALL NOT revive authority from late replies.
Unavailable digest, structured details or intent state SHALL disable new choices and name the unavailable source while preserving usable navigation/context and any known export-as-of. A stale-but-available digest SHALL retain its age warning and existing exact-text validation; the UI SHALL NOT assert whole-snapshot or live-tracker admission guarantees that the endpoint does not provide. Refusals SHALL distinguish invalid/unoffered choice, authentication/forbidden state and source unavailability without claiming successful application. Controls SHALL preserve j/k triage, shared hints, safe detail/deep links and selected-only expansion, be keyboard-operable outside the selection button, show visible focus, and announce busy, result and error state. They SHALL add no implicit default, number, approve, deny, close, generic retry or Telegram action.

ID: REQ-dashboard-decisions-003
Source: heart-and-soul/vision.md (earned calm, Rule4); REQ-owner-decision-desk-001/-002; this candidate design D1-D5
Scope: v1-mandatory

#### Scenario: Fresh choose records a pending receipt

- **WHEN** the authenticated owner explicitly chooses an eligible displayed option and the endpoint returns `200` with `meta.created=true`
- **THEN** the captured decision shows that returned pending receipt as recorded and awaiting application
- **AND** the browser has sent only the exact option to the existing owner intent endpoint
- **AND** it does not claim the bead was closed or the option applied

#### Scenario: Identical repeat is already handled

- **WHEN** an identical choice returns `200` with `meta.created=false`
- **THEN** the captured decision reports Already handled with the returned existing receipt and status
- **AND** it does not create or display a second receipt or change pending to applied

#### Scenario: Conflicting live choice is already handled

- **WHEN** a different choice loses to a live intent and returns `409` with `intent_conflict:<state>`
- **THEN** the captured decision reports Already handled and preserves or refreshes the actual winning choice
- **AND** it does not claim the losing option was selected or applied

#### Scenario: Concurrent clicks and foreign winners preserve one choice

- **WHEN** requests race from this row, another browser tab or the existing Telegram path
- **THEN** this row suppresses additional local in-flight submission and resolves from the existing shared one-live-intent outcome
- **AND** the winner is reflected by its actual receipt while same-option replay or different-option conflict adds no second live choice

#### Scenario: Late response stays with the captured row and session

- **WHEN** a choice response arrives after selection changed to another decision or the owner session ended
- **THEN** it cannot alter that other decision or revive protected state after session teardown
- **AND** any still-authorized result belongs only to the captured bead and exact submitted option

#### Scenario: Unavailable choice sources preserve honest context

- **WHEN** the digest or recorded-choice source is unavailable, the query errors, or selected structured details are missing or malformed
- **THEN** no new choose control is actionable and the affected source is named
- **AND** usable rows, details, navigation and any known export-as-of remain available
- **AND** a degraded null intent is not described as no prior choice or a calm all-clear

#### Scenario: Unsupported option remains exact and unselectable

- **WHEN** a displayed option exceeds the existing intent endpoint's one-to-512-character request bound
- **THEN** it is named as unsupported for choice and cannot be submitted by that control
- **AND** it is not truncated, normalized, replaced by a default or granted a wider API bound

#### Scenario: Stale but available context retains current admission semantics

- **WHEN** a choice comes from a stale-but-available exported digest
- **THEN** the source age remains visible and recording uses the endpoint's current exact-option validation
- **AND** a still-offered exact string may be accepted while removed or invalid context is refused by its named reason
- **AND** the UI does not promise version-bound or live-tracker admission

#### Scenario: Invalid or unknown option names its refusal

- **WHEN** the owner intent endpoint returns `422` for an unoffered option, non-open decision, unavailable structured detail or malformed request
- **THEN** the captured decision names the refusal and offers read recovery
- **AND** no new receipt or Already handled/application claim is fabricated

#### Scenario: Denied owner authority changes no decision

- **WHEN** existing owner authentication, CSRF, Origin or scoped-route checks deny the request
- **THEN** the page presents the actual sign-in, forbidden or unavailable state and no admitted choice
- **AND** it does not use caller actor/source fields or connector credentials to manufacture owner authority
- **AND** it does not automatically replay the denied mutation

#### Scenario: Source refusal remains availability rather than success

- **WHEN** the endpoint returns `503` because the new-choice digest or intent pool is unavailable
- **THEN** the captured decision names source unavailability and offers read recovery
- **AND** it does not imply an accepted intent, successful application or no prior choice

#### Scenario: Confirmed choice survives a failed or lagging refresh

- **WHEN** a confirmed choice receipt is followed by a failed read or a lagging read without that intent
- **THEN** the confirmed receipt remains visible in the current authenticated session and refresh trouble is stated separately
- **AND** an older or null read does not reopen the live choice controls

#### Scenario: Unknown submission outcome is recovered without automatic replay

- **WHEN** a POST timeout, disconnect or server failure leaves admission unconfirmed
- **THEN** the captured decision states that the outcome is unknown and offers read refresh before explicit owner retry
- **AND** it performs no automatic mutation replay or fabricated success/failure receipt

#### Scenario: Pending and applying remain awaiting the tracker

- **WHEN** a known live receipt is pending or applying, including one with a categorical last-error reason
- **THEN** the selected detail states the actual option and awaiting/incomplete-attempt state with Already handled
- **AND** it exposes no actionable new choice, force-apply, cancel or generic retry

#### Scenario: Failed intent permits a fresh explicit choice

- **WHEN** the latest receipt is failed and the decision otherwise satisfies choice eligibility
- **THEN** the failure and its categorical reason remain visible and the owner may explicitly choose an offered option as a new intent
- **AND** the control does not mutate or silently replay the failed intent

#### Scenario: Reopened applied decision preserves the current limitation

- **WHEN** an exported open decision still carries an applied live receipt under the existing protocol
- **THEN** it shows that receipt and Already handled without admitting a replacement choice
- **AND** it does not invent superseding, release, cancellation or reopen authority

#### Scenario: Pointer and keyboard share a discoverable choose path

- **WHEN** the owner uses pointer or Tab followed by Enter or Space on an eligible option button
- **THEN** both invoke the same explicit choose behavior through native labelled controls outside the row-selection button
- **AND** focus is visible and busy, result and error state is announced
- **AND** j/k selection, shared footer/help hints, selected-only expansion, current deep links and same-origin detail links remain usable

## Source References

- Non-Negotiable Rule4 (deterministic infrastructure) and earned calm from vision.md
- RFC0025 Development Tracker Write Bridge amendment
- REQ-owner-decision-desk-001/-002 and adopted owner authentication/CSRF contract
```
## Risks / Trade-offs

- **Stale exported context:** admission validates a current digest rather than live tracker state or an immutable options version. Keep the age plaque; applier revalidation can fail a previously accepted pending choice. No stronger promise is made.
- **Confirmed choice with lagging read:** local acknowledgement is needed to avoid a second apparent action; it must remain row/session-bound and must not outlive owner authority.
- **Existing applied intent on reopened bead:** the current live index blocks replacement choice. `bu-c58oek` owns superseding semantics; display the limitation honestly.
- **Unreadable stranded applying intent:** current reconciliation can remain applying; `bu-watgme` owns bounded settlement. Controls must not force retry or infer failure/success.
- **Shared Postgres applier authority:** `bu-3leptg` owns a dedicated role/chart change. This UI adds no grants and cannot attest live least-privilege posture from source.
- **Defaults:** operations prose suggests deadline default application, while the bridge excludes it and `bu-vbx9wt` requires owner policy. This candidate never applies a default or treats that prose as consent.
- **Concurrent active contracts:** native strict/overwrite checks cannot approve an unowned integration or prove semantic compatibility by themselves. GateG2 in `integration.md` includes exact baseline, scenario, ID and both-prohibition checks.

## Migration and rollback limits

No migration or deployment occurs in this candidate. Integration first applies the adopted exact clauses under source allocation; implementation follows only when its bead is released. Source landing still requires its own authorization. A future UI rollback may remove controls but cannot delete/reset accepted intents, cancel effects, release applied live slots, widen callback routes or revert owner authentication. No new feature flag or rollback grant is implied.

The existing `.3` owner/operator retains deployment and actual-applier smoke authority. A separately authorized test-labelled synthetic decision must not unblock operational/product/owner work. Preserve the original smoke outcome under `verification.md`; inability to run it leaves that acceptance pending.

## Open operational facts

[Unknown] exact deployed image/head, bridge migration availability, fresh export and enabled actual applier. [Unknown] exact dev-instance/TEST-bead/option/effect authorization. These are named operator handoff prerequisites, not choices omitted from mandatory behavior or reasons to weaken the original smoke. No live queries, credential reads, enrollment, real decisions or smoke were performed. All normative choices are concrete in this candidate; only exact review/adoption/integration and operational evidence remain external gates.
