## 1. Correct the false retention number in the live baseline

- [x] 1.1 Replace the `connector-filtered-events` Retention policy claim that
  partitions "older than 90 days" may be dropped with the shipped 12-month
  default from `src/butlers/jobs/retention.py:151`, carrying every other clause
  of the requirement forward unchanged.
- [x] 1.2 State the three conditions that must all hold before any partition is
  dropped (scheduled, enabled, not dry-run) and record that none of them holds
  in the shipped configuration.

## 2. Write the contract that was actually missing

- [x] 2.1 Specify that replay lineage (`public.audit_log`, indefinite) and the
  event payload (`connectors.filtered_events`, prunable) age independently, and
  that a lineage record is not proof the event row survives.
- [x] 2.2 Specify that replay history may not be re-sourced onto prunable
  storage, because that would silently make an indefinitely retained record
  deletable.
- [x] 2.3 Do not restate the audit log's own retention guarantee here;
  `dashboard-audit-log` already owns it and duplicating it would create a second
  owner for one fact.

## 3. Gate deletion behind an owner decision

- [x] 3.1 Specify that enabling a sweep requires a recorded retention window,
  not merely a configuration flag, and that keep-forever is the stated position
  until one exists.
- [x] 3.2 Write the retention-window options up with their costs in
  `proposal.md` and leave the number unresolved for the owner. Choosing it is
  irreversible data loss and is not an engineering-judgment call.

## 4. Pin the properties so a silent regression cannot land

- [x] 4.1 `tests/jobs/test_retention_owner_gate.py` fails if any roster
  `butler.toml` schedules a retention pruner. Demonstrated red by adding such a
  schedule entry, then reverted.
- [x] 4.2 The same module fails if the `enabled` default flips to true or the
  `dry_run` default flips to false. Demonstrated red for both, then reverted.
- [x] 4.3 The same module fails if replay history is re-sourced from
  `connectors.filtered_events`. Demonstrated red by rewriting the query, then
  reverted.
- [x] 4.4 The same module fails if the retention module gains any reference to
  `audit_log`. Demonstrated red by injecting one, then reverted.
- [x] 4.5 Guard the guard: the scan asserts roster configs were actually found,
  and cross-checks its job-name list against the live job registry so the scan
  cannot narrow silently.

## 5. Do not enact a policy

- [x] 5.1 Delete no data, add no pruner, enable no existing pruner, and pick no
  retention number.
