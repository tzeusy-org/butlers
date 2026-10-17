# Owned canonical integration and release gates

This artifact is a candidate, not adoption or application of capability deltas. Its complete payload is the fenced Markdown in `design.md`. The existing main spec and foreign merged active artifact remain authoritative and untouched. No new ADDED requirement shadows either prohibition. `skip_specs:true` is deliberate for the present source-only documentation deliverable; a future actual controls amendment must use normal spec deltas and cannot retain that marker to bypass specification work.

## G1: Independent review and exact owner adoption

The coordinator obtains fresh independent semantic/security/UX review of the full immutable candidate commit/tree, including baseline hashes and all24 payload scenarios. Author self-check, passing guards, PR status or prior `.3`/PR4495 decisions do not meet this gate. The owner chooses whether to adopt the exact candidate including native explicit option controls, source-unavailable disabling, same/different replay distinction, row/session-bound confirmed acknowledgement, and unknown outcome/no automatic replay. Silence leaves both existing read-only prohibitions in force. Adoption identity/date/head/tree must be recorded before implementation release.

## G2: Explicitly allocated integration of both prohibitions

The current foreign `.3` contract/rollout owner must allocate source integration explicitly. This lane changes none of that owner's adopted package and does not archive task4.2 or4.3. Integration cannot be completed by copying only REQ-dashboard-decisions-003 or adding inert parallel text.

The allocated integrator shall perform these exact bounded source changes from the revalidated baseline:

1. Apply the entire proposed same-name **Read-Only Decision Detail and Selection** block in the fenced payload to canonical `openspec/specs/dashboard-decisions/spec.md`, retaining its four unrelated scenario bodies byte for byte. The affected **Valid detail remains read-only** heading stays unchanged; its WHEN condition explicitly covers pre-adoption/integration instead of silently deleting or renaming that guarantee. The new scoped controls permission and its cross-reference are explicit.
2. Resolve the merged `owner-decision-desk-write-bridge/specs/dashboard-decisions/spec.md` **Recorded Decision Intent State Is Visible** block under that owner's allocation. If its already-merged added requirement is first synced, use a full MODIFIED replacement of that new canonical block; if the owner allocates a direct bounded active-block correction, apply the complete proposed replacement there and later sync that corrected block. Preserve both original scenario names and bodies. Do not stage a second ADDED requirement with the same name. Native pending-parent syntax alone is not source allocation or safe application order.
3. Add the exact **Explicit Offered-Option Capture in Selected Decisions** block only as part of the coordinated integration that resolves items1/2. Recheck free ID reservations across main, every active change and every open PR complete artifact (including fenced future payloads): proposed002 for the legacy detail backfill,003 for new controls and004 for the legacy recorded-state backfill. Existing backend REQ-owner-decision-desk-001/-002 and other requirement IDs remain unchanged. If any proposed number was allocated meanwhile, stop integration for exact revised-artifact review/adoption rather than silently changing the approved payload.
4. Replace the canonical Purpose with the exact proposed text below. This is a direct main-spec purpose amendment only under the same explicit allocation/adoption; it cannot be represented as an ignored delta Purpose or a claim of observed existing controls.
5. Resolve any still-open same-name delta at the refreshed baseline before applying. Audit the complete canonical+active composition for contradictory no-choose clauses, scenario/ID loss and changed unrelated API/writer authority. Retain original delta files/history according to the owning change's lifecycle; no unchecked rollout task may be ticked or archived as complete.

### Exact proposed canonical Purpose

> The Decisions lane makes open owner decision beads visible as a source-honest dashboard surface backed by the exported Beads snapshot and recorded intent state. After exact controls amendment adoption and owned canonical integration, it permits the authenticated owner to record an explicitly offered option through the existing intent API; tracker application remains with the existing management workload, and the digest itself remains read-only.

G2 receipt must bind the exact integrated commit/tree, the revalidated baseline hashes, all preserved unrelated scenarios, both complete replacements, Purpose and added controls block; strict OpenSpec, body-loss/overwrite and countable-task proof are necessary hygiene. Independent semantic compatibility review must confirm no prohibition was merely shadowed. If integration changes the adopted candidate's substantive choices, obtain renewed exact adoption. G1 and G2 are both necessary; either alone leaves bu-tku4df non-runnable. Source landing is separately authorized and is not achieved by this draft PR.

## G3: Source implementation and separate operation

Only after G1/G2 and coordinator release does original bu-tku4df own its bounded page/client/query/test outcome. No feature code is included here. The `.3` owner/operator keeps existing deployment/image/migration/export/applier readiness. The actual applier dev smoke remains in the original acceptance and `verification.md`, with an explicit exact-instance/TEST-bead/option/effect authorization. No enrollment/login, deployed job triggering, source activation or real decision follows from contract adoption or source-only landing.

Telegram activation remains bu-6es2sp and is unnecessary for dashboard-only smoke. Dedicated applier Postgres role bu-3leptg, reopened intent bu-c58oek, unreadable reconciliation bu-watgme, token hardening bu-2hlwhh, default policy bu-vbx9wt and re-prompt bu-omyol5 keep their existing boundaries. Serialize only actual shared-source/runtime verification overlap; do not convert all siblings into invented universal blockers.

## Release state at publication

| Gate | State |
| --- | --- |
| Candidate authored and source checks | Reported against exact pushed head; does not imply adoption |
| Fresh independent semantic/security/UX review | Pending; coordinator arranges it |
| G1 exact owner adoption | Pending |
| G2 explicit source allocation and canonical integration | Pending; current prohibitions remain in force |
| Original implementation release | Blocked on G1 andG2 plus normal coordination |
| Actual dev applier/export evidence/authority | Pending separately with `.3` owner/operator |
| Source landing, deployment or Telegram activation | Not authorized by this candidate |
