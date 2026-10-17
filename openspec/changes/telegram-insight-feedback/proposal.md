# Telegram insight feedback

## Why
The insight broker emits feedback metadata, but owner Telegram deliveries do not expose it. Explicit owner verdicts must be available on the phone.

## What Changes
- Carry typed feedback bindings through the production notify envelope and render per-insight Useful, Not now and Never buttons.
- Add domain-separated ifb1 callbacks bound to server-created candidate time, primary-owner channel verification, and exact connector-authenticated routes.
- Keep feedback reversible, deduplicate repeated verdicts under the candidate lock, and use a server-computed 72-hour snooze.

## Impact
Messenger notify rendering, Telegram connector callbacks, Switchboard feedback API and broker, and production delivery wiring. No migration or new secret. Approval and Decision Desk semantics remain unchanged.
