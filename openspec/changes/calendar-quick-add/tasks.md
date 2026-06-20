# Tasks — calendar-quick-add

Single small backend feature: one parse-only endpoint plus its models. No DB
migration, no new MCP tool. Confirm reuses the existing user-events create path.

## 1. Request/response models

- [ ] 1.1 Add `QuickAddParseRequest` (`text: str`, optional `timezone: str`,
  optional `butler_name`) to `src/butlers/api/models/calendar_workspace.py`
- [ ] 1.2 Add `QuickAddDraft` (`title`, `start_at`, `end_at`, optional `location`,
  optional `description`) and `QuickAddParseResponse`
  (`parse_available: bool`, `draft: QuickAddDraft | None`, `reason: str | None`)
- [ ] 1.3 Validate blank/empty `text` at the model boundary (422 on blank input)

## 2. Parse-only endpoint

- [ ] 2.1 Add `POST /api/calendar/workspace/parse-quick-add` to
  `src/butlers/api/routers/calendar_workspace.py`; resolve the calendar-owning
  butler the same way the other workspace endpoints do
- [ ] 2.2 Resolve the model via `resolve_model(pool, butler_name, Complexity.CHEAP)`
  (simple/cheap tier); run a single LLM parse of `text` into a draft event,
  honoring the optional display `timezone`
- [ ] 2.3 Return `parse_available=true` with a populated `draft` on success;
  perform NO provider or projection write (read-only)

## 3. Degraded / unparseable paths

- [ ] 3.1 When `resolve_model(...)` returns `None`, return HTTP 200 with
  `parse_available=false`, a human-readable `reason`, and no `draft` (do not
  fabricate or heuristically guess an event)
- [ ] 3.2 When the LLM response cannot be interpreted as a single event draft,
  return `parse_available=false` with a `reason` and no `draft`
- [ ] 3.3 Confirm no write occurs on any degraded/unparseable path

## 4. Confirm reuses the existing create path

- [ ] 4.1 Confirm the parse-quick-add response is advisory only; the confirm step
  submits the (possibly edited) draft to the existing
  `POST /api/calendar/workspace/user-events` with `action="create"` and a
  `request_id` — no new write endpoint is added

## 5. Tests + spec + gate

- [ ] 5.1 Unit test: valid text → `parse_available=true` + draft fields; assert no
  MCP create tool is invoked (parse is read-only)
- [ ] 5.2 Unit test: `resolve_model` returns `None` → `parse_available=false`, no
  `draft`, no write
- [ ] 5.3 Unit test: blank `text` → 422; unparseable LLM output →
  `parse_available=false` with `reason`
- [ ] 5.4 Add the `POST /api/calendar/workspace/parse-quick-add` row to the
  dashboard-api endpoint inventory (under `#### Calendar Workspace`)
- [ ] 5.5 Run `openspec validate calendar-quick-add --strict`
- [ ] 5.6 Quality gate: `ruff check`/`format --check` on touched files, then
  targeted calendar-workspace test suite
