## Why

Runtime parsers report a tool call's raw arguments without the defaults the daemon binds, so a
`chronicler_day_close_bundle` call that omitted an optional parameter never paired with its
executed capture and persisted a second, parser-only record carrying raw input. Persisted
tool-call records now carry only the capture allowlist view, and such a call pairs with its
capture as one record.

## What Changes

- Chronicler Day-Close Cache Surface: a parser call that omits optional parameters pairs with
  its executed capture as one record that keeps the canonical tool name.

## Impact

- Spec: `chronicler-api` (MODIFIED "Chronicler Day-Close Cache Surface", adding one scenario).
- Code: `src/butlers/core/spawner_tool_calls.py`, `src/butlers/core/tool_call_capture.py`.
- The retained case `test_writer_accepts_executed_wrapper_capture_with_parser_duplicate_and_dst_window`
  pins the old two-record shape and moves to one record under contract evolution.
