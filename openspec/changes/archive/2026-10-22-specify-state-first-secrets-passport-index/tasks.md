## 1. Spec artifact (this change)

- [x] 1.1 Amend the `butler-secrets` requirement `Passport-Book Information Architecture`:
      replace the family-first spine order with the five state-first groups (`needs-hand`,
      `in-progress`, `stale`, `ready`, `not-set`) and add the exact `CredentialState`-to-group
      mapping, verified directly against `frontend/src/components/secrets/passport/types.ts`.
- [x] 1.2 Restate the full requirement (all scenarios) in the `MODIFIED Requirements` block per
      this repo's overwrite-guard convention (`AGENTS.md` § "Two unarchived OpenSpec changes can
      silently overwrite each other"), changing/adding only the targeted scenarios.
- [x] 1.3 Add scenarios covering mixed families, transient states, urgent severity, all five
      empty-group cases, search order, keyboard order, identity switching, and the accessible
      family qualifier, per `bu-k87os` acceptance criterion 4.
- [x] 1.4 Add the `Spine Sort Control And Recency Resolution` requirement: recency removal, the
      `alpha`/`severity`-only sort surface, the legacy `?sort=recency` fallback, and the
      content-blind, ephemeral real-inventory comparison method (`bu-k87os` AC3, AC5).
- [x] 1.5 Record the coexistence of `openspec/changes/make-operator-status-readers-distinguish-
      unavailable` and `openspec/changes/repair-secrets-authority-projections` as unarchived
      sibling changes touching the same `butler-secrets` spec file, in `proposal.md`.
- [x] 1.6 Confirm no other unarchived change carries a `## MODIFIED Requirements` block for
      `Passport-Book Information Architecture` or a `Spine Sort Control` requirement
      (`rg -l '^### Requirement: <Name>$' openspec/changes/*/specs/*/spec.md`).

## 2. Validation (this change)

- [x] 2.1 `openspec validate specify-state-first-secrets-passport-index --strict`. Passes on 2026-10-22 (coordinator r38s, bu-u5euwb.6).
- [x] 2.2 `python3 scripts/check_spec_overwrites.py` — no unfrozen baseline losses. Passes on 2026-10-22: no unfrozen baseline losses.
- [x] 2.3 `python3 scripts/check_countable_tasks.py`. Passes on 2026-10-22: all 80 unarchived changes carry countable task lines.
- [x] 2.4 `make check-guards`. Passes on the archive PR head (bu-u5euwb.6).
- [x] 2.5 Independent UX/spec review with zero unresolved threads (`bu-k87os` acceptance
      criterion 6). Done: independent review bu-wbst4z (rev-4907), PR #4914, zero unresolved threads.

## 3. Owner gate (blocks all future work)

- [x] 3.1 Obtain exact owner approval of this spec artifact (`bu-k87os` acceptance criterion 7).
      No frontend, telemetry, inventory, API, or deployment change may land before this step. Satisfied by the recorded owner decision bu-6u5lxd, choice A: adopt the exact artifact at PR #4080 head dd5189061589d60aff2f97ac9f58d3483f8ffeff (see bu-k87os notes).

## 4. Future bounded implementation (blocked on Task 3, out of this bead)

- [x] 4.1 `frontend/src/components/secrets/passport/constants.ts`: reclassify `rotating` out of
      `NEEDS_HAND_STATES` into a new `IN_PROGRESS_STATES` set; add matching `STALE_STATES` /
      `READY_STATES` / `NOT_SET_STATES` (or an equivalent state→group map); adjust
      `STATE_CATALOG.rotating.tone` to `"dim"` / `sliver: false`. Done as the equivalent state-to-group map `SPINE_GROUP_BY_STATE`: constants.ts landed in PR #4165 (4d901078f, bu-6u5lxd) and verified on main 2026-10-22; vitest src/components/secrets passes (34 files, 570 tests).
- [x] 4.2 `frontend/src/components/secrets/passport/spine-builder.ts`: remove `lastTouchOrder`;
      implement the four-key within-group tie-break comparator (severity, family, label, focus
      key). Done: spine-builder.ts landed in PR #4165 (4d901078f, bu-6u5lxd) and verified on main 2026-10-22; vitest src/components/secrets passes (34 files, 570 tests).
- [x] 4.3 `frontend/src/components/secrets/passport/types.ts`: narrow `SpineSortMode` to
      `"severity" | "alpha"`; remove `SpineEntry.lastTouchOrder`. Done: types.ts landed in PR #4165 (4d901078f, bu-6u5lxd) and verified on main 2026-10-22; vitest src/components/secrets passes (34 files, 570 tests).
- [x] 4.4 `frontend/src/components/secrets/passport/Spine.tsx`: render five groups from the new
      state→group map instead of three; remove `recency` from `SORTERS` and `SortPicker`; add the
      per-row accessible family qualifier. Done: Spine.tsx landed in PR #4165 (4d901078f, bu-6u5lxd) and verified on main 2026-10-22; vitest src/components/secrets passes (34 files, 570 tests).
- [x] 4.5 `frontend/src/components/secrets/passport/DirectionPassport.tsx`: fall back an
      unrecognized `?sort=` value (including legacy `recency`) to `"severity"` without rewriting
      the URL. Done: DirectionPassport.tsx landed in PR #4165 (4d901078f, bu-6u5lxd) and verified on main 2026-10-22; vitest src/components/secrets passes (34 files, 570 tests).
- [x] 4.6 Update the passport test suite (`Spine.roving.test.tsx`, `secrets-fe5.test.tsx` and its
      snapshot, `spine-duplicate-key.test.tsx`, and any test asserting the old three-family group
      render) to the new five-group shape; add tests for the tie-break chain, the legacy
      `?sort=recency` fallback, and the accessible family qualifier. Done: the passport test suite landed in PR #4165 (4d901078f, bu-6u5lxd) and verified on main 2026-10-22; vitest src/components/secrets passes (34 files, 570 tests).
- [x] 4.7 File a new bead for this implementation step; do not fold it into `bu-k87os`. Done: the implementation was filed and closed as bu-6u5lxd.
