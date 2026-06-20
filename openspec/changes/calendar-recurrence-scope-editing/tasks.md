## 1. Widen the recurrence_scope literal

- [ ] 1.1 Change `recurrence_scope` on `calendar_update_event` (~:3075) from `Literal["series"]` to `Literal["this", "following", "series"]`, defaulting to `"series"`
- [ ] 1.2 Change `recurrence_scope` on `calendar_delete_event` (~:3414) the same way; update its docstring (drop "v1 supports series-scoped deletion only")
- [ ] 1.3 Unit tests: each scope value is accepted; an unknown scope string is rejected; default remains `"series"`

## 2. Add calendar_delete_event_instance

- [ ] 2.1 Register `calendar_delete_event_instance(event_id, instance_start_at, send_updates, request_id)` via `@_tool("core")`, gated by `_require_calendar_write_permission`
- [ ] 2.2 Resolve the occurrence (base `event_id` + `instance_start_at`), append a timezone-correct `EXDATE` to the series `recurrence` array, and PATCH the provider
- [ ] 2.3 Mark the matching `calendar_event_instances` row `is_exception = true` (status cancelled) in the projection
- [ ] 2.4 Unit + fake-provider tests: EXDATE appended for exactly the named occurrence; row marked `is_exception`; rest of the series intact; missing occurrence surfaces fail-open not-found

## 3. Add calendar_update_event_instance

- [ ] 3.1 Register `calendar_update_event_instance(event_id, instance_start_at, <partial fields>, request_id)` via `@_tool("core")`, gated by `_require_calendar_write_permission`
- [ ] 3.2 Detach the occurrence (EXDATE the original slot; carry edited fields onto the detached occurrence) and PATCH the provider
- [ ] 3.3 Mark the matching `calendar_event_instances` row `is_exception = true` and project the edited occurrence
- [ ] 3.4 Unit + fake-provider tests: only the named occurrence reflects the edit; `is_exception = true`; the rest of the series is unchanged

## 4. this / following semantics on the existing CRUD tools

- [ ] 4.1 `recurrence_scope="this"` on update/delete routes to the single-occurrence path (EXDATE + exception)
- [ ] 4.2 `recurrence_scope="following"` bounds the original RRULE with `UNTIL` just before `instance_start_at` and applies the mutation to the occurrence-and-onward remainder
- [ ] 4.3 `recurrence_scope="series"` keeps the existing whole-series behavior unchanged
- [ ] 4.4 Tests: delete-this, delete-following, and delete-series each touch the correct occurrence set (fake provider)

## 5. Impact preview + high-impact gate

- [ ] 5.1 Add an occurrence-count helper (1 for `this`, remaining-from-boundary for `following`, whole-series count for `series`) reusing the occurrence expander
- [ ] 5.2 Feed the count into the existing `_gate_high_impact_mutation` path (~:7427) so blast radius is reported before the write
- [ ] 5.3 Unit tests: the preview count matches the scope; large `series`/`following` trips the high-impact gate, `this` does not

## 6. Spec + regression + docs

- [ ] 6.1 Apply this change's `module-calendar` spec delta (tool count 16 → 18; update/delete scope scenarios; retire "series-scoped in v1"; new occurrence-scoped requirement)
- [ ] 6.2 Update/replace any regression test that asserts the old `Literal["series"]`-only or series-only-deletion behavior
- [ ] 6.3 Run `openspec validate calendar-recurrence-scope-editing --strict`
- [ ] 6.4 Quality gate: `ruff check`/`format --check` + targeted calendar test suite, then full `pytest` (excluding e2e) before merge
