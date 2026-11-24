## Why

One tracked defect becomes about 16 log fingerprints per window and every fingerprint changes on
each deploy, because the log scanner reads the rendered traceback as the exception type. The spec
already says `exception_type` is a class name, so this restores the spec rather than changing it.
Dismissals, cooldowns, publication holds and joins cannot hold on identities that never repeat.
The anonymizer also rewrites resolvable module and class names as `[REDACTED-HOST]`, so a PR
title can read `unknown in [REDACTED-HOST]` and cannot be checked against its evidence.

## What changes

- `core.logging` writes `exc_type`, `exc_frame` and `exc_message_sanitized` for a logged exception
  before it is rendered; the scanner fingerprints those, and parses legacy lines by their last
  `Type: message` line, never taking raw text as the type.
- The anonymizer keeps dotted paths that resolve to a module of this repository, the stdlib or an
  installed package, and class names on them; internal hosts (including `.ts.net` tailnet names and
  identifier-shaped names such as `redis.internal`) stay redacted.
- Modifies `Finding Extraction` in `qa-log-scanner`.
- One-time rotation: every log fingerprint changes once; dismissals, cooldowns and holds keyed on
  old fingerprints go inert. Accepted by the owner on `bu-ejow5p.4` (spec-restoring bug fix).

## Not decided here

Incident correlation across call sites, the `session_records` fingerprint path, a log backend
change, backfill of historical findings, and rendering the traceback tail into the agent prompt.

Tracking: `bu-ejow5p.4`. Spec wording lands in a PR marked `(needs owner approval)`.
