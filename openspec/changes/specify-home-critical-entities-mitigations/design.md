## Context

Baseline: `origin/main` on 2026-11-15, with the departure-fix substrate merged (bu-jv1moa.114
resolver, bu-jv1moa.115 fixes and replay gate, bu-jv1moa.116 arrival card).

What exists and is reused unchanged:
- RFC 0028: the `(domain, service)` risk map (`roster/home/modules/actuation.py`), unknown is
  `protected`, consequential and protected calls park, the approved replay is bound to the executor
  task, tool and canonical argument digest, and a receipt in `home.ha_command_log` settles to
  `succeeded`, `failed` or `unverified` from a live read-back. The same approval never retries an
  `unverified` or `attempting` receipt.
- bu-svqpzo.12: `effective_actuation_risk` runs a `reversible` call unprompted only for explicit
  same-domain entity targets with allowlisted, bounded data keys; anything else becomes
  `consequential`.
- bu-jv1moa.2: approval cards show the exact stored call and bind `args_digest` (sha256 of the
  canonical JSON, `butlers.modules.approvals.previews.args_digest`).
- bu-jv1moa.6: the safety sentinel (`src/butlers/jobs/home_safety.py`) keeps `home:safety`
  conditions for owner-declared `home.safety_watch` sensors (leak, smoke, CO, gas) and sends a
  high-priority owner alert straight through the Switchboard. It has no actuation path.
- bu-dhloi1.3: a model cannot choose high priority; only authenticated deterministic safety
  producers, owner-authored break-through rules and owner-flagged obligations bypass quiet hours.
- bu-jv1moa.29 substrate: `park_prepared_action` (`origin='prepared'`, durable and silent),
  `replay_refusal` consulted by `_call_service` before any receipt or request, the system-actor
  withdrawal through `transition_pending_action`, and `prepared_action_status` with
  `public.resolve_home_action_status` (core_337).
- RFC 0038's capability broker reads RFC 0028's map through `rfc0028_actuation_risk`
  (`src/butlers/capability_broker/actuation.py`); consequential and protected always need a
  per-event approval.

Non-goals: marking any entity critical; any tapless or automatic actuation; any change to the
sentinel's alert (priority, text, timing, lane); a Telegram approve button (gate G6); an immediate
lane (gate G1); a new approval status; any mitigation for the away-safety producers (freezer, away
door, sensing-blind), which stay read-only.

## Outcome map

| Outcome | Owner of the data | Contract | Verification seam (existing file, extend) |
|---|---|---|---|
| Critical registry | Home: `home.critical_entities` | REQ-module-home-assistant-001 | new `tests/api/test_home_critical_entities.py` (mirrors `test_home_safety_watch.py`) |
| Critical calls park; fail-closed | Home `_call_service` gate; broker adapter | REQ-module-home-assistant-002 | `tests/capability_broker/test_actuation_risk.py`, `tests/modules/test_module_home_assistant.py` |
| Pre-call state, target binding | Home `_call_service`; receipt | REQ-module-home-assistant-003 | `tests/modules/test_module_home_assistant.py`, `tests/modules/approvals/test_prepared_execution.py` |
| `already_satisfied` | receipt; resolver | REQ-module-home-assistant-004; REQ-home-deterministic-jobs-011 follow-up delta | the two files above, `tests/migrations/test_core_337_home_action_status.py` |
| Mitigation declaration and parking | Home: `home.safety_mitigations`; approvals row | REQ-home-deterministic-jobs-013 | `tests/jobs/test_home_safety_sentinel.py`, new `tests/api/test_home_safety_mitigations.py` |
| Mitigation premise, cancellation | shared replay gate; approvals transition | REQ-home-deterministic-jobs-014 | `tests/jobs/test_home_departure_fixes.py` (gate dispatch), `tests/jobs/test_home_safety_sentinel.py` |
| One physical executor | Home `_call_service` | REQ-home-deterministic-jobs-015 | a contract test in `tests/contracts/` that no job module posts to Home Assistant |

