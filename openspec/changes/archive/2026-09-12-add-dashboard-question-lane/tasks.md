## 1. Schema

- [x] 1.1 `core_213`: add `public.dashboard_messages.sources JSONB` (nullable,
      downgrade drops it).
- [x] 1.2 `sw_033`: add `'unanswerable'` to `dead_letter_queue`'s
      `valid_failure_category` CHECK vocabulary (downgrade restores the
      original list).

## 2. Classifier prompt and tool schemas

- [x] 2.1 `_build_dashboard_lane_prompt` gains LANE D (question), describing
      `answer_question(scope, question, target)` and
      `cannot_answer(question_summary, scope_checked, reason)`; the
      ambiguous/no-tool branch and the terminal-refusal explanation both
      enumerate all four tools.
- [x] 2.2 `roster/switchboard/tools/routing/structured_classify.py` gains
      `ANSWER_QUESTION_TOOL`/`CANNOT_ANSWER_TOOL` schemas,
      `_validate_tool_call` branches for both, and an
      `include_question_lane` parameter on `try_structured_classification`
      (offered alongside `include_bug_report`; wired at the pipeline call
      site the same way, still unreachable for dashboard turns until
      bu-0ynlk.6 lifts the `source != "dashboard"` fast-lane exclusion).

## 3. Switchboard tools

- [x] 3.1 Extract `_dispatch_dashboard_target` from `route_to_butler`'s
      `route.execute` dispatch tail (permission-independent — envelope
      build, dashboard-turn Stop check, claim, dispatch, result parsing,
      optional sticky-stamp) so `route_to_butler` and
      `answer_question(scope="domain")` share it. Statement/action routes stamp
      the accepted target; answer routes leave it unset so later turns are
      reclassified under the question-lane contract.
- [x] 3.2 `_build_dashboard_answer_block`: read-only sibling of
      `_build_dashboard_confirm_block` — forbids writes, requires citing
      `sources` on `conversation_reply` for a grounded answer, requires an
      honest decline (no fabricated citation) otherwise.
- [x] 3.3 `answer_question(scope, question, target)`: validates scope,
      enforces the lane-exclusivity guard, dispatches via
      `_dispatch_dashboard_target` for `scope="domain"`; falls back to the
      shared `_dashboard_cannot_answer` helper for `scope="system"` (no
      Concierge yet).
- [x] 3.4 `cannot_answer(question_summary, scope_checked, reason)`: enforces
      the lane-exclusivity guard, then calls `_dashboard_cannot_answer` —
      claims the dead-letter external action, captures
      `failure_category='unanswerable'`, marks the turn terminal, replies
      in-thread naming `scope_checked`. Never files a bug report, never
      routes to a domain butler.
- [x] 3.5 `route_to_butler`'s lane-conflict guard extended from `== "bug"` to
      `{"bug", "answer", "cannot_answer"}`; `file_bug_report`'s existing
      "never suppressed, only surfaced" behavior is unchanged.
- [x] 3.6 Both new question-lane tools fail closed without a valid dashboard
      conversation context; they cannot become unbounded non-dashboard route
      or dead-letter aliases.

## 4. Pipeline integration

- [x] 4.1 `_extract_answer_question_calls` (domain-scope only) merges into
      the same `routed`/`acked`/`failed` bookkeeping `_extract_routed_butlers`
      populates, so a successful answer-lane dispatch flows through the
      identical routing-verdict/telemetry path `route_to_butler` uses.
- [x] 4.2 `_extract_cannot_answer_calls` (matches `cannot_answer` and a
      `scope="system"` `answer_question` fallback) short-circuits with an
      early `RoutingResult(target_butler="dead_letter", ...)` return —
      mirrors the `file_bug_report` early-return block — so the generic
      "no lane decision" dead-letter net never double-captures or
      double-replies for a turn the new tools already handled.
- [x] 4.3 `conversation_reply_create`/`message_create` gain a `sources`
      parameter; `message_get_by_id`/`message_list`/`message_find_reply_since`
      select the new column.
- [x] 4.4 `core_tools/_conversation_reply.py`'s `conversation_reply` MCP tool
      gains the `sources` parameter with empty-list and blank-name rejection.
- [x] 4.5 The dashboard SSE `message_complete` event includes `sources`.

## 5. Tests

- [x] 5.1 `structured_classify.py`: schema-offer test (four tools), a
      question fixture that validates and executes `answer_question` with
      scope/target populated, and a `cannot_answer` fixture.
- [x] 5.2 `_switchboard.py`: answer-block injection (not confirm-block),
      domain-target-required validation, `scope="system"` fallback,
      `cannot_answer` dead-letter/reply/terminal-marking, and lane-exclusivity
      coverage across all four tools (12 tests in
      `tests/daemon/test_dashboard_lane_tools.py`).
- [x] 5.3 `_conversation_reply.py`: empty-`sources` rejection, omitted-`sources`
      is unaffected, non-empty `sources` persists.
- [x] 5.4 `pipeline.py`: prompt-level grep tests (LANE D present, no
      general/best-guess fallback instruction anywhere in the rendered
      prompt) and pipeline-level fixture tests (domain question routes,
      `cannot_answer` dead-letters without double-capture, system-scope
      fallback dead-letters, an ambiguous-question fixture resolves via
      `cannot_answer` never `route_to_butler('general')`).
- [x] 5.5 Migration round-trip tests: `core_213` (nullable column, array
      persists, downgrade drops it) and `sw_033` (accepts `unanswerable` and
      the original vocabulary, rejects an invented category, downgrade
      restores the original CHECK).
- [x] 5.6 `openspec validate --all --strict`; `make lint`; targeted suites for
      pipeline, `_switchboard`, `_conversation_reply`, `api/conversations`,
      `structured_classify` green.

## 6. Docs

- [x] 6.1 Add the `butler-switchboard`/`dashboard-conversations` spec deltas
      (four-terminal-tool lane taxonomy, question-lane scenarios, `sources`
      contract).
- [x] 6.2 Dated supersession note in
      `docs/redesigns/2026-07-28-talk-to-butlers-maturity-pursuit.md`
      recording that the 2026-09-02 owner directive supersedes its "no
      generic question lane" decision for this run.
