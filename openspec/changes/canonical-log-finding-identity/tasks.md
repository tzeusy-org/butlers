## 1. Slice 1: structured log identity

- [x] 1.1 `add_exception_identity` processor on the JSON file and console handlers.
- [x] 1.2 Scanner prefers `exc_type`/`exc_frame`/`exc_message_sanitized`; legacy fallback; real-pipeline test.
- [x] 1.3 Anonymized traceback tail kept in `structured_evidence`.

## 2. Slice 2: code-preserving anonymization

- [x] 2.1 Module-name set from the file tree (no import) and `_is_code_identifier`.
- [x] 2.2 `.ts.net` is never public.

## 3. Deferred

- [ ] 3.1 Render the traceback tail in the investigation prompt (multi-line evidence needs its own format).
- [ ] 3.2 Live check: `Triage complete` total at most 3 while bu-2cgabc is unfixed.
