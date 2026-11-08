# Google GenAI SDK 1.30.0

This finite series binds Google’s public source commit
`38cac5bbf4941ec5fa760238bd423c0ecc2c6f04`. Gemini CLI must install the
resulting SDK package at its actual nested dependency boundary; an unchanged
registry SDK does not acquire this transport from Gemini’s outer fetch wrapper.

The three patches add the isolated Undici alias, retain all original dependency
nodes, inject the selected terminal fetch into the existing API client and
GoogleAuth transporter options, and map the existing Node WebSocket factory.
The shared executor is copied exactly from `../shared/egress.ts`; its private
imports avoid global dispatcher initialization. Original API-key/Vertex mode
selection, URLs, scopes, user-supplied options and native WebSocket callbacks
remain in their owning SDK factories. Supplying an already constructed custom
auth client is a separate caller responsibility and is not covered by injecting
GoogleAuth constructor options.

Prepare with `scripts/runtime_clis/prepare_vendor.py`, then run ordinary
`npm ci` and `npm pack` with the pinned Node toolchain. Keep lifecycle scripts:
upstream `prepare` runs patch-package, all five Rollup exports, all three
production API extractors and the upstream final dependency adjustment. The
SDK’s older TypeScript DOM declarations need the shared executor’s precise
runtime AbortSignal constructor type; no runtime polyfill is installed.

A complete source replay and a successful stock build/package are distinct
from native API/auth/WebSocket operation evidence. The package must retain its
normal exports and production dependencies. Final Gemini nested lock resolution,
native operation coverage and immutable image installation remain required.