## Critical-entity registry (REQ-module-home-assistant-001)

`home.critical_entities`: one row per explicit entity id (`[a-z0-9_]+\.[a-z0-9_]+`), an owner label,
and who and when added it. It is written only by owner-only dashboard routes (`GET/PUT/DELETE
/api/home/critical-entities`), the same authentication as `departure_fixes` (decided D7 applies).
No MCP tool, conversation, goal, standing rule or job writes it. Membership is never inferred from
a Home Assistant label, area, device class, attribute or `friendly_name`, and the owner label is
display text only: nothing reads authority from it. A refused write stores nothing and answers with
one fixed code. Every add and remove is audited by entity id and actor, not by label.

Removing an entry is an owner act and takes effect for calls evaluated afterwards. An approval
already parked as critical keeps its park; removal never turns a parked call into an unprompted
one.

## Critical calls always park (REQ-module-home-assistant-002)

At evaluation, `_call_service` reads the registry in the same call (no cache that outlives the
call). Then:

- A call whose explicit target names a critical entity has effective risk at least
  `consequential`, whatever its base class, and parks. A `protected` call stays `protected`.
- A call whose target is not an explicit entity list (area, device, label, `all`, data-carried
  selectors, absent) already parks under bu-svqpzo.12. While the registry is non-empty it is also
  treated as possibly critical, so no rule below may approve it.
- If the registry cannot be read (table missing, query error, timeout), every call is treated as
  naming a critical entity: nothing runs unprompted, and every park is marked critical.
- A critical park is excluded from every approval shortcut: standing rules, decision-memory
  suggestions, autonomy suggestions and RFC 0038 capability grants. Each critical call needs its own
  owner approval of that stored call.
- The capability broker adapter reads the registry too. A goal call naming a critical entity is
  `consequential`; an unreadable registry is `unknown`. Either parks per event.
- `safe` calls (`homeassistant.update_entity`) are reads and are not promoted.

The park's `why` gains the fixed category `critical_entity`. It never includes the owner label or a
value.

## Pre-call state and target binding (REQ-module-home-assistant-003)

This applies to every approved replay whose target names a critical entity (or any call while the
registry is unreadable) and to every safety mitigation. After the replay premise gate and before the
receipt claim:

1. The target must be an explicit list of entities, each of which is one explicit entity of the
   stored call. A data-carried selector is refused as ambiguous, as today.
2. Home reads each target entity live from Home Assistant (`GET /api/states/<entity>`), not from
   the websocket cache. The read must finish no more than 10 seconds before the receipt claim
   (decided default, D8). A failed read, a missing entity, a state of `unavailable` or `unknown`, or
   an older read sends nothing.
3. The read set must equal the stored target set exactly; a partial read sends nothing.
4. A refusal writes no receipt, sends no request, leaves the approval `approved` (status
   `approved_not_run`) and records one approval event with a content-blind reason
   (`pre_state_unavailable`, `pre_state_stale`, `target_unbound`).
5. On success the pre-call state (state and the attributes the post-condition verifier reads) is
   stored on the receipt as `pre_state`, beside `requested_state` and `observed_state`.

The same approval may be replayed again after a refusal, because no request left the process.

## `already_satisfied` (REQ-module-home-assistant-004)

If the pre-call state already meets the call's declared post-condition (`verify_post_condition`
against the pre-state), Home sends nothing and writes a receipt that settles directly to
`already_satisfied`: no `attempting` phase, `pre_state` recorded, no `observed_state` claim. It is
not `succeeded` and it claims no effect. A service with no
declared verifier can never be `already_satisfied`; it proceeds to the normal attempt and, as
today, settles `unverified`.

After a request leaves, nothing changes: `succeeded` needs a matching live read-back; a mismatch or
unreadable read-back is `unverified`; HA rejection is `failed`. A mitigation whose post-condition
fails therefore reads "unclear, check the item", never "done".

### Resolver follow-up (REQ-home-deterministic-jobs-011)

