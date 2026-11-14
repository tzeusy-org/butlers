## Why

The Dots-superset pursuit (bu-jv1moa) promised owner-facing "doors": a link in an owner
notification that opens the vendor page where the owner can cancel, manage, return or check in.
S1 (PR #5015, squash 61695a67d3) delivered the fixed self-reference registry, the sender and
independently resolving Messenger receiver, and inert body text, but only for dashboard self
routes. Vendor links still have no owner-confirmed home: `finance.subscriptions.cancellation_url`
carries a write-time provenance column (bu-svqpzo.10, finance_017) and nothing else, and an
email-extracted link can only be shown as text marked "not verified".

The owner decision bu-kr3x6j is closed (option A) and approves exactly this sentence for
`security.md`:

> A door opens a vendor page whose host the owner confirmed; it never carries an amount, cart
> contents or confirmation token, so following it spends nothing.

It allows four kinds (cancel, manage, return, check in) and explicitly refuses a RFC 0038 D4
carve-out: doors stay inert in messages from exposed goals until separately approved. This change
is the S3 shaping packet (bu-jv1moa.40): it adopts the registry, confirmation, migration and
first-consumer contracts so an implementation lane can be dispatched. It is spec-only.

## What Changes

- ADD capability `vendor-doors`: canonical `public.vendor_doors` registry (owner-confirmed rows
  plus inert proposals), the dashboard-only confirmation and revocation path, typed door
  references resolved independently by sender and receiver, URL and slot rules, and the renewal
  consumer.
- Records the actual owners found on main (design D1), the migration split (D5), and the renewal
  consumer rule (D6).
- Everything is dark behind `VENDOR_DOORS_LIVE = False` (design D8) under RFC 0038 cutover rules.

## Non-Goals

- No code, migration, table or route in this change. No implementation is authorized by merging it.
- No pay, book, confirm-purchase, token, login or `web_app` control. A door never carries an
  amount, cart contents or confirmation token.
- No live link to a non-owner. No live door in a message produced under exposure: that stays
  inert until a separate owner approval (bu-kr3x6j excludes the D4 carve-out).
- No `manage_booking`, `order_status` or `bill_portal` kinds. The kind enum is exactly four.
- No model-callable confirmation tool and no activation of any runtime path.
- No change to `security.md` in this PR. The sentence above is applied by the implementation PR
  through a reviewed edit (task 6.1).
- A click is never a broker effect and never evidence that a cancellation happened.

## Impact

- New spec: `openspec/specs/vendor-doors/spec.md` (on archive).
- Implementation PR later touches: core migration (registry), finance migration (backfill),
  `src/butlers/api/routers/vendor_doors.py`, `roster/switchboard/tools/notification/self_links.py`
  and the Messenger receiver, `roster/finance/jobs/finance_jobs.py`, docs, `security.md`.
