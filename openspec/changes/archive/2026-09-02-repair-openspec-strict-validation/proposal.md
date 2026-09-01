## Why

`openspec validate --all --strict` treats RFC-2119 guidance as a failure when a
baseline requirement has no `SHALL` or `MUST` in its normative paragraph. The
remaining warning-only failures are mechanical paragraph-shape debt: their
existing scenarios already define the behavior and need no contract change.

## What Changes

- Add one neutral RFC-2119 sentence to each affected baseline requirement,
  preserving its existing prose and every scenario verbatim.
- Keep this change limited to validation hygiene; it does not alter runtime
  behavior or requirement meaning.

## Capabilities

### Modified Capabilities

The affected capability specs gain explicit normative paragraph structure.

## Impact

No production code, database, API, or scenario behavior changes. This delta
only makes existing requirement prose explicitly normative so strict validation
can detect future drift.

## Validation inventory

The initial measurement on 2026-09-02 was `232 passed, 56 failed (288
items)`. All 56 failures were Class 1 mechanical validation debt: 34 active
delta scenario-loss diagnostics, 48 blank requirement-body diagnostics, 2
zero-scenario diagnostics, and 220 warning-only RFC-2119 paragraph diagnostics.
The 34 scenario-loss repairs copied 105 current baseline scenario blocks
verbatim. Class 2 substantive spec contradictions: 0.