The resolver's closed set is defined by REQ-home-deterministic-jobs-011 in
`specify-home-departure-fixes-arrival`, which is not archived yet. A second active definition of the
same ID is ambiguous to the citation gate, so this change does not modify it. Under the current text
an `already_satisfied` receipt reads `unknown` and a mitigation withdrawal reads `rejected`; neither
reads as success, so nothing here is unsafe while the follow-up waits. Once that change is
archived, S-H files one MODIFIED delta of REQ-home-deterministic-jobs-011 that adds
`already_satisfied` to the closed set (receipt-decided, never `succeeded`, card text "already that
way, nothing sent") and maps a `system:home-safety-mitigation` withdrawal to `cancelled`. S-I
depends on that delta, so no mitigation reaches the owner while its withdrawal reads `rejected`.

## Safety mitigations (REQ-home-deterministic-jobs-013)

`home.safety_mitigations`: for one `safety_watch` sensor, one exact `(domain, service)`, one explicit
target entity and allowed data keys. A sensor may have at most one mitigation per target entity. The
allowlist is closed and holds only pairs with a declared post-condition verifier: `switch.turn_off`
and `fan.turn_off` today (no data keys), plus `valve.close_valve`, which S-I adds to the risk map as
`protected` (it is unknown, so `protected`, today; this tightens nothing and loosens nothing) with
the verifier "state is `closed`". Any later pair needs its own RFC 0028 classification and verifier.
The mitigation's target is always treated as critical for that call, whether or
not the owner also listed it in the registry. Owner-only routes, content-blind refusals and D7 apply.

Refused at declaration: an area, device, label, `all`, wildcard or multi-entity target; a data key
outside the pair's allowlist; a data-carried selector; a pair without a declared post-condition
verifier; a sensor not in `safety_watch`. There is no "secure all" or "shut everything" mitigation:
each target is its own action, approval and receipt.

When the sentinel opens a `home:safety` condition (open, not escalation or re-send), it first sends
the alert exactly as today. Separately, after that attempt and in its own transaction, it parks one
prepared action per declared mitigation for that sensor: `origin='prepared'`, dedup key
`home:safety-mitigation:<condition id>:<target entity>`, the exact stored call, `blast_radius`
`self`, reversibility from the RFC 0028 class. A parking failure is logged by outcome and never
delays, alters or retracts the alert. The job never calls Home Assistant.

The alert text gains at most a fixed pointer ("a fix you set up is waiting on the dashboard"). Whether
that pointer may appear in a high-priority alert is part of G3; until it is answered the alert text is
unchanged and the pointer appears only on the dashboard. Approval is dashboard-only (G6).

## Mitigation premise and cancellation (REQ-home-deterministic-jobs-014)

The replay gate that `_call_service` consults becomes one dispatcher on the dedup-key prefix:
`home:departure-fix:` keeps its leave-edge premise unchanged; `home:safety-mitigation:` gets:

- **Premise.** Holds only while the originating `home:safety` condition is still active (not
  resolved) in `public.owner_conditions`. Resolved, missing or a different condition id: refuse. An
  unreadable ledger: refuse (actuation fails closed).
- **Digest recheck.** The stored call's `args_digest` must equal the digest of the current
  declaration's call. An edited or removed declaration refuses (`declaration_changed`,
  `mitigation_removed`).
- A refusal sends nothing and writes no receipt, as for fixes.

Pending mitigations are withdrawn by the sentinel's next run with system actor
`system:home-safety-mitigation` and reasons `condition_resolved`, `declaration_changed`,
`mitigation_removed` or `watch_removed`. An unreadable ledger leaves them pending. A withdrawn
mitigation reads `cancelled` once the resolver follow-up lands. A prepared mitigation expires after 2 hours if not approved (decided
default, D9); the approvals sweep expires it.

Approved-but-unrun mitigations cannot be rejected (dashboard abandon only), so the replay premise and
digest recheck are the only guards on an old approval, as for fixes. A receipt is never edited, and
no text says a mitigation was undone.

## One physical executor (REQ-home-deterministic-jobs-015)

