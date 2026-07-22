## 1. Specification and eligibility primitives

- [x] 1.1 Add the shared pure provenance/legacy-all-day eligibility helper with
  conservative malformed metadata and timezone handling.
- [x] 1.2 Cover the helper's explicit-marker, legacy-midnight, valid-human, and
  malformed-input behavior with focused unit tests.

## 2. Calendar projection and ledger hygiene

- [x] 2.1 Carry Google date-only `all_day=true` through `CalendarEvent` parsing
  and provider projection without changing source/event upsert semantics.
- [x] 2.2 Add the exact three-key obsolete internal-source purge and internal
  roster-name validation while retaining unavailable-projection fail-open paths.
- [x] 2.3 Add focused module and source-ledger tests for date-only projection,
  exact cleanup, invalid-name rejection, and valid-name registration.

## 3. Context and radar behavior

- [x] 3.1 Filter the calendar context producer to the latest eligible active
  human meeting candidate and add named regression coverage.
- [x] 3.2 Filter radar candidates before every detector and add named coverage
  for overlap, back-to-back, overloaded-day, legacy rows, and malformed input.

## 4. Verification and handoff

- [x] 4.1 Run strict OpenSpec validation, focused unit and migrated tests, and
  touched-file formatting/lint checks.
- [x] 4.2 Review the diff for projection visibility, cascade preservation, and
  context-bus-producers compatibility; commit, push, and open the focused PR.

## 5. All-day reversible-mutation fidelity correction

- [x] 5.1 Carry nullable `all_day` through inverse update/create arguments,
  the update tool/model/action/approval payloads, and Google PATCH
  serialization without changing recurrence-instance semantics.
- [x] 5.2 Add update-undo and delete-undo regressions covering `all_day=true`
  plus date-only Google boundaries with no `dateTime` serialization.
- [x] 5.3 Re-run strict OpenSpec validation and focused calendar verification
  after the correction, then review the amended PR diff.

## 6. Metadata-only radar provenance correction

- [x] 6.1 Add behavior-executing conflict-radar coverage for a timed, unmarked
  `BUTLER:` provider row across overlap, back-to-back, and overloaded-day paths.
- [x] 6.2 Remove title-prefix candidate suppression so shared metadata/all-day/
  legacy provenance remains the sole radar exclusion authority.
- [x] 6.3 Re-run strict OpenSpec validation and focused calendar provenance,
  context-producer, source-ledger, and conflict-radar verification.
