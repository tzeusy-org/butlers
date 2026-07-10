# Tasks

## 1. Spec authoring

- [x] 1.1 Add `Filter reason for discretion IGNORE` scenario to the
      `Filtered Event Persistence (Batch Flush)` requirement with the seven
      canonical `<kind>` values from `classify_ignore_kind`.
- [x] 1.2 Add the `Filtered-Content Privacy Tier` requirement (filtered =
      bounded preview + `raw={}`; error = exempt; replay = best-effort).
- [x] 1.3 Reconcile `Filtered Events Table` and `Full Payload Shape`
      requirement text so the "full payload for replay" language no longer
      contradicts the privacy tier (union-carry all still-true scenarios).
- [x] 1.4 Add `## Source References` footer grounding the tier in
      `security.md` (Sensitive Data Categories / metadata-tier precedent).

## 2. Telegram user-client compliance (contained)

- [x] 2.1 Change the three `filtered`-status persistence sites in
      `_process_message` to persist `raw={}` (connector-rule block,
      global-rule skip, discretion IGNORE).
- [x] 2.2 Leave the `error`-status persistence site unchanged (retains raw).
- [x] 2.3 Add unit tests: policy-block and global-skip persist `raw={}` with a
      bounded preview; error status retains full raw.
- [x] 2.4 Update the two stale cross-reference comments in
      `test_discretion_ignore_persistence.py` that assert Telegram persists
      full raw.

## 3. Verification

- [x] 3.1 `openspec validate filtered-events-privacy-tier --strict` is green.
- [x] 3.2 `ruff check` + `ruff format --check` clean on touched files.
- [x] 3.3 Targeted pytest green for the touched connector + live-listener tests.
- [x] 3.4 `openspec archive filtered-events-privacy-tier --yes` applies the
      deltas to `openspec/specs/connector-filtered-events/spec.md` in the same
      PR.

## 4. Follow-up (reported, not in this PR)

- [ ] 4.1 Bring the remaining connectors into compliance with the
      Filtered-Content Privacy Tier: Gmail, Discord, Google Calendar, Google
      Health, Spotify, Telegram bot each still persist full raw for
      `filtered`-status content. File as a separate bead (discovered-from
      this change).