- The Home module's `ha_call_service` handler under the approvals executor is the only code that
  sends a Home Assistant service request. Departure fixes, mitigations and any later prepared-call
  consumer park through `park_prepared_action` and never post themselves.
- One replay gate (the dispatcher above), one status resolver (`prepared_action_status` and
  core_337) and one receipt ledger serve every consumer.
- Implementing owner of the shared substrate: the mitigation slice (S-I below) generalizes
  `replay_refusal` from bu-jv1moa.115 into the prefix dispatcher; the critical gate (S-G) owns the
  registry read inside `_call_service`. Graph edges: S-I depends on S-G and S-H; S-H depends on S-G.
- Comfort sessions (`specify-home-comfort-sessions`) and any later actuating consumer must use the
  same executor; this change does not specify them.

## Scope: alert versus action

The sentinel's alert is a read-only, deterministic, high-priority message. A mitigation is a
consequential physical action that waits for the owner. They share a condition id and nothing else:
the alert never waits on, depends on or reports a mitigation; a mitigation never runs because an
alert was sent; and missing approval or missing sensor evidence never becomes success.

## Proposed RFC 0028 Amendment 1 (OWNER ADOPTION REQUIRED, NOT ADOPTED)

The text below is proposed exact wording for `about/legends-and-lore/rfcs/0028-home-physical-actuation-contract.md`.
It is not in the RFC and binds nothing until the owner adopts it verbatim or as edited.

> ## Amendment 1: Critical entities and approved safety mitigations
>
> **Status:** Proposed (owner adoption required). **Date:** 2026-11-15.
>
> 1. The owner MAY mark explicit Home Assistant entities critical in a Home-owned registry. Only the
>    authenticated owner, through an owner-only dashboard route, adds or removes an entry. No model
>    session, tool argument, goal, capability grant, standing rule, Home Assistant label, area,
>    attribute or name adds, removes or implies an entry.
> 2. The `reversible` class runs without approval only for a call whose target is an explicit list
>    of entities, none of which is critical. A call naming a critical entity is at least
>    `consequential` and parks, whatever its base class. While any entity is critical, a call whose
>    target is not an explicit entity list is treated as naming one. No standing rule, decision
>    memory, suggestion or capability grant approves a critical call; each needs its own owner
>    approval of the stored call.
> 3. If the registry cannot be read, every call is treated as naming a critical entity.
> 4. Before an approved call naming a critical entity, or an approved safety mitigation, leaves the
>    process, Home reads every target entity live from Home Assistant. If a read fails, the entity is
>    missing, `unavailable` or `unknown`, the read set differs from the stored target, or the read is
>    more than 10 seconds old at the receipt claim, no request is sent and the approval stays unrun.
>    The pre-call state is kept on the receipt.
> 5. If the pre-call state already meets the call's declared post-condition, no request is sent and
>    the receipt settles `already_satisfied`. `already_satisfied` is not `succeeded` and claims no
>    effect. A service with no declared post-condition is never `already_satisfied`.
> 6. An approved safety mitigation is an owner-declared exact call for one explicit entity, parked
>    when a deterministic safety condition opens, and replayed only while that condition is still
>    open and the owner's declaration still digests to the stored call. There is no all, area,
>    device, label, wildcard or multi-entity mitigation and no action that secures several items.
>    Mitigations never run without an owner approval.
> 7. The risk map, unknown-is-protected, receipt fencing, and the rule that `unverified` is never
>    success are unchanged. The receipt now settles to exactly one of `succeeded`, `failed`,
>    `unverified` or `already_satisfied`.

## Owner gates

Not adopted, not written as requirements:
- **G7 (new). Adopt Amendment 1** above, verbatim or edited. Until then REQ-module-home-assistant-001
  to 004 and REQ-home-deterministic-jobs-013 to 015 are proposed only and no implementation bead may
  start. This includes the `already_satisfied` outcome and the 10-second bound in item 4.
- **G3 (existing, overlaps).** Safety consumers while away. Two points here touch it and are not
  decided: whether a high-priority safety alert may carry the mitigation pointer, and whether a
  mitigation may ever run faster than a dashboard approval (for example tapless while away). This
  change keeps the alert unchanged and every mitigation approval-only.
