## Why

The butler-chronicler "Retrospective-Only Scope" requirement names the once-daily day-close summary as the only sanctioned owner-facing message and permits no other. The day-close gap interview ask (bu-whhll.12, REQ-butler-chronicler-008) has shipped since July as a separate, opted-in, once-daily correction prompt. The canonical spec therefore contradicts itself. The archived `deterministic-schedule-jobs` design recorded the contradiction and left it for an owner decision.

The owner decided on 2026-10-24T15:37Z (bu-idc5mx.8 = A) to amend the scope requirement so that it sanctions opted-in correction prompts as a second owner-facing surface. Everything else stays retrospective-only. The decision rejected the alternative of retiring the gap interview.

## What Changes

- MODIFY "Retrospective-Only Scope" (butler-chronicler) to sanction exactly two owner-facing surfaces:
  - the day-close summary;
  - opted-in correction prompts, which today means only the gap interview ask defined by REQ-butler-chronicler-008: at most one per day, delivery-preference gated, and answered through the corrections contract.
- No other owner-facing message is permitted. Adding another correction prompt requires its own spec requirement.
- Add a scenario that covers both the sanctioned prompt and the continuing prohibition.
- Backfill the requirement's trace lines (`REQ-butler-chronicler-011`).

## Capabilities

### Modified Capabilities

- `butler-chronicler`: "Retrospective-Only Scope" sanctions opted-in correction prompts.

## Impact

- This is a spec-only conformance change. The behaviour has already shipped, so no code, schedule or tool changes.
- The pending `generalist-lead-focused-delegation` change also modifies this requirement for RFC 0038, and carries the same sanction so that its archive at the switch keeps it.
- `REQ-butler-chronicler-009` and `-010` are left free for draft PR #4755 (bu-vpn5ij), which needs two new IDs. Its current numbering collides with the existing `-008`.
