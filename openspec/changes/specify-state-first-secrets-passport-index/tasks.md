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

- [ ] 2.1 `openspec validate specify-state-first-secrets-passport-index --strict`.
- [ ] 2.2 `python3 scripts/check_spec_overwrites.py` — no unfrozen baseline losses.
- [ ] 2.3 `python3 scripts/check_countable_tasks.py`.
- [ ] 2.4 `make check-guards`.
- [ ] 2.5 Independent UX/spec review with zero unresolved threads (`bu-k87os` acceptance
      criterion 6).

## 3. Owner gate (blocks all future work)

- [ ] 3.1 Obtain exact owner approval of this spec artifact (`bu-k87os` acceptance criterion 7).
      No frontend, telemetry, inventory, API, or deployment change may land before this step.

## 4. Future bounded implementation (blocked on Task 3, out of this bead)

- [ ] 4.1 `frontend/src/components/secrets/passport/constants.ts`: reclassify `rotating` out of
      `NEEDS_HAND_STATES` into a new `IN_PROGRESS_STATES` set; add matching `STALE_STATES` /
      `READY_STATES` / `NOT_SET_STATES` (or an equivalent state→group map); adjust
      `STATE_CATALOG.rotating.tone` to `"dim"` / `sliver: false`.
- [ ] 4.2 `frontend/src/components/secrets/passport/spine-builder.ts`: remove `lastTouchOrder`;
      implement the four-key within-group tie-break comparator (severity, family, label, focus
      key).
- [ ] 4.3 `frontend/src/components/secrets/passport/types.ts`: narrow `SpineSortMode` to
      `"severity" | "alpha"`; remove `SpineEntry.lastTouchOrder`.
- [ ] 4.4 `frontend/src/components/secrets/passport/Spine.tsx`: render five groups from the new
      state→group map instead of three; remove `recency` from `SORTERS` and `SortPicker`; add the
      per-row accessible family qualifier.
- [ ] 4.5 `frontend/src/components/secrets/passport/DirectionPassport.tsx`: fall back an
      unrecognized `?sort=` value (including legacy `recency`) to `"severity"` without rewriting
      the URL.
- [ ] 4.6 Update the passport test suite (`Spine.roving.test.tsx`, `secrets-fe5.test.tsx` and its
      snapshot, `spine-duplicate-key.test.tsx`, and any test asserting the old three-family group
      render) to the new five-group shape; add tests for the tie-break chain, the legacy
      `?sort=recency` fallback, and the accessible family qualifier.
- [ ] 4.7 File a new bead for this implementation step; do not fold it into `bu-k87os`.