- **G6 (existing, overlaps).** A Telegram approve button for a parked fix. The same question applies
  to mitigations. Until answered, approval is dashboard-only.

Decided engineering defaults (reversible by a later change, not owner gates):
- **D7** (from bu-jv1moa.29) applies: registry entries and mitigations are written by the dashboard
  owner only.
- **D8.** Pre-call freshness bound 10 seconds, live read, not cache (also in Amendment 1 item 4, so
  adoption confirms it).
- **D9.** A prepared mitigation expires 2 hours after parking if not approved.
- **D10.** Mitigations are offered for `home:safety` roles only (leak, smoke, CO, gas), not for the
  away-safety producers.

## Proposed test cases

One behavior gate per invariant, extending the owners in the outcome map. Every case asserts zero
Home Assistant service POSTs unless the case is an approved, fresh, unsatisfied replay.

| Invariant | Case |
|---|---|
| Critical target parks | `light.turn_on` on a critical light parks with `critical_entity`; the same call on a non-critical light still runs unprompted |
| Area/all while registry non-empty | an approved-by-rule path refuses to auto-approve; parks |
| Owner-only changes | registry and mitigation routes refuse non-owner, tool and session callers; content-blind code; nothing stored |
| Unknown policy refusal | registry table missing or query error: a reversible explicit call parks; broker returns `unknown` |
| Shortcuts excluded | a standing rule pinning the exact critical call does not approve it; a goal grant does not |
| Stored-call digest | an edited mitigation declaration refuses replay (`declaration_changed`); the stored `tool_args` are byte-identical to park time |
| Stale or missing pre-state | read older than 10 s, failed read, `unavailable`, partial read set: no receipt, no POST, `approved_not_run`, one approval event |
| Replay | the same approval after a stale refusal and a later fresh read sends once; after `unverified` it never resends |
| Already-satisfied no-op | valve already closed: no POST, receipt `already_satisfied`, resolver never `succeeded` (`already_satisfied` after the follow-up delta), card text not "done" |
| Measured post-condition failure | HA accepts, read-back mismatches: `unverified`, never `succeeded` |
| Mitigation premise | condition resolved before approval: withdrawn `condition_resolved`; resolved after approval: replay refuses |
| Alert independence | parking raises: the alert is still sent unchanged |
| Single executor | contract test: no module under `src/butlers/jobs/` posts to `/api/services/` |

Test deltas belong to the implementation PRs. This planning change: Tests +0 ~0 -0.

## Successor packets

- **S-F. Adopt Amendment 1.** Owner act (G7). Held; no code.
- **S-G. Critical registry and gate (REQ-module-home-assistant-001, 002).** Deps: S-F. Home
  migration `critical_entities`, owner routes and Devices panel, the registry read and promotion in
  `_call_service`, the broker adapter, exclusion from standing rules, suggestions and grants,
  fail-closed. Dark: an empty registry changes nothing.
- **S-H. Pre-call state and `already_satisfied` (REQ-module-home-assistant-003, 004; the
  REQ-home-deterministic-jobs-011 follow-up delta).** Deps: S-G, and the archive of
  `specify-home-departure-fixes-arrival` for the delta. Receipt migration (`pre_state`,
  `already_satisfied` in the status CHECK), the live pre-read in `_call_service`, the resolver and a
  core migration replacing `resolve_home_action_status`.
- **S-I. Safety mitigations (REQ-home-deterministic-jobs-013, 014, 015).** Deps: S-G, S-H,
  bu-jv1moa.115 (merged). Home migration `safety_mitigations`, owner routes and panel, parking in the
  sentinel after the alert, the prefix replay dispatcher, cancellation, the single-executor contract
  test. Dark behind its own flag, off by default.
- **S-J. Telegram approve button for fixes and mitigations.** Blocked on G6 and bu-kqme7f.4; merge
  with S-E from bu-jv1moa.29 if that is filed. Held.
