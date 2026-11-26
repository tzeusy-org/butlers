## Why

Real days change in batches: "I'm sick, clear tomorrow", "push everything after lunch 45 minutes".
Today each affected event is found, reopened and edited by hand, one provider write and one
approval at a time, and there is no single way back. bu-5ii4gj.11 (calendar pursuit run 26 move 11)
asks for one owner verb to compile a multi-event plan, one decision to apply it and one undo.

## What changes

- **Plan compiler.** A pure, deterministic function turns one verb (`clear_window`, `shift_window`)
  and its parameters into an ordered list of steps over the owner's events, each with its exact tool
  call, its authority class and a reason, plus the events it deliberately skipped and why. No model,
  no provider call, no randomness.
- **`calendar_plan_change` (read-only).** Returns `{plan_id, plan_digest, steps, skipped}`. It
  writes nothing and parks nothing.
- **Apply with receipts (P2).** `calendar_apply_plan(plan_id, plan_digest)` parks
  every step that touches an owner's event as its own approvals card (only Butlers blocks run
  directly), each bound to the etag the plan was compiled against and sent once, with a per-step
  receipt; dependent steps are skipped when their dependency is declined. It returns `pending`.
  After the RFC 0038 goal cutover the cards become one decision set.
- **One composite undo (P2).** `calendar_undo_plan(plan_id)` offers one approvals card per reversal, each bound to the step's recorded `etag_after`.

## Out of scope

No model in the compiler, no series-wide edits, no free-text message to a third party. The
`anchor_moved` trigger (P3) is blocked until a producer exists and is not built here. `running_late`
and `away_window` verbs, and the standing-reply template a late message needs, wait on
bu-jv1moa.28 and the Travel read (P4). No dashboard event edit dialogs (bu-kf3n4i).

## Slices

P1 the pure compiler and read-only `calendar_plan_change`; P2 apply, per-step receipts and the
composite undo. P3 and P4 are recorded in the tasks as blocked.
