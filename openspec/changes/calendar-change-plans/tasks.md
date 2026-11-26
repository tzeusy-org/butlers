## 1. Spec

- [ ] 1.1 This change reviewed before runtime code.

## 2. Slices

- [ ] 2.1 P1 `core/temporal/change_plans.py` `compile_plan` (`clear_window`, `shift_window`) and the read-only `calendar_plan_change` tool.
- [ ] 2.2 P2 `calendar_apply_plan` (one decision set for the steps that reach others, then the rest in order, each etag-bound and sent once), `plan` / `plan_step` rows in `calendar_action_log`, and `calendar_undo_plan` as one approvals card.
- [ ] 2.3 P3 `anchor_moved` trigger: BLOCKED. There is no producer that watches a provider move of an event a Butlers block anchors to; none is built here. Reopen when one exists.
- [ ] 2.4 P4 `running_late` and `away_window`: parked on bu-jv1moa.28 (standing-reply templates) and the Travel read; not built.
