## Context

QA overview readers must not turn missing summary or case-rail sources into calm empty states.

## Decisions

- Keep the existing response contracts unchanged; derive unavailable presentation from query state.
- Preserve successful zero and empty semantics; use `SourceDegradedNote` for the rail so retry remains explicit.
